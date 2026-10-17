"""Private original-session byte custody, not export/containment/grading authority.

Regular-file writes/fsync may block: runtime admission still needs the external
bounded I/O/physical contract. No raw copy or new traffic/memory grant is made.
The original ticket stays held, including on publication/close uncertainty.
"""
import hashlib
import os
from pathlib import Path
import stat

from trial_sdk_session_resources import SDKSessionResources


class RetentionError(ValueError):
    pass


def require(value, code):
    if not value: raise RetentionError(code)


class SessionStreamRetention:
    def __init__(self, binding, path):
        # Publish this shell in the run before invoking construction.
        try:
            self.binding = binding
            self.state = 'opening'
            self.fds = {}
            self.observed = dict(stdout=0, stderr=0)
            self.written = dict(stdout=0, stderr=0)
            self.synced = dict(stdout=0, stderr=0)
            self.hashes = {name: hashlib.sha256() for name in self.observed}
            self.identities = {}
            self.last_generation = 0
            self.pending = None
            self.close_attempted = set()
            self.faults = set()
            self._terminal_graph = None
            require(type(binding) is SDKSessionResources, 'retention_binding')
            binding.resources._check()
            binding.resources._lookup(binding.resources._memory, binding.allocation)
            require(not binding.owner.snapshot()['launch_attempted'], 'retention_before_launch')
            path = Path(path)
            require(path.is_absolute() and '..' not in path.parts, 'retention_path')
            self.path = str(path)
            for parent in path.parents:
                require(not parent.is_symlink(), 'retention_path')
            parent = path.parent.stat()
            require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                    'retention_private_parent')
            self._open('parent', path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            held = os.fstat(self.parent)
            require((held.st_dev, held.st_ino) == (parent.st_dev, parent.st_ino)
                    and stat.S_IMODE(held.st_mode) == 0o700, 'retention_parent_changed')
            os.mkdir(path.name, 0o700, dir_fd=self.parent)  # Existing/partial never reopens.
            self._open('directory', path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=self.parent)
            for name in self.observed:
                self._open(name, name+'.bin', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=self.directory)
                meta = os.fstat(self.fds[name])
                self.identities[name] = (meta.st_dev, meta.st_ino)
            os.fsync(self.directory)
            os.fsync(self.parent)
            self.state = 'open'
        except BaseException:
            self.state = 'unknown'
            binding.resources.freeze()
            raise

    @property
    def directory(self): return self.fds.get('directory')

    @property
    def parent(self): return self.fds.get('parent')

    def _open(self, name, path, flags, mode=0o600, *, dir_fd=None):
        fd, published = None, False
        try:
            fd = os.open(path, flags, mode, dir_fd=dir_fd)
            self.fds[name] = fd
            published = True
        finally:
            if fd is not None and not published:
                self.fds.pop(name, None)
                self.close_attempted.add(name)
                self.faults.add(name+'_close_unknown')
                os.close(fd)
                self.faults.remove(name+'_close_unknown')

    def record(self, name, slab, count, generation):
        """Called once per actual positive read, before parser/clock callbacks.

        Also accepts a known positive prefix from the original owner's exception
        receipt. Such a read still poisons the caller and cannot become success.
        """
        require(self.state == 'open', 'retention_consumed')
        try:
            self.state = 'writing'
            require(type(generation) is int and generation > self.last_generation,
                    'retention_generation')
            self.last_generation = generation
            require(name in self.observed and type(count) is int and 0 < count <= 512
                    and type(slab) is bytearray and count <= len(slab), 'retention_read')
            self.binding.resources._lookup(self.binding.resources._memory, self.binding.allocation)
            self.pending = memoryview(slab)[:count]
            self.observed[name] += count
            # Existing stream limits, never a new allowance. Preserve the actual
            # over-limit observation, but do not publish a successful file receipt.
            require(self.observed['stdout'] <= 3*1024*1024 and
                    sum(self.observed.values()) <= 4*1024*1024, 'retention_bound')
            offset = 0
            while offset < count:
                with self.pending[offset:] as tail:
                    written = os.write(self.fds[name], tail)
                require(type(written) is int and 0 < written <= count-offset, 'retention_write')
                offset += written
                self.written[name] += written
            self.hashes[name].update(self.pending)
            os.fsync(self.fds[name])
            self.synced[name] = self.written[name]
            self.pending.release()
            self.pending = None
            self.state = 'open'
        except BaseException:
            self.state = 'unknown'
            self.binding.resources.freeze()
            raise

    def close(self):
        require(getattr(self, 'state', 'opening') not in ('writing', 'sealing', 'closing'),
                'retention_busy')
        return self._close()

    def _close(self):
        """Close each independently owned descriptor once; never retry uncertainty."""
        # No OS acquisition precedes these fields. A shell interrupted during
        # container/hash construction therefore owns no descriptors yet.
        if 'faults' not in self.__dict__: return ()
        primary = None
        was_closed = self.state == 'closed'
        try:
            self.state = 'closing'
            for name in tuple(reversed(self.fds)):
                if name in self.close_attempted: continue
                fd = self.fds[name]
                if fd is None: continue
                self.close_attempted.add(name)
                self.faults.add(name+'_close_unknown')
                try:
                    os.close(fd)
                    self.fds[name] = None
                    self.faults.remove(name+'_close_unknown')
                except BaseException as error:
                    if primary is None: primary = error
            if primary is not None: raise primary
        except BaseException:
            self.state = 'unknown'
            self.binding.resources.freeze()
            raise
        self.state = 'closed' if was_closed else 'unknown'
        return tuple(sorted(self.faults))

    def finish(self, pump):
        require(self.state == 'open', 'retention_consumed')
        try:
            self.state = 'sealing'
            require(pump._retention is self and pump._resources is self.binding,
                    'retention_pump_binding')
            native = self.binding.owner.snapshot()
            require(native['wait'] is not None and native['stdout_eof'] and native['stderr_eof']
                    and native['release'] is not None and native['release']['released']
                    and native['release']['handles_detached'] and not native['has_handles'],
                    'retention_wait_pending')
            require(pump._failure is None and not pump._cleanup_unknown and pump._lines is None
                    and pump._assembler.pending_bytes == 0, 'retention_stream_unknown')
            for name in self.observed:
                require(self.observed[name] == self.written[name] == self.synced[name] == pump._bytes[name]
                        and self.hashes[name].hexdigest() == pump._hash[name].hexdigest(),
                        'retention_stream_binding')
                meta = os.fstat(self.fds[name])
                require(stat.S_ISREG(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o600
                        and meta.st_uid == os.getuid() and meta.st_nlink == 1
                        and (meta.st_dev, meta.st_ino) == self.identities[name]
                        and meta.st_size == self.written[name], 'retention_file_changed')
                os.fsync(self.fds[name])
                # Reuse the ended stderr slab only after both EOFs and no pending
                # stream/error loan. No full-file readback or second raw buffer.
                os.lseek(self.fds[name], 0, os.SEEK_SET)
                digest, count = hashlib.sha256(), 0
                with memoryview(pump._stderr) as scratch:
                    while True:
                        n = os.readv(self.fds[name], [scratch])
                        if not n: break
                        count += n
                        require(count <= self.written[name], 'retention_readback_size')
                        with scratch[:n] as part: digest.update(part)
                after = os.fstat(self.fds[name])
                named = os.stat(name+'.bin', dir_fd=self.directory, follow_symlinks=False)
                require((meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) ==
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        and (named.st_dev, named.st_ino) == self.identities[name]
                        and count == self.written[name]
                        and digest.hexdigest() == self.hashes[name].hexdigest(), 'retention_readback_changed')
            os.fsync(self.directory)
            os.fsync(self.parent)
            self._close()
            require(not self.faults, 'retention_close_unknown')
            self.state = 'closed'
        except BaseException:
            self.state = 'unknown'
            self.binding.resources.freeze()
            raise

    def snapshot(self):
        if '_terminal_graph' not in self.__dict__:
            return dict(state='initialization_unknown', qualification='no_completion_receipt')
        return dict(state=self.state, observed=dict(self.observed), written=dict(self.written),
                    synced=dict(self.synced), sha256={k:v.hexdigest() for k,v in self.hashes.items()},
                    close_faults=tuple(sorted(self.faults)), pending_backing=self.pending is not None,
                    terminal_graph_held=self._terminal_graph is not None,
                    qualification='private_local_bytes_not_containment_export_or_grade')
