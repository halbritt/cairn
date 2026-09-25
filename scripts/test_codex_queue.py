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
from unittest import mock

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
        self.threads = []

    def serve(self, loaded=('native-one',), add='confirm', start='confirm', malformed_loaded=False,
              request_collision=False):
        """Serve exactly one connection with the requested fixture behavior.

        add: 'confirm' or 'refuse' (no acknowledgment; the add may have committed).
        start: 'confirm', 'busy', 'busy_extra', 'unknown' or 'drop'.
        """
        def exact(stream, size):
            result = b''
            while len(result) < size:
                chunk = stream.read(size-len(result))
                if not chunk:
                    raise EOFError()
                result = result + chunk
            return result

        def frame(body):
            header = bytes([129, len(body)]) if len(body) < 126 else bytes([129, 126])+struct.pack('!H', len(body))
            return header+body

        def refused(kind, request):
            error = {'code': -32600, 'message': 'thread already has an active or pending turn'}
            if kind == 'busy_extra':
                error['data'] = {'hint': 'tolerate extra fields'}
            if kind == 'unknown':
                error = {'code': -32000, 'message': 'queued submission is not pending'}
            return frame(json.dumps({'id': request['id'], 'error': error}).encode())

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
                            result = {} if malformed_loaded else {'data': list(loaded), 'nextCursor': None}
                        elif method == 'thread/queue/add':
                            if add == 'refuse':
                                return  # Submission may have committed; no acknowledgment.
                            if request_collision:
                                connection.sendall(frame(json.dumps({'id': request['id'],
                                    'method': 'server/notice', 'params': {}}).encode()))
                            result = {'queuedSubmission': {'id': 'queued-one', **request['params']}}
                        elif method == 'thread/queue/start':
                            if start in ('busy', 'busy_extra'):
                                connection.sendall(refused(start, request))
                                continue
                            if start == 'unknown':
                                connection.sendall(refused(start, request))
                                continue
                            if start == 'drop':
                                return  # Outcome unknown; the connection just ends.
                            result = {'turn': {'id': 'turn-one', 'status': 'inProgress'}}
                        else:
                            raise AssertionError(method)
                        connection.sendall(frame(json.dumps({'id': request['id'], 'result': result}).encode()))
            except EOFError:
                pass
            except Exception as exc:
                self.errors.append(exc)
        for worker in self.threads:  # One waiter at a time keeps connections ordered.
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])
        worker = threading.Thread(target=run)
        self.threads.append(worker)
        worker.start()

    def join(self):
        for worker in self.threads:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])

    def enqueue(self):
        return self.queue.enqueue(self.path, self.process, 'native-one', 'wake only', 'delivery-one')

    def methods(self):
        return [request['method'] for request in self.requests]

    def test_queues_only_on_the_observed_process_and_loaded_conversation(self):
        self.serve()
        queued_id, started = self.enqueue()
        self.assertEqual((queued_id, started), ('queued-one', True))
        submission = next(r for r in self.requests if r['method'] == 'thread/queue/add')
        self.assertEqual(submission['params'], dict(threadId='native-one', clientUserMessageId='delivery-one',
            input=[dict(type='text', text='wake only')]))
        self.assertNotIn('thread/resume', self.methods())
        self.assertNotIn('turn/start', self.methods())
        start = next(r for r in self.requests if r['method'] == 'thread/queue/start')
        self.assertEqual(start['params'], dict(threadId='native-one', queuedSubmissionId='queued-one'))

    def test_concurrent_owner_turn_keeps_the_wakeup_queued(self):
        self.serve(start='busy')
        self.assertEqual(self.enqueue(), ('queued-one', False))
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/start']), 1)
        self.assertNotIn('turn/interrupt', self.methods())

    def test_busy_refusal_with_extra_error_fields_still_counts_as_busy(self):
        self.serve(start='busy_extra')
        self.assertEqual(self.enqueue(), ('queued-one', False))

    def test_failed_or_lost_start_after_confirmed_add_retains_the_submission(self):
        self.serve(start='unknown')
        self.assertEqual(self.enqueue(), ('queued-one', False))
        self.serve(start='drop')
        self.assertEqual(self.enqueue(), ('queued-one', False))
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/add']), 2)

    def test_retained_submission_start_retries_never_re_add(self):
        self.serve(start='busy')
        self.assertEqual(self.enqueue(), ('queued-one', False))
        self.serve(start='confirm')
        self.assertTrue(self.queue.start(self.path, self.process, 'native-one', 'queued-one'))
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/add']), 1)
        starts = [r for r in self.requests if r['method'] == 'thread/queue/start']
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[1]['params'], dict(threadId='native-one', queuedSubmissionId='queued-one'))
        self.assertNotIn('thread/resume', self.methods())
        self.assertNotIn('turn/start', self.methods())

    def test_retained_submission_start_stays_false_while_busy_or_raced(self):
        self.serve(start='busy')
        self.assertEqual(self.enqueue(), ('queued-one', False))
        self.serve(start='drop')
        self.assertFalse(self.queue.start(self.path, self.process, 'native-one', 'queued-one'))
        self.serve(start='busy_extra')
        self.assertFalse(self.queue.start(self.path, self.process, 'native-one', 'queued-one'))
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/add']), 1)

    def test_never_loads_an_unloaded_conversation(self):
        self.serve(loaded=())
        with self.assertRaisesRegex(self.queue.QueueUnavailable, 'not loaded'):
            self.enqueue()
        self.assertNotIn('thread/queue/add', self.methods())

    def test_socket_replaced_by_another_process_is_refused_before_writing(self):
        self.serve()
        with self.assertRaisesRegex(self.queue.QueueUnavailable, 'process'):
            self.queue.enqueue(self.path, dict(self.process, pid=os.getpid()+1), 'native-one', 'wake only', 'delivery-one')
        self.assertEqual(self.requests, [])

    def test_changed_process_identity_is_refused_before_writing(self):
        self.serve()
        with self.assertRaisesRegex(self.queue.QueueUnavailable, 'identity'):
            self.queue.enqueue(self.path, dict(self.process, start=self.process['start']+1), 'native-one', 'wake only', 'delivery-one')
        self.assertEqual(self.requests, [])

    def test_missing_endpoint_is_retryable_not_uncertain(self):
        absent = str(Path(self.temp.name)/'absent.sock')
        with self.assertRaisesRegex(self.queue.QueueUnavailable, 'unavailable'):
            self.queue.enqueue(absent, self.process, 'native-one', 'wake only', 'delivery-one')

    def test_malformed_admission_response_cannot_strand_an_unsent_wake(self):
        self.serve(malformed_loaded=True)
        with self.assertRaises(self.queue.QueueUnavailable):
            self.enqueue()
        self.assertNotIn('thread/queue/add', [r['method'] for r in self.requests])

    def test_shared_app_server_route_verifies_by_user_and_tui_identity(self):
        # A TUI sharing an app-server via --remote is not the socket peer;
        # its queue writes verify the peer user plus the observed TUI identity.
        self.serve()
        self.assertEqual(self.queue.enqueue(self.path, self.process, 'native-one', 'wake only',
                                            'delivery-one', owner='uid'), ('queued-one', True))
        self.serve(start='busy')
        self.assertFalse(self.queue.start(self.path, self.process, 'native-one', 'queued-one', owner='uid'))
        self.serve()
        with self.assertRaisesRegex(self.queue.QueueUnavailable, 'identity'):
            self.queue.enqueue(self.path, dict(self.process, start=self.process['start']+1), 'native-one',
                               'wake only', 'delivery-one', owner='uid')

    def test_verified_peer_reports_the_socket_owning_process(self):
        # The control seam exposes the app-server identity both modes pin to.
        for owner in ('process', 'uid'):
            with self.subTest(owner=owner):
                self.serve()
                identity = self.queue.verified_peer(self.path, self.process, owner=owner)
                self.assertEqual(identity, dict(pid=os.getpid(),
                    start=self.process['start'], boot=self.process['boot']))
        with self.assertRaises(self.queue.QueueUnavailable):
            self.queue.verified_peer(str(Path(self.temp.name)/'absent.sock'), self.process)

    def test_lost_submission_response_is_an_error_and_is_not_retried(self):
        self.serve(add='refuse')
        with self.assertRaises(self.queue.QueueError):
            self.enqueue()
        self.assertEqual(len([r for r in self.requests if r['method'] == 'thread/queue/add']), 1)

    def test_deadline_before_add_send_is_retryable(self):
        self.serve()
        original = self.queue._Rpc

        class ExpiringRpc(original):
            def __call__(self, method, params, busy_ok=False):
                if method == 'thread/queue/add':
                    self.deadline = self.queue_time.monotonic() - 1
                return super().__call__(method, params, busy_ok)

        ExpiringRpc.queue_time = self.queue.time
        with mock.patch.object(self.queue, '_Rpc', ExpiringRpc):
            with self.assertRaises(self.queue.QueueUnavailable):
                self.enqueue()
        self.assertNotIn('thread/queue/add', self.methods())

    def test_server_request_with_matching_id_is_not_a_response(self):
        self.serve(request_collision=True)
        self.assertEqual(self.enqueue(), ('queued-one', True))


if __name__ == '__main__':
    unittest.main()
