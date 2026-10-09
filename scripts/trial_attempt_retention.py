"""Selected failure-channel storage for a fixed owned composition worker.

No raw dictionary ingestion, executor, process authentication or grading API.
The worker validates its end packet separately; its external owner observes
actual return/release and binds this selected file to that closed attempt.
"""
import dataclasses
import hashlib
import os
import stat

from selected_native.retention import Retention, RetentionError, SELECTED_LIMIT, encode, require
from trial_coding_stream import CodingStream


@dataclasses.dataclass(frozen=True)
class FailurePrepared:
    run_id: str
    intent_sha256: str
    manifest_sha256: str
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

    def _write(self, data):
        self._check_directory()
        fd = os.open('ATTEMPT-EVIDENCE.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
            written = os.fstat(target.fileno())
        os.fsync(self.fd)
        self._check_directory()
        fd = os.open('ATTEMPT-EVIDENCE.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as source:
            actual = os.fstat(source.fileno())
            require(stat.S_ISREG(actual.st_mode) and actual.st_uid == os.geteuid()
                    and stat.S_IMODE(actual.st_mode) == 0o600 and actual.st_nlink == 1
                    and actual.st_size == len(data), 'attempt_readback_custody')
            require((actual.st_dev, actual.st_ino) == (written.st_dev, written.st_ino),
                    'attempt_readback_identity')
            returned = source.read(SELECTED_LIMIT+1)
        require(returned == data, 'attempt_readback_mismatch')

    def prepare(self, composer, hook_rows):
        require(self.state == 'open' and self.fd is not None, 'attempt_writer_unavailable')
        self.state = 'failed'  # A refusal, I/O fault or interrupt consumes this writer.
        code, report, data, receipt = None, None, b'', None
        try:
            require(type(composer) is CodingStream and not composer.closed,
                    'attempt_composer_type')
            require(composer.reader.expected['session_id'] == self.intent.run_id,
                    'attempt_run_mismatch')
            require(self.retention.state != 'prepared', 'attempt_success_already_prepared')
            report = composer.observations(hook_rows)
            data = encode(dict(schema='cairn.selected-attempt-evidence/1', run_id=self.intent.run_id,
                intent_sha256=self.intent_sha256, source_manifest_sha256=self.intent.source_manifest_sha256,
                stream=report, success_claimed=False, validity_decided=False, task_acceptance=False,
                return_receipt=False, standalone_file_is_closure=False,
                basis='selected reported channels; external owner must bind end packet, '
                      'actual worker return/release and process/input observations'))
            require(len(data) <= SELECTED_LIMIT, 'attempt_selected_bounds')
            self._write(data)
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
        report, data, receipt, composer, hook_rows = None, b'', None, None, None
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
