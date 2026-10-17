"""Caller allowance bounds both copied suffix and actual write offer."""
import sys
import inspect
import unittest
from unittest.mock import patch

from trial_coding_launch import compile_sdk_launch
from trial_sdk_control import SDKControlError
from trial_sdk_direct_writer import SDKDirectWriter, SDKDirectWriterError
from trial_sdk_native_process_owner import NativeProcessOwner
from test_trial_sdk_command_input import process_boundaries


class WriterAllowance(unittest.TestCase):
    def writer(self):
        launch = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000002',
            model='fixture-model', effort='high', prompt='Fixed bounded writer test.',
            memory_instruction='No source is opened.')
        owner = NativeProcessOwner()
        writer = SDKDirectWriter(launch.control, owner)
        return launch.control, owner, writer

    def test_partial_writes_copy_and_offer_only_the_current_allowance(self):
        with process_boundaries():
            control, owner, writer = self.writer()
            expected = control.pending_write()
            self.assertGreater(len(expected), 3)
            owner.start(('/inert',), '/inert', {})
            calls = []
            def write(fd, offered):
                frame = sys._getframe(1)
                while frame is not None and frame.f_code is not inspect.unwrap(SDKDirectWriter.advance).__code__:
                    frame = frame.f_back
                self.assertIsNotNone(frame)
                # Source lifetime observation: inspect the actual copied object
                # at the syscall boundary, not a mocked controller response.
                pending = frame.f_locals['pending']
                calls.append((len(pending), bytes(offered)))
                return min(2, len(offered))
            with patch('os.write', side_effect=write):
                self.assertEqual(writer.advance(3), 2)
                self.assertEqual(writer.advance(1), 1)
            self.assertEqual(calls, [(3, expected[:3]), (1, expected[2:3])])
            self.assertEqual(control.pending_write(), expected[3:])
            state = writer.snapshot()
            self.assertEqual(state['positive_write_bytes'], 3)
            self.assertEqual(state['controller_written_bytes'], 3)
            self.assertEqual(state['native_known_written_bytes'], 3)

    def test_invalid_allowance_has_no_write_or_controller_mutation(self):
        with process_boundaries():
            control, owner, writer = self.writer()
            owner.start(('/inert',), '/inert', {})
            before = control.ledger()
            with patch('os.write', side_effect=AssertionError('invalid allowance write')):
                for maximum in (0, -1, 513, True, None, 1.5):
                    with self.subTest(maximum=maximum):
                        with self.assertRaisesRegex(SDKDirectWriterError, 'sdk_direct_writer_limit'):
                            writer.advance(maximum)
                        self.assertEqual(control.ledger(), before)
                for maximum in (0, -1, 513, True, 1.5):
                    with self.subTest(controller_maximum=maximum):
                        with self.assertRaisesRegex(SDKControlError, 'sdk_write_limit'):
                            control.pending_write(maximum)
                        self.assertEqual(control.ledger(), before)
            self.assertIsNone(owner.io_receipt())

    def test_would_block_reoffers_identical_bounded_suffix_without_advancing(self):
        with process_boundaries():
            control, owner, writer = self.writer()
            expected = control.pending_write(7)
            owner.start(('/inert',), '/inert', {})
            offers = []
            def write(fd, body):
                offers.append(bytes(body))
                if len(offers) == 1:
                    raise BlockingIOError()
                return len(body)
            with patch('os.write', side_effect=write):
                self.assertEqual(writer.advance(7), 0)
                self.assertEqual(writer.advance(7), 7)
            self.assertEqual(offers, [expected, expected])
            self.assertEqual(writer.snapshot()['positive_write_bytes'], 7)


if __name__ == '__main__':
    unittest.main()
