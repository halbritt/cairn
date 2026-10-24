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
import base64
import json
import os
from pathlib import Path
import shutil
import stat

import trial_blind_packet_draft as packet
import trial_paired_request as paired
import trial_task_eval as task_eval
import trial_task_permissions as permissions
import trial_legacy_case_materials as case_materials

REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/1'
ACTION_REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/2'
SELECTED_REQUEST_SCHEMA = 'cairn.legacy-blind-conversion/3'
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


def _materials(request, changed, plan):
    if request['schema'] == SELECTED_REQUEST_SCHEMA:
        manifest = case_materials.read_manifest(request['case_materials'],
                                                evaluator_sha256=plan.get('evaluator_sha256'))
        by_case = {row['case']: row for row in manifest['cases']}
        result = {}
        for case in sorted({case for case, _ in changed}):
            need(case in by_case, 'legacy_materials_missing')
            row = by_case[case]
            result[case] = dict(case=case, contract=row['contract'],
                                task_statement=row['task_statement'],
                                grading_instructions=row['grading_instructions'],
                                channels=row['channels'], unavailable_channels=row['unavailable_channels'],
                                manifest=request['case_materials'],
                                evaluator_contract=manifest['evaluator_observation_contract'],
                                source_case_sha256=row['source_case_sha256'],
                                task_bytes=row['task_statement']['bytes'],
                                task_sha256=row['task_statement']['sha256'])
        return result
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
        supported = ((*BOUND_CHANNELS, 'action_trace') if request['schema'] == ACTION_REQUEST_SCHEMA else
                     (*BOUND_CHANNELS, 'action_trace', 'artifact_bundle', 'evaluator_observation')
                     if request['schema'] == SELECTED_REQUEST_SCHEMA else BOUND_CHANNELS)
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
    if request['schema'] not in (ACTION_REQUEST_SCHEMA, SELECTED_REQUEST_SCHEMA):
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
                media_type='application/json' if name in ('action_trace', 'evaluator_observation') else 'text/plain',
                input=dict(reference), transform=dict(kind='exact_copy'),
                output={'bytes': reference['bytes'], 'sha256': reference['sha256']})


def _artifact_entry(reference, content):
    return dict(name=case_materials.ARTIFACT_NAME, kind='file', representation='bytes',
                media_type='application/json', input=dict(reference),
                transform=dict(kind='exact_copy'), output=_digest(content))


def _pregrade_snapshot(run_dir, record, case, stream_bytes):
    """Verify the producer-issued pre-grade snapshot and make a blind artifact view."""
    custody_pin = record.get('final_artifact_custody')
    need(isinstance(custody_pin, dict) and set(custody_pin) ==
         {'schema', 'record_path', 'bytes', 'sha256'} and
         custody_pin['schema'] == 'cairn.legacy-pregrade-snapshot-pin/1',
         'legacy_pregrade_snapshot_unavailable')
    run_id = record['run_id']
    relative = Path(custody_pin['record_path'])
    expected_path = Path('final-artifacts') / (hashlib.sha256(run_id.encode()).hexdigest()+'.json')
    need(not relative.is_absolute() and relative == expected_path and
         all(part not in ('', '.', '..') for part in relative.parts), 'legacy_pregrade_snapshot_path')
    raw = _read_path(run_dir/relative, limit=paired.FILE_LIMIT)
    need(_digest(raw) == {k: custody_pin[k] for k in ('bytes', 'sha256')},
         'legacy_pregrade_snapshot_mismatch')
    snapshot = _json(raw)
    need(isinstance(snapshot, dict) and snapshot.get('schema') == 'cairn.legacy-pregrade-snapshot/1' and
         set(snapshot) == {'schema', 'attempt', 'baseline', 'stream', 'prompt', 'writer_release',
                           'workspace', 'git', 'bytes_captured'}, 'legacy_pregrade_snapshot_shape')
    need(snapshot['attempt'] == dict(run_id=run_id, case=record['case'], arm=record['arm'],
                                     seed=record['seed'], workspace=snapshot['workspace'].get('path')),
         'legacy_pregrade_snapshot_attempt')
    try:
        baseline = task_eval.AttemptBaseline.load(run_dir/'runs'/run_id, case, record['arm'],
                                                  record['seed'], record.get('baseline_custody'))
    except (ValueError, OSError, TypeError, KeyError):
        raise paired.Refusal('legacy_pregrade_baseline_invalid') from None
    need(snapshot['baseline'] == dict(pin=record.get('baseline_custody'),
                                      base_commit=baseline.base_commit,
                                      repository=baseline.repository) and
         snapshot['stream'] == dict(bytes=len(stream_bytes), sha256=record['stream_sha256']) and
         hashlib.sha256(stream_bytes).hexdigest() == record['stream_sha256'] and
         snapshot['prompt'] == dict(bytes=record.get('prompt_bytes'), sha256=record['prompt_sha256']),
         'legacy_pregrade_snapshot_inputs')
    need(snapshot['workspace'].get('path') == str(run_dir/'runs'/run_id/'work'/case['cwd']) and
         snapshot['attempt']['workspace'] == snapshot['workspace']['path'],
         'legacy_pregrade_snapshot_workspace')
    writer_path = run_dir/'runs'/run_id/'writer-release.json'
    writer_raw = _read_path(writer_path)
    writer = _json(writer_raw)
    need(_exact(writer) == _exact(record.get('writer_release')) and
         writer.get('final_artifact_custody') == custody_pin, 'legacy_pregrade_writer_binding')
    projection = {key: value for key, value in writer.items()
                  if key not in ('final_artifact_custody', 'final_artifact_snapshot')}
    release_sha = hashlib.sha256(json.dumps(projection, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()
    need(snapshot['writer_release'].get('sha256') == release_sha and
         snapshot['writer_release'].get('state') == writer.get('state') and
         snapshot['writer_release'].get('writer_quiescence') == writer.get('writer_quiescence'),
         'legacy_pregrade_writer_binding')
    workspace = snapshot['workspace']
    need(isinstance(workspace, dict) and type(workspace.get('entries')) is list and
         type(workspace.get('deletions')) is list and len(workspace['entries']) <= 4096 and
         type(snapshot['git']) is dict and type(snapshot['git'].get('index_refs')) is list,
         'legacy_pregrade_snapshot_shape')
    entries = []
    for row in workspace['entries']:
        need(isinstance(row, dict) and type(row.get('path_b64')) is str and
             type(row.get('mode')) is int and 0 <= row['mode'] <= 0o777 and
             row.get('kind') in ('file', 'directory', 'symlink'), 'legacy_pregrade_snapshot_entry')
        try:
            path_bytes = base64.b64decode(row['path_b64'], validate=True)
        except (ValueError, TypeError):
            raise paired.Refusal('legacy_pregrade_snapshot_entry') from None
        need(path_bytes and b'\x00' not in path_bytes and not path_bytes.startswith(b'/') and
             b'..' not in path_bytes.split(b'/'), 'legacy_pregrade_snapshot_entry')
        projected = {k: row[k] for k in ('path_b64', 'mode', 'kind')}
        if row['kind'] == 'file':
            data = _decode_content(row.get('content_b64'), row.get('bytes'), row.get('sha256'))
            projected.update(bytes=len(data), sha256=row['sha256'], content_b64=row['content_b64'])
        elif row['kind'] == 'symlink':
            target = _decode_content(row.get('target_b64'), row.get('bytes'), row.get('sha256'))
            projected.update(bytes=len(target), sha256=row['sha256'], target_b64=row['target_b64'])
        else:
            need(set(row) == {'path_b64', 'mode', 'kind'}, 'legacy_pregrade_snapshot_entry')
        entries.append(projected)
    deletions = workspace['deletions']
    need(type(deletions) is list and len(deletions) <= 4096, 'legacy_pregrade_snapshot_deletions')
    for deleted in deletions:
        try:
            path_bytes = base64.b64decode(deleted, validate=True)
        except (ValueError, TypeError):
            raise paired.Refusal('legacy_pregrade_snapshot_deletions') from None
        need(path_bytes and b'\x00' not in path_bytes and not path_bytes.startswith(b'/') and
             b'..' not in path_bytes.split(b'/'), 'legacy_pregrade_snapshot_deletions')
    git_rows = []
    for row in snapshot['git']['index_refs']:
        need(isinstance(row, dict) and type(row.get('path_b64')) is str and
             row.get('kind') in ('file', 'absent'), 'legacy_pregrade_git_entry')
        projected = {k: row[k] for k in ('path_b64', 'kind')}
        try:
            git_path = base64.b64decode(row['path_b64'], validate=True)
        except (ValueError, TypeError):
            raise paired.Refusal('legacy_pregrade_git_entry') from None
        need(git_path and b'\x00' not in git_path and not git_path.startswith(b'/') and
             b'..' not in git_path.split(b'/'), 'legacy_pregrade_git_entry')
        if row['kind'] == 'file':
            need(type(row.get('mode')) is int and 0 <= row['mode'] <= 0o777,
                 'legacy_pregrade_git_entry')
            data = _decode_content(row.get('content_b64'), row.get('bytes'), row.get('sha256'))
            projected.update(mode=row['mode'], bytes=len(data), sha256=row['sha256'],
                             content_b64=row['content_b64'])
        else:
            need(set(row) == {'path_b64', 'kind'}, 'legacy_pregrade_git_entry')
        git_rows.append(projected)
    blind = dict(schema='cairn.legacy-blind-final-artifacts/1', workspace_entries=entries,
                 deletions=deletions, git_index_refs=git_rows)
    public = packet.canonical_bytes(blind)
    need(len(public) <= paired.FILE_LIMIT, 'legacy_pregrade_artifact_bound')
    return raw, snapshot, public, custody_pin


def _decode_content(encoded, size, digest):
    need(type(encoded) is str and type(size) is int and 0 <= size <= paired.FILE_LIMIT and
         type(digest) is str and len(digest) == 64, 'legacy_pregrade_snapshot_content')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise paired.Refusal('legacy_pregrade_snapshot_content') from None
    need(len(content) == size and hashlib.sha256(content).hexdigest() == digest,
         'legacy_pregrade_snapshot_content')
    return content


def _evaluator_projection(case, record, source, task_ref, stream_ref, artifact_ref,
                          answer_ref, action_ref, snapshot_pin, attempt_pin, chosen):
    """Project only evaluator source/input/exit/precedence; never checks or verdicts."""
    expected = case_materials.shell_checks(case)
    log = record.get('check_log', [])
    need(type(log) is list and len(log) == len(expected), 'legacy_evaluator_observation_unavailable')
    exits = []
    for check, item in zip(expected, log):
        need(type(item) is dict and item.get('run') == check['run'][:200],
             'legacy_evaluator_observation_mismatch')
        if item.get('outcome') == 'timeout':
            need(set(item) == {'run', 'outcome', 'seconds'} and
                 type(item['seconds']) in (int, float) and item['seconds'] >= 0,
                 'legacy_evaluator_observation_mismatch')
            exits.append(None)
        else:
            need(set(item) == {'run', 'exit', 'seconds', 'output'} and type(item['exit']) is int and
                 -255 <= item['exit'] <= 255 and type(item['seconds']) in (int, float) and
                 item['seconds'] >= 0 and type(item['output']) is str,
                 'legacy_evaluator_observation_mismatch')
            exits.append(item['exit'])
    contract = chosen['evaluator_contract']
    need(type(contract['evaluator_source_sha256']) is str and
         len(contract['evaluator_source_sha256']) == 64 and
         type(contract['selected_assets_sha256']) is str and
         len(contract['selected_assets_sha256']) == 64, 'legacy_evaluator_source_binding')
    input_fields = dict(task_statement_sha256=task_ref['sha256'],
                        final_answer_sha256=answer_ref['sha256'],
                        stream_sha256=stream_ref['sha256'],
                        baseline_sha256=record['baseline_custody']['sha256'],
                        pregrade_snapshot_sha256=snapshot_pin['sha256'],
                        selected_action_trace_sha256=None if action_ref is None else action_ref['sha256'],
                        selected_case_sha256=chosen['source_case_sha256'])
    input_sha = hashlib.sha256(packet.canonical_bytes(input_fields)).hexdigest()
    observation = dict(schema='cairn.legacy-v12-evaluator-observation/1',
        evaluator_source_sha256=contract['evaluator_source_sha256'],
        selected_assets_sha256=contract['selected_assets_sha256'],
        selected_case_sha256=chosen['source_case_sha256'], input_sha256=input_sha,
        input_hashes=input_fields, grade_execution=dict(boundary='in_process_grade', returned=True,
            process_exit_codes=exits), precedence=contract['precedence'])
    public = packet.canonical_bytes(observation)
    need(len(public) <= 64 * 1024, 'legacy_evaluator_observation_bound')
    subject = dict(schema='cairn.legacy-v12-evaluator-subject/1', source=source,
        selected_materials=dict(schema=chosen['manifest']['schema'], sha256=chosen['manifest']['sha256']),
        attempt=attempt_pin,
        task_statement=task_ref, stream=stream_ref, pregrade_snapshot=snapshot_pin,
        final_answer=answer_ref, artifact=artifact_ref, action=action_ref,
        evaluator_source_sha256=contract['evaluator_source_sha256'],
        selected_assets_sha256=contract['selected_assets_sha256'],
        precedence_sha256=hashlib.sha256(packet.canonical_bytes(contract['precedence'])).hexdigest())
    return public, subject


def _plan(request_pin, output):
    request = _json(_read(request_pin))
    need(isinstance(request, dict) and request.get('schema') in
         (REQUEST_SCHEMA, ACTION_REQUEST_SCHEMA, SELECTED_REQUEST_SCHEMA),
         'legacy_request_shape')
    if request['schema'] == SELECTED_REQUEST_SCHEMA:
        paired.shape(request, 'schema agent plan binding namespace policy policy_ref review_measurement '
                     'action_parser case_materials', 'legacy_request_shape')
    else:
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
    if request['schema'] == SELECTED_REQUEST_SCHEMA:
        selected_materials = dict(schema=case_materials.SCHEMA, sha256=request['case_materials']['sha256'])
        need(plan.get('case_materials') == selected_materials and agent.get('case_materials') == selected_materials,
             'legacy_material_manifest_not_selected')
        need(agent.get('frozen', {}).get('label_version') == case_materials.VERSION and
             agent.get('frozen', {}).get('cases_sha256') == case_materials.EXPECTED_CASES_SHA256,
             'legacy_material_source_mismatch')
        case_materials.read_manifest(request['case_materials'], evaluator_sha256=plan.get('evaluator_sha256'))
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
    materials = _materials(request, changed, plan)

    namespace = request['namespace']
    identity = {k: binding[k] for k in ('request_id', 'request_sha256', 'campaign_id', 'occurrence_id', 'schedule_sha256')}
    files, samples, comparisons, contracts, attempts = {}, [], [], {}, []
    for case, seed in changed:
        chosen = materials[case]
        need(not chosen.get('unavailable_channels'), 'legacy_selected_channel_unavailable')
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
            artifact_ref = None
            snapshot_pin = None
            if 'artifact_bundle' in chosen['channels']:
                raw_snapshot, snapshot, artifact_bytes, snapshot_pin = _pregrade_snapshot(
                    run_dir, record, case_materials.case_source(case), content)
                artifact_ref = _retain(files, output, 'artifacts/'+sample_id+'.json', artifact_bytes)
                sources['artifact_bundle'] = artifact_ref
                observed['artifact_bundle'] = dict(snapshot=snapshot_pin, projection=artifact_ref)
            if 'evaluator_observation' in chosen['channels']:
                need(snapshot_pin is not None and artifact_ref is not None,
                     'legacy_evaluator_observation_unavailable')
                observation, subject = _evaluator_projection(
                    case_materials.case_source(case), record, source,
                    chosen['task_statement'], dict(path=str(run_dir/'runs'/record['run_id']/'stream.jsonl'),
                    **_digest(content)), artifact_ref,
                    sources.get('final_answer'), sources.get('action_trace'), snapshot_pin,
                    record_pins[record['run_id']], chosen)
                sources['evaluator_observation'] = _retain(
                    files, output, 'evaluations/'+sample_id+'.json', observation)
                subject_pin = _retain(files, output, 'subjects/evaluator-'+sample_id+'.json',
                                      packet.canonical_bytes(subject))
                observations['evaluator_observation'] = _retain(
                    files, output, 'observations/evaluator-'+sample_id+'.json',
                    packet.canonical_bytes(dict(schema='cairn.blind-evaluator-observation/2',
                        source=source, channel='evaluator_observation',
                        evidence=[sources['evaluator_observation']], role='evaluator_observation',
                        subject=subject_pin)))
                observed['evaluator_observation'] = dict(projection=sources['evaluator_observation'],
                    subject=subject_pin, observation=observations['evaluator_observation'])
            samples.append(dict(
                source=source,
                sample_id=sample_id, case_contract_sha256=chosen['contract']['sha256'],
                channels=[dict(name=channel,
                               entries=[_artifact_entry(case_materials.ARTIFACT_NAME, sources[channel])]
                               if channel == 'artifact_bundle' else [_entry(channel, sources[channel])],
                               observation=observations.get(channel))
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
