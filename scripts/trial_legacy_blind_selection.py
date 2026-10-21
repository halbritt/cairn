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
Indeterminate pairs are listed and block changed-pair completion. A run with
no changed pair gets a receipt only (no selection or packet); it reports
no_grading_needed only when every pair is determinate and unchanged.

The converter reads the selected answer/actions, the attempt identity and the
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
import trial_task_permissions as permissions

REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/1'
ACTION_REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/2'
BINDING_SCHEMA = 'cairn.legacy-pair-binding/1'
RECEIPT_SCHEMA = 'cairn.legacy-blind-conversion-receipt/1'
AGENT_SCHEMA = 'cairn.task-eval.agent/1'
DETERMINISTIC = frozenset(('correct', 'mistake', 'incomplete'))
GRADED = DETERMINISTIC | {'undetermined', 'needs_review'}
# The protected initial baseline is not final artifact/evaluator custody.
# Only /2 explicitly selects the retained-stream action projection.
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
    # An empty plan or record set would pass every coverage check vacuously and
    # then classify as no_grading_needed. The weekly 126/63 shape is the
    # runner's check; this converter only forbids empty runs.
    need(plan['runs'] and records, 'legacy_run_empty')
    planned = [tuple(row[:3]) for row in plan['runs'] if isinstance(row, list) and len(row) == 4]
    need(len(planned) == len(plan['runs']) and len(set(planned)) == len(planned), 'legacy_plan_mismatch')
    need(set(planned) == {(r['case'], r['arm'], r['seed']) for r in records}, 'legacy_attempt_missing')
    pairs = {}
    for record in records:
        pairs.setdefault((record['case'], record['seed']), {})[record['arm']] = record
    need(all(set(cell) == set(agent['arms']) for cell in pairs.values()), 'legacy_attempt_missing')
    need(pairs, 'legacy_run_empty')
    return pairs


def _classify(cell):
    outcomes = [record['outcome'] for record in cell.values()]
    if not all(o in DETERMINISTIC for o in outcomes):
        return 'indeterminate'
    return 'changed' if len(set(outcomes)) > 1 else 'unchanged'


JOIN_STATES = dict(changed='changed', unchanged='unchanged', indeterminate='undetermined')


def _grading(counts):
    """What changed-pair grading still requires. Only a run whose every pair is
    determinate and unchanged needs no grading; indeterminate pairs block it."""
    if counts['indeterminate']:
        if not counts['changed'] and not counts['unchanged']:
            return dict(state='incomplete_all_pairs_indeterminate', complete=False, deliveries_required=None,
                        reason='no_pair_classified')
        # Changed pairs still need their deliveries; the run cannot complete either way.
        return dict(state='blocked_indeterminate_pairs', complete=False,
                    deliveries_required=2*counts['changed'] if counts['changed'] else None,
                    reason='indeterminate_pairs_present')
    if counts['changed']:
        # One delivery to each of the two blind grader slots per changed pair.
        return dict(state='awaiting_grading', complete=False, deliveries_required=2*counts['changed'],
                    reason='changed_pairs_selected')
    return dict(state='no_grading_needed', complete=True, deliveries_required=0,
                reason='all_pairs_determinate_unchanged')


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
        supported = (*BOUND_CHANNELS, 'action_trace') if request['schema'] == ACTION_REQUEST_SCHEMA else BOUND_CHANNELS
        need(isinstance(channels, list) and set(channels) <= set(supported), 'legacy_channel_unbound')
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


def _stream(run_dir, record):
    """Hash the retained UTF-8 representation, not claimed original pipe bytes."""
    content = _read_path(run_dir/'runs'/record['run_id']/'stream.jsonl')
    need(hashlib.sha256(content).hexdigest() == record['stream_sha256'], 'legacy_attempt_content_mismatch')
    return content


def _action_parser(request, plan):
    if request['schema'] != ACTION_REQUEST_SCHEMA:
        return None
    selected = request['action_parser']
    paired.shape(selected, 'schema task_eval task_permissions', 'legacy_action_parser_shape')
    need(selected['schema'] == 'cairn.legacy-action-parser/1', 'legacy_action_parser_shape')
    for name, module in (('task_eval', task_eval), ('task_permissions', permissions)):
        # Do not execute caller-selected source. The existing imported parser
        # and its permission helpers must have these exact quiescent source bytes.
        need(_read(selected[name]) == _read_path(module.__file__), 'legacy_action_parser_mismatch')
    need(plan.get('evaluator_sha256') == selected['task_eval']['sha256'], 'legacy_action_parser_mismatch')
    return selected


def _action_projection(trace, text, harness):
    commands = trace['commands']
    outputs = task_eval.parse_tool_outputs(text, harness, require_completed=False)
    need(all(type(c) is str for c in commands) and all(
        type(row['command']) is str and type(row['output']) is str and type(row['is_error']) is bool
        for row in outputs), 'legacy_action_shape')
    content = packet.canonical_bytes(dict(schema='cairn.legacy-action-trace/1',
                                          commands=commands, tool_outputs=outputs))
    need(len(content) <= paired.ORIGINAL_LIMITS['tool_room_bytes'], 'legacy_action_bound')
    return content


def _retain(files, output, name, content):
    files[name] = content
    return dict(path=str(output/name), **_digest(content))


def _entry(name, reference):
    return dict(name=name, kind='content', representation='bytes',
                media_type='application/json' if name == 'action_trace' else 'text/plain',
                input=dict(reference), transform=dict(kind='exact_copy'),
                output={'bytes': reference['bytes'], 'sha256': reference['sha256']})


def _plan(request_pin, output):
    request = _json(_read(request_pin))
    need(isinstance(request, dict) and request.get('schema') in (REQUEST_SCHEMA, ACTION_REQUEST_SCHEMA),
         'legacy_request_shape')
    extension = ' action_parser' if request['schema'] == ACTION_REQUEST_SCHEMA else ''
    paired.shape(request, 'schema agent plan binding namespace policy policy_ref review_measurement cases'+extension,
                 'legacy_request_shape')
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
    parser = _action_parser(request, plan)
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
    record_pins = {}
    for run_id, record in by_run.items():
        path = run_dir/'runs'/run_id/'result.json'
        raw = _read_path(path)
        retained = _json(raw)
        # Legacy records carry floats, so compare exact sorted JSON, not blind canonical bytes.
        need(_exact(retained) == _exact(record), 'legacy_attempt_mismatch')
        record_pins[run_id] = dict(path=str(path), **_digest(raw))

    classes = {key: _classify(cell) for key, cell in pairs.items()}
    changed = sorted(key for key, value in classes.items() if value == 'changed')
    # Every coverage, identity, provenance and binding check above has run.
    # With no changed pair there is nothing to select: no blind selection or
    # packet is produced (the packet contract stays nonempty), and the receipt
    # alone classifies the run. See _grading for when that is complete.
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
            content = _stream(run_dir, record)
            text = content.decode('utf-8', errors='replace')
            harness = record.get('harness', 'claude')
            trace = task_eval.parse_stream(text, harness)
            stream_sha = record['stream_sha256']
            sample_id = 'sample-' + hashlib.sha256(packet.canonical_bytes(
                [namespace, pair_key, record['run_id']])).hexdigest()[:32]
            source = dict(schema='cairn.blind-source/2', **pair_key, arm=roles[arm], run_id=record['run_id'])
            sources = dict(task_statement=chosen['task_statement'], grading_instructions=chosen['grading_instructions'])
            observations, observed = {}, dict(stream_sha256=stream_sha)
            if 'final_answer' in chosen['channels']:
                answer = trace['answer'] or ''
                need(len(answer) == record.get('answer_chars'), 'legacy_answer_mismatch')
                answer = answer.encode('utf-8')
                sources['final_answer'] = _retain(files, output, 'answers/'+sample_id+'.txt', answer)
                observed['final_answer'] = _digest(answer)
            if 'action_trace' in chosen['channels']:
                sources['action_trace'] = _retain(files, output, 'actions/'+sample_id+'.json',
                                                   _action_projection(trace, text, harness))
                subject = dict(schema='cairn.legacy-action-subject/1', source=source, parser=parser,
                    agent=request['agent'], plan=request['plan'], binding=request['binding'],
                    attempt=record_pins[record['run_id']], harness=harness,
                    stream=dict(path=str(run_dir/'runs'/record['run_id']/'stream.jsonl'), **_digest(content)),
                    prompt=dict(bytes=record['prompt_bytes'], sha256=record['prompt_sha256']),
                    stream_representation='legacy-retained-utf8/1', custody='harness_recorded_hash')
                subject_pin = _retain(files, output, 'subjects/'+sample_id+'.json', packet.canonical_bytes(subject))
                observation = dict(schema='cairn.blind-evaluator-observation/2', source=source,
                    channel='action_trace', evidence=[sources['action_trace']],
                    role='evaluator_observation', subject=subject_pin)
                observations['action_trace'] = _retain(files, output, 'observations/'+sample_id+'.json',
                                                       packet.canonical_bytes(observation))
                observed['action_trace'] = dict(projection=sources['action_trace'], subject=subject_pin,
                                                observation=observations['action_trace'])
            samples.append(dict(
                source=source,
                sample_id=sample_id, case_contract_sha256=chosen['contract']['sha256'],
                channels=[dict(name=channel, entries=[_entry(channel, sources[channel])], observation=observations.get(channel))
                          for channel in chosen['channels']]))
            # Declared: hashes the legacy harness wrote into its own record (same
            # process, pinned only through the binding's agent.json hash).
            # Observed: what this conversion read now. Neither is independent custody.
            attempts.append(dict(run_id=record['run_id'], sample_id=sample_id,
                                 declared_by_harness=dict(stream_sha256=record['stream_sha256'],
                                                          prompt_sha256=record['prompt_sha256']),
                                 observed_now=observed))
            ids.append(sample_id)
        comparisons.append(dict(
            comparison_id='pair-' + hashlib.sha256(packet.canonical_bytes([namespace, pair_key])).hexdigest()[:32],
            pair_key=pair_key, samples=sorted(ids)))
    selection_pin = None
    comparisons = sorted(comparisons, key=lambda c: c['comparison_id'])
    if changed:
        selection = dict(schema='cairn.blind-selection/2', namespace=namespace, policy=request['policy'],
                         policy_ref=request['policy_ref'], case_contracts=[contracts[k] for k in sorted(contracts)],
                         samples=sorted(samples, key=lambda s: s['sample_id']), comparisons=comparisons,
                         review_measurement=request['review_measurement'])
        files['selection.json'] = packet.canonical_bytes(selection)
        selection_pin = dict(path=str(output/'selection.json'), **_digest(files['selection.json']))
    indeterminate = [dict(case=case, seed=seed) for (case, seed), value in sorted(classes.items())
                     if value == 'indeterminate']
    counts = {name: sum(1 for v in classes.values() if v == name) for name in ('changed', 'unchanged', 'indeterminate')}
    receipt = dict(schema=RECEIPT_SCHEMA, request=dict(request_pin),
                   sources=dict(agent=request['agent'], plan=request['plan'], binding=request['binding']),
                   selection=selection_pin,
                   pairs=dict(total=len(classes), **counts),
                   # Join vocabulary (trial_grading_custody bundle selection rows).
                   pair_classification=[dict(case=case, seed=seed, state=JOIN_STATES[value])
                                        for (case, seed), value in sorted(classes.items())],
                   changed_pair_grading=_grading(counts),
                   comparisons=[c['comparison_id'] for c in comparisons],
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
        if receipt['selection'] is not None:
            for directory in sorted({Path(name).parent for name in files if Path(name).parent != Path('.')}):
                (output/directory).mkdir(mode=0o700)
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
