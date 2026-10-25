"""Opt-in, fixed-code stage trace for the CAIRN-220 Codex queue probe.

Load this directory first on PYTHONPATH inside the disposable watcher sandbox
and set CAIRN220_QUEUE_TRACE to an owner-only path mounted from the host. The
observer wraps the production codex_queue methods without changing results.
Only fixed stage names, exception classes, and a bounded native integer code
are written. No prompts, exception messages, paths, or tokens are retained.
"""

import hashlib
import os
import threading
from trace_io import append_trace, create_trace


_TRACE = os.environ.get("CAIRN220_QUEUE_TRACE")
_LOCK = threading.Lock()
_TRACE_FD = None
_LAST_RECEIPT_ID = None


def _write(stage, outcome, error=None, code=None, flags=None, digests=None):
    if _TRACE_FD is None:
        return
    with _LOCK:
        append_trace(_TRACE_FD, stage, outcome, error, code, flags, digests)


def _refusal_class(exc):
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if code == -32600 and message == "thread already has an active or pending turn":
        return "busy"
    if code == -32600 and isinstance(message, str) and message.startswith("queued submission not found:"):
        return "submission_not_found"
    if code == -32600:
        return "invalid_request_other"
    return "native_refusal_other"


if _TRACE:
    # Establish the private trace before startup assertions or queue work.
    _TRACE_FD = create_trace(_TRACE)
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
            connection = value[0]
            original_recv = connection.recv

            def _observed_recv(*recv_args, **recv_kwargs):
                message = original_recv(*recv_args, **recv_kwargs)
                try:
                    decoded = __import__("json").loads(message)
                except (TypeError, ValueError):
                    return message
                if isinstance(decoded, dict) and decoded.get("method") == "item/started":
                    _write("native_item_started", "observed")
                return message

            connection.recv = _observed_recv
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
            global _LAST_RECEIPT_ID
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
                    flags = {}
                    if stage == "queue_start":
                        flags["receipt_matches_start"] = bool(
                            isinstance(params, dict) and isinstance(params.get("queuedSubmissionId"), str)
                            and params.get("queuedSubmissionId") == _LAST_RECEIPT_ID)
                        if isinstance(exc, _queue.QueueRefused):
                            flags["native_refusal_class"] = _refusal_class(exc)
                    _write(stage, "refused", type(exc).__name__, getattr(exc, "code", None), flags=flags)
                raise
            if stage:
                flags = {}
                digests = {}
                if stage == "queue_add":
                    queued = value.get("queuedSubmission") if isinstance(value, dict) else None
                    receipt_id = queued.get("id") if isinstance(queued, dict) else None
                    client_id = queued.get("clientUserMessageId") if isinstance(queued, dict) else None
                    _LAST_RECEIPT_ID = receipt_id if isinstance(receipt_id, str) and receipt_id else None
                    flags["receipt_confirmed"] = _LAST_RECEIPT_ID is not None
                    flags["delivery_id_matches"] = bool(
                        isinstance(params, dict) and isinstance(params.get("clientUserMessageId"), str)
                        and client_id == params["clientUserMessageId"])
                    if _LAST_RECEIPT_ID:
                        digests["receipt_id_sha256"] = hashlib.sha256(_LAST_RECEIPT_ID.encode()).hexdigest()
                    if isinstance(params, dict) and isinstance(params.get("clientUserMessageId"), str):
                        digests["delivery_id_sha256"] = hashlib.sha256(
                            params["clientUserMessageId"].encode()).hexdigest()
                elif stage == "queue_start":
                    response = value if isinstance(value, dict) else {}
                    turn = response.get("turn") if isinstance(response.get("turn"), dict) else {}
                    turn_id = turn.get("id")
                    flags["receipt_matches_start"] = bool(
                        isinstance(params, dict) and isinstance(params.get("queuedSubmissionId"), str)
                        and params.get("queuedSubmissionId") == _LAST_RECEIPT_ID)
                    flags["turn_started"] = isinstance(turn_id, str) and bool(turn_id)
                    if flags["turn_started"]:
                        digests["turn_id_sha256"] = hashlib.sha256(turn_id.encode()).hexdigest()
                _write(stage, "after", flags=flags, digests=digests)
            return value

        _queue._open = _observed_open
        _queue._admit = _observed_admit
        _queue._Rpc.__call__ = _observed_rpc
