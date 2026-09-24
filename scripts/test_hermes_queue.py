"""Unit tests for Hermes native queue client adapter against real Unix domain sockets."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

from integrations.lifecycle import hermes_queue


class HermesQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'hermes-bridge.sock')
        self.process = dict(
            pid=os.getpid(),
            start=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        )
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(3)
        self.addCleanup(self.listener.close)
        self.requests = []
        self.errors = []
        self.threads = []

    def serve(self, behavior='confirm', busy=False):
        def run():
            try:
                conn, _ = self.listener.accept()
                with conn:
                    conn.settimeout(3)
                    file_obj = conn.makefile('r', encoding='utf-8')
                    line = file_obj.readline()
                    if not line:
                        return
                    req = json.loads(line)
                    self.requests.append(req)
                    req_id = req.get('id')
                    method = req.get('method')
                    params = req.get('params', {})

                    if behavior == 'drop':
                        return

                    if method == 'session/queue_message':
                        if behavior == 'not_found':
                            resp = {'id': req_id, 'error': {'code': -32002, 'message': 'SESSION_NOT_FOUND: session does not exist'}}
                        elif behavior == 'mismatch':
                            resp = {'id': req_id, 'error': {'code': -32001, 'message': 'SESSION_MISMATCH: session changed'}}
                        else:
                            resp = {
                                'id': req_id,
                                'result': {
                                    'queued': True,
                                    'queued_id': params.get('client_id'),
                                    'session_id': params.get('session_id'),
                                    'busy': busy or (behavior == 'busy'),
                                    'request_id': params.get('request_id'),
                                    'delivery_id': params.get('delivery_id'),
                                }
                            }
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/abort':
                        if behavior == 'request_mismatch':
                            resp = {'id': req_id, 'error': {'code': -32001, 'message': 'REQUEST_MISMATCH: active turn belongs to request req_other; abort refused to protect owner turn'}}
                        elif behavior == 'dequeued':
                            resp = {'id': req_id, 'result': {'aborted': True, 'turn_stop': 'dequeued_before_admission', 'session_id': params.get('session_id'), 'request_id': params.get('expected_request_id'), 'tools': []}}
                        else:
                            resp = {
                                'id': req_id,
                                'result': {
                                    'aborted': True,
                                    'turn_stop': 'interrupted',
                                    'session_id': params.get('session_id'),
                                    'request_id': params.get('expected_request_id'),
                                    'tools': [{'item_id': 'proc_123', 'process_id': '4567', 'stop_state': 'terminated'}],
                                }
                            }
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/tools_status':
                        resp = {'id': req_id, 'result': {'session_id': params.get('session_id'), 'tools': [{'session_id': 'proc_123', 'status': 'running'}]}}
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/status':
                        resp = {'id': req_id, 'result': {'session_id': params.get('session_id'), 'status': 'busy' if busy else 'idle'}}
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
            except Exception as exc:
                self.errors.append(exc)

        worker = threading.Thread(target=run)
        self.threads.append(worker)
        worker.start()

    def join_workers(self):
        for worker in self.threads:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])

    def test_unchanged_composer_direct_submission(self):
        """1. Queues directly into pending input without touching prompt_toolkit composer."""
        self.serve(behavior='confirm', busy=False)
        queued_id, started = hermes_queue.enqueue(
            self.path, self.process, 'ses_hermes123', 'wake text', 'delivery_one'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertTrue(started)
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(req['method'], 'session/queue_message')
        self.assertEqual(req['params']['session_id'], 'ses_hermes123')
        self.assertEqual(req['params']['text'], 'wake text')

    def test_busy_ordering_without_preemption(self):
        """2. Busy session queues serially into pending input without interrupt queue."""
        self.serve(behavior='busy', busy=True)
        queued_id, started = hermes_queue.enqueue(
            self.path, self.process, 'ses_hermes123', 'wake text', 'delivery_one'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertFalse(started)

    def test_stale_native_session_refusal(self):
        """3. Refuses when target session is not found or has mismatched expectation."""
        self.serve(behavior='not_found')
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('SESSION_NOT_FOUND', str(ctx.exception))

        self.serve(behavior='mismatch')
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one', expected_session_id='ses_other'
            )
        self.join_workers()
        self.assertIn('SESSION_MISMATCH', str(ctx.exception))

    def test_uncertain_delivery_no_resend(self):
        """4. Failure after request transmission raises QueueError (uncertain outcome; never resend)."""
        self.serve(behavior='drop')
        with self.assertRaises(hermes_queue.QueueError) as ctx:
            hermes_queue.enqueue(
                self.path, self.process, 'ses_hermes123', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertNotIsInstance(ctx.exception, hermes_queue.QueueUnavailable)
        self.assertIn('uncertain', str(ctx.exception))

    def test_enqueue_carries_request_and_delivery_id(self):
        """5. Enqueue carries exact Cairn request_id and delivery_id."""
        self.serve(behavior='confirm', busy=False)
        queued_id, started = hermes_queue.enqueue(
            self.path, self.process, 'ses_hermes123', 'wake text', 'delivery_one',
            request_id='req-1234', delivery_id='del-5678'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertTrue(started)
        req = self.requests[0]
        self.assertEqual(req['params']['request_id'], 'req-1234')
        self.assertEqual(req['params']['delivery_id'], 'del-5678')

    def test_abort_active_request_success(self):
        """6. Abort matching active request returns interrupted status and tool outcomes."""
        self.serve(behavior='confirm')
        result = hermes_queue.abort(
            self.path, self.process, 'ses_hermes123', expected_request_id='req-1234'
        )
        self.join_workers()
        self.assertTrue(result.get('aborted'))
        self.assertEqual(result.get('turn_stop'), 'interrupted')
        tools = result.get('tools', [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]['item_id'], 'proc_123')
        self.assertEqual(tools[0]['stop_state'], 'terminated')

    def test_abort_mismatched_request_refused(self):
        """7. Abort with mismatched request ID raises RequestMismatchError to protect owner turn."""
        self.serve(behavior='request_mismatch')
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.path, self.process, 'ses_hermes123', expected_request_id='req-other'
            )
        self.join_workers()
        self.assertIn('REQUEST_MISMATCH', str(ctx.exception))

    def test_abort_pending_dequeued_before_admission(self):
        """8. Abort when message is still pending removes it before admission."""
        self.serve(behavior='dequeued')
        result = hermes_queue.abort(
            self.path, self.process, 'ses_hermes123', expected_request_id='req-pending'
        )
        self.join_workers()
        self.assertTrue(result.get('aborted'))
        self.assertEqual(result.get('turn_stop'), 'dequeued_before_admission')
        self.assertEqual(result.get('tools'), [])

    def test_tools_status(self):
        """9. tools_status returns process status for session."""
        self.serve(behavior='confirm')
        result = hermes_queue.tools_status(
            self.path, self.process, 'ses_hermes123', tool_ids=['proc_123']
        )
        self.join_workers()
        tools = result.get('tools', [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]['session_id'], 'proc_123')
        self.assertEqual(tools[0]['status'], 'running')

    def test_abort_turn_mismatch_refused(self):
        """10. Abort with mismatched turn ID raises RequestMismatchError to protect active turn."""
        def serve_turn_mismatch():
            conn, _ = self.listener.accept()
            with conn:
                line = conn.makefile('r', encoding='utf-8').readline()
                req = json.loads(line)
                resp = {'id': req['id'], 'error': {'code': -32003, 'message': 'TURN_MISMATCH: active turn is turn-1 (expected turn-2); abort refused to protect active turn'}}
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
        t = threading.Thread(target=serve_turn_mismatch)
        self.threads.append(t)
        t.start()
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.path, self.process, 'ses_hermes123', expected_turn_id='turn-2'
            )
        self.join_workers()
        self.assertIn('TURN_MISMATCH', str(ctx.exception))

    def test_abort_unspecified_refused(self):
        """11. Abort with neither request nor turn specified raises RequestMismatchError while running."""
        def serve_unspecified():
            conn, _ = self.listener.accept()
            with conn:
                line = conn.makefile('r', encoding='utf-8').readline()
                req = json.loads(line)
                resp = {'id': req['id'], 'error': {'code': -32004, 'message': 'UNSPECIFIED_ABORT: expected_request_id or expected_turn_id required while agent is running; abort refused to protect active turn'}}
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
        t = threading.Thread(target=serve_unspecified)
        self.threads.append(t)
        t.start()
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.path, self.process, 'ses_hermes123'
            )
        self.join_workers()
        self.assertIn('UNSPECIFIED_ABORT', str(ctx.exception))

    def test_enqueue_rejects_empty_result(self):
        """12. Enqueue rejects response with missing queued or started/busy fields."""
        def serve_empty():
            conn, _ = self.listener.accept()
            with conn:
                line = conn.makefile('r', encoding='utf-8').readline()
                req = json.loads(line)
                resp = {'id': req['id'], 'result': {}}
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
        t = threading.Thread(target=serve_empty)
        self.threads.append(t)
        t.start()
        with self.assertRaises(hermes_queue.QueueError) as ctx:
            hermes_queue.enqueue(
                self.path, self.process, 'ses_hermes123', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('invalid bridge response schema', str(ctx.exception))

    def test_abort_rejects_malformed_result(self):
        """13. Abort rejects response missing aborted or turn_stop fields."""
        def serve_malformed():
            conn, _ = self.listener.accept()
            with conn:
                line = conn.makefile('r', encoding='utf-8').readline()
                req = json.loads(line)
                resp = {'id': req['id'], 'result': {'aborted': True}} # missing turn_stop
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
        t = threading.Thread(target=serve_malformed)
        self.threads.append(t)
        t.start()
        with self.assertRaises(hermes_queue.QueueError) as ctx:
            hermes_queue.abort(
                self.path, self.process, 'ses_hermes123', expected_request_id='req-1'
            )
        self.join_workers()
        self.assertIn('turn_stop', str(ctx.exception))


    def test_prepare_idle_wake_populates_request_identity(self):
        """14. prepare_idle_wake populates request_id and coordination passes it to hermes_queue."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'integrations' / 'lifecycle'))
        from integrations.lifecycle import coordination
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'state.json'
            state = {
                'agent': {
                    'agent_id': 'agent-123',
                    'execution_id': 'exec-456',
                    'native_session_id': 'ses_hermes123',
                    'metadata': {'state': 'idle', 'delivery_mode': 'existing-session', 'workspace': '/home/user/project'}
                },
                'workspace': '/home/user/project',
                'process': self.process,
            }
            path.write_text(json.dumps(state))

            config = {
                'idle_wakeup': True,
                'native_delivery': True,
                'harness': 'hermes',
            }

            with patch.object(coordination, 'call', return_value={'delivery_id': 'del-uuid-999'}), \
                 patch.object(coordination, 'hermes_queue_endpoint', return_value=self.path):
                prepared = coordination.prepare_idle_wake(config, state, path)

            self.assertIsNotNone(prepared)
            wake = prepared['wake']
            self.assertEqual(wake['delivery_id'], 'del-uuid-999')
            self.assertIn('request_id', wake)
            self.assertTrue(wake['request_id'])

            # Verify submit_idle_wake passes wake['request_id'] to hermes_queue.enqueue
            self.serve(behavior='confirm', busy=False)
            with patch.object(coordination, 'hermes_queue_endpoint', return_value=self.path):
                coordination.submit_idle_wake(config, path, prepared)
            self.join_workers()

            self.assertEqual(len(self.requests), 1)
            req = self.requests[0]
            self.assertEqual(req['params']['request_id'], wake['request_id'])
            self.assertEqual(req['params']['delivery_id'], 'del-uuid-999')


if __name__ == '__main__':
    unittest.main()
