"""Offline paired request preparation. This entry point never launches an actor."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import uuid

from trial_task_input import load_input
from trial_task_input import corpus_entities

SCHEMA = 'cairn.paired-eval.request/1'
REVIEWS = ('task', 'oracle', 'comparator', 'supervisor', 'native', 'integration')
ORIGINAL_LIMITS = dict(selected_input_bytes=9500, final_answer_bytes=8192,
                      native_searches=2, pulls=4, hook_seconds=5, last_native_memory_seconds=30)
FILE_LIMIT = 8 * 1024 * 1024
PIN_LIMIT = 512 * 1024 * 1024


class Refusal(ValueError):
    """Fixed public code; no path, payload or exception text."""


def need(condition, code):
    if not condition:
        raise Refusal(code)


def shape(document, fields, code):
    need(isinstance(document, dict) and set(document) == set(fields.split()), code)


def utc(text):
    need(isinstance(text, str), 'utc_timestamp')
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise Refusal('utc_timestamp') from None
    need(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), 'utc_timestamp')
    return parsed


def identifier(text):
    try:
        need(isinstance(text, str) and str(uuid.UUID(text)) == text, 'canonical_uuid')
    except (ValueError, AttributeError):
        raise Refusal('canonical_uuid') from None


def capacity_check(reference, plan, now, models):
    capacity = read_json_pin(reference)
    shape(capacity, 'schema plan_sha256 state observed_at valid_until account_binding route_binding quota_policy reservation resources demands', 'capacity_shape')
    need(capacity['schema'] == 'cairn.paired-capacity-evidence/1' and capacity['state'] == 'supported', 'capacity_unknown')
    need(capacity['plan_sha256'] == plan['sha256'], 'capacity_plan_mismatch')
    need(utc(capacity['observed_at']) <= now < utc(capacity['valid_until']), 'capacity_stale')
    accounts = read_json_pin(capacity['account_binding'])
    shape(accounts, 'schema accounts', 'account_binding_shape')
    need(accounts['schema'] == 'cairn.paired-account-binding/1' and isinstance(accounts['accounts'], list)
         and 0 < len(accounts['accounts']) <= 64, 'account_binding_unknown')
    account_ids = set()
    for account in accounts['accounts']:
        shape(account, 'account_id dedicated interactive_use', 'account_shape')
        need(isinstance(account['account_id'], str) and 0 < len(account['account_id']) <= 256
             and account['dedicated'] is True and account['interactive_use'] is False, 'dedicated_account_unknown')
        need(account['account_id'] not in account_ids, 'duplicate_account')
        account_ids.add(account['account_id'])
    routes = read_json_pin(capacity['route_binding'])
    shape(routes, 'schema plan_sha256 routes', 'route_binding_shape')
    need(routes['schema'] == 'cairn.paired-route-binding/1' and routes['plan_sha256'] == plan['sha256'], 'route_binding_unknown')
    shape(routes['routes'], 'CURRENT CAND primary secondary', 'route_roles')
    for role, route in routes['routes'].items():
        shape(route, 'account_id model', 'route_shape')
        need(route['account_id'] in account_ids and route['model'] == models[role], 'route_model_mismatch')
    policy = read_json_pin(capacity['quota_policy'])
    shape(policy, 'schema units percent_conversion', 'quota_policy_shape')
    need(policy == dict(schema='cairn.paired-quota-policy/1', units=['calls','tokens'], percent_conversion=False), 'quota_policy_unknown')
    resources = capacity['resources']
    need(isinstance(resources, list) and 0 < len(resources) <= 64, 'capacity_resources')
    keys, demand_totals = [], {}
    for resource in resources:
        shape(resource, 'account_id model window_id unit remaining required', 'capacity_resource_shape')
        need(all(isinstance(resource[k], str) and 0 < len(resource[k]) <= 256 for k in ('account_id','model','window_id')), 'capacity_binding_unknown')
        need(resource['account_id'] in account_ids and resource['unit'] in ('calls', 'tokens'), 'capacity_unit_unknown')
        need(all(type(resource[k]) is int and resource[k] >= 0 for k in ('remaining','required'))
             and resource['remaining'] >= resource['required'] > 0, 'capacity_insufficient')
        key = tuple(resource[k] for k in ('account_id','model','window_id','unit'))
        need(key not in keys, 'duplicate_resource')
        keys.append(key); demand_totals[key] = 0
    shape(capacity['demands'], 'CURRENT CAND primary secondary', 'capacity_demands')
    for role, demands in capacity['demands'].items():
        need(isinstance(demands, list) and 0 < len(demands) <= 64, 'role_demand_unknown')
        seen = set()
        for demand in demands:
            shape(demand, 'resource_index max_units_per_attempt', 'demand_shape')
            index, units = demand['resource_index'], demand['max_units_per_attempt']
            need(type(index) is int and 0 <= index < len(keys) and index not in seen, 'demand_resource')
            seen.add(index)
            need(type(units) is int and units > 0 and (keys[index][3] != 'calls' or units == 1), 'demand_bound_unknown')
            need(keys[index][:2] == (routes['routes'][role]['account_id'], models[role]), 'demand_route_mismatch')
            # Every actor attempt and worst-case changed pair gets both graders.
            demand_totals[keys[index]] += units * (plan['attempts'] // 2)
    need(all(resource['required'] == demand_totals[key] for resource,key in zip(resources, keys)), 'capacity_estimate_mismatch')
    reservation = read_json_pin(capacity['reservation'])
    shape(reservation, 'schema plan_sha256 resources_sha256 state valid_until', 'reservation_shape')
    need(reservation['schema'] == 'cairn.paired-reservation/1' and reservation['state'] == 'held'
         and utc(reservation['valid_until']) > now and reservation['plan_sha256'] == plan['sha256']
         and reservation['resources_sha256'] == canonical_sha(resources), 'reservation_unknown')


def grading_check(grading, reviewers):
    shape(grading, 'primary secondary excluded_agents rubric changed_pair_definition blinding_policy dispute_owner review_budget', 'grading_shape')
    identities = []
    for name in ('primary','secondary'):
        grader = grading[name]
        shape(grader, 'agent_id model', 'grader_shape')
        identifier(grader['agent_id']); identities.append(grader['agent_id'])
        need(isinstance(grader['model'], str) and 0 < len(grader['model']) <= 256, 'grader_model')
    need(isinstance(grading['excluded_agents'], list), 'grader_exclusions')
    for excluded in grading['excluded_agents']:
        identifier(excluded)
    identifier(grading['dispute_owner'])
    need(len(set(identities)) == 2 and not set(identities) & (set(grading['excluded_agents']) | reviewers | {grading['dispute_owner']}), 'grader_not_independent')
    for name in ('rubric','changed_pair_definition','blinding_policy','review_budget'):
        pin(grading[name])


def validate(request, *, now):
    shape(request, 'schema request_id campaign_id occurrence_id scheduled_at expires_at frozen_input plan interfaces dispositions limits capacity corpus_selection grading output reply supervisor_source', 'request_shape')
    need(request['schema'] == SCHEMA, 'request_schema')
    for name in ('request_id','campaign_id','occurrence_id'):
        identifier(request[name])
    need(utc(request['scheduled_at']) <= now < utc(request['expires_at']), 'request_expired')
    need(request['limits'] == ORIGINAL_LIMITS, 'limits_changed')
    plain_path(request['output'])
    shape(request['frozen_input'], 'path manifest_sha256', 'input_reference')
    input_root = plain_path(request['frozen_input']['path'])
    need(hashlib.sha256(read_bytes(input_root/'FROZEN.json')).hexdigest() ==
         request['frozen_input']['manifest_sha256'], 'manifest_mismatch')
    check_input_files(input_root)
    try:
        bundle = load_input(input_root)
    except (ValueError, TypeError, KeyError):
        raise Refusal('frozen_input_invalid') from None
    plan = request['plan']
    shape(plan, 'instrument cases arms seeds attempts sha256', 'plan_shape')
    need(plan['sha256'] == canonical_sha({k:v for k,v in plan.items() if k != 'sha256'}), 'plan_mismatch')
    need(plan['instrument'] in ('weekly-frozen-cases','supplemental-fresh-tasks'), 'instrument_unknown')
    need(plan['arms'] == ['CURRENT','CAND'] and bundle['document']['arms'] == plan['arms'], 'paired_arms')
    need(plan['cases'] == [c['id'] for c in bundle['cases']], 'case_substitution')
    seeds = plan['seeds']
    need(isinstance(seeds, list) and 0 < len(seeds) <= 128 and
         all(type(s) is int and 0 <= s <= 2**31-1 for s in seeds) and len(set(seeds)) == len(seeds), 'seeds_shape')
    need(type(plan['attempts']) is int and plan['attempts'] == len(plan['cases'])*2*len(seeds), 'attempt_count')
    shape(request['interfaces'], 'CURRENT CAND', 'interface_shape')
    for arm, interface in request['interfaces'].items():
        shape(interface, 'api_revision actor_base components', 'interface_fields')
        frozen = bundle['document']['memory_arms'][arm]
        components = {k:v for k,v in frozen.items() if k in ('binary','hook','bridge','embedding_worker')}
        need(interface['api_revision'] == frozen['api_revision'] and interface['actor_base'] == bundle['document']['baseline_commit']
             and interface['components'] == components, 'interface_mismatch')
        for reference in components.values():
            pin(reference)
    shape(request['dispositions'], ' '.join(REVIEWS), 'dispositions_shape')
    pin(request['supervisor_source'])
    grading_check(request['grading'], {review.get('reviewer_id') for review in request['dispositions'].values()
                                     if isinstance(review, dict)})
    subjects = dict(task=hashlib.sha256(read_bytes(input_root/'input.json')).hexdigest(),
        oracle=request['grading']['rubric']['sha256'],
        comparator=canonical_sha(dict(plan=plan, interfaces=request['interfaces'])),
        supervisor=request['supervisor_source']['sha256'],
        native=bundle['document']['native']['binary']['sha256'],
        integration=request['frozen_input']['manifest_sha256'])
    for name, review in request['dispositions'].items():
        shape(review, 'artifact subject_sha256 reviewer_id state valid_until', 'review_shape')
        need(review['state'] == 'accepted' and utc(review['valid_until']) > now, 'review_unavailable')
        identifier(review['reviewer_id'])
        need(isinstance(review['subject_sha256'], str) and len(review['subject_sha256']) == 64, 'review_subject')
        pin(review['artifact'])
        need(review['subject_sha256'] == subjects[name], 'review_subject_mismatch')
    capacity_check(request['capacity'], plan, now, dict(CURRENT=bundle['document']['native']['model'],
        CAND=bundle['document']['native']['model'], **{role: request['grading'][role]['model'] for role in ('primary','secondary')}))
    check_selection(read_json_pin(request['corpus_selection']), bundle['notes'])
    shape(request['reply'], 'event_id recipient_id correlation_id', 'reply_shape')
    for ident in request['reply'].values():
        identifier(ident)
    return bundle


def canonical_sha(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def check_input_files(root):
    count, total = 0, 0
    for directory, names, files in os.walk(root, followlinks=False):
        count += len(names) + len(files)
        need(count <= 4096, 'input_file_count')
        for name in names:
            need(not (Path(directory)/name).is_symlink(), 'path_indirection')
        for name in files:
            total += len(read_bytes(Path(directory)/name))
            need(total <= 64*1024*1024, 'input_byte_limit')


def plain_path(raw):
    need(isinstance(raw, str) and Path(raw).is_absolute(), 'absolute_path_required')
    path = Path(raw)
    need('..' not in path.parts and all(not p.is_symlink() for p in (path, *path.parents)), 'path_indirection')
    return path


def read_bytes(path):
    path = plain_path(str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'regular_file_required')
        content = source.read(FILE_LIMIT + 1)
    need(len(content) <= FILE_LIMIT, 'file_limit')
    return content


def read_json(path):
    try:
        return json.loads(read_bytes(path), object_pairs_hook=unique_object,
                          parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Refusal('invalid_json') from None


def pin_shape(reference):
    need(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'pin_shape')
    need(isinstance(reference['sha256'], str) and re.fullmatch('[0-9a-f]{64}', reference['sha256']), 'pin_digest')


def pin(reference):
    pin_shape(reference)
    path = plain_path(reference['path'])
    digest, total = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'regular_file_required')
        while content := source.read(1024*1024):
            total += len(content)
            need(total <= PIN_LIMIT, 'pin_byte_limit')
            digest.update(content)
    need(digest.hexdigest() == reference['sha256'], 'pin_mismatch')


def read_json_pin(reference):
    # Read and parse the same bytes that were hashed, without a second open.
    pin_shape(reference)
    content = read_bytes(reference['path'])
    need(hashlib.sha256(content).hexdigest() == reference['sha256'], 'pin_mismatch')
    try:
        return json.loads(content, object_pairs_hook=unique_object,
                          parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Refusal('invalid_json') from None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def reject_nonfinite(_):
    raise Refusal('nonfinite_json')


def write_selected(path, document):
    with path.open('x', encoding='utf-8') as target:
        os.chmod(path, 0o600)
        json.dump(document, target, ensure_ascii=False, allow_nan=False, sort_keys=True)
        target.write('\n')
        target.flush()
        os.fsync(target.fileno())


def freeze_corpus(openings, policy):
    """Preserve supplied whole openings; source authentication remains external.

    Exclusion matches the declared policy only, not an exhaustive semantic audit.
    This function never queries or copies an operational memory store.
    """
    shape(policy, 'description excluded_record_ids forbidden_text excluded_artifacts', 'corpus_policy_shape')
    need(isinstance(policy['description'], str) and 0 < len(policy['description'].encode()) <= 4096, 'corpus_policy_empty')
    need(isinstance(openings, list) and len(openings) <= 1024, 'corpus_count')
    need(isinstance(policy['excluded_record_ids'], list) and len(policy['excluded_record_ids']) <= 1024
         and isinstance(policy['forbidden_text'], list) and len(policy['forbidden_text']) <= 1024
         and all(isinstance(s, str) and 0 < len(s.encode()) <= 4096 for s in policy['forbidden_text']), 'corpus_exclusions')
    for record in policy['excluded_record_ids']:
        identifier(record)
    need(len(set(policy['excluded_record_ids'])) == len(policy['excluded_record_ids']), 'corpus_exclusions')
    need(isinstance(policy['excluded_artifacts'], list) and len(policy['excluded_artifacts']) <= 64, 'excluded_artifacts')
    for reference in policy['excluded_artifacts']:
        pin(reference)
    notes, identities, excluded, seen, total = [], [], [], set(), 0
    for opening in openings:
        shape(opening, 'record_id version body body_sha256 kind shareable lifecycle repo entities', 'opening_shape')
        identifier(opening['record_id'])
        need(opening['record_id'] not in seen, 'duplicate_source')
        seen.add(opening['record_id'])
        need(type(opening['version']) is int and opening['version'] > 0, 'source_version')
        need(isinstance(opening['body'], str) and 0 < len(opening['body'].encode()) <= 65536, 'source_body')
        total += len(opening['body'].encode())
        need(total <= FILE_LIMIT, 'corpus_byte_limit')
        need(hashlib.sha256(opening['body'].encode()).hexdigest() == opening['body_sha256'], 'source_body_changed')
        need(opening['shareable'] is True and opening['lifecycle'] == 'active', 'source_ineligible')
        need(isinstance(opening['kind'], str) and 0 < len(opening['kind']) <= 96 and
             isinstance(opening['repo'], str) and 0 < len(opening['repo']) <= 4096, 'source_metadata')
        corpus_entities(opening)
        need(len(opening['entities']) <= 16 and all(len(e['name'].encode()) <= 4096 for e in opening['entities']), 'source_entities')
        reason = ('excluded_identity' if opening['record_id'] in policy['excluded_record_ids'] else
                  'forbidden_text' if any(s.casefold() in opening['body'].casefold() for s in policy['forbidden_text']) else None)
        if reason:
            excluded.append(dict(record_id=opening['record_id'], version=opening['version'], reason=reason))
            continue
        note = dict(id=opening['record_id'], body=opening['body'], kind=opening['kind'],
                    shareable=True, repo=opening['repo'], entities=opening['entities'])
        notes.append(note)
        identities.append({k:v for k,v in opening.items() if k != 'body'})
    corpus = json.loads(json.dumps(dict(notes=notes), ensure_ascii=False))
    selection = dict(schema='cairn.corpus-selection/1', policy=policy, notes=identities, excluded=excluded,
                     corpus_sha256=canonical_sha(corpus), claim='supplied whole source openings; authentication and semantic exclusion review external')
    return corpus, json.loads(json.dumps(selection, ensure_ascii=False))


def check_selection(selection, notes):
    shape(selection, 'schema policy notes excluded corpus_sha256 claim', 'selection_shape')
    need(selection['schema'] == 'cairn.corpus-selection/1', 'selection_schema')
    need(selection['corpus_sha256'] == canonical_sha(dict(notes=notes)), 'corpus_selection_mismatch')
    by_id = {note['id']: note for note in notes}
    need(isinstance(selection['excluded'], list) and len(selection['excluded']) <= 1024, 'excluded_sources')
    seen = set(by_id)
    for excluded in selection['excluded']:
        shape(excluded, 'record_id version reason', 'excluded_source_shape')
        identifier(excluded['record_id'])
        need(excluded['record_id'] not in seen and type(excluded['version']) is int
             and excluded['version'] > 0 and excluded['reason'] in ('excluded_identity','forbidden_text'), 'excluded_source')
        seen.add(excluded['record_id'])
    need(isinstance(selection['notes'], list) and len(selection['notes']) == len(by_id), 'selection_count')
    openings = []
    for identity in selection['notes']:
        need(isinstance(identity, dict) and identity.get('record_id') in by_id, 'selection_source')
        note = by_id[identity['record_id']]
        need(all(note.get(k, [] if k == 'entities' else None) == identity.get(k)
                 for k in ('kind','shareable','repo','entities')), 'selection_metadata')
        openings.append(dict(identity, body=note['body']))
    corpus, rebuilt = freeze_corpus(openings, selection['policy'])
    need(corpus['notes'] == notes and rebuilt['notes'] == selection['notes'], 'selection_contamination')


def prepare(request, *, registry, now):
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    bundle = validate(request, now=now)
    output = plain_path(request['output'])
    need(not output.exists(), 'target_exists')
    registry = plain_path(str(registry))
    need(registry.is_dir() and registry.stat().st_mode & 0o077 == 0, 'private_registry_required')
    paths = (output, registry, bundle['root'])
    need(all(a != b and a not in b.parents and b not in a.parents
             for i, a in enumerate(paths) for b in paths[i+1:]), 'control_path_overlap')
    # A partial preparation remains held for operator reconciliation. Never undo
    # identity registration to manufacture a retry after an uncertain write.
    for namespace, ident in (('occurrence', request['occurrence_id']), ('request', request['request_id'])):
        marker = registry/(namespace+'-'+ident+'.json')
        try:
            write_selected(marker, dict(schema='cairn.paired-preparation-intent/1',
                request_id=request['request_id'], occurrence_id=request['occurrence_id'],
                request_sha256=canonical_sha(request), output_sha256=canonical_sha(str(output)),
                state='held_preparation', provider_launched=False))
        except FileExistsError:
            raise Refusal(namespace+'_registered') from None
    output.mkdir(mode=0o700)
    write_selected(output/'request.json', request)
    selected = dict(schema='cairn.paired-eval.preparation/1', status='prepared_for_review',
                    request_sha256=canonical_sha(request), runtime_admitted=False,
                    evidence_basis='declared pinned input; no authorization or runtime attestation')
    write_selected(output/'preparation.json', selected)
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check').add_argument('request')
    preparation = commands.add_parser('prepare')
    preparation.add_argument('request')
    preparation.add_argument('--registry', required=True)
    commands.add_parser('launch').add_argument('request')
    args = parser.parse_args(argv)
    try:
        need(args.command != 'launch', 'provider_execution_unavailable')
        request = read_json(args.request)
        now = dt.datetime.now(dt.timezone.utc)
        if args.command == 'prepare':
            selected = prepare(request, registry=args.registry, now=now)
        else:
            validate(request, now=now)
            selected = dict(status='validated_for_review', runtime_admitted=False,
                            request_sha256=canonical_sha(request))
    except Refusal as error:
        selected = dict(status='refused', code=str(error))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        # Public errors omit paths, payloads and the original exception text.
        selected = dict(status='refused', code='invalid_or_unavailable_input')
    print(json.dumps(selected, sort_keys=True))
    return 2 if selected['status'] == 'refused' else 0


if __name__ == '__main__':
    raise SystemExit(main())
