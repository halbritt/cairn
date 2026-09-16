"""Native queue transport: real Unix sockets, bounded external protocol fixtures."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeQueue(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('codex_queue', ROOT/'integrations/lifecycle/codex_queue.py')
        self.queue = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.queue)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name)/'native.sock')
        self.process = dict(pid=os.getpid(),
            start=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(2)
        self.addCleanup(self.listener.close)
        self.requests = []
        self.errors = []

    def serve(self, loaded=('native-one',), refuse=False, busy=False):
        def exact(stream, size):
            result = b''
            while len(result) < size:
                chunk = stream.read(size-len(result))
                if not chunk:
                    raise EOFError()
                result += chunk
            return result

        def run():
            try:
                connection, _ = self.listener.accept()
                with connection, connection.makefile('rb') as stream:
                    connection.settimeout(2)
                    headers = {}
                    while (line := stream.readline()) != b'\r\n':
                        if not line:
                            return  # Peer identity refused before the handshake.
                        if b':' in line:
                            key, value = line.split(b':', 1)
                            headers[key.lower()] = value.strip()
                    accept = base64.b64encode(hashlib.sha1(headers[b'sec-websocket-key'] +
                        b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                    connection.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                        b'Connection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                    while True:
                        opcode, length = exact(stream, 2)
                        if opcode & 15 == 8:
                            return
                        masked, length = length & 128, length & 127
                        if length == 126:
                            length = struct.unpack('!H', exact(stream, 2))[0]
                        elif length == 127:
                            length = struct.unpack('!Q', exact(stream, 8))[0]
                        mask = exact(stream, 4) if masked else b''
                        raw = exact(stream, length)
                        if masked:
                            raw = bytes(c ^ mask[i % 4] for i, c in enumerate(raw))
                        request = json.loads(raw)
                        self.requests.append(request)
                        method = request['method']
                        if method == 'initialized':
                            continue
                        if method == 'initialize':
                            result = {'userAgent': 'fixture'}
                        elif method == 'thread/loaded/list':
                            result = {'data': list(loaded), 'nextCursor': None}
                        elif method == 'thread/queue/add':
                            if refuse:
                                return  # Submission may have committed; no acknowledgment.
                            result = {'queuedSubmission': {'id': 'queued-one', **request['params']}}
                        elif method == 'thread/queue/start':
                            result = {'turn': {'id': 'turn-one', 'status': 'inProgress'}}
                        else:
                            raise AssertionError(method)
                        response = {'id': request['id'], 'result': result}
                        if method == 'thread/queue/start' and busy:
                            response = {'id': request['id'], 'error': {'code': -32600,
                                'message': 'thread already has an active or pending turn'}}
                        body = json.dumps(response).encode()
                        header = bytes([129, len(body)]) if len(body) < 126 else bytes([129, 126])+struct.pack('!H', len(body))
                        connection.sendall(header+body)
            except EOFError:
                pass
            except Exception as exc:
                self.errors.append(exc)
        self.worker = threading.Thread(target=run)
        self.worker.start()
        self.addCleanup(self.join)

    def join(self):
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.errors, [])

    def test_queues_only_on_the_observed_process_and_loaded_conversation(self):
        self.serve()
        result = self.queue.enqueue(self.path, self.process, 'native-one', 'wake only', 'delivery-one')
        self.assertEqual(result, 'queued-one')
        submission = next(r for r in self.requests if r['method'] == 'thread/queue/add')
        self.assertEqual(submission['params'], dict(threadId='native-one', clientUserMessageId='delivery-one',
            input=[dict(type='text', text='wake only')]))
        self.assertNotIn('thread/resume', [r['method'] for r in self.requests])
        self.assertNotIn('turn/start', [r['method'] for r in self.requests])
        start = next(r for r in self.requests if r['method'] == 'thread/queue/start')
        self.assertEqual(start['params'], dict(threadId='native-one', queuedSubmissionId='queued-one'))

    def test_concurrent_owner_turn_keeps_the_wakeup_queued(self):
        self.serve(busy=True)
        self.assertEqual(self.queue.enqueue(self.path, self.process, 'native-one', 'wake only', 'delivery-one'), 'queued-one')
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/start']), 1)
        self.assertNotIn('turn/interrupt', [r['method'] for r in self.requests])

    def test_never_loads_an_unloaded_conversation(self):
        self.serve(loaded=())
        with self.assertRaisesRegex(self.queue.QueueError, 'not loaded'):
            self.queue.enqueue(self.path, self.process, 'native-one', 'wake only', 'delivery-one')
        self.assertNotIn('thread/queue/add', [r['method'] for r in self.requests])

    def test_socket_replaced_by_another_process_is_refused_before_writing(self):
        self.serve()
        with self.assertRaisesRegex(self.queue.QueueError, 'process'):
            self.queue.enqueue(self.path, dict(self.process, pid=os.getpid()+1), 'native-one', 'wake only', 'delivery-one')
        self.assertEqual(self.requests, [])

    def test_lost_submission_response_is_an_error_and_is_not_retried(self):
        self.serve(refuse=True)
        with self.assertRaises(self.queue.QueueError):
            self.queue.enqueue(self.path, self.process, 'native-one', 'wake only', 'delivery-one')
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/add']), 1)


if __name__ == '__main__':
    unittest.main()
