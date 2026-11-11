"""Run deterministic S-05 Go/index/PostgreSQL fixtures in the common wrapper.

This is host orchestration only. Builds, generated protocol workers, the actual
Go API, PostgreSQL, assertions and report classification run inside the unchanged
test_hybrid_runtime_operations.py namespace. No model is loaded. Real Python /
Rust transition qualification remains a separate, prerequisite-gated matrix.
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

INNER = r'''
import hashlib,json,os,pathlib,subprocess,sys,tarfile,tempfile,traceback
root=pathlib.Path(tempfile.mkdtemp(prefix='semantic-transition-',dir='/tmp'))
artifacts=root/'artifacts';artifacts.mkdir(mode=0o700)
binary=root/'semantic.test';cli=root/'cairn'
env=dict(os.environ,CC='/usr/bin/gcc',GOMAXPROCS='2',GOFLAGS='-p=2',GOTELEMETRY='off',ORT_DISABLE_TELEMETRY='1',TMPDIR='/tmp')
pathlib.Path('/tmp/bin').mkdir(exist_ok=True)
pathlib.Path('/tmp/bin/cc').symlink_to('/usr/bin/gcc')
env['PATH']='/tmp/bin:'+env['PATH']
commands=[];started=False;code=1;pg=None
def run(argv,name,timeout=180,selected_env=None):
 commands.append(dict(argv=argv,log=name))
 with (artifacts/name).open('wb') as log:
  return subprocess.run(argv,env=selected_env or env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,check=True)
try:
 run(['go','test','-c','-race','-o',str(binary),'./semantic'],'build-semantic.log')
 run(['go','build','-buildvcs=false','-o',str(cli),'./cmd/cairn'],'build-api.log')
 pg=pathlib.Path(subprocess.check_output(['pg_config','--bindir'],env=env,text=True).strip())
 (root/'socket').mkdir(mode=0o700)
 run([str(pg/'initdb'),'-D',str(root/'data'),'--auth-local=trust','--auth-host=reject','--no-locale','-E','UTF8'],'initdb.log')
 run([str(pg/'pg_ctl'),'-D',str(root/'data'),'-l',str(artifacts/'postgres.log'),'-o','-F -k '+str(root/'socket')+" -c listen_addresses=''",'-w','start'],'postgres-start.log')
 started=True
 (root/'owned-cluster').write_text('semantic-transition-fixture/1\n')
 selected=dict(env,CAIRN_TRANSITION_DISPOSABLE_ROOT=str(root),CAIRN_TEST_DATABASE_URL='host='+str(root/'socket')+' dbname=postgres sslmode=disable',CAIRN_TRANSITION_CLI=str(cli),CAIRN_TRANSITION_REPORT_DIR=str(artifacts))
 run(['go','tool','test2json','-t','-p','semantic',str(binary),'-test.v','-test.run=^TestSemanticTransition','-test.timeout=120s'],'tests.jsonl',timeout=150,selected_env=selected)
 events=[json.loads(line) for line in (artifacts/'tests.jsonl').read_text().splitlines()]
 passed=[e['Test'] for e in events if e['Action']=='pass' and 'Test' in e]
 failed=[e.get('Test','package') for e in events if e['Action']=='fail']
 skipped=[e.get('Test','package') for e in events if e['Action']=='skip']
 matrix=[name for name in passed if name.startswith('TestSemanticTransitionDeterministicMatrix/') and name.count('/')==1]
 if len(matrix)!=10 or failed or skipped:raise RuntimeError('incomplete deterministic transition matrix')
 report=dict(schema='cairn.semantic-transition-fixture/1',kind='deterministic-protocol-workers-real-go-and-postgresql',matrix_cases=matrix,passed=passed,failed=failed,skipped=skipped,real_worker_acceptance='BLOCKED: accepted S04R Python closure and S04/R01 supported-class/identity qualification required',binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),api_sha256=hashlib.sha256(cli.read_bytes()).hexdigest())
 (artifacts/'report.json').write_text(json.dumps(report,indent=2)+'\n');code=0
except BaseException:
 (artifacts/'failure.txt').write_text(traceback.format_exc())
finally:
 if started:
  try:run([str(pg/'pg_ctl'),'-D',str(root/'data'),'-m','immediate','-w','stop'],'postgres-stop.log',timeout=20)
  except BaseException:
   (artifacts/'cleanup-failure.txt').write_text(traceback.format_exc());code=1
 (artifacts/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
 (artifacts/'source-inputs.json').write_text(json.dumps({str(p.relative_to('/work')):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/work').rglob('*') if p.is_file() and p.suffix in ('.go','.sql','.mod','.sum','.py')},sort_keys=True)+'\n')
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:archive.add(artifacts,arcname='artifacts')
sys.exit(code)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', required=True, type=Path)
    args = parser.parse_args()
    output = args.artifact_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        'isolation', ROOT / 'scripts/test_hybrid_runtime_operations.py')
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
    sandbox = isolation.build_sandbox_spec(ROOT)
    read_fd, write_fd = os.pipe()
    received = []

    def read_report():
        with os.fdopen(read_fd, 'rb') as stream:
            received.append(stream.read(1048576))

    argv = isolation.namespace_argv(ROOT, ['/usr/bin/python3', '-I', '-B', '-c', INNER],
                                    spec=sandbox, report_fd=write_fd)
    (output / 'command.json').write_text(json.dumps({'argv': argv, 'spec': sandbox}, indent=2))
    with (output / 'artifacts.tar.gz').open('wb') as stdout, (output / 'wrapper.log').open('wb') as stderr:
        try:
            child = subprocess.Popen(argv, env={'PATH': '/usr/bin:/bin'}, pass_fds=(write_fd,),
                                     stdout=stdout, stderr=stderr, start_new_session=True)
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        reader = threading.Thread(target=read_report, daemon=True)
        reader.start()
        try:
            code = child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            code = 124
        finally:
            reader.join(timeout=5)
    report = json.loads(received[0]) if received and received[0] else {'passed': False}
    (output / 'isolation.json').write_text(json.dumps(report, indent=2))
    if (output / 'artifacts.tar.gz').stat().st_size:
        with tarfile.open(output / 'artifacts.tar.gz') as archive:
            archive.extractall(output, filter='data')
    (output / 'result.json').write_text(json.dumps({'returncode': code, 'isolation_passed': report.get('passed')}))
    if code or not report.get('passed'):
        raise SystemExit('transition fixture failed; retained artifacts: ' + str(output))
    print('S05 deterministic transition fixtures passed; real-worker qualification remains blocked. ' + str(output))


if __name__ == '__main__':
    main()
