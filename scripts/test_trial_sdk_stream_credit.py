"""Pure original-ledger stream accounting. No subject or kernel fixtures."""
import sys
import unittest

from trial_sdk_attempt_resources import AttemptResources, ResourceError, TRAFFIC
from trial_sdk_stream_credit import StreamCredit, StreamCreditError, StreamCreditDenied


class StreamLedgerTests(unittest.TestCase):
    def test_growth_shares_original_bank_and_data_slot_without_changing_fixed_grant(self):
        bank = AttemptResources()
        fixed = bank.grant(bank.local, 'control', 100)
        stream = bank.stream_grant(bank.local, 578)
        with self.assertRaisesRegex(ResourceError, '^resource_attempt_live$'):
            bank.close()
        self.assertEqual(bank.grant_info(fixed), {'maximum': 100, 'available_at_issue': TRAFFIC})
        with self.assertRaisesRegex(ResourceError, '^resource_slot_busy$'):
            bank.grant(bank.local, 'data', 1)
        with self.assertRaisesRegex(ResourceError, '^resource_ticket$'):
            bank.grant_info(stream)
        self.assertTrue(bank.grow_stream(stream, TRAFFIC - 100))
        before = bank.snapshot()
        self.assertFalse(bank.grow_stream(stream, TRAFFIC - 99))
        self.assertEqual(bank.snapshot(), before)
        self.assertEqual(bank.stream_info(stream), {
            'initial_maximum': 578, 'maximum': TRAFFIC - 100, 'available_at_issue': TRAFFIC - 100})
        self.assertEqual(before['traffic_available'], 0)
        bank.settle_stream(stream, 47)
        self.assertEqual(bank.snapshot()['traffic_available'], TRAFFIC - 147)
        self.assertEqual(bank.grant_info(fixed), {'maximum': 100, 'available_at_issue': TRAFFIC})
        bank.settle(fixed, 19)
        self.assertEqual(bank.snapshot()['traffic_spent'], 66)
        self.assertEqual(bank.snapshot()['traffic_reserved'], 0)

    def test_prefixes_from_stream_and_two_fixed_credits_survive_shared_freeze(self):
        from trial_sdk_command_service_broker import WireCredit
        from trial_sdk_native_process_owner import NativeProcessOwner
        bank = AttemptResources()
        domain = bank.bind(NativeProcessOwner())  # Inert source identity, no launch.
        a = WireCredit(bank, bank.local, 64)
        b = WireCredit(bank, domain, 64)
        p, q = a.promise(32), b.promise(32)
        a.known(p, 32)
        b.known(q, 32)
        stream = bank.stream_grant(bank.local, 100)
        bank.grow_stream(stream, 200)
        bank.quarantine_stream(stream, 7)
        a._poison()  # Existing WireCredit terminal path after another credit freezes.
        b._poison()
        bank.quarantine_stream(stream, 23)
        bank.quarantine_stream(stream, 23)
        state = bank.snapshot()
        self.assertEqual((state['traffic_spent'], state['traffic_reserved']), (87, 241))
        self.assertEqual(state['traffic_available'], TRAFFIC - 328)
        self.assertTrue(state['unknown'])
        with self.assertRaisesRegex(ResourceError, '^resource_count$'):
            bank.quarantine_stream(stream, 22)
        with self.assertRaisesRegex(ResourceError, '^resource_unknown$'):
            bank.grow_stream(stream, 201)

    def test_fixed_final_zero_and_immutable_historical_ceilings_are_unchanged(self):
        bank = AttemptResources()
        fixed = bank.grant(bank.local, 'control', TRAFFIC - 100)
        stream = bank.stream_grant(bank.local, 100)
        with self.assertRaisesRegex(ResourceError, '^resource_ticket$'):
            bank.settle(stream, 0)
        with self.assertRaisesRegex(ResourceError, '^resource_stream_ticket$'):
            bank.grow_stream(fixed, TRAFFIC)
        bank.settle_stream(stream, 100)
        zero = bank.grant(bank.local, 'data', 0)
        self.assertEqual(bank.grant_info(zero), {'maximum': 0, 'available_at_issue': 0})
        bank.settle(zero, 0)
        self.assertEqual(bank.grant_info(fixed)['maximum'], TRAFFIC - 100)
        bank.settle(fixed, TRAFFIC - 100)
        self.assertEqual(bank.snapshot()['traffic_spent'], TRAFFIC)

    def test_stream_prefix_identity_count_and_diagnostic_copy(self):
        bank = AttemptResources()
        stream = bank.stream_grant(bank.local, 34)
        bank.grow_stream(stream, 578)
        copied = bank.stream_snapshot(stream)
        copied['maximum'] = TRAFFIC
        self.assertEqual(bank.stream_info(stream)['maximum'], 578)
        bank.freeze()
        for token, count in ((type(stream)(stream.ordinal), 17), (stream, True),
                             (stream, -1), (stream, 579)):
            before = bank.snapshot()
            with self.assertRaises(ResourceError):
                bank.quarantine_stream(token, count)
            self.assertEqual(bank.snapshot(), before)
        bank.quarantine_stream(stream, 578)
        self.assertEqual(bank.snapshot()['traffic_available'], TRAFFIC - 578)
        self.assertEqual(bank.snapshot()['traffic_reserved'], 0)
        self.assertTrue(bank.stream_snapshot(stream)['unknown'])

    def test_fixed_issue_after_stream_updates_combined_peak_before_either_settles(self):
        bank = AttemptResources()
        stream = bank.stream_grant(bank.local, 578)
        fixed = bank.grant(bank.local, 'control', 100)
        bank.settle_stream(stream, 47)
        bank.settle(fixed, 19)
        self.assertEqual(bank.snapshot()['committed_peak'], 678)


class StreamCreditTests(unittest.TestCase):
    def test_parked_credit_tx_and_stdio_then_protected_receive_use_only_two_promises(self):
        bank = AttemptResources()
        credit = StreamCredit(bank, bank.local, 578)
        transaction, peer = object(), object()
        tx = credit.promise(34)
        future = credit.protect_future_rx(transaction, peer, 1, 544, tx)
        stderr = credit.promise_stdio(512)
        self.assertEqual(credit.snapshot()['pending'], 2)
        self.assertEqual(credit.snapshot()['future_reserved'], 544)
        self.assertEqual(credit.maximum, 1090)
        credit.known(stderr, 7)
        credit.known(tx, 34)
        rx = credit.promote_future_rx(future, transaction, peer, 1)
        self.assertEqual((rx.maximum, credit.snapshot()['known']), (544, 41))
        self.assertEqual(credit.snapshot()['future_reserved'], 0)
        stdout = credit.promise_stdio(1)
        credit.known(stdout, 1)
        credit.known(rx, 100)
        # Transaction ended, but the SAME original producer lifetime stays open.
        stdin = credit.promise_stdio(512)
        credit.known(stdin, 11)
        self.assertFalse(credit.snapshot()['closed'])
        credit.finish()  # Trusted fixture says all relevant source lifetimes ended.
        self.assertEqual(bank.snapshot()['traffic_spent'], 153)
        self.assertEqual(bank.snapshot()['traffic_reserved'], 0)

    def setup_future(self, initial=578):
        bank = AttemptResources()
        credit = StreamCredit(bank, bank.local, initial)
        tx = credit.promise(34)
        transaction, peer = object(), object()
        future = credit.protect_future_rx(transaction, peer, 1, 544, tx)
        return bank, credit, tx, transaction, peer, future

    def test_protected_future_cannot_be_spent_and_capacity_denial_keeps_progress_possible(self):
        bank, credit, tx, transaction, peer, future = self.setup_future()
        fixed = bank.grant(bank.local, 'control', TRAFFIC - 578)
        before = bank.snapshot(), credit.snapshot()
        with self.assertRaisesRegex(StreamCreditDenied, '^stream_capacity_denied$'):
            credit.promise_stdio(1)
        self.assertFalse(credit.grow(579))
        self.assertEqual((bank.snapshot(), credit.snapshot()), before)
        credit.known(tx, 34)
        rx = credit.promote_future_rx(future, transaction, peer, 1)
        credit.known(rx, 32)
        # Known smaller reply frees room within the SAME existing commitment.
        stdio = credit.promise_stdio(512)
        credit.known(stdio, 512)
        self.assertEqual(credit.snapshot()['known'], 578)
        self.assertFalse(bank.snapshot()['unknown'])
        credit.finish()
        bank.settle(fixed, 0)

    def test_future_identity_generation_and_full_trigger_required_once(self):
        bank, credit, tx, transaction, peer, future = self.setup_future()
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_trigger_pending$'):
            credit.promote_future_rx(future, transaction, peer, 1)
        credit.known(tx, 34)
        for arguments in ((object(), transaction, peer, 1), (future, object(), peer, 1),
                          (future, transaction, object(), 1), (future, transaction, peer, True)):
            with self.assertRaisesRegex(StreamCreditError, '^stream_future_binding$'):
                credit.promote_future_rx(*arguments)
        rx = credit.promote_future_rx(future, transaction, peer, 1)
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_binding$'):
            credit.promote_future_rx(future, transaction, peer, 1)
        credit.known(rx, 100)
        next_tx = credit.promise(34)
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_binding$'):
            credit.protect_future_rx(transaction, peer, 1, 544, next_tx)
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_trigger$'):
            credit.protect_future_rx(transaction, peer, 2, 544, tx)
        next_future = credit.protect_future_rx(transaction, peer, 2, 544, next_tx)
        credit.known(next_tx, 33)
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_trigger_pending$'):
            credit.promote_future_rx(next_future, transaction, peer, 2)
        self.assertEqual(credit.snapshot()['generation'], 2)
        with self.assertRaisesRegex(StreamCreditError, '^stream_receipt_pending$'):
            credit.finish()
        credit.quarantine()
        self.assertEqual(bank.snapshot()['traffic_spent'], 167)

    def test_two_active_limit_and_wire_vs_raw_bounds(self):
        bank = AttemptResources()
        credit = StreamCredit(bank, bank.local, 1)
        one = credit.promise_stdio(1)
        two = credit.promise_stdio(512)
        with self.assertRaisesRegex(StreamCreditError, '^stream_promise_slots$'):
            credit.promise(32)
        for method, value in ((credit.promise, 31), (credit.promise, 545),
                              (credit.promise_stdio, 0), (credit.promise_stdio, 513),
                              (credit.promise_stdio, True)):
            with self.assertRaisesRegex(StreamCreditError, '^stream_promise$'):
                method(value)
        credit.known(one, 1)
        credit.known(two, 0)  # Actual known EAGAIN is zero, not uncertainty.
        self.assertEqual((credit.snapshot()['known'], credit.maximum), (1, 513))
        self.assertFalse(bank.snapshot()['unknown'])

    def test_unknown_keeps_future_and_full_original_ceiling_without_replay(self):
        bank, credit, tx, transaction, peer, future = self.setup_future()
        stdio = credit.promise_stdio(512)
        credit.unknown_prefix(stdio, 7)
        state = credit.snapshot()
        self.assertEqual((state['known'], state['pending'], state['future_reserved']), (7, 2, 544))
        self.assertEqual((bank.snapshot()['traffic_spent'], bank.snapshot()['traffic_reserved']), (7, 1083))
        for method, args in ((credit.known, (tx, 34)), (credit.grow, (1091,)),
                             (credit.promote_future_rx, (future, transaction, peer, 1)),
                             (credit.finish, ())):
            with self.assertRaisesRegex(StreamCreditError, '^stream_credit_unknown$'):
                method(*args)
        self.assertEqual(credit.snapshot(), state)

    def test_generation_exhaustion_and_second_future_refuse_before_mutation(self):
        from trial_sdk_attempt_resources import MAX_ORDINAL
        bank = AttemptResources()
        credit = StreamCredit(bank, bank.local, 578)
        tx = credit.promise(34)
        transaction, peer = object(), object()
        future = credit.protect_future_rx(transaction, peer, MAX_ORDINAL, 544, tx)
        before = bank.snapshot(), credit.snapshot()
        with self.assertRaises(StreamCreditError):
            credit.protect_future_rx(transaction, peer, MAX_ORDINAL, 544, tx)
        self.assertEqual((bank.snapshot(), credit.snapshot()), before)
        credit.known(tx, 34)
        rx = credit.promote_future_rx(future, transaction, peer, MAX_ORDINAL)
        credit.known(rx, 32)
        tx2 = credit.promise(34)
        before = bank.snapshot(), credit.snapshot()
        with self.assertRaisesRegex(StreamCreditError, '^stream_future_binding$'):
            credit.protect_future_rx(transaction, peer, MAX_ORDINAL + 1, 544, tx2)
        self.assertEqual((bank.snapshot(), credit.snapshot()), before)

    def test_other_credit_freeze_still_allows_terminal_recording_of_stream_prefix(self):
        bank, credit, tx, transaction, peer, future = self.setup_future()
        credit.known(tx, 34)
        bank.freeze()
        credit.quarantine()
        credit.quarantine()
        self.assertEqual(bank.snapshot()['traffic_spent'], 34)
        self.assertEqual(bank.snapshot()['traffic_reserved'], 544)
        self.assertTrue(credit.snapshot()['unknown'])

    def test_each_of_three_stdio_actions_is_funded_not_one_fictional_512_byte_step(self):
        bank, credit, tx, transaction, peer, future = self.setup_future()
        for count in (512, 512, 512):
            stdio = credit.promise_stdio(512)
            credit.known(stdio, count)
        self.assertEqual((credit.snapshot()['known'], credit.maximum), (1536, 2114))
        self.assertEqual(credit.snapshot()['future_reserved'], 544)
        credit.known(tx, 34)
        rx = credit.promote_future_rx(future, transaction, peer, 1)
        credit.known(rx, 544)
        credit.finish()
        self.assertEqual(bank.snapshot()['traffic_spent'], 2114)

    def test_no_positive_action_or_eof_authority_minted_at_original_exhaustion(self):
        bank = AttemptResources()
        fixed = bank.grant(bank.local, 'control', TRAFFIC - 1)
        credit = StreamCredit(bank, bank.local, 1)
        byte = credit.promise_stdio(1)
        credit.known(byte, 1)
        before = bank.snapshot(), credit.snapshot()
        with self.assertRaises(StreamCreditDenied):
            credit.promise_stdio(1)
        self.assertEqual((bank.snapshot(), credit.snapshot()), before)
        # No raw EOF read/stream-close method or invented EOF fact in this port.
        self.assertNotIn('eof', credit.snapshot())
        credit.quarantine()  # Trusted fixture cannot establish lifetime end.
        self.assertEqual(bank.snapshot()['traffic_reserved'], TRAFFIC - 1)
        self.assertIsNotNone(fixed)

    def test_constructor_capacity_denial_does_not_consume_slot_or_poison(self):
        bank = AttemptResources()
        fixed = bank.grant(bank.local, 'control', TRAFFIC)
        before = bank.snapshot()
        with self.assertRaises(StreamCreditDenied):
            StreamCredit(bank, bank.local, 1)
        self.assertEqual(bank.snapshot(), before)
        bank.settle(fixed, TRAFFIC - 1)
        credit = StreamCredit(bank, bank.local, 1)
        self.assertEqual(credit.maximum, 1)


class InterruptionTests(unittest.TestCase):
    @staticmethod
    def inject(call, predicate, fault):
        hits = []
        def trace(frame, event, argument):
            if event == 'line' and not hits and predicate(frame):
                hits.append(True)
                raise fault
            return trace
        old = sys.gettrace()
        try:
            sys.settrace(trace)
            try:
                call()
            except BaseException as caught:
                if caught is not fault:
                    raise AssertionError('primary was replaced') from caught
            else:
                raise AssertionError('required trace did not fire')
        finally:
            sys.settrace(old)
        if hits != [True]:
            raise AssertionError('required trace was not exactly once')

    def test_grow_bank_return_and_outer_handoff_freeze_without_losing_ceiling(self):
        for where in ('bank_handoff', 'outer_return'):
          for fault in (KeyboardInterrupt(), StreamCreditDenied('injected'), BlockingIOError()):
            with self.subTest(where=where, fault=type(fault).__name__):
                bank = AttemptResources()
                credit = StreamCredit(bank, bank.local, 34)
                def selected(frame):
                    if frame.f_locals.get('self') is not credit:
                        return False
                    if where == 'bank_handoff':
                        return (frame.f_code is StreamCredit._grow.__code__
                            and frame.f_locals.get('accepted') is True and credit._bank_call)
                    return (frame.f_code is StreamCredit.grow.__code__
                        and frame.f_locals.get('method') is StreamCredit.grow.__wrapped__
                        and frame.f_locals.get('answer') is True and not credit._busy)
                self.inject(lambda: credit.grow(578), selected, fault)
                self.assertEqual(credit.maximum, 578)
                self.assertEqual(bank.snapshot()['traffic_reserved'], 578)
                self.assertTrue(bank.snapshot()['unknown'])
                with self.assertRaises(StreamCreditError):
                    credit.promise_stdio(1)

    def test_constructor_final_handoff_keeps_original_ticket_and_freezes(self):
        for final_return in (False, True):
            with self.subTest(final_return=final_return):
                bank = AttemptResources()
                self.inject(lambda: StreamCredit(bank, bank.local, 578), lambda frame:
                    frame.f_code is StreamCredit.__init__.__code__
                    and frame.f_locals.get('resources') is bank
                    and getattr(frame.f_locals.get('self'), '_mutating', False)
                    and not frame.f_locals.get('self')._bank_call
                    and (not final_return or 'answer' in frame.f_locals), KeyboardInterrupt())
                self.assertTrue(bank.snapshot()['unknown'])
                self.assertEqual(bank.snapshot()['traffic_reserved'], 578)
                self.assertEqual(bank.snapshot()['domains'][0]['traffic'], ('data',))

    def test_promote_single_state_transfer_and_guard_final_return_never_refund(self):
        # Both the transfer body and its outer final-return guard are targeted.
        for where in ('before_transfer', 'after_transfer', 'return'):
            with self.subTest(where=where):
                bank, credit, tx, transaction, peer, future = StreamCreditTests().setup_future()
                credit.known(tx, 34)
                method = StreamCredit.promote_future_rx
                def selected(frame):
                    if frame.f_locals.get('self') is not credit:
                        return False
                    if where == 'return':
                        return (frame.f_code is method.__code__
                            and frame.f_locals.get('method') is method.__wrapped__
                            and frame.f_locals.get('answer') is not None and not credit._busy)
                    if frame.f_code is not method.__wrapped__.__code__:
                        return False
                    if where == 'before_transfer':
                        return credit._mutating and 'promise' in frame.f_locals
                    return credit._state.future is None
                self.inject(lambda: credit.promote_future_rx(future, transaction, peer, 1),
                            selected, KeyboardInterrupt())
                state = credit.snapshot()
                self.assertEqual(state['known'], 34)
                self.assertEqual(state['promised'] + state['future_reserved'], 544)
                self.assertEqual(bank.snapshot()['traffic_spent'], 34)
                self.assertEqual(bank.snapshot()['traffic_reserved'], 544)
                self.assertTrue(state['unknown'])

    def test_staged_known_prefix_survives_interrupt_without_double_count(self):
        for after_apply in (False, True):
            with self.subTest(after_apply=after_apply):
                bank = AttemptResources()
                credit = StreamCredit(bank, bank.local, 100)
                promise = credit.promise_stdio(100)
                def selected(frame):
                    return (frame.f_code is StreamCredit.known.__wrapped__.__code__
                        and frame.f_locals.get('self') is credit
                        and credit._receipt is not None
                        and (credit._state.known == 17) == after_apply)
                self.inject(lambda: credit.known(promise, 17), selected, BlockingIOError())
                self.assertEqual((bank.snapshot()['traffic_spent'],
                                  bank.snapshot()['traffic_reserved']), (17, 83))
                credit.quarantine()
                self.assertEqual(bank.snapshot()['traffic_spent'], 17)

    def test_interrupted_stream_settlement_never_exposes_capacity_or_repeats_prefix(self):
        for after_delete in (False, True):
            with self.subTest(after_delete=after_delete):
                bank = AttemptResources()
                credit = StreamCredit(bank, bank.local, 100)
                p = credit.promise_stdio(100)
                credit.known(p, 17)
                def selected(frame):
                    return (frame.f_code.co_name == 'settle_stream'
                        and frame.f_locals.get('self') is bank and bank._spent == 17
                        and (credit.ticket not in bank._streams) == after_delete)
                self.inject(credit.finish, selected, KeyboardInterrupt())
                state = bank.snapshot()
                self.assertTrue(state['unknown'])
                self.assertGreaterEqual(state['traffic_spent'] + state['traffic_reserved'], 17)
                self.assertTrue(credit.snapshot()['unknown'])
                self.assertEqual(credit.maximum, 100)
                with self.assertRaises(ResourceError):
                    bank.grant(bank.local, 'control', 1)

    def test_bank_growth_single_scalar_and_final_guard_preserve_conservative_commitment(self):
        # Discover only executed lines for this exact inert method/outer guard.
        sample = AttemptResources()
        token = sample.stream_grant(sample.local, 34)
        wrapper = AttemptResources.grow_stream
        body = next(c.cell_contents for c in wrapper.__closure__
                    if callable(c.cell_contents) and c.cell_contents.__name__ == 'grow_stream')
        points = set()
        def selected(frame):
            return (frame.f_locals.get('self') is sample
                    and (frame.f_code is body.__code__ or
                         (frame.f_code is wrapper.__code__ and frame.f_locals.get('method') is body)))
        def record(frame, event, argument):
            if event == 'line' and selected(frame):
                points.add((frame.f_code, frame.f_lineno))
            return record
        old = sys.gettrace()
        try:
            sys.settrace(record)
            sample.grow_stream(token, 578)
        finally:
            sys.settrace(old)
        self.assertGreater(len(points), 15)
        for code, line in points:
            bank = AttemptResources()
            token = bank.stream_grant(bank.local, 34)
            self.inject(lambda: bank.grow_stream(token, 578), lambda frame:
                frame.f_code is code and frame.f_lineno == line
                and frame.f_locals.get('self') is bank, KeyboardInterrupt())
            state = bank.snapshot()
            self.assertIn(state['traffic_reserved'], (34, 578))
            self.assertEqual(state['traffic_spent'], 0)
            if state['traffic_reserved'] == 578:
                self.assertTrue(state['unknown'])
                self.assertGreaterEqual(state['committed_peak'], 578)


if __name__ == '__main__':
    unittest.main()
