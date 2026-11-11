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


def backend(stager, args, plan=None):
    result = subprocess.run([stager, *args], input=encoded(plan) if plan is not None else None,
                            capture_output=True, timeout=120, check=False)
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
    result = subprocess.run([str(probe)], capture_output=True, timeout=15, env=env)
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
            config = decoded(raw)
            if not isinstance(config, dict) or config.get('inbox_recall_binding') != str(self.generation / 'binding.json'):
                raise ValueError('profile must already name its new versioned binding path')
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
    if set(plan) != required or not canonical(str(root)) or not canonical(plan['python']) or not canonical(plan['shell']):
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


def inspect(stager, selected, legacy=False):
    generation = backend(stager, ['inspect-generation', '--path', selected['path'], '--sha256', selected['sha256']])
    files = {member['name']: member for member in generation['files']}
    if legacy:
        member = files['retained-argv.json']
        launches = decoded(read_selected({key: member[key] for key in ('path', 'sha256')}, 1024 * 1024))
        return dict(selection=selected, launchers=launches, retained='explicit-legacy')
    member = files['selection.json']
    selection = decoded(read_selected({key: member[key] for key in ('path', 'sha256')}, 1024 * 1024))
    if selection['schema'] != SELECTION_SCHEMA:
        raise ValueError('unsupported installation selection')
    for component in selection['components']:
        d = component['directory']
        backend(stager, ['inspect-directory', '--root', str(Path(d['path']).parent)],
                dict(identity=d['identity'], files=component['files']))
    for label, argv in selection['launchers'].items():
        anchor = selection['anchors'][label]
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
        # subprocess.run kills and joins its owned child when this unwinds.
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
