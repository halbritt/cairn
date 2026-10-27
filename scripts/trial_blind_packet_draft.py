"""Offline selected packet drafts. Declared approval is never release authority."""
import base64
import json
import re
from pathlib import PurePosixPath

import trial_paired_request as paired
import pinned_document as pinned
from trial_work_product import COUNT_LIMIT, TOTAL_LIMIT, GIT_OUTPUT_LIMIT

CHANNELS = frozenset(('task_statement', 'grading_instructions', 'final_answer',
                      'artifact_bundle', 'action_trace', 'evaluator_observation'))
POLICIES = ('rubric', 'changed_pair_definition', 'blinding_policy', 'release_specification', 'review_budget')
PAIR_KEY = ('request_id','request_sha256','campaign_id','occurrence_id','schedule_sha256','case','seed','wording')
SAFE_INTEGER = 2**53 - 1
MEDIA_TYPES = frozenset(('text/plain', 'application/json', 'application/octet-stream', 'text/x-diff'))
META = ('name', 'kind', 'representation', 'media_type')
ARTIFACTS = dict(file='bytes', patch='bytes', index='bytes', binary='bytes',
                 deleted='deletion_record', rename='rename_record')


def _name(value):
    paired.need(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',value), 'blind_name')


def _digest_shape(value):
    paired.shape(value, 'bytes sha256', 'blind_digest_shape')
    paired.need(type(value['bytes']) is int and 0 <= value['bytes'] <= paired.FILE_LIMIT and
                type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}',value['sha256']), 'blind_digest')


def _structured(value):
    if type(value) is str:
        value.encode('utf-8',errors='strict')
    elif type(value) is int:
        paired.need(abs(value) <= SAFE_INTEGER, 'blind_integer')
    elif type(value) is dict:
        for key,child in value.items():
            paired.need(type(key) is str, 'blind_json_key')
            _structured(key); _structured(child)
    elif type(value) is list:
        for child in value: _structured(child)
    else:
        paired.need(value is None or type(value) is bool, 'blind_json_value')


def canonical_bytes(value):
    """C from source contract /2; not the legacy request canonical/file hash."""
    _structured(value)
    return (json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def _json(content):
    value = paired.decode_json(content.decode('utf-8',errors='strict'))
    _structured(value)
    return value


class _Inputs:
    def __init__(self, output):
        self.output, self.files, self.total = output, {}, 0

    def read(self, reference):
        paired.shape(reference, 'path bytes sha256', 'blind_pin_shape')
        _digest_shape({k:reference[k] for k in ('bytes','sha256')})
        path = paired.plain_path(reference['path'])
        paired.need(str(path) == reference['path'], 'blind_pin_path')
        paired.need(self.output is None or (path != self.output and path not in self.output.parents and self.output not in path.parents),
                    'control_path_overlap')
        if path in self.files:
            pin, content = self.files[path]
            paired.need(pin == reference, 'blind_pin_conflict')
            return content
        content = paired.read_bytes(path)
        paired.need(pinned.digest(content) == {k:reference[k] for k in ('bytes','sha256')}, 'blind_pin_mismatch')
        self.total += len(content)
        self.files[path] = (dict(reference), content)
        return content


def _transform(content, transform):
    paired.need(type(transform) is dict, 'blind_transform')
    if transform.get('kind') == 'exact_copy':
        paired.shape(transform, 'kind', 'blind_transform')
        return content
    paired.shape(transform, 'kind paths', 'blind_transform')
    paired.need(transform['kind'] == 'closed_json_projection', 'blind_transform')
    paths = transform['paths']
    paired.need(type(paths) is list and 0 < len(paths) <= COUNT_LIMIT, 'blind_projection')
    selected = []
    for path in paths:
        paired.need(type(path) is list and path and all(type(key) is str and key and
                    key not in ('*','**') for key in path), 'blind_projection_path')
        p = tuple(path)
        paired.need(all(p[:len(q)] != q and q[:len(p)] != p for q in selected), 'blind_projection_overlap')
        selected.append(p)
    source, result = _json(content), []
    for path in selected:
        value = source
        for key in path:
            paired.need(type(value) is dict and key in value, 'blind_projection_missing_object_key')
            value = value[key]
        paired.need(value is None or type(value) in (bool,str,int), 'blind_projection_terminal')
        result.append(dict(path=list(path),value=value))
    return canonical_bytes(result)


def _contract(value):
    paired.shape(value, 'schema case_key case_class required_channels artifact_inventory', 'blind_contract')
    paired.need(value['schema'] == 'cairn.blind-case-contract/2', 'blind_contract_schema')
    _name(value['case_key']); _name(value['case_class'])
    names = value['required_channels']
    paired.need(type(names) is list and all(type(n) is str and n in CHANNELS for n in names) and
                len(set(names)) == len(names) and len(names) >= 3 and
                {'task_statement','grading_instructions'} <= set(names), 'blind_required_channels')
    inventory = value['artifact_inventory']
    paired.need(type(inventory) is list and len(inventory) <= COUNT_LIMIT, 'blind_inventory')
    for entry in inventory:
        paired.shape(entry, ' '.join(META), 'blind_inventory_entry')
        _metadata(entry, artifact=True)
    paired.need(len({e['name'] for e in inventory}) == len(inventory), 'blind_inventory_duplicate')
    if 'artifact_bundle' not in names:
        paired.need(value['artifact_inventory'] == [], 'blind_inventory')


def _source(value):
    paired.shape(value, 'schema '+' '.join(PAIR_KEY)+' arm run_id', 'blind_source')
    paired.need(value['schema'] == 'cairn.blind-source/2', 'blind_source_schema')
    for key in ('request_id','campaign_id','occurrence_id','case','run_id'): _name(value[key])
    for key in ('request_sha256','schedule_sha256'):
        paired.need(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}',value[key]), 'blind_source_digest')
    paired.need(type(value['seed']) is int and 0 <= value['seed'] <= SAFE_INTEGER and
                value['wording'] == 'task' and value['arm'] in ('CURRENT','CAND'),
                'blind_source_identity')


def _logical_name(name):
    paired.need(type(name) is str and name and not PurePosixPath(name).is_absolute() and
                all(p not in ('','.','..') for p in name.split('/')) and
                '\\' not in name and all(ord(c)>=32 and ord(c)!=127 for c in name), 'blind_logical_name')


def _metadata(entry, *, artifact):
    _logical_name(entry['name'])
    paired.need(type(entry['media_type']) is str and entry['media_type'] in MEDIA_TYPES, 'blind_media')
    kinds = ARTIFACTS if artifact else {'content':'bytes'}
    paired.need(type(entry['kind']) is str and entry['kind'] in kinds and
                entry['representation'] == kinds[entry['kind']], 'blind_representation')
    if entry['kind'] in ('deleted','rename'):
        paired.need(entry['media_type'] == 'application/json', 'blind_record_media')


def _artifact_record(entry, content):
    if entry['kind'] == 'deleted':
        record = _json(content)
        paired.shape(record, 'schema name', 'blind_deletion')
        paired.need(record == dict(schema='cairn.blind-deletion/1',name=entry['name']), 'blind_deletion')
    if entry['kind'] == 'rename':
        record = _json(content)
        paired.shape(record, 'schema from to', 'blind_rename')
        paired.need(record['schema'] == 'cairn.blind-rename/1' and record['from'] != record['to'], 'blind_rename')
        _logical_name(record['from']); _logical_name(record['to'])


def _render(channels, inputs, contract, source):
    paired.need(type(channels) is list, 'blind_channels')
    for channel in channels: paired.shape(channel, 'name entries observation', 'blind_channel')
    names = [c['name'] for c in channels]
    paired.need(all(type(n) is str for n in names) and len(set(names)) == len(names) and
                names == contract['required_channels'], 'blind_channel_coverage')
    result, logical_names = [], set()
    totals = {group:[0,0] for group in ('input','answer','tool','artifact')}
    ceilings = dict(input=paired.ORIGINAL_LIMITS['selected_input_bytes'],
                    answer=paired.ORIGINAL_LIMITS['final_answer_bytes'],
                    tool=paired.ORIGINAL_LIMITS['tool_room_bytes'], artifact=TOTAL_LIMIT)
    for channel in channels:
        artifact = channel['name'] == 'artifact_bundle'
        # Actor evidence ceilings do not define a grader-input allowance. Selected
        # evaluator observations and instructions use retention/work-product bounds.
        group = {'task_statement':'input','final_answer':'answer','action_trace':'tool'}.get(channel['name'],'artifact')
        paired.need(type(channel['entries']) is list and len(channel['entries']) <= COUNT_LIMIT and
                    (artifact or len(channel['entries']) == 1), 'blind_entries')
        if channel['name'] in ('action_trace','evaluator_observation'):
            observation = _json(inputs.read(channel['observation']))
            paired.shape(observation, 'schema source channel evidence role subject', 'blind_observation')
            paired.need(observation['schema'] == 'cairn.blind-evaluator-observation/2' and
                        canonical_bytes(observation['source']) == canonical_bytes(source) and
                        observation['channel'] == channel['name'] and
                        canonical_bytes(observation['evidence']) ==
                        canonical_bytes([e['input'] for e in channel['entries']]), 'blind_observation_binding')
            paired.need(observation['role'] == 'evaluator_observation', 'blind_observation_role')
            inputs.read(observation['subject'])
        else:
            paired.need(channel['observation'] is None, 'blind_unexpected_observation')
        entries = []
        for entry in channel['entries']:
            paired.shape(entry, 'name kind representation media_type input transform output', 'blind_entry')
            _metadata(entry, artifact=artifact)
            paired.need(entry['name'] not in logical_names, 'blind_duplicate_name')
            logical_names.add(entry['name'])
            content = inputs.read(entry['input'])
            input_length = len(content)
            content = _transform(content, entry['transform'])
            _digest_shape(entry['output'])
            paired.need(pinned.digest(content) == entry['output'], 'blind_output_mismatch')
            totals[group][0] += input_length
            totals[group][1] += len(content)
            paired.need(max(totals[group]) <= ceilings[group], 'blind_channel_bound')
            if entry['kind'] in ('patch','index'):
                paired.need(max(input_length,len(content)) <= GIT_OUTPUT_LIMIT, 'blind_artifact_bound')
            if artifact: _artifact_record(entry, content)
            entries.append(dict(kind=channel['name'],name=entry['name'],media_type=entry['media_type'],
                encoding='base64',**pinned.digest(content),data=base64.b64encode(content).decode('ascii')))
        if artifact:
            paired.need([{k:e[k] for k in META} for e in channel['entries']] == contract['artifact_inventory'],
                        'blind_inventory_coverage')
        result.extend(entries)
    return result


def _prepare(selection_pin, output=None):
    inputs = _Inputs(output)
    selection = _json(inputs.read(selection_pin))
    paired.shape(selection, 'schema namespace policy policy_ref case_contracts samples comparisons review_measurement', 'blind_selection')
    paired.need(selection['schema'] == 'cairn.blind-selection/2', 'blind_selection_schema')
    _name(selection['namespace'])
    policy = selection['policy']
    paired.shape(policy, 'schema '+' '.join(POLICIES), 'blind_policy')
    paired.need(policy['schema'] == 'cairn.blind-policy/2', 'blind_policy_schema')
    for name in POLICIES:
        paired.shape(policy[name], 'version pin', 'blind_policy_version')
        _name(policy[name]['version']); inputs.read(policy[name]['pin'])
    public_policy = selection['policy_ref']
    paired.shape(public_policy, 'schema id', 'blind_policy_ref')
    paired.need(public_policy['schema'] == 'cairn.blind-policy-ref/1', 'blind_policy_ref_schema')
    _name(public_policy['id'])
    paired.need(type(selection['case_contracts']) is list and selection['case_contracts'] and
                type(selection['samples']) is list and selection['samples'], 'blind_selection_empty')
    contracts, used = {}, set()
    for reference in selection['case_contracts']:
        contract = _json(inputs.read(reference)); _contract(contract)
        paired.need(reference['sha256'] not in contracts and
                    contract['case_key'] not in [c['case_key'] for c in contracts.values()], 'blind_duplicate_contract')
        contracts[reference['sha256']] = contract
    samples, sample_pins, public_files, cells, runs = {}, {}, {}, set(), set()
    for sample in selection['samples']:
        paired.shape(sample, 'source sample_id case_contract_sha256 channels', 'blind_sample')
        ident = sample['sample_id']; _name(ident)
        source = sample['source']; _source(source)
        cell = tuple(source[k] for k in (*PAIR_KEY,'arm'))
        paired.need(cell not in cells and source['run_id'] not in runs and ident not in samples, 'blind_duplicate_mapping')
        cells.add(cell); runs.add(source['run_id'])
        contract = contracts[sample['case_contract_sha256']]; used.add(sample['case_contract_sha256'])
        paired.need(source['case'] == contract['case_key'], 'blind_case_join')
        public = dict(schema='cairn.blind-sample-draft/1',sample_id=ident,policy_ref=public_policy,
                      channels=_render(sample['channels'],inputs,contract,source))
        content = canonical_bytes(public)
        public_files['samples/'+ident+'.json'] = content
        _retention(public_files)
        samples[ident] = (sample,public)
        sample_pins[ident] = pinned.digest(content)
    paired.need(used == set(contracts), 'blind_unused_contract')
    paired.need(type(selection['comparisons']) is list and selection['comparisons'], 'blind_comparisons_empty')
    pair_pins, paired_samples, pair_keys = {}, set(), set()
    for pair in selection['comparisons']:
        paired.shape(pair, 'comparison_id pair_key samples', 'blind_comparison')
        _name(pair['comparison_id'])
        paired.shape(pair['pair_key'], ' '.join(PAIR_KEY), 'blind_pair_key')
        ids = pair['samples']
        paired.need(type(ids) is list and len(ids) == 2 and all(type(i) is str and i in samples for i in ids) and
                    ids[0] != ids[1] and not (set(ids) & paired_samples) and
                    pair['comparison_id'] not in pair_pins, 'blind_pair_mapping')
        sources = [samples[i][0]['source'] for i in ids]
        key_bytes = canonical_bytes(pair['pair_key'])
        paired.need(key_bytes not in pair_keys and all(canonical_bytes({k:s[k] for k in PAIR_KEY}) == key_bytes
                    for s in sources) and {s['arm'] for s in sources} == {'CURRENT','CAND'}, 'blind_pair_join')
        paired_samples.update(ids); pair_keys.add(key_bytes)
        public = dict(schema='cairn.blind-comparison-draft/1',comparison_id=pair['comparison_id'],
            policy_ref=public_policy,samples=[dict(sample_id=ident,sample_packet_sha256=sample_pins[ident]['sha256'],
                packet=samples[ident][1]) for ident in pair['samples']])
        content = canonical_bytes(public)
        pair_pins[pair['comparison_id']] = pinned.digest(content)
        public_files['comparisons/'+pair['comparison_id']+'.json'] = content
        _retention(public_files)
    paired.need(paired_samples == set(samples), 'blind_unpaired_sample')
    subject = dict(schema='cairn.blind-approval-subject/2',selection=selection,
                   sample_packets=sample_pins,comparison_packets=pair_pins)
    _retention(public_files,canonical_bytes(subject))
    return inputs, subject, public_files


def _fit(subject, inputs):
    review = subject['selection']['review_measurement']
    paired.shape(review, 'schema unit semantics graders', 'blind_review_measurement')
    paired.need(review['schema'] == 'cairn.blind-review-measurement/1' and
                review['unit'] in ('bytes','tokens','unknown') and
                review['semantics'] in ('comparison_plus_control','unknown'), 'blind_review_semantics')
    paired.need(type(review['graders']) is list and len(review['graders']) == 2, 'blind_graders')
    for slot,grader in enumerate(review['graders'],1):
        paired.shape(grader, 'slot limit control', 'blind_grader')
        paired.need(type(grader['slot']) is int and grader['slot'] == slot and
                    (grader['limit'] is None or type(grader['limit']) is int and
                     0 <= grader['limit'] <= SAFE_INTEGER), 'blind_grader_limit')
        inputs.read(grader['control'])
    comparisons = {}
    for ident,pin in subject['comparison_packets'].items():
        graders = []
        for grader in review['graders']:
            control = inputs.read(grader['control'])
            supported = review['unit'] == 'bytes' and review['semantics'] == 'comparison_plus_control' and grader['limit'] is not None
            required = pin['bytes']+len(control) if supported else None
            graders.append(dict(slot=grader['slot'],unit=review['unit'],required=required,limit=grader['limit'],
                                state=('fits' if required <= grader['limit'] else 'over_budget') if supported else 'unknown'))
        comparisons[ident] = graders
    states = {g['state'] for gs in comparisons.values() for g in gs}
    return dict(state='over_budget' if 'over_budget' in states else 'unknown' if 'unknown' in states else 'fits',
                comparisons=comparisons)


def _retention(public_files, *private_bytes):
    paired.need(sum(map(len,public_files.values()))+sum(map(len,private_bytes)) <= paired.FILE_LIMIT,
                'blind_retention_bound')


def _subject(selection_pin):
    inputs, subject, public_files = _prepare(selection_pin)
    _fit(subject,inputs)
    _retention(public_files,canonical_bytes(subject))
    return subject


def _draft(selection_pin, approval_pin, output, now):
    now = paired.utc(now.isoformat())
    output = paired.plain_path(str(output))
    paired.need(not output.exists(), 'target_exists')
    inputs, subject, public_files = _prepare(selection_pin,output)
    fit = _fit(subject,inputs)
    subject_bytes = canonical_bytes(subject)
    subject_sha = pinned.digest(subject_bytes)['sha256']
    approval = _json(inputs.read(approval_pin))
    paired.shape(approval, 'schema state subject_sha256 observed_at valid_until issuer_role', 'blind_approval')
    paired.need(approval['schema'] == 'cairn.blind-declared-approval/2' and approval['state'] == 'approved',
                'blind_approval_unapproved')
    paired.need(approval['subject_sha256'] == subject_sha, 'blind_approval_mismatch')
    paired.need(paired.utc(approval['observed_at']) <= now < paired.utc(approval['valid_until']), 'blind_approval_stale')
    inputs.read(approval['issuer_role'])
    crosswalk = canonical_bytes(dict(schema='cairn.blind-crosswalk/2',selection_pin=selection_pin,
        approval_pin=approval_pin,subject=subject,subject_sha256=subject_sha))
    receipt = dict(schema='cairn.blind-draft-receipt/2',status='declared_correspondence_valid',
        policy=subject['selection']['policy'],selection_pin=selection_pin,approval_pin=approval_pin,
        subject_sha256=subject_sha,input_bytes=inputs.total,public_files={n:pinned.digest(c) for n,c in public_files.items()},
        private_crosswalk=pinned.digest(crosswalk),retention_fit='fits',declared_grader_fit=fit,
        release_authority_verified=False,runtime_admitted=False,delivery_available=False)
    receipt_bytes = canonical_bytes(receipt)
    _retention(public_files,crosswalk,receipt_bytes,receipt_bytes)
    output.mkdir(mode=0o700)
    try:
        for name in ('public','public/samples','public/comparisons','private'):
            (output/name).mkdir(mode=0o700)
        for name,content in public_files.items(): paired.write_selected_bytes(output/'public'/name,content)
        paired.write_selected_bytes(output/'private/crosswalk.json',crosswalk)
        paired.write_selected_bytes(output/'private/receipt.json',receipt_bytes)
    except OSError:
        raise paired.Refusal('blind_draft_io_consumed') from None
    return receipt


def _checked(function, *args):
    try:
        return function(*args)
    except paired.Refusal:
        raise
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):
        raise paired.Refusal('blind_draft_invalid_or_unavailable') from None


def approval_subject(selection_pin):
    """Private source preparation only; returns an acyclic subject, never approval."""
    return _checked(_subject,selection_pin)


def draft(selection_pin, approval_pin, output, *, now):
    """Offline declared correspondence. Private quiescent source is a precondition."""
    return _checked(_draft,selection_pin,approval_pin,output,now)


def deliver(*args, **kwargs):
    raise paired.Refusal('blind_delivery_unavailable')


if __name__ == '__main__':
    print('{"status":"refused","code":"blind_delivery_unavailable"}')
    raise SystemExit(2)
