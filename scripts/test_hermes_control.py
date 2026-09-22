"""Cancellation ordering and restart recovery; native transport is tested separately."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/lifecycle'))
import coordination
import hermes_control as control


class Fixture:
    def __init__(self):
        self.session = dict(agent_id=str(uuid.uuid4()), execution_id=str(uuid.uuid4()))
        self.binding = dict(session_id='native-session', request_id=str(uuid.uuid4()),
                            delivery_id=str(uuid.uuid4()), turn_id='native-session:turn-one', ownership_token='token-one')
        self.attempt = dict(attempt_id=str(uuid.uuid4()), session=self.session, native_turn_id=self.binding['turn_id'],
                            turn_exclusive=True, delivery={'delivery_id': self.binding['delivery_id']},
                            cancel={'requested_at': 'now'}, tools=[])
        self.result = dict(ownership=dict(self.binding, exclusive=True, revoked=False, cancelled=False,
                                         turn_ended=False, tool_admission_closed=False, active_tool_calls=0),
                           tools=[], inventory_complete=True, terminal_scan='unknown_remaining')
        self.ledger = dict(attempt_id=self.attempt['attempt_id'])
        self.saved = copy.deepcopy(self.ledger)
        self.events = []
        self.rpc_error = None
        self.api_error = None

    def save(self):
        self.saved = copy.deepcopy(self.ledger)

    def api(self, operation, request):
        self.events.append((operation, copy.deepcopy(request)))
        if operation == 'session-inbox-control':
            return {'attempt': copy.deepcopy(self.attempt)}
        if self.api_error:
            raise self.api_error
        if operation == 'session-tool-capture':
            self.attempt['tools'].extend(dict(t, stop_state='captured') for t in request['items'])
        if operation == 'session-tool-stop':
            self.attempt['terminal_scan'] = request['terminal_scan']
            if 'turn_stop' in request:
                self.attempt['turn_stop_state'] = request['turn_stop']
            if request.get('owner_join') and self.attempt['turn_exclusive']:
                self.attempt['turn_exclusive'] = False
                self.attempt['terminal_scan'] = ''
        return self.attempt

    def rpc(self, method, params):
        self.events.append((method, copy.deepcopy(params)))
        if method != 'session/request_status':
            # Durable marker must exist before the first byte can be sent.
            self.assert_saved_dispatch(method, params)
            if self.rpc_error:
                raise self.rpc_error
        return copy.deepcopy(self.result)

    def assert_saved_dispatch(self, method, params):
        if not any(d['kind'] == method.removeprefix('session/request_') and d['params'] == params
                   for d in self.saved.get('dispatch', {}).values()):
            raise AssertionError('native mutation was sent before durable intent')

    def poll(self):
        control.Controller(self.ledger, self.save, self.api, self).poll(self.session, self.binding)

    def restart(self):
        self.ledger = copy.deepcopy(self.saved)

    def tool(self, item='tool-one', stop='captured'):
        return dict(item_id=item, process_id='pid:123:start:456', native_turn_id=self.binding['turn_id'], stop_state=stop)

    def calls(self, name):
        return [r for op, r in self.events if op == name]


class ControlTests(unittest.TestCase):
    def test_uncertain_cancel_is_not_replayed_after_restart(self):
        f = Fixture()
        f.rpc_error = control.ControlUncertain('reply lost after effect')
        with self.assertRaises(control.ControlUncertain):
            f.poll()
        f.restart()
        f.poll()
        self.assertEqual(len(f.calls('session/request_cancel')), 1)
        self.assertEqual(f.calls('session-inbox-reconcile'), [])

    def test_unavailable_before_send_allows_retry_but_refusal_does_not(self):
        for error, count in ((control.ControlUnavailable('not sent'), 2),
                             (control.ControlRefused(-1, 'OWNERSHIP_REVOKED'), 1)):
            with self.subTest(error=type(error).__name__):
                f = Fixture()
                f.rpc_error = error
                with self.assertRaises(type(error)):
                    f.poll()
                f.restart()
                f.rpc_error = None
                f.poll()
                self.assertEqual(len(f.calls('session/request_cancel')), count)

    def test_capture_precedes_cleanup_and_uncertain_item_is_not_killed_twice(self):
        f = Fixture()
        f.result['ownership'].update(cancelled=True, tool_admission_closed=True, active_tool_calls=1)
        f.result['tools'] = [f.tool()]
        f.rpc_error = control.ControlUncertain('kill reply lost')
        with self.assertRaises(control.ControlUncertain):
            f.poll()
        names = [op for op, _ in f.events]
        self.assertLess(names.index('session-tool-capture'), names.index('session/request_cleanup'))
        f.restart()
        f.poll()
        self.assertEqual(len(f.calls('session/request_cleanup')), 1)
        self.assertEqual(f.calls('session-inbox-reconcile'), [])

    def test_joined_owner_can_only_reconcile_after_natural_verified_end(self):
        f = Fixture()
        f.result['ownership'].update(exclusive=False, revoked=True)
        f.result['tools'] = [f.tool()]
        f.poll()
        self.assertEqual(f.calls('session/request_cancel'), [])
        self.assertEqual(f.calls('session/request_cleanup'), [])
        self.assertEqual(f.calls('session-inbox-reconcile'), [])
        self.assertTrue(f.calls('session-tool-stop')[-1]['owner_join'])
        f.result['ownership'].update(turn_ended=True, tool_admission_closed=True)
        f.result['tools'] = [f.tool(stop='terminated')]
        f.result['terminal_scan'] = 'clear'
        f.poll()
        self.assertEqual(f.calls('session-inbox-reconcile')[-1]['reason'], 'exclusivity_revoked')

    def test_confirm_requires_quiescence_and_complete_os_scan(self):
        for change in ({'active_tool_calls': 1}, {'turn_ended': False}, {'tool_admission_closed': False},
                       {'inventory_complete': False}, {'terminal_scan': 'unknown_remaining'}):
            with self.subTest(change=change):
                f = Fixture()
                f.result['ownership'].update(cancelled=True, turn_ended=True, tool_admission_closed=True)
                f.result['terminal_scan'] = 'clear'
                for key, value in change.items():
                    (f.result if key in f.result else f.result['ownership'])[key] = value
                f.poll()
                self.assertEqual(f.calls('session-inbox-reconcile'), [])
        f = Fixture()
        f.result['ownership'].update(cancelled=True, turn_ended=True, tool_admission_closed=True)
        f.result['terminal_scan'] = 'clear'
        f.poll()
        self.assertEqual(f.calls('session-inbox-reconcile')[-1]['reason'], 'cancel_confirmed')

    def test_disappearing_captured_tool_is_not_clear(self):
        f = Fixture()
        f.attempt['tools'] = [f.tool()]
        f.result['ownership'].update(cancelled=True, turn_ended=True, tool_admission_closed=True)
        f.result['terminal_scan'] = 'clear'
        f.poll()
        self.assertEqual(f.calls('session-inbox-reconcile'), [])
        self.assertEqual(f.calls('session-tool-stop')[-1]['terminal_scan'], 'unknown_remaining')

    def test_changed_binding_or_tool_identity_cannot_mutate(self):
        for mismatch in ('token', 'attempt', 'process'):
            f = Fixture()
            if mismatch == 'token':
                f.result['ownership']['ownership_token'] = 'another-token'
            elif mismatch == 'attempt':
                f.attempt['attempt_id'] = str(uuid.uuid4())
            else:
                f.attempt['tools'] = [f.tool()]
                f.result['tools'] = [dict(f.tool(), process_id='another-process')]
            with self.subTest(mismatch=mismatch), self.assertRaises(control.ControlUncertain):
                f.poll()
            self.assertFalse(any(op in ('session-tool-capture', 'session-tool-stop', 'session/request_cancel')
                                 for op, _ in f.events))

    def test_uncertain_store_mutation_reuses_exact_uuid_and_payload(self):
        f = Fixture()
        f.result['tools'] = [f.tool()]
        f.api_error = OSError('API reply lost')
        with self.assertRaises(OSError):
            f.poll()
        first = f.calls('session-tool-capture')[0]
        f.restart()
        f.api_error = None
        f.poll()
        self.assertEqual(f.calls('session-tool-capture'), [first, first])

    def test_nonexclusive_claim_cannot_be_upgraded_by_later_snapshot(self):
        f = Fixture()
        f.attempt['turn_exclusive'] = False
        f.poll()
        self.assertEqual(f.calls('session/request_cancel'), [])

    def test_first_owner_join_at_ended_turn_requires_fresh_scan(self):
        f = Fixture()
        f.result['ownership'].update(exclusive=False, revoked=True, turn_ended=True, tool_admission_closed=True)
        f.result['terminal_scan'] = 'clear'
        f.poll()
        self.assertEqual(f.calls('session-inbox-reconcile'), [])
        f.restart()
        f.poll()
        self.assertEqual(f.calls('session-inbox-reconcile')[-1]['reason'], 'exclusivity_revoked')

    def test_definitive_reconcile_refusal_does_not_block_new_observations(self):
        f = Fixture()
        f.ledger['pending'] = dict(operation='session-inbox-reconcile', request=dict(
            request_id=str(uuid.uuid4()), session=f.session, attempt_id=f.attempt['attempt_id'], reason='cancel_confirmed'))
        f.api_error = coordination.CoordinationError('CLEANUP_UNCONFIRMED', 'new evidence required')
        with self.assertRaises(coordination.CoordinationError):
            f.poll()
        f.restart()
        f.api_error = None
        f.poll()
        self.assertEqual(len(f.calls('session-inbox-reconcile')), 1)
        self.assertEqual(len(f.calls('session/request_status')), 1)

    def test_hook_binds_only_exact_native_admission_and_preserves_legacy_delivery(self):
        f = Fixture()
        wake = dict(native_id=f.binding['session_id'], request_id=f.binding['request_id'],
                    delivery_id=f.binding['delivery_id'])
        binding = dict(delivery_id=wake['delivery_id'], native_turn_id=f.binding['turn_id'])
        state = dict(idle_wake=wake)
        config = dict(harness='hermes', hermes_request_control=True)
        event = dict(request_ownership=f.result['ownership'])
        selected = coordination.hermes_control_binding(config, state, event, binding)
        self.assertTrue(selected['turn_exclusive'])
        self.assertEqual(state['hermes_request'], f.binding)
        self.assertEqual(coordination.hermes_control_binding(config, {}, {}, binding), binding)
        event['request_ownership']['request_id'] = str(uuid.uuid4())
        with self.assertRaises(coordination.CoordinationError):
            coordination.hermes_control_binding(config, state, event, binding)


if __name__ == '__main__':
    unittest.main()
