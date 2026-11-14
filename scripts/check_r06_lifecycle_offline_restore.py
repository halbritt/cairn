#!/usr/bin/env python3
"""Finite retained /2 cold restore using its genuine, unchanged literal argv."""
import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import site as python_site
import stat
import subprocess
import sys
import sysconfig
import tarfile
import time
import types
import uuid

from disposable_host import DisposableHost, OwnedCluster, pg_bindir, require
import stage_lifecycle_compatibility as installer
from check_r06_native_unit import OwnedUnit

ROOT = Path(__file__).resolve().parents[1]
TOKEN_NAMES = {'worker.token', 'sender.token', 'credential-selectors.json'}


class ClosureRefusal(ValueError):
    pass


class ProcessReceipts:
    """Observe collector subprocesses without replacing Popen or any product API."""

    def __init__(self, evidence):
        self.evidence, self.processes = Path(evidence), {}

    def observe(self, frame, event, value):
        if event != 'return':
            return
        code = frame.f_code
        if code is subprocess.Popen.__init__.__code__:
            process = frame.f_locals['self']
            if getattr(process, 'pid', None) is not None:
                argv = frame.f_locals['args']
                argv = argv if isinstance(argv, (list,tuple)) else [argv]
                self.processes[process.pid] = dict(pid=process.pid, argv=list(map(str,argv)), exit=None)
        elif code in (subprocess.Popen.wait.__code__, subprocess.Popen.poll.__code__) and isinstance(value,int):
            process = frame.f_locals['self']
            if process.pid in self.processes:
                self.processes[process.pid]['exit'] = value

    def __enter__(self):
        require(sys.getprofile() is None, 'collector already has a profiler')
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *error):
        sys.setprofile(None)
        if self.evidence.is_dir():
            write(self.evidence/'collector-processes.json', list(self.processes.values()))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ClosureRefusal('unsafe archive member')
    return path


def inventory(root):
    root = Path(root)
    root_info = root.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700):
        raise ClosureRefusal('selected runtime root must be owned and mode 0700')
    rows = {}
    for path in sorted(Path(root).rglob('*')):
        name = str(path.relative_to(root))
        info = path.lstat()
        if path.name in TOKEN_NAMES or info.st_uid != os.getuid() or info.st_mode & 0o7022:
            raise ClosureRefusal('uncontrolled or credential payload member: ' + name)
        if stat.S_ISDIR(info.st_mode):
            row = dict(kind='directory', mode=stat.S_IMODE(info.st_mode), uid=info.st_uid)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            row = dict(kind='file', mode=stat.S_IMODE(info.st_mode), uid=info.st_uid,
                       bytes=info.st_size, sha256=digest(path))
        else:
            raise ClosureRefusal('unsupported link or special payload member: ' + name)
        rows[name] = row
    return dict(schema='cairn.retained2-offline-payload/1', root=str(Path(root).resolve()),
                uid=os.getuid(), mode=0o700, members=rows)


def verify(root, manifest):
    if manifest['uid'] != os.getuid() or str(Path(root).resolve()) != manifest['root']:
        raise ClosureRefusal('foreign owner or changed selected destination')
    if inventory(root) != manifest:
        raise ClosureRefusal('restored closure differs from frozen paths/bytes/modes/owner')


def seal(root, archive):
    manifest = inventory(root)
    with tarfile.open(archive, 'w:gz') as out:
        for name in manifest['members']:
            out.add(Path(root) / name, arcname=name, recursive=False)
    return manifest


def restore(root, archive, manifest):
    root = Path(root)
    if root.exists() or root.is_symlink() or str(root.resolve()) != manifest['root']:
        raise ClosureRefusal('restore destination must be the absent selected path')
    if manifest['uid'] != os.getuid() or manifest['mode'] != 0o700:
        raise ClosureRefusal('foreign restoration owner')
    # Validate the complete archive before creating the destination or any file.
    with tarfile.open(archive, 'r:gz') as package:
        entries = package.getmembers()
        names = [member.name for member in entries]
        if len(names) != len(set(names)) or set(names) != set(manifest['members']):
            raise ClosureRefusal('archive membership differs')
        for member in entries:
            safe_name(member.name)
            expected = manifest['members'][member.name]
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ClosureRefusal('archive link or special member')
            if member.uid != manifest['uid'] or member.mode != expected['mode']:
                raise ClosureRefusal('archive owner/mode differs')
            if member.isfile():
                raw = package.extractfile(member).read()
                if (expected['kind'] != 'file' or len(raw) != expected['bytes']
                        or hashlib.sha256(raw).hexdigest() != expected['sha256']):
                    raise ClosureRefusal('archive bytes differ')
            elif expected['kind'] != 'directory':
                raise ClosureRefusal('archive member type differs')
        root.mkdir(mode=0o700)
        for member in entries:
            destination = root / member.name
            if member.isdir():
                destination.mkdir(mode=member.mode)
            else:
                with destination.open('xb') as out:
                    shutil.copyfileobj(package.extractfile(member), out)
                destination.chmod(member.mode)
    verify(root, manifest)


def copy_python(destination):
    """Use CPython's unmodified prefix discovery, without venv/home/path shims."""
    destination = Path(destination)
    executable = Path(sys.executable).resolve()
    (destination / 'bin').mkdir(mode=0o700, parents=True)
    target = destination / 'bin' / executable.name
    shutil.copyfile(executable, target)
    target.chmod(0o500)
    lib = destination / 'lib'
    lib.mkdir(mode=0o700)
    original = Path(sysconfig.get_path('stdlib'))
    # The selected copy contains regular bytes. Preserve source-link provenance
    # separately instead of retaining a link into the retired host installation.
    source_files = {}
    for path in sorted(original.rglob('*')):
        relative = path.relative_to(original)
        if any(p in {'__pycache__', 'site-packages', 'dist-packages'} for p in relative.parts) or path.suffix == '.pyc':
            continue
        if path.is_file():
            source_files[str(relative)] = dict(source=str(path), resolved=str(path.resolve()),
                symlink=os.readlink(path) if path.is_symlink() else None,
                sha256=digest(path), bytes=path.stat().st_size,
                mode=stat.S_IMODE(path.stat().st_mode), uid=path.stat().st_uid)
    shutil.copytree(original, lib / original.name, symlinks=False,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages', 'dist-packages'))
    for path in (lib / original.name).rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o500 if path.stat().st_mode & 0o111 else 0o400)
    (lib / original.name).chmod(0o700)
    spec = importlib.util.find_spec('websocket')
    require(spec is not None and spec.origin, 'installed websocket package is required; no download fallback')
    distribution = importlib.metadata.distribution('websocket-client')
    package = Path(spec.origin).parent
    metadata = Path(distribution._path)
    require(package.is_dir() and metadata.is_dir(), 'installed websocket package/metadata directories required')
    # Honor this exact interpreter's installed site policy (Debian uses
    # dist-packages); never add an import-path shim or use a global package.
    site = Path(python_site.getsitepackages([str(destination)])[-1])
    require(site.is_relative_to(destination),'interpreter site directory escaped selected prefix')
    site.mkdir(mode=0o700)
    websocket_sources = {}
    for source in (package,metadata):
        copied = site / source.name
        shutil.copytree(source,copied,symlinks=False,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for path in sorted(copied.rglob('*')):
            path.chmod(0o700 if path.is_dir() else 0o400)
            if path.is_file():
                origin = source / path.relative_to(copied)
                websocket_sources[str(path.relative_to(site))] = dict(source=str(origin),
                    resolved=str(origin.resolve()),sha256=digest(origin),bytes=origin.stat().st_size,
                    mode=stat.S_IMODE(origin.stat().st_mode),uid=origin.stat().st_uid,
                    symlink=os.readlink(origin) if origin.is_symlink() else None)
        copied.chmod(0o700)
    return target, dict(executable=str(executable), sha256=digest(executable), stdlib=str(original),
        source_files=source_files, symlinks='dereferenced into frozen regular bytes; links retained as provenance only',
        websocket=dict(version=distribution.version,package=str(package),metadata=str(metadata),
            restored_package=str(site/package.name),restored_metadata=str(site/metadata.name),
            requires=distribution.requires,source_files=websocket_sources,
            optional_extras='none selected; wsaccel/python-socks/test/docs extras are not mandatory dependencies'),
        relocation='unchanged CPython prefix discovery; no wrapper, patch or PYTHONHOME')


def copy_native_libraries(python, cli, destination, evidence):
    """Pin installed ELF dependencies without patching the selected interpreter."""
    destination.mkdir(mode=0o700)
    inputs = [python,cli,*sorted(python.parent.parent.glob('lib/python*/lib-dynload/*.so'))]
    libraries, loader = {}, None
    for index,path in enumerate(inputs):
        code,out,err = command(['ldd',path],evidence,'native-dependencies-'+str(index))
        text = (out+err).decode()
        require(code == 0 or 'not a dynamic executable' in text or 'statically linked' in text,
                'cannot inventory selected ELF dependencies')
        require('not found' not in text,'missing installed ELF dependency')
        for line in text.splitlines():
            match = re.search(r'^\s*(\S+)\s+=>\s+(/\S+)',line)
            direct = re.search(r'^\s*(/\S+)\s+\(',line)
            if match:
                name,source = match.groups()
            elif direct:
                source=direct[1];name=Path(source).name
                if 'ld-linux' in name:loader=Path(source)
            else:
                continue
            require('/' not in name,'noncanonical library SONAME')
            row=dict(source=source,resolved=str(Path(source).resolve()),sha256=digest(source),
                bytes=Path(source).stat().st_size,
                mode=stat.S_IMODE(Path(source).stat().st_mode),uid=Path(source).stat().st_uid,
                source_symlink=os.readlink(source) if Path(source).is_symlink() else None)
            require(name not in libraries or libraries[name]['sha256']==row['sha256'],
                    'conflicting installed ELF library names')
            libraries[name]=row
    require(loader is not None,'fixed unmodified ELF loader anchor must be declared')
    for name,row in libraries.items():
        path=destination/name
        shutil.copyfile(row['source'],path);path.chmod(0o500)
    return dict(libraries=libraries,search_path=str(destination),
        loader_anchor=dict(path=str(loader),resolved=str(loader.resolve()),sha256=digest(loader)),
        loader_policy='Unchanged ELF PT_INTERP uses this declared non-retired OS loader; identical loader bytes also archived.')


WEBSOCKET_PROOF = '''import json,sys,ssl,socket,importlib.metadata
from pathlib import Path
import websocket
left,right=socket.socketpair()
sender,receiver=websocket.WebSocket(),websocket.WebSocket()
sender.sock,receiver.sock=left,right
sender.connected=receiver.connected=True
try:
    sender.send("offline restored websocket closure")
    assert receiver.recv()=="offline restored websocket closure"
finally:
    left.close();right.close()
modules={name:str(Path(module.__file__).resolve()) for name,module in sys.modules.copy().items()
         if getattr(module,"__file__",None) and not str(module.__file__).startswith("<")}
maps=sorted({line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines()
             if line.split()[-1].startswith("/") and (".so" in Path(line.split()[-1]).name)})
print(json.dumps(dict(version=websocket.__version__,distribution=importlib.metadata.version("websocket-client"),
    modules=modules,native_maps=maps,ssl=ssl.OPENSSL_VERSION,socketpair_exchange=True)))
'''


def command(argv, evidence, name, *, body=b'', env=None, trace=False, cwd=None):
    argv = list(map(str, argv))
    write(evidence / (name + '.argv.json'), argv)
    (evidence / (name + '.stdin')).write_bytes(body)
    actual = (['strace', '-f', '-q', '-s', '4096', '-e', 'trace=process,file',
               '-o', str(evidence / (name + '.trace')), *argv] if trace else argv)
    started = time.monotonic()
    child = subprocess.Popen(actual, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, cwd=cwd)
    require(child.pid > 1, 'unsafe owned child')
    timed_out = False
    terminated = []
    try:
        stdout, stderr = child.communicate(body, timeout=100)
    except subprocess.TimeoutExpired:
        timed_out = True
        for pid in reversed(owned_process_tree(child.pid)):
            try:
                os.kill(pid,signal.SIGKILL)
                terminated.append(pid)
            except ProcessLookupError:
                pass
        stdout, stderr = child.communicate(timeout=10)
    (evidence / (name + '.stdout')).write_bytes(stdout)
    (evidence / (name + '.stderr')).write_bytes(stderr)
    write(evidence / (name + '.exit.json'), dict(pid=child.pid, exit=child.returncode,
        joined=True, timed_out=timed_out, terminated_owned_pids=terminated,
        group_signals=False, elapsed=time.monotonic()-started))
    require(not timed_out, 'owned command timed out: ' + name)
    return child.returncode, stdout, stderr


def origins(trace, payload, app, credentials, *, expected_exit=0):
    """File names/exec only; never trace read/write buffers or credential contents."""
    raw = Path(trace).read_text()
    forbidden = [str(ROOT), str(Path.home()/'git/cairn'), sysconfig.get_path('stdlib'), '/usr/local/lib/python',
                 '/usr/lib/python3/dist-packages', str(Path.home() / '.local/lib/python')]
    for prefix in forbidden:
        if '"' + prefix in raw:
            raise ClosureRefusal('restored process reached retired/checkout origin: ' + prefix)
    paths = set()
    link_metadata = {}
    for line in raw.splitlines():
        # Successful file operations only for the declared non-retired OS ABI.
        if re.search(r'= -1\b', line):
            continue
        quoted_values = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        if re.search(r'\breadlink(?:at)?\(', line):
            # strace prints the returned link target as its second string. It
            # is metadata, not an opened/executed file or a dependency origin.
            if len(quoted_values) >= 2:
                link_metadata[quoted_values[0]] = quoted_values[1]
            quoted_values = quoted_values[:1]
        for quoted in quoted_values:
            if quoted.startswith('/') and not quoted.startswith((str(payload), str(app))):
                paths.add(quoted)
    os_inputs = {}
    ancestor_metadata = {}
    ancestors = {str(path) for root in (payload,app) for path in root.parents}
    for name in sorted(paths):
        path = Path(name)
        if name in credentials:
            raise ClosureRefusal('credential escaped fixture application state')
        if name in ancestors:
            accesses = [line for line in raw.splitlines() if '"' + name + '"' in line]
            require(not any(re.search(r'\b(open\w*|exec\w*|creat|chdir)\(', line) for line in accesses),
                    'runtime read an ancestor outside its declared closure')
            ancestor_metadata[name] = 'path ownership/link metadata only; no open/exec/directory read'
        elif path.is_file() and name.startswith(('/usr/', '/lib/', '/lib64/', '/etc/')):
            os_inputs[name] = dict(sha256=digest(path), origin='declared non-retired base OS ABI/config')
        elif name.startswith(('/proc/', '/dev/', '/sys/', '/etc/', '/usr/', '/lib/', '/lib64/')) or name == '/':
            os_inputs[name] = dict(origin='base OS/process/device metadata; not a retired Python package')
        else:
            raise ClosureRefusal('undeclared external origin: ' + name)
    exits = {int(pid): int(code) for pid, code in re.findall(r'^(\d+)\s+\+\+\+ exited with (\d+) \+\+\+', raw, re.M)}
    require('killed by' not in raw and exits and all(code == expected_exit for code in exits.values()),
            'restored engine/helper child exited outside its declared expectation')
    return dict(retired_origin_reads=False, checkout_reads=False, base_OS=os_inputs,
        ancestor_metadata=ancestor_metadata, readlink_metadata=link_metadata, numeric_exits=exits,
        pids=sorted({int(x) for x in re.findall(r'^(\d+)\s', raw, re.M)}))


def owned_process_tree(pid):
    """Observe only descendants of the exact task-owned parent, without signals."""
    require(pid > 1, 'unsafe owned parent PID')
    found, pending = {pid}, [pid]
    while pending:
        current = pending.pop()
        path = Path('/proc') / str(current) / 'task' / str(current) / 'children'
        try:
            children = [int(value) for value in path.read_text().split()]
        except FileNotFoundError:
            children = []
        for child in children:
            require(child > 1, 'unsafe descendant PID')
            if child not in found:
                found.add(child)
                pending.append(child)
    return sorted(found)


def snapshot(host, evidence, name):
    statement = '''SELECT json_build_object(
      'memory', (SELECT count(*) FROM cairn.memory_record),
      'captures', (SELECT count(*) FROM cairn.capture_observation),
      'sessions', (SELECT count(*) FROM cairn.agent_session_attempt),
      'records', (SELECT coalesce(json_agg(row_to_json(r)), '[]'::json) FROM
        (SELECT record_id,version,kind,body,repo,task_id,run_id,observed_writer,witness
         FROM cairn.record_version ORDER BY record_id,version) r),
      'capture_outcomes', (SELECT coalesce(json_agg(row_to_json(c)), '[]'::json) FROM
        (SELECT harness,hook_event,outcome,selector_outcome FROM cairn.capture_observation
         ORDER BY sequence) c))'''
    code, stdout, stderr = command([host.pg_bin / 'psql', '-X', '-A', '-t', '-d', host.dsn,
        '-v', 'ON_ERROR_STOP=1', '-c', statement], evidence, name)
    require(code == 0 and not stderr, 'private Store readback failed')
    value = json.loads(stdout)
    write(evidence / (name + '.json'), value)
    return value


def unchanged_v3_refuses2(python, pin, inspected, evidence, app):
    path = ROOT / 'integrations/lifecycle/lifecycle_compatibility.py'
    spec = importlib.util.spec_from_file_location('retained2_negative_compatibility', path)
    compat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compat)
    literal_sha = hashlib.sha256(compat.VERIFIER_LITERAL.encode()).hexdigest()
    require(literal_sha == 'e65d18a8454cc616df3b80f395a67f965dfb06c2f6f3f4ba0c1284ab55731190',
            'approved /3 verifier literal changed')
    receipt = dict(schema='cairn.lifecycle-compatibility/1', source_revision='negative fixture only',
        snapshot_root=str(app / ('0' * 64)),
        members={name: dict(sha256='0' * 64, bytes=0) for name in compat.MEMBERS},
        bootstrap='lifecycle_compatibility.py', loader='lifecycle_bundle.py',
        roles={role: role + '.py' for role in ('memory','coordination','inbox_recall','inbox_binding')},
        aliases={}, binary={key:pin['images']['control'][key] for key in ('path','sha256')},
        binding=dict(inspected['retained_python']['binding'], schema='cairn.inbox-recall-binding/2'),
        memory_config=inspected['memory_config'], coordination_configs=inspected['coordination_configs'])
    negative = evidence / 'negative-v3-receipt.json'
    negative.write_bytes(installer.encoded(receipt))
    vector = compat.launch_vector(python, negative, digest(negative), 'memory', 'memory.py',
        inspected['memory_config']['path'], 13, app / 'unselected-launcher', '/bin/sh')
    code, stdout, stderr = command(vector, evidence, 'unchanged-v3-refuses2')
    require(code == 1 and stdout == b'' and stderr ==
        b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n',
        'unchanged /3 verifier no longer rejects negative /2 schema')
    write(evidence / 'unchanged-v3-refuses2.json', dict(literal_sha256=literal_sha, exit=code,
        scope='Shape-valid intentionally negative receipt; never a generated /2 compatibility selection.',
        collector_python=dict(path=str(python),sha256=digest(python)),
        original_genuine2_vectors_unchanged=True))


def check_disabled_binding(inspected, evidence):
    """Collector calls the existing selected API; no added loader or validator."""
    config_path = Path(inspected['memory_config']['path'])
    loader_path = config_path.parent / 'lifecycle_bundle.py'
    spec = importlib.util.spec_from_file_location('selected_retained2_bundle', loader_path)
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    bridge = loader.Bundle(config_path.parent).load('inbox_recall')
    config = json.loads(config_path.read_text())
    engine, readback = bridge.validate_binding(config)
    require(readback == config and engine.INBOX_RECALL_VERSION == 1,
            'disabled binding changed ordinary profile validation')
    try:
        bridge.validate_binding(config, require_enabled=True)
    except ValueError as error:
        require(str(error) == 'inbox recall binding is disabled', 'unexpected disabled binding refusal')
        write(evidence/'disabled-binding-refusal.json',dict(ordinary_validation='accepted',
            require_enabled='refused', message=str(error), operation='existing validate_binding API',
            engine_started=False))
    else:
        raise AssertionError('disabled binding admitted an enabled-required operation')


def run(images_path, evidence, process_receipts):
    evidence = Path(evidence)
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    original_home, original_path = os.environ['HOME'], os.environ['PATH']
    pin = json.loads(Path(images_path).read_text())
    images = {role: Path(pin['images'][role]['path']) for role in ('api', 'stager', 'control')}
    for role, path in images.items():
        require(digest(path) == pin['images'][role]['sha256'], 'changed selected b8 image: ' + role)
    app, payload = evidence / 'application', evidence / 'selected-runtime'
    app.mkdir(mode=0o700)
    payload.mkdir(mode=0o700)
    cluster = OwnedCluster.start(app / 'pg', pg_bin=pg_bindir())
    host, owned_pids = None, set()
    pg_pid = int((app / 'pg/data/postmaster.pid').read_text().splitlines()[0])
    owned_pids.update(owned_process_tree(pg_pid))
    try:
        host = DisposableHost.start(cluster, repo='fixture:r06-retained2-cold', binary=images['api'],
            database='retained2_' + uuid.uuid4().hex, home=app / 'api-home',
            socket_path=app / 'api.sock', log=evidence / 'api.log')
        owned_pids.update(owned_process_tree(host.process.pid))
        python, provenance = copy_python(payload / 'python')
        cli = payload / 'cairn'
        shutil.copyfile(images['api'], cli)
        cli.chmod(0o500)
        native = copy_native_libraries(python,cli,payload/'native-libraries',evidence)
        proof_program=payload/'websocket-proof.py'
        proof_program.write_text(WEBSOCKET_PROOF);proof_program.chmod(0o400)
        runtime_env=dict(os.environ,LD_LIBRARY_PATH=native['search_path'])
        provider = payload / 'fixture-selector'
        selected = dict(structured_output=dict(checkpoint=None, workstream=None, memories=[
            dict(record_id=None, kind='lesson', title='Cold restoration',
                 body='A restored retained Python lifecycle saved this synthetic fixture memory.')]))
        provider.write_text('#!' + str(python) + ' -IB\nimport json,sys\njson.load(sys.stdin)\nprint(' + repr(json.dumps(selected)) + ')\n')
        provider.chmod(0o500)
        account, work = app / 'account', app / 'workspace'
        account.mkdir(mode=0o700)
        work.mkdir(mode=0o700)
        temporary = app / 'tmp'
        temporary.mkdir(mode=0o700)
        common = dict(harness='claude', cairn=str(cli), socket=str(host.socket),
            token_file=str(host.token('worker')), repo=host.repo, config_home=str(account))
        memory = dict(common, state_dir=str(app / 'memory-state'), recall_mode='agent_tools',
                      context_bytes=9500, claude=str(provider), capture_observations=True)
        coordination = dict(common, state_dir=str(app / 'coordination-state'), native_delivery=True,
                            binding='retained2-cold-fixture')
        owner = types.SimpleNamespace(root=payload, evidence=evidence, images=images,
                                      memory=memory, coordination=coordination)
        release = OwnedUnit.stage_legacy(owner, python=python)
        inspected = installer.inspect_retained(str(images['stager']), release['selection'])
        vectors = inspected['launchers']
        require(all(v[0] == str(python) and v[1:3] == ['-I', '-B'] for v in vectors.values()),
                'producer silently selected another interpreter/vector')
        # Profile the actual intended absolute interpreter before argv/payload freeze.
        code, out, err = command([python, '-I', '-B', '-c',
            'import json,sys,sysconfig; print(json.dumps(dict(prefix=sys.prefix,base=sys.base_prefix,path=sys.path,version=sys.version,cache_tag=sys.implementation.cache_tag,SOABI=sysconfig.get_config_var("SOABI"))))'],
            evidence, 'selected-python-profile', env=runtime_env,cwd=work)
        require(code == 0 and not err, 'owned interpreter cannot relocate without an alternate anchor')
        profile = json.loads(out)
        require(Path(profile['prefix']).is_relative_to(payload)
                and all(not p or Path(p).is_relative_to(payload) for p in profile['path']),
                'owned interpreter retains a global Python package fallback')
        archive = evidence / 'offline-retained2.tar.gz'
        manifest = seal(payload, archive)
        write(evidence / 'payload-manifest.json', manifest)
        write(evidence / 'frozen-selection.json', dict(selection=release['selection'], argv=vectors,
            inspected=inspected, python=provenance, profile=profile, archive_sha256=digest(archive),
            native_libraries=native,
            credentials_archived=False, image_inputs={r:pin['images'][r] for r in images},
            image_manifest_sha256=digest(images_path),
            original_image_build={key:pin[key] for key in
                ('source_head','source_tree','toolchain','compiler_tools','image_vcs_metadata','vcs_stamped')},
            platform=dict(system=os.uname().sysname, machine=os.uname().machine,
                          release=os.uname().release),
            source_pins={name:digest(ROOT/name) for name in
                ('scripts/check_r06_lifecycle_offline_restore.py','scripts/disposable_host.py',
                 'scripts/check_r06_native_unit.py','scripts/stage_lifecycle_compatibility.py',
                 'integrations/lifecycle/lifecycle_compatibility.py')}))
        shutil.rmtree(payload)
        require(not payload.exists() and not Path(vectors['memory'][0]).exists(), 'retired paths still exist')
        write(evidence / 'initial-absence.json', dict(payload=str(payload), absent=True,
            interpreter=str(python), argv_unchanged=vectors, owned_children_using_payload=0))
        restore(payload, archive, manifest)
        require(installer.inspect_retained(str(images['stager']), release['selection']) == inspected,
                'restored producer selection/readback differs')
        before = snapshot(host, evidence, 'store-before')
        require(before['memory'] == 0 and before['captures'] == 0 and before['sessions'] == 0,
                'private fixture was not initially empty')
        event = dict(hook_event_name='SessionEnd', session_id=str(uuid.uuid4()), cwd=str(work),
            reason='other', messages=[dict(role='user', text='Remember the real cold restoration fixture result.'),
                                     dict(role='assistant', text='The retained lifecycle now runs only from its restored offline closure.')])
        verify(payload, manifest)
        engine_env = dict(host.env, CLAUDE_CONFIG_DIR=str(account), PWD=str(work), TMPDIR=str(temporary),
                          LD_LIBRARY_PATH=native['search_path'])
        write(evidence/'application-environment.json', dict(PWD=str(work),TMPDIR=str(temporary),
            CLAUDE_CONFIG_DIR=str(account),LD_LIBRARY_PATH=native['search_path'],HOME_preserved=True,PATH_preserved=True))
        code,out,err=command([python,'-I','-B',proof_program],evidence,'restored-websocket',
            env=engine_env,trace=True,cwd=work)
        require(code==0 and not err,'restored websocket availability/exchange failed')
        websocket_readback=json.loads(out)
        require(websocket_readback['version']==provenance['websocket']['version']
                and websocket_readback['distribution']==provenance['websocket']['version']
                and websocket_readback['socketpair_exchange']
                and all(Path(path).is_relative_to(payload) for path in websocket_readback['modules'].values()),
                'websocket imported an unsealed package origin')
        require(all(Path(path).is_relative_to(payload/'native-libraries')
                    or Path(path).is_relative_to(payload/'python')
                    or Path(path).resolve()==Path(native['loader_anchor']['resolved'])
                    for path in websocket_readback['native_maps']),
                'websocket loaded an unsealed native library outside the fixed OS loader anchor')
        websocket_origins=origins(evidence/'restored-websocket.trace',payload,app,set())
        owned_pids.update(websocket_origins['pids'])
        write(evidence/'restored-websocket-origins.json',websocket_origins)
        write(evidence/'restored-websocket.json',websocket_readback)
        code, stdout, stderr = command(vectors['memory'], evidence, 'restored-memory-capture',
            body=json.dumps(event).encode(), env=engine_env, trace=True, cwd=work)
        after = snapshot(host, evidence, 'store-after')
        require(code == 0 and not stderr and after['memory'] == before['memory'] + 1
                and after['captures'] == before['captures'] + 1
                and len(after['records']) == 1 and after['records'][0]['repo'] == host.repo
                and after['records'][0]['kind'] == 'lesson'
                and selected['structured_output']['memories'][0]['body'] in after['records'][0]['body']
                and after['capture_outcomes'] == [dict(harness='claude',hook_event='SessionEnd',
                                                      outcome='saved',selector_outcome='completed')],
                'restored actual memory capture did not write one Store record')
        native = json.loads((Path(release['manifest'])).read_text())
        require(native['schema'] == 'cairn.inbox-recall-binding/2' and native['enabled'] is False,
                'genuine disabled /2 selection changed')
        observed = origins(evidence / 'restored-memory-capture.trace', payload, app,
                           {str(host.token(role)) for role in ('worker','sender')})
        owned_pids.update(observed['pids'])
        write(evidence / 'restored-origins.json', observed)
        # Binding-disabled staging still preserves the old presence path. A
        # synthetic event cannot invent an observed native process or register it.
        code, co, ce = command(vectors['coordination'], evidence, 'disabled-coordination',
            body=json.dumps(dict(event, hook_event_name='SessionStart', source='startup')).encode(),
            env=engine_env, trace=True, cwd=work)
        require(code == 1 and co == b'' and ce ==
            b'Cairn coordination: INVALID_HOST: native process is not an observed ancestor; session presence unavailable\n',
            'synthetic native presence did not keep its exact guard refusal')
        disabled_origins = origins(evidence / 'disabled-coordination.trace', payload, app, set(), expected_exit=1)
        owned_pids.update(disabled_origins['pids'])
        write(evidence / 'disabled-origins.json', disabled_origins)
        check_disabled_binding(inspected, evidence)
        # The actual existing environment switch disables the coordinator itself.
        code, co, ce = command(vectors['coordination'], evidence, 'coordination-disabled-env',
            body=json.dumps(dict(event,hook_event_name='SessionStart',source='startup')).encode(),
            env=dict(engine_env,CAIRN_COORDINATION_DISABLED='1'),trace=True,cwd=work)
        require(code == 0 and co == b'{}\n' and ce == b'', 'disabled coordinator did not preserve its no-op contract')
        env_origins = origins(evidence/'coordination-disabled-env.trace',payload,app,set())
        owned_pids.update(env_origins['pids'])
        write(evidence/'coordination-disabled-env-origins.json',env_origins)
        require(snapshot(host,evidence,'store-disabled') == after,
                'disabled coordination changed private Store state')
        # Missing/tampered runtime members refuse before any effectful invocation.
        refusals = []
        package_init=Path(provenance['websocket']['restored_package'])/'__init__.py'
        targets={'interpreter':python,'websocket':package_init,'library':payload/'native-libraries/libssl.so.3'}
        for component,target in targets.items():
            for kind in ('missing','tampered','mixed-member','owner-mode'):
                raw, mode = target.read_bytes(), stat.S_IMODE(target.stat().st_mode)
                if kind == 'missing': target.unlink()
                elif kind in ('tampered','mixed-member'):
                    target.chmod(0o600)
                    other=targets['websocket'] if component!='websocket' else targets['library']
                    target.write_bytes(raw+b'changed' if kind=='tampered' else other.read_bytes())
                    target.chmod(mode)
                else: target.chmod(0o777)
                try:
                    verify(payload, manifest)
                except ClosureRefusal as error:
                    refusals.append(dict(component=component,kind=kind,refused=True,message=str(error),engine_started=False))
                else:
                    raise AssertionError('changed trusted runtime was accepted')
                if target.exists(): target.chmod(0o600)
                target.write_bytes(raw)
                target.chmod(mode)
                verify(payload, manifest)
        # Actual unchanged inspector checks all genuine /2 binding references and versions.
        binding = Path(release['manifest'])
        original = binding.read_bytes()
        binding_mode = stat.S_IMODE(binding.stat().st_mode)
        for kind in ('mixed-version', 'foreign-reference', 'wrong-binding'):
            changed = json.loads(original)
            if kind == 'mixed-version': changed['schema'] = 'cairn.inbox-recall-binding/3'
            elif kind == 'foreign-reference': changed['memory_config']['path'] = str(app / 'foreign' / 'memory.json')
            else: changed['memory_config']['sha256'] = '0' * 64
            binding.chmod(0o600)
            binding.write_bytes(installer.encoded(changed))
            binding.chmod(binding_mode)
            try:
                installer.inspect_retained(str(images['stager']), release['selection'])
            except (ValueError, RuntimeError) as error:
                refusals.append(dict(kind=kind, refused=True, message=str(error), engine_started=False))
            else:
                raise AssertionError('changed /2 selection was accepted')
            binding.chmod(0o600)
            binding.write_bytes(original)
            binding.chmod(binding_mode)
            verify(payload, manifest)
        write(evidence / 'refusals.json', refusals)
        # This /3-only negative is a collector diagnostic, never an alternate
        # selected /2 launcher. Its unchanged -I -S vector may write bytecode,
        # so do not point it into the frozen retained /2 dependency closure.
        diagnostic_python = Path(sys.executable).resolve()
        unchanged_v3_refuses2(diagnostic_python, pin, inspected, evidence, app)
        require(snapshot(host,evidence,'store-final') == after, 'negative controls changed Store state')
        verify(payload, manifest)
        require(installer.inspect_retained(str(images['stager']), release['selection']) == inspected,
                'final genuine /2 inspector readback differs')
        write(evidence / 'final-restoration-readback.json', dict(exact_manifest=True,
            selected_argv_unchanged=True, inspector_unchanged=True, binding_schema=native['schema']))
        require(os.environ['HOME'] == original_home and os.environ['PATH'] == original_path, 'host identity changed')
        write(evidence / 'result.json', dict(status='PASS finite retained2 cold restore', before=before, after=after,
            binding_schema=native['schema'], disabled_coordination=True, ordinary_memory_preserved=True,
            archive_sha256=digest(archive), manifest_sha256=digest(evidence/'payload-manifest.json'),
            selected_argv=vectors, origin_proof='restored-origins.json', refusals=len(refusals),
            websocket_version=websocket_readback['version'],websocket_exchange=True,
            websocket_origin_proof='restored-websocket-origins.json',
            limits=['Linux x86_64 and the declared non-retired base-OS ABI only.',
                    'Synthetic Claude ordinary SessionEnd memory capture; no native-client or other harness claim.',
                    'Runtime integrity is independently checked before launch; /2 source loader keeps its own contract.',
                    'Semantic reverse rebuild, all-host M05, common RC and production remain separate.']))
    finally:
        # Include every traced task/thread even when an earlier assertion failed.
        for trace in evidence.glob('*.trace'):
            owned_pids.update(int(x) for x in re.findall(r'^(\d+)\s', trace.read_text(), re.M))
        if host is not None:
            process = host.server.process if host.server else None
            pid = process.pid if process else None
            host.close()
            write(evidence / 'api-cleanup.json', dict(pid=pid, exit=process.returncode if process else None,
                joined=True, group_signals=False))
        pidfile = app / 'pg/data/postmaster.pid'
        pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
        if pid is not None:
            owned_pids.update(owned_process_tree(pid))
            code, _, _ = command([cluster.pg_bin / 'pg_ctl', '-D', cluster.root/'data',
                '-m', 'immediate', '-w', 'stop'], evidence, 'owned-pg-stop')
            require(code == 0, 'owned PostgreSQL stop command failed')
        stopped = cluster.stop()
        write(evidence / 'pg-cleanup.json', dict(pid=pid, stopped=stopped, group_signals=False))
        require(stopped, 'owned PostgreSQL did not stop')
        for receipt in evidence.glob('*.exit.json'):
            owned_pids.add(json.loads(receipt.read_text())['pid'])
        if host is not None and process is not None:
            owned_pids.add(process.pid)
        owned_pids.update(process_receipts.processes)
        require(all(row['exit'] is not None for row in process_receipts.processes.values()),
                'a collector subprocess lacks a numeric exit')
        live = [pid for pid in sorted(owned_pids) if (Path('/proc') / str(pid)).exists()]
        write(evidence / 'owned-pids-absent.json', dict(pids=sorted(owned_pids), live=live,
            all_greater_than_one=all(pid > 1 for pid in owned_pids), group_signals=False))
        require(not live, 'owned process survived cleanup: ' + repr(live))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    with ProcessReceipts(args.evidence) as process_receipts:
        run(args.images, args.evidence, process_receipts)
