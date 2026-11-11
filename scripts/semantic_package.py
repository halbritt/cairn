"""Build and stage an offline semantic worker; never select a live service path.

Payload, rendered launcher and observation-manifest addresses are independent.
The default installer remains the Python installer. See docs/semantic-package.md.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


SCHEMA = 'cairn.semantic-worker-manifest/1'
CRATES = ('semantic-backend', 'semantic-math', 'semantic-ort', 'semantic-protocol',
          'semantic-rank', 'semantic-score', 'semantic-text', 'semantic-tokenizer')
MODELS = ('model_optimized.onnx', 'config.json', 'tokenizer.json',
          'special_tokens_map.json', 'tokenizer_config.json')
LIBRARIES = ('ort', 'openblas', 'gfortran', 'quadmath')
MODES = ('rank', 'stream', 'embed')
TARGET = 'x86_64-unknown-linux-gnu'
MARKER = b'cairn.semantic-offline-stage/1\n'


class PackageError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def absolute(path):
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.normpath(path):
        raise PackageError('expected normalized absolute path: ' + str(path))
    # A symlink at any selected component would turn an immutable path mutable.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise PackageError('symlink in selected path: ' + str(part))
    if any(c in str(path) for c in '\n\r\x00'):
        raise PackageError('control character in selected path')
    return path


def regular_bytes(path):
    path = absolute(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise PackageError('not a regular file: ' + str(path))
        data = stream.read()
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, k) != getattr(after, k) for k in fields):
        raise PackageError('input changed while reading: ' + str(path))
    return data


def file_record(data, executable=False):
    """Immutable member bytes and destination mode, not original input mode."""
    return {'bytes': len(data), 'sha256': digest(data),
            'mode': 0o555 if executable else 0o444}


def source_files(root):
    root = absolute(root)
    files = {}
    for crate in CRATES:
        directory = root / 'rust' / crate
        if not directory.is_dir():
            raise PackageError('missing source crate: ' + str(directory))
        for base, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in ('target', '.git'))
            for name in [*dirs, *names]:
                if (Path(base) / name).is_symlink():
                    raise PackageError('source symlink: ' + str(Path(base) / name))
            for name in sorted(names):
                if (name.endswith(('.rs', '.c', '.h')) or name in ('Cargo.toml', 'Cargo.lock')
                        or name.startswith('rust-toolchain')):
                    path = Path(base) / name
                    files[path.relative_to(root).as_posix()] = regular_bytes(path)
    for name in ('rust-toolchain', 'rust-toolchain.toml'):
        if (root / name).exists():
            files[name] = regular_bytes(root / name)
    return files


def records(files):
    return {name: file_record(data, name.startswith('bin/'))
            for name, data in sorted(files.items())}


def capture(argv, **kwargs):
    result = subprocess.run(argv, check=True, capture_output=True, **kwargs)
    return result.stdout.decode('utf-8')


def executable_observation(command):
    selected = shutil.which(command)
    if selected is None:
        raise PackageError('tool not installed: ' + command)
    resolved = Path(selected).resolve(strict=True)
    data = regular_bytes(resolved)
    return {'selected': selected, 'resolved': str(resolved), 'bytes': len(data),
            'sha256': digest(data), 'observed_mode': stat.S_IMODE(resolved.stat().st_mode)}


def cargo_messages(text):
    # Cargo -vv prefixes build-script output with [crate version] even with
    # --message-format=json. Retain that output in the log, not as JSON facts.
    return [json.loads(line) for line in text.splitlines() if line.startswith('{')]


def build(source, output):
    """Fresh locked/offline release build plus retained commands and input hashes."""
    source, output = absolute(source), absolute(output)
    if output.exists():
        raise PackageError('build output must not exist: ' + str(output))
    output.mkdir(mode=0o700, parents=False)
    inputs = source_files(source)
    env = os.environ.copy()
    env.update(CARGO_TARGET_DIR=str(output / 'target'), CARGO_NET_OFFLINE='true',
               CARGO_NET_RETRY='0', RUSTUP_AUTO_INSTALL='0', CC_ENABLE_DEBUG_OUTPUT='1')
    argv = ['cargo', 'build', '--locked', '--offline', '--release', '--bins',
            '--target', TARGET, '--manifest-path',
            str(source / 'rust/semantic-backend/Cargo.toml'), '--jobs', '2',
            '--verbose', '--verbose', '--message-format=json']
    with (output / 'cargo.stdout.jsonl').open('wb') as stdout, (output / 'cargo.stderr').open('wb') as stderr:
        subprocess.run(argv, env=env, stdout=stdout, stderr=stderr, check=True)
    artifacts = cargo_messages((output / 'cargo.stdout.jsonl').read_text())
    binaries = {}
    for mode in ('rank', 'embed'):
        name = 'cairn-semantic-' + mode
        matches = [a for a in artifacts if a.get('reason') == 'compiler-artifact'
                   and a['target']['name'] == name and a.get('executable')]
        if len(matches) != 1 or matches[0]['profile']['opt_level'] != '3' or matches[0]['profile']['test']:
            raise PackageError('missing release binary artifact: ' + name)
        data = regular_bytes(matches[0]['executable'])
        (output / name).write_bytes(data)
        (output / name).chmod(0o500)
        binaries[mode] = file_record(data, True)
    # Keep local/registry inputs and actual per-unit command output available;
    # do not promote them to a complete linker attestation.
    registry = None
    directory = os.environ.get('CAIRN_CARGO_DIRECTORY_SOURCE')
    if directory:
        registry = {}
        for path in sorted(absolute(directory).rglob('*')):
            if path.is_file():
                registry[path.relative_to(directory).as_posix()] = file_record(regular_bytes(path))
    tool_names = {'cargo': 'cargo', 'rustc': env.get('RUSTC', 'rustc'),
                  'cc': env.get('CC', 'cc'), 'cxx': env.get('CXX', 'c++'),
                  'ar': env.get('AR', 'ar'), 'linker': env.get('CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER', 'cc')}
    receipt = {
        'schema': 'cairn.semantic-build/1', 'argv': argv,
        'rustc': capture([tool_names['rustc'], '-vV']), 'target': TARGET,
        'target_cfg': capture([tool_names['rustc'], '--print', 'cfg', '--target', TARGET]).splitlines(),
        'environment': {k: v for k, v in sorted(env.items()) if k in ('CC', 'CXX', 'AR', 'AS', 'LD', 'RUSTC', 'RUSTDOC', 'CC_ENABLE_DEBUG_OUTPUT')
                        or k.startswith(('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CFLAGS', 'CXXFLAGS', 'CARGO_TARGET_'))},
        'tools': {name: executable_observation(command) for name, command in tool_names.items()},
        'binaries': binaries, 'source_inputs': records(inputs), 'directory_source_inputs': registry,
        'cargo_lock_sha256': digest(inputs['rust/semantic-backend/Cargo.lock']),
        'compiler_artifacts': [a for a in artifacts if a.get('reason') in ('compiler-artifact', 'build-script-executed')],
        'logs': {name: file_record(regular_bytes(output / name)) for name in ('cargo.stdout.jsonl', 'cargo.stderr')},
        'unobserved': ['complete linked-input attestation', 'complete effective linker input closure'],
    }
    if registry is None:
        receipt['unobserved'].append('directory_source_inputs')
    # Detect edited sources rather than binding a post-build snapshot to old output.
    if records(source_files(source)) != records(inputs):
        raise PackageError('source changed during build')
    (output / 'build.json').write_bytes(canonical(receipt) + b'\n')
    return receipt


def tree_records(root):
    actual = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(base) / name
            if path.is_symlink():
                raise PackageError('immutable tree contains symlink: ' + str(path))
        for name in names:
            path = Path(base) / name
            data = regular_bytes(path)
            actual[path.relative_to(root).as_posix()] = {
                'bytes': len(data), 'sha256': digest(data), 'mode': stat.S_IMODE(path.stat().st_mode)}
        if stat.S_IMODE(Path(base).stat().st_mode) != 0o555:
            raise PackageError('immutable directory mode differs: ' + str(base))
    return actual


def publish_tree(parent, address, files, executable=()):
    expected = {name: file_record(data, name in executable) for name, data in files.items()}
    target = parent / address
    if target.exists() or target.is_symlink():
        expected_dirs = {'.'} | {str(parent) for name in files for parent in Path(name).parents}
        actual_dirs = {str(Path(base).relative_to(target)) for base, _, _ in os.walk(target)}
        if (target.is_symlink() or not target.is_dir() or actual_dirs != expected_dirs
                or tree_records(target) != expected):
            raise PackageError('immutable path differs; refusing repair: ' + str(target))
        return target
    pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=parent))
    try:
        for name, data in sorted(files.items()):
            path = pending / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), expected[name]['mode'])
                os.fsync(stream.fileno())
        for base, _, _ in os.walk(pending, topdown=False):
            Path(base).chmod(0o555)
            descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        pending.rename(target)
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if pending.exists():
            for base, _, _ in os.walk(pending):
                Path(base).chmod(0o700)
            shutil.rmtree(pending)
    return target


@contextmanager
def stage_root(root):
    root = absolute(root)
    legacy = Path(os.environ.get('CAIRN_HOME', str(Path.home() / '.local/share/cairn'))) / 'semantic'
    if root == legacy or legacy in root.parents:
        raise PackageError('refusing existing Python semantic tree as Rust staging destination')
    if not root.is_dir() or root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise PackageError('destination must be an existing owned 0700 directory')
    marker = root / '.cairn-semantic-stage'
    if not marker.exists():
        if any(root.iterdir()):
            raise PackageError('destination is not an empty disposable staging directory')
        with marker.open('xb') as stream:
            stream.write(MARKER)
    if regular_bytes(marker) != MARKER:
        raise PackageError('wrong staging root marker')
    lock = root / '.stage-lock'
    if lock.is_symlink():
        raise PackageError('staging lock symlink')
    with lock.open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        for name in ('payloads', 'launchers', 'manifests'):
            directory = root / name
            absolute(directory)
            directory.mkdir(mode=0o700, exist_ok=True)
        yield root


def worker_environment():
    return {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
            'XDG_CACHE_HOME': '/nonexistent/cache', 'XDG_CONFIG_HOME': '/nonexistent/config',
            'XDG_DATA_HOME': '/nonexistent/data', 'XDG_STATE_HOME': '/nonexistent/state',
            'ORT_DISABLE_TELEMETRY': '1', 'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
            'OPENBLAS_NUM_THREADS': '2', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
            'NUMEXPR_NUM_THREADS': '2', 'VECLIB_MAXIMUM_THREADS': '2', 'CAIRN_SEMANTIC_STREAM_CACHE': '1'}


def launcher(payload, files, mode, abi):
    """No Python, shell expansion of caller input, mutable selection or argv API."""
    binary = payload / 'bin' / ('cairn-semantic-embed' if mode == 'embed' else 'cairn-semantic-rank')
    argv = [str(binary), '--model-dir', str(payload / 'model'),
            '--ort-runtime', str(payload / 'native/ort'), '--openblas', str(payload / 'native/openblas'),
            '--gfortran', str(payload / 'native/gfortran'), '--quadmath', str(payload / 'native/quadmath'),
            '--source-root', str(payload / 'source')]
    if mode == 'stream':
        argv.append('--stream')
    lines = ['#!/bin/sh', 'set -eu', '[ "$#" -eq 0 ] || { echo "fixed launcher takes no arguments" >&2; exit 2; }',
             '[ -z "${LD_LIBRARY_PATH-}${LD_PRELOAD-}${LD_AUDIT-}" ] || { echo "inherited loader override refused" >&2; exit 2; }']
    # Embed the digest list in the rendered launcher, not a rewritable checksum file.
    # Symlinks and changed bytes fail before the model/native worker starts.
    for name in sorted(files):
        path = payload / name
        lines.append('[ -f {0} ] && [ ! -L {0} ] || exit 1'.format(shlex.quote(str(path))))
    dirs = {'.'} | {str(parent) for name in files for parent in Path(name).parents}
    for kind, count in (('f', len(files)), ('d', len(dirs))):
        lines.append('[ "$(/usr/bin/find ' + shlex.quote(str(payload)) + ' -type ' + kind
                     + ' -printf .)" = ' + shlex.quote('.' * count) + ' ] || exit 1')
    lines.append('[ -z "$(/usr/bin/find ' + shlex.quote(str(payload)) + ' -type l -print -quit)" ] || exit 1')
    checks = ''.join(digest(data) + '  ' + str(payload / name) + '\n' for name, data in sorted(files.items()))
    for item in abi:
        checks += item['sha256'] + '  ' + item['path'] + '\n'
    lines += ['/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/sha256sum --check --status <<\'CAIRN_PACKAGE_DIGESTS\'',
              checks.rstrip('\n'), 'CAIRN_PACKAGE_DIGESTS',
              'exec /usr/bin/env -i ' + shlex.join([key + '=' + value for key, value in worker_environment().items()])
              + ' ' + shlex.join(argv), '']
    return '\n'.join(lines).encode('utf-8')


def elf_requirements(path):
    path = absolute(path)
    return {'header': capture(['/usr/bin/readelf', '-h', str(path)]),
            'notes': capture(['/usr/bin/readelf', '-n', str(path)]),
            'program_headers': capture(['/usr/bin/readelf', '-l', str(path)]),
            'dynamic': capture(['/usr/bin/readelf', '-d', str(path)]),
            'version_requirements': capture(['/usr/bin/readelf', '-V', str(path)])}


def stage(root, source, build_root, model, libraries, provenance, abi=None):
    source, build_root, model = map(absolute, (source, build_root, model))
    receipt = read_json(build_root / 'build.json')
    if receipt['schema'] != 'cairn.semantic-build/1' or receipt['target'] != TARGET:
        raise PackageError('unsupported build receipt')
    inputs = source_files(source)
    if receipt['source_inputs'] != records(inputs):
        raise PackageError('source differs from selected build receipt')
    if set(libraries) != set(LIBRARIES):
        raise PackageError('all four native library roles are required')
    if set(provenance) != set(MODELS) | set(LIBRARIES):
        raise PackageError('provenance must name all five model files and four libraries')
    files = {'source/' + name: data for name, data in inputs.items()}
    for mode in ('rank', 'embed'):
        name = 'cairn-semantic-' + mode
        data = regular_bytes(build_root / name)
        if file_record(data, True) != receipt['binaries'][mode]:
            raise PackageError('binary differs from selected build receipt: ' + mode)
        files['bin/' + name] = data
    for name in MODELS:
        files['model/' + name] = regular_bytes(model / name)
    for role, path in libraries.items():
        files['native/' + role] = regular_bytes(path)
    for role, evidence in provenance.items():
        name = 'model/' + role if role in MODELS else 'native/' + role
        if (not isinstance(evidence, dict) or not isinstance(evidence.get('source'), str)
                or not evidence['source'] or evidence.get('sha256') != digest(files[name])):
            raise PackageError('missing or mismatched provenance: ' + role)
    checked_abi = []
    for entry in abi or ():
        path = absolute(entry['path'])
        data = regular_bytes(path)
        if digest(data) != entry['sha256']:
            raise PackageError('ABI prerequisite digest differs: ' + str(path))
        checked_abi.append({'path': str(path), 'bytes': len(data), 'sha256': digest(data),
                            'observed_mode': stat.S_IMODE(path.stat().st_mode)})
    files['build.json'] = canonical(receipt) + b'\n'
    for name in receipt['logs']:
        data = regular_bytes(build_root / name)
        if file_record(data) != receipt['logs'][name]:
            raise PackageError('build log digest differs: ' + name)
        files['build-logs/' + name] = data
    requirements = {name: elf_requirements(build_root / ('cairn-semantic-' + name)) for name in ('rank', 'embed')}
    requirements.update({role: elf_requirements(path) for role, path in libraries.items()})
    payload_data = {'schema': 'cairn.semantic-payload/1', 'files': records(files),
                    'provenance': provenance, 'native_elf': requirements,
                    'host_abi_prerequisites': checked_abi if abi is not None else None}
    address = digest(canonical(payload_data))
    files['payload.json'] = canonical(payload_data) + b'\n'
    with stage_root(root) as destination:
        payload = publish_tree(destination / 'payloads', address, files,
                               executable=('bin/cairn-semantic-rank', 'bin/cairn-semantic-embed'))
        launch_files = {mode: launcher(payload, files, mode, checked_abi) for mode in MODES}
        launch_address = digest(canonical({mode: digest(data) for mode, data in launch_files.items()}))
        launch_root = publish_tree(destination / 'launchers', launch_address, launch_files, MODES)
        manifest = {
            'schema': SCHEMA, 'payload': {'address': address, 'path': str(payload), **payload_data},
            'source_identity': {'scheme': 'sha256-canonical-source-member-records/1',
                                'sha256': digest(canonical(receipt['source_inputs']))},
            'build_identity': {'scheme': 'sha256-canonical-build-receipt/1', 'sha256': digest(canonical(receipt))},
            'build': receipt, 'target': TARGET, 'linkage': 'dynamic; see ELF requirements and observations',
            'requirements': {'host_abi_prerequisites': checked_abi if abi is not None else None,
                             'cpu': {'compile_target_cfg': receipt.get('target_cfg'),
                                     'complete_effective_cpu_kernel_class': 'unobserved'},
                             'launcher_environment': worker_environment()},
            'launchers': {mode: {'path': str(launch_root / mode), 'sha256': digest(launch_files[mode])} for mode in MODES},
            'launcher_set_address': launch_address,
            'derivations': {'rank': {'state': 'unobserved'}, 'embed': {'state': 'unobserved'}},
            'qualification': {'state': 'incomplete', 'managed_inventory': 'unobserved',
                              'cpu_kernel_class': 'unobserved', 'actual_loaded_dependency_closure': 'unobserved',
                              'complete_upstream_artifact_provenance': 'unobserved; retained selected input provenance is recorded',
                              'worker_connect_and_telemetry_writes': 'unobserved'},
        }
        if abi is None:
            manifest['qualification']['unobserved_observations'] = ['host_abi_prerequisites']
        manifest_address = digest(canonical(manifest))
        manifest_root = publish_tree(destination / 'manifests', manifest_address,
                                     {'manifest.json': canonical(manifest) + b'\n'})
        return {'manifest': str(manifest_root / 'manifest.json'), 'sha256': digest(canonical(manifest) + b'\n'),
                'payload': str(payload), 'launchers': manifest['launchers'], 'qualification': 'incomplete'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--source-root', required=True)
    build_parser.add_argument('--output', required=True)
    stage_parser = commands.add_parser('stage')
    stage_parser.add_argument('--destination', required=True)
    stage_parser.add_argument('--source-root', required=True)
    stage_parser.add_argument('--build', required=True)
    stage_parser.add_argument('--model-dir', required=True)
    for role in LIBRARIES:
        stage_parser.add_argument('--' + role, required=True)
    stage_parser.add_argument('--provenance', required=True)
    stage_parser.add_argument('--abi-prerequisites')
    args = parser.parse_args()
    try:
        if args.operation == 'build':
            receipt = build(args.source_root, args.output)
            result = {'build_receipt': str(Path(args.output) / 'build.json'), 'binaries': receipt['binaries'],
                      'unobserved': receipt['unobserved']}
        else:
            result = stage(args.destination, args.source_root, args.build, args.model_dir,
                           {role: getattr(args, role) for role in LIBRARIES}, read_json(args.provenance),
                           read_json(args.abi_prerequisites) if args.abi_prerequisites else None)
        print(json.dumps(result, sort_keys=True))
    except (PackageError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'semantic package refused: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
