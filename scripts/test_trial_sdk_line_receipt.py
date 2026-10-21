"""Actual stream parsing with controlled read chunks and monotonic clock samples."""
import hashlib
import unittest
from unittest.mock import patch

from trial_sdk_incoming import LineAssembler, MAX_DOCUMENT_BYTES
from trial_sdk_stream_driver import SDKStreamDriver, StreamDriverError


class Reads:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read_into(self, name, slab):
        chunk = next(self.chunks)
        slab[:len(chunk)] = chunk
        return len(chunk)


class LineReceiptTests(unittest.TestCase):
    def pump(self, chunks):
        # No process or controller is needed for ordinary document input. Use
        # the production read/assembler/parser/clock path with a controlled IO.
        pump = SDKStreamDriver.__new__(SDKStreamDriver)
        pump._io = Reads(chunks)
        pump._stdout, pump._stderr = bytearray(512), bytearray(512)
        pump._assembler = LineAssembler(MAX_DOCUMENT_BYTES)
        pump._active = pump._lines = pump._clock = pump._retention = pump._selected_input = None
        pump._stdout_completed_ns = None
        pump._line_offset = 0
        pump._bytes = dict(stdout=0, stderr=0)
        pump._hash = {name: hashlib.sha256() for name in pump._bytes}
        return pump

    def test_split_utf8_line_uses_completion_not_first_byte(self):
        raw = '{"type":"user","message":"é"}\n'.encode()
        split = raw.index(b'\xc3') + 1
        pump = self.pump([raw[:split], raw[split:]])
        with patch('trial_sdk_stream_driver.time.monotonic_ns',
                   side_effect=[29_000_000_000, 29_100_000_000, 30_900_000_000, 31_000_000_000]):
            self.assertIsNone(pump._read_body('stdout'))
            event = pump._read_body('stdout')
        receipt = event.line_receipt
        self.assertEqual(receipt.first_byte_ns, 29_000_000_000)
        self.assertEqual(receipt.completed_ns, 31_000_000_000)
        self.assertEqual((receipt.start_offset, receipt.end_offset), (0, len(raw)))
        self.assertEqual(receipt.raw_sha256, hashlib.sha256(raw[:-1]).hexdigest())
        self.assertEqual(event.document.take()['message'], 'é')
        pump._drop_lines()

    def test_suspended_batch_keeps_stdout_clock_across_stderr_read(self):
        first, second = b'{"type":"user"}\n', b'{"type":"assistant"}\n'
        pump = self.pump([first + second, b'err'])
        with patch('trial_sdk_stream_driver.time.monotonic_ns', side_effect=[1, 2, 8, 9]):
            a = pump._read_body('stdout')
            pump._read_body('stderr')
            b = pump._line()
        self.assertEqual((a.line_receipt.completed_ns, b.line_receipt.completed_ns), (2, 2))
        self.assertEqual((b.line_receipt.start_offset, b.line_receipt.end_offset),
                         (len(first), len(first + second)))
        self.assertEqual(b.line_receipt.raw_sha256, hashlib.sha256(second[:-1]).hexdigest())
        self.assertIsNone(pump._line())

    def test_failed_completion_clock_keeps_raw_observation_without_receipt(self):
        raw = b'{"type":"user"}\n'
        pump = self.pump([raw])
        with patch('trial_sdk_stream_driver.time.monotonic_ns', side_effect=[2, 1]):
            with self.assertRaisesRegex(StreamDriverError, 'sdk_stream_clock_unknown'):
                pump._read_body('stdout')
        self.assertEqual(pump._bytes['stdout'], len(raw))
        self.assertEqual(pump._hash['stdout'].hexdigest(), hashlib.sha256(raw).hexdigest())
        self.assertIsNone(pump._lines)
        self.assertIsNone(pump._stdout_completed_ns)
