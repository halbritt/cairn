"""Internal owned worker primitives; no public request or native execution route.

Only trusted fixed runner builders create Child commands. Held cgroup/process
handles are observations and cleanup authority, never provider admission.
"""
import dataclasses
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


class OwnedWorker(Child):
    """Direct-launcher pidfd for the new domain; legacy Child stays unchanged."""
    def __init__(self, group, argv):
        super().__init__(group,argv)
        self.group_identity=group.identity
        self.group_relative=group.relative
        self.pidfd=None
        try:
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            self.boot=str(uuid.UUID(boot)) if str(uuid.UUID(boot))==boot else None
        except (OSError,ValueError):
            self.boot=None
        # Still unreaped: opening this handle cannot bind a reused PID.
        try:
            self.pidfd = os.pidfd_open(self.process.pid, 0)
        except OSError:
            # Preserve the direct child/streams for the caller's held leaf
            # cleanup. No PID fallback, no actor gate, no invented wait.
            group.bootstrap_child = self
            raise Unavailable('owned_launcher_handle_unknown') from None


    def observe_wait(self):
        fact=super().observe_wait()
        if fact is not None and not isinstance(fact,OwnedWaitFact):
            self.wait=OwnedWaitFact(fact.pid,fact.start_ticks,fact.raw_status,fact.returncode,self.boot)
        return self.wait

    def close(self):
        errors=[]
        if self.pidfd is not None:
            try:
                os.close(self.pidfd)
            except OSError:
                errors.append('worker_descriptor_close_unknown')
            self.pidfd=None
        errors.extend(super().close())
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
        if (not isinstance(child,OwnedWorker) or group is None or role in self.children or role in self.closed or
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
        errors=['owned_launcher_handle_unknown'] if getattr(group,'bootstrap_child',None) else []
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
