"""Pure syscall-boundary receipts; no actual descriptors, sockets or children."""
import errno
import sys
import unittest
from unittest.mock import patch

import trial_sdk_native_process_owner as process_module
import trial_sdk_native_input as input_module
from test_trial_sdk_command_input import descriptor_boundaries, process_boundaries


def after_released_loan(method, action, fault):
    fired = False
    def trace(frame, event, arg):
        nonlocal fired
        if event == 'line' and frame.f_code is method.__code__ and not fired:
            loan = frame.f_locals.get('loan')
            if loan is not None:
                try:
                    loan.nbytes
                except ValueError:
                    fired = True
                    raise fault
        return trace
    try:
        sys.settrace(trace)
        action()
    finally:
        sys.settrace(None)
    return fired


class IOReceiptTests(unittest.TestCase):
    def test_observed_eof_and_prior_eof_have_distinct_attempted_receipts(self):
        with process_boundaries(), patch.object(process_module.os, 'readv', return_value=0) as read:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            with memoryview(bytearray(8)) as slab:
                self.assertEqual(owner.read_into('stdout', slab), 0)
                self.assertEqual(owner.io_receipt(), (1, 'stdout', 8, True, 0, False))
                self.assertEqual(owner.read_into('stdout', slab), 0)
                self.assertEqual(owner.io_receipt(), (2, 'stdout', 8, False, None, False))
            self.assertEqual(read.call_count, 1)

    def test_generation_exhaustion_preserves_terminal_receipt_without_io(self):
        # Boundary seed only: executing 2**53 calls is neither bounded nor useful.
        # Real objects/public operations still enforce refusal and no syscall.
        terminal = (2**53-1, 'stdin', 2, True, 2, False)
        with process_boundaries(), patch.object(input_module.os, 'write') as write, \
                patch.object(process_module.os, 'readv') as read:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            owner._io = terminal
            with self.assertRaisesRegex(process_module.OwnerError, 'owner_io_sequence'):
                owner.write(b'ab')
            with memoryview(bytearray(8)) as slab:
                with self.assertRaisesRegex(process_module.OwnerError, 'owner_io_sequence'):
                    owner.read_into('stdout', slab)
            self.assertIs(owner.io_receipt(), terminal)
            original = input_module.NativeInput(739)
            original._receipt = terminal
            with self.assertRaisesRegex(input_module.NativeInputError, 'native_input_sequence'):
                original.write(b'ab')
            self.assertIs(original.write_receipt(), terminal)
            self.assertEqual(write.call_count, 0)
            self.assertEqual(read.call_count, 0)

    def test_input_reentrancy_foreign_process_and_closed_metadata_keep_original_receipt(self):
        with descriptor_boundaries():
            original = input_module.NativeInput(731)
            def write(fd, body):
                before = original.write_receipt()
                with self.assertRaisesRegex(input_module.NativeInputError, 'native_input_busy'):
                    original.write(b'x')
                self.assertIs(original.write_receipt(), before)
                return 2
            with patch.object(input_module.os, 'write', side_effect=write):
                self.assertEqual(original.write(b'ab'), 2)
            before = original.write_receipt()
            with patch.object(input_module.os, 'getpid', return_value=-1):
                with self.assertRaisesRegex(input_module.NativeInputError, 'native_input_busy'):
                    original.write(b'x')
                self.assertIs(original.write_receipt(), before)
            original.close()
            with patch.object(input_module.os, 'getpid', side_effect=AssertionError('probe')), \
                    patch.object(input_module.os, 'fstat', side_effect=AssertionError('probe')):
                self.assertIs(original.write_receipt(), before)

    def test_positive_read_and_known_block_keep_count_if_final_cleanup_is_interrupted(self):
        for syscall, expected in ((3, 3), (BlockingIOError(errno.EAGAIN, 'read'), 0)):
            with self.subTest(syscall=syscall), process_boundaries(), \
                    patch.object(process_module.os, 'readv', side_effect=[syscall]) as read:
                owner = process_module.NativeProcessOwner()
                owner.start(('/fixed-command',), '/fixed-cwd', {})
                fault = BlockingIOError(errno.EAGAIN, 'cleanup')
                fired = [False]
                def action():
                    with self.assertRaises(BlockingIOError) as caught:
                        with memoryview(bytearray(8)) as slab:
                            owner.read_into('stdout', slab)
                    self.assertIs(caught.exception, fault)
                    fired[0] = True
                self.assertTrue(after_released_loan(process_module.NativeProcessOwner.read_into,
                                                   action, fault))
                self.assertTrue(fired[0])
                self.assertEqual(owner.io_receipt(), (1, 'stdout', 8, True, expected, True))
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaisesRegex(process_module.OwnerError, 'owner_output_unavailable'):
                        owner.read_into('stdout', slab)
                self.assertEqual(read.call_count, 1)

    def test_write_cleanup_preserves_fresh_inner_prefix_in_both_input_modes(self):
        for allow in (False, True):
            with self.subTest(allow=allow), process_boundaries(), \
                    patch.object(input_module.os, 'write', return_value=2) as write:
                owner = process_module.NativeProcessOwner(allow_stdin_epipe=allow)
                owner.start(('/fixed-command',), '/fixed-cwd', {})
                def action():
                    with self.assertRaises(KeyboardInterrupt):
                        owner.write(b'abcd')
                self.assertTrue(after_released_loan(input_module.NativeInput.write,
                                                   action, KeyboardInterrupt()))
                self.assertEqual(owner.io_receipt(), (1, 'stdin', 4, True, 2, True))
                self.assertEqual(owner.snapshot()['native_input']['known_written'], 2)
                self.assertTrue(owner.snapshot()['native_input']['write_unknown'])
                with self.assertRaises(process_module.OwnerError):
                    owner.write(b'cd')
                self.assertEqual(write.call_count, 1)

    def test_outer_write_cleanup_failure_does_not_hide_completed_inner_write(self):
        with process_boundaries(), patch.object(input_module.os, 'write', return_value=2):
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fired = False
            def trace(frame, event, arg):
                nonlocal fired
                if (event == 'line' and frame.f_code is process_module.NativeProcessOwner.write.__code__
                        and frame.f_locals.get('count') == 2 and frame.f_locals.get('body') is None
                        and not fired):
                    fired = True
                    raise KeyboardInterrupt
                return trace
            try:
                sys.settrace(trace)
                with self.assertRaises(KeyboardInterrupt):
                    owner.write(b'abcd')
            finally:
                sys.settrace(None)
            self.assertTrue(fired)
            self.assertEqual(owner.io_receipt(), (1, 'stdin', 4, True, 2, True))
            self.assertFalse(owner.snapshot()['native_input']['write_unknown'])
            self.assertEqual(owner.snapshot()['primary'], 'owner_input_failed')

    def test_zero_read_close_failure_retains_observed_eof_without_retry(self):
        with process_boundaries(), patch.object(process_module.os, 'readv', return_value=0) as read:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            with patch.object(process_module.os, 'close', side_effect=OSError(errno.EINTR, 'close')) as close:
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaises(OSError):
                        owner.read_into('stdout', slab)
                    self.assertEqual(owner.io_receipt(), (1, 'stdout', 8, True, 0, True))
                    self.assertTrue(owner.snapshot()['stdout_eof'])
                    with self.assertRaises(process_module.OwnerError):
                        owner.read_into('stdout', slab)
                self.assertEqual(close.call_count, 1)
            self.assertEqual(read.call_count, 1)

    def test_unknown_syscall_return_is_none_not_zero_or_previous_prefix(self):
        for direction in ('read', 'write'):
            with self.subTest(direction=direction), process_boundaries():
                owner = process_module.NativeProcessOwner()
                owner.start(('/fixed-command',), '/fixed-cwd', {})
                with patch.object(process_module.os, 'readv', side_effect=[2, OSError(errno.EIO, 'read')]), \
                        patch.object(input_module.os, 'write', side_effect=[2, OSError(errno.EIO, 'write')]):
                    with memoryview(bytearray(8)) as slab:
                        operation = (lambda: owner.read_into('stdout', slab)) if direction == 'read' else (lambda: owner.write(b'abcd'))
                        self.assertEqual(operation(), 2)
                        with self.assertRaises((process_module.OwnerError, OSError)):
                            operation()
                    self.assertEqual(owner.io_receipt(),
                        (2, 'stdout' if direction == 'read' else 'stdin', 8 if direction == 'read' else 4,
                         True, None, True))

    def test_epipe_receipt_distinguishes_strict_unknown_rejection_and_repeated_refusal(self):
        for allow in (False, True):
            with self.subTest(allow=allow), descriptor_boundaries(), \
                    patch.object(input_module.os, 'write', side_effect=BrokenPipeError(errno.EPIPE, 'write')) as write:
                owner = input_module.NativeInput(731, allow_epipe=allow)
                with self.assertRaises(input_module.NativeInputRejected if allow else BrokenPipeError):
                    owner.write(b'ab')
                self.assertEqual(owner.write_receipt(), (1, 'stdin', 2, True, 0 if allow else None, not allow))
                with self.assertRaises(input_module.NativeInputError):
                    owner.write(b'ab')
                self.assertEqual(owner.write_receipt(), (2, 'stdin', None, False, None, False))
                self.assertEqual(write.call_count, 1)

    def test_reentrant_and_foreign_calls_do_not_replace_active_receipt(self):
        with process_boundaries():
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            def read(fd, views):
                before = owner.io_receipt()
                with self.assertRaisesRegex(process_module.OwnerError, 'owner_io_custody'):
                    owner.write(b'x')
                self.assertEqual(owner.io_receipt(), before)
                return 2
            with patch.object(process_module.os, 'readv', side_effect=read):
                with memoryview(bytearray(8)) as slab:
                    self.assertEqual(owner.read_into('stdout', slab), 2)
            before = owner.io_receipt()
            with patch.object(process_module.os, 'getpid', return_value=-1):
                with self.assertRaisesRegex(process_module.OwnerError, 'owner_io_custody'):
                    owner.write(b'x')
                self.assertEqual(owner.io_receipt(), before)
            owner.close_stdin()
            # Metadata remains readable without consulting descriptor or process state.
            with patch.object(process_module.os, 'getpid', side_effect=AssertionError('probe')), \
                    patch.object(process_module.os, 'fstat', side_effect=AssertionError('probe')):
                self.assertEqual(owner.io_receipt(), before)

    def test_write_receipt_binds_fresh_inner_call_and_never_reuses_old_count(self):
        with process_boundaries(), patch.object(input_module.os, 'write',
                side_effect=[2, BlockingIOError(errno.EAGAIN, 'write')]) as write:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            self.assertEqual(owner.write(b'abcd'), 2)
            self.assertEqual(owner.io_receipt(), (1, 'stdin', 4, True, 2, False))
            with self.assertRaises(BlockingIOError):
                owner.write(b'cd')
            self.assertEqual(owner.io_receipt(), (2, 'stdin', 2, True, 0, False))
            owner.close_stdin()
            with self.assertRaises(input_module.NativeInputError):
                owner.write(b'cd')
            self.assertEqual(owner.io_receipt(), (3, 'stdin', None, False, None, False))
            self.assertEqual(write.call_count, 2)

    def test_read_prefix_then_refusal_has_fresh_unattempted_receipt(self):
        with process_boundaries(), patch.object(process_module.os, 'readv', return_value=3) as read:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            with memoryview(bytearray(8)) as slab:
                self.assertEqual(owner.read_into('stdout', slab), 3)
                first = owner.io_receipt()
                self.assertEqual(first, (1, 'stdout', 8, True, 3, False))
                with self.assertRaisesRegex(process_module.OwnerError, 'owner_output_buffer'):
                    owner.read_into('stdout', b'invalid')
                self.assertEqual(owner.io_receipt(), (2, 'stdout', None, False, None, False))
                self.assertEqual(first, (1, 'stdout', 8, True, 3, False))
                self.assertEqual(read.call_count, 1)


if __name__ == '__main__':
    unittest.main()
