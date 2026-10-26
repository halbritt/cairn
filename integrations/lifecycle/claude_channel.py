"""Write one verified wake through a local Claude channel bridge socket."""
import importlib
import importlib.util
from pathlib import Path
import sys


def _load_sibling_module(name):
    if name in sys.modules:
        mod = sys.modules[name]
        if mod is None:
            raise ImportError(f"import of {name} halted; None in sys.modules")
        return mod
    qualified = f"integrations.lifecycle.{name}"
    if qualified in sys.modules:
        mod = sys.modules[qualified]
        if mod is None:
            raise ImportError(f"import of {qualified} halted; None in sys.modules")
        return mod
    here = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    candidates = [
        here / f"{name}.py",
        Path("/home/halbritt/.cache/agent-worktrees/cairn/cairn-244-246-lifecycle/integrations/lifecycle") / f"{name}.py",
        Path.home() / "git" / "cairn" / "integrations" / "lifecycle" / f"{name}.py",
    ]
    for path in candidates:
        try:
            if path.is_file():
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                return module
        except OSError:
            continue
    return importlib.import_module(name)


_transport = _load_sibling_module("unix_peer_transport")

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
