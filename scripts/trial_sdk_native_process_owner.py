"""Direct-child mechanism for trusted fixed builders; not launch admission.

The builder binds executable, argv/environment, namespace and capacity authority.
This module observes its own direct child only. It never adopts a reported PID,
outer launcher wait, payload identity, or borrowed stdio descriptor as that child.
All calls are serialized; no foreign waiter, FD mutation or retaining callbacks.
"""
import fcntl
import os
import select
import signal
import socket
import subprocess
import struct
import termios

from trial_sdk_native_input import NativeInput, NativeInputError, NativeInputRejected


class OwnerError(ValueError):
    pass


class NativeProcessOwner:
    def __init__(self, *, allow_stdin_epipe=False):
        # Fixed command builders may select known stdin rejection, never SDK callers.
        if type(allow_stdin_epipe) is not bool:
            raise OwnerError('owner_input_policy')
        self._allow_stdin_epipe = allow_stdin_epipe
        self._process = self._input = self._wait = self._release = None
        self._fds = {}
        self._consumed = self._launched = self._stdin_closed = False
        self._launch_attempted = self._stdin_close_unknown = False
        self._eof = {'stdout': False, 'stderr': False}
        self._read_unknown = set()
        self._probe_positive = {'stdout': 0, 'stderr': 0}
        self._wait_unknown = self._signal_unknown = False
        self._last_signal = 0
        self._primary = None
        self._cleanup = set()
        self._io_creator = os.getpid()
        self._io_busy = False
        self._io = None
        self._write_generation = None

    def io_receipt(self):
        """Latest local I/O observation; memory only, not delivery or authority."""
        if self._write_generation is not None:
            inner = self._input.write_receipt()
            if inner is not None and inner[0] == self._write_generation:
                return (self._io[0], 'stdin', inner[2], inner[3], inner[4],
                        self._io[5] or inner[5])
        return self._io

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
            self._input = NativeInput(self._fds['stdin'], allow_epipe=self._allow_stdin_epipe)
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
        if os.getpid() != self._io_creator or self._io_busy:
            raise OwnerError('owner_io_custody')
        if self._io is not None and self._io[0] == 2**53-1:
            raise OwnerError('owner_io_sequence')
        acquired = entered = False
        known_error = None
        try:
            try:
                self._io_busy = acquired = True
                self._write_generation = None
                self._io = (1 if self._io is None else self._io[0]+1,
                            'stdin', None, False, None, False)
                if not self._launched or self._primary is not None or self._input is None:
                    raise OwnerError('owner_input_unavailable')
                before = self._input.write_receipt()
                self._write_generation = 1 if before is None else before[0]+1
                entered = True
                try:
                    count = self._input.write(body)
                except (BlockingIOError, NativeInputRejected) as error:
                    observed = self.io_receipt()
                    if not observed[5] and (not observed[3] or observed[4] == 0):
                        known_error = error
                    raise
            finally:
                body = None
                if acquired:
                    self._io_busy = False
            return count
        except BaseException as error:
            if acquired and entered and error is not known_error:
                observed = self.io_receipt()
                if observed[3] or self._input.write_receipt() is None:
                    self._io = (*self._io[:5], True)
            if not isinstance(error, (BlockingIOError, NativeInputRejected)) and self._primary is None:
                self._primary = 'owner_input_failed'
            body = None
            if acquired:
                self._io_busy = False
            raise

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
        if os.getpid() != self._io_creator or self._io_busy:
            raise OwnerError('owner_io_custody')
        if self._io is not None and self._io[0] == 2**53-1:
            raise OwnerError('owner_io_sequence')
        loan = count = None
        acquired = False
        try:
            try:
                self._io_busy = acquired = True
                self._write_generation = None
                self._io = (1 if self._io is None else self._io[0]+1,
                            name if type(name) is str and name in self._eof else 'invalid',
                            None, False, None, False)
                if type(name) is not str or name not in self._eof or name in self._read_unknown:
                    raise OwnerError('owner_output_unavailable')
                if (type(destination) is not memoryview or destination.readonly
                        or destination.ndim != 1 or destination.format != 'B'
                        or not destination.c_contiguous or not 0 < len(destination) <= 512):
                    raise OwnerError('owner_output_buffer')
                self._io = (*self._io[:2], len(destination), False, None, False)
                if self._eof[name]:
                    return 0  # Prior EOF, not a new syscall/count observation.
                if name not in self._fds:
                    raise OwnerError('owner_output_unavailable')
                loan = memoryview(destination)
                self._io = (*self._io[:3], True, None, False)
                try:
                    count = os.readv(self._fds[name], [loan])
                except (BlockingIOError, InterruptedError):
                    count = 0
                    self._io = (*self._io[:3], True, count, False)
                    return -1
                except BaseException:
                    self._fail('owner_output_unknown')
                if type(count) is not int or not 0 <= count <= len(loan):
                    count = None
                    raise OwnerError('owner_output_count')
                self._io = (*self._io[:3], True, count, False)
                if count == 0:
                    self._eof[name] = True
                    self._close_fd(name)
            finally:
                if loan is not None:
                    loan.release()
                destination = None
                if acquired:
                    self._io_busy = False
            return count
        except BaseException:
            if acquired and self._io is not None and self._io[3]:
                known = count if type(count) is int and 0 <= count <= self._io[2] else self._io[4]
                self._io = (*self._io[:4], known, True)
                self._read_unknown.add(name)
                if self._primary is None:
                    self._primary = 'owner_output_unknown'
            destination = None
            if acquired:
                self._io_busy = False
            raise

    def probe_output_eof(self, name, scratch):
        """At exhausted payload credit, observe EOF only on a drained hung-up stream.

        Uses the caller's existing scratch, including four bytes for FIONREAD.
        'data' observes queued bytes without consuming them; 'pending' is not EOF.
        Requires the owner's original AF_UNIX stream and exclusive reader custody.
        """
        if name not in self._eof or name in self._read_unknown:
            raise OwnerError('owner_output_unavailable')
        if (type(scratch) is not memoryview or scratch.readonly or scratch.ndim != 1
                or scratch.format != 'B' or not scratch.c_contiguous
                or not 4 <= len(scratch) <= 512 or struct.calcsize('i') != 4):
            raise OwnerError('owner_output_buffer')
        if self._eof[name]:
            return 'eof'
        fd = self.fileno(name)
        loan = None
        try:
            try:
                loan = memoryview(scratch)
                poll = select.poll()
                poll.register(fd, select.POLLIN | select.POLLRDHUP | select.POLLHUP)
                events = poll.poll(0)
                flags = 0
                for observed_fd, observed_flags in events:
                    if (observed_fd != fd or flags or type(observed_flags) is not int
                            or observed_flags & ~(select.POLLIN | select.POLLRDHUP | select.POLLHUP)):
                        raise OwnerError('owner_output_probe_readiness')
                    flags = observed_flags
                with loan[:4] as pending:
                    result = fcntl.ioctl(fd, termios.FIONREAD, pending, True)
                    queued = struct.unpack_from('i', pending)[0]
                if type(result) is not int or result != 0 or queued < 0:
                    raise OwnerError('owner_output_probe_queue')
                if queued:
                    return 'data'
                if not flags & (select.POLLRDHUP | select.POLLHUP):
                    return 'pending'
                # A permanent read-side hangup and empty queue admit no new data
                # under the selected exclusive-reader/unchanged-descriptor contract.
                with loan[:1] as probe:
                    count = self.read_into(name, probe)
                if count > 0:
                    self._probe_positive[name] += count
                    raise OwnerError('owner_output_probe_positive')
                return 'eof' if count == 0 else 'pending'
            finally:
                if loan is not None:
                    loan.release()
                scratch = None
        except BaseException:
            self._read_unknown.add(name)
            if self._primary is None:
                self._primary = 'owner_output_probe_unknown'
            raise

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
                'last_io': self.io_receipt(),
                'input_rejected': self._input is not None and self._input.snapshot()['input_rejected'],
                'output_probe_positive_bytes': dict(self._probe_positive),
                'signal_unknown': self._signal_unknown, 'wait_unknown': self._wait_unknown,
                'has_handles': bool(self._fds) or input_open,
                'release': None if self._release is None else dict(self._release),
                'qualification': 'unqualified'}
