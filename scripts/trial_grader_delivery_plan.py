"""Offline private delivery plans; no renderer, release authority or transport."""
import base64

import trial_paired_request as paired
import pinned_document as pinned
import trial_blind_packet_draft as blind
import trial_grading_custody as custody

C = blind.canonical_bytes
SLOTS = ('primary', 'secondary')
FLAGS = dict(release_authority_verified=False, runtime_admitted=False, delivery_available=False)


def _document(inputs, reference):
    return blind._json(inputs.read(reference))


def _schema(value, fields, schema):
    paired.shape(value, 'schema '+fields, 'delivery_shape')
    paired.need(value['schema'] == schema, 'delivery_schema')


def _frozen(inputs, selection, now):
    cross = _document(inputs, selection['crosswalk'])
    _schema(cross, 'selection_pin approval_pin subject subject_sha256', 'cairn.blind-crosswalk/2')
    subject = cross['subject']
    _schema(subject, 'selection sample_packets comparison_packets', 'cairn.blind-approval-subject/2')
    paired.need(pinned.digest(C(subject))['sha256'] == cross['subject_sha256'], 'delivery_subject_hash')
    frozen = subject['selection']
    _schema(frozen, 'namespace policy policy_ref case_contracts samples comparisons review_measurement',
            'cairn.blind-selection/2')
    # The frozen selection is historical data, not a request to reopen its evidence graph.
    paired.need(_document(inputs, cross['selection_pin']) == frozen, 'delivery_frozen_selection')
    paired.need(cross['approval_pin'] == selection['approval'], 'delivery_approval_pin')
    approval = _document(inputs, selection['approval'])
    _schema(approval, 'state subject_sha256 observed_at valid_until issuer_role', 'cairn.blind-declared-approval/2')
    paired.need(approval['state'] == 'approved' and approval['subject_sha256'] == cross['subject_sha256'],
                'delivery_declared_approval')
    paired.need(paired.utc(approval['observed_at']) <= now < paired.utc(approval['valid_until']), 'delivery_approval_stale')
    inputs.read(approval['issuer_role'])
    policy = selection['policy']
    _schema(policy, ' '.join(blind.POLICIES), 'cairn.blind-policy/2')
    paired.need(policy == frozen['policy'], 'delivery_policy_correspondence')
    for name in blind.POLICIES:
        paired.shape(policy[name], 'version pin', 'delivery_policy_version')
        blind._name(policy[name]['version']); inputs.read(policy[name]['pin'])
    _schema(frozen['policy_ref'], 'id', 'cairn.blind-policy-ref/1')
    blind._name(frozen['policy_ref']['id'])
    receipt = _document(inputs, selection['draft_receipt'])
    _schema(receipt, 'status policy selection_pin approval_pin subject_sha256 input_bytes public_files '
            'private_crosswalk retention_fit declared_grader_fit '+' '.join(FLAGS), 'cairn.blind-draft-receipt/2')
    paired.need(receipt['status'] == 'declared_correspondence_valid' and receipt['retention_fit'] == 'fits' and
                all(receipt[k] is False for k in FLAGS) and receipt['policy'] == policy and
                receipt['private_crosswalk'] == {k: selection['crosswalk'][k] for k in ('bytes','sha256')} and
                all(receipt[k] == cross[k] for k in ('selection_pin','approval_pin','subject_sha256')),
                'delivery_draft_receipt')
    paired.need(type(receipt['input_bytes']) is int and receipt['input_bytes'] >= 0, 'delivery_input_measurement')
    expected_files = {kind+'/'+name+'.json': pin for kind, key in
                      [('samples','sample_packets'),('comparisons','comparison_packets')]
                      for name, pin in subject[key].items()}
    for pin in expected_files.values(): blind._digest_shape(pin)
    paired.need(receipt['public_files'] == expected_files, 'delivery_draft_files')
    # Reuse the selected measurement rules without treating them as rendered input fit.
    paired.need(receipt['declared_grader_fit'] == blind._fit(subject, inputs), 'delivery_draft_measurement')
    return subject, receipt


def _sessions(inputs, selection, bundle, now):
    sessions = _document(inputs, selection['sessions'])
    _schema(sessions, 'binding_sha256 observed_at valid_until exclusions slots', 'cairn.grader-session-selection/1')
    paired.need(sessions['binding_sha256'] == paired.canonical_sha(bundle['binding']), 'delivery_session_binding')
    paired.need(paired.utc(sessions['observed_at']) <= now < paired.utc(sessions['valid_until']), 'delivery_sessions_stale')
    paired.need(type(sessions['exclusions']) is list and len(sessions['exclusions']) <= 1024, 'delivery_exclusions')
    excluded = {key: set() for key in ('agent_id','session_id','execution_id')}
    seen = set()
    for row in sessions['exclusions']:
        paired.shape(row, 'role agent_id session_id execution_id', 'delivery_exclusion')
        paired.need(row['role'] in ('actor_engineer','reviewer','coordinator','earlier_exposed'), 'delivery_exclusion_role')
        for key in excluded:
            blind._name(row[key]); excluded[key].add(row[key])
        paired.need(C(row) not in seen, 'delivery_duplicate_exclusion'); seen.add(C(row))
    policy = bundle['policy']
    paired.need(set(policy['excluded_agents']) <= excluded['agent_id'] and
                set(policy['excluded_sessions']) <= excluded['session_id'] and
                any(r['role'] == 'coordinator' and all(r[k] == policy['coordinator'][k]
                    for k in ('agent_id','session_id')) for r in sessions['exclusions']), 'delivery_exclusion_coverage')
    paired.need(type(sessions['slots']) is list and len(sessions['slots']) == 2, 'delivery_slots')
    for expected, row in zip(SLOTS, sessions['slots']):
        paired.shape(row, 'slot agent_id session_id execution_id freshness observation control route workload envelope', 'delivery_slot')
        paired.need(row['slot'] == expected and all(row[k] == policy['graders'][expected][k]
                    for k in ('agent_id','session_id')), 'delivery_slot_identity')
        for key in excluded:
            blind._name(row[key])
            paired.need(row[key] not in excluded[key], 'delivery_session_reused_or_excluded')
            excluded[key].add(row[key])
        paired.need(row['freshness'] in ('declared_fresh','unknown'), 'delivery_freshness')
        for field in ('control','route','workload'): inputs.read(row[field])
        if row['observation'] is not None: inputs.read(row['observation'])
        envelope = row['envelope']
        paired.shape(envelope, 'renderer unit semantics limit transport framing tool_schema declared_input_bytes declared_input_tokens',
                     'delivery_envelope')
        paired.need(envelope['unit'] in ('bytes','tokens','unknown') and
                    envelope['semantics'] in ('full_rendered_request','unknown'), 'delivery_envelope_units')
        for field in ('limit','declared_input_bytes','declared_input_tokens'):
            value = envelope[field]
            paired.need(value is None or type(value) is int and 0 <= value <= blind.SAFE_INTEGER, 'delivery_envelope_measurement')
        for field in ('renderer','transport','framing','tool_schema'):
            if envelope[field] is not None: inputs.read(envelope[field])
    return sessions


def _pairs(inputs, selection, subject, bundle):
    frozen = subject['selection']
    blind._name(frozen['namespace'])
    paired.need(type(frozen['case_contracts']) is list and frozen['case_contracts'], 'delivery_case_contracts')
    contracts, case_names, historical = {}, set(), {}
    for reference in frozen['case_contracts']:
        contract = _document(inputs, reference); blind._contract(contract)
        paired.need(reference['sha256'] not in contracts and contract['case_key'] not in case_names,
                    'delivery_duplicate_contract')
        contracts[reference['sha256']] = contract; case_names.add(contract['case_key'])
    paired.need(type(frozen['samples']) is list and 0 < len(frozen['samples']) <= 126 and
                type(frozen['comparisons']) is list and 0 < len(frozen['comparisons']) <= 63,
                'delivery_frozen_coverage')
    samples, runs, cells = {}, set(), set()
    for sample in frozen['samples']:
        paired.shape(sample, 'source sample_id case_contract_sha256 channels', 'delivery_sample')
        blind._name(sample['sample_id']); blind._source(sample['source'])
        source = sample['source']; cell = tuple(source[k] for k in (*blind.PAIR_KEY,'arm'))
        paired.need(sample['sample_id'] not in samples and source['run_id'] not in runs and cell not in cells,
                    'delivery_sample_mapping')
        samples[sample['sample_id']] = sample; runs.add(source['run_id']); cells.add(cell)
    paired.need(set(subject['sample_packets']) == set(samples), 'delivery_sample_coverage')
    comparisons = {}
    for pair in frozen['comparisons']:
        paired.shape(pair, 'comparison_id pair_key samples', 'delivery_comparison_mapping')
        blind._name(pair['comparison_id'])
        paired.shape(pair['pair_key'], ' '.join(blind.PAIR_KEY), 'delivery_pair_key')
        paired.need(pair['comparison_id'] not in comparisons, 'delivery_duplicate_comparison')
        comparisons[pair['comparison_id']] = pair
    paired.need(set(subject['comparison_packets']) == set(comparisons), 'delivery_comparison_coverage')
    paired.need(type(selection['pairs']) is list and len(selection['pairs']) == len(comparisons), 'delivery_pair_coverage')
    join_pairs = {p['pair_id']: p for p in bundle['packets']}
    inventory = {(r['case'],r['seed'],r['arm']):r for r in bundle['inventory']}
    result, used_pairs, used_samples, used_comparisons, used_keys, used_contracts = [], set(), set(), set(), set(), set()
    for row in selection['pairs']:
        paired.shape(row, 'pair_id comparison', 'delivery_pair_selection')
        blind._name(row['pair_id'])
        content = inputs.read(row['comparison']); public = blind._json(content)
        paired.need(content == C(public), 'delivery_comparison_canonical')
        _schema(public, 'comparison_id policy_ref samples', 'cairn.blind-comparison-draft/1')
        ident = public['comparison_id']; mapping = comparisons[ident]
        paired.need(ident not in used_comparisons and row['pair_id'] not in used_pairs, 'delivery_duplicate_pair')
        used_comparisons.add(ident); used_pairs.add(row['pair_id'])
        paired.need(pinned.digest(content) == subject['comparison_packets'][ident] and
                    public['policy_ref'] == frozen['policy_ref'], 'delivery_comparison_binding')
        ids = mapping['samples']; sources = []
        paired.need(type(ids) is list and len(ids) == 2 and type(public['samples']) is list and len(public['samples']) == 2,
                    'delivery_pair_samples')
        for sample_id, embedded in zip(ids, public['samples']):
            paired.shape(embedded, 'sample_id sample_packet_sha256 packet', 'delivery_embedded_sample')
            sample = samples[sample_id]; packet = embedded['packet']
            _schema(packet, 'sample_id policy_ref channels', 'cairn.blind-sample-draft/1')
            sample_digest = pinned.digest(C(packet))
            paired.need(sample_id not in used_samples and embedded['sample_id'] == packet['sample_id'] == sample_id and
                        packet['policy_ref'] == frozen['policy_ref'] and sample_digest == subject['sample_packets'][sample_id] and
                        embedded['sample_packet_sha256'] == sample_digest['sha256'], 'delivery_sample_binding')
            contract = contracts[sample['case_contract_sha256']]
            used_contracts.add(sample['case_contract_sha256'])
            paired.need(sample['source']['case'] == contract['case_key'], 'delivery_case_contract_join')
            _channels(packet['channels'], sample['channels'], contract, inputs, historical)
            used_samples.add(sample_id); sources.append(sample['source'])
        key = mapping['pair_key']; encoded_key = C(key)
        paired.need(encoded_key not in used_keys and {s['arm'] for s in sources} == {'CURRENT','CAND'} and
                    all({k:s[k] for k in blind.PAIR_KEY} == key for s in sources), 'delivery_pair_identity')
        used_keys.add(encoded_key)
        binding = bundle['binding']
        paired.need(all(key[k] == binding[k] for k in
                    ('request_id','request_sha256','campaign_id','occurrence_id','schedule_sha256')) and key['wording'] == 'task',
                    'delivery_occurrence_binding')
        joined = join_pairs[row['pair_id']]
        paired.need(joined['packet_sha256'] == row['comparison']['sha256'] and joined['samples'] == ids and
                    (joined['case'],joined['seed']) == (key['case'],key['seed']), 'delivery_join_packet')
        for source in sources:
            attempt = inventory[(source['case'],source['seed'],source['arm'])]
            paired.need(attempt['run_id'] == source['run_id'], 'delivery_attempt_binding')
        result.append(dict(pair_id=row['pair_id'], comparison_id=ident, pair_key=key, samples=ids,
                           sources=sources, comparison=row['comparison']))
    paired.need(used_samples == set(samples) and used_comparisons == set(comparisons) and used_pairs == set(join_pairs) and
                used_contracts == set(contracts),
                'delivery_dangling_mapping')
    paired.need(all(path not in inputs.files or reference == inputs.files[path][0]
                    for path, reference in historical.items()), 'delivery_retained_pin_conflict')
    return result


def _retained_pin(reference, inputs, historical):
    """Validate a frozen source declaration, never open its task/observation body."""
    paired.shape(reference, 'path bytes sha256', 'delivery_retained_pin')
    blind._digest_shape({k:reference[k] for k in ('bytes','sha256')})
    path = paired.plain_path(reference['path'])
    paired.need(str(path) == reference['path'], 'delivery_retained_path')
    paired.need(path != inputs.output and path not in inputs.output.parents and inputs.output not in path.parents,
                'control_path_overlap')
    previous = historical.get(path, inputs.files.get(path, (reference, None))[0])
    paired.need(reference == previous, 'delivery_retained_pin_conflict')
    historical[path] = reference


def _transform_declaration(transform):
    paired.need(type(transform) is dict, 'delivery_transform')
    if transform.get('kind') == 'exact_copy':
        paired.shape(transform, 'kind', 'delivery_transform')
        return
    paired.shape(transform, 'kind paths', 'delivery_transform')
    paired.need(transform['kind'] == 'closed_json_projection' and type(transform['paths']) is list and
                0 < len(transform['paths']) <= blind.COUNT_LIMIT, 'delivery_projection')
    seen = []
    for path in transform['paths']:
        paired.need(type(path) is list and path and all(type(k) is str and k and k not in ('*','**') for k in path),
                    'delivery_projection_path')
        paired.need(all(path[:len(p)] != p and p[:len(path)] != path for p in seen), 'delivery_projection_overlap')
        seen.append(path)


def _channels(public, selected, contract, inputs, historical):
    """Check retained bytes against frozen output declarations, without rereading task sources."""
    paired.need(type(public) is list and type(selected) is list, 'delivery_channels')
    expected, names = [], set()
    totals = {k:[0,0] for k in ('input','answer','tool','artifact')}
    for channel in selected:
        paired.shape(channel, 'name entries observation', 'delivery_channel')
        name = channel['name']
        paired.need(type(name) is str and name in blind.CHANNELS and name not in names,
                    'delivery_channel_name')
        names.add(name)
        if name in ('action_trace','evaluator_observation'):
            _retained_pin(channel['observation'], inputs, historical)
        else:
            paired.need(channel['observation'] is None, 'delivery_unexpected_observation')
        paired.need(type(channel['entries']) is list and len(channel['entries']) <= blind.COUNT_LIMIT and
                    (name == 'artifact_bundle' or len(channel['entries']) == 1), 'delivery_channel_entries')
        for entry in channel['entries']:
            paired.shape(entry, 'name kind representation media_type input transform output', 'delivery_channel_entry')
            blind._metadata(entry, artifact=name == 'artifact_bundle')
            _retained_pin(entry['input'], inputs, historical)
            _transform_declaration(entry['transform'])
            blind._digest_shape(entry['output'])
            if entry['transform']['kind'] == 'exact_copy':
                paired.need(entry['output'] == {k:entry['input'][k] for k in ('bytes','sha256')}, 'delivery_exact_copy')
            if entry['kind'] in ('patch','index'):
                paired.need(max(entry['input']['bytes'],entry['output']['bytes']) <= blind.GIT_OUTPUT_LIMIT,
                            'delivery_patch_index_bound')
            expected.append((name, entry))
            group = {'task_statement':'input','final_answer':'answer','action_trace':'tool'}.get(name,'artifact')
            totals[group][0] += entry['input']['bytes']; totals[group][1] += entry['output']['bytes']
        if name == 'artifact_bundle':
            paired.need([{k:e[k] for k in blind.META} for e in channel['entries']] == contract['artifact_inventory'],
                        'delivery_inventory_coverage')
    paired.need({'task_statement','grading_instructions'} <= names and len(names) >= 3 and
                len(public) == len(expected) and [c['name'] for c in selected] == contract['required_channels'],
                'delivery_channel_coverage')
    ceilings = dict(input=paired.ORIGINAL_LIMITS['selected_input_bytes'], answer=paired.ORIGINAL_LIMITS['final_answer_bytes'],
                    tool=paired.ORIGINAL_LIMITS['tool_room_bytes'], artifact=blind.TOTAL_LIMIT)
    paired.need(all(max(totals[k]) <= ceilings[k] for k in totals), 'delivery_channel_bound')
    logical_names = set()
    for actual, (name, entry) in zip(public, expected):
        paired.shape(actual, 'kind name media_type encoding bytes sha256 data', 'delivery_public_channel')
        paired.need(actual['name'] not in logical_names, 'delivery_duplicate_channel_name')
        logical_names.add(actual['name'])
        paired.need(actual['encoding'] == 'base64' and actual['kind'] == name and
                    actual['name'] == entry['name'] and actual['media_type'] == entry['media_type'], 'delivery_channel_metadata')
        blind._digest_shape({k:actual[k] for k in ('bytes','sha256')})
        content = base64.b64decode(actual['data'], validate=True)
        paired.need(base64.b64encode(content).decode('ascii') == actual['data'] and
                    pinned.digest(content) == entry['output'] == {k:actual[k] for k in ('bytes','sha256')},
                    'delivery_channel_content')
        blind._artifact_record(entry, content)


def _build(selection_pin, output, now):
    now = paired.utc(now.isoformat())
    output = paired.plain_path(str(output))
    paired.need(not output.exists(), 'target_exists')
    inputs = blind._Inputs(output)
    selection = _document(inputs, selection_pin)
    _schema(selection, 'crosswalk draft_receipt approval policy grading_bundle sessions pairs', 'cairn.grader-delivery-selection/1')
    subject, draft_receipt = _frozen(inputs, selection, now)
    bundle = _document(inputs, selection['grading_bundle'])
    custody.join_results(bundle, [], [])
    for key in ('rubric','changed_pair_definition','blinding_policy'):
        paired.need(bundle['binding'][key+'_sha256'] == selection['policy'][key]['pin']['sha256'], 'delivery_join_policy')
    sessions = _sessions(inputs, selection, bundle, now)
    pairs = _pairs(inputs, selection, subject, bundle)
    files = {}

    def copy_file(name, reference):
        content = inputs.read(reference)
        files[name] = content
        return dict(path=str(output/name), **pinned.digest(content))

    copies = {}
    for slot, selected_control in zip(sessions['slots'], subject['selection']['review_measurement']['graders']):
        paired.need(slot['control'] == selected_control['control'], 'delivery_selected_control')
        copied = dict(control=copy_file('controls/'+slot['slot']+'.bin', slot['control']), overhead={})
        for field in ('transport','framing','tool_schema'):
            if slot['envelope'][field] is not None:
                copied['overhead'][field] = copy_file('overhead/'+slot['slot']+'-'+field+'.bin', slot['envelope'][field])
        copies[slot['slot']] = copied
    for pair in pairs:
        pair['deliveries'] = []
        for slot in sessions['slots']:
            comparison = copy_file('comparisons/'+pair['pair_id']+'-'+slot['slot']+'.json', pair['comparison'])
            copied = copies[slot['slot']]
            envelope = slot['envelope']
            selected_bytes = comparison['bytes'] + copied['control']['bytes'] + sum(p['bytes'] for p in copied['overhead'].values())
            measured = envelope['declared_input_'+envelope['unit']] if envelope['unit'] in ('bytes','tokens') else None
            if envelope['unit'] == 'bytes' and envelope['semantics'] == 'full_rendered_request' and measured is not None:
                paired.need(measured >= selected_bytes, 'delivery_envelope_undercount')
            over = envelope['limit'] is not None and (
                envelope['unit'] == 'bytes' and selected_bytes > envelope['limit'] or
                envelope['semantics'] == 'full_rendered_request' and measured is not None and measured > envelope['limit'])
            budget = dict(selected_component_bytes=selected_bytes,
                overhead_inventory='declared_complete' if len(copied['overhead']) == 3 else 'unknown',
                unit=envelope['unit'], limit=envelope['limit'], declared_input_measurement=measured,
                state='over_budget' if over else 'unknown')
            pair['deliveries'].append(dict(slot=slot['slot'], agent_id=slot['agent_id'], session_id=slot['session_id'],
                execution_id=slot['execution_id'], comparison=comparison, packet_sha256=comparison['sha256'],
                **copied, budget=budget, wire_fit='unknown'))
    plan = dict(schema='cairn.grader-delivery-plan/1', selection_pin=selection_pin, selection=selection,
        binding=bundle['binding'], binding_sha256=paired.canonical_sha(bundle['binding']), sessions=sessions,
        pairs=pairs, declared_grader_fit=draft_receipt['declared_grader_fit'], wire_fit='unknown',
        budget_state='over_budget' if any(d['budget']['state'] == 'over_budget' for p in pairs for d in p['deliveries']) else 'unknown',
        qualification='unqualified', **FLAGS)
    files['plan.json'] = C(plan)
    receipt = dict(schema='cairn.grader-delivery-plan-receipt/1', selection_pin=selection_pin,
        files={name:dict(path=str(output/name),**pinned.digest(content)) for name,content in files.items()},
        qualification='unqualified', retention_fit='fits', wire_fit='unknown', **FLAGS)
    receipt_bytes = C(receipt)
    paired.need(all(len(content) <= paired.FILE_LIMIT for content in files.values()) and
                sum(map(len,files.values())) + 2*len(receipt_bytes) <= paired.FILE_LIMIT, 'delivery_retention_bound')
    output.mkdir(mode=0o700)
    try:
        for directory in ('controls','overhead','comparisons'): (output/directory).mkdir(mode=0o700)
        for name, content in files.items(): paired.write_selected_bytes(output/name, content)
        paired.write_selected_bytes(output/'receipt.json', receipt_bytes)
    except OSError:
        raise paired.Refusal('delivery_io_consumed') from None
    return receipt


def build_plan(selection_pin, absent_output, *, now):
    """Borrow a pin; preflight private quiescent inputs, then consume one output path."""
    try:
        return _build(selection_pin, absent_output, now)
    except paired.Refusal:
        raise
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):
        raise paired.Refusal('delivery_invalid_or_unavailable') from None


def deliver(*args, **kwargs):
    """Refuse unless a fabric, delivery config and separately held authority are all supplied."""
    if any(kwargs.get(name) is None for name in ('fabric', 'config', 'authority')):
        raise paired.Refusal('grader_delivery_unavailable')
    import trial_grader_delivery
    return trial_grader_delivery.dispatch(*args, **kwargs)


def main(argv=None):
    print('{"status":"refused","code":"grader_delivery_unavailable"}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
