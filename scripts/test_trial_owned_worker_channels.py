"""Real ordinary child IPC; launcher command replaced at the Popen boundary only.

No namespaces, cgroups, native CLI, service, provider or production data access.
"""
import errno
import json
from itertools import product
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from trial_owned_workers import AttemptDomain, OwnedWorker, Unavailable

REAL_POPEN = subprocess.Popen
CHILD = r'''
import json, os, sys
status, membership = map(int, sys.argv[1:])
os.close(membership)
fds = []
for name in os.listdir('/proc/self/fd'):
    try:
        os.fstat(int(name))
        fds.append(int(name))
    except OSError:
        pass
os.write(status, json.dumps(sorted(fds)).encode() + b'\n')
os.write(1, b'ready\n')
data = b''
while True:
    block = os.read(0, 4096)
    if not block:
        break
    data += block
os.write(1, b'echo:' + data)
os.write(2, b'error\n')
os.write(status, b'done\n')
sys.exit(7)
'''


class Group:
    def __init__(self):
        self.file = tempfile.TemporaryFile()
        info = os.fstat(self.file.fileno())
        self.identity = (info.st_dev, info.st_ino)
        self.relative = '/ordinary-test-only'
        self.files = {'cgroup.procs': self.file.fileno()}
        self.bootstrap_child = None


class ChannelsTests(unittest.TestCase):
    def setUp(self):
        self.group = Group()
        self.child = None
        self.workers = []
        self.status_write = None
        self.buffers = {'stdout': b'', 'stderr': b'', 'status': b''}
        self.eof = set()
        self.patcher = patch('trial_owned_workers.subprocess.Popen', self.ordinary_popen)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.cleanup)

    def ordinary_popen(self, command, **kwargs):
        self.assertEqual(command[3:5], ['bwrap', '--json-status-fd'])
        self.status_write = int(command[5])
        self.assertEqual(kwargs['pass_fds'], (self.group.file.fileno(), self.status_write))
        self.assertTrue(kwargs['close_fds'])
        if self.group.bootstrap_child is not None:
            self.workers.append(self.group.bootstrap_child)
        return REAL_POPEN([sys.executable, '-c', CHILD, str(self.status_write),
                           str(self.group.file.fileno())], **kwargs)

    def cleanup(self):
        candidates = [self.child, self.group.bootstrap_child, *self.workers]
        seen = set()
        for child in candidates:
            if child is None or id(child) in seen:
                continue
            seen.add(id(child))
            if child.process is not None:
                self.assertIsInstance(child.process, REAL_POPEN)
                self.assertGreater(child.process.pid, 1)
                if child.observe_wait() is None:
                    try:
                        if child.pidfd is not None:
                            signal.pidfd_send_signal(child.pidfd, signal.SIGKILL)
                        else:
                            # Real, unreaped direct child; its PID cannot be reused.
                            os.kill(child.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 2
                    while child.observe_wait() is None and time.monotonic() < deadline:
                        time.sleep(.005)
                    if child.wait is None:
                        child.process.wait(timeout=2)
                        self.fail('owned child wait not observed')
            child.close()
        self.group.file.close()

    def start(self, **kwargs):
        self.child = OwnedWorker(self.group, [], **kwargs)
        self.pump(lambda: b'ready\n' in self.buffers['stdout'] and b'\n' in self.buffers['status'])
        return self.child

    def pump(self, done):
        child = self.child
        deadline = time.monotonic() + 2
        with selectors.DefaultSelector() as selector:
            for name, fd in [('stdout', child.process.stdout.fileno()),
                             ('stderr', child.process.stderr.fileno()), ('status', child.status_fd)]:
                if name not in self.eof:
                    selector.register(fd, selectors.EVENT_READ, name)
            while not done() and time.monotonic() < deadline:
                for key, _ in selector.select(.01):
                    block = os.read(key.fd, 4096)
                    if block:
                        self.buffers[key.data] += block
                        self.assertLess(sum(map(len, self.buffers.values())), 16384)
                    else:
                        self.eof.add(key.data)
                        selector.unregister(key.fd)
                child.observe_wait()
        self.assertTrue(done(), 'bounded ordinary child condition missing')

    def test_default_pipe_stdout_reopen_can_inject_canary(self):
        child = self.start()
        self.assertTrue(stat.S_ISFIFO(os.fstat(child.process.stdout.fileno()).st_mode))
        fd = os.open(f'/proc/{child.process.pid}/fd/1', os.O_WRONLY)
        try:
            os.write(fd, b'pipe-canary\n')
        finally:
            os.close(fd)
        self.pump(lambda: b'pipe-canary\n' in self.buffers['stdout'])

    def test_protected_stdout_stderr_and_status_cannot_be_reopened(self):
        child = self.start(channels='unix-stream/1')
        for fd in (0, 1, 2, self.status_write):
            with self.subTest(fd=fd), self.assertRaises(OSError) as caught:
                opened = os.open(f'/proc/{child.process.pid}/fd/{fd}', os.O_WRONLY)
                os.close(opened)
            self.assertEqual(caught.exception.errno, errno.ENXIO)

    def test_both_profiles_preserve_io_eof_wait_and_only_expected_inheritance(self):
        for profile in ('pipes/1', 'unix-stream/1'):
            with self.subTest(profile=profile):
                extra = os.open('/dev/null', os.O_RDONLY)
                os.set_inheritable(extra, True)
                before = len(os.listdir('/proc/self/fd'))
                try:
                    child = self.start(channels=profile)
                    self.assertEqual(len(os.listdir('/proc/self/fd')), before + 5)
                    inherited = json.loads(self.buffers['status'].split(b'\n')[0])
                    self.assertEqual(inherited, sorted([0, 1, 2, self.status_write]))
                    for fd in (child.process.stdin.fileno(), child.process.stdout.fileno(),
                               child.process.stderr.fileno(), child.status_fd):
                        self.assertFalse(os.get_inheritable(fd))
                        if profile == 'unix-stream/1':
                            self.assertTrue(stat.S_ISSOCK(os.fstat(fd).st_mode))
                            view = socket.socket(fileno=fd)
                            try:
                                self.assertEqual(view.family, socket.AF_UNIX)
                                self.assertEqual(view.type, socket.SOCK_STREAM)
                                self.assertEqual(view.getsockname(), '')
                                self.assertEqual(view.getpeername(), '')
                            finally:
                                view.detach()
                        else:
                            self.assertTrue(stat.S_ISFIFO(os.fstat(fd).st_mode))
                    self.assertEqual(child.process.stdin.write(b'payload'), 7)
                    child.process.stdin.flush()
                    child.process.stdin.close()
                    self.pump(lambda: len(self.eof) == 3 and child.wait is not None)
                    self.assertEqual(self.buffers['stdout'], b'ready\necho:payload')
                    self.assertEqual(self.buffers['stderr'], b'error\n')
                    self.assertTrue(self.buffers['status'].endswith(b'done\n'))
                    self.assertEqual(child.wait.pid, child.process.pid)
                    self.assertTrue(os.WIFEXITED(child.wait.raw_status))
                    self.assertEqual(child.wait.returncode, 7)
                    self.assertEqual(child.process.stdout.read1(1), b'')
                    self.assertEqual(child.process.stderr.read1(1), b'')
                    self.assertEqual(child.close(), [])
                    self.assertEqual(child.close(), [])
                    self.assertEqual(len(os.listdir('/proc/self/fd')), before)
                finally:
                    os.close(extra)
                self.child = None
                self.buffers = {'stdout': b'', 'stderr': b'', 'status': b''}
                self.eof = set()

    def test_profile_typo_refuses_without_allocating_or_spawning(self):
        before = len(os.listdir('/proc/self/fd'))
        with self.assertRaisesRegex(Unavailable, '^owned_worker_channel_profile$'):
            OwnedWorker(self.group, [], channels='unix-stream/2')
        self.assertIsNone(self.group.bootstrap_child)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_socket_allocation_and_popen_faults_retain_cleanup_without_leaks(self):
        original = socket.socketpair
        calls = 0
        def allocation_fault(*args):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError('selected allocation failure')
            return original(*args)
        for boundary, effect in (('socket.socketpair', allocation_fault),
                                 ('subprocess.Popen', OSError('selected Popen failure'))):
            before = len(os.listdir('/proc/self/fd'))
            with patch('trial_owned_workers.' + boundary, side_effect=effect):
                with self.assertRaisesRegex(Unavailable, '^owned_worker_setup_unknown$'):
                    OwnedWorker(self.group, [], channels='unix-stream/1')
            held = self.group.bootstrap_child
            self.assertIsNotNone(held)
            self.assertIsNone(held.process)
            self.assertIsNone(held.observe_wait())
            self.assertIn('owned_worker_setup_unknown', held.acquisition_observations)
            self.assertEqual(held.close(), [])
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)
            self.group.bootstrap_child = None

    def test_missing_endpoint_cookie_refuses_without_falling_back_to_pipes(self):
        before = len(os.listdir('/proc/self/fd'))
        with patch('trial_owned_workers.socket.socket.getsockopt',
                   side_effect=OSError(errno.ENOPROTOOPT, 'selected cookie unavailable')):
            with self.assertRaisesRegex(Unavailable, '^owned_worker_setup_unknown$'):
                OwnedWorker(self.group, [], channels='unix-stream/1')
        held = self.group.bootstrap_child
        self.assertIsNotNone(held)
        self.assertIsNone(held.process)
        self.assertIn('owned_worker_setup_unknown', held.acquisition_observations)
        self.assertEqual(held.close(), [])
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_pidfd_and_late_setup_faults_keep_the_actual_bootstrap_child(self):
        original = os.set_blocking
        for profile, fault in product(('pipes/1', 'unix-stream/1'),
                                      ('pidfd', 'first-stream', 'last-stream', 'unexpected')):
            if profile == 'pipes/1' and fault == 'unexpected':
                continue  # Legacy path only translates OSError; preserve it.
            calls = 0
            def setup(fd, blocking):
                nonlocal calls
                held = self.group.bootstrap_child
                if held is not None and held.process is not None:
                    self.assertGreater(held.process.pid, 1)
                    calls += 1
                    if calls == (3 if fault == 'last-stream' else 1):
                        if fault == 'unexpected':
                            raise RuntimeError('selected unexpected setup failure')
                        raise OSError('selected late setup failure')
                return original(fd, blocking)
            before = len(os.listdir('/proc/self/fd'))
            target = 'os.pidfd_open' if fault == 'pidfd' else 'os.set_blocking'
            effect = OSError('selected pidfd failure') if fault == 'pidfd' else setup
            expected = RuntimeError if fault == 'unexpected' else Unavailable
            try:
                with patch('trial_owned_workers.' + target, side_effect=effect):
                    with self.assertRaises(expected):
                        OwnedWorker(self.group, [], channels=profile)
                held = self.group.bootstrap_child
                self.assertIsNotNone(held)
                self.assertIsInstance(held.process, REAL_POPEN)
                self.assertIn('owned_worker_setup_unknown', held.acquisition_observations)
                if fault == 'pidfd':
                    self.assertIn('owned_launcher_handle_unknown', held.acquisition_observations)
            finally:
                self.cleanup()
                self.group = Group()
            self.assertIsNotNone(held.wait)
            self.assertEqual(held.wait.pid, held.process.pid)
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_status_writer_close_unknown_and_fd_reuse_are_never_treated_as_peer_identity(self):
        original = os.close
        for profile, mode in product(('pipes/1', 'unix-stream/1'),
                                     ('still-open', 'closed', 'file-reused', 'socket-reused')):
            with self.subTest(profile=profile, mode=mode):
                state = {'fired': False, 'sentinel': None, 'peer': None}
                before = len(os.listdir('/proc/self/fd'))
                def faulty_close(fd):
                    held = self.group.bootstrap_child
                    if held and held.process is not None and fd == held.pending_status_write and not state['fired']:
                        state['fired'] = True
                        self.assertEqual(os.fstat(fd).st_ino == os.fstat(held.status_fd).st_ino,
                                         profile == 'pipes/1')
                        if mode != 'still-open':
                            original(fd)
                        if mode.endswith('reused'):
                            if mode == 'file-reused':
                                fresh = os.open('/dev/null', os.O_RDWR)
                            else:
                                endpoint, state['peer'] = socket.socketpair()
                                fresh = endpoint.detach()
                            if fresh != fd:
                                os.dup2(fresh, fd, inheritable=False)
                                original(fresh)
                            state['sentinel'] = fd
                        raise OSError(errno.EIO, 'selected ambiguous close')
                    return original(fd)
                try:
                    with patch('trial_owned_workers.os.close', faulty_close):
                        with self.assertRaisesRegex(Unavailable, '^owned_worker_setup_unknown$'):
                            OwnedWorker(self.group, [], channels=profile)
                    held = self.group.bootstrap_child
                    self.assertTrue(state['fired'])
                    self.assertIsNotNone(held.process)
                    self.assertIn('worker_descriptor_close_unknown', held.acquisition_observations)
                    self.assertIn('worker_descriptor_close_unknown', held.close() + held.acquisition_observations)
                    self.assertIsNone(held.pending_status_write)
                    if state['sentinel'] is not None:
                        os.fstat(state['sentinel'])  # Must still belong to the unrelated holder.
                        if state['peer'] is not None:
                            os.write(state['sentinel'], b'unrelated')
                            self.assertEqual(state['peer'].recv(9), b'unrelated')
                finally:
                    self.cleanup()
                    if state['peer'] is not None:
                        state['peer'].close()
                    if state['sentinel'] is not None:
                        original(state['sentinel'])
                    self.group = Group()
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)


    def test_parent_stream_close_unknown_retains_identity_and_does_not_close_reused_fd(self):
        child = self.start(channels='unix-stream/1')
        target = child.process.stdout.fileno()
        original = os.close
        state = {'fired': False, 'sentinel': None}
        def close_and_reuse(fd):
            if fd == target and not state['fired']:
                state['fired'] = True
                original(fd)
                replacement = os.open('/dev/null', os.O_RDWR)
                if replacement != fd:
                    os.dup2(replacement, fd, inheritable=False)
                    original(replacement)
                state['sentinel'] = fd
                raise OSError(errno.EIO, 'selected parent close failure')
            return original(fd)
        try:
            with patch('trial_owned_workers.os.close', close_and_reuse):
                with self.assertRaisesRegex(OSError, 'worker_stream_close_unknown'):
                    child.process.stdout.close()
            self.assertIn('worker_descriptor_close_unknown', child.close())
            self.assertIsNotNone(os.fstat(state['sentinel']))
            self.assertIn('worker_descriptor_close_unknown', child.close())
            os.write(state['sentinel'], b'still unrelated')
        finally:
            if state['sentinel'] is not None:
                original(state['sentinel'])

    def test_identity_read_failure_keeps_parent_endpoint_for_later_cleanup(self):
        child = self.start(channels='unix-stream/1')
        target = child.process.stdin.fileno()
        original = os.fstat
        def unknown(fd):
            if fd == target:
                raise OSError(errno.EIO, 'selected identity observation failure')
            return original(fd)
        with patch('trial_owned_workers.os.fstat', unknown):
            with self.assertRaisesRegex(OSError, 'worker_stream_close_unknown'):
                child.process.stdin.close()
        self.assertTrue(stat.S_ISSOCK(os.fstat(target).st_mode))
        self.assertIn('worker_descriptor_close_unknown', child.close())
        with self.assertRaises(OSError) as caught:
            os.fstat(target)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_domain_cleanup_finds_failed_bootstrap_child_and_retains_real_wait(self):
        original = os.set_blocking
        def late_failure(fd, blocking):
            held = self.group.bootstrap_child
            if held is not None and held.process is not None:
                raise OSError('selected late setup failure')
            return original(fd, blocking)
        with patch('trial_owned_workers.os.set_blocking', late_failure):
            with self.assertRaises(Unavailable):
                OwnedWorker(self.group, [], channels='unix-stream/1')
        held = self.group.bootstrap_child
        self.assertIsNotNone(held.pidfd)
        # Mock only cgroup operations. The worker, pidfd, kill and wait are real;
        # no AttemptDomain constructor, namespace, service or cgroup is used.
        domain = object.__new__(AttemptDomain)
        domain.groups, domain.children = {'diagnostic': self.group}, {}
        domain.closed, domain.stopped = {}, {}
        self.group.kill = lambda: None
        self.group.populated = lambda: False
        self.group.events = lambda: {}
        self.group.close = lambda: []
        fact = domain.close_role('diagnostic', time.monotonic() + 2)
        self.assertEqual(fact['wait']['pid'], held.process.pid)
        self.assertEqual(fact['wait']['returncode'], -signal.SIGKILL)
        self.assertIn('owned_worker_setup_unknown', fact['errors'])
        self.assertNotIn('outer_wait_unknown', fact['errors'])
        self.assertIsNone(held.pidfd)

    def test_prespawn_cleanup_close_failure_keeps_selected_fault(self):
        before = len(os.listdir('/proc/self/fd'))
        original = os.close
        failed = False
        def once(fd):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(errno.EIO, 'selected pre-spawn close failure')
            return original(fd)
        with patch('trial_owned_workers.subprocess.Popen', side_effect=OSError('selected Popen failure')):
            with patch('trial_owned_workers.os.close', once):
                with self.assertRaisesRegex(Unavailable, '^owned_worker_setup_unknown$'):
                    OwnedWorker(self.group, [], channels='unix-stream/1')
        held = self.group.bootstrap_child
        self.assertIsNone(held.process)
        self.assertIn('worker_descriptor_close_unknown', held.acquisition_observations)
        self.assertIn('worker_descriptor_close_unknown', held.close())
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_protected_constructor_cannot_replace_an_existing_bootstrap_owner(self):
        original = os.set_blocking
        def late_failure(fd, blocking):
            held = self.group.bootstrap_child
            if held is not None and held.process is not None:
                raise OSError('selected retained bootstrap')
            return original(fd, blocking)
        with patch('trial_owned_workers.os.set_blocking', late_failure):
            with self.assertRaises(Unavailable):
                OwnedWorker(self.group, [], channels='unix-stream/1')
        held = self.group.bootstrap_child
        before = len(os.listdir('/proc/self/fd'))
        with self.assertRaisesRegex(Unavailable, '^owned_worker_bootstrap_held$'):
            OwnedWorker(self.group, [], channels='unix-stream/1')
        self.assertIs(self.group.bootstrap_child, held)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
