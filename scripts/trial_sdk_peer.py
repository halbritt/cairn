"""Linux credential-aware borrowed-record endpoint; no launch or body authority."""
import ctypes
import os
import select
import socket
import struct
import sys
import time

CREDENTIAL = struct.Struct('iII')
RIGHT = struct.Struct('i')
CONTROL = socket.CMSG_SPACE(CREDENTIAL.size) + socket.CMSG_SPACE(16*RIGHT.size)
AUXILIARY = 2*CONTROL + 2*128  # CPython control/address staging and returned copies.
_LIBC = None


def _prctl(option, value=0):
    global _LIBC
    if _LIBC is None:
        _LIBC = ctypes.CDLL(None, use_errno=True)
        _LIBC.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong]*4
        _LIBC.prctl.restype = ctypes.c_int
    return _LIBC.prctl(option, value, 0, 0, 0)


class PeerError(ValueError):
    pass


class _LinuxCredential(ctypes.Structure):
    _fields_ = [('pid', ctypes.c_int), ('uid', ctypes.c_uint), ('gid', ctypes.c_uint)]


def _check_credential_abi():
    # Selected Linux layout only; not installed-header or physical-fit proof.
    if (sys.platform != 'linux' or ctypes.sizeof(ctypes.c_int) != 4
            or ctypes.sizeof(ctypes.c_uint) != 4 or ctypes.sizeof(_LinuxCredential) != 12
            or (_LinuxCredential.pid.offset, _LinuxCredential.uid.offset,
                _LinuxCredential.gid.offset) != (0, 4, 8)
            or struct.calcsize('i') != 4 or struct.calcsize('I') != 4
            or CREDENTIAL.size != 12 or RIGHT.size != 4):
        raise PeerError('peer_credential_abi')


_check_credential_abi()


class CreditDenied(PeerError):
    pass


def check_ancillary(ancillary, expected):
    """Validate every visible control item; close received rights even on refusal."""
    bad = unknown = False
    credentials = 0
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            bad = True
            unknown |= len(data) % RIGHT.size != 0
            for pos in range(0, len(data)-RIGHT.size+1, RIGHT.size):
                fd = RIGHT.unpack_from(data, pos)[0]
                try:
                    os.close(fd)
                except BaseException:
                    unknown = True
        elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(data) == CREDENTIAL.size:
            credentials += 1
            bad |= CREDENTIAL.unpack(data) != expected
        else:
            bad = True
    return bad or credentials != 1, unknown




class Peer:
    @staticmethod
    def protect():
        if _prctl(4, 0) or _prctl(3) != 0:
            raise PeerError('peer_protection')
        return os.getpid()

    def __init__(self, endpoint, pidfd, pid, uid, gid, deadline, scratch, reserve, release):
        self._socket = self._pidfd = None
        self._failed = self._busy = self._closed_known = self._close_unknown = False
        self._unknown = self._cleanup_unknown = False
        self._primary = None
        loan = None
        held = False
        try:
            if (type(endpoint) is not socket.socket or type(pidfd) is not int or pidfd < 0
                    or any(type(n) is not int for n in (pid, uid, gid, deadline))
                    or not 1 < pid <= 2**31-1 or not 0 <= uid < 2**32 or not 0 <= gid < 2**32
                    or not callable(reserve) or not callable(release)):
                raise PeerError('peer_configuration')
            if (type(scratch) is not memoryview or scratch.readonly or scratch.ndim != 1
                    or scratch.format != 'B' or not scratch.c_contiguous or len(scratch) != 512):
                raise PeerError('peer_scratch')
            loan = memoryview(scratch)
            now = time.monotonic_ns()
            if _prctl(3) != 0 or not now < deadline <= now+140_000_000_000:
                raise PeerError('peer_setup_boundary')
            self._pid, self._uid, self._gid, self._deadline = pid, uid, gid, deadline
            self._reserve, self._release = reserve, release
            try:
                granted = reserve(AUXILIARY)
            except BaseException:
                self._cleanup_unknown = True
                raise PeerError('peer_setup_reserve_unknown') from None
            if type(granted) is not bool:
                self._cleanup_unknown = True
                raise PeerError('peer_setup_reserve_unknown')
            if not granted:
                raise CreditDenied('peer_credit')
            held = True
            self._socket = endpoint.dup()
            self._pidfd = os.dup(pidfd)
            if os.get_inheritable(self._socket.fileno()) or os.get_inheritable(self._pidfd):
                raise PeerError('peer_setup_descriptor')
            self._options()
            if self._socket.getsockname() not in ('', b'') or self._socket.getpeername() not in ('', b''):
                raise PeerError('peer_setup_named')
            self._verify_pidfd(loan)
            self._live()
        except BaseException:
            try:
                self.close()
            except BaseException:
                raise PeerError('peer_setup_cleanup_unknown') from None
            raise
        finally:
            if loan is not None:
                for i in range(len(loan)):
                    loan[i] = 0
                loan.release()
            scratch = None
            if held:
                try:
                    self._release(AUXILIARY)
                except BaseException:
                    self._cleanup_unknown = True
                    try:
                        self.close()
                    except BaseException:
                        self._close_unknown = True
                    raise PeerError('peer_setup_cleanup_unknown') from None

    def _verify_pidfd(self, scratch):
        fd = os.open('/proc/self/fdinfo/'+str(self._pidfd),
                     os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            used = 0
            while used < 512:
                if time.monotonic_ns() >= self._deadline:
                    raise PeerError('peer_pidfd_identity')
                view = scratch[used:]
                try:
                    n = os.readv(fd, [view])
                finally:
                    view.release()
                if not n:
                    break
                used += n
            if not 0 < used < 512:
                raise PeerError('peer_pidfd_identity')
            found = start = 0
            for end in range(used+1):
                if end < used and scratch[end] != 10:
                    continue
                if end-start >= 5 and all(scratch[start+j] == c for j,c in enumerate(b'Pid:\t')):
                    value = 0
                    if end-start == 5:
                        raise PeerError('peer_pidfd_identity')
                    for i in range(start+5,end):
                        if not 48 <= scratch[i] <= 57:
                            raise PeerError('peer_pidfd_identity')
                        value = value*10 + scratch[i]-48
                        if value > 2**31-1:
                            raise PeerError('peer_pidfd_identity')
                    found += 1
                    if found != 1 or value != self._pid:
                        raise PeerError('peer_pidfd_identity')
                start = end+1
            if found != 1:
                raise PeerError('peer_pidfd_identity')
        finally:
            try:
                os.close(fd)  # One attempt; no reuse of an uncertain raw number.
            except BaseException:
                self._cleanup_unknown = True
                raise PeerError('peer_setup_cleanup_unknown') from None

    def _fail(self, code, unknown=False):
        self._failed = True
        self._unknown |= unknown
        if self._primary is None:
            self._primary = code
        raise PeerError(self._primary)

    def _live(self):
        if self._failed or self._socket is None or self._pidfd is None:
            self._fail('peer_unusable')
        if _prctl(3) != 0 or time.monotonic_ns() >= self._deadline:
            self._fail('peer_boundary')
        check = select.poll()
        check.register(self._pidfd, select.POLLIN)
        if check.poll(0):
            self._fail('peer_lifetime')

    def _options(self):
        for option, wanted in ((socket.SO_TYPE, socket.SOCK_SEQPACKET),
                               (socket.SO_DOMAIN, socket.AF_UNIX), (socket.SO_PASSCRED, 1)):
            if self._socket.getsockopt(socket.SOL_SOCKET, option) != wanted:
                self._fail('peer_options')

    def _enter(self):
        if self._busy:
            raise PeerError('peer_busy')
        self._live()
        self._busy = True

    def _ancillary(self, ancillary):
        return check_ancillary(ancillary, (self._pid, self._uid, self._gid))

    def fileno(self):
        """Borrow for serialized readiness only; no dup/close/transfer/rebind."""
        self._enter()
        try:
            self._options()
            return self._socket.fileno()
        finally:
            self._busy = False

    def receive_into(self, destination):
        loan = ancillary = address = None
        acquired = held = False
        try:
            if (type(destination) is not memoryview or destination.readonly or destination.ndim != 1
                    or destination.format != 'B' or not destination.c_contiguous or not 32 <= len(destination) <= 544):
                raise PeerError('peer_destination')
            loan = memoryview(destination)
            self._enter()
            acquired = True
            self._options()
            try:
                granted = self._reserve(AUXILIARY)
            except BaseException:
                self._cleanup_unknown = True
                self._fail('peer_reserve_unknown')
            if type(granted) is not bool:
                self._cleanup_unknown = True
                self._fail('peer_reserve_unknown')
            if not granted:
                raise CreditDenied('peer_credit')
            held = True
            try:
                n, ancillary, flags, address = self._socket.recvmsg_into(
                    [loan], CONTROL, socket.MSG_DONTWAIT | socket.MSG_CMSG_CLOEXEC)
            except (BlockingIOError, InterruptedError):
                return -1
            except BaseException:
                self._cleanup_unknown = True
                self._fail('peer_receive_unknown', True)
            bad, unknown = self._ancillary(ancillary)
            self._cleanup_unknown |= unknown
            if bad or unknown or not 32 <= n <= len(loan) or flags & ~(socket.MSG_EOR | socket.MSG_CMSG_CLOEXEC):
                self._fail('peer_record_refused')
            try:
                self._live()
            except BaseException:
                self._fail('peer_post_receive_unknown', True)
            return n
        except CreditDenied:
            raise
        except BaseException:
            if acquired:
                for i in range(len(loan)):
                    loan[i] = 0
            raise
        finally:
            ancillary = address = None
            if loan is not None:
                loan.release()
            destination = None
            try:
                if held:
                    try:
                        self._release(AUXILIARY)
                    except BaseException:
                        self._cleanup_unknown = True
                        if not self._failed:
                            self._fail('peer_release_unknown')
            finally:
                if acquired:
                    self._busy = False

    def send(self, body):
        loan = None
        acquired = False
        try:
            if type(body) is bytes:
                loan = memoryview(body)
            elif (type(body) is memoryview and body.readonly and body.ndim == 1
                  and body.format == 'B' and body.c_contiguous):
                loan = memoryview(body)
            else:
                raise PeerError('peer_body')
            if not 32 <= len(loan) <= 544:
                raise PeerError('peer_record_length')
            self._enter()
            acquired = True
            try:
                n = self._socket.send(loan, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
            except (BlockingIOError, InterruptedError):
                return -1
            except BaseException:
                self._fail('peer_send_unknown', True)
            if n != len(loan):
                self._fail('peer_send_unknown', True)
            try:
                self._live()
            except BaseException:
                self._fail('peer_post_send_unknown', True)
            return n
        finally:
            if loan is not None:
                loan.release()
            body = None
            if acquired:
                self._busy = False

    def close(self):
        if self._busy:
            raise PeerError('peer_busy')
        if self._socket is None and self._pidfd is None:
            if self._close_unknown:
                raise PeerError('peer_close_unknown')
            self._closed_known = True
            return
        sock, pidfd = self._socket, self._pidfd
        self._socket = self._pidfd = None
        unknown = False
        for fd in (sock.detach() if sock is not None else None, pidfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    unknown = True
        self._close_unknown |= unknown
        self._closed_known = not self._close_unknown
        if unknown:
            raise PeerError('peer_close_unknown')

    def snapshot(self):
        return dict(primary=self._primary, failed=self._failed, outcome_unknown=self._unknown,
                    cleanup_unknown=self._cleanup_unknown, closed_known=self._closed_known,
                    close_unknown=self._close_unknown, qualification='unqualified')
