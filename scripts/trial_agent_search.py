#!/usr/bin/env python3
"""Opt-in native agent CLI trial using an owned disposable database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import time
import uuid

from trial_host import TrialHost
from trial_openrouter import configured_key, relay
from trial_retrieval_tools import cli, final_answer, private_file, sandbox

PROJECT = Path(__file__).resolve().parents[1]
SCENARIO = PROJECT / 'trials/agent-search/scenario.json'


def tool_observations(stdout):
    searches, pulls, failures, seen = {}, {}, [], set()
    for line in stdout.splitlines():
        event = json.loads(line)
        part = event.get('part', {})
        if event.get('type') != 'tool_use' or part.get('tool') != 'bash' or part.get('callID') in seen:
            continue
        state = part['state']
        if state['status'] in ('pending', 'running'):
            continue
        seen.add(part['callID'])
        if state['status'] != 'completed':
            failures.append(dict(call_id=part['callID'], status=state['status']))
            continue
        try:
            response = json.loads(state['output'])
            words = shlex.split(state['input']['command'])
        except (ValueError, KeyError):
            failures.append(dict(call_id=part['callID'], status='unparsed-tool-response'))
            continue
        if not isinstance(response, dict) or response.get('schema') != 'cairn.response/1':
            failures.append(dict(call_id=part['callID'], status='unrecognized-tool-response'))
            continue
        if not response.get('ok'):
            failures.append(dict(call_id=part['callID'], status=response.get('status', 'unknown')))
            continue
        data = response['data']
        prefix = ['/opt/cairn', 'agent', '--socket', '/opt/api.sock', '--token-file', '/opt/agent.token']
        if not isinstance(data, dict) or words[:6] != prefix or len(words) < 7:
            failures.append(dict(call_id=part['callID'], status='unrecognized-tool-command'))
            continue
        if words[6] == 'search' and data.get('schema') == 'cairn.agent-search/1':
            searches[data['receipt_id']] = data
        elif words[6] == 'pull' and 'selection' in data:
            args = words[7:]
            if args[:1] == ['--request-id']:
                args = args[2:]
            if len(args) != 2:
                failures.append(dict(call_id=part['callID'], status='unparsed-pull-command'))
                continue
            receipt, _ = args
            record = data['selection']['record']
            pulls[(receipt, record['record_id'], record['version'])] = data
    return searches, pulls, failures


def answer_checks(answer, expected, cited_sources, required_source):
    checks = {key: isinstance(answer, dict) and type(answer.get(key)) is type(value) and answer[key] == value
              for key, value in expected.items()}
    sources = answer.get('sources') if isinstance(answer, dict) else None
    valid_sources = isinstance(sources, list) and bool(sources) and all(isinstance(s, str) for s in sources)
    checks['sources_actually_pulled'] = valid_sources and set(sources) <= set(cited_sources)
    checks['outcome_source_pulled_and_cited'] = required_source in cited_sources
    return checks


def run(root, binary, opencode, preflight_only):
    scenario = json.loads(SCENARIO.read_text())
    sources = []
    for source in scenario['sources']:
        body = (PROJECT / source['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != source['sha256']:
            raise ValueError('Frozen source changed: ' + source['path'])
        sources.append(dict(source, body=body.decode()))
    root.mkdir(mode=0o700)
    for name in ['store', 'work', 'home']:
        (root / name).mkdir(mode=0o700)
    store = root / 'store'
    env = dict(os.environ, CAIRN_HOME=str(store), CAIRN_DATABASE_URL=os.environ['CAIRN_TEST_DATABASE_URL'])
    subprocess.run([str(binary), 'migrate'], env=env, capture_output=True, check=True, timeout=15)
    scope = scenario['scope']
    records = []
    for source in sources:
        record = cli(binary, env, 'remember', '--repo', scope['repo'], '--shareable', source['body'])
        records.append(dict(path=source['path'], sha256=source['sha256'], record_id=record['record_id'], version=record['version']))
    identities = []
    for role, principal in [('agent', 'agent:native-search'), ('observer', 'host:native-search')]:
        token = secrets.token_urlsafe(32)
        private_file(store / (role + '.token'), token)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(), principal=principal,
                               repo=scope['repo'], role=role, destination='hosted'))
    private_file(store / 'identities.json', json.dumps(identities))
    config_path = root / 'opencode.json'
    private_file(config_path, '{}')
    child_env = dict(PATH='/usr/bin:/bin', HOME='/trial-home', XDG_CONFIG_HOME='/trial-home/.config',
                     XDG_DATA_HOME='/trial-home/.local/share', XDG_CACHE_HOME='/trial-home/.cache',
                     XDG_STATE_HOME='/trial-home/.local/state', OPENCODE_CONFIG='/opt/opencode.json',
                     OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
    base = sandbox(opencode, binary, root / 'work', root / 'home', config_path, store, None, None, True)[:-1]
    base += ['--clearenv']
    for name, value in child_env.items():
        base += ['--setenv', name, value]
    base += ['--']
    agent = ['/opt/cairn', 'agent', '--socket', '/opt/api.sock', '--token-file', '/opt/agent.token']
    observer = [str(binary), 'agent', '--socket', str(store / 'api.sock'), '--token-file', str(store / 'observer.token')]
    host = None
    with (root / 'api.log').open('wb') as log:
        api = subprocess.Popen([str(binary), 'serve'], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not (store / 'api.sock').exists():
                if api.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Disposable API did not start')
                time.sleep(.02)
            # Same mounted CLI and generated command, without model spend.
            command = base + agent + ['search', '--repo', scope['repo'], '--task', scope['task_id'], '--run', 'preflight', 'host retrieval outcome']
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)
            view = json.loads(result.stdout)['data']
            entry = next(e for e in view['index'] if e['record_id'] == records[1]['record_id'])
            pulled = subprocess.run(base + shlex.split(entry['pull_command']), capture_output=True, text=True, check=True, timeout=20)
            assert json.loads(pulled.stdout)['data']['selection']['record']['record_id'] == records[1]['record_id']
            private_file(root / 'preflight.json', json.dumps(dict(native_search=True, generated_pull=True,
                         ordinary_agent_profile=True, observer_token_not_mounted=True, child_environment_cleared=True)))
            if preflight_only:
                return
            limits = scenario['limits']
            with relay(configured_key(scenario['model']), root / 'relay.json', model=scenario['model'],
                       max_requests=limits['requests'], max_output_tokens=limits['output_tokens'], response_seconds=90) as route:
                config = dict(autoupdate=False, share='disabled', enabled_providers=[route['provider']],
                              provider={route['provider']: dict(npm='@ai-sdk/openai-compatible', name=route['binding'],
                              options=dict(baseURL=route['endpoint'], apiKey=route['api_key']),
                              models={route['model']: dict(name=route['model'], limit=dict(context=131072, output=8192))})},
                              agent=dict(build=dict(temperature=0, steps=10)),
                              permission={'*': 'deny', 'bash': {'*': 'deny', '/opt/cairn agent *': 'allow'}})
                config_path.write_text(json.dumps(config))
                search = shlex.join(agent + ['search', '--repo', scope['repo'], '--task', scope['task_id'], '--run', scope['run_id']])
                prompt = scenario['task'] + '\nUse bash with ' + search + ' QUERY. Each result has a complete pull_command; run it to inspect the source. Only search and pull are permitted. Return your final JSON when the sources suffice.'
                command = base + ['/opt/opencode', 'run', '--pure', '--format', 'json', '-m', route['provider'] + '/' + route['model'], prompt]
                host = TrialHost(root / 'host', observer, env, scope)
                start = time.monotonic()
                process = host.run(['--destination', 'hosted', '--query', 'bootstrapmarkerwithoutmatches',
                                    '--prompt', 'Use the declared source tools.', '--task-class', 'source-answering',
                                    '--capability', scenario['model'], '--timeout', str(limits['process_seconds']) + 's', '--', *command],
                                   timeout=limits['process_seconds'] + 20)
                duration = time.monotonic() - start
                private_file(root / 'stdout.jsonl', process.stdout.decode())
                private_file(root / 'stderr.txt', process.stderr.decode())
                envelopes = [json.loads(line) for line in process.stderr.splitlines() if line.startswith(b'{"schema":"cairn.response/1"')]
                outcome = envelopes[-1]['data']
                answer = final_answer(process.stdout)
                host.finish('sha256:' + hashlib.sha256(process.stdout).hexdigest() if answer else '')
                searches, pulls, failures = tool_observations(process.stdout)
                links = []
                for receipt in searches:
                    links.append(host.call('link-run-retrieval', dict(request_id=str(uuid.uuid4()),
                                           run_receipt_id=outcome['receipt_id'], retrieval_receipt_id=receipt,
                                           expected_reader='agent:native-search', method='opencode-native-cli-tool-result/1')))
                cited = answer.get('sources', []) if answer else []
                if not isinstance(cited, list) or not all(isinstance(s, str) for s in cited):
                    cited = []
                observed_citations = []
                for (receipt, record, version) in pulls:
                    if record in cited:
                        cli(binary, env, 'agent', '--token-file', str(store / 'agent.token'), 'usage',
                            payload=dict(request_id=str(uuid.uuid4()), receipt_id=receipt, record_id=record,
                                         version=version, signal='cited', method='opencode-final-source-citation/1'))
                        observed_citations.append(record)
                checks = answer_checks(answer, scenario['expected'], observed_citations, records[1]['record_id'])
                checks['native_search_observed'] = bool(searches)
                checks['process_exited_zero'] = process.returncode == 0
                evidence = host.call('evidence', dict(request_id=str(uuid.uuid4()), repo=scope['repo'],
                                     body=json.dumps(dict(checks=checks, stdout_sha256=hashlib.sha256(process.stdout).hexdigest(),
                                                          scenario_sha256=hashlib.sha256(SCENARIO.read_bytes()).hexdigest())),
                                     source='prospective native CLI source-answering checks', sensitivity='local'))
                assessment = host.call('assess-run', dict(request_id=str(uuid.uuid4()), receipt_id=outcome['receipt_id'],
                                       expected_version=0, task_outcome='accepted' if all(checks.values()) else 'rejected',
                                       failure_domain='none' if all(checks.values()) else 'task', method='native-cli-source-answer/1',
                                       evidence_ids=[evidence['evidence_id']], reason='Prospective source-answer and actual tool-contact checks; no general memory-benefit claim.'))
                uses = cli(binary, env, 'use-report', scope['repo'])
                runs = cli(binary, env, 'run-report', scope['repo'])
                assert len(runs['rows']) == 1 and runs['rows'][0]['linked_retrievals'] == len(searches)
                joined = [row for row in uses['rows'] if row.get('run_receipt_id') == outcome['receipt_id']]
                assert all(row['task_outcome'] == assessment['task_outcome'] for row in joined)
                report = dict(schema='cairn.agent-search-trial-result/1', scenario_sha256=hashlib.sha256(SCENARIO.read_bytes()).hexdigest(),
                              controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              cairn_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), opencode_sha256=hashlib.sha256(opencode.read_bytes()).hexdigest(),
                              records=records, answer=answer, checks=checks, outcome=outcome, assessment=assessment,
                              duration_seconds=duration, searches=len(searches), body_pulls=len(pulls), tool_failures=failures,
                              links=links, joined_exposures=joined, runs=runs['rows'])
                private_file(root / 'report.json', json.dumps(report, indent=2))
                print(json.dumps(dict(task_outcome=assessment['task_outcome'], checks=checks, searches=len(searches),
                                      pulls=len(pulls), tool_failures=len(failures), duration_seconds=round(duration, 3))), flush=True)
        finally:
            if host is not None:
                host.retain_terminal()
            api.terminate()
            api.wait(timeout=15)
            for name in ['agent.token', 'observer.token', 'identities.json']:
                (store / name).unlink()
            config_path.unlink()
            shutil.rmtree(root / 'home')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cairn', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    run(args.output.absolute(), args.cairn.resolve(strict=True), args.opencode.resolve(strict=True), args.preflight_only)
