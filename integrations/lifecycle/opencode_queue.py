"""Queue a wakeup on an already running OpenCode session without touching its TUI composer."""
import json
from pathlib import Path
import socket
import struct
import time


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A pre-send refusal or atomic BUSY result proves no native admission."""


class QueueRefused(QueueError):
    """Native admission definitely refused this request; retain it for review."""


class QueueAlreadyAdmitted(QueueError):
    """The idempotent native request ran earlier; use owner-boundary recovery."""


class QueueUnsupported(QueueUnavailable):
    """The process has no native prompt_idle route; use owner boundaries."""


class CancelRefused(QueueError):
    """Native cancellation definitely did not act on any request.

    reason is one of unavailable, unsupported, invalid, session_unavailable,
    turn_mismatch, not_active or native_refused.
    """

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class CancelUncertain(QueueError):
    """The cancel call may have reached native code; its outcome is unknown."""


BUSY_CODE = -32600
# Covers the plugin's 30-second native settle limit plus transport slack.
CANCEL_TIMEOUT = 35.0
CANCEL_REFUSALS = {
    -32601: 'unsupported',
    -32004: 'unsupported',
    -32602: 'invalid',
    -32002: 'session_unavailable',
    -32006: 'turn_mismatch',
    -32007: 'not_active',
    -32003: 'native_refused',
}


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


def _supports(endpoint, process, native_id, capability):
    """Probe a peer-verified bridge for one nonmutating capability flag."""
    transport = _open(endpoint, process)
    try:
        request = {'id': 4, 'method': 'session/capabilities', 'params': {'session_id': native_id}}
        transport.sendall((json.dumps(request) + '\n').encode('utf-8'))
        line = transport.makefile('r', encoding='utf-8').readline()
        if not line:
            return False
        response = json.loads(line)
        result = response.get('result') if isinstance(response, dict) and response.get('id') == 4 else None
        return (isinstance(result, dict) and result.get('session_id') == native_id and
                result.get(capability) is True)
    except (OSError, ValueError, TypeError):
        return False
    finally:
        _discard(transport)


def supports_idle(endpoint, process, native_id):
    """Probe a peer-verified bridge for its nonmutating prompt_idle capability."""
    return _supports(endpoint, process, native_id, 'prompt_idle')


def supports_cancel(endpoint, process, native_id):
    """Probe a peer-verified bridge for request-specific native cancellation."""
    return _supports(endpoint, process, native_id, 'cancel_request')


def supports_tool_capture(endpoint, process, native_id):
    """Probe the bridge's fail-closed pre-tool capture hooks."""
    return _supports(endpoint, process, native_id, 'tool_capture')


def cancel_request(endpoint, process, native_id, request_id, expected_turn_id):
    """Cancel only the exact exclusive native request on its pinned turn.

    Returns the accepted result once the native runner's cancellation settled.
    That is not evidence that the turn stopped or its tools were cleaned up;
    the host must still observe both. Raises CancelRefused when native code
    definitely did not act, and CancelUncertain when it may have. Never
    automatically resend an uncertain cancellation.
    """
    for name, value in (('native_id', native_id), ('request_id', request_id),
                        ('expected_turn_id', expected_turn_id)):
        if not isinstance(value, str) or not value.strip():
            raise CancelRefused('invalid', f'{name} must be a nonempty string')
    try:
        transport = _open(endpoint, process)
    except QueueUnavailable as exc:
        raise CancelRefused('unavailable', str(exc)) from exc
    try:
        transport.settimeout(CANCEL_TIMEOUT)
        req = {'id': 5, 'method': 'session/cancel_request', 'params': {
            'session_id': native_id, 'request_id': request_id, 'expected_turn_id': expected_turn_id}}
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        line = transport.makefile('r', encoding='utf-8').readline()
        if not line:
            raise CancelUncertain('native cancel outcome is uncertain; OpenCode closed connection without response')
        msg = json.loads(line)
        if (not isinstance(msg, dict) or type(msg.get('id')) is not int or msg['id'] != req['id']
                or ('error' in msg) == ('result' in msg)):
            raise CancelUncertain('native cancel response is invalid; outcome is uncertain')
        if 'error' in msg:
            err = msg['error']
            if not isinstance(err, dict) or type(err.get('code')) is not int or not isinstance(err.get('message'), str):
                raise CancelUncertain('native cancel error is invalid; outcome is uncertain')
            code, message = err['code'], err['message']
            if code in CANCEL_REFUSALS:
                raise CancelRefused(CANCEL_REFUSALS[code], f'native OpenCode did not cancel: {message}')
            if code == -32000 and message.startswith('UNSUPPORTED_CONTROL:'):
                raise CancelRefused('unsupported', f'native OpenCode did not cancel: {message}')
            raise CancelUncertain(f'native OpenCode cancel outcome is uncertain: {message}')
        result = msg['result']
        if (not isinstance(result, dict) or result.get('outcome') != 'accepted'
                or result.get('session_id') != native_id or result.get('request_id') != request_id
                or result.get('turn_id') != expected_turn_id):
            raise CancelUncertain('native cancel acknowledgment is invalid; outcome is uncertain')
        return result
    except QueueError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CancelUncertain('native cancel outcome is uncertain; do not automatically resend') from exc
    finally:
        _discard(transport)


def enqueue(endpoint, process, native_id, text, delivery_id, expected_session_id=None, request_id=None):
    """Submit an atomic idle-only wakeup to an active OpenCode session.

    Returns (delivery_id, started). A BUSY result proves no admission and may
    retry at the next idle boundary. Transport failures after sending remain
    uncertain and must never be automatically retried.
    """
    if not native_id or not isinstance(native_id, str) or not native_id.strip():
        raise QueueUnavailable('native_id must be a nonempty string')
    if not isinstance(delivery_id, str) or not delivery_id.strip():
        raise QueueUnavailable('delivery_id must be a nonempty string')
    if request_id is None:
        request_id = delivery_id
    if not isinstance(request_id, str) or not request_id.strip():
        raise QueueUnavailable('request_id must be a nonempty string')
    exp_id = expected_session_id or native_id
    if not exp_id or not isinstance(exp_id, str) or not exp_id.strip():
        raise QueueUnavailable('expected_session_id must be a nonempty string')

    transport = _open(endpoint, process)
    try:
        req = {
            'id': 1,
            'method': 'session/prompt_idle',
            'params': {
                'session_id': native_id,
                'text': text,
                'delivery_id': delivery_id,
                'request_id': request_id,
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
        if (not isinstance(msg, dict)
                or type(msg.get('id')) is not int
                or msg['id'] != req['id']
                or ('error' in msg) == ('result' in msg)):
            raise QueueError('native queue response is invalid; outcome is uncertain; do not automatically resend')
        if 'error' in msg:
            err = msg['error']
            if (not isinstance(err, dict)
                    or type(err.get('code')) is not int
                    or not isinstance(err.get('message'), str)):
                raise QueueError('native queue error is invalid; outcome is uncertain; do not automatically resend')
            code = err.get('code')
            message = err.get('message', '')
            if code == BUSY_CODE:
                raise QueueUnavailable(f'native OpenCode busy: {message}')
            if code in (-32004, -32601) or (code == -32000 and message.startswith('UNSUPPORTED_CONTROL:')):
                raise QueueUnsupported(f'native OpenCode prompt_idle unavailable: {message}')
            if code in (-32001, -32002):
                raise QueueUnavailable(f'native session unavailable: {message}')
            if code == -32003:
                raise QueueRefused(f'native OpenCode refused prompt_idle: {message}')
            if code == -32005:
                raise QueueAlreadyAdmitted(f'native OpenCode request already admitted: {message}')
            raise QueueError(f'native OpenCode prompt_idle outcome is uncertain: {message}')

        result = msg.get('result')
        if (not isinstance(result, dict)
                or result.get('queued') is not True
                or result.get('queued_id') != delivery_id
                or result.get('request_id') != request_id
                or result.get('session_id') != native_id
                or result.get('started') is not True):
            raise QueueError('native queue acknowledgment is invalid; outcome is uncertain; do not automatically resend')
        return result['queued_id'], result['started']
    except QueueUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError('native queue outcome is uncertain; do not automatically resend') from exc
    finally:
        _discard(transport)


def abort(endpoint, process, native_id, expected_turn_id=None):
    """Attempt turn-fenced session abort.

    Disabled until atomic turn-fencing upstream is available.
    """
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
