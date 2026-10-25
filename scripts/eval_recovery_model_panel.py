"""Run the frozen payload-free CAIRN-226 classification panel locally."""

import hashlib
import json
from pathlib import Path
import tempfile

from recovery_model_report import compile_report, read_evidence
from workflow_model_report import attempt_ref, event_uuid, opaque


PANEL = Path(__file__).resolve().parents[1] / 'trials/recovery-model/FROZEN.json'
PANEL_V2 = Path(__file__).resolve().parents[1] / 'trials/recovery-model/FROZEN-v2.json'


def evaluate(panel, panel_path=None):
    schema = panel.get('schema')
    if schema not in ('cairn.recovery-panel/1', 'cairn.recovery-panel/2') or not isinstance(panel.get('cases'), list):
        raise ValueError('unsupported recovery panel')
    cases = panel['cases']
    expected_case_count = panel.get('expected_case_count', 10 if schema == 'cairn.recovery-panel/1' else None)
    if (type(expected_case_count) is not int or len(cases) != expected_case_count
            or len({case['id'] for case in cases}) != len(cases)):
        raise ValueError('recovery panel cases changed')
    workflows = []
    for case in cases:
        observation = case['observation']
        event = opaque(event_uuid(observation['event_id']))
        selected = case.get('workflow')
        deliveries = []
        if selected:
            responses = selected.get('provider_responses')
            if responses is not None and (type(responses) is not int or responses < 0):
                raise ValueError('invalid frozen provider response count')
            provider = dict(usage=dict(observed_responses=responses)) if responses is not None else None
            attempt = attempt_ref(selected['attempt_id'])
            if attempt is None:
                raise ValueError('invalid frozen workflow attempt')
            deliveries.append(dict(delivery=opaque(event_uuid(observation['delivery_id'])),
                                   attempt=attempt,
                                   provider=provider,
                                   turn_ref='synthetic-' + case['id'] if provider is not None else None))
        workflows.append(dict(event=event, deliveries=deliveries))
    with tempfile.TemporaryDirectory(prefix='cairn-recovery-panel-') as temporary:
        evidence = Path(temporary) / 'evidence.json'
        evidence.write_text(json.dumps(dict(schema='cairn.recovery-evidence/1',
                                            observations=[case['observation'] for case in cases])))
        observations = read_evidence(evidence, max_observations=len(cases))
    report = compile_report(dict(schema='cairn.workflow-model-report/1', workflows=workflows), observations)
    failures = []
    owner_prompt_turns = {}
    for case, row in zip(cases, report['observations'], strict=True):
        owner_prompt = case.get('owner_prompt')
        if owner_prompt is not None:
            if not isinstance(owner_prompt, dict):
                raise ValueError('invalid joined owner prompt evidence')
            turn = opaque(event_uuid(owner_prompt.get('native_turn_id')))
            responses = owner_prompt.get('provider_responses')
            if (turn is None or (responses is not None
                    and (type(responses) is not int or responses < 0))):
                raise ValueError('invalid joined owner prompt identity or usage')
            previous = owner_prompt_turns.setdefault(turn, responses)
            if previous != responses:
                raise ValueError('conflicting joined owner prompt usage')
        for key, expected in case['observation'].items():
            if key in ('event_id', 'delivery_id', 'attempt_id'):
                continue  # Identifiers are intentionally represented only by opaque joins.
            if key not in row['observation'] or row['observation'][key] != expected:
                failures.append(dict(case=case['id'], field='observation.' + key,
                                     expected=expected, observed=row['observation'].get(key)))
        for key, expected in case['expected'].items():
            if row[key] != expected:
                failures.append(dict(case=case['id'], field=key, expected=expected, observed=row[key]))
    if report['repeated_emitted_reminder_revisions'] != panel['expected_repeated_emitted_reminder_revisions']:
        failures.append(dict(field='repeated_emitted_reminder_revisions'))
    for phase, expected in panel['expected_phase_responses'].items():
        if report['phases'][phase]['observed_responses'] != expected:
            failures.append(dict(phase=phase, field='observed_responses', expected=expected,
                                 observed=report['phases'][phase]['observed_responses']))
    for field, expected_by_phase in (('provider_turns', panel['expected_phase_provider_turns']),
                                     ('operator_actions', panel['expected_phase_operator_actions'])):
        for phase, expected in expected_by_phase.items():
            if report['phases'][phase][field] != expected:
                failures.append(dict(phase=phase, field=field, expected=expected,
                                     observed=report['phases'][phase][field]))
    panel_path = panel_path or (PANEL if schema == 'cairn.recovery-panel/1' else PANEL_V2)
    owner_responses = (None if any(value is None for value in owner_prompt_turns.values())
                       else sum(owner_prompt_turns.values()))
    return dict(schema='cairn.recovery-panel-result/1', panel_schema=schema, cases=len(cases),
                joined_owner_prompt_native_turns=len(owner_prompt_turns),
                joined_owner_prompt_responses=owner_responses,
                panel_sha256=hashlib.sha256(panel_path.read_bytes()).hexdigest(),
                passed=not failures, failures=failures,
                repeated_emitted_reminder_revisions=report['repeated_emitted_reminder_revisions'],
                phase_responses={name: summary['observed_responses']
                                 for name, summary in report['phases'].items()},
                phase_provider_turns={name: summary['provider_turns']
                                      for name, summary in report['phases'].items()},
                phase_operator_actions={name: summary['operator_actions']
                                        for name, summary in report['phases'].items()},
                qualification='synthetic local classification only; no provider/model work measured')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', type=Path, default=PANEL_V2,
                        help='versioned recovery panel (defaults to the current v2 panel)')
    args = parser.parse_args()
    result = evaluate(json.loads(args.panel.read_text()), panel_path=args.panel)
    print(json.dumps(result, sort_keys=True))
    if not result['passed']:
        raise SystemExit(1)
