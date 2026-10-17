"""Actual ordinary child/socket/pidfd observations, not native CLI qualification."""
import os
from pathlib import Path
import signal
import sys
import time
import unittest
import json
from unittest.mock import patch

from trial_sdk_native_process_owner import NativeProcessOwner, OwnerError
from trial_sdk_input_receiver import InputReceiver

HERE = Path(__file__).parent
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
OBSERVATIONS = []


class OwnerTests(unittest.TestCase):
    def drain(self, owner):
        deadline = time.monotonic() + 2
        got = {'stdout': bytearray(), 'stderr': bytearray()}
        scratch = bytearray(512)
        while time.monotonic() < deadline:
            for name in got:
                if not owner.snapshot()[name+'_eof']:
                    with memoryview(scratch) as view:
                        n = owner.read_into(name, view)
                    if n > 0:
                        got[name].extend(scratch[:n])
            if owner.observe_wait() is not None and all(owner.snapshot()[n+'_eof'] for n in got):
                return got
            time.sleep(.001)
        self.fail('owned child wait/EOF deadline')

    def cleanup_owner(self, owner, expect_clean=True):
        if owner.snapshot()['release'] is not None:
            self.assertEqual(owner.snapshot()['release']['released'], expect_clean)
            self.assertTrue(owner.snapshot()['release']['handles_detached'])
            OBSERVATIONS.append(owner.snapshot())
            return
        if owner.snapshot()['pid'] is None:
            owner.cleanup_unlaunched()
            return
        deadline = time.monotonic() + 2
        if owner.snapshot()['pid'] is not None and owner.observe_wait() is None:
            owner.signal(2**53-1, int(signal.SIGKILL))
        owner.close_stdin()
        scratch = bytearray(512)
        while owner.observe_wait() is None and time.monotonic() < deadline:
            time.sleep(.001)
        for name in ('stdout', 'stderr'):
            while not owner.snapshot()[name+'_eof'] and time.monotonic() < deadline:
                with memoryview(scratch) as view:
                    owner.read_into(name, view)
        self.assertEqual(owner.release()['released'], expect_clean)
        OBSERVATIONS.append(owner.snapshot())

    def test_raw_wait_belongs_to_owned_child_and_eof_is_separate(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'exit'), str(HERE), ENV)
        target = owner.snapshot()['pid']
        self.assertGreater(target, 1)
        deadline = time.monotonic() + 2
        while owner.observe_wait() is None and time.monotonic() < deadline:
            time.sleep(.001)
        fact = owner.observe_wait()
        self.assertEqual(fact['pid'], target)
        self.assertEqual(fact['raw_status'], 7 << 8)
        self.assertEqual(fact['returncode'], 7)
        self.assertFalse(owner.snapshot()['stdout_eof'])
        with self.assertRaisesRegex(OwnerError, '^owner_release_pending$'):
            owner.release()
        owner.close_stdin()
        got = {'stdout': bytearray(), 'stderr': bytearray()}
        scratch = bytearray(512)
        for name in got:
            while not owner.snapshot()[name+'_eof'] and time.monotonic() < deadline:
                with memoryview(scratch) as view:
                    n = owner.read_into(name, view)
                if n > 0:
                    got[name].extend(scratch[:n])
                elif n == -1:
                    time.sleep(.001)
        self.assertEqual(got, {'stdout': b'out', 'stderr': b'err'})
        self.assertTrue(owner.release()['released'])
        self.assertEqual(owner.release(), owner.snapshot()['release'])

    def test_empty_argument_survives_native_launch_shape(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'empty', ''), str(HERE), ENV)
        owner.close_stdin()
        deadline = time.monotonic() + 2
        while owner.observe_wait() is None and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(owner.observe_wait()['returncode'], 0)

    def test_actual_receiver_counts_and_closes_owned_native_input(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'echo'), str(HERE), ENV)
        used = 0
        def reserve(n):
            nonlocal used
            if used+n > 528:
                return False
            used += n
            return True
        def release(n):
            nonlocal used
            self.assertLessEqual(n, used)
            used -= n
        receiver = InputReceiver(lambda graph, meta: graph == {'hello': 1}, reserve, release,
                                 owner.write, owner.close_stdin)
        for operation, body in enumerate((b'{"hello":', b'1}\n'), 1):
            receiver.receive(9, operation, body, time.monotonic_ns())
            self.assertEqual(receiver.reply, (140, operation, len(body)))
            self.assertEqual(receiver.advance(), 0)  # Reply still has local ownership.
            self.assertEqual(owner.snapshot()['native_input']['known_written'], 0)
            receiver.reply_sent()  # Explicit local protocol-completion testimony.
        while receiver.snapshot()['custody_state'] == 'draining':
            receiver.advance()
        receiver.receive(5, 3, b'', time.monotonic_ns())
        receiver.advance()
        self.assertEqual(receiver.reply, (133, 3, 12))
        self.assertTrue(owner.snapshot()['native_input']['closed_known'])
        receiver.reply_sent()
        self.assertEqual(self.drain(owner), {'stdout': b'{"hello":1}\n', 'stderr': b'done'})
        self.assertEqual(owner.snapshot()['native_input']['known_written'], 12)
        self.assertEqual(owner.observe_wait()['returncode'], 0)
        self.assertEqual(used, 0)

    def test_pidfd_signal_is_separate_from_actual_raw_wait(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'wait'), str(HERE), ENV)
        self.assertFalse(os.get_inheritable(owner.fileno('pidfd')))
        self.assertTrue(owner.signal(1, int(signal.SIGTERM)))
        self.assertIsNone(owner.snapshot()['wait'])
        owner.close_stdin()
        self.drain(owner)
        self.assertEqual(owner.observe_wait()['raw_status'], int(signal.SIGTERM))
        self.assertEqual(owner.observe_wait()['returncode'], -int(signal.SIGTERM))
        self.assertFalse(owner.signal(2, int(signal.SIGKILL)))
        self.assertTrue(owner.release()['released'])

    def test_blocked_input_preserves_actual_write_count(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'block'), str(HERE), ENV)
        scratch = bytearray(512)
        deadline = time.monotonic()+2
        while True:
            with memoryview(scratch) as view:
                n = owner.read_into('stdout', view)
            if n > 0:
                self.assertEqual(scratch[:n], b'ready')
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.001)
        body = b'x'*512
        total = 0
        for _ in range(6144):
            try:
                n = owner.write(body)
            except BlockingIOError:
                break
            self.assertGreater(n, 0)
            self.assertLessEqual(n, 512)
            total += n
        else:
            self.fail('owned undrained socket never backpressured within3MiB')
        self.assertGreater(total, 0)
        self.assertEqual(owner.snapshot()['native_input']['known_written'], total)
        self.assertIsNone(owner.snapshot()['primary'])
        owner.signal(1, int(signal.SIGKILL))
        owner.close_stdin()
        self.drain(owner)
        self.assertEqual(owner.observe_wait()['raw_status'], int(signal.SIGKILL))
        self.assertEqual(owner.snapshot()['native_input']['known_written'], total)

    def test_uncertain_close_never_retries_or_becomes_known_release(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner, False)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'exit'), str(HERE), ENV)
        self.drain(owner)
        original = os.close
        calls = []
        def post_close_fault(fd):
            calls.append(fd)
            original(fd)
            raise OSError(5, 'injected post-close uncertainty')
        with patch('trial_sdk_native_input.os.close', post_close_fault):
            with self.assertRaises(OSError):
                owner.close_stdin()
            with self.assertRaises(OwnerError):
                owner.close_stdin()
        self.assertEqual(len(calls), 1)
        self.assertTrue(owner.snapshot()['native_input']['close_unknown'])
        result = owner.cleanup_after_wait()
        self.assertFalse(result['released'])
        self.assertTrue(result['handles_detached'])
        self.assertEqual(owner.release(), result)
        self.assertEqual(owner.cleanup_after_wait(), result)

    def test_eof_does_not_manufacture_wait_or_release(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'eofwait'), str(HERE), ENV)
        scratch = bytearray(512)
        deadline = time.monotonic()+2
        while not all(owner.snapshot()[n+'_eof'] for n in ('stdout', 'stderr')):
            self.assertLess(time.monotonic(), deadline)
            for name in ('stdout', 'stderr'):
                with memoryview(scratch) as view:
                    n = owner.read_into(name, view)
                self.assertIn(n, (-1, 0))
            time.sleep(.001)
        self.assertIsNone(owner.observe_wait())
        with self.assertRaisesRegex(OwnerError, '^owner_release_pending$'):
            owner.release()
        owner.close_stdin()
        self.drain(owner)
        self.assertEqual(owner.observe_wait()['returncode'], 0)

    def test_child_stdio_and_bounded_original_descriptor_scan(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'fds'), str(HERE), ENV)
        owner.close_stdin()
        self.assertEqual(self.drain(owner), {'stdout': b'stdio-only', 'stderr': b''})
        self.assertEqual(owner.observe_wait()['returncode'], 0)

    def test_prelaunch_refusal_is_consumed_and_has_no_handles(self):
        owner = NativeProcessOwner()
        with self.assertRaisesRegex(OwnerError, '^owner_plan$'):
            owner.start(('relative-command',), str(HERE), ENV)
        self.assertIsNone(owner.snapshot()['pid'])
        self.assertFalse(owner.snapshot()['has_handles'])
        self.assertFalse(owner.snapshot()['launch_attempted'])
        with self.assertRaisesRegex(OwnerError, '^owner_launch_consumed$'):
            owner.start(('/usr/bin/python3',), str(HERE), ENV)
        self.assertTrue(owner.cleanup_unlaunched()['released'])

    def test_unused_owner_cleanup_permanently_closes_launch_gate(self):
        owner = NativeProcessOwner()
        self.assertTrue(owner.cleanup_unlaunched()['released'])
        with patch('trial_sdk_native_process_owner.subprocess.Popen', side_effect=AssertionError('unexpected launch after release')):
            with self.assertRaisesRegex(OwnerError, '^owner_launch_consumed$'):
                owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'exit'), str(HERE), ENV)

    def test_prelaunch_stdin_close_cannot_release_a_future_open_descriptor(self):
        owner = NativeProcessOwner()
        self.addCleanup(self.cleanup_owner, owner)
        with self.assertRaisesRegex(OwnerError, '^owner_input_unavailable$'):
            owner.close_stdin()
        owner.start(('/usr/bin/python3', '-I', '-B', str(HERE/'trial_sdk_native_process_owner_child.py'), 'exit'), str(HERE), ENV)
        self.drain(owner)
        self.assertFalse(owner.snapshot()['stdin_closed'])
        self.assertTrue(owner.snapshot()['has_handles'])
        with self.assertRaisesRegex(OwnerError, '^owner_release_pending$'):
            owner.release()
        owner.close_stdin()
        self.assertTrue(owner.release()['released'])
        self.assertFalse(owner.snapshot()['has_handles'])


if __name__ == '__main__':
    before_fds = len(os.listdir('/proc/self/fd'))
    result = unittest.main(exit=False)
    after_fds = len(os.listdir('/proc/self/fd'))
    print(json.dumps({'owned_children': OBSERVATIONS, 'fd_counts': [before_fds, after_fds],
                      'test_count': result.result.testsRun, 'successful': result.result.wasSuccessful()}, sort_keys=True))
    raise SystemExit(not result.result.wasSuccessful() or before_fds != after_fds)
