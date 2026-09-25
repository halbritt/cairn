"""Queue a wakeup on an already loaded Codex thread without touching its TUI."""
import json
import os
from pathlib import Path
import socket
import struct
import time


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A precondition refused before the queue submission was sent."""


BUSY_CODE = -32600
BUSY_MESSAGE = 'thread already has an active or pending turn'


def _busy(error):
    """Only the known busy refusal matches; extra fields are tolerated."""
    return (isinstance(error, dict) and error.get('code') == BUSY_CODE and
            error.get('message') == BUSY_MESSAGE)


class _Rpc:
    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.sequence = 0

    def _remaining(self, sent=False):
        left = self.deadline - time.monotonic()
        if left <= 0:
            if sent:
                raise QueueError('native queue response timed out')
            raise QueueUnavailable('native queue deadline expired before request send')
        self.connection.settimeout(left)

    def __call__(self, method, params, busy_ok=False):
        self.sequence += 1
        self._remaining()
        self.connection.send(json.dumps(dict(id=self.sequence, method=method, params=params)))
        while True:
            self._remaining(sent=True)
            message = json.loads(self.connection.recv())
            if not isinstance(message, dict):
                raise QueueError('native Codex returned an invalid response')
            if 'method' in message:
                continue  # Server requests are not responses, even when ids collide.
            if message.get('id') != self.sequence:
                continue
            if 'error' in message:
                if busy_ok and _busy(message['error']):
                    return None  # An existing turn keeps ownership; queued input waits.
                raise QueueError(f'native Codex refused {method}')
            return message['result']


def _discard(transport, connection):
    try:
        if connection is not None:
            connection.close(timeout=0)
        else:
            transport.close()
    except OSError:
        pass


def _open(endpoint, process, owner='process'):
    """Connect and verify the endpoint owner before anything is sent.

    Returns (connection, websocket module, peer identity). owner='process'
    pins the socket to the observed app-server process. owner='uid' serves a
    TUI sharing an app-server via --remote: the socket peer must belong to
    this user, and the observed TUI process must still carry its recorded
    identity. The returned peer identity is the process actually owning the
    socket — the app-server — which control tooling must pin its operations
    to; it is distinct from the observed TUI in the uid mode.
    """
    try:
        import websocket
    except ImportError as exc:
        raise QueueUnavailable('native Codex queue requires python3-websocket') from exc
    transport = socket.socket(socket.AF_UNIX)
    connection = None
    try:
        transport.settimeout(4)
        transport.connect(endpoint)
        peer, peer_uid, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        checked = peer
        identity = dict(pid=peer, start=0, boot='')
        if owner == 'process':
            if peer != process['pid']:
                raise QueueUnavailable('native socket belongs to a different process')
        else:
            if peer_uid != os.geteuid():
                raise QueueUnavailable('native socket belongs to another user')
            checked = process['pid']
        start = int(Path(f'/proc/{checked}/stat').read_text().rsplit(')', 1)[1].split()[19])
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if start != process['start'] or boot != process['boot']:
            raise QueueUnavailable('native process identity has changed')
        peer_boot = boot if owner == 'process' else Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        peer_start = start if owner == 'process' else int(
            Path(f'/proc/{peer}/stat').read_text().rsplit(')', 1)[1].split()[19])
        identity = dict(pid=peer, start=peer_start, boot=peer_boot)
        connection = websocket.create_connection('ws://localhost', socket=transport, timeout=4)
        return connection, websocket, identity
    except QueueUnavailable:
        _discard(transport, connection)
        raise
    except (OSError, websocket.WebSocketException) as exc:
        _discard(transport, connection)
        raise QueueUnavailable('native queue endpoint is unavailable') from exc


def _admit(connection, rpc, native_id):
    """Shared pre-send admission: initialize, then the loaded-only check."""
    rpc('initialize', dict(clientInfo=dict(name='cairn-idle-wakeup', version='1'),
                           capabilities=dict(experimentalApi=True)))
    connection.send(json.dumps(dict(method='initialized')))
    cursor = None
    while True:
        page = rpc('thread/loaded/list', dict(limit=100, **({'cursor': cursor} if cursor else {})))
        if not isinstance(page['data'], list):
            raise QueueError('native Codex returned an invalid thread list')
        if native_id in page['data']:
            return
        cursor = page.get('nextCursor')
        if not cursor:
            raise QueueUnavailable('native conversation is not loaded; refusing to resume it')


def verified_peer(endpoint, process, owner='process'):
    """The verified app-server identity for one endpoint, or an exception.

    Shared seam for native control tooling: the same SO_PEERCRED and
    identity rules as enqueue, without sending anything. The returned
    {pid, start, boot} names the process actually owning the socket, which
    turn interrupt/stop operations must pin themselves to; in the uid mode
    it is the launcher's app-server, not the observed TUI.
    """
    connection, _, identity = _open(endpoint, process, owner)
    connection.close(timeout=0)
    return identity


def enqueue(endpoint, process, native_id, text, client_id, owner='process'):
    """One queue/add attempt plus a first start try for that exact submission.

    Returns (queued_submission_id, started). QueueUnavailable proves nothing
    was sent, so a later cycle may retry the whole add. Any other failure
    during or after the add is uncertain and must never be re-sent. Once the
    add is confirmed, a failed start never raises: keep the returned id and
    retry only its start.
    """
    connection, websocket, _ = _open(endpoint, process, owner)
    try:
        rpc = _Rpc(connection, time.monotonic() + 4)
        try:
            _admit(connection, rpc, native_id)
        except QueueUnavailable:
            raise
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as exc:
            raise QueueUnavailable(f'native queue is not ready: {exc}') from exc
        # turn/start can steer an active turn. The native queue serializes a
        # concurrent owner submission and leaves the draft in the TUI alone.
        result = rpc('thread/queue/add', dict(threadId=native_id, clientUserMessageId=client_id,
                                            input=[dict(type='text', text=text)]))
        queued = result['queuedSubmission']
        if (not isinstance(queued, dict) or queued.get('clientUserMessageId') != client_id or
                not isinstance(queued.get('id'), str) or not queued['id']):
            raise QueueError('native queue did not confirm this submission')
        # An interrupted thread can leave queued input paused. This starts only
        # this submission and refuses to steer an existing active/pending turn.
        try:
            started = rpc('thread/queue/start', dict(threadId=native_id, queuedSubmissionId=queued['id']),
                          busy_ok=True)
            if started is not None and (not isinstance(started, dict) or
                    not isinstance(started.get('turn'), dict) or not started['turn'].get('id')):
                raise QueueError('native queue did not confirm its start')
            return queued['id'], started is not None
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException):
            return queued['id'], False  # The retained id retries only its start.
    except (OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as exc:
        raise QueueError('native queue outcome is uncertain; do not automatically resend') from exc
    finally:
        connection.close(timeout=0)


def start(endpoint, process, native_id, queued_id, owner='process'):
    """Retry only the start of one retained, still-pending submission.

    Returns True when its native turn started and False for every
    unconfirmed outcome (busy, raced consumption, refused). QueueUnavailable
    again proves nothing was sent. The submission itself is never re-added.
    """
    connection, websocket, _ = _open(endpoint, process, owner)
    try:
        rpc = _Rpc(connection, time.monotonic() + 4)
        try:
            _admit(connection, rpc, native_id)
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as exc:
            raise QueueUnavailable(f'native queue is not ready: {exc}') from exc
        try:
            started = rpc('thread/queue/start', dict(threadId=native_id, queuedSubmissionId=queued_id),
                          busy_ok=True)
            return (started is not None and isinstance(started, dict) and
                    isinstance(started.get('turn'), dict) and bool(started['turn'].get('id')))
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException):
            return False
    finally:
        connection.close(timeout=0)
