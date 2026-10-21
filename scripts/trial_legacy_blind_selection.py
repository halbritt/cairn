"""Legacy task-eval output -> cairn.blind-selection/2 for changed pairs. CAIRN-107.

Converts the legacy harness output (cairn.task-eval.agent/1 agent.json, its
plan.json and runs/<run_id>/) into the selection consumed unchanged by
trial_blind_packet_draft, and from there trial_grader_delivery_plan and
trial_grader_delivery. See docs/legacy-blind-selection.md.

Pair identity is a binding seam. blind-source/2 keys every pair to a paired
request (request_id, request_sha256, campaign_id, occurrence_id,
schedule_sha256) that legacy output does not carry, and to CURRENT/CAND roles
that legacy memory labels do not name. Without a pinned
cairn.legacy-pair-binding/1 naming the exact agent.json and plan.json hashes,
conversion refuses legacy_pair_identity_unbound. A binding is a declaration:
it is not request admission, release authority, delivery or grading, and the
receipt says so.

Original-attempt content needs the stream_sha256 and prompt_sha256 that
trial_task_eval.run_agent records per attempt. Older output lacks them and
refuses original_attempt_provenance_unavailable. Those hashes are the
harness's own record, reported as declared, not as independent custody.
Indeterminate pairs are listed and block changed-pair completion.

The converter reads only the selected answer, the attempt identity and the
deterministic outcome of each legacy record. It never copies check verdicts,
check logs, memory evidence or any other record field. Task statements,
grading instructions and case contracts are supplied by the caller as pins.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import trial_blind_packet_draft as packet
import trial_paired_request as paired
import trial_task_eval as task_eval

REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/1'
BINDING_SCHEMA = 'cairn.legacy-pair-binding/1'
RECEIPT_SCHEMA = 'cairn.legacy-blind-conversion-receipt/1'
AGENT_SCHEMA = 'cairn.task-eval.agent/1'
DETERMINISTIC = frozenset(('correct', 'mistake', 'incomplete'))
GRADED = DETERMINISTIC | {'undetermined', 'needs_review'}
# Channels legacy output can bind exactly. Artifact bundles would need the
# workspace base that legacy prepare_workspace does not verify, and traces or
# evaluator observations need an evaluator-observation contract.
BOUND_CHANNELS = ('task_statement', 'grading_instructions', 'final_answer')
INPUT_LIMIT = 64 * 1024 * 1024
need = paired.need


def _digest(content):
    return dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def _read_path(path, limit=INPUT_LIMIT):
    path = paired.plain_path(str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'regular_file_required')
        content = source.read(limit + 1)
    need(len(content) <= limit, 'legacy_input_limit')
    return content


def _read(reference):
    need(isinstance(reference, dict) and set(reference) == {'path', 'bytes', 'sha256'}, 'legacy_pin_shape')
    content = _read_path(reference['path'])
    need(_digest(content) == {'bytes': reference['bytes'], 'sha256': reference['sha256']}, 'legacy_pin_mismatch')
    return content


def _json(content):
    return paired.decode_json(content.decode('utf-8', errors='strict'))


def _exact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _admission(agent, records):
    admission = agent.get('admission')
    need(isinstance(admission, dict) and admission.get('state') == 'complete' and admission.get('stop') is None and
         admission.get('not_started') == [] and admission.get('planned') == admission.get('admitted') == len(records),
         'legacy_run_incomplete')


def _identity(records, arms):
    """run_id is case.arm.sSEED (trial_task_eval.run_agent). That map is injective
    only when neither case nor arm contains a dot; otherwise refuse."""
    seen = {}
    for record in records:
        need(isinstance(record, dict), 'legacy_attempt_identity_ambiguous')
        case, arm, seed = record.get('case'), record.get('arm'), record.get('seed')
        need(type(case) is str and type(arm) is str and type(seed) is int and seed >= 0 and
             case and '.' not in case and '/' not in case and arm in arms and '.' not in arm and
             record.get('run_id') == f'{case}.{arm}.s{seed}' and record['run_id'] not in seen,
             'legacy_attempt_identity_ambiguous')
        seen[record['run_id']] = record
    return seen


def _coverage(plan, agent, records):
    need(isinstance(plan, dict) and plan.get('arms') == agent['arms'] and isinstance(plan.get('runs'), list),
         'legacy_plan_mismatch')
    planned = [tuple(row[:3]) for row in plan['runs'] if isinstance(row, list) and len(row) == 4]
    need(len(planned) == len(plan['runs']) and len(set(planned)) == len(planned), 'legacy_plan_mismatch')
    need(set(planned) == {(r['case'], r['arm'], r['seed']) for r in records}, 'legacy_attempt_missing')
    pairs = {}
    for record in records:
        pairs.setdefault((record['case'], record['seed']), {})[record['arm']] = record
    need(all(set(cell) == set(agent['arms']) for cell in pairs.values()), 'legacy_attempt_missing')
    return pairs


def _classify(cell):
    outcomes = [record['outcome'] for record in cell.values()]
    if not all(o in DETERMINISTIC for o in outcomes):
        return 'indeterminate'
    return 'changed' if len(set(outcomes)) > 1 else 'unchanged'


def _materials(request, changed):
    cases = request['cases']
    need(isinstance(cases, list), 'legacy_materials')
    by_case = {}
    for entry in cases:
        need(isinstance(entry, dict) and set(entry) == {'case', 'contract', 'task_statement', 'grading_instructions'} and
             entry['case'] not in by_case, 'legacy_materials')
        by_case[entry['case']] = entry
    selected = {}
    for case in sorted({case for case, _ in changed}):
        need(case in by_case, 'legacy_materials_missing')
        entry = by_case[case]
        contract = _json(_read(entry['contract']))
        need(isinstance(contract, dict) and contract.get('case_key') == case, 'legacy_case_mismatch')
        channels = contract.get('required_channels')
        need(isinstance(channels, list) and set(channels) <= set(BOUND_CHANNELS), 'legacy_channel_unbound')
        task = _read(entry['task_statement']); _read(entry['grading_instructions'])
        selected[case] = dict(entry, channels=channels, task_bytes=len(task),
                              task_sha256=hashlib.sha256(task).hexdigest())
    return selected


def _provenance(records):
    """Original-attempt content custody: the stream and prompt hashes the
    harness recorded when it wrote the attempt (trial_task_eval.run_agent).
    Output from before that record existed has no provenance; lengths alone
    never stand in for it."""
    for record in records:
        need(all(isinstance(record.get(k), str) and len(record[k]) == 64 and
                 all(c in '0123456789abcdef' for c in record[k]) for k in ('stream_sha256', 'prompt_sha256')),
             'original_attempt_provenance_unavailable')


def _answer(run_dir, record):
    """The selected final answer, parsed by the harness's own stream parser,
    from the exact stream bytes the harness recorded for this attempt."""
    content = _read_path(run_dir/'runs'/record['run_id']/'stream.jsonl')
    need(hashlib.sha256(content).hexdigest() == record['stream_sha256'], 'legacy_attempt_content_mismatch')
    answer = task_eval.parse_stream(content.decode('utf-8', errors='replace'), record.get('harness', 'claude'))['answer']
    answer = answer or ''
    need(len(answer) == record.get('answer_chars'), 'legacy_answer_mismatch')
    return answer.encode('utf-8'), _digest(content)['sha256']


def _entry(name, reference):
    return dict(name=name, kind='content', representation='bytes', media_type='text/plain',
                input=dict(reference), transform=dict(kind='exact_copy'),
                output={'bytes': reference['bytes'], 'sha256': reference['sha256']})


def _plan(request_pin, output):
    request = _json(_read(request_pin))
    paired.shape(request, 'schema agent plan binding namespace policy policy_ref review_measurement cases',
                 'legacy_request_shape')
    need(request['schema'] == REQUEST_SCHEMA, 'legacy_request_shape')
    need(request['binding'] is not None, 'legacy_pair_identity_unbound')
    agent = _json(_read(request['agent']))
    need(isinstance(agent, dict) and agent.get('schema') == AGENT_SCHEMA and isinstance(agent.get('records'), list),
         'legacy_agent_schema')
    need(agent.get('wording') == 'task', 'legacy_wording_unsupported')
    arms = agent.get('arms')
    need(isinstance(arms, list) and len(arms) == 2 and len(set(arms)) == 2, 'legacy_arms_unsupported')
    run_dir = Path(request['agent']['path']).parent
    need(request['plan']['path'] == str(run_dir/'plan.json'), 'legacy_plan_mismatch')
    plan = _json(_read(request['plan']))
    binding = _json(_read(request['binding']))
    paired.shape(binding, 'schema agent_sha256 plan_sha256 request_id request_sha256 campaign_id occurrence_id '
                 'schedule_sha256 arm_roles', 'legacy_binding_mismatch')
    roles = binding['arm_roles']
    need(binding['schema'] == BINDING_SCHEMA and binding['agent_sha256'] == request['agent']['sha256'] and
         binding['plan_sha256'] == request['plan']['sha256'] and isinstance(roles, dict) and
         set(roles) == set(arms) and sorted(roles.values()) == ['CAND', 'CURRENT'], 'legacy_binding_mismatch')

    records = agent['records']
    _admission(agent, records)
    by_run = _identity(records, arms)
    pairs = _coverage(plan, agent, records)
    need(all(r.get('outcome') in GRADED for r in records), 'legacy_attempt_not_graded')
    _provenance(records)
    for run_id, record in by_run.items():
        retained = _json(_read_path(run_dir/'runs'/run_id/'result.json'))
        # Legacy records carry floats, so compare exact sorted JSON, not blind canonical bytes.
        need(_exact(retained) == _exact(record), 'legacy_attempt_mismatch')

    classes = {key: _classify(cell) for key, cell in pairs.items()}
    changed = sorted(key for key, value in classes.items() if value == 'changed')
    need(changed, 'legacy_no_changed_pairs')
    materials = _materials(request, changed)

    namespace = request['namespace']
    identity = {k: binding[k] for k in ('request_id', 'request_sha256', 'campaign_id', 'occurrence_id', 'schedule_sha256')}
    files, samples, comparisons, contracts, attempts = {}, [], [], {}, []
    for case, seed in changed:
        chosen = materials[case]
        contracts[chosen['contract']['sha256']] = chosen['contract']
        pair_key = dict(identity, case=case, seed=seed, wording='task')
        ids = []
        for arm in arms:
            record = pairs[(case, seed)][arm]
            need(record['prompt_sha256'] == chosen['task_sha256'] and record.get('prompt_bytes') == chosen['task_bytes'],
                 'legacy_task_statement_mismatch')
            answer, stream_sha = _answer(run_dir, record)
            sample_id = 'sample-' + hashlib.sha256(packet.canonical_bytes(
                [namespace, pair_key, record['run_id']])).hexdigest()[:32]
            name = 'answers/' + sample_id + '.txt'
            files[name] = answer
            answer_pin = dict(path=str(output/name), **_digest(answer))
            sources = dict(task_statement=chosen['task_statement'], grading_instructions=chosen['grading_instructions'],
                           final_answer=answer_pin)
            samples.append(dict(
                source=dict(schema='cairn.blind-source/2', **pair_key, arm=roles[arm], run_id=record['run_id']),
                sample_id=sample_id, case_contract_sha256=chosen['contract']['sha256'],
                channels=[dict(name=channel, entries=[_entry(channel, sources[channel])], observation=None)
                          for channel in chosen['channels']]))
            # Declared: hashes the legacy harness wrote into its own record (same
            # process, pinned only through the binding's agent.json hash).
            # Observed: what this conversion read now. Neither is independent custody.
            attempts.append(dict(run_id=record['run_id'], sample_id=sample_id,
                                 declared_by_harness=dict(stream_sha256=record['stream_sha256'],
                                                          prompt_sha256=record['prompt_sha256']),
                                 observed_now=dict(stream_sha256=stream_sha, final_answer=_digest(answer))))
            ids.append(sample_id)
        comparisons.append(dict(
            comparison_id='pair-' + hashlib.sha256(packet.canonical_bytes([namespace, pair_key])).hexdigest()[:32],
            pair_key=pair_key, samples=sorted(ids)))
    selection = dict(schema='cairn.blind-selection/2', namespace=namespace, policy=request['policy'],
                     policy_ref=request['policy_ref'], case_contracts=[contracts[k] for k in sorted(contracts)],
                     samples=sorted(samples, key=lambda s: s['sample_id']),
                     comparisons=sorted(comparisons, key=lambda c: c['comparison_id']),
                     review_measurement=request['review_measurement'])
    files['selection.json'] = packet.canonical_bytes(selection)
    indeterminate = [dict(case=case, seed=seed) for (case, seed), value in sorted(classes.items())
                     if value == 'indeterminate']
    counts = {name: sum(1 for v in classes.values() if v == name) for name in ('changed', 'unchanged', 'indeterminate')}
    receipt = dict(schema=RECEIPT_SCHEMA, request=dict(request_pin),
                   sources=dict(agent=request['agent'], plan=request['plan'], binding=request['binding']),
                   selection=dict(path=str(output/'selection.json'), **_digest(files['selection.json'])),
                   pairs=dict(total=len(classes), **counts),
                   comparisons=[c['comparison_id'] for c in selection['comparisons']],
                   unselected=sorted([case, seed, value] for (case, seed), value in classes.items() if value != 'changed'),
                   # A pair that cannot be classified may hide a change; changed-pair
                   # grading cannot be complete while any such pair exists.
                   changed_pair_coverage=dict(
                       state='blocked_indeterminate_pairs' if indeterminate else 'all_pairs_classified',
                       complete_possible=not indeterminate, indeterminate=indeterminate),
                   attempts=sorted(attempts, key=lambda a: a['run_id']),
                   changed_pair_definition='both deterministic outcomes (correct, mistake, incomplete) and unequal',
                   custody=dict(pair_identity='declared_binding', attempt_content='harness_recorded_hash',
                                independent_attempt_custody=False),
                   pair_identity='declared_binding', runtime_admitted=False, release_authority_verified=False,
                   delivery_available=False, grading_performed=False)
    files['receipt.json'] = packet.canonical_bytes(receipt)
    return receipt, files


def _existing(output, files):
    present = {str(p.relative_to(output)): p for p in output.rglob('*') if p.is_file() or p.is_symlink()}
    need(set(present) == set(files) and all(not p.is_symlink() and p.read_bytes() == files[n]
                                            for n, p in present.items()), 'legacy_output_conflict')


def _convert(request_pin, output):
    output = paired.plain_path(str(output))
    receipt, files = _plan(request_pin, output)
    if output.exists():
        _existing(output, files)
        return receipt
    output.mkdir(mode=0o700)
    try:
        (output/'answers').mkdir(mode=0o700)
        for name in sorted(files):
            if name != 'receipt.json':
                paired.write_selected_bytes(output/name, files[name])
        # The unchanged draft contract is the acceptance check for composition.
        packet.approval_subject(receipt['selection'])
        paired.write_selected_bytes(output/'receipt.json', files['receipt.json'])
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return receipt


def convert(request_pin, output):
    """Write a pair-keyed, arm-blinded selection for changed pairs. Never admits,
    releases, delivers or grades anything."""
    try:
        return _convert(request_pin, output)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise paired.Refusal('legacy_conversion_invalid_or_unavailable') from None


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--request', required=True, help='conversion request JSON path')
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        content = _read_path(args.request)
        receipt = convert(dict(path=args.request, bytes=len(content), sha256=args.request_sha256), Path(args.output))
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    print(json.dumps(dict(status='converted', selection=receipt['selection'], pairs=receipt['pairs'],
                          runtime_admitted=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
