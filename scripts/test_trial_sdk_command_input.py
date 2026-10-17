"""Pure command-stdin boundary tests; no actual descriptors or child processes."""
import contextlib
import errno
import os
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trial_sdk_native_input as input_module
import trial_sdk_native_process_owner as process_module


@contextlib.contextmanager
def descriptor_boundaries():
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(input_module.os, 'fstat',
            return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)))
        stack.enter_context(patch.object(input_module.fcntl, 'fcntl',
            return_value=os.O_NONBLOCK | os.O_WRONLY))
        stack.enter_context(patch.object(input_module.os, 'get_inheritable', return_value=False))
        closed = stack.enter_context(patch.object(input_module.os, 'close'))
        yield closed


@contextlib.contextmanager
def process_boundaries():
    class Endpoint:
        def __init__(self, fd):
            self.fd = fd

        def detach(self):
            fd, self.fd = self.fd, None
            return fd

    with descriptor_boundaries() as closed, contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(process_module.socket, 'socketpair', side_effect=[
            (Endpoint(n), Endpoint(n + 1)) for n in (731, 733, 735)]))
        stack.enter_context(patch.object(process_module.os, 'set_blocking'))
        stack.enter_context(patch.object(process_module.subprocess, 'Popen',
            return_value=SimpleNamespace(pid=123, returncode=None)))
        stack.enter_context(patch.object(process_module.os, 'pidfd_open', return_value=737))
        stack.enter_context(patch.object(process_module.os, 'readv', return_value=0))
        stack.enter_context(patch.object(process_module.os, 'waitpid', return_value=(123, 7 << 8)))
        yield closed


class CommandInputTests(unittest.TestCase):
    def test_command_epipe_preserves_prefix_and_refuses_suffix_without_replay(self):
        with descriptor_boundaries(), patch.object(input_module.os, 'write',
                side_effect=[2, BrokenPipeError(errno.EPIPE, 'controlled')]) as write:
            owner = input_module.NativeInput(731, allow_epipe=True)
            self.assertEqual(owner.write(b'abcd'), 2)
            for _ in range(2):
                with self.assertRaisesRegex(input_module.NativeInputRejected, '^native_input_rejected$'):
                    owner.write(b'cd')
            self.assertEqual(write.call_count, 2)
            state = owner.snapshot()
            self.assertEqual(state['known_written'], 2)
            self.assertTrue(state['input_rejected'])
            self.assertFalse(state['write_unknown'])
            self.assertFalse(state['closed_known'])
            owner.close()
            self.assertTrue(owner.snapshot()['closed_known'])

    def test_owned_command_rejection_allows_waited_nonzero_and_known_release(self):
        with process_boundaries(), patch.object(input_module.os, 'write',
                side_effect=[2, BrokenPipeError(errno.EPIPE, 'controlled')]) as write:
            owner = process_module.NativeProcessOwner(allow_stdin_epipe=True)
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            self.assertEqual(owner.write(b'abcd'), 2)
            for _ in range(2):
                with self.assertRaises(input_module.NativeInputRejected):
                    owner.write(b'cd')
            self.assertEqual(write.call_count, 2)
            state = owner.snapshot()
            self.assertTrue(state['input_rejected'])
            self.assertIsNone(state['primary'])
            self.assertEqual(state['native_input']['known_written'], 2)
            self.assertFalse(state['native_input']['write_unknown'])
            self.assertFalse(state['stdin_closed'])
            owner.close_stdin()
            with self.assertRaisesRegex(process_module.OwnerError, '^owner_release_pending$'):
                owner.release()
            with memoryview(bytearray(512)) as slab:
                self.assertEqual(owner.read_into('stdout', slab), 0)
                self.assertEqual(owner.read_into('stderr', slab), 0)
            self.assertEqual(owner.observe_wait(), {'pid': 123, 'raw_status': 7 << 8, 'returncode': 7})
            self.assertTrue(owner.release()['released'])
            self.assertFalse(owner.snapshot()['has_handles'])
            self.assertTrue(owner.snapshot()['input_rejected'])
            with self.assertRaisesRegex(process_module.OwnerError, '^owner_launch_consumed$'):
                owner.start(('/fixed-command',), '/fixed-cwd', {})

    def test_default_sdk_epipe_is_unknown_and_poisoned(self):
        with process_boundaries(), patch.object(input_module.os, 'write',
                side_effect=[2, BrokenPipeError(errno.EPIPE, 'controlled')]) as write:
            owner = process_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            self.assertEqual(owner.write(b'abcd'), 2)
            with self.assertRaises(BrokenPipeError):
                owner.write(b'cd')
            state = owner.snapshot()
            self.assertEqual(state['primary'], 'owner_input_failed')
            self.assertEqual(state['native_input']['known_written'], 2)
            self.assertTrue(state['native_input']['write_unknown'])
            self.assertFalse(state['input_rejected'])
            with self.assertRaisesRegex(process_module.OwnerError, '^owner_input_unavailable$'):
                owner.write(b'cd')
            self.assertEqual(write.call_count, 2)
            owner.close_stdin()

    def test_other_errors_remain_unknown_in_command_mode(self):
        errors = (InterruptedError(errno.EINTR, 'controlled'),
                  OSError(errno.EBADF, 'controlled'), OSError(errno.ENOSPC, 'controlled'),
                  BrokenPipeError(errno.EIO, 'controlled'), BrokenPipeError(), KeyboardInterrupt())
        for error in errors:
            with self.subTest(error=type(error).__name__, errno=getattr(error, 'errno', None)), \
                    descriptor_boundaries(), patch.object(input_module.os, 'write',
                        side_effect=[2, error]) as write:
                owner = input_module.NativeInput(731, allow_epipe=True)
                self.assertEqual(owner.write(b'abcd'), 2)
                with self.assertRaises(type(error)):
                    owner.write(b'cd')
                self.assertEqual(owner.snapshot()['known_written'], 2)
                self.assertTrue(owner.snapshot()['write_unknown'])
                self.assertFalse(owner.snapshot()['input_rejected'])
                with self.assertRaisesRegex(input_module.NativeInputError, '^native_input_unusable$'):
                    owner.write(b'cd')
                self.assertEqual(write.call_count, 2)
                owner.close()

    def test_command_write_accounting_interruption_retains_unknown(self):
        owner = None
        interrupted = False
        def trace(frame, event, arg):
            nonlocal interrupted
            if (event == 'line' and frame.f_code is input_module.NativeInput.write.__code__
                    and frame.f_locals.get('count') == 2 and not interrupted):
                interrupted = True
                raise KeyboardInterrupt
            return trace
        with descriptor_boundaries(), patch.object(input_module.os, 'write', return_value=2) as write:
            owner = input_module.NativeInput(731, allow_epipe=True)
            try:
                sys.settrace(trace)
                with self.assertRaises(KeyboardInterrupt):
                    owner.write(b'abcd')
            finally:
                sys.settrace(None)
            self.assertTrue(interrupted)
            self.assertTrue(owner.snapshot()['write_unknown'])
            self.assertFalse(owner.snapshot()['input_rejected'])
            with self.assertRaisesRegex(input_module.NativeInputError, '^native_input_unusable$'):
                owner.write(b'abcd')
            self.assertEqual(write.call_count, 1)
            owner.close()

    def test_command_would_block_can_resume_without_rejection(self):
        with descriptor_boundaries(), patch.object(input_module.os, 'write',
                side_effect=[BlockingIOError(errno.EAGAIN, 'controlled'), 2]):
            owner = input_module.NativeInput(731, allow_epipe=True)
            with self.assertRaises(BlockingIOError):
                owner.write(b'ab')
            self.assertEqual(owner.snapshot()['known_written'], 0)
            self.assertFalse(owner.snapshot()['write_unknown'])
            self.assertFalse(owner.snapshot()['input_rejected'])
            self.assertEqual(owner.write(b'ab'), 2)
            self.assertEqual(owner.snapshot()['known_written'], 2)
            owner.close()

    def test_rejection_does_not_hide_uncertain_stdin_close_or_retry_it(self):
        with process_boundaries(), patch.object(input_module.os, 'write',
                side_effect=BrokenPipeError(errno.EPIPE, 'controlled')):
            owner = process_module.NativeProcessOwner(allow_stdin_epipe=True)
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            with self.assertRaises(input_module.NativeInputRejected):
                owner.write(b'body')
            with patch.object(input_module.os, 'close', side_effect=OSError(errno.EINTR, 'controlled')) as close:
                with self.assertRaises(OSError):
                    owner.close_stdin()
                with self.assertRaisesRegex(process_module.OwnerError, '^owner_stdin_close_unknown$'):
                    owner.close_stdin()
                self.assertEqual(close.call_count, 1)
            with memoryview(bytearray(512)) as slab:
                owner.read_into('stdout', slab)
                owner.read_into('stderr', slab)
            owner.observe_wait()
            release = owner.cleanup_after_wait()
            self.assertFalse(release['released'])
            self.assertIn('stdin_close_unknown', release['cleanup'])
            state = owner.snapshot()
            self.assertTrue(state['input_rejected'])
            self.assertEqual(state['native_input']['known_written'], 0)
            self.assertTrue(state['native_input']['close_unknown'])
            self.assertFalse(state['native_input']['closed_known'])


if __name__ == '__main__':
    unittest.main()
