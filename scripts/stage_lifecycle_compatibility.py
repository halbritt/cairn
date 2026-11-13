#!/usr/bin/env python3
"""Stage and inspect selected F-04 files; never update live hooks or activate a unit."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import uuid


SELECTION_SCHEMA = 'cairn.lifecycle-install-selection/1'
# The retained /2 bundle contract, independent of any selected payload loader.
RETAINED_MEMBERS = frozenset((
    'lifecycle_bundle.py', 'memory.py', 'coordination.py', 'inbox_recall.py',
    'inbox_binding.py', 'grant_ledger.py', 'transcript_recovery.py',
    'unix_peer_transport.py', 'wake_transport.py', 'process_scan.py',
    'codex_queue.py', 'claude_channel.py', 'opencode_queue.py', 'hermes_queue.py',
    'recall_dispatch.py', 'hook_admission.py', 'native_session.py',
))


class InstallationInterrupted(RuntimeError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate installation field')
        result[key] = value
    return result


def decoded(raw):
    return json.loads(raw, object_pairs_hook=unique)


def canonical(path):
    return (type(path) is str and path.startswith('/') and not path.startswith('//') and os.path.normpath(path) == path
            and '\0' not in path and not any(0xd800 <= ord(c) <= 0xdfff for c in path))


@contextlib.contextmanager
def directory(path, private=False):
    if not canonical(path):
        raise ValueError('canonical absolute installation path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path[1:].split('/'):
            if not component:
                continue
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                raise ValueError('unsafe installation ancestor')
        info = os.fstat(fd)
        if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('private installation directory must be owned mode 0700')
        yield fd
    finally:
        os.close(fd)


def read_selected(descriptor, limit):
    if set(descriptor) != {'path', 'sha256'} or not canonical(descriptor['path']):
        raise ValueError('invalid selected input descriptor')
    path = Path(descriptor['path'])
    with directory(str(path.parent)) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_mode & 0o7022 or before.st_size > limit):
            raise ValueError('selected input is not a controlled bounded regular file: ' + path.name)
        raw = source.read(limit + 1)
        after = os.fstat(source.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or len(raw) != before.st_size or len(raw) > limit or sha(raw) != descriptor['sha256']:
            raise ValueError('selected input bytes or metadata changed')
        return raw


def run_owned(argv, *, payload=None, timeout, env=None):
    # Defer SIGTERM during Popen's exec handshake so an asynchronous exception
    # cannot escape after fork but before ownership of the child is returned.
    pending = []
    previous = signal.signal(signal.SIGTERM, lambda *args: pending.append(True))
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        signal.signal(signal.SIGTERM, previous)
        if pending:
            raise InstallationInterrupted('installation interrupted during spawn; reconcile selected paths')
        stdout, stderr = process.communicate(payload, timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except BaseException:
        if process is not None:
            process.kill()
            process.communicate()
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)


def backend(stager, args, plan=None):
    result = run_owned([stager, *args], payload=encoded(plan) if plan is not None else None, timeout=120)
    if result.returncode:
        raise RuntimeError('stager refused; reconcile immutable paths before retry: ' + result.stderr.decode(errors='replace'))
    return decoded(result.stdout)


def qualify_launch_anchors(plan, scratch):
    """Check the selected noninteractive shell without reading real startup files.

    Python/stdlib and the shell remain the owner's external launch anchors, not
    receipt-authenticated payloads. Record the actual interpreter profile; do
    not claim that its sys.path listing authenticates the whole OS dependency set.
    """
    marker = scratch / 'startup-read'
    startup = scratch / 'startup'
    startup.write_text('printf unexpected > ' + shlex.quote(str(marker)) + '\n')
    startup.chmod(0o600)
    probe = scratch / 'probe'
    vector = [plan['python'], '-I', '-S', '-B', '-c',
              'import json,sys;print(json.dumps(dict(version=sys.version,path=sys.path,isolated=sys.flags.isolated,no_site=sys.flags.no_site)))']
    probe.write_text('#!' + plan['shell'] + '\nexec ' + shlex.join(vector) + '\n')
    probe.chmod(0o500)
    env = dict(os.environ, ENV=str(startup), BASH_ENV=str(startup))
    result = run_owned([str(probe)], timeout=15, env=env)
    if result.returncode or result.stderr or marker.exists():
        raise ValueError('selected shell/interpreter failed isolated startup qualification')
    profile = decoded(result.stdout)
    if profile['isolated'] != 1 or profile['no_site'] != 1:
        raise ValueError('selected interpreter must run isolated without site initialization')
    return dict(python=plan['python'], shell=plan['shell'], interpreter_profile=profile,
                retention='external approved interpreter/stdlib and shell; not receipt-authenticated')


class Assembler:
    def __init__(self, stager, root, plan, scratch):
        self.stager, self.root, self.plan, self.scratch = stager, root, plan, scratch
        identity = plan['id']
        if str(uuid.UUID(identity)) != identity or uuid.UUID(identity).int == 0:
            raise ValueError('installation requires a preselected nonzero UUID')
        if type(plan['enabled']) is not bool:
            raise ValueError('enabled must be explicitly selected as a Boolean')
        self.profile_mode = plan.get('profile_mode', 'bound')
        if self.profile_mode not in ('bound', 'unbound') or self.profile_mode == 'unbound' and plan['enabled']:
            raise ValueError('unbound profiles require an explicitly disabled binding')
        if type(plan['source_revision']) is not str or any(0xd800 <= ord(c) <= 0xdfff for c in plan['source_revision']):
            raise ValueError('source revision must be Unicode provenance text')
        self.generation = root / 'generations' / ('selection-' + identity)
        self.components = []
        self.payloads = {}

    def descriptor(self, name):
        return {'path': str(self.generation / name), 'sha256': sha(self.payloads[name])}

    def publish_directory(self, namespace, identity, payloads, executable=()):
        source = Path(tempfile.mkdtemp(dir=self.scratch))
        files = []
        for name, raw in sorted(payloads.items()):
            path = source / name
            path.write_bytes(raw)
            path.chmod(0o600)
            files.append(dict(name=name, path=str(path), sha256=sha(raw), executable=name in executable))
        selected = backend(self.stager, ['directory', '--root', str(self.root / namespace)],
                           dict(identity=identity, files=files))
        for member in files:
            member['path'] = str(Path(selected['path']) / member['name'])
        self.components.append(dict(directory=selected, files=files))
        return Path(selected['path'])

    def capture_writer(self):
        sources = {name: read_selected(pin, 2 * 1024 * 1024) for name, pin in self.plan['sources'].items()}
        source = self.scratch / 'source'
        source.mkdir(mode=0o700)
        for name, raw in sources.items():
            if not name or Path(name).name != name or '\\' in name or name in ('.', '..'):
                raise ValueError('source names must be basenames')
            (source / name).write_bytes(raw)
        writer = types.ModuleType('selected_lifecycle_material_writer')
        writer.__file__ = str(source / 'lifecycle_compatibility.py')
        exec(compile(sources['lifecycle_compatibility.py'], writer.__file__, 'exec'), writer.__dict__)
        if set(sources) != set(writer.MEMBERS):
            raise ValueError('selected source closure differs from its material writer')
        return writer, writer.guarded_sources(source)

    def profiles(self):
        inputs = [self.plan['memory_config'], *self.plan['coordination_configs']]
        if not 2 <= len(inputs) <= 9:
            raise ValueError('one memory and one to eight coordination configurations required')
        for index, pin in enumerate(inputs):
            name = 'memory.json' if index == 0 else 'coordination-' + str(index - 1) + '.json'
            raw = read_selected(pin, 1024 * 1024)
            # Configuration keeps the source interpreter's duplicate-last and
            # opaque-value semantics. Only plans/receipts require unique keys.
            config = json.loads(raw.decode('utf-8'))
            if not isinstance(config, dict):
                raise ValueError('profile must be an object')
            if self.profile_mode == 'bound':
                if config.get('inbox_recall_binding') != str(self.generation / 'binding.json'):
                    raise ValueError('profile must already name its new versioned binding path')
            elif config.get('inbox_recall_binding'):
                raise ValueError('unbound profiles must already be unbound; no binding is removed')
            self.payloads[name] = raw
        return self.descriptor('memory.json'), [self.descriptor('coordination-' + str(i) + '.json') for i in range(len(inputs) - 1)]

    def snapshot(self, writer, sources):
        aliases = self.plan['aliases']
        for name, target in aliases.items():
            if (type(name) is not str or not name or Path(name).name != name or '\\' in name or '\0' in name
                    or name in ('.', '..') or name in sources or target not in ('memory.py', 'coordination.py')):
                raise ValueError('invalid compatibility alias')
            sources[name] = sources[target]
        hashes = {name: sha(raw) for name, raw in sources.items()}
        identity = sha(encoded(hashes))
        path = self.publish_directory('snapshots', identity, sources)
        return dict(snapshot_root=str(path), members={name: dict(sha256=hashes[name], bytes=len(raw)) for name, raw in sources.items()},
                    bootstrap='lifecycle_compatibility.py', loader='lifecycle_bundle.py',
                    roles={role: role + '.py' for role in ('memory', 'coordination', 'inbox_recall', 'inbox_binding')}, aliases=aliases)

    def launchers(self, writer, receipt):
        launchers, anchors = {}, {}
        roles = [('memory', receipt['memory_config'], (13, 150))]
        roles += [('coordination', config, (2, 15)) for config in receipt['coordination_configs']]
        for index, (role, config, timeouts) in enumerate(roles):
            entry = next((name for name, target in sorted(receipt['aliases'].items()) if target == role + '.py'), role + '.py')
            for timeout in timeouts:
                label = role + ':' + str(index) + ':' + str(timeout)
                vector = writer.launch_vector(self.plan['python'], str(self.generation / 'receipt.json'),
                                              sha(self.payloads['receipt.json']), role, entry, config['path'], timeout,
                                              str(self.root / 'launch'), self.plan['shell'])
                raw = writer.launcher_bytes(vector, self.plan['shell'])
                if len(raw) > 65536:
                    raise ValueError('launcher exceeds checked caller readback bound')
                path = self.publish_directory('launch', sha(raw), {'inbox': raw}, ('inbox',)) / 'inbox'
                launchers[label] = [str(path)]
                anchors[label] = dict(vector=vector, vector_sha256=sha(encoded(vector)), verifier_sha256=sha(writer.VERIFIER_LITERAL.encode()),
                                      python=self.plan['python'], shell=self.plan['shell'], sha256=sha(raw), bytes=len(raw))
        return launchers, anchors

    def stage(self):
        anchors_profile = qualify_launch_anchors(self.plan, self.scratch)
        writer, sources = self.capture_writer()
        memory, coordinators = self.profiles()
        binary = backend(self.stager, ['--root', str(self.root / 'bin'), '--binary', self.plan['binary']['path'], '--sha256', self.plan['binary']['sha256']])
        self.components.append(dict(directory=dict(path=str(Path(binary['path']).parent), identity=binary['sha256']),
                                    files=[dict(name='cairn', **binary, executable=True)]))
        binding = dict(schema='cairn.inbox-recall-binding/3', enabled=self.plan['enabled'], binary=binary,
                       memory_config=memory, coordination_configs=coordinators)
        self.payloads['binding.json'] = encoded(binding)
        receipt = dict(schema='cairn.lifecycle-compatibility/1', source_revision=self.plan['source_revision'],
                       **self.snapshot(writer, sources), binary=binary,
                       binding=dict(**self.descriptor('binding.json'), schema=binding['schema']),
                       memory_config=memory, coordination_configs=coordinators)
        self.payloads['receipt.json'] = encoded(receipt)
        if len(self.payloads['receipt.json']) > 1024 * 1024:
            raise ValueError('compatibility receipt exceeds 1 MiB')
        launchers, anchors = self.launchers(writer, receipt)
        selection = dict(schema=SELECTION_SCHEMA, generation_id=self.plan['id'], receipt=self.descriptor('receipt.json'),
                         components=self.components, launchers=launchers, anchors=anchors,
                         launch_environment=anchors_profile, activation='not-performed')
        self.payloads['selection.json'] = encoded(selection)
        self.payloads['retained-argv.json'] = encoded(launchers)
        source = self.scratch / 'generation'
        source.mkdir(mode=0o700)
        files = []
        for name, raw in sorted(self.payloads.items()):
            path = source / name
            path.write_bytes(raw)
            path.chmod(0o600)
            files.append(dict(name=name, path=str(path), sha256=sha(raw)))
        return backend(self.stager, ['generation', '--root', str(self.root / 'generations')], dict(id=self.plan['id'], files=files))


def stage(stager, root, plan):
    required = {'id', 'enabled', 'sources', 'source_revision', 'binary', 'memory_config', 'coordination_configs', 'aliases', 'python', 'shell'}
    if (set(plan) not in (required, required | {'profile_mode'}) or not canonical(str(root))
            or not canonical(plan['python']) or not canonical(plan['shell'])):
        raise ValueError('incomplete installation plan or noncanonical launch anchor')
    with directory(str(root), private=True) as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in ('bin', 'snapshots', 'launch', 'generations'):
            try:
                os.mkdir(name, 0o700, dir_fd=locked)
                os.fsync(locked)
            except FileExistsError:
                pass
            with directory(str(root / name), private=True):
                pass
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=root) as temporary:
            selected = Assembler(stager, root, plan, Path(temporary)).stage()
        with directory(str(root), private=True) as after:
            if (os.fstat(locked).st_dev, os.fstat(locked).st_ino) != (os.fstat(after).st_dev, os.fstat(after).st_ino):
                raise ValueError('installation root changed; reconcile published paths')
        return selected


def retained_member(files, descriptor):
    if (type(descriptor) is not dict or set(descriptor) != {'path', 'sha256'}
            or not canonical(descriptor['path'])):
        raise ValueError('invalid retained input descriptor')
    member = files.get(Path(descriptor['path']).name)
    if member is None or descriptor != {key: member[key] for key in ('path', 'sha256')}:
        raise ValueError('retained descriptor differs from verified generation member')
    return Path(descriptor['path']).name


def retained_vector(files, vector, role, profiles):
    if (type(vector) is not list or not vector
            or any(type(arg) is not str or '\0' in arg
                   or any(0xd800 <= ord(c) <= 0xdfff for c in arg) for arg in vector)
            or not canonical(vector[0])):
        raise ValueError('invalid retained Python argv')
    offset, flags = 1, set()
    while offset < len(vector) and vector[offset] in ('-I', '-S', '-B'):
        if vector[offset] in flags:
            raise ValueError('ambiguous retained Python flags')
        flags.add(vector[offset])
        offset += 1
    tail = ['--config', profiles[0]['path']] if role == 'memory' else None
    if role == 'coordination':
        matches = [pin for pin in profiles[1:] if vector[offset + 1:] == ['hook', '--config', pin['path']]]
        if len(matches) != 1:
            raise ValueError('retained coordination argv must select one verified profile')
        tail = ['hook', '--config', matches[0]['path']]
    if offset >= len(vector) or vector[offset + 1:] != tail or not canonical(vector[offset]):
        raise ValueError('retained argv does not match its literal hook role')
    name = Path(vector[offset]).name
    if (name not in files or files[name]['path'] != vector[offset]
            or files[name]['sha256'] != files[role + '.py']['sha256']):
        raise ValueError('retained argv entrypoint differs from its verified bundle role')
    return name


def inspect_retained(stager, selected):
    """Verify /2 data and literal argv without executing its selected code."""
    generation = backend(stager, ['inspect-generation', '--path', selected['path'], '--sha256', selected['sha256']])
    files = {member['name']: member for member in generation['files']}
    pins = {name: {key: member[key] for key in ('path', 'sha256')} for name, member in files.items()}
    required = RETAINED_MEMBERS | {'binding.json', 'lifecycle-bundle.json', 'retained-argv.json'}
    if not required <= files.keys() or 'selection.json' in files or 'receipt.json' in files:
        raise ValueError('explicit retained /2 generation required; no compatibility receipt is inferred')
    binding = decoded(read_selected(pins['binding.json'], 1024 * 1024))
    if (type(binding) is not dict
            or set(binding) != {'schema', 'enabled', 'engine', 'bridge', 'memory_config', 'coordination_configs'}
            or binding['schema'] != 'cairn.inbox-recall-binding/2' or type(binding['enabled']) is not bool
            or type(binding['coordination_configs']) is not list or not 1 <= len(binding['coordination_configs']) <= 8):
        raise ValueError('unsupported retained /2 binding')
    profiles = [binding['memory_config'], *binding['coordination_configs']]
    names = [retained_member(files, pin) for pin in profiles]
    if len(set(names)) != len(names) or required.intersection(names):
        raise ValueError('ambiguous retained profile selection')
    expected = set(required) | set(names)
    bundle = decoded(read_selected(pins['lifecycle-bundle.json'], 1024 * 1024))
    if (type(bundle) is not dict or set(bundle) != {'schema', 'sha256', 'files'}
            or bundle['schema'] != 'cairn.lifecycle-bundle/1' or type(bundle['files']) is not dict
            or set(bundle['files']) != RETAINED_MEMBERS or sha(encoded(bundle['files'])) != bundle['sha256']
            or bundle['files'] != {name: files[name]['sha256'] for name in RETAINED_MEMBERS}):
        raise ValueError('retained bundle differs from verified generation closure')
    for role, entry in (('engine', 'memory.py'), ('bridge', 'inbox_recall.py')):
        descriptor = binding[role]
        if type(descriptor) is not dict or set(descriptor) != {'path', 'sha256', 'bundle'} or descriptor['bundle'] != bundle:
            raise ValueError('retained engine/bridge bundle selection disagrees')
        name = retained_member(files, {key: descriptor[key] for key in ('path', 'sha256')})
        if descriptor['sha256'] != files[entry]['sha256']:
            raise ValueError('retained engine/bridge entrypoint differs from its bundle role')
        expected.add(name)
    for pin in profiles:
        config = json.loads(read_selected(pin, 1024 * 1024))
        if type(config) is not dict or config.get('inbox_recall_binding') not in (None, '', pins['binding.json']['path']):
            raise ValueError('retained profile selects another binding')
    launchers = decoded(read_selected(pins['retained-argv.json'], 1024 * 1024))
    if type(launchers) is not dict or set(launchers) != {'memory', 'coordination'}:
        raise ValueError('one literal retained argv per hook role required')
    for role, vector in launchers.items():
        expected.add(retained_vector(files, vector, role, profiles))
    if files.keys() != expected:
        raise ValueError('retained generation has unknown or unselected members')
    return dict(selection=selected, launchers=launchers, memory_config=profiles[0], coordination_configs=profiles[1:],
                retained_python=dict(binding=pins['binding.json'], binding_schema=binding['schema'],
                                     engine=binding['engine'], bridge=binding['bridge'],
                                     bundle_manifest=pins['lifecycle-bundle.json'], retained_argv=pins['retained-argv.json']))


def inspect(stager, selected, legacy=False):
    generation = backend(stager, ['inspect-generation', '--path', selected['path'], '--sha256', selected['sha256']])
    files = {member['name']: member for member in generation['files']}
    if legacy:
        if 'selection.json' in files:
            raise ValueError('compatibility generations require full inspection, not --legacy')
        member = files['retained-argv.json']
        launches = decoded(read_selected({key: member[key] for key in ('path', 'sha256')}, 1024 * 1024))
        return dict(selection=selected, launchers=launches, retained='explicit-legacy')
    member = files['selection.json']
    selection = decoded(read_selected({key: member[key] for key in ('path', 'sha256')}, 1024 * 1024))
    if selection['schema'] != SELECTION_SCHEMA:
        raise ValueError('unsupported installation selection')
    receipt_pin = {key: files['receipt.json'][key] for key in ('path', 'sha256')}
    if selection['receipt'] != receipt_pin:
        raise ValueError('selection receipt differs from verified generation member')
    receipt = decoded(read_selected(receipt_pin, 1024 * 1024))
    components = {}
    for component in selection['components']:
        d = component['directory']
        if (not canonical(d['path']) or d['path'] != str(Path(d['path']).parent / d['identity'])
                or d['path'] in components):
            raise ValueError('component path differs from its selected identity')
        backend(stager, ['inspect-directory', '--root', str(Path(d['path']).parent)],
                dict(identity=d['identity'], files=component['files']))
        components[d['path']] = {member['name']: member for member in component['files']}
    snapshot = components[receipt['snapshot_root']]
    if {name: member['sha256'] for name, member in snapshot.items()} != {name: member['sha256'] for name, member in receipt['members'].items()}:
        raise ValueError('receipt snapshot differs from verified components')
    binary = components[str(Path(receipt['binary']['path']).parent)]['cairn']
    if {key: binary[key] for key in ('path', 'sha256')} != receipt['binary']:
        raise ValueError('receipt binary differs from verified components')
    for label, argv in selection['launchers'].items():
        if type(argv) is not list or len(argv) != 1 or not canonical(argv[0]):
            raise ValueError('invalid selected launcher argv')
        anchor = selection['anchors'][label]
        vector = anchor['vector']
        if type(vector) is not list or len(vector) < 5 or any(type(value) is not str for value in vector):
            raise ValueError('invalid selected launch vector')
        for flag, value in (('--receipt', receipt_pin['path']), ('--receipt-sha256', receipt_pin['sha256'])):
            if vector.count(flag) != 1 or vector[vector.index(flag) + 1:][:1] != [value]:
                raise ValueError('launcher receipt differs from verified generation member')
        raw = read_selected(dict(path=argv[0], sha256=anchor['sha256']), 65536)
        prefix = ('#!' + anchor['shell'] + '\nexec ').encode()
        if (len(raw) != anchor['bytes'] or not raw.startswith(prefix)
                or shlex.split(raw[len(prefix):].decode()) != [*anchor['vector'], '--', '$@']
                or sha(encoded(anchor['vector'])) != anchor['vector_sha256']
                or sha(anchor['vector'][4].encode()) != anchor['verifier_sha256']):
            raise ValueError('installed launcher byte/argv readback differs from selection')
    retained = files['retained-argv.json']
    if decoded(read_selected({key: retained[key] for key in ('path', 'sha256')}, 1024 * 1024)) != selection['launchers']:
        raise ValueError('retained launch argv differ from selection')
    return dict(selection=selected, receipt=selection['receipt'], launchers=selection['launchers'], activation='not-performed')


def main():
    def interrupted(signum, frame):
        # run_owned defers this exception until child ownership is established.
        raise InstallationInterrupted('installation interrupted; reconcile the selected UUID before retry')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stager', required=True)
    sub = parser.add_subparsers(dest='operation', required=True)
    write = sub.add_parser('stage')
    write.add_argument('--root', required=True)
    read = sub.add_parser('inspect')
    read.add_argument('--path', required=True)
    read.add_argument('--sha256', required=True)
    read.add_argument('--legacy', action='store_true')
    args = parser.parse_args()
    if not canonical(args.stager):
        raise ValueError('canonical stager path required')
    if args.operation == 'stage':
        if not canonical(args.root):
            raise ValueError('canonical absolute installation root required')
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('installation plan exceeds 1 MiB')
        selected = stage(args.stager, Path(args.root), decoded(raw))
        result = inspect(args.stager, selected)
    else:
        result = inspect(args.stager, dict(path=args.path, sha256=args.sha256), args.legacy)
    sys.stdout.buffer.write(encoded(result) + b'\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        sys.stderr.write('lifecycle staging failed: ' + str(error) + '\n')
        raise SystemExit(1)
