"""Queue a wakeup on an already running OpenCode session without touching its TUI composer."""
import json
from pathlib import Path
import socket
import struct
import time


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A precondition refused before the queue submission was sent."""


BUSY_CODE = -32600


def _discard(transport):
    try:
        transport.close()
    except OSError:
        pass


def _open(endpoint, process):
    """Connect and verify the exact native process before anything is sent."""
    transport = socket.socket(socket.AF_UNIX)
    try:
        transport.settimeout(4.0)
        transport.connect(endpoint)
        peer, _, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != process['pid']:
            raise QueueUnavailable('native socket belongs to a different process')
        start = int(Path(f'/proc/{peer}/stat').read_text().rsplit(')', 1)[1].split()[19])
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if start != process['start'] or boot != process['boot']:
            raise QueueUnavailable('native process identity has changed')
        return transport
    except QueueUnavailable:
        _discard(transport)
        raise
    except OSError as exc:
        _discard(transport)
        raise QueueUnavailable('native queue endpoint is unavailable') from exc


def enqueue(endpoint, process, native_id, text, client_id, expected_session_id=None):
    """Submit an atomically admitted native wakeup to an active OpenCode session.

    Returns (queued_submission_id, started). QueueUnavailable proves nothing
    was sent, so a later cycle may retry. Any failure during or after writing
    is uncertain and must never be re-sent.
    """
    if not native_id or not isinstance(native_id, str) or not native_id.strip():
        raise QueueUnavailable('native_id must be a nonempty string')
    exp_id = expected_session_id or native_id
    if not exp_id or not isinstance(exp_id, str) or not exp_id.strip():
        raise QueueUnavailable('expected_session_id must be a nonempty string')

    transport = _open(endpoint, process)
    try:
        req = {
            'id': 1,
            'method': 'session/prompt_async',
            'params': {
                'session_id': native_id,
                'text': text,
                'client_id': client_id,
                'expected_session_id': exp_id,
            }
        }
        payload = (json.dumps(req) + '\n').encode('utf-8')
        transport.sendall(payload)

        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native queue outcome is uncertain; OpenCode closed connection without response; do not automatically resend')
        msg = json.loads(line)
        if 'error' in msg:
            err = msg['error']
            code = err.get('code')
            message = err.get('message', '')
            if 'BUSY' in message:
                # Busy refusal: turn is running; queued input waits, never merges
                return client_id, False
            if code in (-32001, -32002, -32004) or 'SESSION_NOT_FOUND' in message or 'SESSION_MISMATCH' in message:
                raise QueueUnavailable(f'native session unavailable: {message}')
            raise QueueError(f'native OpenCode refused prompt_async: {message}')

        result = msg.get('result', {})
        queued_id = result.get('queued_id', client_id)
        started = result.get('started', True)
        return queued_id, started
    except QueueUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError('native queue outcome is uncertain; do not automatically resend') from exc
    finally:
        _discard(transport)


def abort(endpoint, process, native_id, expected_turn_id=None):
    """Cancel only the exact request owned by the patched native session boundary."""
    if not isinstance(expected_turn_id, str) or not expected_turn_id.strip():
        raise QueueUnavailable('expected request identity is required')
    transport = _open(endpoint, process)
    try:
        req = {
            'id': 2,
            'method': 'session/abort',
            'params': {
                'session_id': native_id,
                'expected_turn_id': expected_turn_id,
            }
        }
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            return False
        msg = json.loads(line)
        if 'error' in msg:
            err = msg['error']
            # Server returns UNSUPPORTED_CONTROL / -32004 to protect newer owner work
            if err.get('code') == -32004 or 'UNSUPPORTED_CONTROL' in err.get('message', ''):
                raise QueueUnavailable(f"native abort refused: {err.get('message')}")
            return False
        return bool(msg.get('result', {}).get('aborted', False))
    except QueueUnavailable:
        raise
    except Exception:
        return False
    finally:
        _discard(transport)


def status(endpoint, process, native_id):
    """Get status of an OpenCode session."""
    transport = _open(endpoint, process)
    try:
        req = {
            'id': 3,
            'method': 'session/status',
            'params': {
                'session_id': native_id,
            }
        }
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native OpenCode closed connection without response')
        msg = json.loads(line)
        if 'error' in msg:
            raise QueueError(f"native OpenCode status error: {msg['error']}")
        return msg.get('result', {})
    finally:
        _discard(transport)
