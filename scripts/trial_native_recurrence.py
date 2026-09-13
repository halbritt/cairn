#!/usr/bin/env python3
"""Prepare and calibrate the frozen native recurrence workspace without model calls.

This command does not yet execute Striatum or a model. It shares the previous
repair experiment's source snapshot, sandbox and independent evaluator, but uses
the distinct prospective inputs in trials/native-recurrence/scenario.json.
"""
import argparse
import json
from pathlib import Path
import subprocess

from trial_agent_env import GATE, GATE_NAME, PROJECT, command_base, evaluate, sha, snapshot
from trial_retrieval_tools import private_file

SCENARIO = PROJECT / 'trials/native-recurrence/scenario.json'
GATE_TEST = 'TestAgentConnectionDefaultsTransferGate'


def pinned_inputs(scenario, cairn, opencode, lesson_file):
    for path, expected in ((GATE, scenario['gate_sha256']), (cairn, scenario['cairn_sha256']),
                           (opencode, scenario['opencode_sha256'])):
        if sha(path.read_bytes()) != expected:
            raise ValueError('Frozen input changed: ' + str(path))
    record = json.loads(lesson_file.read_text())['data']['selection']['record']
    expected = scenario['lesson']
    if (record['record_id'] != expected['record_id'] or record['version'] != expected['version']
            or sha(record['body'].encode()) != expected['body_sha256']
            or record['class'] != 'A' or record['sensitivity'] != 'shareable'):
        raise ValueError('Retrieved lesson does not match the frozen shareable A record')
    calibration = scenario['calibration']
    reference = subprocess.check_output(['git', '-C', str(PROJECT), 'show',
        calibration['reference_commit'] + ':' + calibration['reference_path']])
    if sha(reference) != calibration['reference_sha256']:
        raise ValueError('Reviewer reference changed')
    return record['body'], reference


def workspace_manifest(originals):
    return [dict(path=name, sha256=sha(body)) for name, body in sorted(originals.items())]


def gate_cases(log):
    cases = {}
    for line in log.read_bytes().splitlines():
        event = json.loads(line)
        name = event.get('Test', '')
        if name.startswith(GATE_TEST + '/') and event['Action'] in ('pass', 'fail', 'skip'):
            name = name.removeprefix(GATE_TEST + '/')
            if name in cases:
                raise ValueError('Gate case ran more than once: ' + name)
            cases[name] = event['Action']
    return cases


def require_calibration(scenario, baseline, reference):
    expected = scenario['calibration']
    base_cases, ref_cases = baseline['cases'], reference['cases']
    if len(base_cases) != expected['case_count'] or set(base_cases) != set(ref_cases):
        raise ValueError('Calibration did not observe the same complete gate in both conditions')
    if sorted(n for n, action in base_cases.items() if action == 'fail') != sorted(expected['baseline_failed']):
        raise ValueError('Baseline defect differs from its frozen calibration')
    if any(action not in ('pass', 'fail') for action in base_cases.values()):
        raise ValueError('Baseline gate skipped a case')
    if baseline['gate']['passed'] or baseline['gate']['gate_actions'] != ['fail']:
        raise ValueError('Baseline was not rejected by the held-out gate')
    if not reference['gate']['passed'] or any(action != 'pass' for action in ref_cases.values()):
        raise ValueError('Reviewed reference did not pass the complete evaluator')


def preflight(args):
    scenario = json.loads(SCENARIO.read_text())
    lesson, reference = pinned_inputs(scenario, args.cairn, args.opencode, args.lesson)
    root = args.output
    root.mkdir(mode=0o700)
    report = dict(schema='cairn.native-recurrence-preflight/1',
        scenario_sha256=sha(SCENARIO.read_bytes()),
        controller_sha256=sha(Path(__file__).read_bytes()),
        model_requests=0, native_execution_verified=False, calibration={})
    # The retained workspace is the exact tree a future native packet will use.
    # Private lesson, oracle and reference are never copied into this directory.
    originals = snapshot(scenario, root / 'workspace')
    manifest = workspace_manifest(originals)
    private_file(root / 'workspace-manifest.json', json.dumps(manifest, indent=2) + '\n')
    report['workspace_manifest_sha256'] = sha((root / 'workspace-manifest.json').read_bytes())
    report['workspace_files'] = len(manifest)
    if set(originals) != set(scenario['snapshot_paths']):
        raise ValueError('Archive file set differs from frozen manifest')
    if GATE_NAME in originals or any(any(name.startswith(p) for p in scenario['excluded_prefixes']) for name in originals):
        raise ValueError('Workspace contains withheld experiment material')
    private_file(root / 'lesson.txt', lesson)
    config = root / 'opencode.json'
    private_file(config, '{}')
    for name in ('home', 'cache'):
        (root / name).mkdir(mode=0o700)
    base = command_base(args.opencode, args.cairn, root, root / 'workspace', root / 'home', config, root / 'cache', False)
    # No API, model credentials or database DSN enters this sandbox. Preserve
    # the command's stdout and stderr before classifying any failed preflight.
    for name, command in (('check', ['make', 'check']), ('tests', ['make', 'test'])):
        result = subprocess.run(base + command, capture_output=True, timeout=180)
        private_file(root / (name + '.stdout'), result.stdout.decode())
        private_file(root / (name + '.stderr'), result.stderr.decode())
        report[name] = dict(exit_code=result.returncode, stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr))
        if result.returncode:
            raise RuntimeError('Frozen workspace ' + name + ' failed; inspect retained logs')
    for name, body in (('baseline', originals['cmd/cairn/agent.go']), ('reference', reference)):
        arm_root = root / name
        arm_root.mkdir(mode=0o700)
        # Evaluating unchanged source here is intentional calibration, not a
        # model candidate or task acceptance. Model runs must use candidate().
        gate = evaluate(scenario, arm_root, args.cairn, args.opencode, root, config,
                        {'cmd/cairn/agent.go': body}, [])
        report['calibration'][name] = dict(gate=gate, cases=gate_cases(arm_root / 'gate.jsonl'))
    require_calibration(scenario, **report['calibration'])
    report['passed'] = True
    report['limits'] = 'Source and evaluator calibration only. No model, native Driver execution or memory benefit measured. Database tests skip. The complete frozen repository includes older different-task experiments equally in all conditions.'
    private_file(root / 'report.json', json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cairn', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--lesson', type=Path, required=True, help='Private Cairn pull response for the pinned lesson')
    args = parser.parse_args()
    for name in ('output', 'cairn', 'opencode', 'lesson'):
        setattr(args, name, getattr(args, name).resolve())
    preflight(args)


if __name__ == '__main__':
    main()
