"""Paired RPC/service composition with real ledgers/codecs and inert OS edges."""
from collections import deque
import select
import socket
import sys
import unittest
from unittest.mock import patch

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_operation import CommandOperation, OperationQuote, HookOperationForward, unsupported_native
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_command_service import CommandService, ChildSublease
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_engine_memory_rpc import ProducerRPC, RPCQuote
from trial_sdk_engine_memory_rpc_client import ProducerClient, ClientQuote, RPCRefused
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_incoming import canonical_chunks, canonical_size_sha256
from trial_sdk_peer import CREDENTIAL
import trial_sdk_memory_command_job as jobs
import trial_sdk_native_process_owner as owners
import trial_sdk_engine_memory_rpc_wire as w
from test_trial_sdk_command_operation import Endpoint, ARGS
from test_trial_sdk_command_service import Owner
from test_trial_sdk_command_input import process_boundaries
from test_trial_sdk_producer_driver import callback_event
from test_trial_sdk_io_receipts import after_released_loan


OP_QUOTE = OperationQuote(32768, 64*1024*1024, 41548, 64*1024*1024,
                          40000, 32*1024*1024, 1024*1024)
RPC_QUOTE = RPCQuote(36928, 128*1024*1024, ClientQuote(34848, 64*1024*1024))


class Pipe:
    def __init__(self, harness, parent):
        self.h, self.parent = harness, parent

    def send(self, record, flags):
        kind = record[5]
        if self.parent and kind == w.REQUEST_CREDIT:
            credit = self.h.rpc.stream_credit()
            state = credit.snapshot()
            self.h.test.assertGreaterEqual(state['future_reserved'], 32)
            if self.h.block_credit and not self.h.blocked:
                self.h.blocked = True
                self.h.blocked_frame = bytes(record)
                raise BlockingIOError()
            if self.h.blocked_frame is not None:
                self.h.test.assertEqual(bytes(record), self.h.blocked_frame)
                self.h.blocked_frame = None
        if not self.parent and kind in (w.REQUEST, w.REQUEST_END):
            state = self.h.rpc.stream_credit().snapshot()
            self.h.test.assertTrue(state['future_reserved'] >= len(record) or
                                   state['promised'] >= len(record))
        target = self.h.to_client if self.parent else self.h.to_parent
        self.h.test.assertFalse(target, 'one bounded record per scripted edge')
        target.append(bytes(record))
        self.h.rpc_wire += len(record)
        if self.parent and kind == w.REQUEST_CREDIT and self.h.on_credit_delivery is not None:
            self.h.on_credit_delivery()
        return len(record)

    def recvmsg_into(self, views, control, flags):
        source = self.h.to_parent if self.parent else self.h.to_client
        if not source:
            if not self.parent and self.h.parent_closed:
                return 0, [], 0, None
            raise BlockingIOError()
        record = source.popleft()
        self.h.test.assertLessEqual(len(record), len(views[0]))
        views[0][:len(record)] = record
        return len(record), [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                              CREDENTIAL.pack(123, 1000, 1000))], 0, None

    def fileno(self): return 124 if self.parent else 123

    def detach(self):
        if self.parent:
            self.h.parent_closed = True
        return self.fileno()


class Harness:
    def __init__(self, test, stdout=b'hello', stderr=b'bad\xff', code=17):
        self.test = test
        self.now, self.steps, self.next_operation = 1, 0, 1
        self.stdout, self.stderr, self.code = stdout, stderr, code
        self.resources = AttemptResources()
        self.owner = NativeProcessOwner()
        self.domain = self.resources.bind(self.owner)
        self.allocation = self.resources.allocate(self.domain, 'operation', RPC_QUOTE.raw, RPC_QUOTE.working)
        self.link = ServiceLink(self.resources, deadline_ns=5_000_000_001, standing_raw=5184,
                                standing_working=1024*1024, clock=lambda: self.now)
        self.service = CommandService(FixedCommandPolicy('/fixed/cairn', '/fixed/socket',
            '/fixed/token', 'repo', 'task', 'run'), '/fixed', {}, ChildSublease(),
            OP_QUOTE.preparation_raw, OP_QUOTE.preparation_working,
            memoryview(bytearray(512)), clock=lambda: self.now)
        self.service_endpoint = Endpoint(self.service)
        self.link._connection = self.transport(self.link, self.service_endpoint)
        ready = self.link.credit(32)
        self.link.arm_receive(ready, 32)
        self.link.receive_step(lambda frame: None)
        ready.finish()
        forward = HookOperationForward(self.link)
        self.gate = MemoryDispatchGate(unsupported_native, forward, 5_000_000_001)
        with patch('time.monotonic_ns', return_value=1):
            _, event = callback_event()
        self.rpc = ProducerRPC(self.resources, self.owner, self.domain, self.allocation,
            gate=self.gate, link=self.link, event=event, quote=RPC_QUOTE, clock=lambda: self.now)
        self.client = ProducerClient(5_000_000_001, RPC_QUOTE.client, clock=lambda: self.now)
        self.to_parent, self.to_client = deque(), deque()
        self.parent_closed = self.block_credit = self.blocked = False
        self.blocked_frame = None
        self.rpc_wire = self.stdio_bytes = self.other_pump_steps = 0
        self.on_pump = self.before_handoff = None
        self.on_credit_delivery = None
        self.rpc._connection = self.transport(self.rpc, Pipe(self, True))
        self.client._connection = self.transport(self.client, Pipe(self, False))

    def transport(self, receiver, endpoint):
        transport = RecordSocket()
        transport._socket, transport._credentials = endpoint, (123, 1000, 1000)
        transport._reserve, transport._release = receiver._reserve_auxiliary, receiver._release_auxiliary
        transport._live = transport._options = lambda: None
        return transport

    def cli_owner(self, *, allow_stdin_epipe):
        self.test.assertTrue(allow_stdin_epipe)
        owner = Owner(self.stdout, self.stderr, self.code)
        def cleanup():
            owner.closed = owner.released = True
            owner.eof = dict(stdout=True, stderr=True)
            return dict(released=True, handles_detached=True, cleanup=())
        owner.cleanup_after_wait = cleanup
        return owner

    def handoff(self):
        pending = self.rpc.pending_request()
        operation = CommandOperation(self.link, OP_QUOTE, operation=self.next_operation,
            lease=self.next_operation, deadline_ns=pending.deadline_ns, clock=lambda: self.now)
        self.next_operation += 1
        operation.bind_gate(self.gate)
        request = pending.request
        if self.before_handoff is not None:
            request = self.before_handoff(request)
        operation.prepare_hook(request.operation, request.args, request.body)
        self.rpc.start_operation(operation)

    def pump(self):
        self.steps += 1
        self.test.assertLess(self.steps, 25000, self.rpc.snapshot())
        self.other_pump_steps += 1  # Outer SDK/source work remains independently scheduled.
        if self.on_pump is not None:
            self.on_pump()
        if self.rpc.snapshot()['phase'] == 'handoff':
            self.handoff()
        with patch.object(jobs, 'NativeProcessOwner', side_effect=self.cli_owner), \
             patch('trial_sdk_memory_gate.time.monotonic_ns', return_value=self.now):
            self.rpc.step()
            if self.service.snapshot()['primary'] is not None:
                self.service.cleanup_step()

    def readiness(self):
        h = self
        class Poll:
            def register(self, fd, events): pass
            def poll(self, timeout):
                if not h.parent_closed:
                    h.pump()
                return [(123, select.POLLIN | select.POLLOUT |
                          (select.POLLHUP if h.parent_closed else 0))]
        return patch('select.poll', Poll)


class ParentTests(unittest.TestCase):
    def test_peer_reply_during_credit_send_is_already_funded_before_promotion(self):
        h = Harness(self)
        document = dict(operation='search', args=list(ARGS), body=None)
        size, digest = canonical_size_sha256(document)
        h.client._send(w.BEGIN, 1, 2_000_000_001, size, bytes.fromhex(digest))
        h.rpc.step()
        h.rpc.step()
        seen = []
        def reply_before_send_returns():
            self.assertEqual(h.client._receive(), w.REQUEST_CREDIT)
            chunk = next(canonical_chunks(document))
            count = min(len(chunk), h.client.transcript.permit)
            h.client._send(w.REQUEST, 1, data=chunk[:count])
            state = h.rpc.stream_credit().snapshot()
            self.assertEqual(state['pending'], 1)  # CREDIT still inside send().
            self.assertGreaterEqual(state['future_reserved'], count + 32)
            seen.append(count)
        h.on_credit_delivery = reply_before_send_returns
        h.rpc.step()
        self.assertEqual(len(seen), 1)
        self.assertEqual(h.rpc.stream_credit().snapshot()['future_reserved'], 0)
        self.assertTrue(h.rpc.snapshot()['receive_pending'])
        h.rpc.step()
        self.assertEqual(h.rpc.transcript.request_count, seen[0])
        self.assertEqual(h.rpc.stream_credit().snapshot()['known'], 34 + 32 + seen[0])

    def test_large_actual_command_output_preserves_replacement_wire_counts(self):
        h = Harness(self, stdout=b'x' * 140000, stderr=b'\xff' * 60000)
        with h.readiness(), patch('os.close'):
            answer = h.client.call('search', ARGS)
            h.client.close()
        self.assertEqual((answer.stdout_bytes, answer.stderr_bytes), (140000, 60000))
        self.assertEqual((answer.stdout, answer.stderr), ('x' * 140000, '\ufffd' * 60000))
        self.assertEqual(h.client.transcript.wire_sizes, (140000, 180000))
        self.assertFalse(h.resources.snapshot()['unknown'])

    def test_known_traffic_shortage_uses_preheld_refusal_and_terminal_handshake(self):
        h = Harness(self)
        other_owner = NativeProcessOwner()
        other = h.resources.bind(other_owner)
        held = h.resources.stream_grant(other, h.resources.snapshot()['traffic_available'])
        self.assertIsNotNone(held)
        with h.readiness(), patch('os.close'):
            with self.assertRaisesRegex(RPCRefused, '^rpc_refused_3$'):
                h.client.call('search', ARGS)
            h.client.close()
        self.assertTrue(h.rpc.snapshot()['closed'])
        self.assertFalse(h.resources.snapshot()['unknown'])
        self.assertEqual(h.service_endpoint.sent, [])

    def test_original_call_deadline_expires_before_source_handoff_without_reset(self):
        h = Harness(self)
        def expire():
            if h.rpc.snapshot()['phase'] == 'handoff':
                self.assertEqual(h.rpc.pending_request().deadline_ns, 2_000_000_001)
                h.now = 2_000_000_001
                h.rpc.step()
        h.on_pump = expire
        with h.readiness():
            with self.assertRaisesRegex(w.RPCError, '^rpc_deadline$'):
                h.client.call('search', ARGS, timeout=2)
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertEqual(h.rpc.snapshot()['deadline_ns'], 2_000_000_001)
        self.assertEqual(h.service_endpoint.sent, [])

    def test_real_command_two_calls_preserve_status_counts_and_whole_stream_lifetime(self):
        h = Harness(self)
        stream = h.rpc.stream_credit()
        with h.readiness(), patch('os.close'):
            for _ in range(2):
                answer = h.client.call('search', ARGS, timeout=2)
                self.assertEqual((answer.returncode, answer.stdout, answer.stderr,
                                  answer.stdout_bytes, answer.stderr_bytes),
                                 (17, 'hello', 'bad\ufffd', 5, 4))
                self.assertIs(h.rpc.stream_credit(), stream)
                self.assertFalse(stream.snapshot()['closed'])
                self.assertNotIn('operation', h.resources.snapshot()['domains'][0]['memory'])
            h.client.close()
        self.assertTrue(h.rpc.snapshot()['closed'])
        self.assertIs(h.rpc.stream_credit(), stream)
        with self.assertRaisesRegex(w.RPCError, '^rpc_retirement_pending$'):
            h.rpc.retire()
        h.owner.cleanup_unlaunched()
        h.rpc.retire()
        self.assertTrue(stream.snapshot()['closed'])
        # Parent observes each profile5 transfer once, separately from actual
        # service records and raw CLI input/output. Neither matching receive nor
        # META's original counts add a second charge for the same physical edge.
        service_wire = sum(32 + len(body) for _, body in h.service_endpoint.sent + h.service_endpoint.received)
        self.assertEqual(h.resources.snapshot()['traffic_spent'], h.rpc_wire + service_wire + 2*(5+4))

    def test_tx_eagain_and_stderr_share_data_ticket_without_spending_protected_reply(self):
        h = Harness(self)
        h.block_credit = True
        stream = h.rpc.stream_credit()
        ticket = stream.ticket
        seen = []
        def diagnostic():
            if not h.blocked or seen:
                return
            before = stream.snapshot()
            self.assertEqual(before['pending'], 1)
            self.assertGreater(before['future_reserved'], 0)
            promise = stream.promise_stdio(4)
            self.assertEqual(stream.snapshot()['pending'], 2)
            self.assertEqual(stream.snapshot()['future_reserved'], before['future_reserved'])
            prior = h.owner.io_receipt()
            with memoryview(bytearray(4)) as slab:
                count = h.owner.read_into('stderr', slab)
            receipt = h.owner.io_receipt()
            self.assertEqual(receipt[1:], ('stderr', 4, True, 3, False))
            self.assertTrue(prior is None or receipt[0] > prior[0])
            stream.known(promise, count)
            h.stdio_bytes += count
            seen.append(True)
        h.on_pump = diagnostic
        with process_boundaries(), patch.object(owners.os, 'readv', return_value=3), h.readiness():
            h.owner.start(('/fixed-producer',), '/fixed', {})
            h.owner.close_stdin()  # Real finite-input handoff, with inert syscall boundaries.
            answer = h.client.call('search', ARGS)
        self.assertEqual(answer.returncode, 17)
        self.assertEqual(seen, [True])
        self.assertIs(h.rpc.stream_credit().ticket, ticket)
        self.assertGreater(h.other_pump_steps, 1)
        self.assertEqual(stream.snapshot()['known'], h.rpc_wire - 76 + h.stdio_bytes)
        self.assertFalse(h.resources.snapshot()['unknown'])

    def test_stdio_cleanup_unknown_freezes_rpc_future_and_retains_both_known_prefixes(self):
        h = Harness(self)
        h.block_credit = True
        stream = h.rpc.stream_credit()
        seen = []
        def diagnostic():
            if not h.blocked or seen:
                return
            promise = stream.promise_stdio(4)
            def action():
                with self.assertRaises(KeyboardInterrupt):
                    with memoryview(bytearray(4)) as slab:
                        h.owner.read_into('stderr', slab)
            self.assertTrue(after_released_loan(NativeProcessOwner.read_into, action, KeyboardInterrupt()))
            receipt = h.owner.io_receipt()
            self.assertEqual(receipt[1:], ('stderr', 4, True, 3, True))
            stream.unknown_prefix(promise, receipt[4])
            seen.append(True)
        h.on_pump = diagnostic
        with process_boundaries(), patch.object(owners.os, 'readv', return_value=3), h.readiness():
            h.owner.start(('/fixed-producer',), '/fixed', {})
            h.owner.close_stdin()
            with self.assertRaisesRegex(ValueError, '^resource_unknown$'):
                h.client.call('search', ARGS)
        self.assertEqual(seen, [True])
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertGreater(stream.snapshot()['future_reserved'], 0)
        self.assertEqual(h.resources.snapshot()['traffic_spent'], 32 + 76 + 3)
        self.assertTrue(h.client.snapshot()['failed'])

    def test_wrong_prepared_request_is_not_dispatched(self):
        from trial_sdk_memory_gate import HookRequest
        h = Harness(self)
        h.before_handoff = lambda request: HookRequest(request.operation, request.args, 'wrong body')
        with h.readiness():
            with self.assertRaisesRegex(w.RPCError, '^rpc_operation_request$'):
                h.client.call('search', ARGS)
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertFalse(h.rpc._operation.snapshot()['dispatch_attempted'])
        self.assertTrue(h.rpc._operation.snapshot()['parent_allocation'])
        self.assertEqual(h.service_endpoint.sent, [])

    def test_zero_call_close_and_known_policy_refusal_have_real_ack_then_eof(self):
        for refuse in (False, True):
            with self.subTest(refuse=refuse):
                h = Harness(self)
                if refuse:
                    def deny():
                        if h.rpc.snapshot()['phase'] == 'handoff':
                            h.rpc.cancel(2)
                            # Fixed source declines handoff and lets bounded
                            # protocol cleanup run; no fake CommandOperation.
                            h.rpc.step()
                    h.on_pump = deny
                with h.readiness(), patch('os.close'):
                    if refuse:
                        with self.assertRaisesRegex(RPCRefused, '^rpc_refused_2$'):
                            h.client.call('search', ARGS)
                    h.client.close()
                self.assertTrue(h.client.snapshot()['connection']['terminal_eof'])
                self.assertTrue(h.rpc.snapshot()['closed'])
                self.assertEqual(h.service_endpoint.sent, [])


if __name__ == '__main__':
    unittest.main()
