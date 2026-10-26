"""Queue a wakeup on an already loaded Codex thread without touching its TUI."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time


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


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A precondition refused before the queue submission was sent."""


class QueueRefused(QueueError):
    """A structured native refusal; its meaning depends on the operation."""
    def __init__(self, method, error):
        super().__init__(f'native Codex refused {method}')
        self.code = error.get('code')
        self.message = error.get('message')


BUSY_CODE = -32600
BUSY_MESSAGE = 'thread already has an active or pending turn'
MAX_PEER_BYTES = _transport.MAX_PEER_BYTES


class _BoundedPeerSocket(_transport.BoundedPeerSocket):
    """Limit reads by the supported preconnected-socket WebSocket path."""
    def __init__(self, transport):
        super().__init__(transport, max_bytes=MAX_PEER_BYTES, error_cls=QueueError)


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
            if type(message.get('id')) is not int or message['id'] != self.sequence:
                continue
            if 'error' in message:
                if 'result' in message or not isinstance(message['error'], dict):
                    raise QueueError('native Codex returned an invalid response')
                if busy_ok and _busy(message['error']):
                    return None  # An existing turn keeps ownership; queued input waits.
                raise QueueRefused(method, message['error'])
            if not isinstance(message.get('result'), dict):
                raise QueueError('native Codex returned an invalid response')
            return message['result']


def _discard(transport, connection):
    _transport.close_quietly(transport, connection)


def _open(endpoint, process, owner='process'):
    """Connect and verify the endpoint owner before anything is sent."""
    try:
        import websocket
    except ImportError as exc:
        raise QueueUnavailable('native Codex queue requires python3-websocket') from exc
    transport = None
    connection = None
    try:
        transport, identity = _transport.open_verified_socket(
            endpoint,
            process,
            owner=owner,
            timeout=4,
            unavailable_cls=QueueUnavailable,
            return_identity=True,
        )
        connection = websocket.create_connection('ws://localhost', socket=_BoundedPeerSocket(transport), timeout=4)
        return connection, websocket, identity
    except QueueUnavailable:
        _discard(transport, connection)
        raise
    except QueueError as exc:
        _discard(transport, connection)
        raise QueueUnavailable('native queue handshake did not complete within byte limit') from exc
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
    """Compatibility boolean: only a confirmed native start returns True."""
    return start_outcome(endpoint, process, native_id, queued_id, owner) == 'started'


def start_outcome(endpoint, process, native_id, queued_id, owner='process'):
    """Retry only the start of one retained, still-pending submission.

    Returns started, busy, refused, unknown or missing. Only the exact native
    not-found response establishes missing at that observation, never whether
    the input was previously admitted. QueueUnavailable is a pre-start failure.
    The submission itself is never re-added.
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
            if started is None:
                return 'busy'
            if isinstance(started.get('turn'), dict) and started['turn'].get('id'):
                return 'started'
            return 'unknown'
        except QueueRefused as exc:
            if type(exc.code) is int and exc.code == -32600 and isinstance(exc.message, str) and exc.message:
                if exc.message == f'queued submission not found: {queued_id}':
                    return 'missing'
                return 'refused'
            return 'unknown'
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException):
            return 'unknown'
    finally:
        connection.close(timeout=0)


MAX_QUEUED_IDS = 64
NATIVE_STATUS_TYPES = ('active', 'idle', 'systemError')


def inspect_thread(endpoint, process, native_id, owner='process', queued_id=None):
    """Read-only, peer/session-verified native thread status and queue probe.

    Connects and verifies peer identity, requires the conversation to be loaded,
    then reads lightweight thread status (includeTurns=False) and current queue
    entries without mutating native state.
    """
    connection, websocket, peer = _open(endpoint, process, owner)
    try:
        rpc = _Rpc(connection, time.monotonic() + 4)
        try:
            _admit(connection, rpc, native_id)
        except QueueUnavailable:
            raise
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as exc:
            raise QueueUnavailable(f'native queue is not ready: {exc}') from exc
        status_type = 'unknown'
        updated_at = None
        queued_ids = []
        try:
            read_result = rpc('thread/read', dict(threadId=native_id, includeTurns=False))
            thread = read_result.get('thread') if isinstance(read_result.get('thread'), dict) else read_result
            if isinstance(thread, dict) and thread.get('id') in (None, native_id):
                raw_status = thread.get('status')
                candidate = raw_status.get('type') if isinstance(raw_status, dict) else raw_status
                if isinstance(candidate, str) and candidate in NATIVE_STATUS_TYPES:
                    status_type = candidate
                if type(thread.get('updatedAt')) is int:
                    updated_at = thread['updatedAt']
            queue_page = rpc('thread/queue/list', dict(threadId=native_id))
            raw_items = queue_page.get('data')
            if not isinstance(raw_items, list):
                return dict(status_type='unknown', updated_at=updated_at, queue_count=0,
                            queued_ids=[], queued_present=None, peer=peer)
            for item in raw_items:
                item_id = item.get('id') if isinstance(item, dict) else (item if isinstance(item, str) else None)
                if not isinstance(item_id, str) or not item_id or len(item_id.encode()) > 256:
                    return dict(status_type='unknown', updated_at=updated_at, queue_count=0,
                                queued_ids=[], queued_present=None, peer=peer)
                if len(queued_ids) < MAX_QUEUED_IDS:
                    queued_ids.append(item_id)
            has_query = isinstance(queued_id, str) and bool(queued_id)
            if queue_page.get('nextCursor') is not None or len(raw_items) > MAX_QUEUED_IDS:
                # Paginated queue membership cannot establish absence within a single bounded page.
                present = True if (has_query and queued_id in queued_ids) else None
                return dict(status_type=status_type, updated_at=updated_at, queue_count=len(queued_ids),
                            queued_ids=queued_ids, queued_present=present, peer=peer)
        except (QueueError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException):
            return dict(status_type='unknown', updated_at=updated_at, queue_count=0,
                        queued_ids=[], queued_present=None, peer=peer)
        return dict(status_type=status_type, updated_at=updated_at, queue_count=len(queued_ids),
                    queued_ids=queued_ids,
                    queued_present=(queued_id in queued_ids) if has_query else None,
                    peer=peer)
    finally:
        connection.close(timeout=0)


