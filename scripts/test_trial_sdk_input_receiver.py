import unittest
import struct
import sys
from test_trial_sdk_input_custody import Ledger
from trial_sdk_input_receiver import InputReceiver, ReceiverError
from trial_sdk_ipc import Codec
from trial_sdk_input_profile import InputClient


class ReceiverTests(unittest.TestCase):
    def test_close_and_cancel_callbacks_cannot_reenter_or_retain_a_nested_body(self):
        for phase in ('end', 'cancel'):
            with self.subTest(phase=phase):
                ledger, observed = Ledger(), []
                def closer():
                    raw = bytes(bytearray(b'{}\n'))
                    before = sys.getrefcount(raw)
                    retained = None
                    try:
                        stream.receive(9, 99, raw, 0)
                    except ReceiverError as fault:
                        retained = fault
                    self.assertIsNotNone(retained)
                    self.assertEqual(str(retained), 'receiver_busy')
                    self.assertEqual(sys.getrefcount(raw), before)
                    with self.assertRaisesRegex(ReceiverError, '^receiver_busy$'):
                        stream.cancel()
                    observed.append(True)
                stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                                       lambda view: len(view), closer)
                if phase == 'end':
                    stream.receive(5, 1, b'', 0)
                    stream.advance()
                    self.assertEqual(stream.reply, (133, 1, 0))
                else:
                    stream.cancel()
                    self.assertEqual(stream.snapshot()['state'], 'cancelled')
                self.assertEqual(observed, [True])
                self.assertEqual(ledger.used, 0)

    def test_invalid_body_budget_order_or_incomplete_end_never_starts_a_write(self):
        cases = [(4, 1, b'{}\n', 0, 100), (9, True, b'{}\n', 0, 100),
                 (9, 1, b'', 0, 100), (9, 1, bytes(513), 0, 100),
                 (5, 1, b'x', 0, 100), (9, 1, bytearray(b'{}\n'), 0, 100),
                 (9, 1, b'{}\n', 0.5, 100), (9, 1, b'{}\n', 0, 2)]
        for kind, op, body, first, maximum in cases:
            with self.subTest(kind=kind, op=op, maximum=maximum):
                ledger, writes, closes = Ledger(), [], []
                stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                                       lambda view: writes.append(len(view)) or len(view),
                                       lambda: closes.append(True), maximum=maximum)
                with self.assertRaises(ValueError):
                    stream.receive(kind, op, body, first)
                self.assertEqual(stream.snapshot()['state'], 'failed')
                self.assertEqual(writes, [])
                self.assertEqual(closes, [True])
                self.assertEqual(ledger.used, 0)
                self.assertIsNone(stream.reply)
        ledger, writes, closes = Ledger(), [], []
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               lambda view: writes.append(len(view)) or len(view),
                               lambda: closes.append(True))
        stream.receive(9, 1, b'{', 0)
        stream.reply_sent()
        with self.assertRaises(ValueError):
            stream.receive(5, 3, b'', 1)
        self.assertEqual(writes, [])
        self.assertEqual(closes, [True])
        self.assertEqual(stream.snapshot()['custodied_bytes'], 1)
        self.assertIsNone(stream.reply)

    def test_each_advance_is_one_attempt_and_zero_eagain_do_not_close_or_ack(self):
        ledger, native, closes, calls = Ledger(), bytearray(), [], []
        def writer(view):
            calls.append(len(view))
            if len(calls) == 1:
                return 0
            if len(calls) == 2:
                raise BlockingIOError()
            native.extend(view[:1])
            return 1
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               writer, lambda: closes.append(True))
        stream.receive(9, 1, b'{}\n', 0)
        stream.reply_sent()
        stream.receive(5, 3, b'', 1)
        for number, expected in enumerate([0, 0, 1, 1, 1], 1):
            self.assertEqual(stream.advance(), expected)
            self.assertEqual(len(calls), number)
            if number < 5:
                self.assertIsNone(stream.reply)
                self.assertEqual(closes, [])
            self.assertFalse(stream.snapshot()['native_outcome_unknown'])
        self.assertEqual(native, b'{}\n')
        self.assertEqual(stream.reply, (133, 3, 3))
        self.assertEqual(closes, [True])
        stream.reply_sent()
        self.assertEqual(ledger.used, 0)

    def test_real_codec_and_client_consume_custody_then_drain_close_receipts(self):
        ledger, native, closed = Ledger(), bytearray(), []
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               lambda view: native.extend(view) or len(view),
                               lambda: closed.append(True), maximum=100)
        peer = InputClient(2, 100)
        request_send = Codec('N2P_DATA', ledger.reserve, ledger.release, profile=2)
        request_read = Codec('N2P_DATA', ledger.reserve, ledger.release, profile=2)
        result_send = Codec('P2N_DATA', ledger.reserve, ledger.release, profile=2)
        result_read = Codec('P2N_DATA', ledger.reserve, ledger.release, profile=2)
        def receive(kind, op, raw):
            sent = request_send.encode_into(memoryview(bytearray(32+len(raw))), kind, op, memoryview(raw))
            record = sent.record
            incoming = request_read.decode(record)
            body = incoming.body
            try:
                stream.receive(incoming.kind, incoming.operation, body, op)
            finally:
                body.release()
                incoming.release()
                record.release()
                sent.release()
        def deliver_reply():
            kind, op, count = stream.reply
            size = 2 if kind == 140 else 5 if kind == 133 else 0
            raw = bytearray(size)
            if kind == 140:
                struct.pack_into('>H', raw, 0, count)
            elif kind == 133:
                struct.pack_into('>BI', raw, 0, 1, count)
            sent = result_send.encode_into(memoryview(bytearray(32+size)), kind, op, memoryview(raw))
            record = sent.record
            incoming = result_read.decode(record)
            body = incoming.body
            try:
                peer.writable() if kind == 132 else peer.result(incoming.operation, incoming.kind, body)
            finally:
                body.release()
                incoming.release()
                record.release()
                sent.release()
            self.assertIsNotNone(stream.reply)
            stream.reply_sent()  # Test transport's known completed send; not model delivery.
        for op, raw in [(1, b'{'), (3, b'}\n')]:
            peer.begin_custody(op, len(raw))
            receive(9, op, raw)
            deliver_reply()
            self.assertIsNone(peer.snapshot()['known_native_total'])
            self.assertEqual(native, b'')
            self.assertEqual(ledger.used, 16)
        peer.begin_end(7)
        receive(5, 7, b'')
        stream.advance()
        self.assertEqual(native, b'{}')
        self.assertIsNone(stream.reply)
        self.assertIsNone(peer.snapshot()['known_native_total'])
        stream.advance()
        self.assertEqual(closed, [True])
        self.assertIsNone(peer.snapshot()['known_native_total'])
        deliver_reply()
        self.assertEqual(peer.snapshot()['known_native_total'], 3)
        self.assertEqual(peer.snapshot()['state'], 'closed')
        self.assertEqual(stream.snapshot()['state'], 'closed')
        self.assertEqual(ledger.used, 0)

    def test_retained_receive_errors_do_not_hold_borrowed_exact_bytes(self):
        ledger, closed = Ledger(), []
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               lambda view: len(view), lambda: closed.append(True))
        raw = bytes(bytearray(b'{"invalid":}\n'))
        before = sys.getrefcount(raw)
        retained = None
        try:
            stream.receive(9, 1, raw, 0)
        except ValueError as fault:
            retained = fault
        self.assertIsNotNone(retained)
        self.assertEqual(sys.getrefcount(raw), before)
        self.assertIsNone(stream.reply)
        self.assertEqual(stream.snapshot()['state'], 'failed')
        self.assertEqual(closed, [True])
        self.assertEqual(ledger.used, 0)

    def test_unknown_write_preserves_primary_and_known_count_when_cleanup_also_fails(self):
        ledger, native, closes = Ledger(), bytearray(), []
        primary, secondary = OSError('selected write fault'), RuntimeError('selected cleanup fault')
        def release(count):
            ledger.releases.append(count)
            raise secondary
        def writer(view):
            native.extend(view[:1])
            raise primary
        def closer():
            closes.append(True)
            raise secondary
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, release, writer, closer)
        stream.receive(9, 1, b'{}\n', 0)
        stream.reply_sent()
        with self.assertRaises(OSError) as caught:
            stream.advance()
        self.assertIs(caught.exception, primary)
        snap = stream.snapshot()
        self.assertEqual(snap['state'], 'failed')
        self.assertTrue(snap['native_outcome_unknown'])
        self.assertTrue(snap['cleanup_unknown'])
        self.assertTrue(snap['close_unknown'])
        self.assertTrue(snap['receiver_cleanup_unknown'])
        self.assertEqual(snap['known_native_written_bytes'], 0)
        self.assertEqual(native, b'{')
        self.assertIsNone(stream.reply)
        returns = list(ledger.releases)
        stream.cancel()
        self.assertEqual(closes, [True])
        self.assertEqual(ledger.releases, returns)
        self.assertEqual(stream.snapshot()['state'], 'failed')

    def test_transport_uncertainty_discards_reply_and_never_replays_input(self):
        ledger, closed, native = Ledger(), [], bytearray()
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               lambda view: native.extend(view) or len(view),
                               lambda: closed.append(True))
        stream.receive(9, 1, b'{}\n', 0)
        with self.assertRaisesRegex(ReceiverError, '^receiver_transport_unknown$'):
            stream.transport_unknown()
        snap = stream.snapshot()
        self.assertTrue(snap['transport_outcome_unknown'])
        self.assertEqual(snap['state'], 'failed')
        self.assertIsNone(stream.reply)
        self.assertEqual(snap['custodied_bytes'], 3)
        self.assertEqual(snap['known_native_written_bytes'], 0)
        self.assertEqual(native, b'')
        self.assertEqual(closed, [True])
        self.assertEqual(ledger.used, 0)
        stream.cancel()
        self.assertEqual(closed, [True])
        with self.assertRaisesRegex(ReceiverError, '^receiver_unusable$'):
            stream.receive(9, 3, b'{}\n', 1)

    def test_uncertain_close_never_emits_end_success_or_retries_close(self):
        for mode in ('throw', 'false', 'true'):
            with self.subTest(mode=mode):
                ledger, closes = Ledger(), []
                primary = OSError('selected close fault')
                def closer():
                    closes.append(True)
                    if mode == 'throw':
                        raise primary
                    return mode == 'true'
                stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                                       lambda view: len(view), closer)
                stream.receive(5, 1, b'', 0)
                with self.assertRaises((ReceiverError, OSError)) as caught:
                    stream.advance()
                if mode == 'throw':
                    self.assertIs(caught.exception, primary)
                snap = stream.snapshot()
                self.assertEqual(snap['state'], 'failed')
                self.assertTrue(snap['close_unknown'])
                self.assertFalse(snap['closed_known'])
                self.assertIsNone(stream.reply)
                self.assertEqual(ledger.used, 0)
                stream.cancel()
                self.assertEqual(closes, [True])
                self.assertEqual(stream.snapshot()['state'], 'failed')

    def test_cancel_discards_unsent_reply_and_closes_once_without_a_success_receipt(self):
        for phase in ('collecting', 'reply-pending', 'draining', 'ending'):
            with self.subTest(phase=phase):
                ledger, closed, writes = Ledger(), [], []
                stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                                       lambda view: writes.append(len(view)) or len(view),
                                       lambda: closed.append(True))
                stream.receive(9, 1, b'{' if phase == 'collecting' else b'{}\n', 0)
                if phase != 'reply-pending':
                    stream.reply_sent()
                if phase == 'ending':
                    stream.receive(5, 3, b'', 1)
                stream.cancel()
                self.assertEqual(stream.snapshot()['state'], 'cancelled')
                self.assertTrue(stream.snapshot()['closed_known'])
                self.assertIsNone(stream.reply)
                self.assertEqual(writes, [])
                self.assertEqual(closed, [True])
                self.assertEqual(ledger.used, 0)
                returns = list(ledger.releases)
                stream.cancel()
                self.assertEqual(closed, [True])
                self.assertEqual(ledger.releases, returns)
                with self.assertRaisesRegex(ReceiverError, '^receiver_unusable$'):
                    stream.advance()

    def test_one_reply_and_paused_suffix_require_a_sent_readiness_notice(self):
        ledger, native, closed = Ledger(), bytearray(), []
        def writer(view):
            native.extend(view)
            return len(view)
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               writer, lambda: closed.append(True))
        stream.receive(9, 1, b'{}\n', 0)
        with self.assertRaisesRegex(ReceiverError, '^receiver_reply_pending$'):
            stream.receive(9, 3, b'{}\n', 1)
        self.assertEqual(stream.reply, (140, 1, 3))
        self.assertEqual(stream.advance(), 0)
        self.assertEqual(native, b'')
        stream.reply_sent()
        stream.receive(9, 3, b'{}\n', 1)
        self.assertEqual(stream.reply, (140, 3, 0))
        stream.reply_sent()
        self.assertTrue(stream.snapshot()['paused'])
        self.assertEqual(stream.advance(), 2)
        self.assertIsNone(stream.reply)
        self.assertEqual(stream.advance(), 1)
        self.assertEqual(stream.reply, (132, 0, None))
        with self.assertRaisesRegex(ReceiverError, '^receiver_reply_pending$'):
            stream.receive(9, 5, b'{}\n', 2)
        self.assertTrue(stream.snapshot()['paused'])
        stream.reply_sent()
        self.assertFalse(stream.snapshot()['paused'])
        stream.receive(9, 5, b'{}\n', 2)
        self.assertEqual(stream.reply, (140, 5, 3))
        stream.reply_sent()
        stream.receive(5, 7, b'', 3)
        self.assertEqual(stream.advance(), 2)
        self.assertEqual(stream.advance(), 1)
        self.assertEqual(stream.reply, (133, 7, 6))
        self.assertEqual(native, b'{}\n{}\n')
        self.assertEqual(closed, [True])
        stream.reply_sent()
        self.assertEqual(ledger.used, 0)

    def test_empty_end_closes_once_without_a_native_write(self):
        ledger, events = Ledger(), []
        stream = InputReceiver(lambda graph, meta: True, ledger.reserve, ledger.release,
                               lambda view: events.append('write') or len(view),
                               lambda: events.append('close'))
        stream.receive(5, 1, b'', 0)
        self.assertIsNone(stream.reply)
        self.assertEqual(stream.advance(), 0)
        self.assertEqual(events, ['close'])
        self.assertEqual(stream.reply, (133, 1, 0))
        self.assertEqual(stream.advance(), 0)
        self.assertEqual(events, ['close'])
        stream.reply_sent()
        self.assertEqual(stream.snapshot()['state'], 'closed')
        self.assertEqual(ledger.used, 0)

    def test_custody_reply_then_end_requires_real_drain_and_close(self):
        ledger, native, closed, valid = Ledger(), bytearray(), [], []
        def validate(graph, metadata):
            valid.append(graph)
            return True
        def writer(view):
            self.assertEqual(valid, [{}])
            native.extend(view)
            return len(view)
        stream = InputReceiver(validate, ledger.reserve, ledger.release,
                               writer, lambda: closed.append(True), maximum=100)
        stream.receive(9, 1, b'{', 0)
        self.assertEqual(stream.reply, (140, 1, 1))
        self.assertEqual(native, b'')
        stream.reply_sent()
        stream.receive(9, 3, b'}\n', 1)
        self.assertEqual(stream.reply, (140, 3, 2))
        self.assertEqual(native, b'')
        stream.reply_sent()
        stream.receive(5, 7, b'', 2)
        self.assertIsNone(stream.reply)
        self.assertEqual(closed, [])
        self.assertEqual(stream.advance(), 2)
        self.assertEqual(native, b'{}')
        self.assertIsNone(stream.reply)
        self.assertEqual(closed, [])
        self.assertEqual(stream.advance(), 1)
        self.assertEqual(native, b'{}\n')
        self.assertEqual(closed, [True])
        self.assertEqual(stream.reply, (133, 7, 3))
        self.assertEqual(stream.snapshot()['state'], 'ending')
        self.assertEqual(stream.advance(), 0)
        self.assertEqual(closed, [True])
        stream.reply_sent()
        self.assertEqual(stream.snapshot()['state'], 'closed')
        self.assertTrue(stream.snapshot()['closed_known'])
        self.assertEqual(ledger.used, 0)


if __name__ == '__main__':
    unittest.main()
