"""Seal selected Python rollback files and publish additive immutable generations.

An exhaustive selected-file seal is not runtime or upstream provenance
qualification. This module never imports or executes the selected interpreter,
packages, workers or models. See docs/semantic-python-closure.md.
"""
import argparse
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile


INPUT_SCHEMA = 'cairn.semantic-python-inputs/1'
PAYLOAD_SCHEMA = 'cairn.semantic-python-payload/1'
CONTROL_SCHEMA = 'cairn.semantic-python-controls/1'
MARKER = b'cairn.semantic-python-closure-stage/1\n'


class ClosureError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def absolute(value):
    path = Path(value)
    if (not path.is_absolute() or str(path) != os.path.normpath(str(path))
            or any(c in str(path) for c in '\x00\r\n')):
        raise ClosureError('expected normalized absolute path: ' + str(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ClosureError('symlink in selected path: ' + str(part))
    return path


def member_name(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or any(c in value for c in '\x00\r\n')
            or PurePosixPath(value).is_absolute()
            or any(x in ('', '.', '..') for x in value.split('/'))):
        raise ClosureError('unsafe relative member: ' + str(value))
    return value


def regular(path, output=None):
    """Hash a stable regular input; optionally copy through the same descriptor."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ClosureError('not a regular file: ' + str(path))
        checksum, length = hashlib.sha256(), 0
        while data := stream.read(1024 * 1024):
            checksum.update(data)
            length += len(data)
            if output is not None:
                output.write(data)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, x) != getattr(after, x) for x in fields):
        raise ClosureError('input changed while reading: ' + str(path))
    return {'type': 'file', 'bytes': length, 'sha256': checksum.hexdigest(),
            'mode': stat.S_IMODE(before.st_mode)}


def tree(root):
    """Inventory every directory, regular file and safe internal symlink."""
    root = absolute(root)
    if not root.is_dir():
        raise ClosureError('selected root is not a directory: ' + str(root))
    result = {'.': {'type': 'directory', 'mode': stat.S_IMODE(root.stat().st_mode)}}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted([*dirs, *names]):
            path = Path(base) / name
            relative = member_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                if (Path(target).is_absolute() or '\\' in target
                        or any(c in target for c in '\x00\r\n')):
                    raise ClosureError('unsafe symlink: ' + relative)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ClosureError('dangling or cyclic symlink: ' + relative) from exc
                if not resolved.is_relative_to(root):
                    raise ClosureError('escaping symlink: ' + relative)
                result[relative] = {'type': 'symlink', 'target': target,
                                    'mode': stat.S_IMODE(mode)}
            elif stat.S_ISDIR(mode):
                result[relative] = {'type': 'directory', 'mode': stat.S_IMODE(mode)}
            elif stat.S_ISREG(mode):
                result[relative] = regular(path)
            else:
                raise ClosureError('unsupported selected file type: ' + relative)
    return result


def seal_inputs(roots, provenance):
    """Bind complete selected trees and selected provenance; no resolution/install."""
    if not roots or not isinstance(provenance, dict) or not provenance:
        raise ClosureError('selected roots and explicit provenance are required')
    source, entries = {}, {}
    for role, value in sorted(roots.items()):
        member_name(role)
        if '/' in role:
            raise ClosureError('input role must be one path component')
        root = absolute(value)
        if any(root == Path(x) or root.is_relative_to(x) or Path(x).is_relative_to(root)
               for x in source.values()):
            raise ClosureError('overlapping input roots')
        source[role] = str(root)
        for name, item in tree(root).items():
            entries[role if name == '.' else role + '/' + name] = item
    selection = {'roots': source, 'entries': entries, 'provenance': provenance}
    return {'schema': INPUT_SCHEMA, 'selection': selection,
            'selection_sha256': digest(canonical(selection))}


def validate_selection(seal):
    if (set(seal) != {'schema', 'selection', 'selection_sha256'}
            or seal['schema'] != INPUT_SCHEMA
            or digest(canonical(seal['selection'])) != seal['selection_sha256']):
        raise ClosureError('input selection manifest digest or schema differs')
    selection = seal['selection']
    if set(selection) != {'roots', 'entries', 'provenance'}:
        raise ClosureError('invalid input selection fields')
    for name in selection['entries']:
        member_name(name)
    actual = seal_inputs(selection['roots'], selection['provenance'])
    if actual != seal:
        raise ClosureError('selected input inventory differs; refusing incomplete/tampered input')
    return selection


def payload_descriptor(seal):
    entries = {'.': {'type': 'directory', 'mode': 0o555}}
    for name, item in seal['selection']['entries'].items():
        record = dict(item)
        record['mode'] = (0o777 if item['type'] == 'symlink' else
                          0o555 if item['type'] == 'directory' or item['mode'] & 0o111 else 0o444)
        entries[name] = record
    return {'schema': PAYLOAD_SCHEMA, 'selection_sha256': seal['selection_sha256'],
            'provenance': seal['selection']['provenance'], 'entries': entries}


def verify_generation(path, descriptor):
    path = absolute(path)
    address = digest(canonical(descriptor))
    if path.name != address or tree(path) != descriptor['entries']:
        raise ClosureError('immutable generation differs; refusing repair: ' + str(path))
    return path


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_pending(path):
    # Only a newly allocated, unpublished directory owned by this invocation.
    for base, _, _ in os.walk(path, followlinks=False):
        Path(base).chmod(0o700)
    shutil.rmtree(path)


@contextmanager
def stage_root(value, input_roots=()):
    root = absolute(value)
    if (not root.is_dir() or root.stat().st_uid != os.getuid()
            or stat.S_IMODE(root.stat().st_mode) != 0o700):
        raise ClosureError('stage must be an existing owned 0700 directory')
    for source in input_roots:
        source = absolute(source)
        if root == source or root.is_relative_to(source) or source.is_relative_to(root):
            raise ClosureError('stage overlaps selected input')
    marker = root / '.cairn-python-closure-stage'
    if not marker.exists() and list(root.iterdir()):
        raise ClosureError('untagged stage must be empty')
    try:
        fd = os.open(marker, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(marker, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise ClosureError('unsafe stage marker')
    with os.fdopen(fd, 'r+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = lock.read()
        if not data:
            if list(root.iterdir()) != [marker]:
                raise ClosureError('untagged stage must be empty')
            lock.write(MARKER)
            lock.flush()
            os.fsync(lock.fileno())
        elif data != MARKER:
            raise ClosureError('stage marker differs')
        if any(x.name not in (marker.name, 'payloads', 'controls', 'manifests') for x in root.iterdir()):
            raise ClosureError('unexpected stage contents')
        for name in ('payloads', 'controls', 'manifests'):
            directory = absolute(root / name)
            directory.mkdir(mode=0o700, exist_ok=True)
            if (not directory.is_dir() or directory.stat().st_uid != os.getuid()
                    or stat.S_IMODE(directory.stat().st_mode) != 0o700):
                raise ClosureError('unsafe generation parent: ' + str(directory))
        fsync_directory(root)
        yield root


def commit_pending(pending, target):
    """Linux no-replace atomic rename, also safe against noncooperating writers."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(pending), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            return False
        raise OSError(code, os.strerror(code), str(target))
    fsync_directory(target.parent)
    return True


def publish_generation(parent, descriptor, write_members):
    address = digest(canonical(descriptor))
    target = parent / address
    if target.exists() or target.is_symlink():
        return verify_generation(target, descriptor)
    pending = Path(tempfile.mkdtemp(prefix='.pending-' + address + '-', dir=parent))
    try:
        write_members(pending)
        for base, _, _ in os.walk(pending, topdown=False, followlinks=False):
            Path(base).chmod(0o555)
            fsync_directory(base)
        if tree(pending) != descriptor['entries']:
            raise ClosureError('new generation differs before publication')
        if not commit_pending(pending, target):
            verify_generation(target, descriptor)
    finally:
        if pending.exists():
            remove_pending(pending)
    return verify_generation(target, descriptor)


def publish_payload(stage, seal):
    selection = validate_selection(seal)
    descriptor = payload_descriptor(seal)

    def copy_members(pending):
        for name, item in sorted(selection['entries'].items()):
            path = pending / name
            if item['type'] == 'directory':
                path.mkdir(mode=0o700)
            elif item['type'] == 'symlink':
                path.symlink_to(item['target'])
            else:
                role, relative = name.split('/', 1)
                with path.open('xb') as output:
                    observed = regular(Path(selection['roots'][role]) / relative, output)
                    output.flush()
                    os.fchmod(output.fileno(), descriptor['entries'][name]['mode'])
                    os.fsync(output.fileno())
                if observed != item:
                    raise ClosureError('input changed during publication: ' + name)
        # Directory additions/removals and link changes are inputs too.
        validate_selection(seal)

    with stage_root(stage, selection['roots'].values()) as root:
        path = publish_generation(root / 'payloads', descriptor, copy_members)
    return {'address': path.name, 'path': str(path), 'descriptor': descriptor}


def publish_controls(stage, payload, files):
    """Address rendered control bytes separately; they may name the payload."""
    verify_generation(payload['path'], payload['descriptor'])
    entries = {'.': {'type': 'directory', 'mode': 0o555}}
    for name, data in sorted(files.items()):
        member_name(name)
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                entries[str(parent)] = {'type': 'directory', 'mode': 0o555}
        entries[name] = {'type': 'file', 'mode': 0o555 if name.startswith('bin/') else 0o444,
                         'bytes': len(data), 'sha256': digest(data)}
    descriptor = {'schema': CONTROL_SCHEMA, 'payload_address': payload['address'], 'entries': entries}

    def write_members(pending):
        for name, item in sorted(entries.items()):
            if name == '.':
                continue
            path = pending / name
            if item['type'] == 'directory':
                path.mkdir(mode=0o700)
            else:
                with path.open('xb') as stream:
                    stream.write(files[name])
                    stream.flush()
                    os.fchmod(stream.fileno(), item['mode'])
                    os.fsync(stream.fileno())

    with stage_root(stage) as root:
        path = publish_generation(root / 'controls', descriptor, write_members)
    return {'address': path.name, 'path': str(path), 'descriptor': descriptor,
            'payload_address': payload['address']}


def read_json(path):
    path = absolute(path)
    regular(path)
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inventory = commands.add_parser('inventory')
    inventory.add_argument('--root', action='append', required=True, metavar='ROLE=ABSOLUTE_PATH')
    inventory.add_argument('--provenance', required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--selection', required=True)
    publish.add_argument('--stage', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--publication', required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        pairs = [item.split('=', 1) for item in args.root]
        if any(len(pair) != 2 for pair in pairs) or len(dict(pairs)) != len(pairs):
            raise ClosureError('each unique root requires ROLE=ABSOLUTE_PATH')
        result = seal_inputs(dict(pairs), read_json(args.provenance))
    elif args.command == 'publish':
        result = publish_payload(args.stage, read_json(args.selection))
    else:
        publication = read_json(args.publication)
        verify_generation(publication['path'], publication['descriptor'])
        result = {'state': 'selected-file-integrity-verified', 'runtime_qualification': 'unobserved'}
    print(canonical(result).decode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except (ClosureError, OSError, ValueError, KeyError) as exc:
        print('Python closure: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
