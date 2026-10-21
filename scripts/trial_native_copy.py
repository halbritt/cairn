"""Retained copy of the declared native binary for an original-execution legacy run.

The harness copies the declared binary into the private run directory and
checks the copy's SHA-256 against the declaration. It then runs ``--version``
on that copy. Before each attempt it re-hashes the copy, and the sandbox
mounts it read-only at the target ``~/.local/bin/claude``, where the provider
runs it by absolute path. The retained host source (``observed.source``) and
the child target (``observed.target``) are recorded as distinct values.

These are harness observations of the bytes it mounted. They are not
provider attestation of the model that served a request.
"""
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import stat

COPY_LIMIT = 2 * 1024 * 1024 * 1024
# Acceptance limit applied AFTER the existing capture returns: --version stdout and
# stderr byte counts above it refuse. It does not bound what the generic capture
# buffers (trial_native_capture keeps its own larger finite limits), and it is not
# a bound on the original aggregate memory or traffic envelope.
VERSION_LIMIT = 4096
VERSION_TIMEOUT = 60


def _open_regular(path):
    """Open without blocking on a FIFO or device, then refuse any descriptor that is not a regular file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('original_native_not_regular')
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, 'rb')


def _hash(path):
    """(sha256, bytes) of a regular file, refusing past COPY_LIMIT."""
    digest, total = hashlib.sha256(), 0
    with _open_regular(path) as source:
        while block := source.read(1024 * 1024):
            total += len(block)
            if total > COPY_LIMIT:
                raise ValueError('original_native_too_large')
            digest.update(block)
    return digest.hexdigest(), total


def file_sha256(path):
    return _hash(path)[0]


def sandbox_target(home=None):
    return (Path.home() if home is None else Path(home)) / '.local/bin/claude'


def _copy(path, copy, size):
    """Copy at most the size that was hashed; a source that grew refuses before writing past it."""
    # The descriptor actually read is checked, even if the path was swapped; it is
    # owned before the destination opens, so a destination failure leaks nothing.
    with _open_regular(path) as source:
        fd_out = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd_out, 'wb') as target:
            written = 0
            while block := source.read(min(1024 * 1024, size + 1 - written)):
                if written + len(block) > size:
                    raise ValueError('original_native_mismatch')
                target.write(block)
                written += len(block)
            if written != size:
                raise ValueError('original_native_mismatch')
            target.flush()
            os.fsync(target.fileno())


def _version(copy, directory):
    """--version through the existing bounded capture; bounded output, reaped child, clear custody."""
    from trial_native_capture import capture
    from trial_process_custody import read_custody
    stdout, _, code, _ = capture([str(copy), '--version'], VERSION_TIMEOUT, directory / 'version-timing.json',
                                 custody_path=directory / 'version-custody.json')
    custody = read_custody(directory / 'version-custody.json')
    if (custody['classification'] != 'completed' or not custody['leader_reaped']
            or custody['cleanup']['status'] != 'observed_clear'):
        raise ValueError('original_native_version_unobserved')
    if code != 0:
        raise ValueError('original_native_version_failed')
    if len(stdout) > VERSION_LIMIT or custody['stderr_bytes'] > VERSION_LIMIT:
        raise ValueError('original_native_version_unbounded')
    return stdout.decode('utf-8', errors='replace').strip()


def retain(path, sha256, out):
    """Copy, re-hash and version the declared binary before any store or actor starts."""
    if not (isinstance(path, str) and Path(path).is_absolute() and isinstance(sha256, str)
            and re.fullmatch('[0-9a-f]{64}', sha256)):
        raise ValueError('original_native_declaration')
    digest, size = _hash(path)
    if digest != sha256:
        raise ValueError('original_native_mismatch')
    directory = Path(out) / 'native'
    directory.mkdir(mode=0o700)
    copy = directory / 'claude'
    try:
        _copy(path, copy, size)
        os.chmod(copy, 0o500)
        observed = file_sha256(copy)
        if observed != sha256:  # the source changed between hash and copy
            raise ValueError('original_native_mismatch')
        version = _version(copy, directory)
    except BaseException:
        copy.unlink(missing_ok=True)  # never leave a partial or unverified copy behind
        raise
    return dict(declared=dict(path=path, sha256=sha256),
                observed=dict(source=str(copy), sha256=observed, version=version,
                              observed_at=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                              target=str(sandbox_target())))
