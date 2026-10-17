"""Linux named record transport bound to actual owned producer/parent lifetimes.

No API dispatch or admission. Listener paths need the fixed builder's private
capture/mount custody; same-UID filesystem permissions alone do not provide it.
All calls are serialized. Descriptor access is a readiness borrow only.
"""
import os
from pathlib import Path
import select
import socket
import stat
import time

from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_peer import AUXILIARY, CONTROL, CREDENTIAL, check_ancillary, _prctl


class BrokerSocketError(ValueError):
    pass


class BrokerCreditDenied(BrokerSocketError):
    pass


def _close_handle(owner, attribute, code, socket_handle=False):
    """Consume one owned handle; premark unknown before any ownership change."""
    value=getattr(owner,attribute)
    if value is None: return
    owner._cleanup.add(code)
    setattr(owner,attribute,None)
    try:
        os.close(value.detach() if socket_handle else value)
    except BaseException:
        pass  # The number is consumed; a retry could close an unrelated resource.
    else:
        owner._cleanup.discard(code)


def _acquire_handle(owner,attribute,code,factory,*args,**kwargs):
    """Retain a factory result before clearing its acquisition uncertainty."""
    owner._cleanup.add(code)
    setattr(owner,attribute,factory(*args,**kwargs))
    owner._cleanup.discard(code)


class RecordSocket:
    """A factory-owned endpoint; no public adoption of a supplied PID or FD."""
    def __init__(self):
        self._socket=self._parent_pidfd=self._owner=None
        self._credentials=self._deadline=self._reserve=self._release=None
        self._busy=self._closed=self._unknown=self._connect_attempted=False
        self._failure=None
        self._cleanup=set()
        self._creator=os.getpid()
        self._transfer_sequence=0
        self._last_transfer=None
        self._terminal_eof=False

    def _begin_transfer(self,direction,maximum):
        if self._transfer_sequence >= 2**53-1:
            self._fail('broker_transfer_sequence')
        self._transfer_sequence+=1
        self._last_transfer=(self._transfer_sequence,direction,maximum,None)

    def _retain_transfer(self,count):
        # Called from the post-syscall path AND its enclosing failure guard.
        # An observed prefix is traffic, never authenticated/usable protocol.
        if self._last_transfer is not None:
            sequence,direction,maximum,_=self._last_transfer
            if type(count) is int and 0<=count<=maximum:
                self._last_transfer=(sequence,direction,maximum,count)

    def _fail(self,code,unknown=False):
        if self._failure is None: self._failure=code
        self._unknown |= unknown
        raise BrokerSocketError(self._failure)

    def _setup(self,endpoint,credentials,deadline,reserve,release,owner=None,parent_pidfd=None):
        # The fixed factory transfers ownership before any later validation.
        self._socket,self._parent_pidfd=endpoint,parent_pidfd
        self._owner,self._credentials,self._deadline=owner,credentials,deadline
        self._reserve,self._release=reserve,release
        self._live()
        self._options()
        if CREDENTIAL.unpack(endpoint.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,CREDENTIAL.size))!=credentials:
            self._fail('broker_connection_peer')

    def _options(self):
        for option,wanted in ((socket.SO_TYPE,socket.SOCK_SEQPACKET),
                              (socket.SO_DOMAIN,socket.AF_UNIX),(socket.SO_PASSCRED,1)):
            if self._socket.getsockopt(socket.SOL_SOCKET,option)!=wanted:
                self._fail('broker_socket_options')
        if os.get_inheritable(self._socket.fileno()): self._fail('broker_socket_inheritable')

    def _live(self):
        if self._failure is not None: raise BrokerSocketError(self._failure)
        if self._closed or self._socket is None: self._fail('broker_socket_closed')
        try:
            if os.getpid()!=self._creator or _prctl(3)!=0:
                self._fail('broker_socket_protection')
            if time.monotonic_ns()>=self._deadline: self._fail('broker_socket_deadline')
            if self._owner is not None:
                if self._owner.observe_wait() is not None: self._fail('broker_peer_ended')
                pidfd=self._owner.fileno('pidfd')  # Borrow, never duplicate/adopt/close.
            else:
                if os.getppid()!=self._credentials[0]: self._fail('broker_parent_changed')
                pidfd=self._parent_pidfd
            poll=select.poll();poll.register(pidfd,select.POLLIN)
            if poll.poll(0): self._fail('broker_peer_ended')
        except BrokerSocketError:
            raise
        except BaseException:
            self._fail('broker_lifetime_unknown',True)

    def fileno(self):
        self._live();self._options()
        return self._socket.fileno()

    def _enter(self):
        if self._busy: raise BrokerSocketError('broker_socket_busy')
        self._live();self._options();self._busy=True

    def send(self,record):
        view=None;acquired=attempted=False
        count=None
        try:
            try:
                if (type(record) is not memoryview or not record.readonly or record.ndim!=1
                        or record.format!='B' or not record.c_contiguous or not 32<=len(record)<=544):
                    raise BrokerSocketError('broker_record_view')
                view=memoryview(record)
                self._enter();acquired=True
                self._begin_transfer('send',len(view))
                attempted=True
                try:
                    count=self._socket.send(view,socket.MSG_DONTWAIT|socket.MSG_NOSIGNAL)
                except (BlockingIOError,InterruptedError):
                    attempted=False
                    self._retain_transfer(0)
                    return -1
                self._retain_transfer(count)
                if count!=len(view): self._fail('broker_send_unknown',True)
                try: self._live()
                except BaseException: self._fail('broker_post_send_unknown',True)
                return count
            finally:
                if view is not None: view.release()
                record=None
                if acquired: self._busy=False
        except BaseException:
            if attempted:self._retain_transfer(count)
            if attempted: self._fail('broker_send_unknown',True)
            raise

    def receive_into(self,destination):
        view=ancillary=address=None
        acquired=held=attempted=False
        count=None
        try:
            try:
                try:
                    if (type(destination) is not memoryview or destination.readonly or destination.ndim!=1
                            or destination.format!='B' or not destination.c_contiguous
                            or not 32<=len(destination)<=544):
                        raise BrokerSocketError('broker_record_view')
                    view=memoryview(destination)
                    self._enter();acquired=True
                    self._cleanup.add('auxiliary_reserve_unknown')
                    try: grant=self._reserve(AUXILIARY)
                    except BaseException: self._fail('broker_auxiliary_unknown',True)
                    if type(grant) is not bool: self._fail('broker_auxiliary_unknown',True)
                    if not grant:
                        self._cleanup.discard('auxiliary_reserve_unknown')
                        raise BrokerCreditDenied('broker_auxiliary_credit')
                    held=True
                    self._cleanup.discard('auxiliary_reserve_unknown')
                    self._begin_transfer('receive',len(view))
                    attempted=True
                    try:
                        count,ancillary,flags,address=self._socket.recvmsg_into(
                            [view],CONTROL,socket.MSG_DONTWAIT|socket.MSG_CMSG_CLOEXEC)
                    except (BlockingIOError,InterruptedError):
                        attempted=False
                        self._retain_transfer(0)
                        return -1
                    except BaseException: self._fail('broker_receive_unknown',True)
                    self._retain_transfer(count)
                    bad,unknown=check_ancillary(ancillary,self._credentials)
                    if unknown: self._cleanup.add('received_right_close_unknown')
                    if bad or unknown or not 32<=count<=len(view) or flags & ~(socket.MSG_EOR|socket.MSG_CMSG_CLOEXEC):
                        self._fail('broker_record_refused',unknown)
                    try: self._live()
                    except BaseException: self._fail('broker_post_receive_unknown',True)
                    return count
                finally:
                    try:
                        if held:
                            self._cleanup.add('auxiliary_release_unknown')
                            try: self._release(AUXILIARY)
                            except BaseException:
                                self._unknown=True
                                if self._failure is None: self._fail('broker_auxiliary_unknown',True)
                            else:self._cleanup.discard('auxiliary_release_unknown')
                    finally:
                        if acquired: self._busy=False
            except BaseException:
                if acquired:
                    for i in range(len(view)): view[i]=0
                raise
            finally:
                ancillary=address=None
                if view is not None: view.release()
                destination=None
        except BaseException:
            if attempted:self._retain_transfer(count)
            if any(name in self._cleanup for name in ('auxiliary_reserve_unknown','auxiliary_release_unknown')):
                self._fail('broker_auxiliary_unknown',True)
            if attempted and self._failure is None:
                self._fail('broker_receive_unknown',True)
            raise

    def receive_terminal_into(self,destination):
        """Observe terminal peer read EOF, never a protocol record or permit.

        The real caller must already have sent its terminal BYE. This method
        accepts only actual zero receive, no ancillary/semantic flags, observed
        shutdown readiness and a still-live original peer. Ordinary record
        reception remains unchanged. A zero-length credentialed packet refuses.
        """
        view=ancillary=address=None
        acquired=held=attempted=False
        count=None
        try:
            try:
                try:
                    if self._terminal_eof:
                        raise BrokerSocketError('broker_terminal_consumed')
                    if (type(destination) is not memoryview or destination.readonly
                            or destination.ndim!=1 or destination.format!='B'
                            or not destination.c_contiguous or not 32<=len(destination)<=544):
                        raise BrokerSocketError('broker_record_view')
                    view=memoryview(destination)
                    self._enter();acquired=True
                    self._cleanup.add('auxiliary_reserve_unknown')
                    grant=self._reserve(AUXILIARY)
                    if type(grant) is not bool:self._fail('broker_auxiliary_unknown',True)
                    if not grant:
                        self._cleanup.discard('auxiliary_reserve_unknown')
                        raise BrokerCreditDenied('broker_auxiliary_credit')
                    held=True
                    self._cleanup.discard('auxiliary_reserve_unknown')
                    self._begin_transfer('receive',len(view));attempted=True
                    try:
                        count,ancillary,flags,address=self._socket.recvmsg_into(
                            [view],CONTROL,socket.MSG_DONTWAIT|socket.MSG_CMSG_CLOEXEC)
                    except (BlockingIOError,InterruptedError):
                        attempted=False;self._retain_transfer(0)
                        return -1
                    self._retain_transfer(count)
                    if ancillary:
                        _,unknown=check_ancillary(ancillary,self._credentials)
                        if unknown:self._cleanup.add('received_right_close_unknown')
                        self._fail('broker_terminal_refused',unknown)
                    if count!=0 or flags & ~socket.MSG_CMSG_CLOEXEC:
                        self._fail('broker_terminal_refused')
                    poll=select.poll()
                    fd=self._socket.fileno()
                    poll.register(fd,select.POLLIN|select.POLLRDHUP)
                    ready=dict(poll.poll(0)).get(fd,0)
                    if (not ready & (select.POLLHUP|select.POLLRDHUP)
                            or ready & (select.POLLERR|select.POLLNVAL)):
                        self._fail('broker_terminal_refused')
                    self._live()
                finally:
                    try:
                        if held:
                            self._cleanup.add('auxiliary_release_unknown')
                            self._release(AUXILIARY)
                            self._cleanup.discard('auxiliary_release_unknown')
                    finally:
                        if acquired:self._busy=False
            except BaseException:
                if acquired:
                    for i in range(len(view)):view[i]=0
                raise
            finally:
                ancillary=address=None
                if view is not None:view.release()
                destination=None
            # Promote only after all owned receive/auxiliary/view cleanup.
            # Later channel-close uncertainty does not erase this EOF fact.
            self._terminal_eof=True
            return 0
        except BaseException:
            if attempted:self._retain_transfer(count)
            if any(name in self._cleanup for name in ('auxiliary_reserve_unknown','auxiliary_release_unknown')):
                self._fail('broker_auxiliary_unknown',True)
            if attempted and self._failure is None:self._fail('broker_terminal_unknown',True)
            raise

    def close(self):
        if self._busy: raise BrokerSocketError('broker_socket_busy')
        self._closed=True
        try:
            _close_handle(self,'_socket','socket_close_unknown',True)
        finally:
            _close_handle(self,'_parent_pidfd','parent_pidfd_close_unknown')

    def snapshot(self):
        return dict(primary=self._failure,outcome_unknown=self._unknown,closed=self._closed,
                    last_transfer=self._last_transfer,
                    terminal_eof=self._terminal_eof,
                    cleanup=tuple(sorted(self._cleanup)),peer=self._credentials,
                    has_handles=self._socket is not None or self._parent_pidfd is not None,
                    closed_known=self._closed and not self._cleanup and self._socket is None
                                 and self._parent_pidfd is None,
                    qualification='unqualified')

    def connect_parent(self,path,deadline_ns,reserve,release):
        """Consume one connect to the actual parent; keep handles on failure."""
        if self._connect_attempted or self._closed: raise BrokerSocketError('broker_connect_consumed')
        self._connect_attempted=True
        try:
            if (type(path) is not str or not path.startswith('/') or '\0' in path
                    or type(deadline_ns) is not int or not callable(reserve) or not callable(release)):
                raise BrokerSocketError('broker_parent_binding')
            parent=os.getppid()
            if parent<=1 or _prctl(3)!=0: raise BrokerSocketError('broker_parent_binding')
            _acquire_handle(self,'_parent_pidfd','parent_pidfd_acquire_unknown',os.pidfd_open,parent)
            _acquire_handle(self,'_socket','socket_acquire_unknown',socket.socket,
                            socket.AF_UNIX,socket.SOCK_SEQPACKET|socket.SOCK_CLOEXEC)
            endpoint=self._socket
            endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            endpoint.settimeout(max(0,(deadline_ns-time.monotonic_ns())/1_000_000_000))
            endpoint.connect(path)
            endpoint.setblocking(False)
            self._setup(endpoint,(parent,os.getuid(),os.getgid()),deadline_ns,reserve,release,
                              parent_pidfd=self._parent_pidfd)
            return self
        except BaseException:
            if self._failure is None:self._failure='broker_connect_unknown'
            self._unknown=True
            raise


class BrokerListener:
    """One fresh pathname, one actual producer, one accepted endpoint; no retry."""
    def __init__(self):
        self._socket=self._directory=self._entry=self._accepted=None
        self._path=self._deadline=self._reserve=self._release=None
        self._connection=None
        self._started=self._accept_attempted=self._closed=False
        self._bind_attempted=self._unlink_attempted=False
        self._failure=None;self._cleanup=set()

    @property
    def connection(self): return self._connection  # Retained even after failed acceptance.

    def start(self,directory,deadline_ns,reserve,release):
        if self._started or self._closed: raise BrokerSocketError('broker_listener_consumed')
        self._started=True
        try:
            path=Path(directory)
            if (not path.is_absolute() or '..' in path.parts or type(deadline_ns) is not int
                    or not callable(reserve) or not callable(release) or _prctl(3)!=0):
                raise BrokerSocketError('broker_listener_binding')
            for part in (path,*path.parents):
                if not stat.S_ISDIR(part.lstat().st_mode): raise BrokerSocketError('broker_listener_path')
            _acquire_handle(self,'_directory','directory_acquire_unknown',os.open,path,
                            os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            value=os.fstat(self._directory)
            if value.st_uid!=os.getuid() or value.st_mode & 0o077:
                raise BrokerSocketError('broker_listener_private_directory')
            self._path=str(path/'memory.sock')
            if len(os.fsencode(self._path))>=108: raise BrokerSocketError('broker_listener_path')
            self._deadline,self._reserve,self._release=deadline_ns,reserve,release
            if time.monotonic_ns()>=deadline_ns: raise BrokerSocketError('broker_socket_deadline')
            _acquire_handle(self,'_socket','listener_acquire_unknown',socket.socket,
                            socket.AF_UNIX,socket.SOCK_SEQPACKET|socket.SOCK_CLOEXEC|socket.SOCK_NONBLOCK)
            self._socket.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            self._bind_attempted=True
            self._socket.bind(self._path)  # Existing leaf, including symlink, refuses.
            _acquire_handle(self,'_entry','entry_acquire_unknown',os.open,'memory.sock',
                            os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self._directory)
            value=os.fstat(self._entry)
            if not stat.S_ISSOCK(value.st_mode): raise BrokerSocketError('broker_listener_entry')
            self._socket.listen(1)
        except BaseException:
            self._failure='broker_listener_setup_unknown'
            raise

    def fileno(self):
        if self._closed or self._failure is not None or self._socket is None:
            raise BrokerSocketError('broker_listener_unavailable')
        if time.monotonic_ns()>=self._deadline: raise BrokerSocketError('broker_socket_deadline')
        return self._socket.fileno()

    def accept(self,owner):
        if self._accept_attempted: raise BrokerSocketError('broker_accept_consumed')
        if type(owner) is not NativeProcessOwner: raise BrokerSocketError('broker_producer_owner')
        state=owner.snapshot()
        if not state['launched'] or state['primary'] is not None or owner.observe_wait() is not None:
            raise BrokerSocketError('broker_producer_owner')
        self.fileno()
        self._accept_attempted=True
        self._cleanup.add('accept_unknown')
        try:
            self._connection=RecordSocket()
            try: self._accepted=self._socket.accept()
            except (BlockingIOError,InterruptedError):
                self._connection=None
                self._accept_attempted=False
                self._cleanup.discard('accept_unknown')
                return None
            endpoint=self._accepted[0]
            self._connection._socket=endpoint
            self._accepted=None
            self._connection._connect_attempted=True
            endpoint.setblocking(False)
            endpoint.set_inheritable(False)
            self._connection._setup(endpoint,(state['pid'],os.getuid(),os.getgid()),self._deadline,
                                    self._reserve,self._release,owner=owner)
            self._cleanup.discard('accept_unknown')
            return self._connection
        except BaseException:
            known=(self._accepted is None and self._connection is not None
                   and self._connection.snapshot()['primary'] is not None
                   and not self._connection.snapshot()['outcome_unknown'])
            if known: self._cleanup.discard('accept_unknown')
            self._failure='broker_accept_refused' if known else 'broker_accept_unknown'
            raise

    def close(self):
        self._closed=True
        try:
            try:
                if self._connection is not None:
                    self._cleanup.add('connection_close_unknown')
                    try: self._connection.close()
                    except BaseException: pass
                    if self._connection.snapshot()['closed_known']:
                        self._cleanup.discard('connection_close_unknown')
            finally:
                if self._accepted is not None:
                    self._cleanup.add('accepted_close_unknown')
                    accepted,self._accepted=self._accepted,None
                    try: os.close(accepted[0].detach())
                    except BaseException: pass
                    else: self._cleanup.discard('accepted_close_unknown')
        finally:
            try:
                if self._bind_attempted and self._entry is None and not self._unlink_attempted:
                    self._cleanup.add('path_unlink_unknown')
                if self._entry is not None and self._directory is not None and not self._unlink_attempted:
                    self._cleanup.add('path_unlink_unknown')
                    self._unlink_attempted=True
                    try:
                        held=os.fstat(self._entry)
                        current=os.stat('memory.sock',dir_fd=self._directory,follow_symlinks=False)
                        if (held.st_dev,held.st_ino)!=(current.st_dev,current.st_ino):
                            raise BrokerSocketError('broker_listener_entry_changed')
                        os.unlink('memory.sock',dir_fd=self._directory)
                    except BaseException: pass
                    else: self._cleanup.discard('path_unlink_unknown')
            finally:
                try: _close_handle(self,'_entry','entry_close_unknown')
                finally:
                    try: _close_handle(self,'_directory','directory_close_unknown')
                    finally: _close_handle(self,'_socket','listener_close_unknown',True)

    def snapshot(self):
        return dict(primary=self._failure,closed=self._closed,cleanup=tuple(sorted(self._cleanup)),
                    accept_consumed=self._accept_attempted,
                    has_handles=any(x is not None for x in (self._socket,self._directory,self._entry,self._accepted)),
                    connection=None if self._connection is None else self._connection.snapshot(),
                    qualification='unqualified')
