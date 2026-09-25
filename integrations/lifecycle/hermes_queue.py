"""Queue a wakeup on an already running Hermes session without touching its composer or interrupting a busy turn."""
import json
from pathlib import Path
import socket
import struct
import time


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A precondition refused before the queue submission was sent."""


class RequestMismatchError(QueueError):
    """Cancellation refused because active turn belongs to another request or owner."""


def _discard(transport):
    try:
        transport.close()
    except OSError:
        pass


def _open(endpoint, process):
    """Connect and verify the exact native process before anything is sent."""
    transport = socket.socket(socket.AF_UNIX)
    try:
        transport.settimeout(4)
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


def enqueue(endpoint, process, native_id, text, client_id, expected_session_id=None, request_id=None, delivery_id=None):
    """Submit a queued wakeup to an active Hermes session.

    Returns (queued_submission_id, started). QueueUnavailable proves nothing
    was sent. Any failure during or after writing is uncertain and must never
    be automatically resent.
    """
    transport = _open(endpoint, process)
    try:
        params = {
            'session_id': native_id,
            'text': text,
            'client_id': client_id,
            'expected_session_id': expected_session_id or native_id,
        }
        if request_id:
            params['request_id'] = request_id
        if delivery_id:
            params['delivery_id'] = delivery_id
        req = {
            'id': 1,
            'method': 'session/queue_message',
            'params': params,
        }
        payload = (json.dumps(req) + '\n').encode('utf-8')
        transport.sendall(payload)

        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native queue outcome is uncertain; Hermes closed connection without response; do not automatically resend')
        msg = json.loads(line)
        if not isinstance(msg, dict) or type(msg.get('id')) is not int or msg['id'] != req['id']:
            raise QueueError('native queue outcome is uncertain; invalid bridge response identity')
        if 'error' in msg:
            err = msg['error']
            if not isinstance(err, dict) or not isinstance(err.get('message'), str):
                raise QueueError('native queue outcome is uncertain; invalid bridge error response')
            code = err.get('code')
            message = err.get('message', '')
            if code in (-32001, -32002) or 'SESSION_NOT_FOUND' in message or 'SESSION_MISMATCH' in message:
                raise QueueUnavailable(f'native session unavailable: {message}')
            raise QueueError(f'native Hermes refused queue_message: {message}')

        result = msg.get('result')
        if not isinstance(result, dict):
            raise QueueError(f'invalid bridge response schema: expected dict result, got {type(result).__name__}')
        if result.get('queued') is not True:
            raise QueueError('invalid bridge response schema: expected queued=True in result')
        if 'started' in result and isinstance(result['started'], bool):
            started = result['started']
        elif 'busy' in result and isinstance(result['busy'], bool):
            started = not result['busy']
        else:
            raise QueueError('invalid bridge response schema: missing started or busy boolean field')
        if result.get('session_id') != native_id:
            raise QueueError(f"session ID mismatch in response: expected {native_id}, got {result.get('session_id')}")
        if request_id and result.get('request_id') != request_id:
            raise QueueError(f"request ID mismatch in response: expected {request_id}, got {result.get('request_id')}")
        if delivery_id and result.get('delivery_id') != delivery_id:
            raise QueueError(f"delivery ID mismatch in response: expected {delivery_id}, got {result.get('delivery_id')}")
        queued_id = result.get('queued_id')
        if not isinstance(queued_id, str) or queued_id != client_id:
            raise QueueError('native queue outcome is uncertain; queue submission ID mismatch')
        return queued_id, started
    except QueueUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError('native queue outcome is uncertain; do not automatically resend') from exc
    finally:
        _discard(transport)


def abort(endpoint, process, native_id, expected_request_id=None, expected_turn_id=None):
    """Abort an active or pending turn on Hermes for this exact request.

    Atomically checks request ownership. If active turn belongs to another
    request or owner prompt, raises RequestMismatchError without signalling
    interruption to protect the owner turn.

    Returns dict with aborted, turn_stop, tools outcomes.
    """
    transport = _open(endpoint, process)
    try:
        params = {
            'session_id': native_id,
        }
        if expected_request_id:
            params['expected_request_id'] = expected_request_id
        if expected_turn_id:
            params['expected_turn_id'] = expected_turn_id

        req = {
            'id': 2,
            'method': 'session/abort',
            'params': params,
        }
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native Hermes closed connection during abort; outcome uncertain; do not resend')
        msg = json.loads(line)
        if 'error' in msg:
            err = msg['error']
            code = err.get('code')
            message = err.get('message', '')
            if code in (-32001, -32003, -32004) or 'REQUEST_MISMATCH' in message or 'TURN_MISMATCH' in message or 'UNSPECIFIED_ABORT' in message:
                raise RequestMismatchError(f'abort refused: {message}')
            if code == -32002 or 'SESSION_MISMATCH' in message or 'SESSION_NOT_FOUND' in message:
                raise QueueUnavailable(f'native session unavailable: {message}')
        result = msg.get('result')
        if not isinstance(result, dict):
            raise QueueError(f'invalid bridge abort response schema: expected dict result, got {type(result).__name__}')
        if 'aborted' not in result or not isinstance(result['aborted'], bool):
            raise QueueError('invalid bridge abort response schema: missing or invalid boolean "aborted" field')
        if 'turn_stop' not in result or not isinstance(result['turn_stop'], str):
            raise QueueError('invalid bridge abort response schema: missing or invalid string "turn_stop" field')
        return result
    except (QueueUnavailable, RequestMismatchError):
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError('native Hermes abort outcome is uncertain; do not resend') from exc
    finally:
        _discard(transport)


def status(endpoint, process, native_id):
    """Get status of a Hermes session."""
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
            raise QueueError('native Hermes closed connection without response')
        msg = json.loads(line)
        if not isinstance(msg, dict) or type(msg.get('id')) is not int or msg['id'] != req['id']:
            raise QueueError('invalid bridge status response identity')
        if 'error' in msg:
            raise QueueError(f"native Hermes status error: {msg['error']}")
        result = msg.get('result')
        if (not isinstance(result, dict) or result.get('session_id') != native_id or
                not isinstance(result.get('status'), str) or not result['status']):
            raise QueueError('invalid bridge status response schema')
        return result
    finally:
        _discard(transport)


def tools_status(endpoint, process, native_id, tool_ids=None):
    """Query status of tool processes associated with a session."""
    transport = _open(endpoint, process)
    try:
        req = {
            'id': 4,
            'method': 'session/tools_status',
            'params': {
                'session_id': native_id,
                'tool_ids': tool_ids or [],
            }
        }
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native Hermes closed connection without response')
        msg = json.loads(line)
        if not isinstance(msg, dict) or type(msg.get('id')) is not int or msg['id'] != req['id']:
            raise QueueError('invalid bridge tools_status response identity')
        if 'error' in msg:
            raise QueueError(f"native Hermes tools_status error: {msg['error']}")
        result = msg.get('result')
        if (not isinstance(result, dict) or result.get('session_id') != native_id or
                not isinstance(result.get('tools'), list)):
            raise QueueError('invalid bridge tools_status response schema')
        return result
    finally:
        _discard(transport)
