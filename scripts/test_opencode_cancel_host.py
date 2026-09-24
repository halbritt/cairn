"""Host cancellation observations for one pinned OpenCode inbox attempt."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integrations.lifecycle import coordination


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
                          inbox_intent=dict(request_id='attempt-one', session=self.session,
                                            native_turn_id='turn-one'),
                          inbox_attempt=copy.deepcopy(self.attempt), delivered_since_idle=True)
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
            coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(calls, ['session-inbox-control'])
        self.assertIn('inbox_intent', self.state)

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


if __name__ == '__main__':
    unittest.main()
