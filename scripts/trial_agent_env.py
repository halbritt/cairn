#!/usr/bin/env python3
"""Opt-in matched repair experiment; run through trial-agent-env.sh.

No model output or candidate code is admitted to the operational store. The
reviewed lesson and acceptance gate are frozen by trials/agent-env/scenario.json.
"""
import argparse
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import tarfile
import time
import uuid

from trial_agent_search import tool_observations
from trial_host import TrialHost
from trial_openrouter import configured_key, relay
from trial_repair_assessment import repair_assessment
from trial_retrieval_tools import cli, private_file, sandbox

PROJECT = Path(__file__).resolve().parents[1]
SCENARIO = PROJECT / 'trials/agent-env/scenario.json'
GATE = PROJECT / 'trials/agent-env/agent_connection_gate_test.go.txt'
GATE_NAME = 'cmd/cairn/agent_connection_gate_test.go'
AGENT = ['/opt/cairn', 'agent', '--socket', '/opt/api.sock', '--token-file', '/opt/agent.token']


def sha(body):
    return hashlib.sha256(body).hexdigest()


def emit(**data):
    print(json.dumps(data), flush=True)


def snapshot(scenario, work):
    work.mkdir(mode=0o700)
    body = subprocess.check_output(['git', '-C', str(PROJECT), 'archive', scenario['base'], *scenario['snapshot_paths']])
    with tarfile.open(fileobj=io.BytesIO(body)) as stream:
        stream.extractall(work, filter='data')
    return {str(p.relative_to(work)): p.read_bytes() for p in work.rglob('*') if p.is_file()}


def candidate(work, originals):
    files = {str(p.relative_to(work)): p for p in work.rglob('*') if p.is_file() or p.is_symlink()}
    changes, outside, patches = {}, [], []
    for name in sorted(set(files) | set(originals)):
        path = files.get(name)
        if path is not None and path.is_symlink():
            outside.append(name)
            continue
        before, after = originals.get(name), path.read_bytes() if path else None
        if before == after:
            continue
        allowed = name == 'cmd/cairn/agent.go' or (before is None and name.endswith('_test.go') and str(Path(name).parent) == 'cmd/cairn' and name != GATE_NAME)
        if not allowed or after is None:
            outside.append(name)
            continue
        changes[name] = after
        for line in difflib.unified_diff((before or b'').decode().splitlines(True), after.decode().splitlines(True),
                                         fromfile='a/' + name if before is not None else '/dev/null', tofile='b/' + name):
            patches.append(line)
            if not line.endswith('\n'):
                patches.append('\n\\ No newline at end of file\n')
    return changes, outside, ''.join(patches)


def command_base(opencode, binary, store, work, home, config, cache, with_api):
    goroot = subprocess.check_output(['go', 'env', 'GOROOT'], text=True).strip()
    gomod = subprocess.check_output(['go', 'env', 'GOMODCACHE'], text=True).strip()
    base = sandbox(opencode, binary, work, home, config, store, None, None, with_api)[:-1]
    base += ['--ro-bind', goroot, '/opt/go', '--ro-bind', gomod, '/opt/gomod', '--bind', str(cache), '/trial-cache', '--clearenv']
    environment = dict(PATH='/opt/go/bin:/usr/bin:/bin', HOME='/trial-home', GOROOT='/opt/go', GOPATH='/trial-home/go',
                       GOMODCACHE='/opt/gomod', GOCACHE='/trial-cache', GOTOOLCHAIN='local', GOPROXY='off',
                       XDG_CONFIG_HOME='/trial-home/.config', XDG_DATA_HOME='/trial-home/.local/share',
                       XDG_CACHE_HOME='/trial-home/.cache', XDG_STATE_HOME='/trial-home/.local/state',
                       OPENCODE_CONFIG='/opt/opencode.json', OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
                       CAIRN_DATABASE_URL='host=/absent-agent-env-trial dbname=denied')
    for name, value in environment.items():
        base += ['--setenv', name, value]
    return base + ['--']


def evaluate(scenario, arm_root, binary, opencode, store, config, changes, outside):
    if outside or not changes:
        return dict(passed=False, reason='write_scope_violation' if outside else 'no_candidate')
    work = arm_root / 'evaluation'
    snapshot(scenario, work)
    for name, body in changes.items():
        (work / name).write_bytes(body)
    (work / GATE_NAME).write_bytes(GATE.read_bytes())
    home, cache = arm_root / 'evaluation-home', arm_root / 'evaluation-cache'
    home.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    base = command_base(opencode, binary, store, work, home, config, cache, False)
    result = subprocess.run(base + ['go', 'test', '-json', '-count=1', '-timeout=90s', './...'], capture_output=True, timeout=150)
    private_file(arm_root / 'gate.jsonl', result.stdout.decode())
    private_file(arm_root / 'gate.stderr', result.stderr.decode())
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
    gate_actions = [e['Action'] for e in events if e.get('Test') == 'TestAgentConnectionDefaultsTransferGate' and e['Action'] in ('pass', 'fail', 'skip')]
    fmt = subprocess.run(base + ['gofmt', '-l', *changes], capture_output=True, timeout=20)
    return dict(passed=result.returncode == 0 and gate_actions == ['pass'] and fmt.returncode == 0 and not fmt.stdout,
                exit_code=result.returncode, gate_actions=gate_actions, formatted=fmt.returncode == 0 and not fmt.stdout,
                skipped_tests=sum(e['Action'] == 'skip' and 'Test' in e for e in events),
                stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr),
                boundary='All Go tests with no test DSN; database-dependent tests skip. Hidden gate uses real Unix HTTP transport.')


def run_arm(scenario, root, binary, opencode, arm, preflight):
    arm_root = root / arm
    arm_root.mkdir(mode=0o700)
    work, home, store, cache = [arm_root / n for n in ('work', 'home', 'store', 'cache')]
    originals = snapshot(scenario, work)
    for path in (home, store, cache):
        path.mkdir(mode=0o700)
    environment = dict(os.environ, CAIRN_HOME=str(store), CAIRN_DATABASE_URL=os.environ['CAIRN_TEST_DATABASE_URL'])
    subprocess.run([str(binary), 'migrate'], env=environment, capture_output=True, check=True, timeout=20)
    scope = dict(repo='trial:agent-env:' + arm, task_id='agent-connection-defaults', run_id=arm)
    lesson = scenario['lesson']
    record = None
    if arm == 'retrieval':
        record = cli(binary, environment, 'remember', '--repo', scope['repo'], '--kind', 'lesson', '--shareable', lesson['body'])
    identities = []
    for role in ('agent', 'observer'):
        token = secrets.token_urlsafe(32)
        private_file(store / (role + '.token'), token)
        identities.append(dict(token_sha256=sha(token.encode()), principal=role + ':agent-env:' + arm,
                               repo=scope['repo'], role=role, destination='hosted'))
    private_file(store / 'identities.json', json.dumps(identities))
    config = arm_root / 'opencode.json'
    private_file(config, '{}')
    base = command_base(opencode, binary, store, work, home, config, cache, True)
    host = None
    with (arm_root / 'api.log').open('wb') as log:
        api = subprocess.Popen([str(binary), 'serve'], env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not (store / 'api.sock').exists():
                if api.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Disposable API did not start')
                time.sleep(.02)
            search = AGENT + ['search', '--repo', scope['repo'], '--task', scope['task_id'], '--run', scope['run_id']]
            # Identical Go/tool preflight in every arm, before any model request.
            probe = subprocess.run(base + ['go', 'test', '-count=1', './cmd/cairn'], capture_output=True, timeout=120)
            private_file(arm_root / 'go-preflight.txt', (probe.stdout + probe.stderr).decode())
            if probe.returncode:
                raise RuntimeError('Go sandbox preflight failed')
            found = subprocess.run(base + search + ['environment overrides'], capture_output=True, check=True, timeout=20)
            view = json.loads(found.stdout)['data']
            entries = view['index']
            if record:
                entry = next(e for e in entries if e['record_id'] == record['record_id'])
                pulled = subprocess.run(base + shlex.split(entry['pull_command']), capture_output=True, check=True, timeout=20)
                if json.loads(pulled.stdout)['data']['selection']['record']['body'] != lesson['body']:
                    raise RuntimeError('Preflight lesson bytes changed')
            elif entries:
                raise RuntimeError('Control has unexpected memory')
            emit(event='preflight_passed', arm=arm, source_records=len(entries))
            if preflight:
                return dict(arm=arm, preflight=True)
            limits = scenario['limits']
            with relay(configured_key(scenario['model']), arm_root / 'relay.json', model=scenario['model'],
                       max_requests=limits['requests'], max_output_tokens=limits['output_tokens'], response_seconds=90) as route:
                config.write_text(json.dumps(dict(autoupdate=False, share='disabled', enabled_providers=[route['provider']],
                    provider={route['provider']: dict(npm='@ai-sdk/openai-compatible', name=route['binding'],
                        options=dict(baseURL=route['endpoint'], apiKey=route['api_key']),
                        models={route['model']: dict(name=route['model'], limit=dict(context=131072, output=8192))})},
                    agent=dict(build=dict(temperature=0, steps=16)),
                    permission={'*': 'deny', 'bash': 'allow', 'read': 'allow', 'edit': 'allow', 'write': 'allow', 'glob': 'allow', 'grep': 'allow'})))
                prompt = scenario['task']
                if arm == 'direct_context':
                    prompt += '\n\nPreviously reviewed experience (relevance to this task is uncertain):\n' + lesson['body']
                elif arm == 'retrieval':
                    prompt += '\n\nBefore editing, search previously reviewed experience with bash: ' + shlex.join(search) + ' QUERY. Use the complete pull_command in a result to inspect its body. Its relevance to this task is uncertain; use your judgment.'
                private_file(arm_root / 'prompt.txt', prompt)
                host = TrialHost(arm_root / 'host', [binary, 'agent', '--socket', store / 'api.sock', '--token-file', store / 'observer.token'], environment, scope)
                command = base + ['/opt/opencode', 'run', '--pure', '--format', 'json', '-m', route['provider'] + '/' + route['model'], prompt]
                emit(event='model_started', arm=arm)
                started = time.monotonic()
                process = host.run(['--destination', 'hosted', '--query', 'bootstrapmarkerwithoutmatches', '--prompt', 'Repair the provided source.',
                                    '--task-class', 'go-cli-repair', '--binding', route['binding'], '--capability', scenario['model'],
                                    '--revision', scenario['base'], '--timeout', str(limits['process_seconds']) + 's', '--', *command],
                                   timeout=limits['process_seconds'] + 25)
                elapsed = time.monotonic() - started
                private_file(arm_root / 'stdout.jsonl', process.stdout.decode())
                private_file(arm_root / 'stderr.txt', process.stderr.decode())
                envelopes = [json.loads(line) for line in process.stderr.splitlines() if line.startswith(b'{"schema":"cairn.response/1"')]
                outcome = envelopes[-1]['data']
                changes, outside, patch = candidate(work, originals)
                private_file(arm_root / 'candidate.patch', patch)
                host.finish('sha256:' + sha(patch.encode()) if patch else '')
                # General coding bash output is not a failed Cairn tool call.
                events = [json.loads(line) for line in process.stdout.splitlines()]
                native = [e for e in events if e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'bash'
                          and e['part'].get('state', {}).get('input', {}).get('command', '').startswith('/opt/cairn agent ')]
                searches, pulls, failures = tool_observations(b'\n'.join(json.dumps(e).encode() for e in native))
                links = [host.call('link-run-retrieval', dict(request_id=str(uuid.uuid4()), run_receipt_id=outcome['receipt_id'],
                         retrieval_receipt_id=receipt, expected_reader='agent:agent-env:' + arm, method='opencode-native-cli-tool-result/1')) for receipt in searches]
                gate = evaluate(scenario, arm_root, binary, opencode, store, config, changes, outside)
                relay_report = json.loads((arm_root / 'relay.json').read_text())
                assessment_fields = repair_assessment(process.returncode, gate, events, relay_report)
                accepted = assessment_fields['task_outcome'] == 'accepted'
                evidence = host.call('evidence', dict(request_id=str(uuid.uuid4()), repo=scope['repo'], sensitivity='local',
                    source='Frozen agent connection repair gate and preserved existing tests',
                    body=json.dumps(dict(gate=gate, outside_scope=outside, patch_sha256=sha(patch.encode()),
                                         process_exit=process.returncode, scenario_sha256=sha(SCENARIO.read_bytes()),
                                         assessment_fields=assessment_fields, stdout_sha256=sha(process.stdout),
                                         relay_report_sha256=sha((arm_root / 'relay.json').read_bytes()),
                                         requests=len(relay_report['requests']), request_limit=relay_report['limits']['requests'],
                                         relay_rejection_codes=relay_report['rejection_codes'],
                                         api_error_statuses=[e['error'].get('data', {}).get('statusCode') for e in events
                                             if e.get('type') == 'error' and e.get('error', {}).get('name') == 'APIError']))))
                assessment = host.call('assess-run', dict(request_id=str(uuid.uuid4()), receipt_id=outcome['receipt_id'], expected_version=0,
                    method='agent-connection-artifact-gate/2', evidence_ids=[evidence['evidence_id']], **assessment_fields))
                report = dict(arm=arm, duration_seconds=elapsed, process_exit=process.returncode, gate=gate,
                    changed_paths=sorted(changes), outside_scope=outside, patch_sha256=sha(patch.encode()),
                    record=record, outcome=outcome, assessment=assessment, searches=len(searches), pulls=len(pulls), tool_failures=failures,
                    exact_lesson_pulled=any(p['selection']['record']['body'] == lesson['body'] for p in pulls.values()),
                    links=links, use_report=cli(binary, environment, 'use-report', scope['repo']),
                    run_report=cli(binary, environment, 'run-report', scope['repo']))
                private_file(arm_root / 'report.json', json.dumps(report, indent=2))
                emit(event='arm_finished', arm=arm, accepted=accepted, searches=len(searches), pulls=len(pulls), seconds=round(elapsed, 3))
                return report
        finally:
            if host is not None:
                host.retain_terminal()
            api.terminate()
            api.wait(timeout=15)
            for name in ('agent.token', 'observer.token', 'identities.json'):
                (store / name).unlink()
            config.unlink()
            for name in ('home', 'cache', 'evaluation-home', 'evaluation-cache'):
                path = arm_root / name
                if path.exists():
                    shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cairn', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    scenario = json.loads(SCENARIO.read_text())
    for path, expected in [(GATE, scenario['gate_sha256']), (args.cairn, scenario['cairn_sha256']), (args.opencode, scenario['opencode_sha256'])]:
        if sha(path.read_bytes()) != expected:
            raise ValueError('Frozen input changed: ' + str(path))
    if sha(scenario['lesson']['body'].encode()) != scenario['lesson']['body_sha256']:
        raise ValueError('Lesson bytes changed')
    args.output.mkdir(mode=0o700)
    report = dict(schema='cairn.agent-env-result/1', scenario_sha256=sha(SCENARIO.read_bytes()),
                  controller_sha256=sha(Path(__file__).read_bytes()), arms=[])
    for arm in scenario['arms']:
        report['arms'].append(run_arm(scenario, args.output, args.cairn.resolve(), args.opencode.resolve(), arm, args.preflight_only))
        (args.output / 'report.json').write_text(json.dumps(report, indent=2))
    pg_bin = subprocess.check_output(['pg_config', '--bindir'], text=True).strip()
    subprocess.run([str(Path(pg_bin) / 'pg_dump'), '-Fc', '-f', str(args.output / 'trial.dump'), os.environ['CAIRN_TEST_DATABASE_URL']], check=True, timeout=30)


if __name__ == '__main__':
    main()
