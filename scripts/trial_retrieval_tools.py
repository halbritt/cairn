#!/usr/bin/env python3
"""Opt-in source-answering experiment; requires an owned disposable test DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import time
import uuid

from trial_openrouter import configured_key, relay

PROJECT = Path(__file__).resolve().parents[1]
SCENARIO = PROJECT / 'trials/retrieval-tools/scenario.json'

MEMORY_TOOL = '''#!/usr/bin/python3
import fcntl,json,subprocess,sys,uuid
with open('/memory-calls/calls.jsonl','a+') as log:
 fcntl.flock(log,fcntl.LOCK_EX)
 log.seek(0)
 if len(log.readlines()) >= 8:
  raise SystemExit('Memory call limit reached')
 operation=sys.argv[1] if len(sys.argv)>1 else ''
 if operation=='search' and len(sys.argv)>2:
  payload=dict(request_id=str(uuid.uuid4()),scope=dict(repo='trial:storage-answering',task_id='storage',run_id='tool'),query=' '.join(sys.argv[2:]),purpose='context',available_tokens=64000)
  command='index'
 elif operation=='pull' and len(sys.argv)==4:
  payload=dict(request_id=str(uuid.uuid4()),receipt_id=sys.argv[2],handle=sys.argv[3])
  command='expand'
 else:
  raise SystemExit('Usage: memory search QUERY | memory pull RECEIPT HANDLE')
 result=subprocess.run(['/opt/cairn','agent','--socket','/opt/api.sock','--token-file','/opt/agent.token',command],input=json.dumps(payload),text=True,capture_output=True,timeout=15)
 response=json.loads(result.stdout)
 log.write(json.dumps(dict(operation=operation,request=payload,response=response,exit_code=result.returncode))+'\\n')
 log.flush()
 print(result.stdout,end='')
 raise SystemExit(result.returncode)
'''


def private_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        out.write(text)


def cli(binary, env, *args, payload=None):
    p = subprocess.run([str(binary), *args], input=None if payload is None else json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=15, check=True)
    return json.loads(p.stdout)['data']


def sandbox(opencode, cairn, work, home, config, store, calls, helper, tool):
    command = ['bwrap', '--tmpfs', '/', '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin',
               '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64', '--ro-bind', '/etc', '/etc',
               '--dir', '/tmp', '--dir', '/run', '--unshare-pid', '--proc', '/proc', '--dev', '/dev',
               '--die-with-parent', '--bind', str(work), '/work', '--bind', str(home), '/trial-home',
               '--ro-bind', str(opencode), '/opt/opencode', '--ro-bind', str(config), '/opt/opencode.json']
    if tool:
        command += ['--ro-bind', str(cairn), '/opt/cairn', '--ro-bind', str(helper), '/opt/memory',
                    '--ro-bind', str(store / 'api.sock'), '/opt/api.sock',
                    '--ro-bind', str(store / 'agent.token'), '/opt/agent.token',
                    '--bind', str(calls), '/memory-calls']
    return command + ['--chdir', '/work', '--']


def final_answer(stdout):
    texts = []
    for line in stdout.splitlines():
        event = json.loads(line)
        if event.get('type') == 'text':
            texts.append(event['part']['text'])
    if not texts:
        return None
    text = texts[-1].strip()
    if text.startswith('```json\n') and text.endswith('```'):
        text = text[8:-3].strip()
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return None
    return answer if isinstance(answer, dict) else None


def run(root, binary, opencode, preflight_only):
    scenario = json.loads(SCENARIO.read_text())
    workload_path = PROJECT / scenario['workload']
    workload_bytes = workload_path.read_bytes()
    if hashlib.sha256(workload_bytes).hexdigest() != scenario['workload_sha256']:
        raise ValueError('Frozen workload changed')
    workload = json.loads(workload_bytes)
    dsn = os.environ['CAIRN_TEST_DATABASE_URL']
    root.mkdir(mode=0o700)  # Never resume or overwrite another experiment.
    store = root / 'store'
    store.mkdir(mode=0o700)
    env = dict(os.environ, CAIRN_HOME=str(store), CAIRN_DATABASE_URL=dsn)
    subprocess.run([str(binary), 'migrate'], env=env, check=True, capture_output=True, timeout=15)
    records = []
    for note in workload['notes']:
        record = cli(binary, env, 'remember', '--repo', 'trial:storage-answering', '--shareable', note['body'])
        records.append(dict(note_id=note['id'], record_id=record['record_id'], version=record['version'], body=note['body']))
    storage_id = next(r['record_id'] for r in records if r['note_id'] == 'storage')
    token = secrets.token_urlsafe(32)
    private_file(store / 'agent.token', token)
    private_file(store / 'identities.json', json.dumps([dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                 principal='agent:storage-answering', repo='trial:storage-answering', role='agent', destination='hosted')]))
    helper = root / 'memory'
    private_file(helper, MEMORY_TOOL)
    helper.chmod(0o700)
    report = dict(schema='cairn.tool-retrieval-trial/1', scenario_sha256=hashlib.sha256(SCENARIO.read_bytes()).hexdigest(),
                  controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  cairn_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  opencode_sha256=hashlib.sha256(opencode.read_bytes()).hexdigest(), records=records, arms=[])
    with (root / 'api.log').open('wb') as log:
        api = subprocess.Popen([str(binary), 'serve'], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not (store / 'api.sock').exists():
                if api.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Disposable API did not start; inspect api.log')
                time.sleep(.02)
            # Exercise the actual sandboxed client before any model request.
            preflight = root / 'preflight'
            preflight.mkdir()
            for name in ['work', 'home', 'calls']:
                (preflight / name).mkdir()
            private_file(preflight / 'config.json', '{}')
            child_env = dict(PATH='/usr/bin:/bin', HOME='/trial-home', CAIRN_DATABASE_URL='host=/absent dbname=denied',
                             XDG_CONFIG_HOME='/trial-home/.config', XDG_DATA_HOME='/trial-home/.local/share',
                             XDG_CACHE_HOME='/trial-home/.cache', XDG_STATE_HOME='/trial-home/.local/state',
                             OPENCODE_CONFIG='/opt/opencode.json', OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
            base = sandbox(opencode, binary, preflight / 'work', preflight / 'home', preflight / 'config.json',
                           store, preflight / 'calls', helper, True)
            def memory(*args):
                result = subprocess.run(base + ['/opt/memory', *args], env=child_env, capture_output=True, text=True, check=True, timeout=20)
                return json.loads(result.stdout)['data']
            empty = memory('search', 'storage')
            assert empty['package']['semantic']['mode'] == 'index'
            assert not empty['package']['semantic'].get('index')  # Empty index is omitted on the wire.
            found = memory('search', 'database')
            handle = next(h for h in found['handles'] if h['record_id'] == storage_id)
            pulled = memory('pull', found['package']['receipt_id'], handle['handle'])
            assert pulled['selection']['record']['record_id'] == storage_id
            report['preflight'] = dict(empty_initial_query=True, refined_query_has_storage=True, pulled_storage=True, client_dsn_unusable=True)
            private_file(root / 'preflight.json', json.dumps(report['preflight'], indent=2) + '\n')
            if preflight_only:
                return
            key = configured_key(scenario['model'])
            for arm in scenario['arms']:
                arm_root = root / arm
                arm_root.mkdir()
                for name in ['work', 'home', 'calls']:
                    (arm_root / name).mkdir()
                with relay(key, arm_root / 'relay.json', model=scenario['model'], max_requests=10,
                           max_output_tokens=8192, response_seconds=90) as route:
                    tool = arm == 'tool_retrieval'
                    config = dict(autoupdate=False, share='disabled', enabled_providers=[route['provider']],
                                  provider={route['provider']: dict(npm='@ai-sdk/openai-compatible', name=route['binding'],
                                  options=dict(baseURL=route['endpoint'], apiKey=route['api_key']),
                                  models={route['model']: dict(name=route['model'], limit=dict(context=131072, output=8192))})},
                                  agent=dict(build=dict(temperature=0, steps=10)),
                                  permission={'*': 'deny', 'bash': {'*': 'deny', '/opt/memory *': 'allow'} if tool else 'deny'})
                    config_path = arm_root / 'opencode.json'
                    private_file(config_path, json.dumps(config))
                    prompt = scenario['task']
                    if arm == 'direct_context':
                        prompt += '\n\nSupplied source notes:\n' + json.dumps(records)
                    elif tool:
                        prompt += '\n\n' + scenario['tool_instruction']
                    command = sandbox(opencode, binary, arm_root / 'work', arm_root / 'home', config_path,
                                      store, arm_root / 'calls', helper, tool)
                    command += ['/opt/opencode', 'run', '--pure', '--format', 'json', '-m', route['provider'] + '/' + route['model'], prompt]
                    start = time.monotonic()
                    process = subprocess.Popen(command, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                    timed_out = False
                    try:
                        stdout, stderr = process.communicate(timeout=240)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        os.killpg(process.pid, signal.SIGKILL)
                        stdout, stderr = process.communicate()
                    duration = time.monotonic() - start
                    private_file(arm_root / 'stdout.jsonl', stdout.decode())
                    private_file(arm_root / 'stderr.txt', stderr.decode())
                    answer = final_answer(stdout)
                    result = dict(arm=arm, process_exit=process.returncode, timed_out=timed_out,
                                  duration_seconds=duration, answer=answer, stdout_sha256=hashlib.sha256(stdout).hexdigest())
                    report['arms'].append(result)
                    (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
                    print(json.dumps(dict(arm=arm, process_exit=process.returncode, duration_seconds=round(duration, 3), parsed_answer=answer is not None)), flush=True)
                config_path.unlink()
                shutil.rmtree(arm_root / 'home')
            calls = root / 'tool_retrieval/calls/calls.jsonl'
            report['tool_calls'] = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        finally:
            api.terminate()
            api.wait(timeout=15)
            (store / 'agent.token').unlink()
            (store / 'identities.json').unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cairn', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    run(args.output.absolute(), args.cairn.resolve(strict=True), args.opencode.resolve(strict=True), args.preflight_only)
