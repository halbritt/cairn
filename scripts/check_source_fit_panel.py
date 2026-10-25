"""Validate the frozen CAIRN-223 source-fit panel without model calls.

This checks fixture custody and the hosted source boundary. It does not score
retrieval relevance or task outcomes; those require a separately admitted run.
"""

import argparse
import hashlib
import json
from pathlib import Path


PANEL = Path(__file__).resolve().parents[1] / 'trials/source-fit/FROZEN.json'
ARMS = {'current', 'direct', 'hints', 'author_split'}
ELIGIBLE = {'shareable_optional', 'mandatory'}


def source_body(record):
    parts = record['body_parts']
    if not isinstance(parts, list) or not parts:
        raise ValueError('record needs nonempty body_parts')
    body = ''
    for part in parts:
        if set(part) not in ({'text'}, {'text', 'repeat'}):
            raise ValueError('invalid body part')
        repeat = part.get('repeat', 1)
        if type(repeat) is not int or not 1 <= repeat <= 200:
            raise ValueError('invalid body repeat')
        if not isinstance(part['text'], str) or not part['text']:
            raise ValueError('invalid body text')
        body += part['text'] * repeat
    if len(body.encode()) > 32768:
        raise ValueError('oversized synthetic source')
    return body


def validate(panel, panel_path=PANEL):
    if panel.get('schema') != 'cairn.source-fit-panel/1':
        raise ValueError('unknown panel schema')
    records = panel['records']
    cases = panel['cases']
    if not isinstance(records, dict) or not records or not isinstance(cases, list):
        raise ValueError('missing records or cases')
    bodies = {key: source_body(value) for key, value in records.items()}
    if any(value.get('eligibility') not in ELIGIBLE | {'local_only_inaccessible_to_hosted'}
           for value in records.values()):
        raise ValueError('unknown eligibility')
    ids = set()
    run_count = 0
    for case in cases:
        case_id = case['id']
        if case_id in ids:
            raise ValueError('duplicate case ID')
        ids.add(case_id)
        order = case['source_order']
        labels = case['source_labels']
        arms = case['arms']
        if (not isinstance(case['task'], str) or not case['task']
                or not isinstance(order, list) or not order or len(order) != len(set(order))
                or not set(order) <= bodies.keys()
                or not isinstance(labels, list) or not set(labels) <= set(order)
                or not set(labels) <= {key for key in order if records[key]['eligibility'] in ELIGIBLE}
                or not isinstance(arms, list) or not {'current', 'direct'} <= set(arms)
                or len(arms) != len(set(arms)) or not set(arms) <= ARMS):
            raise ValueError(f'invalid case: {case_id}')
        if not case.get('required_answer') or not case.get('forbidden_conclusion'):
            raise ValueError(f'missing outcome rubric: {case_id}')
        # Direct context must contain the full eligible source, never a substring
        # or a local-only record. The source order is retained for diagnosis.
        direct = {key: bodies[key] for key in order if records[key]['eligibility'] in ELIGIBLE}
        if any(key in direct for key in order if records[key]['eligibility'] not in ELIGIBLE):
            raise ValueError(f'inaccessible source exposed: {case_id}')
        if 'author_split' in arms and case_id != 'buried_live_qualification':
            raise ValueError('author split was not preregistered for this case')
        if 'hints' in arms and (not isinstance(case.get('hint_suffix'), str)
                               or 'Project: Cairn.' not in case['hint_suffix']
                               or '"' not in case['hint_suffix']):
            raise ValueError(f'missing exact project/source hint: {case_id}')
        if 'author_split' in arms and case.get('author_split') != dict(
                original='relay_mixed', current='relay_current_extract',
                preserve_original_history=True):
            raise ValueError('unfrozen author-maintained extraction')
        run_count += len(arms)
    plan = panel['provider_plan']
    account_homes = plan.get('account_homes')
    assignment = plan.get('account_assignment')
    if account_homes is not None or assignment is not None:
        if (not isinstance(account_homes, dict) or not account_homes
                or not isinstance(assignment, dict)
                or set(assignment) != ids
                or not set(assignment.values()) <= set(account_homes)
                or set(assignment.values()) != set(account_homes)):
            raise ValueError('account assignment must cover every case across all pinned homes')
    else:
        # Existing Claude freezes predate the multi-home Codex assignment field.
        # Keep validating their immutable manifests without fabricating accounts.
        account_homes, assignment = {}, {}
    if (plan.get('status') not in {'proposed_not_authorized',
                                    'admission_pending_runner_preflight'}
            or plan.get('maximum_runs') != run_count
            or plan.get('replicates') != 1
            or plan.get('maximum_wall_seconds_per_run', 0) * run_count
            > plan.get('maximum_campaign_wall_seconds', 0)
            or 'subscription' not in plan.get('route', '')
            or 'OpenRouter fallback' not in plan.get('route', '')):
        raise ValueError('provider plan status, arm ceiling, or subscription route is invalid')
    if account_homes:
        required_disabled = {'shell_tool', 'code_mode', 'code_mode_only'}
        if (plan.get('maximum_primary_used_percent') != 80
                or not required_disabled <= set(plan.get('disabled_features', []))
                or plan.get('tool_availability_preflight') is None
                or plan.get('maximum_tokens_per_run', 0) * run_count
                + plan.get('minimum_next_run_reserve', 0)
                > plan.get('maximum_total_reported_tokens', 0)):
            raise ValueError('Codex route gates or full-panel token reserve are not frozen')
    account_runs = {account: sum(len(case['arms']) for case in cases
                                 if assignment[case['id']] == account)
                    for account in account_homes}
    return dict(schema='cairn.source-fit-validation/1', cases=len(cases), runs=run_count,
                account_runs=account_runs,
                source_bytes={key: len(body.encode()) for key, body in bodies.items()},
                source_sha256={key: hashlib.sha256(body.encode()).hexdigest()
                               for key, body in bodies.items()},
                panel_sha256=hashlib.sha256(Path(panel_path).read_bytes()).hexdigest(),
                qualification='fixture and eligibility only; no provider or task outcome')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--panel', default=str(PANEL))
    panel_path = Path(parser.parse_args().panel)
    print(json.dumps(validate(json.loads(panel_path.read_text()), panel_path), sort_keys=True))


if __name__ == '__main__':
    main()
