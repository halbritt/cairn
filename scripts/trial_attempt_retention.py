"""Selected observation storage for a fixed owned composition worker.

No raw dictionary ingestion, executor, process authentication or grading API.
Optional end binding checks the declaration against the consumed stream/source.
The external owner must observe actual return/release and bind the selected
file to that closed attempt.
"""
import dataclasses
import hashlib
import os
import stat

from selected_native.retention import Retention, RetentionError, PreparedReceipt, SELECTED_LIMIT, encode, require
from selected_native.frames import parse, FrameError
from trial_coding_stream import CodingStream
from trial_worker_protocol import decode_end, ProtocolError
from trial_coding_protocol import decode_hooks, validate_hooks, coding_packet, CodingDecoder


@dataclasses.dataclass(frozen=True)
class ConsumedHooks:
    """Bounded selected projection and original framed digest; no authority."""
    selected: bytes
    packet_sha256: str

    @classmethod
    def consume(cls, payload, *, run_id, source_manifest_sha256):
        selected = decode_hooks(payload, run_id=run_id,
                                source_manifest_sha256=source_manifest_sha256)
        return cls(encode(selected), hashlib.sha256(coding_packet(b'H', payload)).hexdigest())


@dataclasses.dataclass(frozen=True)
class FailurePrepared:
    run_id: str
    intent_sha256: str
    selected_sha256: str
    selected_bytes: int
    return_receipt: bool = False


class AttemptRetention:
    """One owned duplicate of the consumed intent's directory descriptor.

    Only a Retention factory object can supply the consumed intent. This local
    type check protects the interface; it does not authenticate the runtime.
    Same-UID tampering, host loss and blocking I/O require the external owner.
    Partial writes remain consumed after failure and must never be retried.
    """
    def __init__(self, retention):
        require(type(retention) is Retention and retention.fd is not None
                and retention.state == 'open', 'attempt_retention_factory')
        self.fd = None
        self.retention = retention
        self.intent = retention.intent
        self.intent_sha256 = retention.intent_sha256
        self.identity = retention.directory_identity
        self.state, self.close_unknown = 'open', False
        code, duplicate, returned = None, None, False
        try:
            duplicate = os.dup(retention.fd)
            self.fd = duplicate
            self._check_directory()
            returned = True
        except RetentionError as exc:
            code = str(exc)
        except OSError:
            code = 'attempt_directory_io'
        finally:
            if not returned:
                self.fd = None
                if duplicate is not None:
                    try:
                        os.close(duplicate)
                    except OSError:
                        pass
        if code is not None:
            raise RetentionError(code)

    def _check_directory(self):
        require(self.fd is not None, 'attempt_writer_unavailable')
        meta = os.fstat(self.fd)
        require(stat.S_ISDIR(meta.st_mode) and (meta.st_dev, meta.st_ino) == self.identity
                and meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700,
                'attempt_directory_custody')

    def _write(self, data, name='ATTEMPT-EVIDENCE.json'):
        require(name in ('ATTEMPT-EVIDENCE.json', 'OBSERVATIONS.json'), 'attempt_leaf_unavailable')
        self._check_directory()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
            written = os.fstat(target.fileno())
        os.fsync(self.fd)
        self._check_directory()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as source:
            actual = os.fstat(source.fileno())
            require(stat.S_ISREG(actual.st_mode) and actual.st_uid == os.geteuid()
                    and stat.S_IMODE(actual.st_mode) == 0o600 and actual.st_nlink == 1
                    and actual.st_size == len(data), 'attempt_readback_custody')
            require((actual.st_dev, actual.st_ino) == (written.st_dev, written.st_ino),
                    'attempt_readback_identity')
            returned = source.read(SELECTED_LIMIT+1)
        require(returned == data, 'attempt_readback_mismatch')

    def _end(self, payload, composer, coding_decoder=None):
        try:
            if coding_decoder is None:
                end = decode_end(payload, run_id=self.intent.run_id,
                                 source_manifest_sha256=self.intent.source_manifest_sha256)
            else:
                from trial_coding_protocol import decode_coding_end
                end = decode_coding_end(payload, run_id=self.intent.run_id,
                                        source_manifest_sha256=self.intent.source_manifest_sha256)
                require(end == coding_decoder.end
                        and end['wire_sha256'] == coding_decoder.wire.hexdigest()
                        and end['wire_bytes'] == coding_decoder.wire_bytes
                        and end['stream_sha256'] == coding_decoder.stream.hexdigest()
                        and end['stream_bytes'] == coding_decoder.stream_bytes
                        and end['timing_packets'] == coding_decoder.timings
                        and end['hook_packet_sha256'] == coding_decoder.hook_sha,
                        'attempt_coding_wire_binding')
        except ProtocolError:
            raise RetentionError('attempt_end_refused') from None
        require(composer.digest_complete and end['stream_sha256'] == composer.hash.hexdigest()
                and end['stream_bytes'] == composer.total, 'attempt_stream_binding')
        return end

    def _success_receipt(self, receipt, end):
        require(type(receipt) is PreparedReceipt and self.retention.state == 'prepared'
                and receipt.run_id == self.intent.run_id and receipt.intent_sha256 == self.intent_sha256
                and receipt.return_receipt is False, 'attempt_success_binding')
        self._check_directory()
        fd = os.open('SELECTED.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as source:
            meta = os.fstat(source.fileno())
            require(type(receipt.selected_bytes) is int and 0 < receipt.selected_bytes <= SELECTED_LIMIT
                    and stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                    and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1
                    and meta.st_size == receipt.selected_bytes, 'attempt_success_custody')
            data = source.read(SELECTED_LIMIT+1)
        require(len(data) == receipt.selected_bytes and hashlib.sha256(data).hexdigest()
                == receipt.selected_sha256, 'attempt_success_readback')
        fd = os.open('PREPARED.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as source:
            meta = os.fstat(source.fileno())
            require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                    and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1
                    and 0 < meta.st_size <= 4096, 'attempt_success_terminal_custody')
            terminal_bytes = source.read(4097)
        try:
            terminal = parse(terminal_bytes)
        except FrameError:
            raise RetentionError('attempt_success_terminal_malformed') from None
        require(type(terminal) is dict and set(terminal) == set(dataclasses.asdict(receipt)) | {
                'schema', 'status', 'exit_snapshot', 'exit_attested', 'task_success_claimed',
                'standalone_terminal_is_success'}
                and terminal.get('schema') == 'cairn.selected-retention-terminal/1'
                and terminal.get('status') == 'prepared_to_return'
                and all(terminal.get(key) == value for key, value in dataclasses.asdict(receipt).items())
                and all(terminal.get(key) is False for key in
                        ('return_receipt', 'exit_attested', 'task_success_claimed', 'standalone_terminal_is_success')),
                'attempt_success_terminal_binding')
        actor = end['actor_wait']
        require(actor is not None and terminal.get('exit_snapshot') == dict(
                pid=actor['pid'], start_ticks=actor['start_ticks'], boot_id=actor['boot_id'],
                exit_code=actor['returncode'], signal_observed=False), 'attempt_success_exit_binding')
        return dataclasses.asdict(receipt)

    def prepare(self, composer, hook_rows):
        """Legacy selected failure-channel API; no end/actual closure claim."""
        return self._prepare(composer, hook_rows)

    def prepare_with_end(self, composer, hook_rows, end_payload):
        """Fixed worker failure path: bind the strict end declaration to D bytes."""
        return self._prepare(composer, hook_rows, end_payload=end_payload)

    def prepare_success_observations(self, composer, hook_rows, end_payload, prepared_receipt):
        """Keep accounting/end observations beside an already prepared success.

        This does not retry success retention or establish process closure.
        An observation-write fault leaves both consumed writers unavailable.
        """
        return self._prepare(composer, hook_rows, end_payload=end_payload,
                             prepared_receipt=prepared_receipt)

    def check_coding_hooks(self, decoder, snapshot):
        """Check the consumed projection before either writer persists success.

        This pure check never consumes a writer or authenticates the source.
        Preparation repeats it after consuming the attempt writer.
        """
        require(type(decoder) is CodingDecoder and not decoder.poisoned
                and decoder.pending is None and decoder.hooks is not None
                and decoder.end is not None, 'attempt_coding_decoder_unavailable')
        require(type(snapshot) is ConsumedHooks, 'attempt_coding_hook_binding')
        try:
            args = dict(run_id=self.intent.run_id,
                        source_manifest_sha256=self.intent.source_manifest_sha256)
            frozen = decode_hooks(snapshot.selected, **args)
            current = validate_hooks(decoder.hooks, **args)
            require(encode(frozen) == snapshot.selected == encode(current)
                    and snapshot.packet_sha256 == decoder.hook_sha
                    and snapshot.packet_sha256 == decoder.end['hook_packet_sha256'],
                    'attempt_coding_hook_binding')
        except (ProtocolError, KeyError, TypeError):
            raise RetentionError('attempt_coding_hook_binding') from None
        return frozen['rows']

    def prepare_coding(self, composer, decoder, prepared_receipt=None, *, hook_snapshot=None):
        """Retain complete coding E and the bounded H from the consumed decoder.

        A typed local decoder is a shape/binding seam, never process authority.
        The fixed sink owns its packet source and observes pipe EOF separately.
        """
        return self._prepare(composer, None, prepared_receipt=prepared_receipt,
                             coding_decoder=decoder, coding=True, hook_snapshot=hook_snapshot)

    def _prepare(self, composer, hook_rows, *, end_payload=None, prepared_receipt=None, coding_decoder=None, coding=False, hook_snapshot=None):
        require(self.state == 'open' and self.fd is not None, 'attempt_writer_unavailable')
        self.state = 'failed'  # A refusal, I/O fault or interrupt consumes this writer.
        code, report, data, receipt, end, success = None, None, b'', None, None, None
        try:
            require(type(composer) is CodingStream and not composer.closed,
                    'attempt_composer_type')
            require(composer.reader.expected['session_id'] == self.intent.run_id,
                    'attempt_run_mismatch')
            if coding:
                hook_rows = self.check_coding_hooks(coding_decoder, hook_snapshot)
                end_payload = encode(coding_decoder.end)
            if prepared_receipt is None:
                require(self.retention.state != 'prepared', 'attempt_success_already_prepared')
            else:
                require(end_payload is not None, 'attempt_end_required')
            if end_payload is not None:
                end = self._end(end_payload, composer, coding_decoder)
            if prepared_receipt is not None:
                success = self._success_receipt(prepared_receipt, end)
            if success is not None:
                require(not end['observations'] and not end['capture_cleanup_observations']
                        and not end.get('coding_observations', [])
                        and end['capture_released'] is True
                        and end['actor_wait'] is not None and end['capture_wait'] is not None
                        and all(value is not None for value in end['actor_wait'].values())
                        and all(value is not None for value in end['capture_wait'].values())
                        and end['actor_wait']['returncode'] == 0
                        and end['capture_wait']['returncode'] == 0
                        and end['input_bytes'] is not None
                        and end['input_bytes_written'] == end['input_bytes'], 'attempt_success_end_conflict')
            report = composer.observations(hook_rows)
            data = encode(dict(schema='cairn.selected-attempt-evidence/1', run_id=self.intent.run_id,
                intent_sha256=self.intent_sha256, source_manifest_sha256=self.intent.source_manifest_sha256,
                stream=report, end_observation=end, selected_success=success,
                success_claimed=False, validity_decided=False, task_acceptance=False,
                return_receipt=False, standalone_file_is_closure=False,
                basis='selected reported channels; external owner must bind end packet, '
                      'actual worker return/release and process/input observations'))
            require(len(data) <= SELECTED_LIMIT, 'attempt_selected_bounds')
            self._write(data, 'OBSERVATIONS.json' if success is not None else 'ATTEMPT-EVIDENCE.json')
            receipt = FailurePrepared(self.intent.run_id, self.intent_sha256,
                                      hashlib.sha256(data).hexdigest(), len(data))
            self.state = 'prepared'
            return receipt
        except RetentionError as exc:
            code = str(exc)
        except OSError:
            code = 'attempt_retention_io'
        except Exception:
            code = 'attempt_internal_refusal'
        except BaseException:
            # Preserve interrupt; never log its traceback or private locals.
            raise
        report, data, receipt, composer, hook_rows, end, success = None, b'', None, None, None, None, None
        raise RetentionError(code)

    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        code = None
        try:
            meta = os.fstat(fd)
            require((meta.st_dev, meta.st_ino) == self.identity, 'attempt_directory_custody')
        except RetentionError as exc:
            code = str(exc)
        except OSError:
            code = 'attempt_close_io'
        finally:
            try:
                os.close(fd)
            except OSError:
                code = code or 'attempt_close_io'
        if code is not None:
            self.close_unknown = True
            raise RetentionError(code)
