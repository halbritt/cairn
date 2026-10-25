"""Legacy conversion receipt -> grading-join bundle -> run report join input. CAIRN-107.

Chain (see docs/legacy-grading-bundle.md):

1. trial_legacy_blind_selection receipt (pair_classification, changed_pair_grading),
   re-verified by re-running the idempotent converter on its own request;
2. a cairn.legacy-grading-join-input/1 bundle for the existing
   trial_grading_custody.join_results (its separate legacy branch);
3. the join report trial_run_report reads with --join, recomputed by the
   existing join: with no grades only when every pair is determinate and
   unchanged; otherwise from the submissions retained by
   trial_grader_delivery.collect over this exact bundle.

Nothing here grades, releases or delivers. No grade or disposition is ever
created: a changed pair without returned grades stays awaiting grades, and
disagreements stay unresolved for the owner.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

import trial_blind_packet_draft as blind
import trial_grading_custody as custody
import trial_legacy_blind_selection as legacy
import trial_paired_request as paired

RESULT_SCHEMA = 'cairn.legacy-grading-bundle-receipt/1'
ARMS = ('CURRENT', 'CAND')
need = paired.need


def _digest(content):
    return dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def _read(reference, code='legacy_bundle_pin_mismatch'):
    need(isinstance(reference, dict) and set(reference) == {'path', 'bytes', 'sha256'}, code)
    content = legacy._read_path(reference['path'])
    need(_digest(content) == {'bytes': reference['bytes'], 'sha256': reference['sha256']}, code)
    return content


def _account_assignment(plan, agent, records):
    assignment_sha256 = plan.get('account_assignment_sha256')
    assignments = plan.get('run_accounts')
    need(agent.get('account_assignment_sha256') == assignment_sha256,
         'legacy_bundle_account_assignment_mismatch')
    if assignment_sha256 is None:
        need(assignments is None and all('account_id' not in record for record in records),
             'legacy_bundle_account_assignment_mismatch')
        return None, None
    need(type(assignment_sha256) is str and re.fullmatch('[0-9a-f]{64}', assignment_sha256) and
         type(assignments) is list and len(assignments) == 126,
         'legacy_bundle_account_assignment_mismatch')
    run_ids = {f'{case}.{arm}.s{seed}' for case, arm, seed, _ in plan['runs']}
    account_by_run = {}
    for row in assignments:
        need(type(row) is dict and set(row) == {'run_id', 'account_id'} and
             type(row['run_id']) is str and row['run_id'] in run_ids and row['run_id'] not in account_by_run and
             paired.capacity_name(row['account_id']),
             'legacy_bundle_account_assignment_mismatch')
        account_by_run[row['run_id']] = row['account_id']
    need(set(account_by_run) == run_ids and all(
        record.get('account_id') == account_by_run.get(record['run_id']) for record in records),
        'legacy_bundle_account_assignment_mismatch')
    return account_by_run, sorted(assignments, key=lambda row: row['run_id'])


def _planned(plan, account_by_run=None):
    """The runner's exact planned identities (trial_weekly_run spec 'planned')."""
    rows = []
    for case, arm, seed, _ in plan['runs']:
        row = dict(run_id=f'{case}.{arm}.s{seed}', case=case, arm=arm, seed=seed)
        if account_by_run is not None:
            row['account_id'] = account_by_run[row['run_id']]
        rows.append(row)
    return sorted(rows, key=lambda r: r['run_id'])


def _runner_plan(content):
    """Read the pinned runner plan while validating its non-diagnostic data."""
    plan = legacy._json(content)
    checked = copy.deepcopy(plan)
    backends = checked.get('memory_backends') if isinstance(checked, dict) else None
    if isinstance(backends, dict):
        for arm in ARMS:
            backend = backends.get(arm)
            readiness = backend.get('readiness') if isinstance(backend, dict) else None
            if isinstance(readiness, dict) and 'cold_seconds' in readiness:
                seconds = readiness['cold_seconds']
                need(type(seconds) is float and math.isfinite(seconds) and seconds >= 0,
                     'legacy_bundle_readiness_diagnostic')
                del readiness['cold_seconds']
    # Keep the stricter blinded-document JSON domain for all remaining plan
    # fields. The two omitted values are pinned runner readiness diagnostics;
    # the converter has already bound the exact source bytes.
    blind._structured(checked)
    return plan


def _packets(receipt, draft_pin):
    changed = receipt['pairs']['changed']
    if not changed:
        need(draft_pin is None, 'legacy_bundle_draft_unexpected')
        return [], []
    need(draft_pin is not None, 'legacy_bundle_draft_required')
    draft = blind._json(_read(draft_pin))
    need(isinstance(draft, dict) and draft.get('schema') == 'cairn.blind-draft-receipt/2'
         and draft.get('selection_pin') == receipt['selection'], 'legacy_bundle_draft_mismatch')
    public = Path(draft_pin['path']).parent.parent/'public'
    selection = blind._json(_read(receipt['selection']))
    samples = {s['sample_id']: s['source'] for s in selection['samples']}
    packets, delivery_pairs = [], []
    for comparison in selection['comparisons']:
        name = 'comparisons/' + comparison['comparison_id'] + '.json'
        content = legacy._read_path(public/name)
        need(draft['public_files'].get(name) == _digest(content), 'legacy_bundle_draft_mismatch')
        key = comparison['pair_key']
        need({samples[i]['arm'] for i in comparison['samples']} == set(ARMS), 'legacy_bundle_draft_mismatch')
        packets.append(dict(case=key['case'], seed=key['seed'], pair_id=comparison['comparison_id'],
                            packet_sha256=_digest(content)['sha256'], samples=list(comparison['samples'])))
        delivery_pairs.append(dict(pair_id=comparison['comparison_id'],
                                   comparison=dict(path=str(public/name), **_digest(content))))
    need(len(packets) == changed, 'legacy_bundle_draft_mismatch')
    return packets, delivery_pairs


def _status(grading):
    # Only the converter's no_grading_needed classification completes without grades.
    return dict(no_grading_needed='no_grading_needed', awaiting_grading='awaiting_grades').get(
        grading['state'], 'incomplete_' + grading['state'])


def _build(receipt_pin, policy, draft_pin):
    receipt_bytes = _read(receipt_pin)
    receipt = blind._json(receipt_bytes)
    need(isinstance(receipt, dict) and receipt.get('schema') == legacy.RECEIPT_SCHEMA, 'legacy_bundle_receipt')
    output = Path(receipt_pin['path']).parent
    need(receipt_pin['path'] == str(output/'receipt.json'), 'legacy_bundle_receipt')
    # Every converter check (coverage, identity, provenance, binding, output bytes)
    # is re-run against the current files; the receipt must be reproduced exactly.
    need(legacy.convert(receipt['request'], output) == receipt, 'legacy_bundle_receipt')
    sources = receipt['sources']
    agent_bytes, plan_bytes, binding_bytes = (_read(sources[k]) for k in ('agent', 'plan', 'binding'))
    agent = json.loads(agent_bytes)
    plan = _runner_plan(plan_bytes)
    pair_binding = blind._json(binding_bytes)
    request = blind._json(_read(receipt['request']))
    need(pair_binding['arm_roles'] == {arm: arm for arm in ARMS} and agent['arms'] == plan['arms']
         and set(agent['arms']) == set(ARMS), 'legacy_bundle_arm_roles')
    records = sorted(agent['records'], key=lambda r: r['run_id'])
    account_by_run, run_accounts = _account_assignment(plan, agent, records)
    planned = _planned(plan, account_by_run)
    need(paired.canonical_sha(planned) == pair_binding['schedule_sha256'], 'legacy_bundle_schedule_mismatch')
    need([r['run_id'] for r in records] == [r['run_id'] for r in planned], 'legacy_bundle_attempt_identity')
    observed = dict(agent_sha256=sources['agent']['sha256'],
                    plan_file_sha256=sources['plan']['sha256'],
                    conversion_receipt_sha256=receipt_pin['sha256'],
                    distractors=agent.get('distractors'),
                    positions=sorted((dict(case=case, seed=seed, arm=arm, position=position)
                                      for case, arm, seed, position in plan['runs']),
                                     key=lambda r: (r['case'], r['seed'], r['arm'])))
    if account_by_run is not None:
        observed.update(account_assignment_sha256=plan['account_assignment_sha256'],
                        run_accounts=run_accounts)
    legacy_plan = dict(schema=custody.LEGACY_PLAN, cases=sorted({r['case'] for r in planned}), arms=list(ARMS),
                       seeds=sorted({r['seed'] for r in planned}), attempts=len(planned),
                       pairs=receipt['pairs']['total'], distractors=1900,
                       declared=dict(pair_binding=pair_binding, pair_binding_pin=sources['binding']),
                       observed=observed)
    legacy_plan['sha256'] = paired.canonical_sha(legacy_plan)
    packets, delivery_pairs = _packets(receipt, draft_pin)
    blind_policy = request['policy']
    bundle = dict(schema=custody.LEGACY_SCHEMA, plan=legacy_plan, schedule=planned,
                  inventory=[dict(case=r['case'], seed=r['seed'], arm=r['arm'], run_id=r['run_id'], state='completed',
                                  outcome=r['outcome'], evidence_sha256=r['stream_sha256']) for r in records],
                  selection=receipt['pair_classification'], packets=packets, policy=policy,
                  binding=dict({k: pair_binding[k] for k in ('request_id', 'campaign_id', 'occurrence_id', 'request_sha256')},
                               **{k + '_sha256': blind_policy[k]['pin']['sha256']
                                  for k in ('rubric', 'changed_pair_definition', 'blinding_policy')}))
    for field in ('plan', 'schedule', 'inventory', 'selection', 'packets', 'policy'):
        bundle['binding'][field + '_sha256'] = paired.canonical_sha(bundle[field])
    custody.join_results(bundle, [], [])  # the bundle must pass the existing join
    grading = receipt['changed_pair_grading']
    result = dict(schema=RESULT_SCHEMA, conversion_receipt=dict(receipt_pin), draft_receipt=draft_pin,
                  bundle_sha256=paired.canonical_sha(bundle), binding_sha256=paired.canonical_sha(bundle['binding']),
                  status=_status(grading), changed_pair_grading=grading, delivery_pairs=delivery_pairs,
                  disputes='owner_only', grades_created=False, delivery_performed=False, runtime_admitted=False)
    return bundle, result


def build(receipt_pin, policy, *, draft_receipt=None):
    """Return (bundle, result). Pure apart from reading and re-verifying pinned files."""
    try:
        return _build(receipt_pin, policy, draft_receipt)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        raise paired.Refusal('legacy_bundle_invalid_or_unavailable') from None


def report_join(bundle, result, collection=None):
    """The --join input for trial_run_report, always recomputed by the existing
    join from this exact bundle; nothing cached is passed through.

    Without a collection: only a run whose every pair is determinate and
    unchanged (zero changed pairs) has a report join, the join with no grades.
    With a collection (trial_grader_delivery.collect over this bundle): its
    join must equal the join recomputed from its own retained submissions,
    with no dispositions, so a changed or stale join is refused.
    """
    try:
        need(isinstance(bundle, dict) and bundle.get('schema') == custody.LEGACY_SCHEMA and isinstance(result, dict)
             and result.get('bundle_sha256') == paired.canonical_sha(bundle), 'legacy_bundle_mismatch')
        if collection is None:
            join = custody.join_results(bundle, [], [])
            need(join['pairs'] and all(pair['selection'] == 'unchanged' for pair in join['pairs'])
                 and join['aggregate']['selection'] == dict(changed=0, unchanged=len(join['pairs']), undetermined=0),
                 'legacy_grades_awaited')
            return join
        join = collection.get('join') if isinstance(collection, dict) else None
        need(isinstance(collection, dict) and collection.get('schema') == 'cairn.grader-delivery-collection/1'
             and isinstance(join, dict) and isinstance(join.get('submissions'), list), 'legacy_collection_mismatch')
        submissions = [entry.get('submission') if isinstance(entry, dict) else None for entry in join['submissions']]
        try:
            expected = custody.join_results(bundle, submissions, [])
        except paired.Refusal:
            raise paired.Refusal('legacy_collection_mismatch') from None
        need(paired.canonical_sha(join) == paired.canonical_sha(expected), 'legacy_collection_mismatch')
        return expected
    except paired.Refusal:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise paired.Refusal('legacy_bundle_invalid_or_unavailable') from None
