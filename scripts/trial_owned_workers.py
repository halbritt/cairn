"""Internal owned cgroup leaf primitives; no public request or native execution route.

Held cgroup directory and control file descriptors are observations and cleanup
authority, never provider admission.
"""
import os
from pathlib import Path
import uuid

CONTROLS = dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32, cpu_max='100000 100000')


class Unavailable(Exception):
    pass


class OwnedCgroup:
    """Only an exclusive new domain leaf; all effects use held directory/file FDs.

    ``controls`` is an explicit resource policy. None keeps the fixture default,
    CONTROLS, unchanged; another route passes its own selected policy (which may
    be empty) instead of inheriting the fixture caps.
    """
    def __init__(self, delegated, controls=None):
        controls = CONTROLS if controls is None else controls
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
            for field, value in controls.items():
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
