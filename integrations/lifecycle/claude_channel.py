"""Write one verified wake through a local Claude channel bridge socket."""
import json
from pathlib import Path
import socket
import struct
import time


REPLY_TIMEOUT_SECONDS = 4


class ChannelError(Exception):
    """The write may have reached the transport; never resend it."""


class ChannelUnavailable(ChannelError):
    """A checked precondition refused before anything was sent."""


def _bridge_identity(pid):
    """Read one process identity, or raise when it is gone."""
    raw = Path(f'/proc/{pid}/stat').read_text()
    start = int(raw.rsplit(')', 1)[1].split()[19])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return start, boot


def write(endpoint, bridge, parent, content, meta):
    """One bounded channel write to an already-selected bridge.

    The registry named this bridge for exactly one native parent; the socket
    must still belong to that bridge process (SO_PEERCRED plus its recorded
    start/boot identity) before anything is sent. The bridge re-verifies the
    parent itself, so a stale or foreign request cannot be delivered.
    """
    line = json.dumps(dict(parent=parent, content=content, meta=meta), separators=(',', ':'))
    if len(line.encode()) > 8192:
        raise ChannelUnavailable('claude channel request exceeds the line limit')
    connection = socket.socket(socket.AF_UNIX)
    attempted = False
    try:
        connection.settimeout(4)
        connection.connect(endpoint)
        peer, peer_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != bridge['pid']:
            raise ChannelUnavailable('claude channel socket belongs to a different process')
        start, boot = _bridge_identity(peer)
        if start != bridge['start'] or boot != bridge['boot']:
            raise ChannelUnavailable('claude channel bridge identity has changed')
        # From here the write is attempted: even a partial send may have
        # reached the peer, so every failure is uncertain, never retryable.
        attempted = True
        connection.sendall(line.encode() + b'\n')
        reply = b''
        deadline = time.monotonic() + REPLY_TIMEOUT_SECONDS
        while b'\n' not in reply:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChannelError('claude channel reply timed out; submission uncertain')
            connection.settimeout(remaining)
            try:
                chunk = connection.recv(4096)
            except socket.timeout as exc:
                raise ChannelError('claude channel reply timed out; submission uncertain') from exc
            if not chunk:
                raise ChannelError('claude channel closed before replying')
            reply += chunk
            if len(reply) > 8192:
                raise ChannelError('claude channel reply exceeds the line limit')
        message = json.loads(reply.split(b'\n', 1)[0])
        if not isinstance(message, dict):
            raise ChannelError('claude channel reply is not an object')
        status = message.get('status')
    except ChannelUnavailable:
        raise
    except (OSError, ValueError) as exc:
        if not attempted:
            raise ChannelUnavailable('claude channel endpoint is unavailable') from exc
        raise ChannelError('claude channel outcome is uncertain; do not resend') from exc
    finally:
        connection.close()
    if status == 'written':
        return  # Written to the transport only; processing is not established.
    if status == 'unavailable':
        raise ChannelUnavailable('claude channel refused this submission before sending')
    raise ChannelError('claude channel write is uncertain; do not resend')
