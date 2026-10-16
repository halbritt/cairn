import hashlib
import sys
import unittest
import weakref

from trial_sdk_input_custody import CustodyError, InputCustody


class Ledger:
    def __init__(self):
        self.used = self.peak = 0
        self.releases = []

    def reserve(self, count):
        self.used += count
        self.peak = max(self.peak, self.used)
        return True

    def release(self, count):
        self.used -= count
        assert self.used >= 0
        self.releases.append(count)


class CustodyTests(unittest.TestCase):
    def test_callback_gated_fragments_validate_before_first_native_write(self):
        ledger, validated, native = Ledger(), [], bytearray()
        payload = b'{ "value":"a\\n\\u00e9", "type":"control" }\r\n'
        def validate(graph, metadata):
            self.assertEqual(graph, {'value': 'a\n\u00e9', 'type': 'control'})
            validated.append(metadata)
            return True
        stream = InputCustody(validate, ledger.reserve, ledger.release)
        for n, byte in enumerate(payload):
            self.assertEqual(stream.offer(bytes([byte]), n), {'custodied': 1})
            self.assertEqual(native, b'')
            if n < len(payload) - 1:
                self.assertEqual(validated, [])
        self.assertEqual(len(validated), 1)
        stream.end_input()
        def writer(view):
            self.assertEqual(len(validated), 1)
            native.extend(view)
            return len(view)
        while stream.snapshot()['state'] != 'drained':
            self.assertGreater(stream.write_to(writer), 0)
        self.assertEqual(native, payload)
        snap = stream.snapshot()
        self.assertEqual(snap['custodied_bytes'], len(payload))
        self.assertEqual(snap['validated_bytes'], len(payload))
        self.assertEqual(snap['known_native_written_bytes'], len(payload))
        self.assertFalse(snap['native_outcome_unknown'])
        self.assertEqual(ledger.used, 0)
        self.assertLessEqual(ledger.peak, 16 + 512)

    def test_cleanup_failure_keeps_known_native_count_and_failed_disposition(self):
        ledger, native = Ledger(), bytearray()
        fault = RuntimeError('release_fault')
        def release(count):
            if count != 16:
                ledger.releases.append(count)
                raise fault
            ledger.release(count)
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, release)
        stream.offer(b'{"a":1}\n', 0)
        stream.end_input()
        def writer(view):
            native.extend(view)
            return len(view)
        with self.assertRaises(RuntimeError) as caught:
            stream.write_to(writer)
        self.assertIs(caught.exception, fault)
        self.assertEqual(native, b'{"a":1}')
        snap = stream.snapshot()
        self.assertEqual(snap['known_native_written_bytes'], 7)
        self.assertFalse(snap['native_outcome_unknown'])
        self.assertTrue(snap['cleanup_unknown'])
        self.assertEqual(snap['state'], 'failed')
        releases = list(ledger.releases)
        stream.cancel()
        self.assertEqual(stream.snapshot()['state'], 'failed')
        self.assertEqual(ledger.releases, releases)

    def test_retained_decode_error_does_not_retain_returned_raw_loan(self):
        class OwnedBuffer(bytearray):
            pass
        ledger = Ledger()
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
        raw = OwnedBuffer(b'{"a":"\xf0x"}\n')
        observed = weakref.ref(raw)
        loan = memoryview(raw).toreadonly()
        retained = None
        try:
            stream.offer(loan, 0)
        except ValueError as error:
            retained = error  # Keep the full public exception/traceback alive.
        finally:
            loan.release()
        self.assertIsNotNone(retained)
        self.assertEqual(str(retained), 'incoming_utf8')
        raw = loan = None
        self.assertIsNone(observed())
        self.assertEqual(ledger.used, 0)
        releases = list(ledger.releases)
        stream.cancel()
        self.assertEqual(stream.snapshot()['state'], 'failed')
        self.assertEqual(ledger.releases, releases)

    def test_invalid_completion_and_incomplete_eof_never_write_native(self):
        cases = ((b'{"a":', b'}\n', lambda graph, meta: True),
                 (b'{"a":', b'1}\n', lambda graph, meta: False),
                 (b'{"a":', b'1}\n', lambda graph, meta: 1),
                 (b'{"a":', None, lambda graph, meta: True))
        for prefix, suffix, validator in cases:
            with self.subTest(suffix=suffix, validator=validator):
                ledger, calls = Ledger(), []
                stream = InputCustody(validator, ledger.reserve, ledger.release)
                self.assertEqual(stream.offer(prefix, 0), {'custodied': len(prefix)})
                self.assertEqual(stream.write_to(lambda view: calls.append('native')), 0)
                with self.assertRaises(ValueError):
                    stream.end_input() if suffix is None else stream.offer(suffix, 1)
                snap = stream.snapshot()
                self.assertEqual(snap['state'], 'failed')
                self.assertEqual(snap['custodied_bytes'], len(prefix))
                self.assertEqual(snap['validated_bytes'], 0)
                self.assertEqual(snap['known_native_written_bytes'], 0)
                self.assertFalse(snap['native_outcome_unknown'])
                self.assertEqual(calls, [])
                self.assertEqual(ledger.used, 0)
                with self.assertRaisesRegex(CustodyError, '^custody_unusable$'):
                    stream.offer(b'{}\n', 2)

    def test_original_128k_limit_and_overflow_with_bounded_forwarding_credit(self):
        limit = 128 * 1024
        for excess in (0, 1):
            with self.subTest(excess=excess):
                ledger, native = Ledger(), bytearray()
                payload = b'{"text":"' + b'x' * (limit - 11 + excess) + b'"}\n'
                self.assertEqual(len(payload), limit + 1 + excess)
                stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
                if excess:
                    with self.assertRaisesRegex(ValueError, '^incoming_bytes_exceeded$'):
                        for n in range(0, len(payload), 512):
                            fragment = payload[n:n + 512]
                            self.assertEqual(stream.offer(fragment, n), {'custodied': len(fragment)})
                    self.assertEqual(stream.snapshot()['state'], 'failed')
                    self.assertEqual(stream.snapshot()['known_native_written_bytes'], 0)
                    self.assertEqual(ledger.used, 0)
                else:
                    for n in range(0, len(payload), 512):
                        fragment = payload[n:n + 512]
                        self.assertEqual(stream.offer(fragment, n), {'custodied': len(fragment)})
                        self.assertEqual(ledger.used, 16)
                    stream.end_input()
                    def writer(view):
                        native.extend(view)
                        return len(view)
                    while stream.snapshot()['state'] != 'drained':
                        self.assertGreater(stream.write_to(writer), 0)
                    self.assertEqual(native, payload)
                    self.assertEqual(stream.snapshot()['validated_bytes'], limit + 1)
                    self.assertEqual(stream.snapshot()['known_native_written_bytes'], limit + 1)
                    self.assertEqual(ledger.used, 0)
                self.assertLessEqual(ledger.peak, 16 + 512)

    def test_utf8_every_split_preserves_original_bytes_and_hash(self):
        payload = b'{ "a":"\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80" }\n'
        for split in range(1, len(payload)):
            with self.subTest(split=split):
                ledger, native, metadata = Ledger(), bytearray(), []
                def validate(graph, meta):
                    self.assertEqual(graph, {'a': '\u00e9\u20ac\U0001f600'})
                    graph['a'] = 'changed graph must not change spelling'
                    metadata.append(meta)
                    return True
                stream = InputCustody(validate, ledger.reserve, ledger.release)
                self.assertEqual(stream.offer(payload[:split], 0), {'custodied': split})
                self.assertEqual(metadata, [])
                self.assertEqual(stream.offer(payload[split:], 1), {'custodied': len(payload) - split})
                stream.end_input()
                def writer(view):
                    native.extend(view)
                    return len(view)
                while stream.snapshot()['state'] != 'drained':
                    stream.write_to(writer)
                self.assertEqual(native, payload)
                self.assertEqual(metadata[0]['raw_sha256'], hashlib.sha256(payload[:-1]).hexdigest())
                self.assertEqual(metadata[0]['first_byte_ns'], 0)
                self.assertEqual(ledger.used, 0)

    def test_multiline_offer_returns_only_first_line_custody_and_blocks_until_drain(self):
        ledger, native, documents = Ledger(), bytearray(), []
        def validate(graph, meta):
            documents.append(graph)
            return True
        stream = InputCustody(validate, ledger.reserve, ledger.release)
        original = bytearray(b'{}\n{"v":1}\n')
        loan = memoryview(original).toreadonly()
        try:
            self.assertEqual(stream.offer(loan, 0), {'custodied': 3})
            self.assertEqual(stream.offer(b'{}\n', 0), {'custodied': 0})
            original[3:] = b'{"v":2}\n'
            def writer(view):
                native.extend(view)
                return len(view)
            while stream.snapshot()['state'] == 'draining':
                stream.write_to(writer)
            self.assertEqual(native, b'{}\n')
            suffix = loan[3:]
            try:
                self.assertEqual(stream.offer(suffix, 0), {'custodied': 8})
            finally:
                suffix.release()
            stream.end_input()
            while stream.snapshot()['state'] != 'drained':
                stream.write_to(writer)
            self.assertEqual(native, b'{}\n{"v":2}\n')
            self.assertEqual(documents, [{}, {'v': 2}])
            self.assertEqual(ledger.used, 0)
        finally:
            loan.release()

    def test_partial_zero_and_eagain_preserve_actual_byte_progress(self):
        ledger, native = Ledger(), bytearray()
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
        stream.offer(b'{"a":1}\n', 0)
        stream.end_input()
        self.assertEqual(stream.write_to(lambda view: 0), 0)
        def blocked(view):
            raise BlockingIOError()
        self.assertEqual(stream.write_to(blocked), 0)
        self.assertEqual(stream.snapshot()['known_native_written_bytes'], 0)
        self.assertFalse(stream.snapshot()['native_outcome_unknown'])
        def partial(view):
            native.extend(view[:1])
            return 1
        while stream.snapshot()['state'] != 'drained':
            self.assertEqual(stream.write_to(partial), 1)
        self.assertEqual(native, b'{"a":1}\n')
        self.assertEqual(stream.snapshot()['known_native_written_bytes'], 8)
        self.assertEqual(ledger.used, 0)

    def test_unknown_native_outcome_preserves_primary_and_never_replays(self):
        ledger, native = Ledger(), bytearray()
        primary, secondary = OSError('write_unknown'), RuntimeError('cleanup_unknown')
        def release(count):
            ledger.releases.append(count)
            raise secondary
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, release)
        stream.offer(b'{"a":1}\n', 0)
        def uncertain(view):
            native.extend(view[:1])
            raise primary
        with self.assertRaises(OSError) as caught:
            stream.write_to(uncertain)
        self.assertIs(caught.exception, primary)
        snap = stream.snapshot()
        self.assertEqual(snap['state'], 'failed')
        self.assertTrue(snap['native_outcome_unknown'])
        self.assertTrue(snap['cleanup_unknown'])
        self.assertEqual(snap['known_native_written_bytes'], 0)
        self.assertEqual(native, b'{')
        releases = list(ledger.releases)
        with self.assertRaisesRegex(CustodyError, '^custody_unusable$'):
            stream.write_to(uncertain)
        stream.cancel()
        self.assertEqual(native, b'{')
        self.assertEqual(ledger.releases, releases)

    def test_cancellation_drops_owned_credit_in_collection_and_drain(self):
        for phase in ('collecting', 'draining', 'partly-written'):
            with self.subTest(phase=phase):
                ledger, native = Ledger(), bytearray()
                stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
                stream.offer(b'{"a":"\xf0' if phase == 'collecting' else b'{"a":1}\n', 0)
                if phase == 'partly-written':
                    def writer(view):
                        native.extend(view[:1])
                        return 1
                    self.assertEqual(stream.write_to(writer), 1)
                stream.cancel()
                snap = stream.snapshot()
                self.assertEqual(snap['state'], 'cancelled')
                self.assertEqual(snap['partial_original_bytes'], 0)
                self.assertEqual(snap['known_native_written_bytes'], len(native))
                self.assertFalse(snap['native_outcome_unknown'])
                self.assertEqual(ledger.used, 0)
                releases = list(ledger.releases)
                stream.cancel()
                self.assertEqual(ledger.releases, releases)
                with self.assertRaisesRegex(CustodyError, '^custody_unusable$'):
                    stream.write_to(lambda view: 0)

    def test_shared_credit_refusal_does_not_call_native_writer(self):
        class SharedLedger(Ledger):
            def reserve(self, count):
                if self.used + count > 2048:
                    return False
                return super().reserve(count)
        ledger, native, attempts = SharedLedger(), bytearray(), []
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
        payload = b'{"a":"' + b'x' * 200 + b'"}\n'
        self.assertTrue(ledger.reserve(len(payload)))  # Caller owns raw ingress loan.
        self.assertEqual(stream.offer(payload, 0), {'custodied': len(payload)})
        ledger.release(len(payload))
        self.assertTrue(ledger.reserve(1905))  # Other existing shared raw accounts.
        def writer(view):
            attempts.append(len(view))
            native.extend(view)
            return len(view)
        self.assertEqual(stream.write_to(writer), 0)
        self.assertEqual(attempts, [])
        self.assertEqual(stream.snapshot()['known_native_written_bytes'], 0)
        self.assertEqual(stream.snapshot()['raw_reserved_bytes'], 16)
        ledger.release(1905)
        stream.end_input()
        while stream.snapshot()['state'] != 'drained':
            self.assertGreater(stream.write_to(writer), 0)
        self.assertEqual(native, payload)
        self.assertEqual(ledger.used, 0)
        self.assertLessEqual(ledger.peak, 2048)

    def test_invalid_write_receipts_are_unknown_and_unusable(self):
        for receipt in (True, -1, 999, 1.5, None):
            with self.subTest(receipt=receipt):
                ledger = Ledger()
                stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
                stream.offer(b'{"a":1}\n', 0)
                with self.assertRaisesRegex(ValueError, '^forward_write_count$'):
                    stream.write_to(lambda view: receipt)
                self.assertTrue(stream.snapshot()['native_outcome_unknown'])
                self.assertEqual(stream.snapshot()['known_native_written_bytes'], 0)
                self.assertEqual(stream.snapshot()['state'], 'failed')
                self.assertEqual(ledger.used, 0)

    def test_final_scratch_cleanup_failure_is_not_successful_stream_completion(self):
        ledger, native = Ledger(), bytearray()
        fault = RuntimeError('scratch_release_fault')
        def release(count):
            if count == 16:
                ledger.releases.append(count)
                raise fault
            ledger.release(count)
        stream = InputCustody(lambda graph, meta: True, ledger.reserve, release)
        stream.offer(b'{}\n', 0)
        stream.end_input()
        def writer(view):
            native.extend(view)
            return len(view)
        self.assertEqual(stream.write_to(writer), 2)
        with self.assertRaises(RuntimeError) as caught:
            stream.write_to(writer)
        self.assertIs(caught.exception, fault)
        snap = stream.snapshot()
        self.assertEqual(native, b'{}\n')
        self.assertEqual(snap['known_native_written_bytes'], 3)
        self.assertFalse(snap['native_outcome_unknown'])
        self.assertTrue(snap['cleanup_unknown'])
        self.assertEqual(snap['state'], 'failed')

    def test_retained_early_errors_do_not_keep_exact_bytes_argument(self):
        # CPython reference counts observe this public borrowed-bytes lifetime.
        for state in ('cancelled', 'failed', 'drained', 'ended'):
            with self.subTest(state=state):
                ledger = Ledger()
                stream = InputCustody(lambda graph, meta: True, ledger.reserve, ledger.release)
                if state == 'cancelled':
                    stream.cancel()
                elif state == 'failed':
                    try:
                        stream.offer(b'\xff', 0)
                    except ValueError:
                        pass
                elif state == 'drained':
                    stream.end_input()
                else:
                    stream.offer(b'{}\n', 0)
                    stream.end_input()
                fragment = bytes(bytearray(b'{"entry":1}\n'))
                baseline = sys.getrefcount(fragment)
                retained = None
                try:
                    stream.offer(fragment, 1)
                except CustodyError as error:
                    retained = error
                self.assertIsNotNone(retained)
                self.assertEqual(str(retained), 'custody_input_ended' if state == 'ended' else 'custody_unusable')
                self.assertEqual(sys.getrefcount(fragment), baseline)
                self.assertEqual(stream.snapshot()['state'], 'draining' if state == 'ended' else state)
                stream.cancel()

    def test_retained_busy_error_does_not_drop_outer_guard_or_keep_inner_bytes(self):
        ledger, native = Ledger(), bytearray()
        def validate(graph, metadata):
            fragment = bytes(bytearray(b'{"inner":1}\n'))
            baseline = sys.getrefcount(fragment)
            retained = None
            try:
                stream.offer(fragment, 1)
            except CustodyError as error:
                retained = error
            self.assertIsNotNone(retained)
            self.assertEqual(str(retained), 'custody_busy')
            self.assertEqual(sys.getrefcount(fragment), baseline)
            with self.assertRaisesRegex(CustodyError, '^custody_busy$'):
                stream.cancel()
            return True
        stream = InputCustody(validate, ledger.reserve, ledger.release)
        self.assertEqual(stream.offer(b'{}\n', 0), {'custodied': 3})
        stream.end_input()
        def writer(view):
            native.extend(view)
            return len(view)
        while stream.snapshot()['state'] != 'drained':
            stream.write_to(writer)
        self.assertEqual(native, b'{}\n')
        self.assertEqual(ledger.used, 0)

    def test_cleanup_callbacks_remain_busy_during_cancel_and_final_drain(self):
        for phase in ('cancel', 'final-drain'):
            with self.subTest(phase=phase):
                ledger, reentry = Ledger(), []
                def release(count):
                    if count == 16:
                        try:
                            stream.cancel()
                        except CustodyError as error:
                            reentry.append(str(error))
                        else:
                            reentry.append('not_refused')
                    ledger.release(count)
                stream = InputCustody(lambda graph, meta: True, ledger.reserve, release)
                if phase == 'cancel':
                    stream.cancel()
                else:
                    stream.offer(b'{}\n', 0)
                    stream.end_input()
                    while stream.snapshot()['state'] != 'drained':
                        stream.write_to(lambda view: len(view))
                self.assertEqual(reentry, ['custody_busy'])
                self.assertEqual(ledger.used, 0)


if __name__ == '__main__':
    unittest.main()
