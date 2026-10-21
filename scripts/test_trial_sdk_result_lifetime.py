"""Exclusive production callback custody; authored services and inert edges."""
import contextlib
import json
import unittest
import weakref
from unittest.mock import patch

from test_trial_sdk_fixed_outer import session_for
from test_trial_sdk_fixed_bootstrap import ScopedBindingHarness, ARGS, finish_bootstrap
from test_trial_sdk_engine_memory_rpc_parent import OP_QUOTE
from test_trial_sdk_callback_phases import MIB
from trial_sdk_fixed_outer import FixedCallbackDriver
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_source_profile import PREPARATION_RAW, PREPARATION_WORKING
from test_trial_sdk_engine_memory_rpc_parent import Harness
from test_trial_sdk_read_custody import search_result, RECEIPT, HANDLE, REQUEST, RECORD
from test_trial_sdk_callback_phases import interrupt_line
from trial_sdk_command_operation import CommandOperation
from trial_sdk_engine_memory_rpc import ProducerRPC
from trial_sdk_engine_adapter import EngineMemoryAdapter
from trial_sdk_read_custody import ReadCustody
from test_trial_sdk_engine_adapter import engine_module
import trial_sdk_engine_memory_rpc_wire as wire


class ExclusiveHarness(ScopedBindingHarness):
    # Generic PhaseHarness deliberately retains borrowed results. Production
    # exposes progress only, so use the pump without those diagnostic borrows.
    pump = Harness.pump


class ResultLifetimeTests(unittest.TestCase):
    @contextlib.contextmanager
    def owned(self):
        h = ExclusiveHarness(self)
        preparation = h.resources.allocate(h.resources.local, 'standing',
                                          PREPARATION_RAW, PREPARATION_WORKING)
        session = h.resources.bind(NativeProcessOwner())
        standing = h.resources.allocate(session, 'operation', 2566, MIB)
        self.assertIsNotNone(preparation)
        self.assertIsNotNone(standing)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            h.rpc._connection = h.held_rpc_connection
            with h.inert_owner(), h.readiness(), session_for(h):
                driver = FixedCallbackDriver(h.coordinator, h.fixed, OP_QUOTE)
                h.handoff = driver.step
                yield h, driver

    def test_two_searches_four_pulls_with_all_standing_reservations(self):
        with self.owned() as (h, driver):
            h.stdout = search_result()
            answers = [h.client.call('search', ARGS) for _ in range(2)]
            h.stdout = json.dumps(dict(ok=True, data={'selection': {'record': {
                'record_id': RECORD, 'version': 1, 'body': 'synthetic café'}}})).encode()
            body = json.dumps(dict(request_id=REQUEST, receipt_id=RECEIPT, handle=HANDLE))
            answers.extend(h.client.call('pull', (), body) for _ in range(4))
            self.assertEqual([a.returncode for a in answers], [0] * 6)
            self.assertEqual(h.gate.snapshot()['dispatches'], 6)
            self.assertEqual(h.gate.snapshot()['expansions_debited'], 4)
            self.assertTrue(all('synthetic caf' in a.stdout for a in answers[2:]))
            self.assertEqual(h.result_aliases, [])
            self.assertEqual(h.pending_aliases, [])
            self.assertIsNone(h.rpc._operation)
            self.assertEqual(h.resources.snapshot()['working_reserved'], 259 * MIB)
            self.assertLessEqual(h.resources.snapshot()['known_allocation_peak'], 512 * MIB)
            self.assertLessEqual(h.resources.snapshot()['raw_peak'], 131072)
            self.assertFalse(h.resources.snapshot()['unknown'])
            h.client.close()

    def test_original_engine_keeps_independent_graphs_after_parent_results_end(self):
        engine = engine_module()
        parent_results = []
        observe = ReadCustody.observe_completed
        def observe_result(custody):
            parent_results.append(weakref.ref(custody._rpc._result))
            return observe(custody)
        with self.owned() as (h, driver), patch.object(ReadCustody, 'observe_completed', observe_result):
            with EngineMemoryAdapter(engine, h.fixed._config, h.client):
                memory = engine.Memory(h.fixed._config, 'synthetic-session')
                h.stdout = search_result()
                graphs = [memory.search('authored synthetic query') for _ in range(2)]
                h.stdout = json.dumps(dict(ok=True, data={'selection': {'record': {
                    'record_id': RECORD, 'version': 1, 'body': 'café 😀' * 1000}}})).encode()
                for _ in range(4):
                    graphs.append(memory.call('pull', payload=dict(
                        request_id=REQUEST, receipt_id=RECEIPT, handle=HANDLE)))
                    self.assertTrue(all(ref() is None for ref in parent_results))
                self.assertEqual(len(graphs), 6)
                self.assertEqual(len({id(g) for g in graphs}), 6)
                self.assertTrue(all(g['selection']['record']['body'] == 'café 😀' * 1000
                                    for g in graphs[2:]))
                self.assertEqual(h.resources.snapshot()['working_reserved'], 259 * MIB)
                self.assertEqual(h.rpc._read_custody._offers[(RECEIPT, HANDLE)][:2], (RECORD, 1))
                h.client.close()

    def test_public_pending_borrow_refuses_after_exclusive_binding(self):
        with self.owned() as (h, driver):
            observed = []
            def handoff():
                with self.assertRaisesRegex(ValueError, 'rpc_exclusive_command_owner'):
                    h.rpc.pending_request()
                with self.assertRaisesRegex(ValueError, 'rpc_exclusive_command_owner'):
                    h.rpc._pending_request(object())
                observed.append(True)
                return driver.step()
            h.handoff = handoff
            h.client.call('search', ARGS)
            self.assertTrue(observed)
            self.assertFalse(h.resources.snapshot()['unknown'])
            h.client.close()

    def test_exclusive_callback_cannot_be_wrapped_by_a_second_owner(self):
        with self.owned() as (h, driver):
            h.client.call('search', ARGS)
            with self.assertRaisesRegex(ValueError, 'outer_command_owner'):
                FixedCallbackDriver(h.coordinator, h.fixed, OP_QUOTE)
            self.assertIs(h.rpc._command_owner, driver)
            h.client.close()

    def test_cancellation_keeps_full_command_working_and_no_result_status(self):
        from trial_sdk_engine_memory_rpc_client import RPCRefused
        with self.owned() as (h, driver):
            cancelled = []
            def cancel():
                if h.rpc._phase == 'command' and not cancelled:
                    cancelled.append(h.rpc.cancel())
            h.on_pump = cancel
            with self.assertRaisesRegex(RPCRefused, 'rpc_refused_4'):
                h.client.call('search', ARGS)
            self.assertEqual(cancelled, [True])
            self.assertIsNone(h.rpc._read_custody._last_operation)
            self.assertEqual(h.resources._memory[h.allocation][3], 193 * MIB)
            self.assertIsNone(h.rpc._operation)
            self.assertFalse(any(r[5] == wire.STATUS for r in h.to_client))
            h.client.close()

    def test_generic_command_cannot_be_adopted_after_a_public_borrow(self):
        h = ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            h.rpc._connection = h.held_rpc_connection
            with h.inert_owner(), h.readiness(), session_for(h):
                finish_bootstrap(self, h)
                h.client.call('search', ARGS)
                retained = h.result_aliases[0]
                driver = FixedCallbackDriver(h.coordinator, h.fixed, OP_QUOTE)
                self.assertIsNone(h.rpc._command_owner)
                h.handoff = driver.step
                h.client.call('search', ARGS)
                self.assertEqual(retained.returncode, 0)
                self.assertEqual(h.resources._memory[h.allocation][3], 257 * MIB)
                h.client.close()

    def test_nonzero_command_ends_parent_text_but_keeps_metadata_charge(self):
        with self.owned() as (h, driver):
            h.code, h.stdout, h.stderr = 17, b'not JSON', b'command refused'
            for _ in range(4):
                answer = h.client.call('search', ARGS)
                self.assertEqual((answer.returncode, answer.stderr), (17, 'command refused'))
            self.assertEqual(h.resources.snapshot()['working_reserved'], 259 * MIB)
            self.assertIsNone(h.rpc._operation)
            h.client.close()

    def test_parse_exception_retains_original_command_ticket_and_traceback(self):
        with self.owned() as (h, driver):
            h.stdout = b'invalid JSON'
            try:
                h.client.call('search', ARGS)
            except ValueError as error:
                retained = error
            else:
                self.fail('invalid result was accepted')
            self.assertIsNotNone(retained.__traceback__)
            self.assertTrue(h.resources.snapshot()['unknown'])
            self.assertIsNotNone(h.rpc._operation._parent)
            self.assertFalse(h.rpc._operation._retired)
            self.assertFalse(any(r[5] == wire.STATUS for r in h.to_client))

    def test_interrupted_exclusive_return_preserves_charge_and_suppresses_status(self):
        for function, needle in (
                (FixedCallbackDriver._release_command, 'phases._transition('),
                (FixedCallbackDriver._release_command, 'rpc._retire_operation()'),
                (ProducerRPC._retire_operation, 'self._operation.retire()'),
                (CommandOperation.retire, 'self.resources.release(self._parent)')):
            with self.subTest(needle=needle), self.owned() as (h, driver):
                hit = []
                def interrupt():
                    if h.rpc._phase == 'phase_return':
                        def action():
                            with self.assertRaises(KeyboardInterrupt):
                                h.rpc.step()
                        hit.extend(interrupt_line(function, needle, action))
                        raise RuntimeError('stop inert client')
                h.on_pump = interrupt
                with self.assertRaisesRegex(RuntimeError, 'stop inert client'):
                    h.client.call('search', ARGS)
                self.assertEqual(hit, [True])
                self.assertTrue(h.resources.snapshot()['unknown'])
                self.assertIsNotNone(h.rpc._operation._parent)
                self.assertFalse(h.rpc._operation._retired)
                self.assertFalse(any(r[5] == wire.STATUS for r in h.to_client))

    def test_interrupt_after_known_parent_release_keeps_metadata_and_prevents_replay(self):
        with self.owned() as (h, driver):
            hit = []
            def interrupt():
                if h.rpc._phase == 'phase_return':
                    def action():
                        with self.assertRaises(KeyboardInterrupt):
                            h.rpc.step()
                    hit.extend(interrupt_line(CommandOperation.retire, 'self._parent = None', action))
                    raise RuntimeError('stop inert client')
            h.on_pump = interrupt
            with self.assertRaisesRegex(RuntimeError, 'stop inert client'):
                h.client.call('search', ARGS)
            self.assertEqual(hit, [True])
            operation = h.rpc._operation
            self.assertNotIn(operation._parent, h.resources._memory)
            self.assertIsNone(operation._document)
            self.assertIsNone(operation._result)
            self.assertIsNone(h.rpc._result)
            self.assertEqual(h.resources._memory[h.allocation][3], 193 * MIB)
            self.assertTrue(h.resources.snapshot()['unknown'])
            self.assertFalse(any(r[5] == wire.STATUS for r in h.to_client))
            with self.assertRaisesRegex(ValueError, 'rpc_parent_unavailable'):
                h.rpc.step()

    def test_metadata_capacity_denial_keeps_original_parent_before_release(self):
        with self.owned() as (h, driver):
            blocked = []
            def fill():
                if h.rpc._phase == 'phase_return' and not blocked:
                    room = 512 * MIB - h.resources._raw - h.resources._working
                    other = h.resources.bind(NativeProcessOwner())
                    blocked.append(h.resources.allocate(other, 'standing', 1, room - 1))
            h.on_pump = fill
            with self.assertRaisesRegex(ValueError, 'rpc_phase_capacity_denied'):
                h.client.call('search', ARGS)
            self.assertTrue(blocked)
            self.assertIsNotNone(blocked[0])
            self.assertIsNotNone(h.rpc._operation._parent)
            self.assertFalse(h.rpc._operation._retired)
            self.assertTrue(h.resources.snapshot()['unknown'])
            self.assertFalse(any(r[5] == wire.STATUS for r in h.to_client))


if __name__ == '__main__':
    unittest.main()
