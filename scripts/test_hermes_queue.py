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

    def serve(self, behavior='confirm', busy=False, response=None):
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
                        if response is not None:
                            resp = response
                        elif behavior == 'not_found':
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
                        if response is not None:
                            resp = response
                        elif behavior == 'request_mismatch':
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
                                    'turn_id': params.get('expected_turn_id'),
                                    'tools': [{'item_id': 'proc_123', 'process_id': '4567', 'stop_state': 'terminated'}],
                                }
                            }
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/tools_status':
                        resp = response if response is not None else {'id': req_id, 'result': {'session_id': params.get('session_id'), 'tools': [{'session_id': 'proc_123', 'status': 'running'}]}}
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/status':
                        resp = response if response is not None else {'id': req_id, 'result': {'session_id': params.get('session_id'), 'status': 'busy' if busy else 'idle'}}
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

    def test_abort_response_must_match_request_identity(self):
        result = dict(aborted=True, turn_stop='interrupted', session_id='ses_hermes123',
                      request_id='req-1234', turn_id='turn-1234', tools=[])
        cases = (
            {'id': 3, 'result': result},
            {'id': 2, 'result': dict(result, session_id='other')},
            {'id': 2, 'result': dict(result, request_id='other')},
            {'id': 2, 'result': dict(result, turn_id='other')},
            {'id': 2, 'error': []},
        )
        for response in cases:
            with self.subTest(response=response):
                self.serve(response=response)
                with self.assertRaises(hermes_queue.QueueError) as caught:
                    hermes_queue.abort(self.path, self.process, 'ses_hermes123',
                                       expected_request_id='req-1234', expected_turn_id='turn-1234')
                self.assertNotIsInstance(caught.exception, hermes_queue.QueueUnavailable)
                self.assertNotIsInstance(caught.exception, hermes_queue.RequestMismatchError)
        self.join_workers()

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

    def test_status_response_must_match_request_and_session(self):
        self.serve()
        self.assertEqual(hermes_queue.status(self.path, self.process, 'ses_hermes123')['status'], 'idle')
        cases = (
            {'id': 4, 'result': {'session_id': 'ses_hermes123', 'status': 'idle'}},
            {'id': 3, 'result': {'session_id': 'other', 'status': 'idle'}},
            {'id': 3, 'result': []},
            {'id': 3, 'result': {'session_id': 'ses_hermes123', 'status': 1}},
        )
        for response in cases:
            with self.subTest(response=response):
                self.serve(response=response)
                with self.assertRaises(hermes_queue.QueueError):
                    hermes_queue.status(self.path, self.process, 'ses_hermes123')
        self.join_workers()

    def test_tools_status_response_must_match_request_and_session(self):
        cases = (
            {'id': 3, 'result': {'session_id': 'ses_hermes123', 'tools': []}},
            {'id': 4, 'result': {'session_id': 'other', 'tools': []}},
            {'id': 4, 'result': None},
            {'id': 4, 'result': {'session_id': 'ses_hermes123', 'tools': {}}},
        )
        for response in cases:
            with self.subTest(response=response):
                self.serve(response=response)
                with self.assertRaises(hermes_queue.QueueError):
                    hermes_queue.tools_status(self.path, self.process, 'ses_hermes123')
        self.join_workers()

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

    def test_enqueue_rejects_uncorrelated_or_malformed_acknowledgments(self):
        result = dict(queued=True, queued_id='delivery_one', session_id='ses_hermes123',
                      started=True, request_id='request_one', delivery_id='delivery_one')
        cases = (
            ('wrong RPC ID', {'id': 2, 'result': result}),
            ('response array', [{'id': 1, 'result': result}]),
            ('malformed error', {'id': 1, 'error': []}),
            ('missing queue ID', {'id': 1, 'result': {key: value for key, value in result.items() if key != 'queued_id'}}),
            ('missing session ID', {'id': 1, 'result': {key: value for key, value in result.items() if key != 'session_id'}}),
            ('missing request ID', {'id': 1, 'result': {key: value for key, value in result.items() if key != 'request_id'}}),
            ('wrong delivery ID', {'id': 1, 'result': dict(result, delivery_id='other')}),
        )
        for name, response in cases:
            with self.subTest(name=name):
                self.serve(response=response)
                with self.assertRaises(hermes_queue.QueueError) as caught:
                    hermes_queue.enqueue(self.path, self.process, 'ses_hermes123', 'wake text',
                                         'delivery_one', request_id='request_one', delivery_id='delivery_one')
                self.assertNotIsInstance(caught.exception, hermes_queue.QueueUnavailable)
        self.join_workers()

    def test_abort_rejects_malformed_result(self):
        """13. Abort rejects response missing aborted or turn_stop fields."""
        def serve_malformed():
            conn, _ = self.listener.accept()
            with conn:
                line = conn.makefile('r', encoding='utf-8').readline()
                req = json.loads(line)
                resp = {'id': req['id'], 'result': {'aborted': True, 'session_id': 'ses_hermes123',
                                                    'request_id': 'req-1'}}  # missing turn_stop
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

    def test_hermes_normalization_preserves_native_turn_id(self):
        """B5: Coordinator normalization preserves Hermes native turn identity."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'integrations' / 'lifecycle'))
        from integrations.lifecycle import coordination

        config = {'harness': 'hermes'}
        event = {
            'session_id': 'ses_hermes_norm_1',
            'cwd': str(Path.cwd()),
            'model': 'hermes-model',
            'turn_id': 'turn-hermes-uuid-42',
            'hook_event_name': 'TurnStart',
        }
        obs = coordination.normalize(config, event)
        self.assertEqual(obs['native_turn_id'], 'turn-hermes-uuid-42')
        self.assertEqual(obs['event'], 'TurnStart')
        self.assertEqual(obs['phase'], 'busy')

        # Invalid turn IDs raise CoordinationError INVALID_HOST
        for bad_turn in [123, 'turn\0bad', 'turn\x01bad', 'x' * 300]:
            bad_event = dict(event, turn_id=bad_turn)
            with self.assertRaises(coordination.CoordinationError) as ctx:
                coordination.normalize(config, bad_event)
            self.assertEqual(ctx.exception.code, 'INVALID_HOST')

    def test_hermes_wake_binding_and_turn_fencing(self):
        """B5: Hermes wake binding ties exact delivery to native turn and fences mismatched turns."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'integrations' / 'lifecycle'))
        from integrations.lifecycle import coordination
        import tempfile
        from unittest.mock import patch

        agent = {
            'agent_id': 'agent-hermes-b5',
            'execution_id': 'exec-b5',
            'native_session_id': 'ses_hermes_b5',
            'metadata': {'state': 'idle', 'delivery_mode': 'existing-session', 'workspace': str(Path.cwd())}
        }
        wake = {
            'transport': 'hermes-queue',
            'delivery_id': 'del-hermes-101',
            'session': coordination.session_ref(agent),
        }
        state = {
            'agent': agent,
            'workspace': str(Path.cwd()),
            'idle_wake': wake,
            'process': self.process,
        }

        wake_prompt = coordination.wake_message(wake)

        # 1. Matching TurnStart event successfully binds delivery_id and native_turn_id
        valid_event = {
            'hook_event_name': 'TurnStart',
            'turn_id': 'turn-hermes-101',
            'prompt': wake_prompt,
        }
        binding = coordination.wake_binding({'harness': 'hermes'}, state, valid_event)
        self.assertEqual(binding, {
            'delivery_id': 'del-hermes-101',
            'native_turn_id': 'turn-hermes-101',
        })

        # 2. Mismatched prompt returns empty binding (owner prompt does not steal delivery)
        mismatched_prompt = dict(valid_event, prompt='Owner typed prompt')
        self.assertEqual(coordination.wake_binding({'harness': 'hermes'}, state, mismatched_prompt), {})

        # 3. Non-TurnStart event returns empty binding
        wrong_event = dict(valid_event, hook_event_name='UserPromptSubmit')
        self.assertEqual(coordination.wake_binding({'harness': 'hermes'}, state, wrong_event), {})

        # 4. In inbox_context, when hermes queue is active, unbound prompt does NOT claim work
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'state.json'
            path.write_text(json.dumps(state))
            config = {
                'native_delivery': True,
                'idle_wakeup': True,
                'harness': 'hermes',
            }
            obs_unbound = {
                'event': 'TurnStart',
                'phase': 'busy',
                'native_turn_id': 'turn-owner-999',
            }
            with patch.object(coordination, 'hermes_queue_endpoint', return_value=self.path):
                ctx_unbound = coordination.inbox_context(config, state, path, obs_unbound, wake_binding=None)
            self.assertEqual(ctx_unbound, '')  # Owner prompt cannot steal queued work

            # 5. When native turn is bound to inbox_intent, mismatched turn raises NATIVE_TURN_MISMATCH
            state['inbox_intent'] = {
                'delivery_id': 'del-hermes-101',
                'native_turn_id': 'turn-hermes-101',
            }
            obs_mismatch = {
                'event': 'TurnStart',
                'phase': 'busy',
                'native_turn_id': 'turn-intruder-888',
            }
            with self.assertRaises(coordination.CoordinationError) as err:
                coordination.inbox_context(config, state, path, obs_mismatch, wake_binding=None)
            self.assertEqual(err.exception.code, 'NATIVE_TURN_MISMATCH')


if __name__ == '__main__':
    unittest.main()
