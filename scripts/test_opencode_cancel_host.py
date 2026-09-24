"""Host cancellation observations for one pinned OpenCode inbox attempt."""
import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from integrations.lifecycle import coordination, opencode_queue


class OpenCodeCancelHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'session.json'
        self.session = dict(agent_id='agent-one', execution_id='execution-one')
        self.attempt = dict(attempt_id='attempt-one', session=self.session,
                            native_turn_id='turn-one', turn_exclusive=True,
                            cancel=dict(requested_at='now'), turn_stop_state='',
                            delivery=dict(delivery_id='delivery-one', lease_id='lease-one'))
        self.state = dict(agent=dict(**self.session, native_session_id='ses-one'),
                          process=coordination.process_reference(os.getpid()),
                          inbox_intent=dict(request_id='attempt-one', session=self.session,
                                            native_turn_id='turn-one'),
                          inbox_attempt=copy.deepcopy(self.attempt), delivered_since_idle=True,
                          opencode_request_endpoint='/unused/bridge.sock')
        self.config = dict(harness='opencode', native_delivery=True, binding='fixture',
                           cairn='/unused/cairn', socket='/unused/api.sock',
                           token_file='/unused/token', state_dir=self.temp.name)
        coordination.write_state(self.path, self.state)

    def test_cancelled_attempt_is_never_renewed_or_normally_reconciled(self):
        calls = []

        def store_call(_config, operation, request, **_kwargs):
            calls.append(operation)
            self.assertEqual(operation, 'session-inbox-control')
            self.assertEqual(request, self.session)
            return dict(attempt=copy.deepcopy(self.attempt))

        with patch.object(coordination, 'call', side_effect=store_call):
            request = coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(calls, ['session-inbox-control'])
        self.assertIn('inbox_intent', self.state)
        self.assertEqual(request['request_id'], 'delivery-one')
        self.assertEqual(request['expected_turn_id'], 'turn-one')
        self.assertIsNone(coordination.prepare_opencode_cancel(
            self.config, self.state, self.path, self.attempt))

    def test_native_stop_runs_without_the_state_lock_and_is_one_shot(self):
        with coordination.session_lock(self.path):
            request = coordination.prepare_opencode_cancel(self.config, self.state, self.path, self.attempt)
        sent = []

        def native_cancel(*args):
            with coordination.session_lock(self.path):
                sent.append(args)
            return dict(outcome='accepted')

        with patch.dict('sys.modules', {'opencode_queue': opencode_queue}), \
                patch.object(opencode_queue, 'cancel_request', side_effect=native_cancel):
            coordination.submit_opencode_cancel(self.config, self.path, request)
        self.assertEqual(sent[0][2:], ('ses-one', 'delivery-one', 'turn-one'))
        state = json.loads(self.path.read_text())
        self.assertEqual(state['cancel_submission']['status'], 'accepted')
        self.assertIsNone(coordination.prepare_opencode_cancel(self.config, state, self.path, self.attempt))

    def test_exact_turn_end_records_stop_and_retains_hold(self):
        calls = []

        def store_call(_config, operation, request, **_kwargs):
            calls.append((operation, copy.deepcopy(request)))
            if operation == 'session-inbox-control':
                return dict(attempt=copy.deepcopy(self.attempt))
            if operation == 'session-tool-stop':
                self.assertEqual(request['session'], self.session)
                self.assertEqual(request['attempt_id'], 'attempt-one')
                self.assertEqual(request['turn_stop'], 'ended')
                self.assertEqual(request['tools'], [])
                return dict(self.attempt, turn_stop_state='ended')
            self.fail(f'cancelled attempt must not call {operation}')

        with patch.object(coordination, 'call', side_effect=store_call):
            result = coordination.inbox_context(self.config, self.state, self.path,
                dict(event='TurnEnd', phase='idle', native_turn_id='turn-one'))
        self.assertEqual(result, '')
        self.assertEqual([operation for operation, _ in calls],
                         ['session-inbox-control', 'session-tool-stop'])
        self.assertIn('inbox_intent', self.state)
        self.assertNotIn('cancel_turn_end', self.state)

    def test_uncertain_stop_report_keeps_retry_identity(self):
        seen = []

        def store_call(_config, operation, request, **_kwargs):
            if operation == 'session-inbox-control':
                return dict(attempt=copy.deepcopy(self.attempt))
            if operation == 'session-tool-stop':
                seen.append(copy.deepcopy(request))
                if len(seen) == 1:
                    raise coordination.CoordinationError('API_UNAVAILABLE', 'response lost')
                return dict(self.attempt, turn_stop_state='ended')
            self.fail(f'unexpected operation {operation}')

        observation = dict(event='TurnEnd', phase='idle', native_turn_id='turn-one')
        with patch.object(coordination, 'call', side_effect=store_call):
            with self.assertRaises(coordination.CoordinationError):
                coordination.inbox_context(self.config, self.state, self.path, observation)
            self.state = json.loads(self.path.read_text())
            coordination.inbox_context(self.config, self.state, self.path, observation)
        self.assertEqual(seen[0], seen[1])
        self.assertIn('inbox_intent', self.state)

    def test_tool_start_capture_precedes_execution_and_retries_same_request(self):
        self.attempt.pop('cancel')
        self.config['opencode_cancel_enabled'] = True
        captures = []

        def store_call(_config, operation, request, **_kwargs):
            if operation == 'session-inbox-control':
                return dict(attempt=copy.deepcopy(self.attempt))
            if operation == 'session-tool-capture':
                captures.append(copy.deepcopy(request))
                if len(captures) == 1:
                    raise coordination.CoordinationError('API_UNAVAILABLE', 'capture response lost')
                return copy.deepcopy(self.attempt)
            self.fail(f'uncaptured tool must not call {operation}')

        observation = dict(event='ToolStart', native_turn_id='turn-one')
        event = dict(call_id='call-one', tool='bash', request_id='delivery-one')
        with patch.object(coordination, 'opencode_capture_available', return_value=True), \
                patch.object(coordination, 'call', side_effect=store_call):
            with self.assertRaises(coordination.CoordinationError):
                coordination.opencode_tool_event(self.config, self.state, self.path, observation, event)
            self.state = json.loads(self.path.read_text())
            coordination.opencode_tool_event(self.config, self.state, self.path, observation, event)
            coordination.opencode_tool_event(self.config, self.state, self.path,
                dict(event='ToolEnd', native_turn_id='turn-one'), event)
        self.assertEqual(captures[0], captures[1])
        self.assertEqual(captures[0]['items'][0]['item_id'], 'call-one')
        self.assertEqual(captures[0]['items'][0]['native_turn_id'], 'turn-one')
        self.assertTrue(self.state['tool_calls']['call-one']['ended'])

    def test_live_escapee_keeps_hold_until_fresh_clear_scan(self):
        self.attempt['turn_stop_state'] = 'ended'
        self.attempt['tools'] = [dict(item_id='call-one', stop_state='captured')]
        self.state['tool_calls'] = dict(call_one=dict(attempt_id='attempt-one', since=1))
        self.state['opencode_turn_since'] = 1
        self.config['opencode_cancel_enabled'] = True
        scans = iter([dict(clear=False, coverage='complete', processes=[dict(pid=55)], unknown=[]),
                      dict(clear=True, coverage='complete', processes=[], unknown=[])])
        operations = []

        def store_call(_config, operation, request, **_kwargs):
            operations.append(operation)
            if operation == 'session-tool-stop':
                if request['terminal_scan'] == 'unknown_remaining':
                    return dict(self.attempt, terminal_scan='unknown_remaining')
                self.assertEqual(request['tools'], [dict(item_id='call-one', stop_state='terminated')])
                return dict(self.attempt, terminal_scan='clear',
                            tools=[dict(item_id='call-one', stop_state='terminated')])
            if operation == 'session-inbox-reconcile':
                self.assertEqual(request['reason'], 'cancel_confirmed')
                return dict(self.attempt, finished_at='now')
            self.fail(f'unexpected operation {operation}')

        scan_module = types.SimpleNamespace(markers=lambda *_args, **_kwargs: next(scans))
        with patch.dict('sys.modules', {'process_scan': scan_module}), \
                patch.object(coordination, 'call', side_effect=store_call):
            coordination.scan_opencode_cancel(self.config, self.state, self.path, self.attempt)
            self.assertEqual(operations, ['session-tool-stop'])
            self.assertIn('inbox_intent', self.state)
            coordination.scan_opencode_cancel(self.config, self.state, self.path,
                                             self.state['inbox_attempt'])
        self.assertEqual(operations, ['session-tool-stop', 'session-tool-stop', 'session-inbox-reconcile'])
        self.assertNotIn('inbox_intent', self.state)

    def test_clear_scan_cannot_replace_missing_turn_stop(self):
        scan_module = types.SimpleNamespace(markers=lambda *_args, **_kwargs: self.fail('scan is premature'))
        with patch.dict('sys.modules', {'process_scan': scan_module}), \
                patch.object(coordination, 'call', side_effect=lambda *_args, **_kwargs: self.fail('premature API write')):
            coordination.scan_opencode_cancel(self.config, self.state, self.path, self.attempt)
        self.assertIn('inbox_intent', self.state)

    def test_committed_cancel_reconcile_recovers_after_local_crash(self):
        close = dict(request_id='close-one', session=self.session,
                     attempt_id='attempt-one', reason='cancel_confirmed')
        self.state['inbox_close'] = close
        coordination.write_state(self.path, self.state)
        calls = []

        def store_call(_config, operation, request, **_kwargs):
            calls.append((operation, copy.deepcopy(request)))
            if operation == 'session-inbox-control':
                return dict(attempt=None)
            if operation == 'session-inbox-reconcile':
                return dict(self.attempt, finished_at='now')
            self.fail(f'unexpected operation {operation}')

        with patch.object(coordination, 'call', side_effect=store_call):
            coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual([operation for operation, _ in calls],
                         ['session-inbox-control', 'session-inbox-reconcile'])
        self.assertEqual(calls[1][1], close)
        self.assertNotIn('inbox_intent', self.state)

    def test_exclusive_admission_requires_cancel_and_capture_capabilities(self):
        self.config.update(idle_wakeup='/unused/herdr', opencode_cancel_enabled=True)
        state = dict(agent=dict(**self.session, native_session_id='ses-one',
                                metadata=dict(workspace=self.temp.name, state='idle',
                                              delivery_mode='existing-session')),
                     process=self.state['process'], workspace=self.temp.name)
        for capture, expected in ((False, False), (True, True)):
            with self.subTest(capture=capture), \
                    patch.dict('sys.modules', {'opencode_queue': opencode_queue}), \
                    patch.object(coordination, 'opencode_idle_endpoint', return_value='/unused/bridge.sock'), \
                    patch.object(coordination, 'opencode_capture_available', return_value=True), \
                    patch.object(opencode_queue, 'supports_cancel', return_value=True), \
                    patch.object(opencode_queue, 'supports_tool_capture', return_value=capture), \
                    patch.object(coordination, 'call', return_value=dict(delivery_id='delivery-one')):
                prepared = coordination.prepare_idle_wake(self.config, state, self.path)
            self.assertIs(prepared['wake']['cancel_capable'], expected)
            state.pop('idle_wake')


if __name__ == '__main__':
    unittest.main()
