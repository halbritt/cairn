import os
import errno
import fcntl
import sys
from unittest.mock import patch
import trial_sdk_native_input as native_owner
import unittest
from trial_sdk_native_input import NativeInput
from trial_sdk_input_receiver import InputReceiver

class NativeOwnerTests(unittest.TestCase):
    def test_validated_original_bytes_and_known_close(self):
        readfd, writefd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owner = None
        try:
            owner = NativeInput(writefd)
            writefd = None  # Successful construction transfers sole write custody.
            held = [0]
            def reserve(n):
                held[0] += n
                return True
            def release(n):
                held[0] -= n
            receiver = InputReceiver(lambda graph, meta: True, reserve, release,
                                     owner.write, owner.close)
            wire = b'{ "x" : "original\\n" }\n'
            receiver.receive(9, 1, wire, 0)
            self.assertEqual(receiver.reply,
                             (140, 1, len(wire)))
            with self.assertRaises(BlockingIOError):
                os.read(readfd, 1)  # Acknowledged custody alone writes nothing.
            receiver.reply_sent()
            receiver.receive(5, 3, b'', 0)
            while receiver.reply is None:
                receiver.advance()
            self.assertEqual(receiver.reply, (133, 3, len(wire)))
            self.assertEqual(os.read(readfd, 512), wire)
            self.assertEqual(os.read(readfd, 1), b'')
            self.assertTrue(owner.snapshot()['closed_known'])
            self.assertFalse(owner.snapshot()['close_unknown'])
            self.assertEqual(held[0], 0)
        finally:
            if owner is not None:
                owner.close()
            if writefd is not None:
                os.close(writefd)
            os.close(readfd)


    def test_would_block_has_no_progress_and_can_resume(self):
        rd, wr = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owner = NativeInput(wr)
        try:
            capacity = fcntl.fcntl(rd, fcntl.F_GETPIPE_SZ)
            self.assertLessEqual(capacity, 1024*1024)
            filled = 0
            for _ in range(capacity//512 + 1):
                try:
                    filled += owner.write(b'A'*512)
                except BlockingIOError:
                    break
            else:
                self.fail('pipe did not reach observed capacity')
            self.assertEqual(owner.snapshot()['known_written'], filled)
            self.assertFalse(owner.snapshot()['write_unknown'])
            # Read enough to remove real pipe backpressure. A 512-byte read
            # alone need not release a kernel pipe-buffer slot.
            remaining = filled
            while remaining:
                chunk = os.read(rd, min(remaining, 512))
                self.assertGreater(len(chunk), 0)
                self.assertEqual(chunk, b'A'*len(chunk))
                remaining -= len(chunk)
            self.assertEqual(owner.write(b'B'*512), 512)
            self.assertEqual(owner.snapshot()['known_written'], filled+512)
            owner.close()
            self.assertEqual(os.read(rd, 512), b'B'*512)
            self.assertEqual(os.read(rd, 1), b'')
        finally:
            owner.close()
            os.close(rd)

    def test_uncertain_close_never_retries_reused_number(self):
        rd, wr = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owner = NativeInput(wr)
        actual_close = os.close
        replacements = []
        calls = []
        def uncertain(fd):
            calls.append(fd)
            actual_close(fd)
            replacements.extend(os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC))
            raise OSError(errno.EINTR, 'injected uncertain return')
        try:
            with patch.object(native_owner.os, 'close', uncertain):
                with self.assertRaises(OSError):
                    owner.close()
                with self.assertRaisesRegex(ValueError, 'native_input_close_unknown'):
                    owner.close()
            self.assertEqual(calls, [wr])
            self.assertIn(wr, replacements)
            for fd in replacements:
                os.fstat(fd)  # A reused FD remains live after second close refusal.
            self.assertTrue(owner.snapshot()['close_unknown'])
            self.assertFalse(owner.snapshot()['closed_known'])
            with self.assertRaisesRegex(ValueError, 'native_input_detached'):
                owner.fileno()
            with self.assertRaisesRegex(ValueError, 'native_input_unusable'):
                owner.write(b'x')
        finally:
            for fd in replacements:
                actual_close(fd)
            actual_close(rd)

    def test_configuration_refusal_does_not_take_descriptor(self):
        rd, wr = os.pipe2(os.O_CLOEXEC)
        try:
            flags = fcntl.fcntl(wr, fcntl.F_GETFL)
            with self.assertRaisesRegex(ValueError, 'native_input_configuration'):
                NativeInput(wr)
            self.assertEqual(fcntl.fcntl(wr, fcntl.F_GETFL), flags)
            os.set_blocking(rd, False)
            with self.assertRaisesRegex(ValueError, 'native_input_configuration'):
                NativeInput(rd)
            os.fstat(rd)
            os.set_blocking(wr, False)
            os.set_inheritable(wr, True)
            with self.assertRaisesRegex(ValueError, 'native_input_configuration'):
                NativeInput(wr)
            self.assertTrue(os.get_inheritable(wr))
        finally:
            os.close(rd)
            os.close(wr)

    def test_retained_error_does_not_retain_raw_body(self):
        rd, wr = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owner = NativeInput(wr)
        os.close(rd)
        raw = bytes(bytearray(b'x'*511))
        before = sys.getrefcount(raw)
        held_error = None
        try:
            try:
                owner.write(raw)
            except BrokenPipeError as error:
                held_error = error
            self.assertIsNotNone(held_error)
            self.assertEqual(sys.getrefcount(raw), before)
            self.assertTrue(owner.snapshot()['write_unknown'])
            self.assertEqual(owner.snapshot()['known_written'], 0)
            with self.assertRaisesRegex(ValueError, 'native_input_unusable'):
                owner.write(b'x')
        finally:
            owner.close()

    def test_invalid_complete_line_closes_without_native_bytes(self):
        rd, wr = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owner = NativeInput(wr)
        try:
            receiver = InputReceiver(lambda graph, meta: False,
                                     lambda n: True, lambda n: None,
                                     owner.write, owner.close)
            with self.assertRaises(ValueError):
                receiver.receive(9, 1, b'{"deny":true}\n', 0)
            self.assertIsNone(receiver.reply)
            self.assertEqual(owner.snapshot()['known_written'], 0)
            self.assertTrue(owner.snapshot()['closed_known'])
            self.assertEqual(os.read(rd, 1), b'')
        finally:
            owner.close()
            os.close(rd)

if __name__ == '__main__':
    unittest.main()
