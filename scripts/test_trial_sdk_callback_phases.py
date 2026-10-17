"""Actual producer/RPC/command service over inert edges; no full SDK fit claim.

The 63067 raw fixture is a HIGHER named floor than the previous 62532.
Working uses the existing RPC128MiB + producer1MiB + service1MiB and 64MiB per
command parent/child. No SDK standing allocation is silently reduced or added.
"""
import contextlib
import inspect
import sys
import unittest
from unittest.mock import patch

from trial_sdk_attempt_resources import AttemptResources, CallbackMemory, ResourceError, RAW, WORKING
from trial_sdk_callback_phases import CallbackPhases, PRODUCER_CANONICAL, REQUEST_CANONICAL, SURVIVING_RAW
from trial_sdk_command_operation import CommandOperation
from trial_sdk_engine_memory_rpc import ProducerRPC, RPCQuote
from trial_sdk_engine_memory_rpc_client import RPCRefused
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_producer_driver import SDKProducerDriver
from trial_sdk_producer_resources import ProducerResources
from trial_sdk_incoming import canonical_chunks
import trial_sdk_engine_memory_rpc_wire as wire
import test_trial_sdk_engine_memory_rpc_parent as fixture
from test_trial_sdk_command_input import process_boundaries


NAMED_RAW = SURVIVING_RAW + PRODUCER_CANONICAL + REQUEST_CANONICAL
MIB = 1024 * 1024


def interrupt_line(function, needle, action):
    lines, start = inspect.getsourcelines(function)
    matches = [start + n for n, line in enumerate(lines) if needle in line]
    assert len(matches) == 1, (needle, matches)
    hit = []
    def trace(frame, event, arg):
        if event == 'line' and frame.f_code is function.__code__ and frame.f_lineno == matches[0]:
            hit.append(True)
            raise KeyboardInterrupt('actual publication line')
        return trace
    prior = sys.gettrace()
    sys.settrace(trace)
    try:
        action()
    finally:
        sys.settrace(prior)
    return hit


class PhaseHarness(fixture.Harness):
    def __init__(self, test):
        events = []
        real_event = fixture.callback_event
        def event():
            control, observed = real_event()
            test.addCleanup(control.close)
            events.append(observed)
            return control, observed
        quote = RPCQuote(NAMED_RAW, fixture.RPC_QUOTE.working+MIB, fixture.RPC_QUOTE.client)
        with patch.object(fixture, 'RPC_QUOTE', quote), patch.object(fixture, 'callback_event', event):
            super().__init__(test)
        self.producer = SDKProducerDriver.from_resources(events[0], ProducerResources(
            self.resources, self.owner, self.domain, self.allocation, self.rpc.stream_credit()))
        self.rpc.bind_producer(self.producer)
        self.pending_aliases, self.result_aliases, self.operations = [], [], []
        self.parked = 0

    @contextlib.contextmanager
    def inert_owner(self):
        with process_boundaries(), patch('os.waitpid', return_value=(0, 0)), \
                patch('time.monotonic_ns', return_value=1), \
                patch('select.select', side_effect=lambda reads, writes, errors, timeout: ([], writes, [])), \
                patch('os.write', side_effect=lambda fd, data: min(127, len(data))):
            self.owner.start(('/inert-producer',), '/inert', {})
            yield

    def finish_input(self):
        for _ in range(100):
            self.producer.step(0)
            if self.producer._input_exhausted:
                break
        self.test.assertTrue(self.producer._input_exhausted)
        self.test.assertTrue(self.owner.snapshot()['native_input']['closed_known'])
        self.test.assertEqual(self.producer._written, self.producer._binding['input_bytes'])

    def handoff(self):
        if not self.rpc.prepare_command_phase():
            self.parked += 1
            return
        self.pending_aliases.append(self.rpc.pending_request())
        super().handoff()
        self.operations.append(self.rpc._operation)

    def pump(self):
        super().pump()
        operation = self.rpc._operation
        if operation is not None and operation._closed and operation._result is not None:
            if not self.result_aliases or self.result_aliases[-1] is not operation._result:
                self.result_aliases.append(operation.result())


class LedgerPhases(unittest.TestCase):
    def binding(self):
        bank = AttemptResources()
        domain = bank.bind(NativeProcessOwner())
        ticket = bank.allocate(domain, 'operation', NAMED_RAW, 128*MIB)
        return bank, domain, ticket, bank.bind_callback_memory(ticket)

    def test_exact_ticket_slot_totals_peaks_and_cumulative_working(self):
        bank, domain, ticket, state = self.binding()
        for index in range(2):
            state = bank.transition_callback_memory(state, SURVIVING_RAW, state.row[3], 'command')
            state = bank.transition_callback_memory(state, SURVIVING_RAW, state.row[3]+64*MIB, 'return')
            state = bank.transition_callback_memory(state, NAMED_RAW-PRODUCER_CANONICAL, state.row[3], 'request')
            self.assertIs(state.ticket, ticket)
            self.assertIs(bank._domains[domain]['memory']['operation'], ticket)
            self.assertEqual(bank._memory[ticket][2:], (NAMED_RAW-PRODUCER_CANONICAL, (192+64*index)*MIB))
            self.assertEqual(bank.snapshot()['working_reserved'], (192+64*index)*MIB)
        self.assertEqual(bank.snapshot()['raw_peak'], NAMED_RAW)
        self.assertEqual(bank.snapshot()['working_peak'], 256*MIB)

    def test_foreign_stale_recreated_replayed_and_released_states_refuse(self):
        bank, domain, ticket, state = self.binding()
        other, _, _, foreign = self.binding()
        forged = CallbackMemory(ticket, state.row, state.generation, state.phase)
        for invalid in (foreign, forged):
            with self.assertRaisesRegex(ResourceError, 'resource_callback_stale'):
                bank.transition_callback_memory(invalid, SURVIVING_RAW, 128*MIB, 'command')
        current = bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')
        with self.assertRaisesRegex(ResourceError, 'resource_callback_stale'):
            bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')
        with self.assertRaisesRegex(ResourceError, 'resource_callback_phase'):
            bank.transition_callback_memory(current, SURVIVING_RAW, 128*MIB, 'command')
        bank.release(ticket)
        with self.assertRaisesRegex(ResourceError, 'resource_callback_stale'):
            bank.transition_callback_memory(current, SURVIVING_RAW, 192*MIB, 'return')
        self.assertFalse(bank.snapshot()['unknown'])

    def test_denial_and_working_decrease_do_not_publish_or_mint_capacity(self):
        bank, _, ticket, state = self.binding()
        state = bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')
        before = bank.snapshot()
        self.assertIsNone(bank.transition_callback_memory(state, SURVIVING_RAW, WORKING, 'return'))
        self.assertEqual(bank.snapshot(), before)
        with self.assertRaisesRegex(ResourceError, 'resource_callback_transition'):
            bank.transition_callback_memory(state, SURVIVING_RAW, 127*MIB, 'return')
        self.assertEqual(bank._memory[ticket], state.row)

    def test_each_interrupted_publication_retains_old_proposed_totals_and_blocks_replay(self):
        # Actual source lines, not a mocked ledger result. Interrupt BEFORE each
        # write and at final _known; the prior publication remains conservative.
        method = AttemptResources.transition_callback_memory.__closure__[0].cell_contents
        for line in ('self._raw=next_raw', 'self._working=next_working',
                     'self._memory[expected.ticket]=proposed.row',
                     'self._callback_memory[expected.ticket]=proposed',
                     'self._raw_peak=max', 'self._working_peak=max',
                     'self._allocation_peak=max', 'return self._known(proposed)'):
            with self.subTest(line=line):
                bank, domain, ticket, state = self.binding()
                hit = []
                def action():
                    with self.assertRaises(KeyboardInterrupt):
                        bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')
                hit = interrupt_line(method, line, action)
                self.assertEqual(hit, [True])
                snap = bank.snapshot()
                self.assertTrue(snap['unknown'])
                self.assertEqual(snap['raw_reserved'], NAMED_RAW)
                self.assertEqual(snap['memory_transition']['old_quote'], (NAMED_RAW, 128*MIB))
                self.assertEqual(snap['memory_transition']['quote'], (SURVIVING_RAW, 128*MIB))
                self.assertIs(bank._domains[domain]['memory']['operation'], ticket)
                with self.assertRaisesRegex(ResourceError, 'resource_unknown'):
                    bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')

    def test_growth_known_return_and_guard_cleanup_interrupts_keep_conservative_evidence(self):
        method = AttemptResources.transition_callback_memory.__closure__[0].cell_contents
        targets = [(method, line) for line in (
            'self._memory_transition=evidence', 'self._raw=next_raw', 'self._working=next_working',
            'self._memory[expected.ticket]=proposed.row', 'self._callback_memory[expected.ticket]=proposed',
            'self._raw_peak=max', 'self._working_peak=max', 'self._allocation_peak=max')]
        targets += [(AttemptResources._known, 'return value'),
                    (AttemptResources.transition_callback_memory, 'if acquired:self._busy=False'),
                    (AttemptResources.transition_callback_memory, 'return answer')]
        for growth in ('working', 'raw'):
            for function, line in targets:
                with self.subTest(growth=growth, line=line):
                    bank, _, ticket, state = self.binding()
                    state = bank.transition_callback_memory(state, SURVIVING_RAW, 128*MIB, 'command')
                    if growth == 'working':
                        raw, working, phase = SURVIVING_RAW, 192*MIB, 'return'
                    else:
                        state = bank.transition_callback_memory(state, SURVIVING_RAW, 192*MIB, 'return')
                        raw, working, phase = SURVIVING_RAW+REQUEST_CANONICAL, 192*MIB, 'request'
                    before = bank.snapshot()
                    def action():
                        with self.assertRaises(KeyboardInterrupt):
                            bank.transition_callback_memory(state, raw, working, phase)
                    self.assertEqual(interrupt_line(function, line, action), [True])
                    snap = bank.snapshot()
                    self.assertTrue(snap['unknown'])
                    self.assertGreaterEqual(snap['raw_reserved'], before['raw_reserved'])
                    self.assertGreaterEqual(snap['working_reserved'], before['working_reserved'])
                    if line != 'self._memory_transition=evidence':
                        self.assertGreaterEqual(snap['raw_reserved'], raw)
                        self.assertGreaterEqual(snap['working_reserved'], working)
                        self.assertGreaterEqual(snap['known_allocation_peak'], raw+working)
                    with self.assertRaisesRegex(ResourceError, 'resource_unknown'):
                        bank.allocate(bank.local, 'standing', 1, 1)


class IntegratedPhases(unittest.TestCase):
    def test_two_actual_calls_retain_prior_results_requests_and_working(self):
        h = PhaseHarness(self)
        observed = []
        transition = h.resources.transition_callback_memory
        def witness(state, raw, working, phase):
            frame = inspect.currentframe()
            names = []
            while frame is not None:
                names.append(frame.f_code.co_name)
                frame = frame.f_back
            if phase == 'command':
                self.assertNotIn('_consume', names)
                self.assertFalse(h.rpc._busy)
                self.assertIsNone(h.rpc._rx)
                self.assertIsNone(h.producer._input)
                self.assertIsNone(h.producer._pending)
            if phase == 'return':
                self.assertNotIn('_text', names)
                observed.append(working)
            return transition(state, raw, working, phase)
        with h.inert_owner(), h.readiness(), patch.object(h.resources, 'transition_callback_memory', side_effect=witness):
            h.finish_input()
            for count in (1, 2):
                answer = h.client.call('search', fixture.ARGS)
                self.assertEqual((answer.stdout, answer.stderr), ('hello', 'bad\ufffd'))
                self.assertEqual(h.resources._memory[h.allocation][3], (129+64*count)*MIB)
                self.assertEqual(h.rpc._phases.snapshot()['retained_command_working'], 64*count*MIB)
                self.assertEqual(h.resources._memory[h.allocation][2], NAMED_RAW-PRODUCER_CANONICAL)
                self.assertEqual(h.resources._raw, sum(row[2] for row in h.resources._memory.values()))
                self.assertEqual(h.resources._working, sum(row[3] for row in h.resources._memory.values()))
            h.client.close()
        self.assertEqual(observed, [193*MIB, 257*MIB])
        self.assertEqual(len(h.pending_aliases), 2)
        self.assertEqual([r.stderr for r in h.result_aliases], ['bad\ufffd', 'bad\ufffd'])
        self.assertTrue(all(o._retired and o._digest is None for o in h.operations))
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertLessEqual(h.resources.snapshot()['raw_peak'], RAW)

    def test_incomplete_input_parks_at_actual_request_end_without_command_allocation(self):
        h = PhaseHarness(self)
        def finish_after_park():
            if h.parked:
                self.assertEqual(h.resources._memory[h.allocation][2], NAMED_RAW)
                self.assertEqual(h.service_endpoint.sent, [])
                self.assertNotIn('operation', h.resources._domains[h.resources.local]['memory'])
                h.finish_input()
                h.on_pump = None
        h.on_pump = finish_after_park
        with h.inert_owner(), h.readiness():
            self.assertEqual(h.client.call('search', fixture.ARGS).returncode, 17)
        self.assertEqual(h.parked, 1)

    def test_status_send_already_funds_next_canonicalization_and_ends_old_raw(self):
        h = PhaseHarness(self)
        send = fixture.Pipe.send
        seen = []
        def send_with_peer(pipe, record, flags):
            if pipe.parent and record[5] == wire.STATUS:
                self.assertIsNone(h.rpc._operation)
                self.assertTrue(h.operations[-1]._retired)
                self.assertIsNone(h.operations[-1]._digest)
                self.assertEqual(h.rpc._phases.state.phase, 'request')
                self.assertEqual(h.resources._memory[h.allocation][2], SURVIVING_RAW+REQUEST_CANONICAL)
                # Actual next canonical encoder executes before send returns;
                # no synthetic boolean stands in for its raw allocation.
                chunks = canonical_chunks(dict(operation='search', args=['x'*20000], body=None))
                try:
                    first = next(chunks)
                    second = next(chunks)
                    seen.append((len(first), len(second)))
                finally:
                    chunks.close()
            return send(pipe, record, flags)
        with h.inert_owner(), h.readiness(), patch.object(fixture.Pipe, 'send', send_with_peer):
            h.finish_input()
            h.client.call('search', fixture.ARGS)
        self.assertEqual(len(seen), 1)
        self.assertTrue(all(0 < n <= 8192 for n in seen[0]))

    def test_accumulated_working_eventually_refuses_unchanged_command_quote(self):
        h = PhaseHarness(self)
        with h.inert_owner(), h.readiness():
            h.finish_input()
            for _ in range(4):
                h.client.call('search', fixture.ARGS)
            self.assertEqual(h.resources._memory[h.allocation][3], 385*MIB)
            # Fifth call: retained385 + standing1 + parent64 + child64 already
            # exceeds original combined512MiB, before any fifth dispatch.
            with self.assertRaisesRegex(ValueError, '^operation_child_denied$'):
                h.client.call('search', fixture.ARGS)
        self.assertEqual(h.resources._memory[h.allocation][3], 385*MIB)
        self.assertEqual(h.next_operation, 5)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertLessEqual(h.resources.snapshot()['known_allocation_peak'], WORKING)

    def test_real_result_frame_refusal_keeps_active_callback_chunk_and_transfers_text(self):
        h = PhaseHarness(self)
        real_frame = h.rpc._result_frame
        seen = []
        def exhaust_at_frame(kind, *fields, data=None):
            if kind == wire.STDOUT:
                self.assertIs(data.obj, h.rpc._text_chunk)
                before = h.resources._memory[h.allocation]
                # Fault injection at the actual nested frame boundary. Consume
                # global capacity and existing DATA slack with real grants and
                # settled promises, forcing the existing _result_frame refusal.
                stream = h.rpc.stream_credit()
                room = h.rpc._stream_room()
                if room:
                    promise = stream.promise(room)
                    stream.known(promise, room)
                other = h.resources.bind(NativeProcessOwner())
                h.resources.stream_grant(other, h.resources.snapshot()['traffic_available'])
                result = real_frame(kind, *fields, data=data)
                self.assertFalse(result)
                self.assertTrue(h.operations[-1]._retired)
                self.assertEqual(bytes(data), b'hello')  # Active loan remains real.
                self.assertEqual(h.resources._memory[h.allocation][2], before[2])
                self.assertGreaterEqual(before[2], SURVIVING_RAW)
                self.assertEqual(h.resources._memory[h.allocation][3], before[3]+64*MIB)
                self.assertEqual(h.result_aliases[0].stdout, 'hello')
                seen.append(True)
                return result
            return real_frame(kind, *fields, data=data)
        with h.inert_owner(), h.readiness(), patch.object(h.rpc, '_result_frame', side_effect=exhaust_at_frame):
            h.finish_input()
            with self.assertRaisesRegex(RPCRefused, '^rpc_refused_3$'):
                h.client.call('search', fixture.ARGS)
            h.client.close()
        self.assertEqual(seen, [True])
        self.assertFalse(h.resources.snapshot()['unknown'])

    def test_cancellation_retires_only_closed_command_and_preserves_working(self):
        h = PhaseHarness(self)
        cancelled = []
        def cancel():
            if h.rpc._phase == 'command' and not cancelled:
                self.assertTrue(h.rpc.cancel())
                cancelled.append(True)
        h.on_pump = cancel
        with h.inert_owner(), h.readiness():
            h.finish_input()
            with self.assertRaisesRegex(RPCRefused, '^rpc_refused_4$'):
                h.client.call('search', fixture.ARGS)
            h.client.close()
        self.assertEqual(cancelled, [True])
        self.assertEqual(h.resources._memory[h.allocation][3], 193*MIB)
        self.assertTrue(h.operations[0]._retired)
        self.assertFalse(h.resources.snapshot()['unknown'])

    def test_public_return_transfer_denial_retains_command_and_suppresses_status(self):
        h = PhaseHarness(self)
        blocked = []
        def fill_working():
            if h.rpc._phase == 'phase_return' and not blocked:
                other = h.resources.bind(NativeProcessOwner())
                available = WORKING-h.resources._raw-h.resources._working
                held = h.resources.allocate(other, 'standing', 1, available-1)
                self.assertIsNotNone(held)
                blocked.append(h.resources._memory[h.allocation])
        h.on_pump = fill_working
        with h.inert_owner(), h.readiness():
            h.finish_input()
            with self.assertRaisesRegex(wire.RPCError, '^rpc_phase_capacity_denied$'):
                h.client.call('search', fixture.ARGS)
        self.assertEqual(h.resources._memory[h.allocation], blocked[0])
        self.assertIsNotNone(h.rpc._operation._parent)
        self.assertFalse(h.rpc._operation._retired)
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertFalse(any(record[5] == wire.STATUS for record in h.to_client))

    def test_public_replayed_phase_refuses_without_second_command_or_refund(self):
        h = PhaseHarness(self)
        replayed = []
        def replay():
            if h.rpc._phase == 'handoff':
                self.assertTrue(h.rpc.prepare_command_phase())
                replayed.append(h.resources._memory[h.allocation])
                h.rpc.prepare_command_phase()
        h.on_pump = replay
        with h.inert_owner(), h.readiness():
            h.finish_input()
            with self.assertRaisesRegex(wire.RPCError, '^rpc_phase_boundary$'):
                h.client.call('search', fixture.ARGS)
        self.assertEqual(h.resources._memory[h.allocation], replayed[0])
        self.assertEqual(h.service_endpoint.sent, [])
        self.assertTrue(h.resources.snapshot()['unknown'])

    def test_public_step_phase_and_final_cleanup_interruptions_suppress_status(self):
        targets = [(CallbackPhases.before_status, line) for line in (
            'self.retain_command_working()', 'self.rpc._retire_operation()', 'self._transition(')]
        targets += [(CallbackPhases._transition, 'self.state = state'),
                    (CommandOperation.retire, 'self.resources.release(self._parent)'),
                    (ProducerRPC.step, 'self._busy = False')]
        for function, line in targets:
            with self.subTest(function=function.__name__, line=line):
                h = PhaseHarness(self)
                hit = []
                def on_pump():
                    if h.rpc._phase == 'phase_return':
                        def action():
                            with self.assertRaises(KeyboardInterrupt):
                                h.rpc.step()
                        hit.extend(interrupt_line(function, line, action))
                        raise RuntimeError('stop inert client after interruption')
                h.on_pump = on_pump
                with h.inert_owner(), h.readiness():
                    h.finish_input()
                    with self.assertRaisesRegex(RuntimeError, '^stop inert client'):
                        h.client.call('search', fixture.ARGS)
                self.assertEqual(hit, [True])
                self.assertTrue(h.rpc.snapshot()['unknown'])
                self.assertTrue(h.resources.snapshot()['unknown'])
                self.assertIs(h.rpc._allocation, h.allocation)
                self.assertFalse(any(record[5] == wire.STATUS for record in h.to_client))
                with self.assertRaisesRegex(wire.RPCError, '^rpc_parent_unavailable$'):
                    h.rpc.step()


if __name__ == '__main__':
    unittest.main()
