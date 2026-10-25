"""Opt-in, fixed-code stage trace for the CAIRN-220 Codex queue probe.

Load this directory first on PYTHONPATH inside the disposable watcher sandbox
and set CAIRN220_QUEUE_TRACE to an owner-only path mounted from the host. The
observer wraps the production codex_queue methods without changing results.
Only fixed stage names, exception classes, and a bounded native integer code
are written. No prompts, exception messages, paths, or tokens are retained.
"""

import json
import os
import threading


_TRACE = os.environ.get("CAIRN220_QUEUE_TRACE")
_LOCK = threading.Lock()
_ALLOWED_ERRORS = {
    "QueueError",
    "QueueUnavailable",
    "QueueRefused",
    "OSError",
    "ValueError",
    "KeyError",
    "TypeError",
    "WebSocketException",
    "ImportError",
}


def _write(stage, outcome, error=None, code=None):
    if not _TRACE:
        return
    row = {"schema": "cairn.codex-queue-stage/1", "stage": stage, "outcome": outcome}
    if error:
        row["error_class"] = error if error in _ALLOWED_ERRORS else "OtherError"
    if type(code) is int and -32768 <= code <= 32767:
        row["native_code"] = code
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with _LOCK:
        descriptor = os.open(_TRACE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


if _TRACE:
    # Establish the private trace before startup assertions or queue work.
    _write("trace", "started")
    try:
        import codex_queue as _queue
    except Exception as _exc:  # Preserve interpreter startup behavior.
        _write("module_import", "failed", type(_exc).__name__)
    else:
        _original_open = _queue._open
        _original_admit = _queue._admit
        _original_rpc = _queue._Rpc.__call__

        def _observed_open(*args, **kwargs):
            _write("_open", "before")
            try:
                value = _original_open(*args, **kwargs)
            except BaseException as exc:
                _write("_open", "refused", type(exc).__name__, getattr(exc, "code", None))
                raise
            _write("_open", "after")
            return value

        def _observed_admit(*args, **kwargs):
            _write("_admit", "before")
            try:
                value = _original_admit(*args, **kwargs)
            except BaseException as exc:
                _write("_admit", "refused", type(exc).__name__, getattr(exc, "code", None))
                raise
            _write("_admit", "after")
            return value

        def _observed_rpc(self, method, params, *args, **kwargs):
            stage = {
                "thread/queue/add": "queue_add",
                "thread/queue/start": "queue_start",
            }.get(method)
            if stage:
                _write(stage, "before")
            try:
                value = _original_rpc(self, method, params, *args, **kwargs)
            except BaseException as exc:
                if stage:
                    _write(stage, "refused", type(exc).__name__, getattr(exc, "code", None))
                raise
            if stage:
                _write(stage, "after")
            return value

        _queue._open = _observed_open
        _queue._admit = _observed_admit
        _queue._Rpc.__call__ = _observed_rpc
