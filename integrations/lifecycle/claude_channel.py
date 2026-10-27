"""Write one verified wake through a local Claude channel bridge socket."""
import importlib
import importlib.util
import os
from pathlib import Path
import sys


def _runtime_bundle():
    if "_CAIRN_BUNDLE" in globals():
        return _CAIRN_BUNDLE
    path = Path(__file__).resolve().with_name("lifecycle_bundle.py")
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError("lifecycle bundle loader is not a bounded regular file")
        raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) != info.st_size:
            raise ValueError("lifecycle bundle loader changed during read")
    spec = importlib.util.spec_from_file_location("cairn_lifecycle_bundle", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module.Bundle(path.parent, bootstrap_source=raw, entry_path=__file__, entry_name="claude_channel.py")


_BUNDLE = _runtime_bundle()


_transport = _BUNDLE.load("unix_peer_transport")

REPLY_TIMEOUT_SECONDS = 4


class ChannelError(Exception):
    """The write may have reached the transport; never resend it."""


class ChannelUnavailable(ChannelError):
    """A checked precondition refused before anything was sent."""


def _bridge_identity(pid):
    """Read one process identity, or raise when it is gone."""
    return _transport.process_identity_tuple(pid)


def write(endpoint, bridge, parent, content, meta):
    """One bounded channel write to an already-selected bridge."""
    framed = _transport.format_json_line(
        dict(parent=parent, content=content, meta=meta),
        compact=True,
        max_bytes=8192,
        too_large_cls=ChannelUnavailable,
        too_large_message="claude channel request exceeds the line limit",
    )
    connection = None
    attempted = False
    try:
        connection = _transport.open_verified_socket(
            endpoint,
            bridge,
            timeout=4,
            unavailable_cls=ChannelUnavailable,
            wrong_process_message="claude channel socket belongs to a different process",
            identity_changed_message="claude channel bridge identity has changed",
            endpoint_unavailable_message="claude channel endpoint is unavailable",
            identity_reader=_bridge_identity,
        )
        attempted = True
        connection.sendall(framed)
        message = _transport.recv_json_line(
            connection,
            timeout=REPLY_TIMEOUT_SECONDS,
            max_bytes=8192,
            require_newline=True,
            timeout_cls=ChannelError,
            timeout_message="claude channel reply timed out; submission uncertain",
            eof_cls=ChannelError,
            eof_message="claude channel closed before replying",
            overflow_cls=ChannelError,
            overflow_message="claude channel reply exceeds the line limit",
        )
        if not isinstance(message, dict):
            raise ChannelError("claude channel reply is not an object")
        status = message.get("status")
    except ChannelUnavailable:
        raise
    except (OSError, ValueError) as exc:
        if not attempted:
            raise ChannelUnavailable("claude channel endpoint is unavailable") from exc
        raise ChannelError("claude channel outcome is uncertain; do not resend") from exc
    finally:
        _transport.close_quietly(connection)
    if status == "written":
        return
    if status == "unavailable":
        raise ChannelUnavailable("claude channel refused this submission before sending")
    raise ChannelError("claude channel write is uncertain; do not resend")
