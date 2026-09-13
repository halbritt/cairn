#!/usr/bin/env python3
"""Run the frozen recurrence through the real native Driver in a private graph.

Default execution uses a model-free runtime to calibrate the whole path. Real
model requests require --execute-model and a prior successful fixture report.
"""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess

from trial_agent_env import candidate, evaluate, sha, snapshot
from trial_native_permissions import probe_permissions
from trial_native_recurrence import PROJECT, SCENARIO, pinned_inputs, workspace_manifest
from trial_openrouter import configured_key, relay
from trial_repair_assessment import repair_assessment
from trial_retrieval_tools import private_file


def executor_pins(striatum, driver_binary):
    paths = ['trial_native_executor.py', 'trial_native_runtime.py', 'trial_native_permissions.py', 'trial_native_recurrence.py',
             'trial_agent_env.py', 'trial_repair_assessment.py', 'trial_openrouter.py', 'trial_retrieval_tools.py']
    go_paths = subprocess.check_output(['go', 'env', 'GOROOT', 'GOMODCACHE'], text=True).splitlines()
    return dict(driver_binary_sha256=sha(driver_binary.read_bytes()), go_paths=go_paths,
        go_binary_sha256=sha((Path(go_paths[0]) / 'bin/go').read_bytes()),
        striatum_head=subprocess.check_output(['git', '-C', str(striatum), 'rev-parse', 'HEAD'], text=True).strip(),
        striatum_diff_sha256=sha(subprocess.check_output(['git', '-C', str(striatum), 'diff', 'HEAD'])),
        cairn_scripts={name: sha((PROJECT / 'scripts' / name).read_bytes()) for name in paths},
        fixture_source_sha256=sha((PROJECT / 'trials/native-recurrence/fixture-runtime.go.txt').read_bytes()))


def runtime_permissions():
    # OpenCode's later wildcard deny overrides its default scratch exception.
    # The native filesystem bridge separately keeps sealed inputs read-only.
    return {'*': 'deny', 'bash': 'allow', 'read': 'allow', 'edit': 'allow',
            'write': 'allow', 'glob': 'allow', 'grep': 'allow',
            'external_directory': {'/tmp/*': 'allow'}}


def require_fixture(fixture, scenario, pins):
    if (fixture['execution_kind'] != 'fixture' or fixture['scenario_sha256'] != sha(SCENARIO.read_bytes())
            or fixture['execution_pins'] != pins or [a['mode'] for a in fixture['arms']] != scenario['arms']):
        raise ValueError('Native fixture does not match this executor and all frozen conditions')
    permission = fixture.get('permission_probe', {})
    if (not permission.get('passed') or permission.get('opencode_sha256') != scenario['opencode_sha256']
            or permission.get('permission') != runtime_permissions()):
        raise ValueError('Native fixture lacks matching real OpenCode permission evidence')
    for arm in fixture['arms']:
        if (not arm['change_set_checked'] or arm['process_exit'] != 0 or arm['invocations'] != 1
                or arm['provider_requests'] != 0 or arm['gate'].get('gate_actions') != ['fail']
                or arm['gate']['passed'] or not arm['exact_prompt_received']):
            raise ValueError('Native fixture lacks completed, independently rejected executor evidence')
    native = fixture['arms'][-1]['host_observation']
    if not native['claim_confirmed'] or not native['invocation_started'] or not native['delivery_observation_id'] or not native['outcome_observation_id']:
        raise ValueError('Native fixture lacks actual Cairn delivery and outcome')


def run_logged(command, log, **kwargs):
    with log.open('wb') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError('Command failed; inspect ' + str(log))


def backend_declaration(runtime, runtime_id, seconds, version_command):
    # Values passed through the declaration reader are simple absolute paths;
    # its supported inline-list format is not a general YAML argument encoder.
    for value in [*runtime, *version_command]:
        if any(c in value for c in ',\n\r"'):
            raise ValueError('Unsupported declaration argument')
    return f'''schema_version: 1
id: native-recurrence
kind: execution-backend
declaration_version: 1
status: accepted
agent_runtimes:
  - id: {runtime_id}
    version_discovery: probe
aliasing:
  aliasing_class: recurrence-{runtime_id}
capabilities:
  effects: [E1]
  supported_pass_types: [build]
constraints:
  internal_retry: {{ max: 0 }}
adapter:
  command: {json.dumps(runtime)}
  version_command: {json.dumps(version_command)}
  prompt_mode: arg
  invocation_limit_s: {seconds}
  dispatch_budget_s: {seconds + 60}
  abort_grace_s: 1
provenance_fields:
  required: [agent_runtime_id, agent_runtime_version, session_nonce, attempts]
'''


def run_arm(args, scenario, root, arm, lesson, driver_binary, runtime_binary):
    root.mkdir(mode=0o700)
    originals = snapshot(scenario, root / 'repo')
    git_env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                   GIT_AUTHOR_NAME='Cairn experiment', GIT_AUTHOR_EMAIL='trial@localhost',
                   GIT_COMMITTER_NAME='Cairn experiment', GIT_COMMITTER_EMAIL='trial@localhost',
                   GIT_AUTHOR_DATE='2026-09-08T00:00:00Z', GIT_COMMITTER_DATE='2026-09-08T00:00:00Z')
    for command in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'Frozen Cairn source']):
        subprocess.run(['git', '-C', str(root / 'repo'), *command], env=git_env, capture_output=True, check=True)
    commit = subprocess.check_output(['git', '-C', str(root / 'repo'), 'rev-parse', 'HEAD'], text=True).strip()
    limits = scenario['limits']
    route_context = relay(configured_key(scenario['model']), root / 'relay.json', model=scenario['model'],
        max_requests=limits['requests'], max_output_tokens=limits['output_tokens'], response_seconds=90) if args.execute_model else nullcontext(None)
    config_path = root / 'opencode.json'
    try:
        with route_context as route:
            config = {}
            if route:
                config = dict(autoupdate=False, share='disabled', enabled_providers=[route['provider']],
                    provider={route['provider']: dict(npm='@ai-sdk/openai-compatible', name=route['binding'],
                        options=dict(baseURL=route['endpoint'], apiKey=route['api_key']),
                        models={route['model']: dict(name=route['model'], limit=dict(context=131072, output=limits['output_tokens']))})},
                    agent=dict(build=dict(temperature=0, steps=limits['requests'])),
                    permission=runtime_permissions())
            private_file(config_path, json.dumps(config))
            private_file(root / 'runtime.json', json.dumps(dict(opencode=str(runtime_binary), cairn=str(args.cairn),
                opencode_config=str(config_path), home=str(root / 'runtime-home'), cache=str(root / 'runtime-cache'),
                observation_file=str(root / 'received-prompt.sha256'),
                go_paths=subprocess.check_output(['go', 'env', 'GOROOT', 'GOMODCACHE'], text=True).splitlines(),
                source_manifest=workspace_manifest(originals), model=route['provider'] + '/' + route['model'] if route else 'fixture/no-model')))
            runtime = ['/usr/bin/python3', str(PROJECT / 'scripts/trial_native_runtime.py'), str(root / 'runtime.json')]
            private_file(root / 'backend.yaml', backend_declaration(runtime, 'opencode' if route else 'fixture', limits['process_seconds'], [str(runtime_binary), '--version']))
            private_file(root / 'spec.json', json.dumps(dict(schema='cairn.native-experiment/1', root=str(root),
                root_commit=commit, cairn_binary=str(args.cairn), mode=arm, task=scenario['task'],
                lesson=lesson if arm != 'no_memory' else '', query='agent connection socket token defaults')))
            print(json.dumps(dict(event='native_arm_started', arm=arm, execution_kind='model' if route else 'fixture')), flush=True)
            run_logged(['systemd-run', '--user', '--scope', '--quiet', '-p', 'Delegate=yes', 'env',
                'STRIATUM_REQUIRE_CGROUP=1', 'STRIATUM_CAIRN_EXPERIMENT=' + str(root / 'spec.json'), str(driver_binary),
                '-test.run=^TestCairnNativeRepairExperiment$', '-test.v', '-test.timeout=15m'], root / 'driver.log',
                cwd=args.striatum / 'internal/driver', timeout=limits['process_seconds'] + 120)
        result = json.loads((root / 'result.json').read_text())
        submission = Path(result['submission_dir']) if result.get('submission_dir') else None
        transcript = (submission / 'exhaust/transcript-0').read_bytes() if submission else b''
        process_exit = 0 if submission and not (submission / 'exhaust/runerr-0').exists() else None
        prompt = (submission / 'exhaust/prompt-0').read_bytes() if submission else b''
        received = root / 'received-prompt.sha256'
        exact_prompt = bool(prompt and received.exists() and received.read_text().strip() == sha(prompt))
        events = []
        for line in transcript.splitlines():
            if line.startswith(b'{'):
                events.append(json.loads(line))
        # Only Driver-admitted output is eligible for repair acceptance. Scratch
        # files and refused submissions remain private diagnostics, never repairs.
        changes, outside, patch = {}, [], ''
        if result['change_set_checked']:
            change = json.loads((root / 'change-set.json').read_text())
            candidate_root = root / 'candidate'
            snapshot(scenario, candidate_root)
            for name, body in change['files'].items():
                path = candidate_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            for name in change['deletes']:
                (candidate_root / name).unlink()
            changes, outside, patch = candidate(candidate_root, originals)
        private_file(root / 'candidate.patch', patch)
        # Evaluation uses a fresh empty config after the bounded relay is closed.
        config_path.write_text('{}')
        gate = evaluate(scenario, root, args.cairn, args.opencode, root, config_path, changes, outside)
        relay_report = json.loads((root / 'relay.json').read_text()) if args.execute_model else {}
        assessment = repair_assessment(process_exit, gate, events, relay_report)
        if not args.execute_model:
            assessment = dict(task_outcome='not_attempted', reason='model-free executor calibration')
            if not result['change_set_checked'] or gate.get('gate_actions') != ['fail'] or process_exit != 0 or not exact_prompt:
                raise RuntimeError('Fixture must complete, admit its harmless output and fail the real repair oracle')
        result.update(execution_kind='model' if args.execute_model else 'fixture', gate=gate,
            assessment=assessment, process_exit=process_exit, changed_paths=sorted(changes), outside_scope=outside,
            patch_sha256=sha(patch.encode()), transcript_sha256=sha(transcript),
            exact_prompt_received=exact_prompt, prompt_sha256=sha(prompt), lesson_occurrences=prompt.decode().count(lesson),
            provider_requests=len(relay_report.get('requests', [])))
        private_file(root / 'assessment.json', json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(event='native_arm_finished', arm=arm, admitted=result['change_set_admitted'],
                             task_outcome=assessment['task_outcome'], provider_requests=result['provider_requests'])), flush=True)
        return result
    finally:
        config_path.unlink(missing_ok=True)
        for name in ('runtime-home', 'runtime-cache', 'evaluation-home', 'evaluation-cache'):
            path = root / name
            if path.exists():
                shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'striatum', 'cairn', 'opencode', 'lesson', 'preflight'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--execute-model', action='store_true')
    parser.add_argument('--fixture-report', type=Path)
    args = parser.parse_args()
    for name in ('output', 'striatum', 'cairn', 'opencode', 'lesson', 'preflight'):
        setattr(args, name, getattr(args, name).resolve())
    if args.fixture_report is not None:
        args.fixture_report = args.fixture_report.resolve()
    # Go auto-toolchain selection follows the module in the working directory.
    os.chdir(PROJECT)
    scenario = json.loads(SCENARIO.read_text())
    lesson, _ = pinned_inputs(scenario, args.cairn, args.opencode, args.lesson)
    preflight = json.loads(args.preflight.read_text())
    if not preflight['passed'] or preflight['scenario_sha256'] != sha(SCENARIO.read_bytes()):
        raise ValueError('Matching successful source/oracle preflight required')
    if args.execute_model and args.fixture_report is None:
        raise ValueError('Model execution requires a completed native fixture report')
    args.output.mkdir(mode=0o700)
    driver_binary = args.output / 'driver.test'
    run_logged(['go', 'test', '-c', '-o', str(driver_binary), './internal/driver'], args.output / 'build-driver.log', cwd=args.striatum, timeout=180)
    pins = executor_pins(args.striatum, driver_binary)
    if args.execute_model:
        require_fixture(json.loads(args.fixture_report.read_text()), scenario, pins)
    permission_probe = None
    if not args.execute_model:
        permission_probe = probe_permissions(args.output / 'permission-probe', args.opencode, args.cairn,
            pins['go_paths'], runtime_permissions())
        if not permission_probe['passed']:
            raise RuntimeError('Actual OpenCode permission probe failed; inspect ' + str(args.output / 'permission-probe'))
    runtime_binary = args.opencode
    if not args.execute_model:
        fixture_source = args.output / 'fixture.go'
        fixture_source.write_bytes((PROJECT / 'trials/native-recurrence/fixture-runtime.go.txt').read_bytes())
        runtime_binary = args.output / 'fixture-runtime'
        run_logged(['go', 'build', '-o', str(runtime_binary), str(fixture_source)], args.output / 'build-fixture.log', timeout=60)
    report = dict(schema='cairn.native-recurrence-result/1', execution_kind='model' if args.execute_model else 'fixture',
                  scenario_sha256=sha(SCENARIO.read_bytes()), controller_sha256=sha(Path(__file__).read_bytes()),
                  runtime_bridge_sha256=sha((PROJECT / 'scripts/trial_native_runtime.py').read_bytes()),
                  driver_binary_sha256=sha(driver_binary.read_bytes()), execution_pins=pins, permission_probe=permission_probe, arms=[])
    for arm in scenario['arms']:
        if executor_pins(args.striatum, driver_binary) != pins:
            raise ValueError('Executor changed during the comparison')
        report['arms'].append(run_arm(args, scenario, args.output / arm, arm, lesson, driver_binary, runtime_binary))
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
