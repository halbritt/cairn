"""Internal owned worker primitives; no public request or native execution route.

Only trusted fixed runner builders create Child commands. Held cgroup/process
handles are observations and cleanup authority, never provider admission.
"""
import dataclasses
import errno
import io
import socket
import stat
import json
import os
from pathlib import Path
import subprocess
import signal
import time
import uuid

FIXTURE = Path(__file__).resolve().parent/'supervision_fixture'
CONTROLS = dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32, cpu_max='100000 100000')

class Unavailable(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class WaitFact:
    pid: int
    start_ticks: int | None
    raw_status: int | None
    returncode: int | None


class OwnedCgroup:
    """Only an exclusive new domain leaf; all effects use held directory/file FDs."""
    def __init__(self, delegated):
        self.name = 'cairn-fixture-'+str(uuid.uuid4())
        self.parent = os.open(delegated, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        self.files = {}
        self.consumed = False
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent)
            self.consumed = True
            self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
            self.path = str(Path(delegated)/self.name)
            self.relative = self.path.removeprefix('/sys/fs/cgroup')
            if self.read('cgroup.type').strip() != 'domain':
                raise Unavailable('cgroup_domain_unavailable')
            for field, value in CONTROLS.items():
                name = field.replace('_', '.')
                self.write(name, str(value).encode())
                if self.read(name).strip() != str(value):
                    raise Unavailable('resource_readback_unknown')
            self.files['cgroup.procs'] = self.open('cgroup.procs', os.O_WRONLY)
            self.files['cgroup.kill'] = self.open('cgroup.kill', os.O_WRONLY)
            for name in ['cgroup.events', 'memory.events', 'pids.events']:
                self.files[name] = self.open(name, os.O_RDONLY)
            if self.populated():
                raise Unavailable('new_cgroup_populated')
        except (OSError, Unavailable):
            self.close()
            raise Unavailable('containment_or_resource_unavailable') from None

    def open(self, name, flags):
        if (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino) != self.identity:
            raise Unavailable('cgroup_identity_unknown')
        return os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)

    def read(self, name):
        fd = self.files.get(name)
        owned = fd is None
        if owned:
            fd = self.open(name, os.O_RDONLY)
        try:
            data = os.pread(fd, 8192, 0)
            if len(data) == 8192:
                raise Unavailable('cgroup_status_bounds')
            return data.decode('ascii')
        finally:
            if owned:
                os.close(fd)

    def write(self, name, data):
        fd = self.files.get(name)
        owned = fd is None
        if owned:
            fd = self.open(name, os.O_WRONLY)
        try:
            if os.write(fd, data) != len(data):
                raise Unavailable('cgroup_write_unknown')
        finally:
            if owned:
                os.close(fd)

    def populated(self):
        pairs = dict(line.split() for line in self.read('cgroup.events').splitlines())
        if pairs.get('populated') not in {'0', '1'}:
            raise Unavailable('cgroup_release_unknown')
        return pairs['populated'] == '1'

    def kill(self):
        self.write('cgroup.kill', b'1')

    def events(self):
        return {name:dict(line.split() for line in self.read(name).splitlines())
                for name in ['memory.events', 'pids.events']}

    def close(self):
        errors = []
        for fd in self.files.values():
            try:
                os.close(fd)
            except OSError:
                errors.append('cgroup_descriptor_close_unknown')
        self.files.clear()
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                errors.append('cgroup_directory_close_unknown')
            self.fd = None
        if self.consumed:
            try:
                os.rmdir(self.name, dir_fd=self.parent)
            except OSError:
                # An unreleased leaf is preserved; never remove existing work.
                errors.append('cgroup_directory_removal_unknown')
        try:
            os.close(self.parent)
        except OSError:
            errors.append('cgroup_parent_close_unknown')
        return sorted(set(errors))


class Child:
    def __init__(self, group, argv):
        status_read, status_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        command = ['/usr/bin/python3', str(FIXTURE/'launcher.py'), str(group.files['cgroup.procs']),
                   'bwrap', '--json-status-fd', str(status_write), *argv]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, pass_fds=(group.files['cgroup.procs'], status_write),
                env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}, close_fds=True)
        except OSError:
            os.close(status_read)
            raise
        finally:
            os.close(status_write)
        self.status_fd = status_read
        self.namespace_fds = []
        self.namespace_identity = None
        self.buffers = dict(stdout=bytearray(), stderr=bytearray(), status=bytearray())
        self.eof = set()
        self.verified = False
        self.wait = None
        try:
            fields = Path(f'/proc/{self.process.pid}/stat').read_text().rsplit(') ', 1)[1].split()
            self.start = int(fields[19])
        except (OSError, ValueError, IndexError):
            self.start = None
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            os.set_blocking(stream.fileno(), False)

    def observe_wait(self):
        if self.wait is not None:
            return self.wait
        pid, raw = os.waitpid(self.process.pid, os.WNOHANG)
        if pid:
            code = os.waitstatus_to_exitcode(raw)
            self.process.returncode = code
            self.wait = WaitFact(pid, self.start, raw, code)
        return self.wait

    def verify_namespace(self, group):
        if self.verified:
            return True
        if b'\n' not in self.buffers['status']:
            return False
        value = json.loads(self.buffers['status'].split(b'\n')[0])
        pid = value['child-pid']
        if type(pid) is not int or pid <= 0:
            raise Unavailable('namespace_status_unknown')
        # Reported IDs are checked against a living member of our cgroup,
        # then anchored with namespace FDs; they are never signal authority.
        before = Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
        if Path(f'/proc/{pid}/cgroup').read_text().strip() != '0::'+group.relative:
            raise Unavailable('namespace_membership_unknown')
        fds = []
        identities = dict(host_init_pid=pid, host_init_start_ticks=int(before))
        try:
            for name in ['pid', 'mnt', 'net', 'user']:
                fd = os.open(f'/proc/{pid}/ns/{name}', os.O_RDONLY | os.O_CLOEXEC)
                fds.append(fd)
                actual = os.fstat(fd).st_ino
                identities[name] = actual
                if actual == os.stat(f'/proc/self/ns/{name}').st_ino:
                    raise Unavailable('namespace_isolation_unknown')
                if name != 'user' and actual != value[name+'-namespace']:
                    raise Unavailable('namespace_status_mismatch')
            after = Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            if before != after or Path(f'/proc/{pid}/cgroup').read_text().strip() != '0::'+group.relative:
                raise Unavailable('namespace_identity_unknown')
            self.namespace_fds = fds
            fds = []
            self.verified = True
            self.namespace_identity = identities
            return True
        finally:
            for fd in fds:
                os.close(fd)

    def close(self):
        errors = []
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            try:
                stream.close()
            except OSError:
                errors.append('worker_stream_close_unknown')
        for fd in [self.status_fd, *self.namespace_fds]:
            try:
                os.close(fd)
            except OSError:
                errors.append('worker_descriptor_close_unknown')
        return sorted(set(errors))


@dataclasses.dataclass(frozen=True)
class OwnedWaitFact(WaitFact):
    boot_id: str | None


def _socket_identity(fd):
    info = os.fstat(fd)
    if not stat.S_ISSOCK(info.st_mode):
        return None
    view = socket.socket(fileno=fd)
    try:
        # Linux SO_COOKIE identifies this endpoint across descriptor reuse.
        # Peer socket inodes differ; neither peer identity nor a bare saved inode
        # can guard an indeterminate close of this endpoint.
        cookie = view.getsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_COOKIE', 57), 8)
    finally:
        view.detach()
    return info.st_dev, info.st_ino, cookie


class _SocketEndpoint:
    def __init__(self, sock):
        self.fd = sock.detach()
        self.identity = None
        self.close_attempted = False
        self.faults = set()

    def close(self):
        if self.fd is not None:
            try:
                if self.identity is None and self.close_attempted:
                    raise OSError('unidentified_socket_close')
                if self.identity is not None and _socket_identity(self.fd) != self.identity:
                    self.faults.add('worker_descriptor_close_unknown')
                    self.fd = None  # This descriptor no longer names our endpoint.
                else:
                    self.close_attempted = True
                    os.close(self.fd)
                    self.fd = None
            except OSError as error:
                if error.errno == errno.EBADF:
                    self.fd = None
                # Keep the number and identity for safe retry. Never retry a
                # raw number after an uncertain close without checking identity.
                self.faults.add('worker_descriptor_close_unknown')
        return sorted(self.faults)


class _SocketStream(io.FileIO):
    def __init__(self, endpoint, mode):
        self.endpoint = endpoint
        # Endpoint custody, not FileIO/destructor ownership, decides whether a
        # descriptor still belongs to us after an indeterminate close.
        super().__init__(endpoint.fd, mode, closefd=False)

    def close(self):
        if not self.closed:
            faults = self.endpoint.close()
            super().close()
            if faults:
                raise OSError('worker_stream_close_unknown')


class _SocketChannels:
    def __init__(self):
        self.ends, self.parents, self.children, self.streams = [], {}, {}, {}

    def allocate(self):
        for name in ('stdin', 'stdout', 'stderr', 'status'):
            parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            for side, sock in ((self.parents, parent), (self.children, child)):
                endpoint = _SocketEndpoint(sock)
                self.ends.append(endpoint)
                side[name] = endpoint
            for endpoint in (self.parents[name], self.children[name]):
                endpoint.identity = _socket_identity(endpoint.fd)
            for endpoint, direction in ((self.parents[name], socket.SHUT_RD if name == 'stdin' else socket.SHUT_WR),
                                        (self.children[name], socket.SHUT_WR if name == 'stdin' else socket.SHUT_RD)):
                view = socket.socket(fileno=endpoint.fd)
                try:
                    view.shutdown(direction)
                finally:
                    view.detach()
            if name == 'status':
                os.set_blocking(self.parents[name].fd, False)
                os.set_blocking(self.children[name].fd, False)
            else:
                raw = _SocketStream(self.parents[name], 'wb' if name == 'stdin' else 'rb')
                self.streams[name] = io.BufferedWriter(raw) if name == 'stdin' else io.BufferedReader(raw)

    def close_children(self):
        faults = []
        for endpoint in self.children.values():
            faults.extend(endpoint.close())
        if faults:
            raise OSError('worker_descriptor_close_unknown')

    def close(self):
        faults = []
        for stream in self.streams.values():
            try:
                stream.close()
            except OSError:
                faults.append('worker_stream_close_unknown')
        for endpoint in self.ends:
            faults.extend(endpoint.close())
        return sorted(set(faults))


class OwnedWorker(Child):
    """Direct-launcher pidfd for the new domain; legacy Child stays unchanged."""
    def __init__(self, group, argv, *, channels='pipes/1'):
        # Legacy Child keeps its exact AST. This acquisition path stores each
        # owned handle before another setup operation can fail.
        if type(channels) is not str or channels not in ('pipes/1', 'unix-stream/1'):
            raise Unavailable('owned_worker_channel_profile')
        self._socket_channels = None
        self.group_identity,self.group_relative=group.identity,group.relative
        self.process,self.pidfd,self.status_fd,self.pending_status_write=None,None,None,None
        self.namespace_fds=[]
        self.namespace_identity=None
        self.buffers=dict(stdout=bytearray(),stderr=bytearray(),status=bytearray())
        self.eof=set()
        self.verified=False
        self.wait,self.start,self.boot=None,None,None
        self.acquisition_observations=[]
        try:
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            self.boot=str(uuid.UUID(boot)) if str(uuid.UUID(boot))==boot else None
        except (OSError,ValueError):
            pass
        if channels == 'unix-stream/1':
            self._start_socket_worker(group, argv)
            return
        try:
            self.status_fd,self.pending_status_write=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
            command=['/usr/bin/python3',str(FIXTURE/'launcher.py'),str(group.files['cgroup.procs']),
                     'bwrap','--json-status-fd',str(self.pending_status_write),*argv]
            self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,pass_fds=(group.files['cgroup.procs'],self.pending_status_write),
                env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True)
            # No reaping or signal reconstruction occurs before this binding.
            group.bootstrap_child=self
            try:
                self.pidfd=os.pidfd_open(self.process.pid,0)
            except OSError:
                self.acquisition_observations.append('owned_launcher_handle_unknown')
                raise
            try:
                fields=Path(f'/proc/{self.process.pid}/stat').read_text().rsplit(') ',1)[1].split()
                self.start=int(fields[19])
            except (OSError,ValueError,IndexError):
                pass
            for stream in [self.process.stdin,self.process.stdout,self.process.stderr]:
                os.set_blocking(stream.fileno(),False)
            try:
                os.close(self.pending_status_write)
            except OSError:
                self.acquisition_observations.append('worker_descriptor_close_unknown')
                raise
            self.pending_status_write=None
        except OSError:
            self.acquisition_observations.append('owned_worker_setup_unknown')
            if self.process is None:
                self.acquisition_observations.extend(self.close())
            reason=('owned_launcher_handle_unknown' if 'owned_launcher_handle_unknown' in
                    self.acquisition_observations else 'owned_worker_setup_unknown')
            raise Unavailable(reason) from None
        group.bootstrap_child=None


    def _start_socket_worker(self, group, argv):
        if getattr(group, 'bootstrap_child', None) is not None:
            raise Unavailable('owned_worker_bootstrap_held')
        self._socket_channels = _SocketChannels()
        # Retain construction custody even before Popen: allocation and channel
        # close failures also need a discoverable owner.
        group.bootstrap_child = self
        try:
            self._socket_channels.allocate()
            self.status_fd = self._socket_channels.parents['status'].fd
            self.pending_status_write = self._socket_channels.children['status'].fd
            command = ['/usr/bin/python3', str(FIXTURE/'launcher.py'), str(group.files['cgroup.procs']),
                       'bwrap', '--json-status-fd', str(self.pending_status_write), *argv]
            self.process = subprocess.Popen(command,
                **{name: self._socket_channels.children[name].fd for name in ('stdin', 'stdout', 'stderr')},
                pass_fds=(group.files['cgroup.procs'], self.pending_status_write),
                env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}, close_fds=True)
            # The actual process is held by bootstrap_child before stream
            # installation, pidfd acquisition or any later setup can fail.
            for name, stream in self._socket_channels.streams.items():
                setattr(self.process, name, stream)
            try:
                self.pidfd = os.pidfd_open(self.process.pid, 0)
            except OSError:
                self.acquisition_observations.append('owned_launcher_handle_unknown')
                raise
            try:
                fields = Path(f'/proc/{self.process.pid}/stat').read_text().rsplit(') ', 1)[1].split()
                self.start = int(fields[19])
            except (OSError, ValueError, IndexError):
                pass
            for stream in self._socket_channels.streams.values():
                os.set_blocking(stream.fileno(), False)
            self._socket_channels.close_children()
            self.pending_status_write = None
        except BaseException as error:
            self.acquisition_observations.extend(sorted({fault for endpoint in self._socket_channels.ends
                                                         for fault in endpoint.faults}))
            self.acquisition_observations.append('owned_worker_setup_unknown')
            if self.process is None:
                self.acquisition_observations.extend(self.close())
            if not isinstance(error, OSError):
                raise
            reason = ('owned_launcher_handle_unknown' if 'owned_launcher_handle_unknown' in
                      self.acquisition_observations else 'owned_worker_setup_unknown')
            raise Unavailable(reason) from None
        group.bootstrap_child = None

    def observe_wait(self):
        if self.process is None:
            return None
        fact=super().observe_wait()
        if fact is not None and not isinstance(fact,OwnedWaitFact):
            self.wait=OwnedWaitFact(fact.pid,fact.start_ticks,fact.raw_status,fact.returncode,self.boot)
        return self.wait

    def close(self):
        errors=[]
        if self._socket_channels is not None:
            errors.extend(self._socket_channels.close())
            self.pending_status_write = self._socket_channels.children.get('status')
            if self.pending_status_write is not None:
                self.pending_status_write = self.pending_status_write.fd
        elif self.pending_status_write is not None:
            # Read end stays held while checking an indeterminate writer close:
            # it pins this pipe inode, so a reused unrelated FD is never closed.
            try:
                read=os.fstat(self.status_fd);write=os.fstat(self.pending_status_write)
                if (read.st_dev,read.st_ino)!=(write.st_dev,write.st_ino):
                    errors.append('worker_descriptor_close_unknown')
                else:
                    os.close(self.pending_status_write)
            except OSError:
                errors.append('worker_descriptor_close_unknown')
            self.pending_status_write=None
        if self._socket_channels is None and self.process is not None:
            for stream in [self.process.stdin,self.process.stdout,self.process.stderr]:
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        errors.append('worker_stream_close_unknown')
        for fd in [self.pidfd, self.status_fd if self._socket_channels is None else None, *self.namespace_fds]:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    errors.append('worker_descriptor_close_unknown')
        self.pidfd,self.status_fd=None,None
        self.namespace_fds=[]
        return sorted(set(errors))


def end_containment(group, children, deadline):
    errors = []
    try:
        group.kill()
    except (OSError, Unavailable):
        errors.append('containment_kill_unknown')
    released = None
    while time.monotonic() < deadline:
        try:
            for child in children.values():
                child.observe_wait()
            if not group.populated():
                released = True
                if all(child.wait is not None for child in children.values()):
                    break
        except (OSError, Unavailable):
            errors.append('containment_release_unknown')
            break
        time.sleep(.005)
    if released is not True:
        errors.append('containment_release_unknown')
    for child in children.values():
        try:
            child.observe_wait()
        except OSError:
            errors.append('outer_wait_unknown')
        if child.wait is None:
            errors.append('outer_wait_unknown')
    return released, errors




class AttemptDomain:
    """Held aggregate parent and role leaves; no launch/admission API.

    Trusted fixed builders create Child objects and bind them here. The empty
    parent applies the ORIGINAL aggregate limits to all its children together.
    """
    ROLES = frozenset({'capture', 'composition', 'diagnostic', 'export'})

    def __init__(self, delegated):
        self.parent = OwnedCgroup(delegated)
        self.groups, self.children, self.closed = {}, {}, {}
        self.final = None
        self.stopped = {}
        try:
            self.parent.write('cgroup.subtree_control', b'+cpu +memory +pids')
            if set(self.parent.read('cgroup.subtree_control').split()) != {'cpu','memory','pids'}:
                raise Unavailable('domain_controllers_unknown')
        except (OSError, Unavailable):
            self.parent.close()
            raise Unavailable('attempt_domain_unavailable') from None

    def leaf(self, role):
        if role not in self.ROLES or role in self.groups or self.final is not None:
            raise Unavailable('owned_worker_role')
        group = OwnedCgroup(self.parent.path)
        if (os.fstat(group.parent).st_dev, os.fstat(group.parent).st_ino) != self.parent.identity:
            group.close()
            raise Unavailable('domain_parent_identity_unknown')
        self.groups[role] = group
        return group

    def bind(self, role, child):
        group = self.groups.get(role)
        if (not isinstance(child,OwnedWorker) or child.acquisition_observations or group is None or role in self.children or role in self.closed or
                child.group_identity != group.identity or child.group_relative != group.relative):
            raise Unavailable('owned_worker_binding')
        self.children[role] = child

    def verified(self, role):
        if role not in self.children or role in self.closed:
            raise Unavailable('owned_worker_binding')
        try:
            return self.children[role].verify_namespace(self.groups[role])
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            raise Unavailable('owned_worker_namespace_unknown') from None

    def stop_role(self, role, deadline):
        if role in self.closed:
            return self.closed[role]
        group = self.groups[role]
        child = self.children.get(role) or getattr(group,'bootstrap_child',None)
        errors=list(child.acquisition_observations) if child else []
        if child and child.wait is None and child.pidfd is not None:
            try:
                signal.pidfd_send_signal(child.pidfd,signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                errors.append('owned_launcher_stop_unknown')
        # A bound launcher can be killed before it joins its leaf. Repeated
        # held-FD kill closes the bootstrap interval; it grants no PID/session
        # reconstruction authority and never signals another cgroup.
        released=None
        while time.monotonic()<deadline:
            try:
                group.kill()
                if child:
                    child.observe_wait()
                if not group.populated():
                    released=True
                    if child is None or child.wait is not None:
                        break
                else:
                    released=None
            except (OSError,Unavailable):
                errors.append('containment_release_unknown')
                break
            time.sleep(.005)
        if released is not True:
            errors.append('containment_release_unknown')
        if child and child.wait is None:
            errors.append('outer_wait_unknown')
        fact = dict(released=released, identity=list(group.identity), wait=None,
                    namespace_identity=child.namespace_identity if child else None,
                    resource_events=None, errors=sorted(set(errors+self.stopped.get(role,{}).get('errors',[]))))
        if child:
            fact['wait'] = dataclasses.asdict(child.wait) if child.wait else None
        self.stopped[role] = fact
        return fact

    def close_role(self, role, deadline):
        if role in self.closed:
            return self.closed[role]
        stopped=self.stop_role(role,deadline)
        fact={**stopped,'errors':stopped['errors'].copy()}
        group=self.groups[role]
        child=self.children.get(role) or getattr(group,'bootstrap_child',None)
        if child:
            fact['wait'] = dataclasses.asdict(child.wait) if child.wait else None
            try:
                fact['errors'].extend(child.close())
            except OSError:
                fact['errors'].append('worker_close_unknown')
        try:
            fact['resource_events'] = group.events()
        except (OSError, Unavailable):
            fact['errors'].append('resource_events_unknown')
        try:
            fact['errors'].extend(group.close())
        except OSError:
            fact['errors'].append('cgroup_close_unknown')
        fact['errors'] = sorted(set(fact['errors']))
        self.closed[role] = fact
        return fact

    def close(self, deadline):
        if self.final is not None:
            return self.final
        # Children close before the held aggregate parent. Every remaining role
        # gets cleanup even if another close reports uncertainty.
        for role in self.groups:
            if role not in self.closed:
                self.close_role(role, deadline)
        released, errors = end_containment(self.parent, {}, deadline)
        try:
            resources = self.parent.events()
        except (OSError, Unavailable):
            resources = None
            errors.append('resource_events_unknown')
        try:
            errors.extend(self.parent.close())
        except OSError:
            errors.append('cgroup_close_unknown')
        self.final = dict(released=released, errors=sorted(set(errors)),
                          resource_events=resources, roles=self.closed.copy(),
                          aggregate_resources=CONTROLS.copy())
        return self.final
