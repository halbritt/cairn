"""Queue a wakeup on an already loaded Codex thread without touching its TUI."""
import json
from pathlib import Path
import socket
import struct
import time


class QueueError(Exception):
    pass


class QueueUnavailable(QueueError):
    """A checked precondition refused before any queue submission."""


def enqueue(endpoint, process, native_id, text, client_id):
    """One submission attempt; callers must retain uncertainty before calling."""
    try:
        import websocket
    except ImportError as exc:
        raise QueueError('native Codex queue requires python3-websocket') from exc

    deadline = time.monotonic() + 4
    sequence = 0
    connection = None
    with socket.socket(socket.AF_UNIX) as transport:
        transport.settimeout(4)
        try:
            transport.connect(endpoint)
            peer, _, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer != process['pid']:
                raise QueueError('native socket belongs to a different process')
            start = int(Path(f'/proc/{peer}/stat').read_text().rsplit(')', 1)[1].split()[19])
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if start != process['start'] or boot != process['boot']:
                raise QueueError('native process identity has changed')
            connection = websocket.create_connection('ws://localhost', socket=transport, timeout=4)

            def remaining():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise QueueError('native queue response timed out')
                connection.settimeout(left)

            def rpc(method, params, allow_busy=False):
                nonlocal sequence
                sequence += 1
                remaining()
                connection.send(json.dumps(dict(id=sequence, method=method, params=params)))
                while True:
                    remaining()
                    message = json.loads(connection.recv())
                    if not isinstance(message, dict):
                        raise QueueError('native Codex returned an invalid response')
                    if message.get('id') != sequence:
                        continue
                    if 'error' in message:
                        if (allow_busy and message['error'] == dict(code=-32600,
                                message='thread already has an active or pending turn')):
                            return None  # Existing turn keeps ownership; queued input waits.
                        raise QueueError(f'native Codex refused {method}')
                    return message['result']

            rpc('initialize', dict(clientInfo=dict(name='cairn-idle-wakeup', version='1'),
                                   capabilities=dict(experimentalApi=True)))
            connection.send(json.dumps(dict(method='initialized')))
            cursor = None
            while True:
                page = rpc('thread/loaded/list', dict(limit=100, **({'cursor': cursor} if cursor else {})))
                if not isinstance(page['data'], list):
                    raise QueueError('native Codex returned an invalid thread list')
                if native_id in page['data']:
                    break
                cursor = page.get('nextCursor')
                if not cursor:
                    raise QueueUnavailable('native conversation is not loaded; refusing to resume it')
            # turn/start can steer an active turn. The native queue serializes a
            # concurrent owner submission and leaves the draft in the TUI alone.
            result = rpc('thread/queue/add', dict(threadId=native_id, clientUserMessageId=client_id,
                                                input=[dict(type='text', text=text)]))
            queued = result['queuedSubmission']
            if (not isinstance(queued, dict) or queued.get('clientUserMessageId') != client_id or
                    not isinstance(queued.get('id'), str) or not queued['id']):
                raise QueueError('native queue did not confirm this submission')
            # An interrupted thread can leave queued input paused. This starts
            # only our queued submission and refuses an active/pending turn.
            started = rpc('thread/queue/start', dict(threadId=native_id, queuedSubmissionId=queued['id']), allow_busy=True)
            if started is not None and (not isinstance(started, dict) or
                    not isinstance(started.get('turn'), dict) or not started['turn'].get('id')):
                raise QueueError('native queue did not confirm its start')
            return queued['id']
        except (OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as exc:
            raise QueueError('native queue outcome is uncertain; do not automatically resend') from exc
        finally:
            if connection is not None:
                connection.close(timeout=0)
