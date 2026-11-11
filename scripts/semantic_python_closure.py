"""Seal selected Python rollback files and publish additive immutable generations.

An exhaustive selected-file seal is not runtime or upstream provenance
qualification. Inventory/publication do not execute selected code. The explicit
fixed-launch path verifies the selection before executing its worker.
"""
import argparse
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import stat
import sys
import tempfile


INPUT_SCHEMA = 'cairn.semantic-python-inputs/1'
PAYLOAD_SCHEMA = 'cairn.semantic-python-payload/1'
CONTROL_SCHEMA = 'cairn.semantic-python-controls/1'
MANIFEST_SCHEMA = 'cairn.semantic-python-observations/1'
MARKER = b'cairn.semantic-python-closure-stage/1\n'
MODE_NAMES = {'rank': 'worker', 'stream': 'worker-stream', 'embed': 'worker-embeddings'}
MODEL_FILES = ('model_optimized.onnx', 'config.json', 'tokenizer.json',
               'special_tokens_map.json', 'tokenizer_config.json')


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


def tree(root, bindings=None):
    """Inventory every directory, regular file and safe internal symlink."""
    root = absolute(root)
    if not root.is_dir():
        raise ClosureError('selected root is not a directory: ' + str(root))
    bindings = bindings or {}
    result = {'.': {'type': 'directory', 'mode': stat.S_IMODE(root.stat().st_mode)}}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted([*dirs, *names]):
            path = Path(base) / name
            relative = member_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                if relative in bindings:
                    expected = absolute(bindings[relative])
                    if not expected.exists() or path.resolve(strict=True) != expected:
                        raise ClosureError('selected payload reference differs: ' + relative)
                    result[relative] = {'type': 'symlink', 'target': target,
                                        'mode': stat.S_IMODE(mode)}
                    continue
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
    if descriptor['schema'] not in (PAYLOAD_SCHEMA, CONTROL_SCHEMA, MANIFEST_SCHEMA):
        raise ClosureError('unknown generation schema')
    address = digest(canonical(descriptor))
    bindings = descriptor.get('bindings', {})
    if bindings:
        payload = absolute(descriptor['payload_path'])
        if payload.name != descriptor['payload_address']:
            raise ClosureError('payload reference address differs')
        for name, target in bindings.items():
            member_name(name)
            if not absolute(target).is_relative_to(payload):
                raise ClosureError('external control reference is not selected payload')
    if path.name != address or tree(path, bindings) != descriptor['entries']:
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
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
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
        if tree(pending, descriptor.get('bindings')) != descriptor['entries']:
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


def control_entries(files, links):
    entries = {'.': {'type': 'directory', 'mode': 0o555}}
    if set(files) & set(links):
        raise ClosureError('duplicate control file/reference')
    for name in sorted([*files, *links]):
        member_name(name)
        for parent in reversed(PurePosixPath(name).parents):
            if str(parent) == '.':
                continue
            previous = entries.setdefault(str(parent), {'type': 'directory', 'mode': 0o555})
            if previous != {'type': 'directory', 'mode': 0o555}:
                raise ClosureError('control member/parent conflict')
        if name in entries:
            raise ClosureError('control member/parent conflict')
        if name in links:
            entries[name] = {'type': 'symlink', 'mode': 0o777, 'target': links[name]}
        else:
            data = files[name]
            entries[name] = {'type': 'file', 'mode': 0o555 if name.startswith('bin/') else 0o444,
                             'bytes': len(data), 'sha256': digest(data)}
    return entries


def control_descriptor(payload, files, links):
    # Both pending and final generations are one level below controls/. Relative
    # links preserve the selected payload binding while remaining safe to export.
    generation = Path(payload['path']).parent.parent / 'controls' / '_generation'
    relative_links = {name: os.path.relpath(target, generation / PurePosixPath(name).parent)
                      for name, target in links.items()}
    return {'schema': CONTROL_SCHEMA, 'payload_address': payload['address'],
            'payload_path': payload['path'], 'bindings': links,
            'entries': control_entries(files, relative_links)}


def publish_controls(stage, payload, files, links=None):
    """Address rendered control bytes separately; they may name the payload."""
    verify_generation(payload['path'], payload['descriptor'])
    links = links or {}
    payload_root = absolute(payload['path'])
    for target in links.values():
        target = absolute(target)
        if (not target.is_relative_to(payload_root) or not target.exists()
                or target.relative_to(payload_root).as_posix() not in payload['descriptor']['entries']):
            raise ClosureError('control reference is not a selected payload member')
    descriptor = control_descriptor(payload, files, links)
    entries = descriptor['entries']

    def write_members(pending):
        for name, item in sorted(entries.items()):
            if name == '.':
                continue
            path = pending / name
            if item['type'] == 'directory':
                path.mkdir(mode=0o700)
            elif item['type'] == 'symlink':
                path.symlink_to(item['target'])
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
    content = io.BytesIO()
    regular(path, content)
    return json.loads(content.getvalue())


def worker_environment(payload, home, mode):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home),
           'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_CONFIG_HOME': str(home / '.config'),
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
           'TOKENIZERS_PARALLELISM': 'false', 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'ORT_DISABLE_TELEMETRY': '1',
           'LC_ALL': 'C.UTF-8', 'LOCPATH': str(payload / 'locale'),
           'GCONV_PATH': str(payload / 'gconv')}
    if mode == 'stream':
        env['CAIRN_SEMANTIC_STREAM_CACHE'] = '1'
    return env


def launcher_bytes(mode, environment, payload, binding_sha, helper_sha):
    # The entry verifies the helper and binding using copied stdlib with site
    # disabled. The helper can then recompute every control byte, including this
    # entry, without embedding its own generation address in those bytes.
    code = ("import hashlib,pathlib,runpy,sys;"
            "p=pathlib.Path(sys.argv[1]).resolve(strict=True).parent.parent;"
            "b=p/'binding.json';h=p/'closure.py';"
            "check=lambda f,s:hashlib.sha256(f.read_bytes()).hexdigest()==s;"
            "(check(b," + repr(binding_sha) + ") and check(h," + repr(helper_sha) + "))"
            " or sys.exit('immutable Python control binding differs');"
            "sys.argv=[str(h),'launch','--control',str(p),'--mode',sys.argv[2]];"
            "runpy.run_path(str(h),run_name='__main__')")
    argv = ['/usr/bin/env', '-i', *[k + '=' + v for k, v in sorted(environment.items())],
            str(payload / 'native/ld-linux-x86-64.so.2'), '--inhibit-cache', '--library-path',
            str(payload / 'native')]
    prefix = ' '.join(shlex.quote(x) for x in argv)
    return ("#!/bin/sh\nset -eu\n"
            "[ \"$#\" -eq 0 ] || { echo 'fixed Python launcher accepts no arguments' >&2; exit 64; }\n"
            "[ -z \"${LD_LIBRARY_PATH-}${LD_PRELOAD-}${LD_AUDIT-}${PYTHONHOME-}${PYTHONPATH-}\" ] || "
            "{ echo 'loader/Python override refused' >&2; exit 65; }\n"
            "exec " + prefix + ' "${0%/*}/python" -I -S -B -c ' + shlex.quote(code)
            + ' "$0" ' + shlex.quote(mode) + '\n').encode('utf-8')


def control_files(binding, original_config, config, helper):
    files = {'original-pyvenv.cfg': original_config, 'pyvenv.cfg': config, 'closure.py': helper}
    binding_bytes = canonical(binding) + b'\n'
    files['binding.json'] = binding_bytes
    payload = Path(binding['payload']['path'])
    for mode, name in MODE_NAMES.items():
        files['bin/' + name] = launcher_bytes(mode, binding['environment'][mode], payload,
                                             digest(binding_bytes), digest(helper))
    return files


def private_home(value, stage):
    home = absolute(value)
    if (not home.is_dir() or home.stat().st_uid != os.getuid()
            or stat.S_IMODE(home.stat().st_mode) != 0o700
            or home == stage or home.is_relative_to(stage) or stage.is_relative_to(home)):
        raise ClosureError('worker HOME must be a separate existing owned 0700 directory')
    return home


def render_python_controls(stage, payload, venv_config, home):
    stage = absolute(stage)
    home = private_home(home, stage)
    if list(home.iterdir()):
        raise ClosureError('worker HOME must initially be empty')
    root = verify_generation(payload['path'], payload['descriptor'])
    required = {'python/bin/python3.11': 'file', 'site': 'directory',
                'workers/semantic_rank.py': 'file', 'workers/embedding_worker.py': 'file',
                'native/ld-linux-x86-64.so.2': 'file', 'locale': 'directory', 'gconv': 'directory',
                **{'model/' + name: 'file' for name in MODEL_FILES}}
    entries = payload['descriptor']['entries']
    if any(entries.get(name, {}).get('type') != kind for name, kind in required.items()):
        raise ClosureError('selected Python payload layout is incomplete')
    if any(entries[name]['mode'] != 0o555 for name in
           ('python/bin/python3.11', 'native/ld-linux-x86-64.so.2')):
        raise ClosureError('selected interpreter/loader is not executable')
    original = io.BytesIO()
    regular(absolute(venv_config), original)
    original = original.getvalue()
    lines = original.decode('utf-8').splitlines(keepends=True)
    if (sum(line.startswith('home = ') for line in lines) != 1
            or 'include-system-site-packages = false\n' not in lines):
        raise ClosureError('expected original isolated venv configuration')
    config = ''.join('home = ' + str(root / 'python/bin') + '\n'
                     if line.startswith('home = ') else line for line in lines).encode('utf-8')
    helper = Path(__file__).read_bytes()
    members = {name: {'bytes': len(data), 'sha256': digest(data)}
               for name, data in [('original-pyvenv.cfg', original), ('pyvenv.cfg', config),
                                  ('closure.py', helper)]}
    links = {'bin/python': str(root / 'python/bin/python3.11'),
             'lib/python3.11/site-packages': str(root / 'site')}
    binding = {'schema': 'cairn.semantic-python-binding/1', 'payload': payload,
               'members': members, 'links': links, 'home': str(home),
               'environment': {mode: worker_environment(root, home, mode) for mode in MODE_NAMES}}
    files = control_files(binding, original, config, helper)
    result = publish_controls(stage, payload, files, links)
    result['launchers'] = {mode: str(Path(result['path']) / 'bin' / name)
                           for mode, name in MODE_NAMES.items()}
    return result


def launch(control, mode):
    control = absolute(control)
    binding = read_json(control / 'binding.json')
    if binding['schema'] != 'cairn.semantic-python-binding/1':
        raise ClosureError('control binding schema differs')
    payload = binding['payload']
    root = verify_generation(payload['path'], payload['descriptor'])
    stage = control.parent.parent
    home = private_home(binding['home'], stage)
    expected_env = worker_environment(root, home, mode)
    if binding['environment'][mode] != expected_env or dict(os.environ) != expected_env:
        raise ClosureError('fixed worker environment differs')
    members = {}
    for name, expected in binding['members'].items():
        member_name(name)
        content = io.BytesIO()
        observed = regular(control / name, content)
        if observed != {'type': 'file', 'mode': 0o444, **expected}:
            raise ClosureError('fixed control member differs: ' + name)
        members[name] = content.getvalue()
    if set(members) != {'original-pyvenv.cfg', 'pyvenv.cfg', 'closure.py'}:
        raise ClosureError('fixed control member inventory differs')
    files = control_files(binding, members['original-pyvenv.cfg'], members['pyvenv.cfg'],
                          members['closure.py'])
    descriptor = control_descriptor(payload, files, binding['links'])
    verify_generation(control, descriptor)
    worker = 'embedding_worker.py' if mode == 'embed' else 'semantic_rank.py'
    argv = [str(root / 'native/ld-linux-x86-64.so.2'), '--inhibit-cache', '--library-path',
            str(root / 'native'), str(control / 'bin/python'), '-B',
            str(root / 'workers' / worker), '--model-dir', str(root / 'model')]
    if mode == 'stream':
        argv.append('--stream')
    os.execve(argv[0], argv, expected_env)


def publish_observations(stage, payload, controls, expected_identities, provenance,
                         observations=None):
    """Publish an independent evidence manifest, never a release acceptance."""
    verify_generation(payload['path'], payload['descriptor'])
    verify_generation(controls['path'], controls['descriptor'])
    if (controls['payload_address'] != payload['address']
            or controls['descriptor']['payload_path'] != payload['path']):
        raise ClosureError('observation manifest selected generations differ')
    manifest = {'schema': MANIFEST_SCHEMA, 'payload': payload, 'controls': controls,
                'expected_identities': expected_identities, 'provenance': provenance,
                'observations': observations,
                'qualification': 'local observations only; exact-candidate and Release acceptance separate'
                if observations is not None else 'runtime/effects/numerics unobserved'}
    data = canonical(manifest) + b'\n'
    descriptor = {'schema': MANIFEST_SCHEMA, 'entries': {
        '.': {'type': 'directory', 'mode': 0o555},
        'manifest.json': {'type': 'file', 'mode': 0o444, 'bytes': len(data), 'sha256': digest(data)}}}

    def write_members(pending):
        with (pending / 'manifest.json').open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())

    with stage_root(stage) as root:
        path = publish_generation(root / 'manifests', descriptor, write_members)
    return {'address': path.name, 'path': str(path / 'manifest.json'),
            'sha256': digest(data), 'descriptor': descriptor}


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
    render = commands.add_parser('render')
    for name in ('stage', 'payload', 'venv-config', 'home'):
        render.add_argument('--' + name, required=True)
    start = commands.add_parser('launch')
    start.add_argument('--control', required=True)
    start.add_argument('--mode', choices=MODE_NAMES, required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        pairs = [item.split('=', 1) for item in args.root]
        if any(len(pair) != 2 for pair in pairs) or len(dict(pairs)) != len(pairs):
            raise ClosureError('each unique root requires ROLE=ABSOLUTE_PATH')
        result = seal_inputs(dict(pairs), read_json(args.provenance))
    elif args.command == 'publish':
        result = publish_payload(args.stage, read_json(args.selection))
    elif args.command == 'verify':
        publication = read_json(args.publication)
        verify_generation(publication['path'], publication['descriptor'])
        result = {'state': 'selected-file-integrity-verified', 'runtime_qualification': 'unobserved'}
    elif args.command == 'render':
        result = render_python_controls(args.stage, read_json(args.payload),
                                        args.venv_config, args.home)
    else:
        launch(args.control, args.mode)
        raise AssertionError('execve returned')
    print(canonical(result).decode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except (ClosureError, OSError, ValueError, KeyError) as exc:
        print('Python closure: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
