"""Pure EOF-probe boundary cases; no actual socket, pidfd or child effects."""
import fcntl
import errno
import select
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trial_sdk_native_process_owner as owner_module
from test_trial_sdk_command_input import process_boundaries


class OutputEOFTests(unittest.TestCase):
    def test_drained_hangup_requires_actual_zero_read_before_eof(self):
        owner = owner_module.NativeProcessOwner()
        def available(fd, request, pending, mutate=True):
            pending[0] = 0
            return 0
        seen = []
        def read(fd, views):
            seen.append((fd, len(views[0])))
            return 0
        with process_boundaries():
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fd = owner.fileno('stdout')
            poll = SimpleNamespace(register=lambda *args: None,
                                   poll=lambda delay: [(fd, select.POLLHUP)])
            with patch.object(select, 'poll', return_value=poll), \
                    patch.object(fcntl, 'ioctl', side_effect=available), \
                    patch.object(owner_module.os, 'readv', side_effect=read):
                with memoryview(bytearray(512)) as slab:
                    self.assertEqual(owner.probe_output_eof('stdout', slab), 'eof')
                self.assertEqual(seen, [(fd, 1)])
            state = owner.snapshot()
            self.assertTrue(state['stdout_eof'])
            self.assertFalse(state['stderr_eof'])
            self.assertIsNone(state['wait'])
            with self.assertRaisesRegex(owner_module.OwnerError, '^owner_release_pending$'):
                owner.release()

    def test_queued_payload_or_live_empty_stream_never_triggers_probe_read(self):
        cases = ((0, 0, 'pending'), (select.POLLIN, 0, 'pending'),
                 (select.POLLIN, 1, 'data'), (select.POLLHUP, 1, 'data'),
                 (select.POLLRDHUP, 512, 'data'))
        for flags, queued, expected in cases:
            with self.subTest(flags=flags, queued=queued), process_boundaries():
                owner = owner_module.NativeProcessOwner()
                owner.start(('/fixed-command',), '/fixed-cwd', {})
                fd = owner.fileno('stdout')
                poll = SimpleNamespace(register=lambda *args: None,
                                       poll=lambda delay: [(fd, flags)] if flags else [])
                def available(fd, request, pending, mutate=True):
                    struct.pack_into('i', pending, 0, queued)
                    return 0
                with patch.object(select, 'poll', return_value=poll), \
                        patch.object(fcntl, 'ioctl', side_effect=available), \
                        patch.object(owner_module.os, 'readv', side_effect=AssertionError('payload read')) as read:
                    with memoryview(bytearray(512)) as slab:
                        self.assertEqual(owner.probe_output_eof('stdout', slab), expected)
                self.assertEqual(read.call_count, 0)
                self.assertFalse(owner.snapshot()['stdout_eof'])
                self.assertIsNone(owner.snapshot()['primary'])

    def test_unexpected_positive_probe_is_retained_and_terminal_not_eof(self):
        with process_boundaries():
            owner = owner_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fd = owner.fileno('stdout')
            poll = SimpleNamespace(register=lambda *args: None,
                                   poll=lambda delay: [(fd, select.POLLRDHUP)])
            def available(fd, request, pending, mutate=True):
                struct.pack_into('i', pending, 0, 0)
                return 0
            def unexpected_read(fd, views):
                views[0][0] = 120
                return 1
            with patch.object(select, 'poll', return_value=poll), \
                    patch.object(fcntl, 'ioctl', side_effect=available), \
                    patch.object(owner_module.os, 'readv', side_effect=unexpected_read) as read:
                with memoryview(bytearray(512)) as slab:
                    with self.assertRaisesRegex(owner_module.OwnerError, '^owner_output_probe_positive$'):
                        owner.probe_output_eof('stdout', slab)
                    for operation in (owner.probe_output_eof, owner.read_into):
                        with self.assertRaisesRegex(owner_module.OwnerError, '^owner_output_unavailable$'):
                            operation('stdout', slab)
                self.assertEqual(read.call_count, 1)
            state = owner.snapshot()
            self.assertFalse(state['stdout_eof'])
            self.assertEqual(state['primary'], 'owner_output_probe_unknown')
            self.assertEqual(state['output_probe_positive_bytes'], {'stdout': 1, 'stderr': 0})
            state['output_probe_positive_bytes']['stdout'] = 0
            self.assertEqual(owner.snapshot()['output_probe_positive_bytes']['stdout'], 1)

    def test_interrupted_zero_budget_probe_is_pending_until_actual_zero(self):
        with process_boundaries():
            owner = owner_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fd = owner.fileno('stdout')
            poll = SimpleNamespace(register=lambda *args: None,
                                   poll=lambda delay: [(fd, select.POLLRDHUP)])
            def available(fd, request, pending, mutate=True):
                struct.pack_into('i', pending, 0, 0)
                return 0
            with patch.object(select, 'poll', return_value=poll), \
                    patch.object(fcntl, 'ioctl', side_effect=available), \
                    patch.object(owner_module.os, 'readv',
                        side_effect=[InterruptedError(errno.EINTR, 'read'), 0]) as read:
                with memoryview(bytearray(512)) as slab:
                    self.assertEqual(owner.probe_output_eof('stdout', slab), 'pending')
                    self.assertFalse(owner.snapshot()['stdout_eof'])
                    self.assertEqual(owner.probe_output_eof('stdout', slab), 'eof')
                self.assertEqual(read.call_count, 2)
            self.assertTrue(owner.snapshot()['stdout_eof'])
            self.assertIsNone(owner.snapshot()['primary'])

    def test_probe_metadata_uncertainty_refuses_without_payload_read(self):
        for fault in (OSError(errno.EIO, 'ioctl'), KeyboardInterrupt()):
            with self.subTest(fault=type(fault).__name__), process_boundaries():
                owner = owner_module.NativeProcessOwner()
                owner.start(('/fixed-command',), '/fixed-cwd', {})
                fd = owner.fileno('stdout')
                poll = SimpleNamespace(register=lambda *args: None,
                                       poll=lambda delay: [(fd, select.POLLHUP)])
                with patch.object(select, 'poll', return_value=poll), \
                        patch.object(fcntl, 'ioctl', side_effect=fault), \
                        patch.object(owner_module.os, 'readv', side_effect=AssertionError('payload read')) as read:
                    with memoryview(bytearray(512)) as slab:
                        with self.assertRaises(type(fault)):
                            owner.probe_output_eof('stdout', slab)
                        with self.assertRaisesRegex(owner_module.OwnerError, '^owner_output_unavailable$'):
                            owner.probe_output_eof('stdout', slab)
                self.assertEqual(read.call_count, 0)
                self.assertFalse(owner.snapshot()['stdout_eof'])
                self.assertEqual(owner.snapshot()['primary'], 'owner_output_probe_unknown')

    def test_observed_zero_does_not_hide_close_uncertainty(self):
        with process_boundaries():
            owner = owner_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fd = owner.fileno('stdout')
            poll = SimpleNamespace(register=lambda *args: None,
                                   poll=lambda delay: [(fd, select.POLLRDHUP)])
            def available(fd, request, pending, mutate=True):
                struct.pack_into('i', pending, 0, 0)
                return 0
            with patch.object(select, 'poll', return_value=poll), \
                    patch.object(fcntl, 'ioctl', side_effect=available), \
                    patch.object(owner_module.os, 'close', side_effect=OSError(errno.EINTR, 'close')):
                with memoryview(bytearray(512)) as slab:
                    with self.assertRaises(OSError):
                        owner.probe_output_eof('stdout', slab)
            self.assertTrue(owner.snapshot()['stdout_eof'])  # Actual zero was observed.
            self.assertIn('stdout_close_unknown', owner.snapshot()['cleanup'])
            owner.close_stdin()
            with memoryview(bytearray(512)) as slab:
                owner.read_into('stderr', slab)
            owner.observe_wait()
            self.assertFalse(owner.release()['released'])

    def test_final_probe_cleanup_interruption_stays_terminal(self):
        with process_boundaries():
            owner = owner_module.NativeProcessOwner()
            owner.start(('/fixed-command',), '/fixed-cwd', {})
            fd = owner.fileno('stdout')
            poll = SimpleNamespace(register=lambda *args: None,
                                   poll=lambda delay: [(fd, select.POLLHUP)])
            def available(fd, request, pending, mutate=True):
                struct.pack_into('i', pending, 0, 0)
                return 0
            interrupted = False
            def trace(frame, event, arg):
                nonlocal interrupted
                if (event == 'line' and frame.f_code is owner_module.NativeProcessOwner.probe_output_eof.__code__
                        and frame.f_locals.get('count') == 0 and not interrupted):
                    try:
                        frame.f_locals['loan'].nbytes
                    except ValueError:
                        interrupted = True
                        raise KeyboardInterrupt
                return trace
            with patch.object(select, 'poll', return_value=poll), \
                    patch.object(fcntl, 'ioctl', side_effect=available):
                with memoryview(bytearray(512)) as slab:
                    try:
                        sys.settrace(trace)
                        with self.assertRaises(KeyboardInterrupt):
                            owner.probe_output_eof('stdout', slab)
                    finally:
                        sys.settrace(None)
                    self.assertTrue(interrupted)
                    self.assertEqual(owner.snapshot()['primary'], 'owner_output_probe_unknown')
                    self.assertTrue(owner.snapshot()['stdout_eof'])
                    with self.assertRaisesRegex(owner_module.OwnerError, '^owner_output_unavailable$'):
                        owner.probe_output_eof('stdout', slab)


if __name__ == '__main__':
    unittest.main()
