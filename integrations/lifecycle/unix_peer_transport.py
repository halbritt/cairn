"""Authoritative Unix domain socket peer verification and bounded JSON framing."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

MAX_PEER_BYTES = 1024 * 1024
DEFAULT_LINE_LIMIT = 8192
DEFAULT_CONNECT_TIMEOUT = 4.0
DEFAULT_PROBE_TIMEOUT = 0.25


def load_sibling_module(name, caller_file=None):
    """Load a sibling lifecycle module across package, script, and staged contexts."""
    if name in sys.modules:
        module = sys.modules[name]
        if module is None:
            raise ImportError(f"import of {name} halted; None in sys.modules")
        return module
    qualified = f"integrations.lifecycle.{name}"
    if qualified in sys.modules:
        module = sys.modules[qualified]
        if module is None:
            raise ImportError(f"import of {qualified} halted; None in sys.modules")
        return module
    candidates = []
    if caller_file:
        candidates.append(Path(caller_file).resolve().parent / f"{name}.py")
    candidates.append(Path(__file__).resolve().parent / f"{name}.py")
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidates.append(base / "integrations" / "lifecycle" / f"{name}.py")
    candidates.append(
        Path("/home/halbritt/.cache/agent-worktrees/cairn/cairn-244-246-lifecycle/integrations/lifecycle")
        / f"{name}.py"
    )
    candidates.append(Path.home() / "git" / "cairn" / "integrations" / "lifecycle" / f"{name}.py")
    for path in candidates:
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if exists:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(name, None)
                    raise
                return module
    return importlib.import_module(name)


def read_boot_id(proc="/proc"):
    """Read the current kernel boot_id string from /proc."""
    return Path(f"{proc}/sys/kernel/random/boot_id").read_text().strip()


def parse_proc_stat(pid="self", proc="/proc"):
    """Parse /proc/<pid>/stat after the comm field into process identity fields."""
    raw = Path(f"{proc}/{pid}/stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    resolved_pid = int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else pid
    return dict(
        pid=resolved_pid,
        state=fields[0],
        parent=int(fields[1]),
        pgid=int(fields[2]),
        sid=int(fields[3]),
        start=int(fields[19]),
    )


def process_start_ticks(pid="self", proc="/proc"):
    """Boot-relative start time of a process in clock ticks."""
    return parse_proc_stat(pid, proc=proc)["start"]


def process_identity_tuple(pid, proc="/proc"):
    """Return (start_ticks, boot_id) for pid."""
    return process_start_ticks(pid, proc=proc), read_boot_id(proc=proc)


def process_reference(pid, proc="/proc"):
    """Build a full process reference dict matching coordination.process_reference."""
    stat_info = parse_proc_stat(pid, proc=proc)
    return dict(
        pid=pid,
        start=stat_info["start"],
        state=stat_info["state"],
        parent=stat_info["parent"],
        boot=read_boot_id(proc=proc),
    )


def process_alive(ref, proc="/proc"):
    """Return True if the process reference still matches a live non-zombie process."""
    try:
        current = process_reference(ref["pid"], proc=proc)
    except (FileNotFoundError, ProcessLookupError, KeyError, TypeError, ValueError, IndexError):
        return False
    return current["state"] not in ("Z", "X") and all(
        current[k] == ref[k] for k in ("pid", "start", "boot")
    )


def peer_credentials(sock):
    """Return (pid, uid, gid) from SO_PEERCRED on an AF_UNIX socket."""
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def verify_peer_credentials(
    sock,
    process,
    *,
    owner="process",
    expected_uid=None,
    unavailable_cls=RuntimeError,
    wrong_process_message="native socket belongs to a different process",
    wrong_user_message="native socket belongs to another user",
    identity_changed_message="native process identity has changed",
    identity_reader=None,
    proc="/proc",
):
    """Verify SO_PEERCRED and /proc/<pid>/stat + boot_id identity before any write."""
    peer, peer_uid, _ = peer_credentials(sock)
    checked = peer
    if owner == "process":
        if peer != process["pid"]:
            raise unavailable_cls(wrong_process_message)
        if expected_uid is not None and peer_uid != expected_uid:
            raise unavailable_cls(wrong_user_message)
    else:
        target_uid = os.geteuid() if expected_uid is None else expected_uid
        if peer_uid != target_uid:
            raise unavailable_cls(wrong_user_message)
        checked = process["pid"]
    if identity_reader is not None:
        start, boot = identity_reader(checked)
    else:
        start, boot = process_identity_tuple(checked, proc=proc)
    if start != process["start"] or boot != process["boot"]:
        raise unavailable_cls(identity_changed_message)
    if owner == "process":
        peer_start, peer_boot = start, boot
    elif identity_reader is not None:
        peer_start, peer_boot = identity_reader(peer)
    else:
        peer_start, peer_boot = process_identity_tuple(peer, proc=proc)
    return dict(pid=peer, start=peer_start, boot=peer_boot)


def close_quietly(transport, connection=None):
    """Close a socket or wrapped connection ignoring OSError."""
    try:
        if connection is not None:
            connection.close(timeout=0)
        elif transport is not None:
            transport.close()
    except OSError:
        pass


def open_verified_socket(
    endpoint,
    process,
    *,
    owner="process",
    timeout=DEFAULT_CONNECT_TIMEOUT,
    unavailable_cls=RuntimeError,
    wrong_process_message="native socket belongs to a different process",
    wrong_user_message="native socket belongs to another user",
    identity_changed_message="native process identity has changed",
    endpoint_unavailable_message="native queue endpoint is unavailable",
    identity_reader=None,
    return_identity=False,
    proc="/proc",
):
    """Connect to an AF_UNIX endpoint and verify peer credentials before returning."""
    transport = socket.socket(socket.AF_UNIX)
    try:
        transport.settimeout(timeout)
        transport.connect(endpoint)
        identity = verify_peer_credentials(
            transport,
            process,
            owner=owner,
            unavailable_cls=unavailable_cls,
            wrong_process_message=wrong_process_message,
            wrong_user_message=wrong_user_message,
            identity_changed_message=identity_changed_message,
            identity_reader=identity_reader,
            proc=proc,
        )
        return (transport, identity) if return_identity else transport
    except unavailable_cls:
        close_quietly(transport)
        raise
    except OSError as exc:
        close_quietly(transport)
        raise unavailable_cls(endpoint_unavailable_message) from exc


def cleanup_stale_socket(path, *, probe_timeout=DEFAULT_PROBE_TIMEOUT):
    """Probe an existing AF_UNIX socket path and unlink only if definitely stale.

    Returns True when the path is clear to bind, or False when a live/unknown listener holds it.
    """
    socket_path = str(path)
    if not os.path.exists(socket_path):
        return True
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.settimeout(probe_timeout)
        probe.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError):
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        return True
    except OSError:
        return False
    else:
        return False
    finally:
        close_quietly(probe)


def bind_private_socket(path, *, backlog=4, accept_timeout=1.0, probe_timeout=DEFAULT_PROBE_TIMEOUT):
    """Clean up a stale socket if safe, bind AF_UNIX, chmod 0600, and listen."""
    socket_path = str(path)
    if not cleanup_stale_socket(socket_path, probe_timeout=probe_timeout):
        return None
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(socket_path)
        os.chmod(socket_path, 0o600)
        server.listen(backlog)
        if accept_timeout is not None:
            server.settimeout(accept_timeout)
        return server
    except Exception:
        close_quietly(server)
        raise


class BoundedPeerSocket:
    """Limit reads by a preconnected-socket client to a hard byte budget."""

    def __init__(self, transport, *, max_bytes=MAX_PEER_BYTES, error_cls=RuntimeError):
        self.transport = transport
        self.received = 0
        self.max_bytes = max_bytes
        self.error_cls = error_cls

    def recv(self, size, *args, **kwargs):
        if self.received >= self.max_bytes:
            raise self.error_cls("native queue response exceeded byte limit")
        chunk = self.transport.recv(min(size, self.max_bytes + 1 - self.received), *args, **kwargs)
        self.received += len(chunk)
        if self.received > self.max_bytes:
            raise self.error_cls("native queue response exceeded byte limit")
        return chunk

    def __getattr__(self, name):
        if name.startswith("recv") or name == "makefile":
            raise self.error_cls("native queue client requested an unbounded socket read")
        return getattr(self.transport, name)


def format_json_line(
    payload,
    *,
    compact=False,
    max_bytes=None,
    too_large_cls=ValueError,
    too_large_message="request exceeds the line limit",
):
    """Serialize payload as a newline-terminated UTF-8 JSON line with optional size check."""
    line = json.dumps(payload, separators=(",", ":") if compact else None)
    encoded = line.encode("utf-8")
    if max_bytes is not None and len(encoded) > max_bytes:
        raise too_large_cls(too_large_message)
    return encoded + b"\n"


def send_json_line(
    transport,
    payload,
    *,
    compact=False,
    max_bytes=None,
    too_large_cls=ValueError,
    too_large_message="request exceeds the line limit",
):
    """Format and send one newline-terminated UTF-8 JSON line."""
    framed = format_json_line(
        payload,
        compact=compact,
        max_bytes=max_bytes,
        too_large_cls=too_large_cls,
        too_large_message=too_large_message,
    )
    transport.sendall(framed)
    return framed


def recv_json_line(
    transport,
    *,
    timeout=None,
    max_bytes=MAX_PEER_BYTES,
    chunk_size=4096,
    require_newline=True,
    timeout_cls=socket.timeout,
    timeout_message="reply timed out",
    eof_cls=EOFError,
    eof_message="connection closed before replying",
    overflow_cls=ValueError,
    overflow_message="reply exceeds the line limit",
):
    """Read and parse one bounded newline-delimited JSON response from transport."""
    reply = b""
    deadline = (time.monotonic() + timeout) if timeout is not None else None
    while b"\n" not in reply:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise timeout_cls(timeout_message)
            transport.settimeout(remaining)
        try:
            chunk = transport.recv(chunk_size)
        except socket.timeout as exc:
            if deadline is not None:
                raise timeout_cls(timeout_message) from exc
            raise
        if not chunk:
            if require_newline or not reply:
                raise eof_cls(eof_message)
            break
        reply += chunk
        if len(reply) > max_bytes:
            raise overflow_cls(overflow_message)
    return json.loads(reply.split(b"\n", 1)[0].decode("utf-8"))


def exchange_json_line(transport, payload, *, timeout=None, max_bytes=MAX_PEER_BYTES, eof_cls=EOFError, eof_message="closed connection without response"):
    """Send a JSON request line and read one bounded JSON response line."""
    send_json_line(transport, payload)
    return recv_json_line(
        transport,
        timeout=timeout,
        max_bytes=max_bytes,
        require_newline=False,
        eof_cls=eof_cls,
        eof_message=eof_message,
    )


try:
    _wake_mod = load_sibling_module("wake_transport", __file__)
    WakeTransport = _wake_mod.WakeTransport
    CodexQueueWakeTransport = _wake_mod.CodexQueueWakeTransport
    ClaudeChannelWakeTransport = _wake_mod.ClaudeChannelWakeTransport
    OpenCodeQueueWakeTransport = _wake_mod.OpenCodeQueueWakeTransport
    HermesQueueWakeTransport = _wake_mod.HermesQueueWakeTransport
    TerminalWakeTransport = _wake_mod.TerminalWakeTransport
    get_wake_transport = _wake_mod.get_wake_transport
    get_harness_wake_transport = _wake_mod.get_harness_wake_transport
except Exception:
    pass
