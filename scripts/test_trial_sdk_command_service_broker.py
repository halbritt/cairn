"""Original-ledger and real codec checks; socket boundaries are inert here."""
import unittest
import hashlib
import socket
import sys

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_service_broker import WireCredit, ServiceLinkError, ServiceLink
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_peer import CREDENTIAL
from trial_sdk_ipc import Codec
import trial_sdk_command_service_wire as w


class CreditTests(unittest.TestCase):
    def test_burst_bounds_and_identity_refuse_before_commitment_changes(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 544)
        for maxima in ((), (31,), (545,), (True,), (544,32), (32,)*5, [32]):
            with self.assertRaisesRegex(ServiceLinkError, '^wire_burst_spec$'):
                credit.burst(maxima)
        self.assertEqual(credit.snapshot()['promised'], 0)
        burst = credit.burst((56,224,40,33))
        with self.assertRaisesRegex(ServiceLinkError, '^wire_burst_unobserved$'):
            credit.end_burst(burst)
        other = WireCredit(resources, resources.bind(__import__(
            'trial_sdk_native_process_owner').NativeProcessOwner()), 32)
        with self.assertRaisesRegex(ServiceLinkError, '^wire_burst_identity$'):
            other.burst_known(burst, 32)
        self.assertEqual(credit.snapshot()['promised'], 353)
        self.assertFalse(resources.snapshot()['unknown'])

    def test_burst_early_close_return_interruption_cannot_refund_unused_commitment(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 353)
        burst = credit.burst((56,224,40,33))
        credit.burst_known(burst, 56)
        hit = []
        def trace(frame, event, arg):
            if (event == 'line' and frame.f_code.co_name == 'guarded' and
                    frame.f_locals.get('self') is credit and
                    frame.f_locals.get('acquired') and not credit._busy and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return trace
        old = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt): credit.end_burst(burst)
        finally: sys.settrace(old)
        self.assertEqual(hit, [True])
        self.assertTrue(resources.snapshot()['unknown'])
        self.assertEqual((resources.snapshot()['traffic_spent'],
                          resources.snapshot()['traffic_reserved']), (56,297))

    def test_uncertain_second_burst_frame_keeps_all_prior_known_bytes_and_remainder(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 406)
        burst = credit.burst((56, 224, 40, 33))
        credit.known(credit.promise(53), 53)
        credit.burst_known(burst, 56)
        credit.burst_unknown_prefix(burst, 17)
        state = resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (126, 280))
        self.assertEqual(credit.snapshot()['burst']['index'], 1)
        self.assertEqual(credit.snapshot()['burst']['known'], 73)
        self.assertTrue(state['unknown'])
        with self.assertRaises(ServiceLinkError): credit.end_burst(burst)

    def test_reply_burst_is_precommitted_once_and_partial_receipts_cannot_refund(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 406)
        burst = credit.burst((56, 224, 40, 33))
        sent = credit.promise(53)
        credit.known(sent, 53)
        credit.burst_known(burst, 56)
        credit.burst_known(burst, 224)
        with self.assertRaisesRegex(ServiceLinkError, '^wire_receipt_pending$'):
            credit.finish()
        self.assertEqual(credit.snapshot()['known'], 333)
        credit.burst_known(burst, 40)
        credit.end_burst(burst)  # Validated no CANCEL: fourth reply not produced.
        credit.finish()
        state = resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (373, 0))
        with self.assertRaises(ServiceLinkError): credit.burst_known(burst, 33)

    def test_freeze_is_idempotent_and_never_changes_known_spend_or_live_tickets(self):
        resources = AttemptResources()
        memory = resources.allocate(resources.local, 'standing', 128, 256)
        credit = WireCredit(resources, resources.local, 64)
        credit.known(credit.promise(32), 32)
        before = resources.snapshot()
        resources.freeze()
        resources.freeze()
        after = resources.snapshot()
        for key in ('raw_reserved', 'working_reserved', 'traffic_spent',
                    'traffic_reserved', 'domains'):
            self.assertEqual(after[key], before[key])
        self.assertTrue(after['unknown'])
        with self.assertRaisesRegex(ValueError, '^resource_unknown$'):
            resources.release(memory)

    def test_finish_return_interruption_freezes_original_ledger_even_after_settlement(self):
        from trial_sdk_native_process_owner import NativeProcessOwner
        resources = AttemptResources()
        first = WireCredit(resources, resources.local, 32)
        other = WireCredit(resources, resources.bind(NativeProcessOwner()), 32)
        first.known(first.promise(32), 32)
        hit = []
        def trace(frame, event, arg):
            if (event == 'line' and frame.f_code.co_name == 'guarded' and
                    frame.f_locals.get('self') is first and
                    frame.f_locals.get('acquired') and not first._busy and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return trace
        old = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt): first.finish()
        finally: sys.settrace(old)
        self.assertEqual(hit, [True])
        self.assertEqual(resources.snapshot()['traffic_spent'], 32)
        self.assertTrue(resources.snapshot()['unknown'])
        with self.assertRaises(ValueError): other.promise(32)

    def test_two_promised_edges_share_original_commitment_and_spend_once(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 120)
        sent = credit.promise(53)
        received = credit.promise(56)
        with self.assertRaisesRegex(ServiceLinkError, '^wire_promise_slots$'):
            credit.promise(32)
        credit.known(sent, 53)
        credit.known(received, 40)
        credit.finish()
        state = resources.snapshot()
        self.assertEqual(state['traffic_spent'], 93)
        self.assertEqual(state['traffic_reserved'], 0)
        with self.assertRaisesRegex(ServiceLinkError, '^wire_credit_closed$'):
            credit.known(sent, 53)

    def test_unknown_edge_keeps_positive_prefix_and_all_unsettled_commitment(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 120)
        first = credit.promise(53)
        credit.known(first, 53)
        credit.promise(56)
        credit.quarantine()
        state = resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (53, 67))
        self.assertTrue(state['unknown'])
        with self.assertRaisesRegex(ServiceLinkError, '^wire_credit_unknown$'):
            credit.finish()

    def test_unfinished_edge_cannot_refund_and_counterfeit_receipt_does_not_consume(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 100)
        first = credit.promise(40)
        with self.assertRaisesRegex(ServiceLinkError, '^wire_receipt_pending$'):
            credit.finish()
        with self.assertRaisesRegex(ServiceLinkError, '^wire_promise$'):
            credit.known(object(), 40)
        self.assertEqual(credit.snapshot()['pending'], 1)
        credit.known(first, 40)
        credit.finish()
        self.assertEqual(resources.snapshot()['traffic_spent'], 40)

    def test_shared_unknown_blocks_another_held_credit_without_refunding_it(self):
        from trial_sdk_native_process_owner import NativeProcessOwner
        resources = AttemptResources()
        domain = resources.bind(NativeProcessOwner())
        first = WireCredit(resources, resources.local, 32)
        other = WireCredit(resources, domain, 32)
        first.quarantine()
        with self.assertRaisesRegex(ValueError, '^resource_unknown$'):
            other.promise(32)
        self.assertEqual(resources.snapshot()['traffic_reserved'], 64)


class LinkTests(unittest.TestCase):
    def test_actual_count_survives_interruption_before_and_after_credit_handoff(self):
        for direction in ('send','receive','burst'):
            for after_credit in (False, True):
                with self.subTest(direction=direction, after_credit=after_credit):
                    link = self.link()
                    endpoint = self.endpoint(self.records((w.READY,)))
                    self.bound(link, endpoint)
                    credit = link.credit(64)
                    if direction == 'send':
                        self.ready(link)
                        link.queue(w.SHUTDOWN, 0, credit)
                        step = link.send_step
                    else:
                        if direction == 'burst':
                            cursor = credit.burst((32,32))
                            link.adopt_receive_burst(credit, cursor)
                        else: link.arm_receive(credit, 32)
                        step = lambda: link.receive_step(lambda frame: None)
                    hit = []
                    def trace(frame, event, arg):
                        if (event == 'line' and frame.f_code.co_name in ('send_step','receive_step')
                                and frame.f_locals.get('self') is link
                                and frame.f_locals.get('count') == 32 and link._io is not None
                                and (credit.snapshot()['known'] == 32) == after_credit and not hit):
                            hit.append(True)
                            raise KeyboardInterrupt()
                        return trace
                    old = sys.gettrace()
                    try:
                        sys.settrace(trace)
                        with self.assertRaises(KeyboardInterrupt): step()
                    finally: sys.settrace(old)
                    self.assertEqual(hit, [True])
                    state = link.resources.snapshot()
                    self.assertEqual((state['traffic_spent'],state['traffic_reserved']), (32,32))
                    self.assertTrue(state['unknown'])
                    self.assertTrue(link.snapshot()['unknown'])

    def test_burst_eagain_preserves_cursor_and_view_until_actual_receive(self):
        link = self.link()
        records = self.records((w.READY,))
        inner = self.endpoint(records)
        class WaitingEndpoint:
            calls = 0
            def recvmsg_into(self, views, control, flags):
                self.calls += 1
                if self.calls == 1: raise BlockingIOError()
                return inner.recvmsg_into(views, control, flags)
        self.bound(link, WaitingEndpoint())
        credit = link.credit(32)
        cursor = credit.burst((32,))
        link.adopt_receive_burst(credit, cursor)
        self.assertFalse(link.receive_step(lambda frame: self.fail('not received')))
        loan = link._rx['hold']
        self.assertEqual(credit.burst_info(cursor)['index'], 0)
        self.assertEqual(credit.snapshot()['known'], 0)
        self.assertTrue(link.receive_step(lambda frame: None))
        self.assertIsNone(link._rx['hold'])
        self.assertTrue(loan.closed)
        link.finish_receive_burst();credit.finish()
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 32)

    def records(self, kinds):
        codec = Codec('S2P', lambda n: True, lambda n: None, profile=4)
        records = []
        for kind in kinds:
            with memoryview(bytearray(32)) as backing, memoryview(b'') as body:
                frame = codec.encode_into(backing, kind, 0, body)
                with frame.record as view: records.append(bytes(view))
                frame.release()
        return records

    def endpoint(self, records):
        class Endpoint:
            def send(self, record, flags): return len(record)
            def recvmsg_into(self, views, control, flags):
                record = records.pop(0)
                views[0][:len(record)] = record
                return (len(record), [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                       CREDENTIAL.pack(123, 1000, 1000))], 0, None)
        return Endpoint()

    def test_precommitted_burst_reuses_one_rx_backing_and_ends_only_after_known_branch(self):
        link = self.link()
        self.bound(link, self.endpoint(self.records((w.READY, w.BYE))))
        credit = link.credit(129)
        cursor = credit.burst((32, 32, 33))
        link.adopt_receive_burst(credit, cursor)
        self.assertTrue(link.receive_step(lambda frame: None))
        self.assertTrue(link.snapshot()['receive_pending'])
        link.queue(w.SHUTDOWN, 0, credit)
        self.assertTrue(link.send_step())
        self.assertTrue(link.receive_step(lambda frame: None))
        self.assertEqual(link.transcript.snapshot()['phase'], 'closed')
        link.finish_receive_burst()
        credit.finish()
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 96)
        self.assertEqual(link.resources.snapshot()['traffic_reserved'], 0)
        self.assertEqual(link.snapshot()['backing_bytes'], 1600)

    def test_future_other_credit_burst_is_committed_before_trigger_then_adopted(self):
        link = self.link()
        self.bound(link, self.endpoint(self.records((w.READY, w.BYE))))
        first = link.credit(32)
        emergency = link.credit(64, emergency=True)
        future = emergency.burst((32, 32))
        link.arm_receive(first, 32)
        self.assertTrue(link.receive_step(lambda frame: None))
        first.finish()
        shutdown = link.credit(32)
        link.queue(w.SHUTDOWN, 0, shutdown)
        self.assertTrue(link.send_step())
        shutdown.finish()
        link.adopt_receive_burst(emergency, future)
        self.assertTrue(link.receive_step(lambda frame: None))
        link.finish_receive_burst()
        emergency.finish()
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 96)
        self.assertEqual(link.resources.snapshot()['traffic_reserved'], 0)

    def link(self):
        return ServiceLink(AttemptResources(), deadline_ns=100, standing_working=4096,
                           standing_raw=2080, clock=lambda: 1)

    def test_standing_backings_debit_original_aggregate_before_prestart(self):
        resources = AttemptResources()
        link = ServiceLink(resources, deadline_ns=100, standing_working=4096,
                           standing_raw=2080, clock=lambda: 1)
        state = resources.snapshot()
        self.assertEqual(state['raw_reserved'], 2080)
        self.assertEqual(state['known_allocation_reserved'], 6176)
        self.assertFalse(link.owner.snapshot()['launch_attempted'])
        self.assertEqual(link.snapshot()['backing_bytes'], 1600)
        self.assertIsNone(link.snapshot()['service_wait'])

    def bound(self, link, endpoint):
        # Pure boundary fixture: actual record/codec/custody paths, but no kernel
        # credentials or live process are established by this test setup.
        transport = RecordSocket()
        transport._socket = endpoint
        transport._credentials = (123, 1000, 1000)
        transport._reserve = link._reserve_auxiliary
        transport._release = link._release_auxiliary
        transport._live = lambda: None
        transport._options = lambda: None
        link._connection = transport

    def test_eagain_keeps_same_frame_and_charges_only_known_send(self):
        link = self.link()
        class Endpoint:
            def __init__(self): self.records = []
            def send(self, record, flags):
                self.records.append(bytes(record))
                if len(self.records) == 1: raise BlockingIOError()
                return len(record)
        endpoint = Endpoint()
        self.bound(link, endpoint)
        # Establish the already-observed READY phase with a real encoded record.
        self.ready(link)
        credit = link.credit(32)
        link.queue(w.SHUTDOWN, 0, credit)
        self.assertFalse(link.send_step())
        self.assertEqual(link.transcript.snapshot()['phase'], 'idle')
        self.assertEqual(credit.snapshot()['known'], 0)
        self.assertTrue(link.send_step())
        self.assertEqual(endpoint.records[0], endpoint.records[1])
        self.assertEqual(link.transcript.snapshot()['phase'], 'closing')
        credit.finish()
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 32)
        self.assertEqual(link.snapshot()['backing_bytes'], 1600)

    def ready(self, link):
        with memoryview(bytearray(32)) as record, memoryview(b'') as body:
            frame = Codec('S2P', lambda n: True, lambda n: None, profile=4).encode_into(
                record, w.READY, 0, body)
            try: link.transcript.observe(frame)
            finally: frame.release()

    def test_authenticated_record_is_charged_even_when_frame_is_invalid(self):
        link = self.link()
        class Endpoint:
            def recvmsg_into(self, views, control, flags):
                views[0][:32] = b'x' * 32
                return (32, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                              CREDENTIAL.pack(123, 1000, 1000))], 0, None)
        self.bound(link, Endpoint())
        credit = link.credit(64)
        link.arm_receive(credit, 64)
        with self.assertRaisesRegex(ValueError, '^frame_magic$'):
            link.receive_step(lambda frame: self.fail('invalid frame reached consumer'))
        state = link.resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (32, 32))
        self.assertTrue(state['unknown'])
        self.assertTrue(link.snapshot()['unknown'])
        with self.assertRaisesRegex(ServiceLinkError, '^service_link_unknown$'):
            link.receive_step(lambda frame: None)

    def test_received_ready_borrow_ends_before_buffer_reuse_and_unused_credit_returns(self):
        link = self.link()
        with memoryview(bytearray(32)) as record, memoryview(b'') as body:
            frame = Codec('S2P', lambda n: True, lambda n: None, profile=4).encode_into(
                record, w.READY, 0, body)
            with frame.record as loan: encoded = bytes(loan)
            frame.release()
        class Endpoint:
            def recvmsg_into(self, views, control, flags):
                views[0][:32] = encoded
                return (32, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                              CREDENTIAL.pack(123, 1000, 1000))], 0, None)
        self.bound(link, Endpoint())
        credit = link.credit(544)
        link.arm_receive(credit, 544)
        seen = []
        self.assertTrue(link.receive_step(lambda frame: seen.append((frame.kind, frame.sequence))))
        credit.finish()
        self.assertEqual(seen, [(w.READY, 1)])
        self.assertEqual(link.transcript.snapshot()['phase'], 'idle')
        self.assertFalse(link.snapshot()['receive_pending'])
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 32)
        self.assertEqual(link.resources.snapshot()['traffic_reserved'], 0)

    def test_channel_close_alone_does_not_return_standing_allocation(self):
        link = self.link()
        link.close_channels()
        self.assertEqual(link.resources.snapshot()['raw_reserved'], 2080)
        with self.assertRaisesRegex(ServiceLinkError, '^service_retirement_pending$'):
            link.retire()
        link.owner.cleanup_unlaunched()
        link.retire()
        self.assertEqual(link.resources.snapshot()['known_allocation_reserved'], 0)
        self.assertTrue(link.snapshot()['retired'])
        with self.assertRaisesRegex(ServiceLinkError, '^service_retired$'):
            link.retire()

    def test_post_send_uncertainty_retains_original_loan_and_full_commitment(self):
        link = self.link()
        class Endpoint:
            def send(self, record, flags): raise OSError('ambiguous boundary')
        self.bound(link, Endpoint())
        self.ready(link)
        credit = link.credit(32)
        link.queue(w.SHUTDOWN, 0, credit)
        with self.assertRaises(ValueError): link.send_step()
        self.assertTrue(link.snapshot()['send_pending'])
        self.assertTrue(link.snapshot()['unknown'])
        state = link.resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (0, 32))
        self.assertTrue(state['unknown'])
        with self.assertRaisesRegex(ServiceLinkError, '^service_link_unknown$'):
            link.send_step()

    def test_post_syscall_failure_preserves_actual_positive_prefix_in_both_directions(self):
        for direction in ('send', 'receive'):
            with self.subTest(direction=direction):
                link = self.link()
                class Endpoint:
                    def send(self, record, flags): return len(record)
                    def recvmsg_into(self, views, control, flags):
                        views[0][:32] = b'x' * 32
                        return (32, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                      CREDENTIAL.pack(123, 1000, 1000))], 0, None)
                self.bound(link, Endpoint())
                live = []
                def lifetime():
                    live.append(True)
                    if len(live) == 2: raise ValueError('post_syscall_peer_loss')
                link._connection._live = lifetime
                credit = link.credit(64)
                if direction == 'send':
                    self.ready(link)
                    link.queue(w.SHUTDOWN, 0, credit)
                    step = link.send_step
                else:
                    link.arm_receive(credit, 32)
                    step = lambda: link.receive_step(lambda frame: self.fail('unusable frame'))
                with self.assertRaises(ValueError): step()
                state = link.resources.snapshot()
                self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (32, 32))
                self.assertTrue(state['unknown'])
                self.assertEqual(credit.snapshot()['known'], 32)

    def test_old_socket_receipt_cannot_charge_a_new_pre_effect_refusal(self):
        link = self.link()
        class Endpoint:
            def send(self, record, flags): return len(record)
        self.bound(link, Endpoint())
        self.ready(link)
        credit = link.credit(255)
        link.queue(w.PREPARE, 1, credit, 1, 99, 10, 100, 100, 2,
                   hashlib.sha256(b'{}').digest())
        self.assertTrue(link.send_step())
        old = link.snapshot()['last_socket_transfer']
        def refuse_before_io(): raise ValueError('peer_pre_effect_failure')
        link._connection._live = refuse_before_io
        link.queue(w.REQUEST, 1, credit, data=b'{}')
        with self.assertRaises(ValueError): link.send_step()
        self.assertEqual(link.snapshot()['last_socket_transfer'], old)
        self.assertEqual(credit.snapshot()['known'], 97)
        state = link.resources.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (97, 158))

    def test_final_socket_cleanup_interrupt_retains_observed_positive_prefix(self):
        link = self.link()
        class Endpoint:
            sends = 0
            def send(self, record, flags):
                self.sends += 1
                return len(record)
        endpoint = Endpoint()
        self.bound(link, endpoint)
        self.ready(link)
        credit = link.credit(64)
        link.queue(w.SHUTDOWN, 0, credit)
        hit = []
        def trace(frame, event, arg):
            if (event == 'line' and frame.f_code is RecordSocket.send.__code__ and
                    endpoint.sends and frame.f_locals.get('record') is None and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return trace
        old = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(ValueError): link.send_step()
        finally: sys.settrace(old)
        self.assertEqual(hit, [True])
        self.assertEqual(link.resources.snapshot()['traffic_spent'], 32)
        self.assertEqual(link.resources.snapshot()['traffic_reserved'], 32)
        self.assertTrue(link.resources.snapshot()['unknown'])

    def test_interruption_at_final_return_freezes_credit_and_retains_positive_receipt(self):
        resources = AttemptResources()
        credit = WireCredit(resources, resources.local, 53)
        promise = credit.promise(53)
        hit = []
        def trace(frame, event, arg):
            if (event == 'line' and frame.f_code.co_name == 'guarded' and
                    frame.f_locals.get('self') is credit and
                    frame.f_locals.get('acquired') and not credit._busy and not hit):
                hit.append(True)
                raise KeyboardInterrupt()
            return trace
        old = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt): credit.known(promise, 53)
        finally: sys.settrace(old)
        self.assertEqual(hit, [True])
        self.assertTrue(credit.snapshot()['unknown'])
        self.assertEqual(resources.snapshot()['traffic_spent'], 53)
        self.assertTrue(resources.snapshot()['unknown'])


if __name__ == '__main__':
    unittest.main()
