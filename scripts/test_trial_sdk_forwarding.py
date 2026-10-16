"""Pure original-line forwarding contracts; injected credit and writer only."""
import errno
import hashlib
import sys
import unittest

from trial_sdk_incoming import (DecodedLine, IncomingError, LineAssembler,
                                MAX_DOCUMENT_BYTES)
from trial_sdk_forwarding import ForwardingError, OriginalLine


class Credit:
    def __init__(self, available=512):
        self.available = available
        self.live = self.peak = 0
        self.grants = []
        self.returns = []
        self.release_error = None

    def reserve(self, size):
        if size > self.available - self.live:
            return False
        self.live += size
        self.peak = max(self.peak, self.live)
        self.grants.append(size)
        return True

    def release(self, size):
        self.returns.append(size)
        if self.release_error:
            raise self.release_error
        self.live -= size


def decoded(raw, fragment=7):
    assembler = LineAssembler(MAX_DOCUMENT_BYTES)
    rows = []
    for i in range(0, len(raw), fragment):
        # Exhaust BEFORE this input chunk/reservation could be released.
        rows.extend(assembler.feed(raw[i:i + fragment], i))
    assembler.finish()
    return rows


def forwarding(raw, credit=None):
    credit = credit or Credit()
    line, = decoded(raw)
    return OriginalLine(line, credit.reserve, credit.release), credit


class OriginalLineTest(unittest.TestCase):
    def test_large_original_spelling_is_not_canonicalized(self):
        raw = (' \t{"n":1.00e+00,"text":"' + 'é😀\\n\\u0061' * 6000 + '"} \r\n').encode()
        self.assertGreater(len(raw), 70 * 1024)
        stream, credit = forwarding(raw)
        doc = stream.take_document()
        graph = doc.take()
        graph['text'] = 'changed independently'
        accepted = bytearray()
        def writer(view):
            self.assertTrue(view.readonly)
            self.assertGreaterEqual(credit.live, len(view))
            size = min(17, len(view))
            accepted.extend(view[:size])  # Test sink, outside production raw budget.
            return size
        while stream.snapshot()['state'] == 'open':
            stream.write_to(writer)
        self.assertEqual(bytes(accepted), raw)
        self.assertEqual(doc.raw_sha256, hashlib.sha256(raw[:-1]).hexdigest())
        self.assertEqual(stream.snapshot()['acknowledged_bytes'], len(raw))
        self.assertFalse(stream.snapshot()['text_retained'])
        self.assertLessEqual(credit.peak, 512)
        self.assertEqual(credit.grants, credit.returns)
        self.assertEqual(credit.live, 0)
        with self.assertRaises(ForwardingError):
            stream.take_document()
        with self.assertRaises(IncomingError):
            doc.take()

    def test_every_unicode_split_and_lf_only_after_original_cr(self):
        raw = b' {"x":"' + 'é€😀'.encode() + b'\\u0061"}\r\n'
        for split in range(1, len(raw)):
            with self.subTest(split=split):
                assembler = LineAssembler(MAX_DOCUMENT_BYTES)
                rows = list(assembler.feed(raw[:split], 11))
                rows += list(assembler.feed(raw[split:], 12))
                assembler.finish()
                credit = Credit()
                stream = OriginalLine(rows[0], credit.reserve, credit.release)
                output = []
                while stream.snapshot()['state'] == 'open':
                    stream.write_to(lambda view: output.append(bytes(view)) or len(view))
                self.assertEqual(b''.join(output), raw)
                self.assertEqual(output[-1], b'\n')
                self.assertEqual(stream.snapshot()['first_byte_ns'], 11)

    def test_credit_precedes_encoding_and_short_credit_allocates_nothing(self):
        credit = Credit(0)
        stream, _ = forwarding(b'{"x":"' + '😀'.encode() * 200 + b'"}\n', credit)
        encodes = []
        def profile(frame, event, arg):
            if event == 'c_call' and getattr(arg, '__name__', '') == 'encode':
                # Observe actual C encoding calls, not a mocked internal encoder.
                encodes.append(credit.live)
                self.assertGreater(credit.live, 0)
        old = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertEqual(stream.write_to(lambda _: self.fail('writer without credit')), 0)
            self.assertEqual(encodes, [])
            self.assertEqual(stream.snapshot()['pending_bytes'], 0)
            credit.available = 512
            while stream.snapshot()['state'] == 'open':
                stream.write_to(lambda view: len(view))
        finally:
            sys.setprofile(old)
        self.assertEqual(encodes, credit.grants)
        self.assertLessEqual(max(encodes), 512)

    def test_partial_write_and_backpressure_keep_same_allocation_until_drain(self):
        stream, credit = forwarding(b'{"a": 1}\n')
        owners = []
        def partial(view):
            owners.append(id(view.obj))
            return 1
        stream.write_to(partial)
        self.assertEqual(credit.live, 8)
        def blocked(view):
            owners.append(id(view.obj))
            raise BlockingIOError(errno.EAGAIN, 'synthetic')
        self.assertEqual(stream.write_to(blocked), 0)
        self.assertEqual(stream.snapshot()['acknowledged_bytes'], 1)
        self.assertEqual(credit.returns, [])
        stream.write_to(lambda view: owners.append(id(view.obj)) or len(view))
        self.assertTrue(all(item == owners[0] for item in owners))
        self.assertEqual(credit.returns, [8])
        self.assertTrue(stream.snapshot()['text_retained'])
        self.assertEqual(stream.write_to(lambda view: len(view)), 1)
        self.assertEqual(stream.snapshot()['state'], 'complete')

    def test_cancel_releases_text_chunk_and_unclaimed_graph_once(self):
        stream, credit = forwarding(b'{"a":[1]}\n')
        stream.write_to(lambda view: 1)
        stream.cancel()
        stream.cancel()
        self.assertEqual(stream.snapshot()['state'], 'cancelled')
        self.assertFalse(stream.snapshot()['text_retained'])
        self.assertEqual(credit.grants, credit.returns)
        with self.assertRaises(ForwardingError):
            stream.take_document()
        with self.assertRaises(ForwardingError):
            stream.write_to(lambda view: self.fail('cancelled write'))

    def test_transferred_graph_survives_cancellation_independently(self):
        stream, _ = forwarding(b'{"a":[1]}\n')
        document = stream.take_document()
        stream.cancel()
        self.assertEqual(document.take(), {'a': [1]})

    def test_writer_failure_preserves_primary_and_uncertain_release_without_retry(self):
        stream, credit = forwarding(b'{}\n')
        primary, cleanup = OSError('primary'), OSError('cleanup')
        credit.release_error = cleanup
        def broken(view):
            raise primary
        with self.assertRaises(OSError) as caught:
            stream.write_to(broken)
        self.assertIs(caught.exception, primary)
        self.assertEqual(stream.snapshot()['state'], 'failed')
        self.assertTrue(stream.snapshot()['cleanup_unknown'])
        self.assertFalse(stream.snapshot()['text_retained'])
        stream.cancel()
        self.assertEqual(credit.returns, [2])

    def test_bad_counts_and_ambiguous_partial_exception_are_terminal(self):
        for value in (None, True, -1, 3, 0.5, BlockingIOError(errno.EAGAIN, 'partial', 1)):
            with self.subTest(value=type(value).__name__):
                stream, credit = forwarding(b'{}\n')
                def writer(view):
                    if isinstance(value, BaseException):
                        raise value
                    return value
                with self.assertRaises((ForwardingError, BlockingIOError)):
                    stream.write_to(writer)
                self.assertEqual(stream.snapshot()['state'], 'failed')
                self.assertEqual(stream.snapshot()['acknowledged_bytes'], 0)
                self.assertEqual(credit.live, 0)

    def test_strict_refusals_before_any_credit_or_output(self):
        for raw in (b'{"a":1,"a":2}\n', b'{"n":1e309}\n', b'{"s":"\\ud800"}\n',
                    b'{} trailing\n', b'[]\n', b'{"n":' + b'9' * 310 + b'}\n',
                    b'{"x":"\xff"}\n', b'{}'):
            with self.subTest(raw=raw[:20]):
                credit = Credit()
                with self.assertRaises(IncomingError):
                    forwarding(raw, credit)
                self.assertEqual(credit.grants, [])

    def test_suspended_feed_pins_backing_until_generator_exhaustion(self):
        backing = bytearray(b'{}\n{"next":2}\n')
        assembler = LineAssembler(100)
        view = memoryview(backing)
        generator = assembler.feed(view, 7)
        first = next(generator)
        view.release()
        credit = Credit()
        stream = OriginalLine(first, credit.reserve, credit.release)
        del first
        while stream.snapshot()['state'] == 'open':
            stream.write_to(lambda piece: len(piece))
        # Finishing forwarding did not drain the independent feed generator.
        with self.assertRaises(BufferError):
            backing.extend(b'x')
        rest = list(generator)
        self.assertEqual(len(rest), 1)
        assembler.finish()
        backing.extend(b'x')
        self.assertTrue(backing.endswith(b'x'))

    def test_encode_release_failure_and_reentrancy_keep_line_closed(self):
        stream, credit = forwarding(b'{}\n')
        cleanup = OSError('release')
        credit.release_error = cleanup
        with self.assertRaises(OSError) as caught:
            stream.write_to(lambda view: len(view))
        self.assertIs(caught.exception, cleanup)
        self.assertEqual(stream.snapshot()['acknowledged_bytes'], 2)
        self.assertTrue(stream.snapshot()['cleanup_unknown'])
        self.assertEqual(credit.returns, [2])
        second, other = forwarding(b'{}\n')
        with self.assertRaises(ForwardingError):
            second.write_to(lambda view: second.cancel())
        self.assertEqual(second.snapshot()['state'], 'failed')
        self.assertEqual(other.live, 0)


if __name__ == '__main__':
    unittest.main()
