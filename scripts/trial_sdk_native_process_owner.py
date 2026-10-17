"""Direct-child mechanism for trusted fixed builders; not launch admission.

The builder binds executable, argv/environment, namespace and capacity authority.
This module observes its own direct child only. It never adopts a reported PID,
outer launcher wait, payload identity, or borrowed stdio descriptor as that child.
All calls are serialized; no foreign waiter, FD mutation or retaining callbacks.
"""
import os
import signal
import socket
import subprocess

from trial_sdk_native_input import NativeInput, NativeInputError


class OwnerError(ValueError):
    pass


class NativeProcessOwner:
    def __init__(self):
        self._process = self._input = self._wait = self._release = None
        self._fds = {}
        self._consumed = self._launched = self._stdin_closed = False
        self._launch_attempted = self._stdin_close_unknown = False
        self._eof = {'stdout': False, 'stderr': False}
        self._read_unknown = set()
        self._wait_unknown = self._signal_unknown = False
        self._last_signal = 0
        self._primary = None
        self._cleanup = set()

    def _fail(self, code):
        if self._primary is None:
            self._primary = code
        raise OwnerError(code)

    def _close_fd(self, name):
        # Detach before one close attempt; uncertain raw numbers never retry.
        fd = self._fds.pop(name, None)
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                self._cleanup.add(name+'_close_unknown')
                raise

    @staticmethod
    def _plan(argv, cwd, env):
        if (type(argv) is not tuple or not 1 <= len(argv) <= 128
                or any(type(s) is not str or '\0' in s or len(s) > 4096 for s in argv)
                or not argv[0].startswith('/') or type(cwd) is not str or not cwd.startswith('/')
                or '\0' in cwd or len(cwd) > 4096 or type(env) is not dict or len(env) > 128
                or any(type(k) is not str or type(v) is not str or not k or '=' in k
                       or '\0' in k or '\0' in v or len(k)+len(v) > 4096 for k,v in env.items())):
            raise OwnerError('owner_plan')
        # Structural validation only. Trusted fixed builder supplies authority.

    def start(self, argv, cwd, env):
        if self._consumed:
            raise OwnerError('owner_launch_consumed')
        self._consumed = True
        try:
            self._plan(argv, cwd, env)
            for name in ('stdin', 'stdout', 'stderr'):
                parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
                self._fds[name] = parent.detach()
                self._fds['child_'+name] = child.detach()
                os.set_blocking(self._fds[name], False)
            # Popen retains direct-child ownership before any subsequent setup.
            # No pass_fds: only the selected stdio duplicates survive child exec.
            self._launch_attempted = True
            self._process = subprocess.Popen(argv, cwd=cwd, env=env, close_fds=True,
                stdin=self._fds['child_stdin'], stdout=self._fds['child_stdout'],
                stderr=self._fds['child_stderr'])
            self._fds['pidfd'] = os.pidfd_open(self._process.pid, 0)
            for name in ('stdin', 'stdout', 'stderr'):
                self._close_fd('child_'+name)
            self._input = NativeInput(self._fds['stdin'])
            del self._fds['stdin']  # Successful construction transfers sole custody.
            self._launched = True
        except BaseException:
            if self._primary is None:
                self._primary = ('owner_launch_unknown' if self._launch_attempted
                                 else 'owner_prelaunch_failed')
            # Keep the object and all acquired handles for explicit cleanup.
            raise

    def fileno(self, name):
        if name == 'stdin':
            if self._input is None:
                raise OwnerError('owner_input_unavailable')
            return self._input.fileno()
        if name not in ('stdout', 'stderr', 'pidfd') or name not in self._fds:
            raise OwnerError('owner_descriptor_unavailable')
        return self._fds[name]  # Readiness borrow only; no dup/close/rebind.

    def write(self, body):
        try:
            if not self._launched or self._primary is not None or self._input is None:
                raise OwnerError('owner_input_unavailable')
            return self._input.write(body)
        except BlockingIOError:
            raise
        except BaseException:
            if self._primary is None:
                self._primary = 'owner_input_failed'
            raise
        finally:
            body = None

    def close_stdin(self):
        if not self._launch_attempted:
            raise OwnerError('owner_input_unavailable')
        if self._stdin_close_unknown:
            raise OwnerError('owner_stdin_close_unknown')
        try:
            if self._input is not None:
                self._input.close()
            else:
                self._close_fd('stdin')
            self._stdin_closed = True
        except BaseException:
            self._stdin_close_unknown = True
            self._cleanup.add('stdin_close_unknown')
            raise

    def read_into(self, name, destination):
        loan = None
        try:
            if name not in self._eof or name in self._read_unknown:
                raise OwnerError('owner_output_unavailable')
            if (type(destination) is not memoryview or destination.readonly
                    or destination.ndim != 1 or destination.format != 'B'
                    or not destination.c_contiguous or not 0 < len(destination) <= 512):
                raise OwnerError('owner_output_buffer')
            if self._eof[name]:
                return 0
            if name not in self._fds:
                raise OwnerError('owner_output_unavailable')
            loan = memoryview(destination)
            try:
                count = os.readv(self._fds[name], [loan])
            except (BlockingIOError, InterruptedError):
                return -1
            except BaseException:
                self._read_unknown.add(name)
                self._fail('owner_output_unknown')
            if count == 0:
                self._eof[name] = True
                self._close_fd(name)
            return count
        finally:
            if loan is not None:
                loan.release()
            destination = None

    def signal(self, operation, number):
        if (type(operation) is not int or not self._last_signal < operation < 2**53
                or type(number) is not int or number not in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL)
                or self._signal_unknown or 'pidfd' not in self._fds):
            raise OwnerError('owner_signal_request')
        self._last_signal = operation
        if self._wait is not None:
            return False
        try:
            signal.pidfd_send_signal(self._fds['pidfd'], number, None, 0)
        except ProcessLookupError:
            return False  # Not a wait, nor proof that no earlier effect occurred.
        except BaseException:
            self._signal_unknown = True
            self._fail('owner_signal_unknown')
        return True  # Kernel accepted target signal; not death or wait evidence.

    def observe_wait(self):
        if self._wait_unknown:
            raise OwnerError('owner_wait_unknown')
        if self._wait is None and self._process is not None:
            try:
                pid, status = os.waitpid(self._process.pid, os.WNOHANG)
            except BaseException:
                self._wait_unknown = True
                self._fail('owner_wait_unknown')
            if pid:
                if pid != self._process.pid:
                    self._wait_unknown = True
                    self._fail('owner_wait_unknown')
                code = os.waitstatus_to_exitcode(status)
                self._process.returncode = code  # Prevent Popen from reaping again.
                self._wait = {'pid': pid, 'raw_status': status, 'returncode': code}
        return None if self._wait is None else dict(self._wait)

    def release(self):
        if self._release is not None:
            return dict(self._release)
        if self._wait is None or not self._stdin_closed or not all(self._eof.values()):
            raise OwnerError('owner_release_pending')
        return self.cleanup_after_wait()

    def cleanup_after_wait(self):
        if self._release is not None:
            return dict(self._release)
        if self._wait is None:
            raise OwnerError('owner_wait_pending')
        if not self._stdin_closed and not self._stdin_close_unknown:
            try:
                self.close_stdin()
            except BaseException:
                pass  # close_stdin records and quarantines that exact uncertainty.
        for name, observed in self._eof.items():
            if not observed:
                self._cleanup.add(name+'_eof_unobserved')
        for name in tuple(self._fds):
            try:
                self._close_fd(name)
            except BaseException:
                pass  # Sticky named uncertainty is retained in the result.
        self._release = {'released': not self._cleanup and self._stdin_closed,
                         'handles_detached': not self._fds,
                         'cleanup': tuple(sorted(self._cleanup))}
        return dict(self._release)

    def cleanup_unlaunched(self):
        if self._process is not None:
            raise OwnerError('owner_has_child')
        self._consumed = True
        if self._release is None:
            for name in tuple(self._fds):
                try:
                    self._close_fd(name)
                except BaseException:
                    pass  # Named close uncertainty remains sticky.
            self._release = {'released': not self._cleanup and not self._launch_attempted,
                             'launch_outcome_unknown': self._launch_attempted,
                             'handles_detached': not self._fds,
                             'cleanup': tuple(sorted(self._cleanup))}
        return dict(self._release)

    def snapshot(self):
        input_open = False
        if self._input is not None:
            try:
                self._input.fileno()
                input_open = True
            except NativeInputError:
                pass
        return {'pid': None if self._process is None else self._process.pid,
                'launched': self._launched, 'stdin_closed': self._stdin_closed,
                'launch_attempted': self._launch_attempted,
                'stdout_eof': self._eof['stdout'], 'stderr_eof': self._eof['stderr'],
                'wait': None if self._wait is None else dict(self._wait),
                'primary': self._primary, 'cleanup': tuple(sorted(self._cleanup)),
                'native_input': None if self._input is None else self._input.snapshot(),
                'signal_unknown': self._signal_unknown, 'wait_unknown': self._wait_unknown,
                'has_handles': bool(self._fds) or input_open,
                'release': None if self._release is None else dict(self._release),
                'qualification': 'unqualified'}
