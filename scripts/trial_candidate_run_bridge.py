"""Pinned candidate preparation to the existing weekly runner; no authority inferred.

Inputs remain owner-held, private and quiescent as in the preparation APIs.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import trial_candidate_request as candidate
import trial_paired_request as paired
import trial_weekly_run as weekly
import trial_weekly_input as weekly_input

SCHEMA = 'cairn.candidate-run-bridge/1'
RECEIPT = 'cairn.candidate-run-bridge-receipt/1'
need = paired.need


def _pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(paired.read_bytes(path)).hexdigest())


def _pins(value):
    """Verify selected control pins without interpreting opaque file contents."""
    if isinstance(value, dict):
        if set(value) == {'path', 'sha256'}:
            paired.pin(value)
        else:
            for item in value.values(): _pins(item)
    elif isinstance(value, list):
        for item in value: _pins(item)


def _load(selection_pin, now):
    selected = paired.read_json_pin(selection_pin)
    paired.shape(selected, 'schema entry prepared runner_profile', 'bridge_selection_shape')
    need(selected['schema'] == SCHEMA, 'bridge_selection_schema')
    entry = paired.read_json_pin(selected['entry'])
    prepared = paired.read_json_pin(selected['prepared'])
    base = Path(selected['entry']['path']).parent
    need(selected['entry']['path'] == str(base/'entry.json') and
         selected['prepared']['path'] == str(base/'prepared.json'), 'bridge_entry_path')
    need(entry.get('schema') == candidate.ENTRY_SCHEMA and entry.get('state') == 'queued_for_review' and
         prepared.get('schema') == candidate.PREPARED_SCHEMA and prepared.get('request_id') == entry['request_id'],
         'bridge_candidate_not_prepared')
    queued = entry['receipt']
    need(set(queued['draft']['files']) == {'request-core.json','schedule.json','review-subjects.json'},
         'bridge_draft_file')
    for name, expected in queued['draft']['files'].items():
        content = paired.read_bytes(base/'draft'/name)
        need(len(content) == expected['bytes'] and hashlib.sha256(content).hexdigest() == expected['sha256'],
             'bridge_draft_changed')
    core = paired.read_json(base/'draft/request-core.json')
    preparation = prepared['preparation']
    retained = paired.plain_path(core['output'])
    need(retained == base/'preparation' and paired.read_json(retained/'preparation.json') == preparation,
         'bridge_preparation_changed')
    need(preparation['schema'] == 'cairn.weekly-eval.preparation/1' and
         preparation['status'] == 'prepared_for_review', 'bridge_candidate_not_prepared')
    for name, expected in preparation['files'].items():
        need(Path(name).name == name and name not in ('.','..'), 'bridge_retained_file')
        content = paired.read_bytes(retained/name)
        need(len(content) == expected['bytes'] and hashlib.sha256(content).hexdigest() == expected['sha256'],
             'bridge_preparation_changed')
    request = paired.read_json(retained/'request.json')
    dispositions = paired.read_json_pin(prepared['dispositions'])
    need(request == dict(core, dispositions=dispositions) and
         paired.canonical_sha(request) == preparation['request_sha256'], 'bridge_preparation_changed')
    _pins(request); _pins({k: prepared[k] for k in ('dispositions','companion','review')})
    evidence = preparation['provider_evidence']
    need(evidence['schema'] == 'cairn.paired-companion-evidence/1', 'bridge_provider_binding')
    for reference in evidence['inputs'].values():
        filename = reference['file']
        need(filename in preparation['files'] and
             preparation['files'][filename]['sha256'] == reference['source']['sha256'],
             'bridge_provider_binding')
        paired.pin(reference['source'])
    for name in ('companion', 'review'):
        need(evidence['inputs'][name]['source'] == prepared[name], 'bridge_provider_binding')
    # Weekly preparation validated intervals covering spend end >= request end.
    # Unchanged retained evidence plus the exact active slot preserves that proof;
    # it does not authenticate the caller-held preparation or reviewer identity.
    for row in dispositions.values():
        need(row['state'] == 'accepted' and paired.utc(row['valid_until']) > now, 'bridge_review_stale')
    profile = paired.read_json_pin(selected['runner_profile'])
    repo, runs = weekly.check_profile(profile)
    # The shared Git helpers use ordinary archive/resolve. A replacement must
    # not silently change source behind an unchanged reviewed object name.
    need(not os.environ.get('GIT_REPLACE_REF_BASE') and
         not candidate._git(repo, 'for-each-ref', '--format=%(refname)', 'refs/replace'),
         'bridge_git_replacements')
    start, end = weekly.current_slot(profile['schedule'], now)
    dates = candidate._iso(start), candidate._iso(end)
    need((entry['scheduled_at'], entry['expires_at']) == dates ==
         (request['scheduled_at'], request['expires_at']), 'bridge_wrong_slot')
    need(entry['campaign_id'] == request['campaign_id'] == profile['campaign_id'], 'bridge_campaign_mismatch')
    commits = {arm:entry[key]['commit'] for arm,key in (('CURRENT','current'),('CAND','candidate'))}
    ids = candidate.occurrence_ids(entry['campaign_id'], dates[0], commits['CAND'], commits['CURRENT'])
    need(entry['request_id'] == request['request_id'] == ids['request'] and
         entry['occurrence_id'] == request['occurrence_id'] == ids['occurrence'], 'bridge_candidate_identity')
    for arm,key in (('CURRENT','current'),('CAND','candidate')):
        need(profile[key+'_ref'] == entry[key]['ref'] and
             candidate.resolve(repo, profile[key+'_ref'], 'bridge_ref_unavailable') == commits[arm], 'bridge_ref_drift')
        need(queued['arms'][arm]['commit'] == commits[arm], 'bridge_arm_mismatch')
    harness = candidate.resolve(repo, profile['harness_commit'], 'bridge_harness_unavailable')
    need(harness == profile['harness_commit'], 'bridge_harness_mismatch')
    execution = paired.read_json_pin(request['weekly_input']['execution'])
    schedule = paired.read_json_pin(request['schedule'])
    plan = request['plan']
    need(plan == paired.read_json_pin(profile['capacity']['plan']), 'bridge_plan_mismatch')
    need(schedule['sha256'] == preparation['schedule_sha256'] == paired.canonical_sha(
         {k:v for k,v in schedule.items() if k != 'sha256'}) and
         schedule['execution'] == request['weekly_input']['execution'] and
         schedule['plan_sha256'] == plan['sha256'], 'bridge_schedule_mismatch')
    need(profile['weekly_source'] == {k:request['weekly_input'][k] for k in ('label_version','frozen_sha256')},
         'bridge_frozen_mismatch')
    need(execution['actor_api_base'] == commits['CURRENT'] and execution['seeds'] == plan['seeds'] and
         execution['wording'] == schedule['wording'] == 'task', 'bridge_execution_mismatch')
    actor=weekly_input.actor(execution)
    need(actor['model'] == profile['capacity']['models']['CURRENT'] ==
         profile['capacity']['models']['CAND'], 'bridge_model_mismatch')
    # The runner's execution contract is the reviewed request's exact execution declaration.
    need(profile.get('execution') == request['weekly_input']['execution'], 'bridge_execution_mismatch')
    planned = sorted((dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s)
                      for c in plan['cases'] for s in plan['seeds'] for a in plan['arms']), key=lambda r:r['run_id'])
    observed = sorted((dict(run_id=f"{r['case']}.{r['arm']}.s{r['seed']}",
                           **{k:r[k] for k in ('case','arm','seed')}) for r in schedule['runs']), key=lambda r:r['run_id'])
    need(observed == planned, 'bridge_schedule_mismatch')
    for arm, original in execution['memory_arms'].items():
        need(original['api_revision'] == original['recall_revision'] == commits[arm] and
             original['binary']['sha256'] == queued['arms'][arm]['binary_sha256'] and
             original['embedding_worker'] == profile['embedding']['worker'], 'bridge_arm_mismatch')
    _pins(execution)
    slot = str(uuid.uuid5(weekly.NAMESPACE, profile['campaign_id']+'|'+dates[0]))
    claim_path = runs/slot/'claim.json'
    if claim_path.exists():
        claim = paired.read_json(claim_path)
        need(claim['slot_id'] == slot and claim['campaign_id'] == profile['campaign_id'] and
             (claim['scheduled_at'], claim['expires_at']) == dates and
             claim['harness_commit'] == harness and claim['executor'] == profile['executor'] and
             claim['current']['commit'] == commits['CURRENT'] and
             claim['candidate']['commit'] == commits['CAND'] and
             claim['capacity']['receipt_sha256'] == profile['capacity']['receipt']['sha256'] and
             claim['selection']['sha256'] == profile['selection']['sha256'] and
             (claim.get('original_execution') or {}).get('execution') == profile['execution'],
             'bridge_stored_slot_mismatch')
    binding = dict(candidate=dict(request_id=ids['request'], occurrence_id=ids['occurrence'],
                       request_sha256=preparation['request_sha256'], schedule_sha256=schedule['sha256'],
                       schedule_file_sha256=request['schedule']['sha256']),
                   runner=dict(request_id=slot, slot_id=slot,
                               occurrence_id=str(uuid.uuid5(weekly.NAMESPACE,'occurrence|'+slot)),
                               schedule_sha256=paired.canonical_sha(planned)),
                   campaign_id=profile['campaign_id'], scheduled_at=dates[0], expires_at=dates[1],
                   commits=commits, harness_commit=harness, execution=request['weekly_input']['execution'])
    fixed = dict(profile, current_ref=commits['CURRENT'], candidate_ref=commits['CAND'])
    return selected, binding, fixed, execution, planned, runs, (base, Path(request['weekly_input']['root']))


class _Executor:
    """Keep runner admission checks; verify the prepared spec before its effect."""
    def __init__(self, delegate, verify):
        self.delegate, self.verify = delegate, verify
    @property
    def name(self): return self.delegate.name
    @property
    def limits(self): return self.delegate.limits
    @property
    def account_ids(self): return self.delegate.account_ids
    def check(self, preview): return self.delegate.check(preview)
    def execute(self, spec, run_dir):
        self.verify(spec)
        return self.delegate.execute(spec, run_dir)


def _run(selection_pin, output, now, executors, compiler, runner):
    selected, binding, profile, execution, planned, runs, sources = _load(selection_pin, now)
    actor=weekly_input.actor(execution)
    output = paired.plain_path(str(output))
    need(output.parent.is_dir() and not (output == runs or output in runs.parents or runs in output.parents),
         'bridge_output_overlap')
    need(all(output != source and output not in source.parents and source not in output.parents
             for source in sources), 'bridge_output_overlap')
    for reference in selected.values():
        if isinstance(reference, dict):
            source = Path(reference['path'])
            need(output != source and output not in source.parents, 'bridge_output_overlap')
    intent = dict(schema=SCHEMA, selection=dict(selection_pin), binding=binding)
    retained = runs/binding['runner']['slot_id']/'receipt.json'

    def receipt_for(content):
        spec_path = retained.parent/'spec.json'
        spec_pin = _pin(spec_path) if spec_path.exists() else None
        if content['status'] == 'complete':
            need(spec_pin is not None, 'bridge_runner_spec_missing')
            verify(paired.read_json_pin(spec_pin))
            pair = paired.read_json_pin({k:content['pair_binding'][k] for k in ('path','sha256')})
            need(pair['request_id'] == binding['runner']['request_id'] and
                 pair['request_sha256'] == spec_pin['sha256'] and
                 pair['occurrence_id'] == binding['runner']['occurrence_id'] and
                 pair['schedule_sha256'] == binding['runner']['schedule_sha256'], 'bridge_runner_binding_mismatch')
            # A complete run must have observed exactly the declared native bytes and effort.
            observed = (content.get('original_execution') or {}).get('observed') or {}
            actor_key='provider' if execution['schema']==weekly_input.CODEX_EXECUTION_SCHEMA else 'native'
            need((observed.get(actor_key) or {}).get('sha256') == actor['binary']['sha256'] and
                 observed.get('reasoning_effort') == actor['effort'], 'bridge_runner_execution_mismatch')
        return dict(schema=RECEIPT, selection=dict(selection_pin), binding=binding, status=content['status'],
                    runner_spec=spec_pin, runner_receipt=_pin(retained), runner=content, runtime_admitted=False)

    def verify(spec):
        need(_load(selection_pin, now)[1] == binding, 'bridge_source_changed')
        need(spec['slot_id'] == binding['runner']['slot_id'] and spec['planned'] == planned and
             spec['schedule_sha256'] == binding['runner']['schedule_sha256'] and
             spec['harness']['commit'] == binding['harness_commit'] and
             spec['account'] == profile['account'], 'bridge_runner_mismatch')
        declared = spec['original_execution']
        actor_key='provider' if execution['schema']==weekly_input.CODEX_EXECUTION_SCHEMA else 'native'
        need(declared['execution'] == binding['execution'] and declared[actor_key] == actor['binary'] and
             declared['model'] == spec['actor_model'] == actor['model'] and
             declared['effort'] == actor['effort'], 'bridge_runner_mismatch')
        # The runner prepares the reviewed embedding config; the declared worker stays separate.
        reviewed = execution['memory_arms']['CURRENT']
        need(spec['embedding']['declared_worker'] == reviewed['embedding_worker'] and
             spec['embedding']['selected_config'] == reviewed['embedding_config'] and
             spec['embedding']['model_identity'] == reviewed['embedding_model_identity'], 'bridge_runner_mismatch')
        for arm, wanted in execution['memory_arms'].items():
            actual = spec['arms'][arm]
            need(actual['commit'] == binding['commits'][arm], 'bridge_runner_mismatch')
            for name in ('binary','hook','bridge'):
                paired.pin(actual[name])
                need(actual[name]['sha256'] == wanted[name]['sha256'], 'bridge_rebuilt_artifact_mismatch')

    available = weekly.default_executors(profile.get('account')) if executors is None else executors
    guarded = {key:_Executor(value, verify) for key,value in available.items()}
    with weekly._runs_lock(output.parent):
        if output.exists():
            need(paired.read_json(output/'intent.json') == intent, 'bridge_intent_conflict')
            if (output/'receipt.json').exists():
                receipt = paired.read_json(output/'receipt.json')
                content = paired.read_json(retained)
                need(receipt == receipt_for(content), 'bridge_receipt_changed')
                return receipt
        else:
            output.mkdir(mode=0o700)
            paired.write_selected(output/'intent.json', intent)
        result = runner(profile, now=now, executors=guarded, compiler=compiler)
        content = paired.read_json(retained)
        need({k:v for k,v in result.items() if k != 'replayed'} == content and
             content['slot_id'] == binding['runner']['slot_id'] and
             content['current']['commit'] == binding['commits']['CURRENT'] and
             content['candidate']['commit'] == binding['commits']['CAND'] and
             content['harness_commit'] == binding['harness_commit'], 'bridge_runner_receipt_mismatch')
        receipt = receipt_for(content)
        paired.write_selected(output/'receipt.json', receipt)
        return receipt


def run(selection_pin, output, *, now, executors=None, compiler=None, runner=None):
    """Invoke the existing runner from a pinned handoff; injections are test seams."""
    try:
        return _run(selection_pin, output, now, executors, compiler, runner or weekly.run)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        raise paired.Refusal('bridge_invalid_or_unavailable') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        result = run(dict(path=args.selection,sha256=args.sha256), args.output, now=dt.datetime.now(dt.timezone.utc))
    except paired.Refusal as error:
        print(json.dumps(dict(status='refused', code=str(error))))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
