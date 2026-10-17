"""Held, bounded offline copies. No mount or process interface is provided."""
import codecs
import hashlib
import os
from pathlib import Path
import stat

from trial_paired_request import PIN_LIMIT, Refusal, need

BLOCK = 8192  # Bound copy/recheck scratch independently of accepted file length.
TEXT_BLOCK = 8192
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class AcquisitionRefusal(Refusal):
    def __init__(self, code, cleanup_faults):
        super().__init__(code)
        self.cleanup_faults = cleanup_faults


def record_cleanup(primary, faults):
    """Best-effort annotation; the owner retains facts if a primary is immutable."""
    try:
        primary.cleanup_faults = faults
    except BaseException:
        # Even a custom failing setter cannot replace the original failure.
        # Snapshot.cleanup_faults remains the authoritative owner-side result.
        return False
    return True


def identity(observed):
    return (observed.st_dev, observed.st_ino, observed.st_mode,
            observed.st_uid, observed.st_gid)


def file_identity(observed):
    return identity(observed) + (observed.st_nlink, observed.st_size,
                                 observed.st_mtime_ns, observed.st_ctime_ns)


def fd_identity(observed):
    return observed.st_dev, observed.st_ino, stat.S_IFMT(observed.st_mode)


def regular(observed, limit):
    need(stat.S_ISREG(observed.st_mode) and observed.st_uid == os.geteuid()
         and observed.st_nlink == 1 and not observed.st_mode & 0o7022
         and 0 < observed.st_size <= limit, 'source_file_policy')


class HeldFD:
    """Detach before close; never retry a failed close or close a reused identity."""
    def __init__(self, fd, observed):
        self.fd = fd
        self.identity = identity(observed)
        self.anchor = fd_identity(observed)

    def check(self):
        need(self.fd is not None and fd_identity(os.fstat(self.fd)) == self.anchor,
             'held_identity_unknown')

    def close(self):
        fd, self.fd = self.fd, None
        if fd is None:
            return
        need(fd_identity(os.fstat(fd)) == self.anchor, 'held_identity_unknown')
        os.close(fd)


class HeldTree:
    """Retain every directory edge from /, and verify reachability by held parents."""
    def __init__(self):
        self.leases = []
        self.edges = []
        self.acquisition_faults = []

    def own(self, fd):
        try:
            lease = HeldFD(fd, os.fstat(fd))
        except BaseException:
            # No anchor was observed. The number could already have been reused;
            # do not reconstruct ownership or close a possibly foreign FD.
            self.acquisition_faults.append('descriptor_cleanup_unknown')
            raise
        self.leases.append(lease)
        return lease

    def directory(self, raw):
        need(isinstance(raw, str) and raw.startswith('/') and '\0' not in raw,
             'snapshot_path')
        path = Path(raw)
        need(str(path) == raw and '..' not in path.parts, 'snapshot_path')
        parent = self.own(os.open('/', READ_FLAGS | os.O_DIRECTORY))
        for name in path.parts[1:]:
            child = self.own(os.open(name, READ_FLAGS | os.O_DIRECTORY,
                                     dir_fd=parent.fd))
            self.edges.append((parent, name, child))
            parent = child
        return parent

    def open_source(self, path, limit):
        need(isinstance(path, str), 'snapshot_path')
        parent = self.directory(str(Path(path).parent))
        leaf = self.own(os.open(Path(path).name, READ_FLAGS, dir_fd=parent.fd))
        observed = os.fstat(leaf.fd)
        regular(observed, limit)
        self.edges.append((parent, Path(path).name, leaf))
        return leaf, file_identity(observed)

    def check(self):
        for lease in self.leases:
            lease.check()
        for parent, name, child in self.edges:
            need(identity(os.stat(name, dir_fd=parent.fd, follow_symlinks=False))
                 == child.identity, 'held_path_changed')

    def close(self):
        faults = list(self.acquisition_faults)
        for lease in reversed(self.leases):
            try:
                lease.close()
            except BaseException:
                # Cleanup cannot skip other owned leases on an interruption.
                # The fixed unknown result is observable; it is never success.
                faults.append('descriptor_cleanup_unknown')
        return tuple(sorted(set(faults)))


def write_all(fd, block):
    pending = memoryview(block)
    while pending:
        written = os.write(fd, pending)
        need(0 < written <= len(pending), 'snapshot_write_unknown')
        pending = pending[written:]


class Snapshot:
    """One consumed directory; failures leave private partial files, never retry."""
    def __init__(self, output):
        self.tree = HeldTree()
        self.files = {}
        self.sources = {}
        self.directory = None
        self.closed = False
        self.cleanup_faults = ()
        self.copied_bytes = 0
        try:
            path = Path(output)
            need(isinstance(output, str) and str(path) == output
                 and path.is_absolute() and path.name not in ('', '.', '..'),
                 'snapshot_path')
            parent = self.tree.directory(str(path.parent))
            os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
            self.directory = self.tree.own(os.open(path.name, READ_FLAGS | os.O_DIRECTORY,
                                                   dir_fd=parent.fd))
            self.tree.edges.append((parent, path.name, self.directory))
            need(stat.S_IMODE(os.fstat(self.directory.fd).st_mode) == 0o700,
                 'snapshot_directory_policy')
            os.fsync(parent.fd)
        except (OSError, Refusal) as error:
            cleanup = self.close()
            code = ('preparation_consumed' if isinstance(error, FileExistsError)
                    else str(error) if isinstance(error, Refusal)
                    else 'preparation_io_or_shape_unknown')
            raise AcquisitionRefusal(code, cleanup) from None
        except BaseException as primary:
            record_cleanup(primary, self.close())
            raise

    def _destination(self, name):
        need(not self.closed, 'snapshot_consumed')
        lease = self.tree.own(os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL
                                      | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                      dir_fd=self.directory.fd))
        self.tree.edges.append((self.directory, name, lease))
        return lease

    def _seal(self, name, lease, expected, length, executable):
        os.fchmod(lease.fd, 0o500 if executable else 0o400)
        os.fsync(lease.fd)
        observed = os.fstat(lease.fd)
        lease.identity = identity(observed)
        regular(observed, length)
        need(observed.st_size == length, 'snapshot_length_mismatch')
        readonly = self.tree.own(os.open(name, READ_FLAGS, dir_fd=self.directory.fd))
        need(file_identity(os.fstat(readonly.fd)) == file_identity(observed),
             'snapshot_identity_changed')
        # Both descriptors remain in the owner, but the writable one is consumed.
        lease.close()
        self.tree.leases.remove(lease)
        self.tree.edges = [edge for edge in self.tree.edges if edge[2] is not lease]
        self.tree.edges.append((self.directory, name, readonly))
        self.files[name] = (readonly, file_identity(observed), expected, length)
        self._readback(name)
        os.fsync(self.directory.fd)

    def copy(self, name, reference, limit, executable=False):
        need(not self.closed, 'snapshot_consumed')
        source, initial = self.tree.open_source(reference['path'], limit)
        need(initial[6] <= PIN_LIMIT - self.copied_bytes, 'snapshot_total_byte_limit')
        source_mode = os.fstat(source.fd).st_mode
        need(bool(source_mode & 0o111) == executable, 'source_executable_policy')
        self.sources[name] = (source, initial)
        destination = self._destination(name)
        digest, length = hashlib.sha256(), 0
        while True:
            block = os.read(source.fd, min(BLOCK, limit + 1 - length))
            if not block:
                break
            length += len(block)
            need(length <= limit, 'snapshot_byte_limit')
            need(len(block) <= PIN_LIMIT - self.copied_bytes, 'snapshot_total_byte_limit')
            self.copied_bytes += len(block)
            write_all(destination.fd, block)
            digest.update(block)
            block = None  # End this buffer before the next read allocates another.
        need(file_identity(os.fstat(source.fd)) == initial, 'source_changed')
        need(length == initial[6] and digest.hexdigest() == reference['sha256'],
             'source_pin_mismatch')
        self._seal(name, destination, digest.hexdigest(), length, executable)

    def generated(self, name, content):
        destination = self._destination(name)
        write_all(destination.fd, content)
        self._seal(name, destination, hashlib.sha256(content).hexdigest(), len(content), False)

    def _readback(self, name, collect=False, *, text=False):
        lease, initial, digest, length = self.files[name]
        lease.check()
        need(file_identity(os.fstat(lease.fd)) == initial, 'snapshot_changed')
        os.lseek(lease.fd, 0, os.SEEK_SET)
        actual, count, blocks = hashlib.sha256(), 0, []
        decoder = codecs.getincrementaldecoder('utf-8')() if text else None
        invalid_utf8 = False
        while True:
            block = os.read(lease.fd, min(TEXT_BLOCK if text else BLOCK, length + 1 - count))
            if not block:
                break
            count += len(block)
            need(count <= length, 'snapshot_changed')
            actual.update(block)
            if text and not invalid_utf8:
                try:
                    blocks.append(decoder.decode(block))
                except UnicodeError:
                    # Finish original-byte and identity validation even on bad
                    # UTF-8; do not retain the exception's raw input backing.
                    invalid_utf8 = True
                    decoder = None
                    blocks.clear()
            elif collect:
                blocks.append(block)
            block = None  # Non-collecting validation holds only one read buffer.
        need(count == length and actual.hexdigest() == digest
             and file_identity(os.fstat(lease.fd)) == initial, 'snapshot_changed')
        if text:
            if invalid_utf8:
                raise UnicodeError('snapshot_utf8')
            blocks.append(decoder.decode(b'', final=True))
            return ''.join(blocks)
        return b''.join(blocks) if collect else None

    def read(self, name):
        need(not self.closed, 'snapshot_consumed')
        need(self.files[name][3] <= 65536, 'snapshot_parse_byte_limit')
        return self._readback(name, collect=True)

    def read_text(self, name):
        """Validated UTF-8 text without collecting or joining complete raw bytes.

        The decoder may concatenate its <=3-byte carry with one TEXT_BLOCK read.
        Decoded fragments, joined text and caller graphs still need working
        allocation custody; this method grants no attempt resource allowance.
        """
        need(not self.closed, 'snapshot_consumed')
        need(self.files[name][3] <= 65536, 'snapshot_parse_byte_limit')
        return self._readback(name, text=True)

    def _verify_files(self):
        need(not self.closed, 'snapshot_consumed')
        self.tree.check()
        for source, initial in self.sources.values():
            need(file_identity(os.fstat(source.fd)) == initial, 'source_changed')
        for name in self.files:
            self._readback(name)

    def _binding(self, name):
        _, initial, digest, length = self.files[name]
        return dict(sha256=digest, bytes=length,
                    dev=initial[0], ino=initial[1], mode=initial[2],
                    uid=initial[3], gid=initial[4], nlink=initial[5],
                    source_identity=self._source_identity(name))

    def bindings(self):
        self._verify_files()
        return {name: self._binding(name) for name in self.files}

    def verify_bindings(self, expected):
        """Same live checks, without exporting a second complete metadata graph."""
        self._verify_files()
        need(type(expected) is dict and self.files.keys() == expected.keys(),
             'snapshot_bindings_changed')
        for name in self.files:
            need(self._binding(name) == expected[name], 'snapshot_bindings_changed')

    def _source_identity(self, name):
        if name not in self.sources:
            return None
        initial = self.sources[name][1]
        return dict(dev=initial[0], ino=initial[1], mode=initial[2],
                    uid=initial[3], gid=initial[4], nlink=initial[5],
                    bytes=initial[6], mtime_ns=initial[7], ctime_ns=initial[8])

    def close(self):
        if not self.closed:
            self.closed = True
            self.cleanup_faults = self.tree.close()
        return self.cleanup_faults
