"""Independent CAIRN-40 review regressions for multi-session and elapsed-time boundaries."""
import contextlib
import copy
import io
import types
import unittest
from unittest.mock import patch

from integrations.lifecycle import coordination
from scripts import test_watch_cancel_poll as fixtures


class SessionBoundaries(unittest.TestCase):
    setUp = fixtures.PendingCancelPassTests.setUp
    def test_unrelated_busy_session_does_not_keep_fast_polling_alive(self):
        self.state.pop('inbox_intent')
        coordination.write_state(self.path, self.state)
        with coordination.session_lock(self.path), patch.object(coordination, 'call') as call:
            self.assertFalse(coordination.watch_pending_cancels(self.config))
        call.assert_not_called()

    def test_failure_stops_binding_even_when_another_session_is_pending(self):
        other = copy.deepcopy(self.state)
        other['agent']['agent_id'] = 'agent-two'
        coordination.write_state(self.path.with_name('second.json'), other)
        requests = []

        def call(_config, operation, request, **_kwargs):
            requests.append(request['agent_id'])
            if request['agent_id'] == 'agent-one':
                raise coordination.CoordinationError('API_UNAVAILABLE', 'fixture failure')
            return dict(attempt=copy.deepcopy(self.attempt))

        with patch.object(coordination, 'call', side_effect=call), \
                patch.object(coordination, 'submit_opencode_cancel'), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(coordination.watch_pending_cancels(self.config))
        self.assertEqual(sorted(requests), ['agent-one', 'agent-two'])


    def test_survivor_and_unknown_scans_hold_until_a_later_clear_scan(self):
        self.config['opencode_cancel_enabled'] = True
        stored = dict(self.attempt, turn_stop_state='ended',
                      tools=[dict(item_id='tool-one', stop_state='captured')])
        scans = iter([dict(clear=False, coverage='complete', processes=[dict(pid=55)], unknown=[]),
                      dict(clear=False, coverage='unknown', processes=[], unknown=[dict(pid=56)]),
                      dict(clear=True, coverage='complete', processes=[], unknown=[])])
        operations = []

        def call(_config, operation, request, **_kwargs):
            operations.append(operation)
            if operation == 'session-inbox-control':
                return dict(attempt=copy.deepcopy(stored))
            if operation == 'session-tool-stop':
                stored['terminal_scan'] = request['terminal_scan']
                if request['terminal_scan'] == 'clear':
                    self.assertEqual(request['tools'], [dict(item_id='tool-one', stop_state='terminated')])
                    stored['tools'] = copy.deepcopy(request['tools'])
                return copy.deepcopy(stored)
            if operation == 'session-inbox-reconcile':
                self.assertEqual(request['reason'], 'cancel_confirmed')
                return dict(stored, finished_at='now')
            self.fail(operation)

        scanner = types.SimpleNamespace(markers=lambda *_args, **_kwargs: next(scans))
        with patch.dict('sys.modules', {'process_scan': scanner}), \
                patch.object(coordination, 'opencode_capture_available', return_value=True), \
                patch.object(coordination, 'call', side_effect=call), \
                patch.object(coordination, 'submit_opencode_cancel') as submit:
            for _ in range(2):
                self.assertTrue(coordination.watch_pending_cancels(self.config))
                self.assertIn('inbox_intent', coordination.json.loads(self.path.read_text()))
                self.assertNotIn('session-inbox-reconcile', operations)
            self.assertFalse(coordination.watch_pending_cancels(self.config))
        self.assertEqual(operations.count('session-inbox-reconcile'), 1)
        self.assertNotIn('inbox_intent', coordination.json.loads(self.path.read_text()))
        submit.assert_not_called()


class SchedulingBoundaries(unittest.TestCase):
    def test_no_fast_pass_begins_at_full_cycle_deadline(self):
        stop, moments = fixtures.FakeStop(), []
        def fast_pass(_config):
            moments.append(stop.now - 1000)
            return True
        with patch.object(coordination, 'pending_cancel_configs', return_value=[{}]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=fast_pass):
            coordination.wait_for_next_cycle(stop, [{}], 6, clock=stop.clock)
        self.assertEqual(moments, [2, 4])
        self.assertEqual(stop.now - 1000, 6)

    def test_an_overrunning_pass_does_not_start_the_next_binding(self):
        stop, seen = fixtures.FakeStop(), []
        def fast_pass(config):
            seen.append(config['binding'])
            stop.now += 10
            return True
        with patch.object(coordination, 'pending_cancel_configs', return_value=[dict(binding='first'), dict(binding='second')]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=fast_pass):
            coordination.wait_for_next_cycle(stop, [], 6, clock=stop.clock)
        self.assertEqual(seen, ['first'])

    def test_shutdown_during_first_binding_skips_the_next(self):
        stop, seen = fixtures.FakeStop(), []
        def fast_pass(config):
            seen.append(config['binding'])
            stop.stopped = True
            return True
        with patch.object(coordination, 'pending_cancel_configs', return_value=[dict(binding='first'), dict(binding='second')]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=fast_pass):
            coordination.wait_for_next_cycle(stop, [], 30, clock=stop.clock)
        self.assertEqual(seen, ['first'])


if __name__ == '__main__':
    unittest.main()
