"""Selected evidence storage only. No launch, socket, credential or raw stream API.

The caller owns a trusted directory FD and supplies process testimony. Neither
that testimony nor a PREPARED file authenticates a process or a received return.
Use under an independently admitted external I/O deadline before runtime use.
"""
import dataclasses
import hashlib
import json
import os
import stat
import uuid

from .frames import Frames, FrameError


SELECTED_LIMIT = 64 * 1024 * 1024  # Storage only; derived format bound in V2 contract.
_FACTORY = object()


class RetentionError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise RetentionError(code)


def identity(value):
    require(isinstance(value, str) and len(value) == 36, 'identity_shape')
    try:
        valid = str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    require(valid, 'identity_shape')


def digest(value):
    require(isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value), 'digest_shape')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclasses.dataclass(frozen=True)
class Intent:
    run_id: str
    task_sha256: str
    oracle_sha256: str
    config_sha256: str
    source_manifest_sha256: str

    def validate(self):
        identity(self.run_id)
        for key in ['task_sha256', 'oracle_sha256', 'config_sha256', 'source_manifest_sha256']:
            digest(getattr(self, key))


@dataclasses.dataclass(frozen=True)
class ExitSnapshot:
    pid: int
    start_ticks: int
    boot_id: str
    exit_code: int
    signal_observed: bool

    def validate(self):
        require(type(self.pid) is int and 0 < self.pid <= 2147483647
                and type(self.start_ticks) is int and 0 < self.start_ticks <= 2**63-1,
                'exit_identity_shape')
        identity(self.boot_id)
        require(type(self.exit_code) is int and -128 <= self.exit_code <= 255
                and type(self.signal_observed) is bool, 'exit_shape')


@dataclasses.dataclass(frozen=True)
class PreparedReceipt:
    run_id: str
    selected_sha256: str
    selected_bytes: int
    intent_sha256: str
    return_receipt: bool = False
    exit_basis: str = 'caller_supplied_not_independently_attested'


class Retention:
    """One newly consumed run. Context manager closes only its own directory FD.

    FD ownership, 0700 directory privacy and no-follow leaf operations protect
    this local writer's paths, not another process running as the same UID.
    Partial files stay after any failure. Never recreate or infer cleanup from
    retained files. A crash has UNKNOWN termination unless an external owner
    observed the return; no standalone PREPARED file is an accepted success.
    """

    def __init__(self, directory_fd, intent, intent_sha256=None, *, _token=None):
        require(_token is _FACTORY, 'factory_required')
        self.fd = directory_fd
        self.intent = intent
        self.intent_sha256 = intent_sha256
        meta = os.fstat(directory_fd)
        self.directory_identity = (meta.st_dev, meta.st_ino)
        self.state = 'open'
        self.close_unknown = False

    @classmethod
    def create(cls, trusted_root_fd, intent):
        require(type(intent) is Intent, 'intent_type')
        intent.validate()
        require(type(trusted_root_fd) is int and trusted_root_fd >= 0, 'root_fd_shape')
        code, fd, writer, returned, interruption, consumed = None, None, None, False, None, False
        try:
            root = os.fstat(trusted_root_fd)
            require(stat.S_ISDIR(root.st_mode) and root.st_uid == os.geteuid()
                    and stat.S_IMODE(root.st_mode) == 0o700, 'root_custody')
            # mkdir consumes the target permanently, including intent failures.
            os.mkdir(intent.run_id, 0o700, dir_fd=trusted_root_fd)
            consumed = True
            os.fsync(trusted_root_fd)
            fd = os.open(intent.run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=trusted_root_fd)
            child = os.fstat(fd)
            require(child.st_uid == os.geteuid() and stat.S_IMODE(child.st_mode) == 0o700,
                    'run_custody')
            encoded_intent = encode({'schema': 'cairn.selected-retention-intent/1',
                **dataclasses.asdict(intent), 'admission_basis': 'caller_pinned_not_verified',
                'launch_performed_by_sink': False})
            writer = cls(fd, intent, hashlib.sha256(encoded_intent).hexdigest(), _token=_FACTORY)
            writer._write('INTENT.json', encoded_intent)
            returned = True
            return writer
        except FileExistsError:
            code = 'target_consumed'
        except RetentionError as exc:
            code = str(exc)
        except OSError:
            code = 'intent_io_after_consume' if consumed else 'intent_io_before_consume'
        except Exception:
            code = 'internal_refusal'
        except BaseException as exc:
            interruption = exc
        finally:
            if not returned and fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass  # Does not replace setup's primary failure; no cleanup claim.
        if interruption is not None:
            raise interruption
        raise RetentionError(code)

    def _check_directory(self):
        meta = os.fstat(self.fd)
        require(stat.S_ISDIR(meta.st_mode) and (meta.st_dev, meta.st_ino) == self.directory_identity
                and meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700,
                'run_custody_lost')

    def _write(self, name, data):
        self._check_directory()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            meta = os.fstat(f.fileno())
        os.fsync(self.fd)
        return meta.st_dev, meta.st_ino

    def _readback(self, expected, written_identity):
        self._check_directory()
        fd = os.open('SELECTED.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as f:
            meta = os.fstat(f.fileno())
            require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                    and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1
                    and meta.st_size == len(expected), 'readback_custody')
            require((meta.st_dev, meta.st_ino) == written_identity, 'readback_identity')
            returned = f.read(SELECTED_LIMIT+1)
        require(returned == expected, 'readback_mismatch')

    def _diagnostic(self, code):
        # Caller keeps the primary code even when diagnostics cannot be stored.
        try:
            self._write('FAILURE.json', encode({'schema': 'cairn.selected-retention-failure/1',
                'run_id': self.intent.run_id, 'code': code,
                'success_claimed': False, 'return_receipt': False}))
            return True
        except Exception:
            return False

    def prepare(self, reader, exit_snapshot):
        require(self.fd is not None and self.state == 'open', 'writer_unavailable')
        self.state = 'failed'  # No exception or interruption can reopen this run.
        code, data, selected, terminal, result = None, b'', None, None, None
        try:
            require(type(reader) is Frames and type(exit_snapshot) is ExitSnapshot,
                    'selected_input_type')
            exit_snapshot.validate()
            require(reader.expected['session_id'] == self.intent.run_id, 'run_mismatch')
            require(exit_snapshot.exit_code == 0 and not exit_snapshot.signal_observed,
                    'native_exit_or_signal')
            # Do not expose a raw dictionary/file ingestion entrypoint.
            selected = reader.finish(exit_snapshot.exit_code)
            data = encode(selected)
            require(len(data) <= SELECTED_LIMIT, 'selected_bounds')
            written_identity = self._write('SELECTED.json', data)
            self._readback(data, written_identity)
            result = PreparedReceipt(self.intent.run_id, hashlib.sha256(data).hexdigest(),
                                     len(data), self.intent_sha256)
            terminal = {'schema': 'cairn.selected-retention-terminal/1',
                        'status': 'prepared_to_return', **dataclasses.asdict(result),
                        'exit_snapshot': dataclasses.asdict(exit_snapshot),
                        'exit_attested': False, 'task_success_claimed': False,
                        'standalone_terminal_is_success': False}
            self._write('PREPARED.json', encode(terminal))
            self.state = 'prepared'
            return result
        except RetentionError as exc:
            code = str(exc)
        except FrameError:
            code = 'reader_refused'
        except OSError:
            code = 'retention_io'
        except Exception:
            code = 'internal_refusal'
        except BaseException:
            # Original interrupt traceback may retain private selected values.
            # Never log/dump it. Poisoned state remains best effort, not masking.
            try:
                self._diagnostic('observer_interrupted')
            except BaseException:
                pass  # Diagnostic failure cannot replace this original interrupt.
            raise
        self._diagnostic(code)
        # Raise outside handlers, without raw selected data in public locals.
        data, selected, terminal, result, reader = b'', None, None, None, None
        raise RetentionError(code)

    def close(self):
        code = None
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                meta = os.fstat(fd)
                require((meta.st_dev, meta.st_ino) == self.directory_identity, 'run_custody_lost')
                os.close(fd)
            except RetentionError as exc:
                self.close_unknown, code = True, str(exc)
            except OSError:
                self.close_unknown, code = True, 'close_io'
        if code is not None:
            raise RetentionError(code)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        code = None
        try:
            self.close()
        except RetentionError:
            self.close_unknown = True
            if exc_type is None:
                code = 'close_io'
        if code is not None:
            raise RetentionError(code)


@dataclasses.dataclass(frozen=True)
class RetainedStatus:
    status: str
    reason: str
    return_receipt: bool = False
    cleanup_authorized: bool = False


def _inspect_run(trusted_root_fd, run_id):
    """Read fixed bounded markers only. Never upgrade files into a native success."""
    identity(run_id)
    require(type(trusted_root_fd) is int and trusted_root_fd >= 0, 'root_fd_shape')
    root = os.fstat(trusted_root_fd)
    require(stat.S_ISDIR(root.st_mode) and root.st_uid == os.geteuid()
            and stat.S_IMODE(root.st_mode) == 0o700, 'root_custody')
    try:
        fd = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=trusted_root_fd)
    except FileNotFoundError:
        return RetainedStatus('unknown', 'missing_run')
    try:
        meta = os.fstat(fd)
        require(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700, 'run_custody')
        def marker(name):
            try:
                leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            with os.fdopen(leaf, 'rb') as f:
                m = os.fstat(f.fileno())
                require(stat.S_ISREG(m.st_mode) and m.st_uid == os.geteuid()
                        and stat.S_IMODE(m.st_mode) == 0o600 and m.st_nlink == 1
                        and m.st_size <= 4096, 'marker_custody')
                raw = f.read(4097)
            require(len(raw) <= 4096, 'marker_bounds')
            def pairs(items):
                value = {}
                for k, v in items:
                    require(k not in value, 'marker_duplicate_key')
                    value[k] = v
                return value
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
            require(isinstance(value, dict) and value.get('run_id') == run_id, 'marker_identity')
            return value, hashlib.sha256(raw).hexdigest()
        intended = marker('INTENT.json')
        if intended is None:
            return RetainedStatus('unknown', 'missing_intent')
        intent, intent_sha = intended
        require(intent.get('schema') == 'cairn.selected-retention-intent/1'
                and intent.get('launch_performed_by_sink') is False, 'intent_marker_shape')
        for key in ['task_sha256', 'oracle_sha256', 'config_sha256', 'source_manifest_sha256']:
            digest(intent.get(key))
        failed = marker('FAILURE.json')
        if failed is not None:
            value, _ = failed
            require(value.get('schema') == 'cairn.selected-retention-failure/1'
                    and value.get('success_claimed') is False and value.get('return_receipt') is False
                    and isinstance(value.get('code'), str)
                    and value.get('code') in {'selected_input_type', 'exit_identity_shape', 'identity_shape',
                        'exit_shape', 'run_mismatch', 'native_exit_or_signal', 'selected_bounds',
                        'readback_custody', 'readback_identity', 'readback_mismatch', 'run_custody_lost',
                        'reader_refused', 'retention_io', 'internal_refusal', 'observer_interrupted'},
                    'failure_marker_shape')
            return RetainedStatus('retained_failure', 'failure_marker_overrides_prepared')
        prepared = marker('PREPARED.json')
        if prepared is None:
            return RetainedStatus('unknown', 'no_terminal_marker')
        value, _ = prepared
        require(value.get('schema') == 'cairn.selected-retention-terminal/1'
                and value.get('status') == 'prepared_to_return' and value.get('intent_sha256') == intent_sha
                and all(value.get(k) is False for k in ['return_receipt', 'exit_attested',
                    'task_success_claimed', 'standalone_terminal_is_success']), 'prepared_marker_shape')
        digest(value.get('selected_sha256'))
        require(type(value.get('selected_bytes')) is int and 0 < value['selected_bytes'] <= SELECTED_LIMIT,
                'prepared_marker_shape')
        return RetainedStatus('unknown', 'prepared_without_external_return')
    finally:
        os.close(fd)


def inspect_run(trusted_root_fd, run_id):
    """Public fixed-code reader; no raw JSON error chain or payload escapes."""
    code, result = None, None
    try:
        result = _inspect_run(trusted_root_fd, run_id)
    except RetentionError as exc:
        code = str(exc)
    except (ValueError, TypeError, UnicodeError):
        code = 'marker_shape'
    except OSError:
        code = 'reconciliation_io'
    except Exception:
        code = 'internal_refusal'
    if code is not None:
        raise RetentionError(code)
    return result
