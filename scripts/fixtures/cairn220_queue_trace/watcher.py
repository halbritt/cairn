"""Observe one disposable watch cycle on its validated installed lifecycle bundle."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
import types

from trace_io import append_trace, create_trace


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


@contextmanager
def _observed_queue(_queue, descriptor):
    # Each context owns one fresh bundle module; no global import is patched.
    last_receipt_id = None
    lock = threading.Lock()
    receivers = []

    def _write(stage, outcome, error=None, code=None, flags=None, digests=None):
        with lock:
            append_trace(descriptor, stage, outcome, error, code, flags, digests)

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
                decoded = json.loads(message)
            except (TypeError, ValueError):
                return message
            if isinstance(decoded, dict) and decoded.get("method") == "item/started":
                _write("native_item_started", "observed")
            return message

        connection.recv = _observed_recv
        receivers.append((connection, original_recv))
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
        nonlocal last_receipt_id
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
                        and params.get("queuedSubmissionId") == last_receipt_id)
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
                last_receipt_id = receipt_id if isinstance(receipt_id, str) and receipt_id else None
                flags["receipt_confirmed"] = last_receipt_id is not None
                flags["delivery_id_matches"] = bool(
                    isinstance(params, dict) and isinstance(params.get("clientUserMessageId"), str)
                    and client_id == params["clientUserMessageId"])
                if last_receipt_id:
                    digests["receipt_id_sha256"] = hashlib.sha256(last_receipt_id.encode()).hexdigest()
                if isinstance(params, dict) and isinstance(params.get("clientUserMessageId"), str):
                    digests["delivery_id_sha256"] = hashlib.sha256(
                        params["clientUserMessageId"].encode()).hexdigest()
            elif stage == "queue_start":
                response = value if isinstance(value, dict) else {}
                turn = response.get("turn") if isinstance(response.get("turn"), dict) else {}
                turn_id = turn.get("id")
                flags["receipt_matches_start"] = bool(
                    isinstance(params, dict) and isinstance(params.get("queuedSubmissionId"), str)
                    and params.get("queuedSubmissionId") == last_receipt_id)
                flags["turn_started"] = isinstance(turn_id, str) and bool(turn_id)
                if flags["turn_started"]:
                    digests["turn_id_sha256"] = hashlib.sha256(turn_id.encode()).hexdigest()
            _write(stage, "after", flags=flags, digests=digests)
        return value

    _queue._open = _observed_open
    _queue._admit = _observed_admit
    _queue._Rpc.__call__ = _observed_rpc

    try:
        _write("observer_attach", "after")
        yield
    finally:
        for connection, original_recv in receivers:
            connection.recv = original_recv
        _queue._open = _original_open
        _queue._admit = _original_admit
        _queue._Rpc.__call__ = _original_rpc


def _installed_coordination(entry):
    path = Path(entry)
    if not path.is_absolute() or path.name != "coordination.py":
        raise ValueError("expected an absolute installed coordination entrypoint")
    loader_path = path.with_name("lifecycle_bundle.py")
    limit = 2 * 1024 * 1024
    descriptor = os.open(loader_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                or before.st_uid != os.geteuid() or before.st_mode & 0o022):
            raise ValueError("bundle loader is not an owner-controlled bounded regular file")
        raw = source.read(limit + 1)
        after = os.fstat(source.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("bundle loader changed during read")
    loader = types.ModuleType("cairn220_installed_bundle")
    loader.__file__ = str(loader_path)
    exec(compile(raw, str(loader_path), "exec"), loader.__dict__)
    if loader.INSTALLED is not True:
        raise ValueError("queue observer requires a staged installed bundle")
    bundle = loader.Bundle(path.parent, bootstrap_source=raw, entry_path=path, entry_name="coordination.py")
    return bundle.load("coordination", entry_path=path)


@contextmanager
def observed_watcher(entry, trace_path):
    """Attach to the queue used by this installed coordinator, then restore it."""
    descriptor = create_trace(str(trace_path))
    try:
        append_trace(descriptor, "trace", "started")
        try:
            coordination = _installed_coordination(entry)
            queue = coordination.queue_helper()
        except BaseException as exc:
            append_trace(descriptor, "observer_attach", "failed", error=type(exc).__name__)
            raise
        with _observed_queue(queue, descriptor):
            yield coordination
    finally:
        os.close(descriptor)


def main():
    arguments = sys.argv[1:]
    if len(arguments) < 3 or arguments[1] != "watch" or "--once" not in arguments[2:]:
        raise ValueError("fixture observer requires coordination.py watch --once")
    previous = sys.argv
    try:
        sys.argv = arguments
        with observed_watcher(arguments[0], os.environ["CAIRN220_QUEUE_TRACE"]) as coordination:
            return coordination.main()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
