"""Inert full session boundaries plus explicit over-cap composition refusals.

Scheduling-only doubles below test ordering, not physical fit or a successful
full composition. No test reduces the existing preparation or endpoint quotes.
"""
import inspect
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixture
import test_trial_sdk_session_resources as session_fixture
from test_trial_sdk_engine_memory_rpc_parent import Harness, RPC_QUOTE, OP_QUOTE
from trial_sdk_command_operation import CommandOperation, HookOperationForward, OperationError, unsupported_native
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_engine_memory_rpc import RPCQuote
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_producer_resources import ProducerResources
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_shared_coordinator import AggregateQuote, CoordinatorError, SharedCoordinator


# Existing RPC/client fixture + producer storage + newly named535 raw bytes
# (RPC chunk512, client carries15, producer transient8 beyond its old carry4).
# This increases the total rather than shrinking any existing partition. These are
# explicitly incomplete working/backing assumptions, NOT a full attempt quote.
AGGREGATE = AggregateQuote(
    RPCQuote(RPC_QUOTE.raw + ProducerResources.MINIMUM_RAW + 535,
             RPC_QUOTE.working + 2**20, RPC_QUOTE.client),
    RPC_QUOTE.raw, RPC_QUOTE.working, ProducerResources.MINIMUM_RAW, 2**20)


class CoordinatorTests(unittest.TestCase):
    setUp = fixture.SDKReviewTests.setUp
    setup_binding = session_fixture.SessionResourcesTests.setup_binding
    running = session_fixture.SessionResourcesTests.running

    def setup_coordinator(self):
        h = Harness(self)
        prepared, binding = self.setup_binding(h.resources)
        session = SDKSessionDriver.from_resources(prepared, binding)
        self.addCleanup(session.close_sources)
        coordinator = SharedCoordinator(session, h.gate, h.link, clock=lambda: h.now)
        return h, session, coordinator

    def callback(self, session, coordinator):
        control = session.launch.control
        response = dict(type='control_response', response=dict(subtype='success',
            request_id=control.bindings['initialize_request_id'], response={}))
        request = dict(type='control_request', request_id='coordinator-0',
            request=dict(subtype='hook_callback',
                callback_id=control.bindings['callback_ids']['SessionStart'],
                input=dict(hook_event_name='SessionStart',
                           session_id=control.bindings['session_id'], extra='café 😀')))
        messages = [bytearray(json.dumps(value).encode() + b'\n') for value in (response, request)]

        def select(reads, writes, errors, timeout):
            self.assertEqual(timeout, 0)
            return ([] if writes or not messages else [733], writes, [])

        def read(fd, views):
            self.assertEqual(fd, 733)
            body = messages[0]
            count = min(len(body), len(views[0]), 17)
            views[0][:count] = body[:count]
            del body[:count]
            if not body:
                messages.pop(0)
            return count

        with patch('select.select', side_effect=select), patch('os.readv', side_effect=read), \
                patch('os.write', side_effect=lambda fd, body: len(body)):
            for _ in range(150):
                turn = coordinator.step()
                if turn.result.kind == 'callback':
                    return turn.result
        self.fail('bounded synthetic callback did not arrive')

    def test_actual_sdk_receipt_and_exact_callback_observation(self):
        h, session, coordinator = self.setup_coordinator()
        before = h.resources.snapshot()['raw_reserved']
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            self.assertIs(coordinator._event, event)
            self.assertEqual(event.observation['deadline_ns'], 5_000_000_001)
            state = session.snapshot()['stream']
            self.assertGreater(state['writer']['positive_write_bytes'], 0)
            self.assertGreater(state['stdout_bytes'], 0)
            self.assertEqual(session._resources.stream.snapshot()['known'],
                state['writer']['positive_write_bytes'] + state['stdout_bytes'])
            self.assertIs(coordinator.resources, h.resources)
            self.assertIs(coordinator.gate, h.gate)
            self.assertEqual(h.resources.snapshot()['raw_reserved'], before)

    def test_original_aggregate_is_shared_but_old_quote_is_undercoverage(self):
        h, session, coordinator = self.setup_coordinator()
        insufficient = AggregateQuote(RPC_QUOTE, RPC_QUOTE.raw, RPC_QUOTE.working,
                                      ProducerResources.MINIMUM_RAW, 2**20)
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            with self.assertRaisesRegex(CoordinatorError, 'coordinator_aggregate_underquoted'):
                coordinator.bind_callback(event, h.owner, h.domain, h.allocation, insufficient)
            self.assertIsNone(coordinator.producer)
            self.assertIsNone(coordinator.rpc)
            self.assertTrue(h.resources.snapshot()['unknown'])
            self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)

    def test_public_reservation_refuses_actual_aggregate_before_port_construction(self):
        prepared, binding = self.setup_binding()
        bank = binding.resources
        session = SDKSessionDriver.from_resources(prepared, binding)
        self.addCleanup(session.close_sources)
        link = ServiceLink(bank, deadline_ns=5_000_000_001,
                           standing_raw=5184, standing_working=2**20, clock=lambda: 1)
        gate = MemoryDispatchGate(unsupported_native, HookOperationForward(link), 30_000_000_001)
        coordinator = SharedCoordinator(session, gate, link, clock=lambda: 1)
        owner = NativeProcessOwner()
        domain = bank.bind(owner)
        AGGREGATE.check()
        self.assertEqual(bank.snapshot()['raw_reserved'], 73286)
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            with self.assertRaisesRegex(CoordinatorError, '^coordinator_aggregate_denied$'):
                coordinator.reserve_callback(event, owner, domain, AGGREGATE)
        self.assertIsNone(coordinator.rpc)
        self.assertIsNone(coordinator.producer)
        self.assertEqual(bank.snapshot()['raw_reserved'], 73286)
        self.assertEqual(bank.snapshot()['raw_reserved'] + AGGREGATE.rpc.raw, 136353)
        self.assertIsNone(owner.snapshot()['pid'])
        self.assertTrue(bank.snapshot()['unknown'])

    def test_unchanged_command_parent_refuses_before_child_reservation(self):
        h, session, coordinator = self.setup_coordinator()
        AGGREGATE.check()
        fixed = h.resources.snapshot()['raw_reserved'] - RPC_QUOTE.raw
        self.assertEqual(fixed + AGGREGATE.rpc.raw, 136353)
        self.assertGreater(fixed + AGGREGATE.rpc.raw, 131072)
        self.assertEqual(fixed + RPC_QUOTE.raw + OP_QUOTE.parent_raw + OP_QUOTE.child_raw, 184530)
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)
        with self.assertRaisesRegex(OperationError, '^operation_parent_denied$'):
            CommandOperation(h.link, OP_QUOTE, operation=1, lease=1,
                             deadline_ns=2_000_000_001, clock=lambda: 1)
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.link.transcript.snapshot()['operation'], 0)

    def test_foreign_gate_and_bank_do_not_bind(self):
        h, session, coordinator = self.setup_coordinator()
        other = Harness(self)
        with self.assertRaisesRegex(CoordinatorError, 'coordinator_binding'):
            SharedCoordinator(session, other.gate, other.link)
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertIs(coordinator.gate._native_forward, h.gate._native_forward)

    def test_new_named_phase_floor_refuses_before_callback_ports_allocate(self):
        h, session, coordinator = self.setup_coordinator()
        old = replace(AGGREGATE, rpc=replace(AGGREGATE.rpc, raw=62532))
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            with self.assertRaisesRegex(CoordinatorError, '^coordinator_phase_underquoted$'):
                coordinator.reserve_callback(event, h.owner, h.domain, old)
        self.assertIsNone(coordinator.rpc)
        self.assertIsNone(coordinator.producer)
        self.assertIsNone(coordinator._allocation)

    def test_recreated_callback_cannot_replace_original_event(self):
        h, session, coordinator = self.setup_coordinator()
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            before = h.resources.snapshot()['raw_reserved']
            with self.assertRaisesRegex(CoordinatorError, '^coordinator_callback_binding$'):
                coordinator.bind_callback(replace(event), h.owner, h.domain,
                                          h.allocation, AGGREGATE)
            self.assertEqual(h.resources.snapshot()['raw_reserved'], before)
            self.assertIsNone(coordinator.rpc)

    def test_foreign_ticket_refuses_before_constructing_callback_ports(self):
        h, session, coordinator = self.setup_coordinator()
        foreign = Harness(self)
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            with self.assertRaisesRegex(ValueError, '^resource_ticket$'):
                coordinator.bind_callback(event, h.owner, h.domain,
                                          foreign.allocation, AGGREGATE)
            self.assertIsNone(coordinator.rpc)
            self.assertIsNone(coordinator.producer)
            self.assertFalse(foreign.resources.snapshot()['unknown'])
            self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)

    def test_original_deadline_is_not_reset_at_callback_handoff(self):
        h, session, coordinator = self.setup_coordinator()
        with self.running(session), patch('time.monotonic_ns', return_value=1):
            event = self.callback(session, coordinator)
            h.now = event.observation['deadline_ns']
            with self.assertRaisesRegex(CoordinatorError, 'coordinator_deadline'):
                coordinator.pending_request()
            self.assertTrue(coordinator.snapshot()['unknown'])
            self.assertTrue(h.resources.snapshot()['unknown'])
            self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)

    def test_standing_release_refuses_before_next_session_effect(self):
        h, session, coordinator = self.setup_coordinator()
        h.resources.release(session._custody._allocation.ticket)
        with patch.object(session, 'step', side_effect=AssertionError('unexpected I/O')) as step:
            with self.assertRaises(ValueError):
                coordinator.step()
        step.assert_not_called()
        self.assertTrue(coordinator.snapshot()['unknown'])

    def interrupt_final(self, method, invoke, primary):
        lines, start = inspect.getsourcelines(method)
        target = start + next(i for i, line in enumerate(lines) if 'self._busy = False' in line)
        hits = []

        def trace(frame, event, arg):
            if event == 'line' and frame.f_code is method.__code__ and frame.f_lineno == target:
                hits.append(True)
                raise primary
            return trace

        prior = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(type(primary)) as caught:
                invoke()
        finally:
            sys.settrace(prior)
        self.assertIs(caught.exception, primary)
        self.assertEqual(hits, [True])

    def test_actual_final_call_interrupt_retains_sdk_positive_prefix(self):
        h, session, coordinator = self.setup_coordinator()
        primary = KeyboardInterrupt('final coordinator cleanup')
        with self.running(session), patch('time.monotonic_ns', return_value=1), \
                patch('select.select', return_value=([], [731], [])), \
                patch('os.write', return_value=2) as write:
            self.interrupt_final(SharedCoordinator._call, coordinator.step, primary)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(session._resources.stream.snapshot()['known'], 2)
            self.assertEqual(session.snapshot()['stream']['writer']['positive_write_bytes'], 2)
            self.assertTrue(coordinator.snapshot()['unknown'])
            self.assertTrue(h.resources.snapshot()['unknown'])

    def test_actual_final_close_interrupt_preserves_completed_source_cleanup(self):
        h, session, coordinator = self.setup_coordinator()
        primary = KeyboardInterrupt('final source cleanup')
        self.interrupt_final(SharedCoordinator.close_sources, coordinator.close_sources, primary)
        self.assertTrue(session.snapshot()['sources_close_attempted'])
        self.assertTrue(session._custody.control.ledger()['closed'])
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)
        self.assertEqual(coordinator.close_sources(), ())

    def test_primary_survives_a_second_interrupt_in_final_cleanup(self):
        h, session, coordinator = self.setup_coordinator()
        primary, secondary = RuntimeError('original'), KeyboardInterrupt('final cleanup')
        coordinator._clock = lambda: (_ for _ in ()).throw(primary)
        lines, start = inspect.getsourcelines(SharedCoordinator._call)
        target = start + next(i for i, line in enumerate(lines) if 'self._busy = False' in line)
        hits = []

        def trace(frame, event, arg):
            if event == 'line' and frame.f_code is SharedCoordinator._call.__code__ \
                    and frame.f_lineno == target:
                hits.append(True)
                raise secondary
            return trace

        prior = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(RuntimeError) as caught:
                coordinator.step()
        finally:
            sys.settrace(prior)
        self.assertIs(caught.exception, primary)
        self.assertEqual(hits, [True])
        self.assertIn('coordinator_final_cleanup_unknown', primary.cleanup_faults)
        coordinator.close_sources()
        self.assertTrue(session._custody.control.ledger()['closed'])

    def test_primary_survives_poison_and_freeze_interrupts(self):
        h, session, coordinator = self.setup_coordinator()
        primary = RuntimeError('original clock failure')
        coordinator._clock = lambda: (_ for _ in ()).throw(primary)
        with patch.object(h.resources, 'freeze', side_effect=KeyboardInterrupt('freeze')), \
                patch.object(session, '_poison', side_effect=KeyboardInterrupt('control')):
            with self.assertRaises(RuntimeError) as caught:
                coordinator.step()
        self.assertIs(caught.exception, primary)
        self.assertTrue(coordinator.snapshot()['unknown'])
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertTrue(session._resources._unknown)
        self.assertIn('resource_freeze_unknown', primary.cleanup_faults)
        self.assertIn('coordinator_session_cleanup_unknown', primary.cleanup_faults)
        coordinator.close_sources()
        self.assertTrue(session._custody.control.ledger()['closed'])

    def test_rotation_is_bounded_even_when_one_lane_is_continuously_ready(self):
        # Scheduling policy only: full ledger composition does not fit the
        # unchanged fixture. These doubles are not receipt/quote evidence.
        h, session, coordinator = self.setup_coordinator()
        calls = []
        coordinator.producer = SimpleNamespace(_ready=False,
            step=lambda timeout: calls.append(('producer', timeout)) or 'progress')
        coordinator.rpc = SimpleNamespace(_closed=False,
            step=lambda: calls.append(('rpc', 0)) or True)
        with patch.object(session, 'step', side_effect=lambda timeout:
                calls.append(('session', timeout)) or SimpleNamespace(kind='progress')):
            turns = [coordinator.step().lane for _ in range(9)]
        self.assertEqual(turns, ['session', 'producer', 'rpc'] * 3)
        self.assertEqual(calls, [(name, 0) for name in turns])
        coordinator.producer = coordinator.rpc = None

    def test_cancel_suppresses_output_without_claiming_native_cancellation(self):
        h, session, coordinator = self.setup_coordinator()
        coordinator.cancel()
        self.assertTrue(coordinator.snapshot()['cancelled'])
        self.assertFalse(h.resources.snapshot()['unknown'])
        with self.assertRaisesRegex(CoordinatorError, 'coordinator_output_pending'):
            coordinator.take_output()
        self.assertIsNone(session.native_owner.snapshot()['wait'])
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 110214)


if __name__ == '__main__':
    unittest.main()
