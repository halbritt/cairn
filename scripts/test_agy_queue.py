"""Unit tests for Agy native queue client adapter and boundary inspection."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from integrations.lifecycle import agy_queue


class AgyQueueUnixSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'agy-bridge.sock')
        self.process = dict(
            pid=os.getpid(),
            start=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        )
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.running = True

    def tearDown(self):
        self.running = False
        try:
            self.listener.close()
        except OSError:
            pass

    def _serve_one(self, handler):
        def loop():
            try:
                self.listener.settimeout(2.0)
                conn, _ = self.listener.accept()
                with conn:
                    handler(conn)
            except (OSError, socket.timeout):
                pass
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    def test_process_verification_rejects_altered_identity(self):
        """Process start time and boot ID changes are caught before connecting."""
        tampered_proc = dict(self.process, start=self.process['start'] + 999)
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.enqueue(self.path, tampered_proc, 'ses_1', 'hello', 'req_1')
        self.assertIn('process start time mismatch', str(ctx.exception))

    def test_session_mismatch_refusal(self):
        """Session mismatch is rejected as QueueUnavailable."""
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.enqueue(
                self.path, self.process, 'ses_actual', 'hello', 'req_1',
                expected_session_id='ses_expected'
            )
        self.assertIn('session mismatch', str(ctx.exception))

    def test_inferred_http_endpoint_strictly_rejected(self):
        """Inferred HTTP Connect-RPC endpoints are refused without fabricating connection."""
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.enqueue('http://127.0.0.1:8080', self.process, 'ses_1', 'hello', 'req_1')
        self.assertIn('inferred HTTP Connect-RPC endpoint is unsupported', str(ctx.exception))

    def test_enqueue_draft_preserved_success(self):
        """Successful enqueue delivers directly to native prompt queue without touching PTY."""
        def handle(conn):
            line = conn.makefile('r').readline()
            req = json.loads(line)
            self.assertEqual(req['method'], 'session/prompt_async')
            self.assertEqual(req['params']['session_id'], 'ses_1')
            self.assertEqual(req['params']['text'], 'wake prompt text')
            self.assertEqual(req['params']['client_id'], 'req_123')
            resp = {'jsonrpc': '2.0', 'id': req['id'], 'result': {'queued': True, 'queued_id': 'req_123', 'started': True}}
            conn.sendall(json.dumps(resp).encode() + b'\n')

        t = self._serve_one(handle)
        queued_id, started = agy_queue.enqueue(
            self.path, self.process, 'ses_1', 'wake prompt text', 'req_123', expected_session_id='ses_1'
        )
        t.join(timeout=1.0)
        self.assertEqual(queued_id, 'req_123')
        self.assertTrue(started)

    def test_enqueue_rejects_empty_result_schema_without_started(self):
        """Enqueue rejects response where started is missing from result dict."""
        def handle(conn):
            line = conn.makefile('r').readline()
            req = json.loads(line)
            resp = {'jsonrpc': '2.0', 'id': req['id'], 'result': {}}
            conn.sendall(json.dumps(resp).encode() + b'\n')

        t = self._serve_one(handle)
        with self.assertRaises(agy_queue.QueueError) as ctx:
            agy_queue.enqueue(
                self.path, self.process, 'ses_1', 'wake prompt', 'req_empty', expected_session_id='ses_1'
            )
        t.join(timeout=1.0)
        self.assertIn('missing or invalid boolean "started" field', str(ctx.exception))

    def test_busy_refusal_preserves_busy_turn_ordering(self):
        """Submitting to a busy Agy session raises QueueBusy, preventing wake merge into active turn."""
        def handle(conn):
            line = conn.makefile('r').readline()
            req = json.loads(line)
            resp = {
                'jsonrpc': '2.0',
                'id': req['id'],
                'error': {
                    'code': -32600,
                    'message': 'BUSY: session already executing active turn'
                }
            }
            conn.sendall(json.dumps(resp).encode() + b'\n')

        t = self._serve_one(handle)
        with self.assertRaises(agy_queue.QueueBusy) as ctx:
            agy_queue.enqueue(
                self.path, self.process, 'ses_1', 'wake prompt', 'req_busy', expected_session_id='ses_1'
            )
        t.join(timeout=1.0)
        self.assertIn('BUSY', str(ctx.exception))

    def test_uncertain_write_reconciliation_on_connection_drop(self):
        """If connection is severed after writing, QueueError is raised (uncertain outcome)."""
        def handle(conn):
            _ = conn.makefile('r').readline()
            conn.close()

        t = self._serve_one(handle)
        with self.assertRaises(agy_queue.QueueError) as ctx:
            agy_queue.enqueue(
                self.path, self.process, 'ses_1', 'wake prompt', 'req_drop', expected_session_id='ses_1'
            )
        t.join(timeout=1.0)
        self.assertNotIsInstance(ctx.exception, agy_queue.QueueUnavailable)
        self.assertIn('uncertain', str(ctx.exception).lower())

    def test_abort_refusal_protects_newer_owner_work(self):
        """Session abort is refused with -32004 UNSUPPORTED_CONTROL per agent 24 contract."""
        def handle(conn):
            line = conn.makefile('r').readline()
            req = json.loads(line)
            self.assertEqual(req['method'], 'session/abort')
            resp = {
                'jsonrpc': '2.0',
                'id': req['id'],
                'error': {
                    'code': -32004,
                    'message': 'UNSUPPORTED_CONTROL: native session abort is disabled to protect newer owner work'
                }
            }
            conn.sendall(json.dumps(resp).encode() + b'\n')

        t = self._serve_one(handle)
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.abort(self.path, self.process, 'ses_1', expected_turn_id='turn_old')
        t.join(timeout=1.0)
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))

    def test_abort_strictly_unavailable_with_none_turn(self):
        """Session abort is refused even when called with expected_turn_id=None or arbitrary socket."""
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.abort(self.path, self.process, 'ses_1', expected_turn_id=None)
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))

    def test_status_refuses_pid_alive_fabrication(self):
        """status() refuses to fabricate idle state from PID liveness when endpoint is absent."""
        with self.assertRaises(agy_queue.QueueUnavailable) as ctx:
            agy_queue.status(None, self.process, 'ses_1')
        self.assertIn('UNSUPPORTED_STATUS', str(ctx.exception))
        self.assertIn('status cannot be inferred from PID liveness', str(ctx.exception))

    def test_status_with_valid_bridge(self):
        """status() returns validated bridge result."""
        def handle(conn):
            line = conn.makefile('r').readline()
            req = json.loads(line)
            self.assertEqual(req['method'], 'session/status')
            resp = {'jsonrpc': '2.0', 'id': req['id'], 'result': {'session_id': 'ses_1', 'status': 'idle'}}
            conn.sendall(json.dumps(resp).encode() + b'\n')

        t = self._serve_one(handle)
        res = agy_queue.status(self.path, self.process, 'ses_1')
        t.join(timeout=1.0)
        self.assertEqual(res, {'session_id': 'ses_1', 'status': 'idle'})


class AgyPresenceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app_data = Path(self.temp.name)
        (self.app_data / 'presence').mkdir(parents=True)

    def test_inspect_presence_lock_empty_when_no_file(self):
        """Nonexistent presence lock returns None."""
        pid = agy_queue.inspect_presence_lock('nonexistent-convo', app_data_dir=str(self.app_data))
        self.assertIsNone(pid)

    def test_inspect_presence_lock_identifies_owning_pid(self):
        """Active flock on presence file identifies the holding process PID."""
        import fcntl
        conv_id = 'test-conversation-uuid'
        lock_path = self.app_data / 'presence' / f'{conv_id}.lock'
        lock_path.touch()

        with open(lock_path, 'r+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            # The current process owns the lock
            pid = agy_queue.inspect_presence_lock(conv_id, app_data_dir=str(self.app_data))
            self.assertEqual(pid, os.getpid())
            fcntl.flock(f, fcntl.LOCK_UN)

        # Once unlocked, inspect returns None
        pid = agy_queue.inspect_presence_lock(conv_id, app_data_dir=str(self.app_data))
        self.assertIsNone(pid)


if __name__ == '__main__':
    unittest.main()
