"""Host orchestration: prepare/export, then check a standalone semantic package.

Builds and preparation use the unchanged campaign wrapper. Native acceptance
uses one direct bwrap entry per case with individual files, private process and
network namespaces, and no Python or development checkout in the runtime root.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import threading

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]

BUILD_PACKAGE = r'''
import hashlib,importlib.util,json,os,pathlib,shutil,subprocess,sys,tarfile
root=pathlib.Path('/work'); package=pathlib.Path('/tmp/standalone-package'); package.mkdir(mode=0o700)
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('package',root/'scripts/semantic_package.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
os.environ.update(CC='gcc',CXX='/usr/bin/g++',AR='/usr/bin/ar',AS='/usr/bin/as',LD='/usr/bin/ld',RUSTC='/opt/toolchains/rust/bin/rustc',RUSTDOC='/opt/toolchains/rust/bin/rustdoc',CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER='/usr/bin/gcc',CARGO_NET_OFFLINE='true',CARGO_NET_RETRY='0',RUSTUP_AUTO_INSTALL='0',TZ='America/Los_Angeles',TMPDIR='/tmp',GOMAXPROCS='2',GOFLAGS='-p=2',GOTELEMETRY='off',ORT_DISABLE_TELEMETRY='1')
pathlib.Path('/tmp/bin').mkdir();pathlib.Path('/tmp/bin/cc').symlink_to('/usr/bin/gcc');os.environ['PATH']='/tmp/bin:'+os.environ['PATH']
for name in ('build','source','model','native','tools','scripts','inputs'): (package/name).mkdir()
if pathlib.Path('/opt/reuse-build').exists():
 receipt=p.read_json('/opt/reuse-build/build.json')
 if receipt['source_inputs']!=p.records(p.source_files(root)):raise RuntimeError('retained build source binding differs')
 build=pathlib.Path('/opt/reuse-build')
else:
 pathlib.Path('/tmp/cargo-home/config.toml').write_text('[source.crates-io]\nreplace-with="owned"\n[source.owned]\ndirectory="/opt/directory-source"\n[net]\noffline=true\n')
 os.environ['CAIRN_CARGO_DIRECTORY_SOURCE']='/opt/directory-source'
 build=pathlib.Path('/tmp/semantic-build');receipt=p.build(root,build)
for name in ('build.json','cairn-semantic-rank','cairn-semantic-embed',*receipt['logs']):shutil.copy2(build/name,package/'build'/name)
for name,data in p.source_files(root).items():
 target=package/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
for name in p.MODELS:shutil.copyfile(pathlib.Path('/opt/model')/name,package/'model'/name)
for role in p.LIBRARIES:shutil.copyfile(pathlib.Path('/opt/native')/role,package/'native'/role)
for name in ('provenance.json','rank-request.json'):shutil.copyfile(pathlib.Path('/opt/inputs')/name,package/'inputs'/name)
for name in ('semantic_package.py','test_semantic_package_real.py'):shutil.copyfile(root/'scripts'/name,package/'scripts'/name)
tool_sources={'go-driver':'scripts/testdata/semantic-package-driver/main.go','observation-probe':'scripts/testdata/semantic-package-observer.rs'}
go_inputs={str(file.relative_to(root)):p.digest(file.read_bytes()) for file in root.rglob('*.go') if file.is_file()}
go_inputs.update({name:p.digest((root/name).read_bytes()) for name in ('go.mod','go.sum')})
if pathlib.Path('/opt/reuse-tools').exists():
 binding=p.read_json('/opt/reuse-tool-binding.json')
 if binding.get('go_inputs')!=go_inputs:raise RuntimeError('retained Go driver dependency-source binding differs; build fresh fixture tools')
 for tool,source in tool_sources.items():
  selected=pathlib.Path('/opt/reuse-tools')/tool
  if binding['source_sha256'][source]!=p.digest((root/source).read_bytes()) or binding['binaries'][tool]!=p.digest(selected.read_bytes()):raise RuntimeError('retained tool binding differs: '+tool)
  shutil.copy2(selected,package/'tools'/tool)
else:
 subprocess.run(['go','build','-buildvcs=false','-o',str(package/'tools/go-driver'),'./scripts/testdata/semantic-package-driver'],cwd=root,check=True)
 spec=importlib.util.spec_from_file_location('fixtures',root/'scripts/test_semantic_package_real.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
 f.build_probe(build,root,package/'tools/observation-probe')
binding={'schema':'cairn.semantic-package-tools/1','source_sha256':{source:p.digest((root/source).read_bytes()) for source in tool_sources.values()},'go_inputs':go_inputs,'go_version':p.capture(['go','version']),'go_build_info':p.capture(['go','version','-m',str(package/'tools/go-driver')]),'binaries':{name:p.digest((package/'tools'/name).read_bytes()) for name in tool_sources}}
(package/'tool-binding.json').write_bytes(p.canonical(binding)+b'\n')
files={str(file.relative_to(package)):p.digest(file.read_bytes()) for file in package.rglob('*') if file.is_file()}
(package/'standalone.json').write_bytes(p.canonical({'schema':'cairn.semantic-standalone-fixture/1','files':files,'shadow_paths':__SHADOW_PATHS__})+b'\n')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
 for path in sorted(package.iterdir()):archive.add(path,arcname=path.name)
'''

PREPARE_FIXTURES = r'''
import pathlib,subprocess,sys,tarfile
output=pathlib.Path('/tmp/semantic-package-fixtures')
argv=['/usr/bin/python3','-I','-B','/work/scripts/test_semantic_package_real.py','--source-root','/work/source','--build','/work/build','--probe','/work/tools/observation-probe','--go-driver','/work/tools/go-driver','--model-dir','/work/model','--ort','/work/native/ort','--openblas','/work/native/openblas','--gfortran','/work/native/gfortran','--quadmath','/work/native/quadmath','--provenance','/work/inputs/provenance.json','--rank-request','/work/inputs/rank-request.json','--output',str(output)]
try:result=subprocess.run(argv,stdout=sys.stderr,stderr=sys.stderr)
finally:
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
  if output.exists():archive.add(output,arcname='fixtures')
raise SystemExit(result.returncode)
'''

BUILD_NATIVE = r'''
import os,pathlib,subprocess,sys,tarfile
root=pathlib.Path('/work');out=pathlib.Path('/tmp/native-tools');out.mkdir(mode=0o700)
os.environ.update(CGO_ENABLED='0',GOMAXPROCS='2',GOFLAGS='-p=2',GOTELEMETRY='off')
for name,target in (('native-fixture','./scripts/testdata/semantic-package-native'),('go-driver','./scripts/testdata/semantic-package-driver')):
 subprocess.run(['go','build','-buildvcs=false','-o',str(out/name),target],cwd=root,check=True)
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
 for path in sorted(out.iterdir()):archive.add(path,arcname=path.name)
'''

# Deliberately invalid system copies, scoped to this namespace. Actual selected
# sealed image hashes must still be observed for every successful mode.
SHADOW_ALIASES = ('/usr/lib/x86_64-linux-gnu/libgfortran.so.5',
                  '/usr/lib/x86_64-linux-gnu/libquadmath.so.0',
                  '/usr/lib/x86_64-linux-gnu/libblas.so.3')


def isolated_entry(isolation, source, code, directory, name, binds=()):
    spec = isolation.build_sandbox_spec(source, chdir='/work', ro_binds=binds)
    read_fd, write_fd = os.pipe()
    report_bytes = []

    def read_report():
        with os.fdopen(read_fd, 'rb') as stream:
            report_bytes.append(stream.read(1048576))

    argv = isolation.namespace_argv(source, ['/usr/bin/python3', '-I', '-B', '-c', code],
                                     spec=spec, report_fd=write_fd, self_test_only=False)
    (directory / (name + '-command.json')).write_text(json.dumps({'argv': argv, 'spec': spec}, indent=2))
    with (directory / (name + '.tar.gz')).open('wb') as stdout, (directory / (name + '.log')).open('wb') as stderr:
        try:
            proc = subprocess.Popen(argv, env={'PATH': '/usr/bin:/bin'}, pass_fds=(write_fd,),
                                    stdout=stdout, stderr=stderr, start_new_session=True)
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        reader = threading.Thread(target=read_report, daemon=True)
        reader.start()
        try:
            code = proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            code = 124
        finally:
            reader.join(timeout=5)
    report = json.loads(report_bytes[0]) if report_bytes and report_bytes[0] else {'passed': False}
    (directory / (name + '-isolation.json')).write_text(json.dumps(report, indent=2))
    if not report.get('passed') and code == 0:
        code = 97
    (directory / (name + '-result.json')).write_text(json.dumps({'returncode': code, 'isolation_passed': report.get('passed')}))
    if code:
        raise RuntimeError(name + ' failed; retained log: ' + str(directory / (name + '.log')))


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fixture_module():
    spec = importlib.util.spec_from_file_location('real_fixture', ROOT / 'scripts/test_semantic_package_real.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_files(fixtures, tools, request):
    """Prepare data bindings; the native entry independently checks every byte."""
    selected = json.loads((fixtures / 'stage-result.json').read_bytes())
    stage_path = Path(selected['payload']).parents[1]
    stage = fixtures / 'stage'
    manifest = stage / Path(selected['manifest']).relative_to(stage_path)
    if file_sha(manifest) != selected['sha256']:
        raise RuntimeError('retained manifest whole-file hash differs')
    metadata = json.loads(manifest.read_bytes())
    bindings = {str(stage_path / path.relative_to(stage)): path
                for path in stage.rglob('*') if path.is_file()}
    for mode, launch in selected['launchers'].items():
        if file_sha(bindings[launch['path']]) != launch['sha256']:
            raise RuntimeError('retained launcher differs: ' + mode)
    # Resolve aliases to these selected ABI objects; never expose a system tree.
    for item in metadata['requirements']['host_abi_prerequisites']:
        path = Path(item['path'])
        if file_sha(path) != item['sha256']:
            raise RuntimeError('retained ABI prerequisite differs: ' + str(path))
        aliases = [alias for alias in path.parent.iterdir() if alias.resolve() == path]
        for alias in aliases:
            bindings[str(alias)] = path
            if str(alias).startswith('/usr/lib/'):
                bindings[str(alias)[4:]] = path
    loader = Path('/lib64/ld-linux-x86-64.so.2').resolve(strict=True)
    if str(loader) not in {item['path'] for item in metadata['requirements']['host_abi_prerequisites']}:
        raise RuntimeError('loader missing from retained ABI prerequisites')
    bindings['/lib64/ld-linux-x86-64.so.2'] = loader
    for src, dst in (('/bin/sh', '/bin/sh'), ('/usr/bin/env', '/usr/bin/env'),
                     ('/usr/bin/find', '/usr/bin/find'), ('/usr/bin/sha256sum', '/usr/bin/sha256sum')):
        bindings[dst] = Path(src).resolve(strict=True)
    bindings.update({'/fixture/native-fixture': tools / 'native-fixture',
                     '/fixture/go-driver': tools / 'go-driver'})
    request = json.loads(Path(request).read_bytes())
    expected = {route + '-' + mode: json.loads((fixtures / (route + '-' + mode + '.stdout')).read_bytes())
                for route in ('direct', 'go') for mode in ('rank', 'stream', 'embed')}
    selection = {'launchers': selected['launchers'], 'request': request.get('request', request),
                 'expected': expected, 'host_pid_namespace': os.readlink('/proc/self/ns/pid'),
                 'host_net_namespace': os.readlink('/proc/self/ns/net')}
    return bindings, selection, Path(selected['payload'])


def native_argv(bindings, descriptor, command):
    argv = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session',
            '--tmpfs', '/tmp', '--tmpfs', '/nonexistent', '--chmod', '0700', '/nonexistent',
            '--dev', '/dev', '--proc', '/proc']
    for destination, source in sorted(bindings.items()):
        argv += ['--ro-bind', str(source), destination]
    argv += ['--ro-bind', str(descriptor), '/fixture/selection.json', '--remount-ro', '/',
             '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
             '--setenv', 'HOME', '/nonexistent', '--chdir', '/tmp',
             '/fixture/native-fixture', *command]
    return argv


def native_case(name, bindings, selection, payload, directory, *, mode='rank', route='direct',
                negative=False, control=False, contaminated=False):
    output = directory / name
    output.mkdir(mode=0o700)
    selected = dict(selection, files={path: file_sha(source) for path, source in bindings.items()})
    # A contaminant is deliberately not admitted to the expected runtime closure.
    if contaminated:
        selected['files'].pop('/control/python3', None)
    descriptor = output / 'selection.json'
    descriptor.write_text(json.dumps(selected, sort_keys=True) + '\n')
    command = ['--mode', mode, '--route', route]
    if negative:
        command.append('--negative')
    if control:
        command.append('--observer-control')
    argv = native_argv(bindings, descriptor, command)
    traced = ['/usr/bin/strace', '-f', '-s', '256', '-yy', '-e',
              'trace=execve,connect,openat,creat,mkdir,mkdirat,write,pwrite64,truncate,rename,unlink',
              '-o', str(output / 'effects.trace'), *argv]
    (output / 'command.json').write_text(json.dumps({'argv': traced, 'runtime_argv': argv}, indent=2) + '\n')
    observed, errors, done = {}, [], threading.Event()
    fixture = fixture_module()

    def sample():
        try:
            while not done.is_set():
                for row in fixture.process_snapshot(payload, observed, ancestor=proc.pid, minimal_root=True):
                    observed[row['pid']] = row
                done.wait(0.01)
        except BaseException as error:
            errors.append(str(error))

    with (output / 'native.json').open('wb') as stdout, (output / 'entry.stderr').open('wb') as stderr:
        proc = subprocess.Popen(traced, stdout=stdout, stderr=stderr,
                                env={'PATH': '/usr/bin:/bin'}, start_new_session=True)
        thread = threading.Thread(target=sample)
        thread.start()
        try:
            proc.wait(timeout=150)
        except subprocess.TimeoutExpired:
            if proc.pid <= 1:
                raise RuntimeError('refusing process-group control for PID <= 1')
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            done.set()
            thread.join()
    report = {'returncode': proc.returncode, 'mapping_errors': errors,
              'workers': list(observed.values()), 'trace_sha256': file_sha(output / 'effects.trace')}
    (output / 'outer-observations.json').write_text(json.dumps(report, indent=2) + '\n')
    if not (output / 'native.json').stat().st_size:
        raise RuntimeError(name + ': native entry did not start; see ' + str(output / 'entry.stderr'))
    native = json.loads((output / 'native.json').read_bytes())
    for stream in ('stdout', 'stderr'):
        (output / ('worker.' + stream)).write_bytes(base64.b64decode(native[stream] or ''))
    if contaminated:
        if proc.returncode == 0 or native['command_started'] or 'Python or development checkout exposed' not in native.get('error', ''):
            raise RuntimeError('bound-Python positive control did not refuse before worker start')
        return {'name': name, 'python_control_refused': True}
    if proc.returncode or errors or not native['boundary']['passed']:
        raise RuntimeError(name + ': native fixture failed: ' + native.get('error', repr(errors)))
    effects = fixture.trace_effects((output / 'effects.trace').read_text())
    if control:
        if not effects['connects'] or not effects['store_mutations']:
            raise RuntimeError('native observer did not detect connect/store controls')
    elif effects['connects'] or effects['store_mutations']:
        raise RuntimeError(name + ': connect or telemetry-store mutation observed')
    elif not negative and (not observed or not native['exact_retained_reply']):
        raise RuntimeError(name + ': missing actual worker mappings or exact retained comparison')
    return {'name': name, 'worker_count': len(observed), 'exact_retained_reply': native['exact_retained_reply'],
            'connect_attempts': len(effects['connects']), 'store_mutations': len(effects['store_mutations']),
            'runtime_files': len(native['boundary']['files'])}


def check_native(fixtures, tools, request, directory):
    bindings, selected, payload = runtime_files(fixtures, tools, request)
    # Generic system math copies are wrong on purpose; selected memfd hashes
    # must still match the retained payload for each successful worker.
    for alias in SHADOW_ALIASES:
        bindings[alias] = tools / 'go-driver'
    rows = [native_case('python-control', dict(bindings, **{'/control/python3': Path('/usr/bin/python3').resolve()}),
                        selected, payload, directory, contaminated=True)]
    rows.append(native_case('observer-control', bindings, selected, payload, directory, control=True))
    for route in ('direct', 'go'):
        for mode in ('rank', 'stream', 'embed'):
            rows.append(native_case(route + '-' + mode, bindings, selected, payload, directory, mode=mode, route=route))
    wrong = directory / 'wrong-selected-bytes'
    wrong.write_bytes(b'wrong selected bytes; retained package is unchanged\n')
    for name, member, missing in (('missing-library', 'native/ort', True), ('wrong-library', 'native/openblas', False),
                                  ('missing-asset', 'model/config.json', True), ('wrong-asset', 'model/tokenizer.json', False)):
        changed = dict(bindings)
        if missing:
            del changed[str(payload / member)]
        else:
            changed[str(payload / member)] = wrong
        for mode in ('rank', 'stream', 'embed'):
            rows.append(native_case(name + '-' + mode, changed, selected, payload, directory, mode=mode, negative=True))
    report = {'schema': 'cairn.semantic-native-package-proof/1', 'cases': rows,
              'qualification': 'local native-root proof only; managed host/ABI/CPU inventory and full S04 remain open'}
    (directory / 'native-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifact-dir', 'rank-request'):
        parser.add_argument('--' + name, required=True)
    for name in ('model-dir', 'ort', 'openblas', 'gfortran', 'quadmath', 'provenance'):
        parser.add_argument('--' + name)
    parser.add_argument('--retained-fixtures', help='exact retained stage-result, stage tree and six raw route outputs')
    parser.add_argument('--directory-source')
    parser.add_argument('--reuse-build')
    parser.add_argument('--reuse-tools')
    parser.add_argument('--reuse-tool-binding')
    args = parser.parse_args()
    if not args.retained_fixtures and not args.directory_source and not args.reuse_build:
        parser.error('select --directory-source or an exact retained --reuse-build')
    if not args.retained_fixtures and any(getattr(args, name) is None for name in
                                        ('model_dir', 'ort', 'openblas', 'gfortran', 'quadmath', 'provenance')):
        parser.error('new package preparation requires all model, library and provenance inputs')
    if bool(args.reuse_tools) != bool(args.reuse_tool_binding):
        parser.error('--reuse-tools requires --reuse-tool-binding')
    directory = Path(args.artifact_dir)
    if not directory.is_absolute():
        parser.error('--artifact-dir must be absolute')
    directory.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('isolation', ROOT / 'scripts/test_hybrid_runtime_operations.py')
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
    isolated_entry(isolation, ROOT, BUILD_NATIVE, directory, 'native-tools')
    tools = directory / 'tools'
    tools.mkdir(mode=0o700)
    with tarfile.open(directory / 'native-tools.tar.gz') as archive:
        archive.extractall(tools, filter='data')
    if args.retained_fixtures:
        fixtures = Path(args.retained_fixtures).resolve(strict=True)
        report = check_native(fixtures, tools, args.rank_request, directory)
        print(json.dumps({'native': 'PASS', 'cases': len(report['cases']), 'artifact_dir': str(directory),
                          'qualification': report['qualification']}))
        return
    binds = [str(Path(args.model_dir) / name) + ':/opt/model/' + name
             for name in ('model_optimized.onnx', 'config.json', 'tokenizer.json', 'special_tokens_map.json', 'tokenizer_config.json')]
    binds += [getattr(args, role) + ':/opt/native/' + role for role in ('ort', 'openblas', 'gfortran', 'quadmath')]
    binds += [args.provenance + ':/opt/inputs/provenance.json', args.rank_request + ':/opt/inputs/rank-request.json']
    for attr, target in (('directory_source', '/opt/directory-source'), ('reuse_build', '/opt/reuse-build'),
                         ('reuse_tools', '/opt/reuse-tools'), ('reuse_tool_binding', '/opt/reuse-tool-binding.json')):
        if getattr(args, attr):
            binds.append(getattr(args, attr) + ':' + target)
    # Bind over existing system objects; bwrap cannot create a new file below
    # its read-only /usr mount. Only this namespace sees the wrong copies.
    shadow_paths = [str(Path(path).resolve(strict=True)) for path in SHADOW_ALIASES]
    isolated_entry(isolation, ROOT, BUILD_PACKAGE.replace('__SHADOW_PATHS__', repr(shadow_paths)), directory, 'prepare', binds)
    standalone = directory / 'standalone'
    standalone.mkdir(mode=0o700)
    with tarfile.open(directory / 'prepare.tar.gz') as archive:
        archive.extractall(standalone, filter='data')
    shadows = [str(standalone / 'tools/go-driver') + ':' + path for path in shadow_paths]
    isolated_entry(isolation, standalone, PREPARE_FIXTURES, directory, 'prepare-fixtures', shadows)
    retained = directory / 'prepared-fixtures'
    retained.mkdir(mode=0o700)
    with tarfile.open(directory / 'prepare-fixtures.tar.gz') as archive:
        archive.extractall(retained, filter='data')
    report = check_native(retained / 'fixtures', tools, args.rank_request, directory)
    print(json.dumps({'prepare': 'PASS', 'native': 'PASS', 'artifact_dir': str(directory),
                      'qualification': report['qualification']}))


if __name__ == '__main__':
    main()
