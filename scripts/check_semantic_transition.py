"""Run S-05 fixtures through the unchanged common isolation wrapper.

The default runs deterministic protocol workers. --real-case1 selects the
retained Python/Rust/Python transition; --real-case2 selects two same-generation
mid-rebuild restarts; --real-case3 selects worker death and earlier caller deadlines.
--real-case4 holds an old real embedding result across a committed source edit.
Every build,
worker, Go API, disposable PostgreSQL cluster and assertion runs inside the
wrapper; the host transports data. Local execution does not close full S-05.
"""

import argparse
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

INNER = r'''
import hashlib,json,os,pathlib,subprocess,sys,tarfile,tempfile,traceback
root=pathlib.Path(tempfile.mkdtemp(prefix='semantic-transition-',dir='/tmp'))
real=__REAL_CASE1__
real_case=__REAL_CASE__
artifacts=root/'artifacts';artifacts.mkdir(mode=0o700)
binary=root/'semantic.test';cli=root/'cairn'
env=dict(os.environ,CC='/usr/bin/gcc',GOMAXPROCS='2',GOFLAGS='-p=2',GOTELEMETRY='off',ORT_DISABLE_TELEMETRY='1',TMPDIR='/tmp')
pathlib.Path('/tmp/bin').mkdir(exist_ok=True)
pathlib.Path('/tmp/bin/cc').symlink_to('/usr/bin/gcc')
env['PATH']='/tmp/bin:'+env['PATH']
commands=[];started=False;code=1;pg=None
def input_inventory():
 return {str(p.relative_to('/work')):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/work').rglob('*') if p.is_file() and p.suffix in ('.go','.sql','.mod','.sum','.py')}
source_inputs=input_inventory()
(artifacts/'source-inputs-prebuild.json').write_text(json.dumps(source_inputs,sort_keys=True)+'\n')
def run(argv,name,timeout=180,selected_env=None):
 commands.append(dict(argv=argv,log=name))
 with (artifacts/name).open('wb') as log:
  return subprocess.run(argv,env=selected_env or env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,check=True)
try:
 if real:
  import importlib.util
  spec=importlib.util.spec_from_file_location('real_transition','/work/scripts/semantic_transition_real.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  retained,python,rust=module.prepare(artifacts,sample_checkpoint=real_case>=2)
 run(['go','version'],'go-version.log')
 run(['go','test','-c','-race','-o',str(binary),'./semantic'],'build-semantic.log')
 run(['go','build','-buildvcs=false','-o',str(cli),'./cmd/cairn'],'build-api.log')
 pg=pathlib.Path(subprocess.check_output(['pg_config','--bindir'],env=env,text=True).strip())
 (root/'socket').mkdir(mode=0o700)
 run([str(pg/'initdb'),'-D',str(root/'data'),'--auth-local=trust','--auth-host=reject','--no-locale','-E','UTF8'],'initdb.log')
 run([str(pg/'pg_ctl'),'-D',str(root/'data'),'-l',str(artifacts/'postgres.log'),'-o','-F -k '+str(root/'socket')+" -c listen_addresses=''",'-w','start'],'postgres-start.log')
 started=True
 (root/'owned-cluster').write_text('semantic-transition-fixture/1\n')
 selected=dict(env,CAIRN_TRANSITION_DISPOSABLE_ROOT=str(root),CAIRN_TEST_DATABASE_URL='host='+str(root/'socket')+' dbname=postgres sslmode=disable',CAIRN_TRANSITION_CLI=str(cli),CAIRN_TRANSITION_REPORT_DIR=str(artifacts))
 if real:
  selected['CAIRN_TRANSITION_REAL_SELECTION']=str(artifacts/'real-selection.json')
  selected['CAIRN_TRANSITION_REAL_CASE']=str(real_case)
  test_name='TestSemanticRealTransitionCase'+str(real_case)
  command=['go','tool','test2json','-t','-p','semantic',str(binary),'-test.v','-test.run=^'+test_name+'$','-test.timeout=900s']
  commands.append(dict(argv=command,log='tests.jsonl'))
  module.run_observed(command,selected,artifacts,retained,python,rust)
 else:
  run(['go','tool','test2json','-t','-p','semantic',str(binary),'-test.v','-test.run=^TestSemanticTransition','-test.timeout=120s'],'tests.jsonl',timeout=150,selected_env=selected)
 if input_inventory()!=source_inputs:raise RuntimeError('source changed during build/fixture execution')
 events=[json.loads(line) for line in (artifacts/'tests.jsonl').read_text().splitlines()]
 passed=[e['Test'] for e in events if e['Action']=='pass' and 'Test' in e]
 failed=[e.get('Test','package') for e in events if e['Action']=='fail']
 skipped=[e.get('Test','package') for e in events if e['Action']=='skip']
 matrix=[name for name in passed if name.startswith('TestSemanticTransitionDeterministicMatrix/') and name.count('/')==1]
 real_subcases={1:[],2:['python','rust'],3:['python_death','python_caller_deadline','rust_death','rust_caller_deadline'],4:['python','rust']}
 expected_real=[test_name]+[test_name+'/'+name for name in real_subcases[real_case]] if real else []
 if failed or skipped or (not real and (len(matrix)!=10 or len(passed)!=32)) or (real and sorted(passed)!=sorted(expected_real)):raise RuntimeError('incomplete selected transition case/matrix')
 report=dict(schema='cairn.semantic-transition-fixture/1',kind='deterministic-protocol-workers-real-go-and-postgresql',matrix_cases=matrix,passed=passed,failed=failed,skipped=skipped,real_worker_acceptance='not executed by deterministic run; full S05/R01/fleet acceptance remains open',binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),api_sha256=hashlib.sha256(cli.read_bytes()).hexdigest())
 if real:
  report.update(kind='local-real-worker-transition-case'+str(real_case),matrix_cases={1:['full_forward_reverse_rebuild'],2:['mid_rebuild_restart/python','mid_rebuild_restart/rust'],3:real_subcases[3],4:['edit_during_embedding/python','edit_during_embedding/rust']}[real_case],real_worker_acceptance='local case'+str(real_case)+' evidence only; full S05/R01/fleet acceptance remains open')
 (artifacts/'report.json').write_text(json.dumps(report,indent=2)+'\n');code=0
except BaseException:
 (artifacts/'failure.txt').write_text(traceback.format_exc())
finally:
 if started:
  try:run([str(pg/'pg_ctl'),'-D',str(root/'data'),'-m','immediate','-w','stop'],'postgres-stop.log',timeout=20)
  except BaseException:
   (artifacts/'cleanup-failure.txt').write_text(traceback.format_exc());code=1
 (artifacts/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
 (artifacts/'source-inputs.json').write_text(json.dumps(source_inputs,sort_keys=True)+'\n')
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:archive.add(artifacts,arcname='artifacts')
sys.exit(code)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', required=True, type=Path)
    cases = parser.add_mutually_exclusive_group()
    cases.add_argument('--real-case1', action='store_true', help='explicit local retained Python/Rust/Python case 1')
    cases.add_argument('--real-case2', action='store_true', help='explicit local Python and Rust mid-rebuild restarts')
    cases.add_argument('--real-case3', action='store_true', help='explicit local Python and Rust death/caller-deadline cases')
    cases.add_argument('--real-case4', action='store_true', help='explicit local Python and Rust edit-during-embedding cases')
    parser.add_argument('--python-selection', type=Path, help='qualified-selection directory containing stage and candidate.json')
    parser.add_argument('--rust-fixtures', type=Path, help='retained Rust fixtures containing stage and stage-result.json')
    args = parser.parse_args()
    real_case = 1 if args.real_case1 else 2 if args.real_case2 else 3 if args.real_case3 else 4 if args.real_case4 else 0
    if real_case and (args.python_selection is None or args.rust_fixtures is None):
        parser.error('real cases require both exact retained selections')
    output = args.artifact_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    wrapper = ROOT / 'scripts/test_hybrid_runtime_operations.py'
    wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    if real_case and wrapper_sha != '937cd3e2cd3f379d4b7e818e012747c5c2914304bc47f9e1b54b860858cc8578':
        raise SystemExit('common wrapper differs from the accepted execution pin')
    inputs = {'common_wrapper_sha256': wrapper_sha,
              'source_files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in ROOT.rglob('*') if p.is_file() and p.suffix in ('.go', '.sql', '.mod', '.sum', '.py')}}
    if real_case:
        for name, path in (('python_candidate', args.python_selection / 'candidate.json'),
                           ('rust_stage_result', args.rust_fixtures / 'stage-result.json')):
            inputs[name] = {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if inputs['python_candidate']['sha256'] != 'ffdc4358a30547f4ddb63628b939893192f0a549e97c3ece31e55f7abe269f46':
            raise SystemExit('retained Python candidate differs from the accepted selection')
    (output / 'execution-inputs.json').write_text(json.dumps(inputs, sort_keys=True, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location(
        'isolation', ROOT / 'scripts/test_hybrid_runtime_operations.py')
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
    binds = []
    if real_case:
        for name in ('empty-locale', 'empty-gconv'):
            (output / name).mkdir(mode=0o700)
        binds = [str(args.python_selection / 'stage') + ':/opt/s04r-selection',
                 str(args.python_selection / 'candidate.json') + ':/opt/s05-python-candidate.json',
                 str(args.rust_fixtures / 'stage') + ':/opt/s05-rust-stage',
                 str(args.rust_fixtures / 'stage-result.json') + ':/opt/s05-rust-result.json',
                 str(output / 'empty-locale') + ':/usr/lib/locale',
                 str(output / 'empty-gconv') + ':/usr/lib/x86_64-linux-gnu/gconv']
    sandbox = isolation.build_sandbox_spec(ROOT, ro_binds=binds)
    read_fd, write_fd = os.pipe()
    received = []

    def read_report():
        with os.fdopen(read_fd, 'rb') as stream:
            received.append(stream.read(1048576))

    inner = INNER.replace('__REAL_CASE1__', repr(bool(real_case))).replace('__REAL_CASE__', str(real_case))
    argv = isolation.namespace_argv(ROOT, ['/usr/bin/python3', '-I', '-B', '-c', inner],
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
            code = child.wait(timeout=1200 if real_case else 600)
        except subprocess.TimeoutExpired:
            if child.pid <= 1:
                raise RuntimeError('refusing process-group control for PID <= 1')
            if real_case >= 2:
                child.terminate()
            else:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if real_case >= 2:
                    child.kill()
                else:
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
    print(('S05 local real case'+str(real_case)+' passed; full transition qualification remains open. ' if real_case else
           'S05 deterministic transition fixtures passed; full real-worker qualification remains open. ') + str(output))


if __name__ == '__main__':
    main()
