"""Pure coordinator composition: real ledger, gate, service and profile-4 codec."""
import socket
import sys
import unittest
from unittest.mock import patch

import trial_sdk_command_operation as operations
from trial_sdk_command_operation import CommandOperation, OperationQuote, OperationError
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_command_service import CommandService, ChildSublease
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_peer import CREDENTIAL
from trial_sdk_ipc import Codec
import trial_sdk_command_service_wire as w
import trial_sdk_memory_command_job as jobs
from test_trial_sdk_command_service import Owner


ARGS = ('--repo', 'repo', '--task', 'task', '--run', 'run', '--tokens', '1024', '--', 'q')
QUOTE = OperationQuote(40000, 64*1024*1024, 65536, 64*1024*1024,
                       40000, 32*1024*1024, 1024*1024)


class Endpoint:
    """Only native-effect seam: bounded authored records, no socket or launch."""
    def __init__(self, service):
        self.service = service
        self.input = Codec('P2S', lambda n: True, lambda n: None, profile=4)
        self.output = Codec('S2P', lambda n: True, lambda n: None, profile=4)
        self.sent = []
        self.received = []
        self.block_kind = None
        self.blocked = []
        self.before_send = None
        self.before_receive = None

    def send(self, record, flags):
        kind = record[5]
        if self.block_kind == kind:
            self.blocked.append(bytes(record))
            raise BlockingIOError()
        if self.before_send:
            self.before_send(kind)
        frame = self.input.decode(record)
        try:
            self.service.accept(frame)
            with frame.body as body:
                self.sent.append((kind, bytes(body)))
        finally:
            frame.release()
        return len(record)

    def recvmsg_into(self, views, control, flags):
        pending = self.service.pending()
        if pending is None:
            raise BlockingIOError()
        kind, operation, size = pending
        if self.before_receive:
            self.before_receive(kind)
        with memoryview(bytearray(size)) as body:
            self.service.copy_reply(body)
            with views[0][:32 + size] as destination:
                frame = self.output.encode_into(destination, kind, operation, body)
            try:
                self.service.sent(frame)
                self.received.append((kind, bytes(body)))
                count = 32 + size
            finally:
                frame.release()
        return count, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                        CREDENTIAL.pack(123, 1000, 1000))], 0, None


class Harness:
    def __init__(self, test, out=b'hello', err=b'bad\xff', code=17, args=ARGS, reusable=False):
        self.test = test
        self.now = 1
        self.resources = AttemptResources()
        self.link = ServiceLink(self.resources, deadline_ns=10000,
                                standing_raw=5184, standing_working=1024*1024,
                                clock=lambda: self.now)
        self.owner = Owner(out, err, code)
        self.owner.cleanup_after_wait = self.cleanup_owner
        self.service = CommandService(
            FixedCommandPolicy('/fixed/cairn', '/fixed/socket', '/fixed/token',
                               'repo', 'task', 'run'), '/fixed', {}, ChildSublease(),
            QUOTE.preparation_raw, QUOTE.preparation_working,
            memoryview(bytearray(512)), clock=lambda: self.now)
        self.endpoint = Endpoint(self.service)
        transport = RecordSocket()
        transport._socket = self.endpoint
        transport._credentials = (123, 1000, 1000)
        transport._reserve = self.link._reserve_auxiliary
        transport._release = self.link._release_auxiliary
        transport._live = lambda: None
        transport._options = lambda: None
        self.link._connection = transport
        ready = self.link.credit(32)
        self.link.arm_receive(ready, 32)
        self.link.receive_step(lambda frame: None)
        ready.finish()
        self.operation = CommandOperation(self.link, QUOTE, operation=1, lease=1,
                                          deadline_ns=10000, clock=lambda: self.now)
        forward = operations.HookOperationForward(self.link) if reusable else self.operation.forward_hook
        self.gate = MemoryDispatchGate(operations.unsupported_native, forward, 10000)
        self.operation.bind_gate(self.gate)
        self.operation.prepare_hook('search', args)

    def cleanup_owner(self):
        self.owner.closed = self.owner.released = True
        self.owner.eof = dict(stdout=True, stderr=True)
        return dict(released=True, handles_detached=True, cleanup=())

    def step(self):
        with patch.object(jobs, 'NativeProcessOwner', return_value=self.owner), \
             patch('trial_sdk_memory_gate.time.monotonic_ns', return_value=self.now):
            answer = self.operation.step()
            if self.service.snapshot()['primary'] is not None:
                self.service.cleanup_step()
        return answer

    def until(self, predicate, maximum=500):
        for _ in range(maximum):
            if predicate():
                return
            self.step()
        self.test.fail('bounded pure model stalled: ' + repr(self.operation.snapshot()))

    def finish(self):
        self.until(lambda: self.operation.snapshot()['closed'])
        return self.operation.result()


class OperationTests(unittest.TestCase):
    def test_full_actual_core_sequence_debit_before_start_and_charged_result(self):
        h = Harness(self)
        def before(kind):
            if kind == w.START:
                self.assertEqual(h.gate.snapshot()['hook_searches'], 1)
                self.assertIsNotNone(h.gate.snapshot()['pending'])
                self.assertFalse(h.owner.launched)
            self.assertGreater(h.resources.snapshot()['traffic_reserved'], 0)
        h.endpoint.before_send = before
        value = h.finish()
        self.assertEqual((value.returncode, value.stdout, value.stderr), (17, 'hello', 'bad\ufffd'))
        self.assertEqual(h.gate.snapshot()['returned'], 1)
        self.assertEqual(h.gate.snapshot()['last_pending']['phase'], 'retired')
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 5184 + 40000)
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)
        self.assertEqual(h.resources.snapshot()['traffic_spent'],
                         sum(32 + len(body) for _, body in
                             h.endpoint.sent + h.endpoint.received) +
                         len(h.owner.input) + len(h.owner.data['stdout']) + len(h.owner.data['stderr']))
        kinds = [kind for kind, _ in h.endpoint.sent]
        self.assertEqual(kinds.count(w.START), 1)
        self.assertEqual(kinds[-1], w.RELEASE)
        del value
        h.operation.retire()
        self.assertEqual(h.resources.snapshot()['raw_reserved'], 5184)

    def test_eagain_start_keeps_identical_token_and_one_debit(self):
        h = Harness(self)
        h.endpoint.block_kind = w.START
        h.until(lambda: h.operation._tx_kind == w.START)
        token = h.link._tx['token']
        for _ in range(3):
            self.assertFalse(h.step())
            self.assertIs(h.link._tx['token'], token)
        self.assertFalse(h.owner.launched)
        self.assertEqual(h.gate.snapshot()['hook_searches'], 1)
        self.assertEqual(h.endpoint.blocked, [h.endpoint.blocked[0]] * 3)
        h.endpoint.block_kind = None
        h.finish()

    def test_maximum_last_text_has_precommitted_terminal_cursor(self):
        h = Harness(self, out=b'x'*512, err=b'')
        def before(kind):
            if kind == w.DELIVER:
                self.assertIsNotNone(h.operation._future)
                self.assertEqual(h.operation._b.snapshot()['burst']['maxima'], (41, 41, 41))
                self.assertEqual(h.operation._a.snapshot()['pending'], 2)
        h.endpoint.before_send = before
        self.assertEqual(h.finish().stdout, 'x'*512)

    def test_output_above_document_limit_keeps_stdout_and_replacement_stderr(self):
        out_size, err_size = 128*1024 + 7, 44*1024
        h = Harness(self, out=b'a'*out_size, err=b'\xff'*err_size)
        h.until(lambda: h.operation.snapshot()['phase'] == 'result', maximum=4000)
        self.assertTrue(all(decoder._parts for decoder in h.operation._decoders))
        self.assertTrue(all(type(part) is str for decoder in h.operation._decoders
                            for part in decoder._parts))
        value = h.finish()
        self.assertEqual((value.stdout_bytes, value.stderr_bytes), (out_size, err_size))
        self.assertEqual((value.stdout, value.stderr), ('a'*out_size, '\ufffd'*err_size))
        self.assertEqual(h.link.transcript.snapshot()['wire_sizes'], (out_size, 3*err_size))
        self.assertLessEqual(h.resources.snapshot()['raw_peak'], 128*1024)
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_second_successful_operation_uses_same_gate_ledger_and_increasing_grants(self):
        h = Harness(self, out=b'first', err=b'', reusable=True)
        self.assertEqual(h.finish().stdout, 'first')
        h.operation.retire()
        first_spent = h.resources.snapshot()['traffic_spent']
        h.owner = Owner(b'second', b'', 0)
        h.owner.cleanup_after_wait = h.cleanup_owner
        h.operation = CommandOperation(h.link, QUOTE, operation=2, lease=2,
                                       deadline_ns=10000, clock=lambda: h.now)
        h.operation.bind_gate(h.gate)
        h.operation.prepare_hook('search', ARGS)
        self.assertEqual(h.finish().stdout, 'second')
        grants = [w.STRUCTS[kind].unpack(body)[0] for kind, body in h.endpoint.sent
                  if kind in (w.TURN, w.DELIVER)]
        self.assertEqual(grants, sorted(set(grants)))
        self.assertEqual(h.gate.snapshot()['hook_searches'], 2)
        self.assertEqual(h.gate.snapshot()['dispatches'], 2)
        self.assertGreater(h.resources.snapshot()['traffic_spent'], first_spent)
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_cancel_crosses_each_active_stage_and_preserves_ack_before_release(self):
        stages = ('prepare', 'request', 'prepared_wait', 'prepared', 'start_wait',
                  'running', 'output', 'result')
        for phase in stages:
            with self.subTest(phase=phase):
                h = Harness(self, out=b'x'*1024, err=b'')
                h.until(lambda: h.operation.snapshot()['phase'] == phase)
                h.operation.cancel()
                result = h.finish()
                self.assertEqual(result['refusal'], 6)
                self.assertTrue(result['cancelled'])
                kinds = [kind for kind, _ in h.endpoint.received]
                ack = max(i for i, (kind, body) in enumerate(h.endpoint.received)
                          if kind == w.REFUSE and body == b'\x06')
                self.assertLess(ack, kinds.index(w.RELEASED))
                self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)
                self.assertEqual(h.gate.snapshot()['hook_searches'],
                                 int(h.operation.snapshot()['forwarded']))

    def test_cancel_crosses_maximum_last_and_nonlast_text(self):
        for size in (512, 1024):
            with self.subTest(size=size):
                h = Harness(self, out=b'x'*size, err=b'')
                h.until(lambda: h.operation._tx_kind == w.DELIVER)
                h.step()  # DELIVER sent; its full text has not been received.
                h.operation.cancel()
                h.step()  # CANCEL queued only after ACK cursor exists.
                self.assertIsNotNone(h.operation._future)
                h.step()
                self.assertTrue(h.operation.snapshot()['cancel_sent'])
                self.assertEqual(h.finish()['refusal'], 6)

    def test_cancel_crosses_turn_reports_and_empty_output_terminal_chain(self):
        for output in (b'prefix', b''):
            with self.subTest(output=output):
                h = Harness(self, out=output, err=b'')
                h.until(lambda: h.operation._tx_kind == w.TURN)
                h.step()  # TURN is accepted; PROGRESS must precede crossed ACK.
                self.assertIsNotNone(h.operation._data)
                h.operation.cancel()
                result = h.finish()
                self.assertEqual(result['refusal'], 6)
                self.assertIsNone(h.operation._data)
                self.assertEqual(h.link.transcript.snapshot()['raw_counts'][1], len(output))
                self.assertEqual(h.gate.snapshot()['hook_searches'], 1)

    def test_cancel_crosses_already_automatic_output_and_status(self):
        for out, pending_kind in ((b'', w.OUTPUT), (b'', w.STATUS),
                                  (b'payload', w.OUTPUT)):
            with self.subTest(out=out, pending=pending_kind):
                h = Harness(self, out=out, err=b'')
                h.until(lambda: h.service.pending() is not None and
                        h.service.pending()[0] == pending_kind)
                h.operation.cancel()
                self.assertEqual(h.finish()['refusal'], 6)
                self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_cancel_during_release_is_already_terminal_and_does_not_send_cancel(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['phase'] == 'releasing')
        self.assertFalse(h.operation.cancel())
        self.assertEqual(h.finish().stdout, 'hello')
        self.assertNotIn(w.CANCEL, [kind for kind, _ in h.endpoint.sent])

    def test_known_preparation_refusal_has_no_gate_debit(self):
        h = Harness(self, args=('--', 'bad scope'))
        result = h.finish()
        self.assertEqual(result['refusal'], 3)
        self.assertEqual(h.gate.snapshot()['dispatches'], 0)
        self.assertFalse(h.owner.launched)

    def test_unknown_progress_retains_allocation_and_exact_gate_failure(self):
        h = Harness(self)
        read = h.owner.read_into
        def unknown_read(name, view):
            return 513 if name == 'stderr' else read(name, view)
        h.owner.read_into = unknown_read
        h.until(lambda: h.operation.snapshot()['unknown'])
        self.assertTrue(h.operation.snapshot()['parent_allocation'])
        self.assertTrue(h.operation.snapshot()['child_allocation'])
        self.assertTrue(h.operation.snapshot()['data_ticket_held'])
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.gate.snapshot()['last_pending']['phase'], 'unknown')
        self.assertEqual(h.resources.snapshot()['traffic_spent'],
                         sum(32 + len(body) for _, body in
                             h.endpoint.sent + h.endpoint.received) + 5)
        with self.assertRaises(OperationError):
            h.operation.step()

    def test_final_zero_offer_holds_positive_ticket_without_reviving_ceiling(self):
        h = Harness(self, out=b'12345678', err=b'')
        h.until(lambda: h.operation.snapshot()['phase'] == 'running')
        # A different original owner has a still-held ticket. Its subsequent
        # known zero use returns room, but cannot revive this CLI final ceiling.
        available = h.resources.snapshot()['traffic_available']
        other = h.resources.grant(h.resources.local, 'data', available - 406 - 8)
        h.until(lambda: h.operation._turn == 1 and h.operation._data is None)
        self.assertEqual(h.link.transcript.snapshot()['final_remaining'], 0)
        h.resources.settle(other, 0)
        h.step()
        self.assertEqual(h.operation.snapshot()['offered'], 0)
        self.assertEqual(h.resources.grant_info(h.operation._data)['maximum'], 1)
        self.assertEqual(h.finish().stdout, '12345678')
        turns = [w.STRUCTS[w.TURN].unpack(body) for kind, body in h.endpoint.sent if kind == w.TURN]
        self.assertEqual([row[2:] for row in turns], [(8, 1), (0, 1)])

    def test_known_refusal_progress_before_cancel_preserves_prefix_and_prior_reason(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['phase'] == 'running')
        available = h.resources.snapshot()['traffic_available']
        prior = h.resources.grant(h.resources.local, 'data', available - 406)
        h.resources.settle(prior, available - 406)
        h.until(lambda: h.link.transcript.snapshot()['progress_outcome'] == 2)
        self.assertIsNone(h.operation._data)
        h.operation.cancel()
        result = h.finish()
        self.assertEqual((result['refusal'], result['prior_refusal']), (6, 2))
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertEqual(h.gate.snapshot()['last_pending']['phase'], 'retired')

    def test_control_exhaustion_uses_emergency_cancel_without_issuing_turn(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['phase'] == 'running')
        available = h.resources.snapshot()['traffic_available']
        prior = h.resources.grant(h.resources.local, 'data', available - 405)
        h.resources.settle(prior, available - 405)
        result = h.finish()
        self.assertEqual(result['budget_refusal'], 'operation_control_exhausted')
        self.assertEqual(result['refusal'], 6)
        self.assertNotIn(w.TURN, [kind for kind, _ in h.endpoint.sent])
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertFalse(h.operation.snapshot()['child_allocation'])
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_small_delivery_credit_then_exhaustion_preserves_text_prefix_and_cancels(self):
        h = Harness(self, out=b'x'*512, err=b'')
        h.until(lambda: h.operation.snapshot()['phase'] == 'output')
        available = h.resources.snapshot()['traffic_available']
        prior = h.resources.grant(h.resources.local, 'data', available - 150)
        h.resources.settle(prior, available - 150)
        result = h.finish()
        self.assertEqual(result['budget_refusal'], 'operation_control_exhausted')
        deliveries = [w.STRUCTS[w.DELIVER].unpack(body) for kind, body in h.endpoint.sent
                      if kind == w.DELIVER]
        self.assertEqual([row[2] for row in deliveries], [75])
        self.assertEqual(h.link.transcript.snapshot()['wire_counts'], (75, 0))
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_preparation_capacity_refusal_never_creates_child_operation_or_gate_debit(self):
        h = Harness(self)
        available = h.resources.snapshot()['traffic_available']
        prior = h.resources.grant(h.resources.local, 'data', available - 96)
        h.resources.settle(prior, available - 96)
        result = h.finish()
        self.assertIsNone(result['refusal'])
        self.assertEqual(result['budget_refusal'], 'operation_control_exhausted')
        self.assertIsNone(h.service.snapshot()['budgets']['lease'])
        self.assertEqual(h.gate.snapshot()['dispatches'], 0)
        self.assertEqual(h.endpoint.sent, [])
        self.assertFalse(h.resources.snapshot()['unknown'])

    def test_last_deliver_refusal_uses_precommitted_terminal_for_released(self):
        h = Harness(self, out=b'x'*512, err=b'')
        h.until(lambda: h.operation._tx_kind == w.DELIVER)
        # The real core's deadline refusal before text is the branch under test.
        # Parent clock remains before its deadline; this is a pure clock seam.
        h.service._clock = lambda: 10000
        result = h.finish()
        self.assertEqual(result['refusal'], 1)
        self.assertEqual(h.resources.snapshot()['traffic_reserved'], 0)

    def test_receive_eagain_retains_exact_rx_loan_and_does_not_poll(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['phase'] == 'prepared_wait')
        receive = h.endpoint.recvmsg_into
        def pending(*args):
            raise BlockingIOError()
        h.endpoint.recvmsg_into = pending
        self.assertFalse(h.step())
        loan = h.link._rx['hold']
        before = h.resources.snapshot()
        self.assertFalse(h.step())
        self.assertIs(h.link._rx['hold'], loan)
        self.assertEqual(h.resources.snapshot(), before)
        h.endpoint.recvmsg_into = receive
        h.finish()

    def test_deadline_does_not_report_completed_gate_or_refund_live_aliases(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['forwarded'])
        before = h.resources.snapshot()
        h.now = 10000
        with self.assertRaisesRegex(OperationError, '^operation_deadline$'):
            h.step()
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.resources.snapshot()['raw_reserved'], before['raw_reserved'])
        self.assertEqual(h.gate.snapshot()['last_pending']['phase'], 'unknown')

    def test_gate_handle_return_interrupt_keeps_unsettled_dispatch_without_guessing_handle(self):
        h = Harness(self)
        h.until(lambda: h.operation.snapshot()['phase'] == 'prepared')
        hit = []
        def interrupt(frame, event, arg):
            if (event == 'return' and frame.f_code.co_name == '_dispatch' and
                    frame.f_locals.get('self') is h.gate and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return interrupt
        previous = sys.gettrace()
        try:
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                h.step()
        finally:
            sys.settrace(previous)
        self.assertEqual(hit, [True])
        self.assertTrue(h.operation.snapshot()['unknown'])
        self.assertFalse(h.operation.snapshot()['pending_gate_handle'])
        self.assertIsNotNone(h.gate.snapshot()['pending'])
        self.assertEqual(h.gate.snapshot()['hook_searches'], 1)
        self.assertFalse(h.owner.launched)
        self.assertTrue(h.resources.snapshot()['unknown'])

    def test_progress_settlement_return_interrupt_preserves_count_without_replay(self):
        h = Harness(self)
        h.until(lambda: h.operation._tx_kind == w.TURN)
        h.step()
        ticket = h.operation._data
        hit = []
        def interrupt(frame, event, arg):
            if (event == 'return' and frame.f_code.co_name == 'guarded' and
                    frame.f_locals.get('self') is h.resources and
                    frame.f_locals.get('args', (None,))[0] is ticket and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return interrupt
        previous = sys.gettrace()
        try:
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                h.step()
        finally:
            sys.settrace(previous)
        self.assertEqual(hit, [True])
        self.assertEqual(h.resources.snapshot()['traffic_spent'],
                         sum(32 + len(body) for _, body in
                             h.endpoint.sent + h.endpoint.received) + 9)
        self.assertTrue(h.operation.snapshot()['unknown'])
        self.assertEqual(h.gate.snapshot()['last_pending']['phase'], 'unknown')
        with self.assertRaises(OperationError):
            h.step()


if __name__ == '__main__':
    unittest.main()
