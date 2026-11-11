"""Host orchestration: prepare/export, then check a standalone semantic package.

All builds, model execution and assertions run in two sequential entries through
the unchanged campaign wrapper. The host only routes data and extracts archives.
Python/bootstrap tools remain visible; this is not literal no-Python acceptance.
"""
import argparse
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

CHECK_PACKAGE = r'''
import pathlib,subprocess,sys,tarfile
output=pathlib.Path('/tmp/semantic-package-fixtures')
argv=['/usr/bin/python3','-I','-B','/work/scripts/test_semantic_package_real.py','--source-root','/work/source','--build','/work/build','--probe','/work/tools/observation-probe','--go-driver','/work/tools/go-driver','--model-dir','/work/model','--ort','/work/native/ort','--openblas','/work/native/openblas','--gfortran','/work/native/gfortran','--quadmath','/work/native/quadmath','--provenance','/work/inputs/provenance.json','--rank-request','/work/inputs/rank-request.json','--output',str(output)]
try:result=subprocess.run(argv,stdout=sys.stderr,stderr=sys.stderr)
finally:
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
  if output.exists():archive.add(output,arcname='fixtures')
raise SystemExit(result.returncode)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifact-dir', 'model-dir', 'ort', 'openblas', 'gfortran', 'quadmath', 'provenance', 'rank-request'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--directory-source')
    parser.add_argument('--reuse-build')
    parser.add_argument('--reuse-tools')
    parser.add_argument('--reuse-tool-binding')
    args = parser.parse_args()
    if not args.directory_source and not args.reuse_build:
        parser.error('select --directory-source or an exact retained --reuse-build')
    if bool(args.reuse_tools) != bool(args.reuse_tool_binding):
        parser.error('--reuse-tools requires --reuse-tool-binding')
    directory = Path(args.artifact_dir)
    if not directory.is_absolute():
        parser.error('--artifact-dir must be absolute')
    directory.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('isolation', ROOT / 'scripts/test_hybrid_runtime_operations.py')
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
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
    isolated_entry(isolation, standalone, CHECK_PACKAGE, directory, 'execute', shadows)
    print(json.dumps({'prepare': 'PASS', 'execute': 'PASS', 'artifact_dir': str(directory),
                      'qualification': 'incomplete; literal Python absence and managed inventory/CPU classes remain gaps'}))


if __name__ == '__main__':
    main()
