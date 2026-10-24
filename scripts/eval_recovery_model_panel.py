"""Run the frozen payload-free CAIRN-226 classification panel locally."""

import hashlib
import json
from pathlib import Path
import tempfile

from recovery_model_report import compile_report, read_evidence
from workflow_model_report import attempt_ref, event_uuid, opaque


PANEL = Path(__file__).resolve().parents[1] / 'trials/recovery-model/FROZEN.json'


def evaluate(panel):
    if panel.get('schema') != 'cairn.recovery-panel/1' or not isinstance(panel.get('cases'), list):
        raise ValueError('unsupported recovery panel')
    cases = panel['cases']
    if len(cases) != 10 or len({case['id'] for case in cases}) != len(cases):
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
        observations = read_evidence(evidence, max_observations=10)
    report = compile_report(dict(schema='cairn.workflow-model-report/1', workflows=workflows), observations)
    failures = []
    for case, row in zip(cases, report['observations'], strict=True):
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
    return dict(schema='cairn.recovery-panel-result/1', cases=len(cases),
                panel_sha256=hashlib.sha256(PANEL.read_bytes()).hexdigest(),
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
    result = evaluate(json.loads(PANEL.read_text()))
    print(json.dumps(result, sort_keys=True))
    if not result['passed']:
        raise SystemExit(1)
