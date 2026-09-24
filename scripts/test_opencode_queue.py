"""Unit tests for OpenCode native queue client adapter against real Unix domain sockets."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from integrations.lifecycle import coordination, opencode_queue

ROOT = Path(__file__).resolve().parent.parent


def assert_owner_boundary_fallback(test, endpoint, process, state_dir, probe, state_update=None):
    """An unpatched process must still claim pending work at an owner turn."""
    delivery_id = '00000000-0000-4000-8000-000000000001'
    session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
    state = dict(workspace=str(state_dir), process=process, agent=dict(**session,
        native_session_id='ses_test', metadata=dict(workspace=str(state_dir), state='idle',
                                                  delivery_mode='existing-session')))
    state.update(state_update or {})
    path = state_dir / 'session.json'
    coordination.write_state(path, state)
    config = dict(harness='opencode', binding='fixture', native_delivery=True, idle_wakeup=True,
                  cairn='/usr/bin/cairn', socket='/tmp/cairn.sock', token_file='/tmp/cairn.token',
                  state_dir=str(state_dir))
    delivery = dict(delivery_id=delivery_id, lease_id='00000000-0000-4000-8000-000000000002',
                    event=dict(event_id='event-one', kind='request', **{'from': 'agent/source', 'ref': {}}))
    claims = []

    def store_call(_config, operation, request, **_kwargs):
        if operation == 'session-inbox-ready':
            return dict(delivery_id=delivery_id)
        if operation == 'session-inbox-claim':
            claims.append(request.copy())
            return dict(attempt=dict(attempt_id=request['request_id'], session=session, delivery=delivery))
        if operation == 'session-inbox-control':
            return dict(attempt=dict(attempt_id=claims[-1]['request_id'], session=session, delivery=delivery))
        if operation == 'session-inbox-reconcile':
            raise coordination.CoordinationError('DELIVERY_ACTIVE', 'request still active')
        if operation == 'event-renew':
            return delivery
        test.fail(f'unexpected store operation: {operation}')

    with patch.object(coordination, 'call', side_effect=store_call), \
            patch.object(coordination, 'opencode_queue_endpoint', return_value=endpoint), \
            patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
        probe()
        with coordination.session_lock(path):
            test.assertIsNone(coordination.prepare_idle_wake(config, state, path))
            coordination.write_state(path, state)
        test.assertNotIn('idle_wake', state)
        probe()
        result = coordination.inbox_context(config, state, path, dict(
            event='TurnStart', phase='busy', native_turn_id='msg_owner'))
    test.assertIn('Cairn has a request', result)
    test.assertEqual(len(claims), 1)
    test.assertNotIn('turn_exclusive', claims[0])
    return claims[0]


class OpenCodeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'opencode-bridge.sock')
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

    def serve(self, behavior='confirm', busy=False, turn_match=True):
        """Serve one connection with specified mock behavior:
        behavior: 'confirm', 'busy', 'not_found', 'drop', 'mismatch', 'abort_confirm', 'abort_refuse'
        """
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
                        return  # Connection terminates abruptly after receiving request
                    if isinstance(behavior, dict):
                        conn.sendall((json.dumps(behavior) + '\n').encode('utf-8'))
                        return

                    if method == 'session/capabilities':
                        if behavior == 'old_plugin':
                            resp = {'id': req_id, 'error': {'code': -32601, 'message': 'Method session/capabilities not found'}}
                        else:
                            resp = {'id': req_id, 'result': {
                                'session_id': params.get('session_id'), 'prompt_idle': behavior != 'missing_api'}}
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/prompt_idle':
                        if behavior == 'not_found':
                            resp = {'id': req_id, 'error': {'code': -32002, 'message': 'SESSION_NOT_FOUND: session does not exist'}}
                        elif behavior == 'mismatch':
                            resp = {'id': req_id, 'error': {'code': -32001, 'message': 'SESSION_MISMATCH: session changed'}}
                        elif behavior == 'busy' or busy:
                            resp = {'id': req_id, 'error': {'code': -32600, 'message': 'BUSY: target session is currently busy; refuse to merge input into active generation'}}
                        elif behavior == 'conflict':
                            resp = {'id': req_id, 'error': {'code': -32003, 'message': 'CONFLICT: request ID already belongs to another prompt'}}
                        else:
                            resp = {
                                'id': req_id,
                                'result': {
                                    'queued': True,
                                    'queued_id': params.get('delivery_id'),
                                    'request_id': params.get('request_id'),
                                    'session_id': params.get('session_id'),
                                    'started': True
                                }
                            }
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/abort':
                        resp = {'id': req_id, 'error': {'code': -32004, 'message': 'UNSUPPORTED_CONTROL: session abort disabled; atomic turn-fencing not yet supported upstream'}}
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
        """1. Submission calls prompt_idle without touching TUI composer."""
        self.serve(behavior='confirm', busy=False)
        queued_id, started = opencode_queue.enqueue(
            self.path, self.process, 'ses_native123', 'cairn wakeup text', 'delivery_one'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertTrue(started)
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(req['method'], 'session/prompt_idle')
        self.assertEqual(req['params']['session_id'], 'ses_native123')
        self.assertEqual(req['params']['text'], 'cairn wakeup text')
        self.assertEqual(req['params']['request_id'], 'delivery_one')
        # Composer is untouched because prompt_idle goes directly to backend session API

    def test_invalid_correlation_rejected_without_opening_socket(self):
        for client in (None, '', '  ', 12, [], {}):
            with self.subTest(client=client), patch.object(opencode_queue, '_open') as connect:
                with self.assertRaises(opencode_queue.QueueUnavailable):
                    opencode_queue.enqueue(self.path, self.process, 'ses_test', 'wake', client)
                connect.assert_not_called()

    def test_busy_ordering_without_preemption(self):
        """2. Busy session refuses submission (-32600 BUSY) as QueueUnavailable without preemption."""
        self.serve(behavior='busy', busy=True)
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_native123', 'cairn wakeup text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('BUSY', str(ctx.exception))
        self.assertEqual(self.requests[0]['method'], 'session/prompt_idle')
        # No abort was sent; active turn remains unmolested

    def test_old_plugin_endpoint_falls_back_to_owner_boundary(self):
        claim = assert_owner_boundary_fallback(
            self, self.path, self.process, Path(self.temp.name), lambda: self.serve('old_plugin'))
        self.join_workers()
        self.assertEqual(claim['session']['agent_id'], 'fixture-agent')
        self.assertEqual([request['method'] for request in self.requests],
                         ['session/capabilities', 'session/capabilities'])

    def test_unpatched_bridge_codes_are_definite_no_admission(self):
        for code, message in ((-32601, 'Method session/prompt_idle not found'),
                              (-32000, 'UNSUPPORTED_CONTROL: native prompt_idle capability unavailable'),
                              (-32004, 'UNSUPPORTED_CONTROL: prompt_idle route missing')):
            with self.subTest(code=code):
                self.serve({'id': 1, 'error': {'code': code, 'message': message}})
                with self.assertRaises(opencode_queue.QueueUnavailable):
                    opencode_queue.enqueue(self.path, self.process, 'ses_test', 'wake', 'delivery_one')
                self.join_workers()

    def test_unsupported_response_disables_wake_for_this_process(self):
        delivery_id = '00000000-0000-4000-8000-000000000001'
        session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
        state = dict(workspace=self.temp.name, process=self.process, agent=dict(**session,
            native_session_id='ses_test', metadata=dict(workspace=self.temp.name, state='idle',
                                                        delivery_mode='existing-session')))
        path = Path(self.temp.name) / 'unsupported.json'
        coordination.write_state(path, state)
        config = dict(harness='opencode', binding='fixture', native_delivery=True, idle_wakeup=True)
        with patch.object(coordination, 'call', return_value=dict(delivery_id=delivery_id)), \
                patch.object(coordination, 'opencode_idle_endpoint', return_value=self.path), \
                patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
            with coordination.session_lock(path):
                prepared = coordination.prepare_idle_wake(config, state, path)
            self.serve({'id': 1, 'error': {'code': -32004,
                                         'message': 'UNSUPPORTED_CONTROL: prompt_idle route missing'}})
            coordination.submit_idle_wake(config, path, prepared)
            self.join_workers()
        state = json.loads(path.read_text())
        self.assertNotIn('idle_wake', state)
        identity = {key: self.process[key] for key in ('pid', 'start', 'boot')}
        self.assertEqual(state['opencode_unsupported'], identity)
        with tempfile.TemporaryDirectory() as tmp:
            claim = assert_owner_boundary_fallback(self, self.path, self.process, Path(tmp),
                                                   lambda: None, dict(opencode_unsupported=identity))
        self.assertEqual(claim['session'], session)

    def test_busy_retries_the_same_delivery_at_next_idle(self):
        state_path = Path(self.temp.name) / 'session.json'
        delivery = '00000000-0000-4000-8000-000000000001'
        config = dict(harness='opencode', binding='fixture', native_delivery=True, idle_wakeup=True)
        state = dict(workspace=self.temp.name, process=self.process, agent=dict(
            agent_id='fixture-agent', execution_id='fixture-execution', native_session_id='ses_native123',
            metadata=dict(workspace=self.temp.name, state='idle', delivery_mode='existing-session')))
        coordination.write_state(state_path, state)
        with patch.object(coordination, 'call', return_value=dict(delivery_id=delivery)), \
                patch.object(coordination, 'opencode_idle_endpoint', return_value=self.path), \
                patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
            with coordination.session_lock(state_path):
                prepared = coordination.prepare_idle_wake(config, state, state_path)
            self.serve('busy')
            coordination.submit_idle_wake(config, state_path, prepared)
            self.join_workers()
            self.assertNotIn('idle_wake', json.loads(state_path.read_text()))
            with coordination.session_lock(state_path):
                state = json.loads(state_path.read_text())
                retry = coordination.prepare_idle_wake(config, state, state_path)
            self.assertEqual(retry['wake']['delivery_id'], delivery)
            self.assertEqual(retry['wake']['request_id'], delivery)
            self.serve('confirm')
            coordination.submit_idle_wake(config, state_path, retry)
            self.join_workers()
            self.assertEqual(json.loads(state_path.read_text())['idle_wake']['status'], 'submitted')
            self.assertEqual(len(self.requests), 2)

    def test_exclusive_binding_requires_the_observed_message_and_admission(self):
        delivery = '00000000-0000-4000-8000-000000000001'
        session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
        wake = dict(transport='opencode-queue', session=session, delivery_id=delivery,
                    request_id=delivery, native_id='ses_native123', cancel_capable=True)
        state = dict(agent=session, idle_wake=wake)
        event = dict(hook_event_name='TurnStart', session_id='ses_native123',
                     turn_id='msg_native_one', delivery_id=delivery, request_id=delivery,
                     prompt=coordination.wake_message(wake))
        self.assertEqual(coordination.opencode_wake_binding(state, event), dict(
            delivery_id=delivery, native_turn_id='msg_native_one', turn_exclusive=True))
        for field, value in [('request_id', 'other'), ('delivery_id', 'other'),
                             ('turn_id', ''), ('hook_event_name', 'TurnEnd')]:
            with self.subTest(field=field):
                self.assertEqual(coordination.opencode_wake_binding(state, dict(event, **{field: value})), {})
        self.assertEqual(coordination.opencode_wake_binding(state, dict(event, prompt='older wake text')),
                         dict(delivery_id=delivery, native_turn_id='msg_native_one', turn_exclusive=True))

    def test_admitted_turn_is_claimed_exclusively_for_exact_delivery(self):
        delivery_id = '00000000-0000-4000-8000-000000000001'
        lease_id = '00000000-0000-4000-8000-000000000002'
        session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
        wake = dict(transport='opencode-queue', session=session, delivery_id=delivery_id,
                    request_id=delivery_id, native_id='ses_native123', cancel_capable=True)
        state = dict(agent=dict(**session, native_session_id='ses_native123'),
                     idle_wake=wake, process=self.process)
        path = Path(self.temp.name) / 'session.json'
        coordination.write_state(path, state)
        config = dict(harness='opencode', binding='fixture', native_delivery=True,
                      idle_wakeup=True, cairn='/usr/bin/cairn', socket='/tmp/cairn.sock',
                      token_file='/tmp/cairn.token', state_dir=self.temp.name)
        event = dict(hook_event_name='TurnStart', turn_id='msg_native_one',
                     delivery_id=delivery_id, request_id=delivery_id,
                     prompt=coordination.wake_message(wake))
        claim = []
        delivery = dict(delivery_id=delivery_id, lease_id=lease_id,
                        event=dict(event_id='event-one', kind='request', **{'from': 'agent/source', 'ref': {}}))

        def store_call(_config, operation, request, **_kwargs):
            if operation == 'session-inbox-claim':
                claim.append(request.copy())
                return dict(attempt=dict(attempt_id=request['request_id'], session=session, delivery=delivery))
            if operation == 'session-inbox-control':
                return dict(attempt=dict(attempt_id=claim[-1]['request_id'], session=session, delivery=delivery))
            if operation == 'session-inbox-reconcile':
                raise coordination.CoordinationError('DELIVERY_ACTIVE', 'request still active')
            if operation == 'event-renew':
                return delivery
            self.fail(f'unexpected store operation: {operation}')

        with patch.object(coordination, 'call', side_effect=store_call):
            binding = coordination.opencode_wake_binding(state, event)
            result = coordination.inbox_context(config, state, path, dict(
                event='TurnStart', phase='busy', native_turn_id='msg_native_one'), binding)
        self.assertIn('Cairn has a request', result)
        self.assertEqual(len(claim), 1)
        self.assertEqual(claim[0]['delivery_id'], delivery_id)
        self.assertEqual(claim[0]['native_turn_id'], 'msg_native_one')
        self.assertIs(claim[0]['turn_exclusive'], True)

    def test_terminal_replay_recovers_exact_delivery_on_owner_turn(self):
        delivery_id = '00000000-0000-4000-8000-000000000001'
        session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
        state = dict(workspace=self.temp.name, process=self.process, agent=dict(**session,
            native_session_id='ses_test', metadata=dict(workspace=self.temp.name, state='idle',
                                                        delivery_mode='existing-session')))
        path = Path(self.temp.name) / 'session.json'
        coordination.write_state(path, state)
        config = dict(harness='opencode', binding='fixture', native_delivery=True,
                      idle_wakeup=True, cairn='/usr/bin/cairn', socket='/tmp/cairn.sock',
                      token_file='/tmp/cairn.token', state_dir=self.temp.name)
        delivery = dict(delivery_id=delivery_id, lease_id='00000000-0000-4000-8000-000000000002',
                        event=dict(event_id='event-one', kind='request', **{'from': 'agent/source', 'ref': {}}))
        claims = []

        def store_call(_config, operation, request, **_kwargs):
            if operation == 'session-inbox-ready':
                return dict(delivery_id=delivery_id)
            if operation == 'session-inbox-claim':
                claims.append(request.copy())
                return dict(attempt=dict(attempt_id=request['request_id'], session=session, delivery=delivery))
            if operation == 'session-inbox-control':
                return dict(attempt=dict(attempt_id=claims[-1]['request_id'], session=session, delivery=delivery))
            if operation == 'session-inbox-reconcile':
                raise coordination.CoordinationError('DELIVERY_ACTIVE', 'request still active')
            if operation == 'event-renew':
                return delivery
            self.fail(f'unexpected store operation: {operation}')

        with patch.object(coordination, 'call', side_effect=store_call), \
                patch.object(coordination, 'opencode_idle_endpoint', return_value=self.path), \
                patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
            with coordination.session_lock(path):
                prepared = coordination.prepare_idle_wake(config, state, path)
            self.serve({'id': 1, 'error': {'code': -32005, 'message': 'ALREADY_ADMITTED: native request is completed'}})
            coordination.submit_idle_wake(config, path, prepared)
            self.join_workers()
            state = json.loads(path.read_text())
            self.assertNotIn('idle_wake', state)
            self.assertEqual(state['opencode_replay']['delivery_id'], delivery_id)
            with coordination.session_lock(path):
                self.assertIsNone(coordination.prepare_idle_wake(config, state, path))
            result = coordination.inbox_context(config, state, path, dict(
                event='TurnStart', phase='busy', native_turn_id='msg_owner'))
        self.assertIn('Cairn has a request', result)
        self.assertEqual(len(self.requests), 1, 'terminal replay must not be submitted again')
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['delivery_id'], delivery_id)
        self.assertEqual(claims[0]['native_turn_id'], 'msg_owner')
        self.assertNotIn('turn_exclusive', claims[0])

    def test_stale_native_session_refusal(self):
        """3. Refuses when native session is not found or has mismatched expectation."""
        self.serve(behavior='not_found')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('SESSION_NOT_FOUND', str(ctx.exception))

        self.serve(behavior='mismatch')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one', expected_session_id='ses_other'
            )
        self.join_workers()
        self.assertIn('SESSION_MISMATCH', str(ctx.exception))

    def test_uncertain_delivery_no_resend(self):
        """4. Failure after request transmission raises QueueError (uncertain outcome; never resend)."""
        self.serve(behavior='drop')
        with self.assertRaises(opencode_queue.QueueError) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_native123', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
        self.assertIn('uncertain', str(ctx.exception))

    def test_busy_word_inside_unknown_error_does_not_authorize_retry(self):
        self.serve({'id': 1, 'error': {
            'code': -32000,
            'message': 'PROMPT_OUTCOME_UNCERTAIN: connection failed after BUSY warning',
        }})
        with self.assertRaises(opencode_queue.QueueError) as ctx:
            opencode_queue.enqueue(self.path, self.process, 'ses_native123', 'wake', 'delivery_one')
        self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
        self.join_workers()

    def test_coordinator_retains_single_submission_after_success_or_uncertainty(self):
        state_path = Path(self.temp.name) / 'session.json'
        config = dict(harness='opencode', binding='fixture', native_delivery=True, idle_wakeup=True)
        ready = dict(delivery_id='00000000-0000-4000-8000-000000000001', request_id='fixture-request')
        initial = dict(workspace=self.temp.name, process=self.process, agent=dict(
            agent_id='fixture-agent', execution_id='fixture-execution', native_session_id='ses_native123',
            metadata=dict(workspace=self.temp.name, state='idle', delivery_mode='existing-session')))

        def prepare():
            with coordination.session_lock(state_path):
                return coordination.prepare_idle_wake(config, json.loads(state_path.read_text()), state_path)

        def ready_call(_config, operation, request):
            self.assertEqual(operation, 'session-inbox-ready')
            self.assertEqual(request, dict(agent_id='fixture-agent', execution_id='fixture-execution'))
            return ready

        with patch.object(coordination, 'call', side_effect=ready_call), \
                patch.object(coordination, 'opencode_idle_endpoint', return_value=self.path), \
                patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
            for outcome in ('confirm', 'drop', {'id': 99, 'result': {}},
                            {'id': 1, 'error': {'code': -32003, 'message': 'CONFLICT: request belongs to another prompt'}}):
                with self.subTest(outcome=outcome):
                    coordination.write_state(state_path, initial)
                    prepared = prepare()
                    prior = len(self.requests)
                    self.serve(behavior=outcome)
                    refused = isinstance(outcome, dict) and 'CONFLICT' in outcome.get('error', {}).get('message', '')
                    if outcome in ('confirm',) or refused:
                        coordination.submit_idle_wake(config, state_path, prepared)
                    else:
                        with self.assertRaises(coordination.CoordinationError) as failure:
                            coordination.submit_idle_wake(config, state_path, prepared)
                        self.assertEqual(failure.exception.code, 'WAKE_UNCERTAIN')
                    self.join_workers()
                    retained = state_path.read_bytes()
                    wake = json.loads(retained)['idle_wake']
                    self.assertEqual(wake['delivery_id'], ready['delivery_id'])
                    self.assertEqual(wake['status'], 'refused' if refused else 'submitted' if outcome == 'confirm' else 'uncertain')
                    if outcome == 'confirm':
                        self.assertEqual(wake['queued_submission_id'], ready['delivery_id'])
                    for _ in range(3):
                        self.assertIsNone(prepare(), 'retained submission must not be replayed')
                    self.assertEqual(state_path.read_bytes(), retained)
                    self.assertEqual(len(self.requests), prior + 1)
                    self.assertEqual(self.requests[-1]['params']['delivery_id'], ready['delivery_id'])
                    self.assertEqual(self.requests[-1]['params']['request_id'], ready['delivery_id'])

    def test_invalid_acknowledgment_is_uncertain(self):
        valid = {
            'queued': True,
            'queued_id': 'delivery_one',
            'request_id': 'delivery_one',
            'session_id': 'ses_native123',
            'started': True,
        }
        replies = [
            {},
            {'id': 1, 'result': {}},
            {'id': 2, 'result': valid},
            {'id': True, 'result': valid},
            {'id': 1, 'result': valid, 'error': {'code': -32002}},
            *({'id': 1, 'result': {**valid, field: value}}
              for field, value in (
                  ('queued', False),
                  ('queued_id', 'another_delivery'),
                  ('request_id', 'another_request'),
                  ('session_id', 'another_session'),
                  ('started', 'true'),
              )),
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                before = len(self.requests)
                self.serve(behavior=reply)
                with self.assertRaises(opencode_queue.QueueError) as ctx:
                    opencode_queue.enqueue(
                        self.path, self.process, 'ses_native123', 'wake text', 'delivery_one'
                    )
                self.join_workers()
                self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
                self.assertIn('uncertain', str(ctx.exception))
                self.assertEqual(len(self.requests), before + 1)

    def test_unsafe_cancellation_refused_to_protect_newer_work(self):
        """5. Unsafe cancellation refused to protect newer owner turns/work."""
        self.serve(behavior='abort_refuse', turn_match=False)
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.abort(
                self.path, self.process, 'ses_native123', expected_turn_id='turn_old'
            )
        self.join_workers()
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))


class OpenCodeBridgeFixtureTests(unittest.TestCase):
    """Integration tests running the actual coordination.ts plugin via Node bridge fixture."""

    def start_fixture(self, mode='normal', extra_env=None):
        import signal
        import subprocess
        env = os.environ.copy()
        env['OPENCODE_FIXTURE_MODE'] = mode
        env.update(extra_env or {})
        proc = subprocess.Popen(
            ['node', str(Path(__file__).parent / 'run_opencode_bridge_fixture.mjs')],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        line = proc.stdout.readline()
        if not line.startswith('READY:'):
            proc.kill()
            raise RuntimeError(f'Fixture failed to start: {line} stderr: {proc.stderr.read()}')
        pid = int(line.split(':')[1].strip())
        process = dict(
            pid=pid,
            start=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        )
        sock_path = f'/tmp/cairn-opencode-{pid}.sock'
        def cleanup():
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
            try:
                proc.stdout.close()
                proc.stderr.close()
            except Exception:
                pass
            try:
                if os.path.exists(sock_path):
                    os.unlink(sock_path)
            except OSError:
                pass
        self.addCleanup(cleanup)
        return proc, sock_path, process

    def test_fixture_normal_idle_delivery(self):
        """Fixture: Idle session delivers prompt_idle directly to SDK without touching TUI composer."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        queued_id, started = opencode_queue.enqueue(
            sock_path, process, 'ses_test', 'wake text', 'req_normal', expected_session_id='ses_test'
        )
        self.assertEqual(queued_id, 'req_normal')
        self.assertTrue(started)

    def test_fixture_passes_admitted_request_and_native_turn_to_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'hooks.jsonl'
            _, endpoint, process = self.start_fixture(extra_env={
                'OPENCODE_FIXTURE_HOOK_CAPTURE': str(capture),
            })
            request = '00000000-0000-4000-8000-000000000001'
            delivery = '00000000-0000-4000-8000-000000000002'
            queued_id, started = opencode_queue.enqueue(
                endpoint, process, 'ses_test', 'wake text', delivery, request_id=request)
            self.assertEqual((queued_id, started), (delivery, True))
            deadline = time.monotonic() + 3
            while not capture.exists() or len(capture.read_text().splitlines()) < 2:
                if time.monotonic() >= deadline:
                    self.fail('native TurnStart and TurnEnd hooks were not captured')
                time.sleep(.02)
            events = [json.loads(line) for line in capture.read_text().splitlines()]
            self.assertEqual([event['hook_event_name'] for event in events], ['TurnStart', 'TurnEnd'])
            self.assertEqual(events[0]['request_id'], request)
            self.assertEqual(events[0]['delivery_id'], delivery)
            self.assertEqual(events[0]['turn_id'], 'msg_native_one')
            self.assertEqual(events[0]['prompt'], 'wake text')
            self.assertEqual(events[1]['turn_id'], 'msg_native_one')
            self.assertNotIn('request_id', events[1])

    def test_fixture_correlation_preserved_with_native_owned_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'requests.jsonl'
            first = '00000000-0000-4000-8000-000000000001'
            second = '00000000-0000-4000-8000-000000000002'
            for session, client in [('ses_test', first), ('ses_test', second), ('ses_other', first)]:
                _, endpoint, process = self.start_fixture(extra_env={'OPENCODE_FIXTURE_CAPTURE': str(capture)})
                queued_id, started = opencode_queue.enqueue(endpoint, process, session, 'wake text', client)
                self.assertEqual(queued_id, client)
                self.assertTrue(started)
            requests = [json.loads(line) for line in capture.read_text().splitlines()]
            self.assertEqual(len(requests), 3, 'one SDK submission per queue request')
            self.assertTrue(all('messageID' not in request['body'] for request in requests))
            self.assertEqual([r['body']['requestID'] for r in requests], [first, second, first])
            self.assertEqual([r['session_id'] for r in requests], ['ses_test', 'ses_test', 'ses_other'])

    def test_fixture_invalid_correlation_rejected_before_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'requests.jsonl'
            _, endpoint, _ = self.start_fixture(extra_env={'OPENCODE_FIXTURE_CAPTURE': str(capture)})
            for client in (None, '', '  ', 12, [], {}):
                with self.subTest(client=client), socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(2)
                    conn.connect(endpoint)
                    conn.sendall((json.dumps(dict(id=1, method='session/prompt_idle', params=dict(
                        session_id='ses_test', expected_session_id='ses_test', text='wake',
                        delivery_id=client, request_id=client))) + '\n').encode())
                    with conn.makefile('r') as response:
                        reply = json.loads(response.readline())
                    self.assertEqual(reply.get('error', {}).get('code'), -32602)
            self.assertFalse(capture.exists(), 'invalid IDs must not reach prompt_idle')

    def test_fixture_busy_refusal_without_preemption(self):
        """Fixture: Busy session refuses submission (-32600 BUSY) as QueueUnavailable without merging into active generation."""
        proc, sock_path, process = self.start_fixture(mode='busy')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_busy', expected_session_id='ses_test'
            )
        self.assertIn('BUSY', str(ctx.exception))

    def test_fixture_conflict_is_a_retained_refusal(self):
        _, endpoint, process = self.start_fixture(mode='conflict')
        with self.assertRaises(opencode_queue.QueueRefused) as ctx:
            opencode_queue.enqueue(endpoint, process, 'ses_test', 'wake text', 'req_conflict')
        self.assertIn('CONFLICT', str(ctx.exception))

    def test_fixture_new_plugin_without_native_api_falls_back_to_owner_boundary(self):
        _, endpoint, process = self.start_fixture(mode='missing_api')
        self.assertFalse(opencode_queue.supports_idle(endpoint, process, 'ses_test'))
        with self.assertRaises(opencode_queue.QueueUnavailable):
            opencode_queue.enqueue(endpoint, process, 'ses_test', 'wake', 'delivery_one')
        with tempfile.TemporaryDirectory() as tmp:
            assert_owner_boundary_fallback(self, endpoint, process, Path(tmp), lambda: None)

    def test_fixture_terminal_replay_is_already_admitted(self):
        for mode in ('completed', 'cancelled', 'failed'):
            with self.subTest(mode=mode):
                _, endpoint, process = self.start_fixture(mode=mode)
                with self.assertRaises(opencode_queue.QueueAlreadyAdmitted) as ctx:
                    opencode_queue.enqueue(endpoint, process, 'ses_test', 'wake', 'delivery_one')
                self.assertIn(mode, str(ctx.exception))

    def test_fixture_session_not_found(self):
        """Fixture: Session not found returns -32002 SESSION_NOT_FOUND (QueueUnavailable)."""
        proc, sock_path, process = self.start_fixture(mode='not_found')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_not_found', expected_session_id='ses_test'
            )
        self.assertIn('SESSION_NOT_FOUND', str(ctx.exception))

    def test_fixture_session_mismatch(self):
        """Fixture: Session mismatch returns -32001 SESSION_MISMATCH (QueueUnavailable)."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_mismatch', expected_session_id='ses_different'
            )
        self.assertIn('SESSION_MISMATCH', str(ctx.exception))

    def test_fixture_sdk_error_propagation(self):
        """Fixture: an SDK transport error stays uncertain without automatic replay."""
        proc, sock_path, process = self.start_fixture(mode='sdk_error')
        with self.assertRaises(opencode_queue.QueueError) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_err', expected_session_id='ses_test'
            )
        self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
        self.assertIn('HTTP 500', str(ctx.exception))

    def test_fixture_abort_disabled(self):
        """Fixture: session/abort stays disabled to protect newer owner work."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.abort(sock_path, process, 'ses_test', expected_turn_id='turn_1')
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))

    def test_fixture_payload_limit_enforced(self):
        """Fixture: Payloads exceeding 64KB are rejected/disconnected."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        oversized = 'X' * (66 * 1024)
        with self.assertRaises(opencode_queue.QueueError):
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', oversized, 'req_large', expected_session_id='ses_test'
            )

    def test_fixture_null_and_malformed_envelope_rejection(self):
        """Fixture: null and non-object envelopes are rejected with -32600 without crashing."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        # Send raw 'null\n'
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock_path)
        s.sendall(b"null\n")
        resp = json.loads(s.makefile('r').readline())
        s.close()
        self.assertEqual(resp.get('error', {}).get('code'), -32600)
        self.assertIn("envelope must be a JSON object", resp['error']['message'])

        # Send raw '[]\n'
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock_path)
        s.sendall(b"[]\n")
        resp = json.loads(s.makefile('r').readline())
        s.close()
        self.assertEqual(resp.get('error', {}).get('code'), -32600)

        # Process must still be alive and responsive
        self.assertIsNone(proc.poll(), "Fixture process must not crash on malformed envelope")
        res = opencode_queue.status(sock_path, process, 'ses_test')
        self.assertEqual(res.get('status'), 'idle')

    def test_fixture_idempotent_double_disposal_preserves_peer_instance(self):
        """Fixture: Disposing an instance twice is idempotent; socket stays active while peer lives."""
        script = """
import plugin from "./integrations/opencode/coordination.ts";
import fs from "node:fs";
import net from "node:net";

const configPath = `/tmp/cairn-test-disp-config-${process.pid}.json`;
fs.writeFileSync(configPath, JSON.stringify({ python: process.execPath, script: "/bin/true", config: "/dev/null" }));
process.env.CAIRN_COORDINATION_CONFIG = configPath;

const clientA = {
  session: {
    get: async () => ({ data: { id: "ses_a" } }),
    status: async () => ({ data: { "ses_a": { type: "idle" } } }),
    promptIdle: async () => ({ data: { status: "accepted" } })
  }
};
const clientB = {
  session: {
    get: async () => ({ data: { id: "ses_b" } }),
    status: async () => ({ data: { "ses_b": { type: "idle" } } }),
    promptIdle: async () => ({ data: { status: "accepted" } })
  }
};

const a = await plugin({ directory: "/tmp", client: clientA, project: {}, worktree: "/tmp", serverUrl: new URL("http://localhost"), $: null });
const b = await plugin({ directory: "/tmp", client: clientB, project: {}, worktree: "/tmp", serverUrl: new URL("http://localhost"), $: null });

const sock = `/tmp/cairn-opencode-${process.pid}.sock`;
const before = fs.existsSync(sock);

await a.dispose();
const afterFirstDispose = fs.existsSync(sock);

await a.dispose(); // Double dispose on A must be a no-op
const afterRepeatedDispose = fs.existsSync(sock);

// Verify B is still reachable and functioning
const s = net.connect(sock);
let resp = "";
s.on("data", d => resp += d);
await new Promise(resolve => {
  s.on("end", resolve);
  s.end(JSON.stringify({
    id: 1,
    method: "session/prompt_idle",
    params: { session_id: "ses_b", text: "hi", delivery_id: "c1", request_id: "c1", expected_session_id: "ses_b" }
  }) + "\\n");
});

await b.dispose();
const afterBDispose = fs.existsSync(sock);

process.stdout.write(JSON.stringify({
  before,
  afterFirstDispose,
  afterRepeatedDispose,
  bOperated: resp.includes('"queued":true'),
  afterBDispose,
}) + "\\n");
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', script],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Disposal test crashed: {proc.stderr}")
        data = json.loads(proc.stdout.strip())
        self.assertTrue(data['before'])
        self.assertTrue(data['afterFirstDispose'])
        self.assertTrue(data['afterRepeatedDispose'])
        self.assertTrue(data['bOperated'])
        self.assertFalse(data['afterBDispose'])


class OpenCodeNativeSchemaTests(unittest.TestCase):
    """Check actual installed server validation, without a session or provider turn."""

    start_fixture = OpenCodeBridgeFixtureTests.start_fixture

    def test_native_validator_accepts_payload_without_message_id(self):
        binary = os.environ.get('OPENCODE_TEST_BINARY')
        if not binary:
            self.skipTest('OPENCODE_TEST_BINARY must name a patched native OpenCode build')
        temp = tempfile.TemporaryDirectory(prefix='cairn-opencode-native-schema-')
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        models = home / 'models.json'
        models.write_text('{}')
        # No operational configuration, credentials, plugins, database or provider.
        env = dict(HOME=str(home), PATH=os.environ.get('PATH', ''),
                   XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'),
                   XDG_STATE_HOME=str(home / 'state'), XDG_CACHE_HOME=str(home / 'cache'),
                   OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                   OPENCODE_DISABLE_MODELS_FETCH='true', OPENCODE_MODELS_PATH=str(models))
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        server = subprocess.Popen([binary, 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                  cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_server():
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        self.addCleanup(stop_server)
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(url + '/global/health', timeout=.5) as response:
                    self.assertTrue(json.load(response)['healthy'])
                break
            except (urllib.error.URLError, TimeoutError):
                if server.poll() is not None or time.monotonic() >= deadline:
                    self.fail('isolated native OpenCode server did not become ready')
                time.sleep(.05)

        delivery_id = '00000000-0000-4000-8000-000000000001'
        body = dict(requestID='', parts=[dict(type='text', text='schema fixture')])
        req = urllib.request.Request(url + '/session/ses_fixture_missing/prompt_idle',
                                     data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(invalid.exception.code, 400)
        with invalid.exception as response:
            self.assertIn('requestID', response.read().decode())

        capture = home / 'requests.jsonl'
        _, endpoint, process = self.start_fixture('native_schema', {
            'OPENCODE_FIXTURE_URL': url, 'OPENCODE_FIXTURE_CAPTURE': str(capture),
        })
        with self.assertRaises(opencode_queue.QueueUnavailable) as outcome:
            opencode_queue.enqueue(endpoint, process, 'ses_fixture_missing', 'schema fixture', delivery_id)
        # The fixture's fake preflight succeeds, then the isolated native
        # server returns 404 for the missing session after payload validation.
        self.assertIn('UNSUPPORTED_CONTROL', str(outcome.exception))
        submitted = [json.loads(line) for line in capture.read_text().splitlines()]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]['body']['requestID'], delivery_id)
        self.assertEqual(submitted[0]['body']['parts'], body['parts'])


if __name__ == '__main__':
    unittest.main()
