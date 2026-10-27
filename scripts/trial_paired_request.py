"""Offline paired request preparation. This entry point never launches an actor."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import uuid

from trial_task_input import load_input
from trial_task_input import corpus_entities
# The pinned-document primitives live in pinned_document; these names stay
# re-exported for existing importers and pinned trial_sdk_* sources.
from pinned_document import (FILE_LIMIT, PIN_LIMIT, Refusal, canonical_sha, decode_json,  # noqa: F401
                             need, pin, pin_shape, plain_path, read_bytes, read_json,
                             read_json_pin, read_pin_bytes, reject_nonfinite, scan_refusal,
                             selected_bytes, shape, unique_object, utc, write_selected,
                             write_selected_bytes)

SCHEMA = 'cairn.paired-eval.request/2'
REVIEWS = ('task', 'oracle', 'comparator', 'supervisor', 'native', 'integration')
ORIGINAL_LIMITS = dict(selected_input_bytes=9500, final_answer_bytes=8192,
                      tool_room_bytes=32000, native_searches=2, pulls=4,
                      hook_seconds=5, last_native_memory_seconds=30)
IMPLEMENTATION_SOURCE_COMMIT = '6fb0fcd07f9cd7f8959190088bb9d070ff863764'
IMPLEMENTATION_SOURCE_SHA = '43a097332584a501b752e8fe15ded91d204bb8a62af34180ec5694b763b188b2'
IMPLEMENTATION_SETTINGS_SHA = '98b58eec1a3554ec8604c9508867684bd63a78ab482c68acab1e90af2a09261d'


def identifier(text):
    try:
        need(isinstance(text, str) and str(uuid.UUID(text)) == text, 'canonical_uuid')
    except (ValueError, AttributeError):
        raise Refusal('canonical_uuid') from None


def mcp_arguments(binding, *, profile='paired'):
    """Compile the fixed paired MCP ceiling; this function runs no process.

    The integrator owns authenticated trial bindings and the binary source pin.
    Caller-supplied argument extensions cannot override the original limits.
    """
    need(profile in ('paired', 'weekly-sdk'), 'mcp_profile')
    shape(binding, 'socket token_file repo task run', 'mcp_binding')
    for name in ('socket','token_file'):
        plain_path(binding[name])
    if not (profile == 'weekly-sdk' and binding['repo'] == 'trial:task-eval'):
        plain_path(binding['repo'])
    for name in ('task','run'):
        need(isinstance(binding[name], str) and 0 < len(binding[name].encode()) <= 256
             and '\0' not in binding[name], 'mcp_scope')
    return ['mcp','--socket',binding['socket'],'--token-file',binding['token_file'],
            '--repo',binding['repo'],'--task',binding['task'],'--run',binding['run'],
            '--tokens',str(ORIGINAL_LIMITS['tool_room_bytes']),
            '--max-tokens',str(ORIGINAL_LIMITS['tool_room_bytes'])]


def capacity_check_v1(reference, plan, now, models):
    """Historical source-only /1 validation; never used by request preparation."""
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


PROVIDER_COUNTERS = ('provider_requests', 'input_tokens', 'output_tokens',
                     'cache_read_input_tokens', 'cache_creation_input_tokens')
CAPACITY_ROLES = ('CURRENT', 'CAND', 'primary', 'secondary',
                  'selector', 'control', 'retry', 'dispute', 'auxiliary')
CAPACITY_INTEGER_MAX = 2**63 - 1
CAPACITY_METRICS = {counter: (counter,) for counter in PROVIDER_COUNTERS}
CAPACITY_METRICS.update(total_tokens=PROVIDER_COUNTERS[1:],
                        input_and_cache_creation_tokens=('input_tokens', 'cache_creation_input_tokens'))


def capacity_integer(value, minimum=0):
    return type(value) is int and minimum <= value <= CAPACITY_INTEGER_MAX


def capacity_name(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value) is not None


def provider_workload(plan, models):
    need('workload' in plan, 'workload_required')
    workload = read_json_pin(plan['workload'])
    shape(workload, 'schema roles', 'workload_shape')
    sharded = workload['schema'] == 'cairn.paired-provider-workload/3'
    need(workload['schema'] in ('cairn.paired-provider-workload/2',
                                'cairn.paired-provider-workload/3'), 'workload_schema')
    allocation = None
    pair_counts = None
    if sharded:
        from trial_weekly_accounts import validate as validate_account_assignment
        need('account_assignment' in plan, 'account_assignment_required')
        allocation, _, pair_counts = validate_account_assignment(plan['account_assignment'], plan)
    else:
        need('account_assignment' not in plan, 'account_assignment_unexpected')
    shape(workload['roles'], ' '.join(CAPACITY_ROLES), 'workload_roles')
    shape(models, 'CURRENT CAND primary secondary', 'workload_models')
    need(capacity_integer(plan['attempts'], 2) and plan['attempts'] % 2 == 0, 'attempt_count')
    jobs = {}
    for role, group in workload['roles'].items():
        shape(group, 'state basis jobs', 'workload_group')
        pin(group['basis'])
        need(isinstance(group['jobs'], list) and len(group['jobs']) <= 64, 'workload_jobs')
        if group['state'] == 'not_applicable':
            need(role not in models and not group['jobs'], 'workload_not_applicable')
            continue
        need(group['state'] == 'included' and bool(group['jobs']), 'workload_unknown')
        if role in models:
            need((len(group['jobs']) == len(pair_counts) if sharded else len(group['jobs']) == 1),
                 'workload_primary_count')
        role_accounts = set()
        role_total = 0
        for job in group['jobs']:
            shape(job, 'id model count per_job account_id' if sharded else 'id model count per_job',
                  'workload_job')
            need(capacity_name(job['id']) and job['id'] not in jobs, 'workload_job_identity')
            need(capacity_name(job['model']) and capacity_integer(job['count'], 1), 'workload_job_bound')
            if role in models:
                if sharded:
                    account_id = job['account_id']
                    need(account_id in pair_counts and account_id not in role_accounts
                         and job['count'] == pair_counts[account_id], 'workload_role_mismatch')
                    role_accounts.add(account_id)
                    role_total += job['count']
                else:
                    need(job['count'] == plan['attempts']//2, 'workload_role_mismatch')
                need(job['model'] == models[role], 'workload_role_mismatch')
            elif sharded:
                need(job['account_id'] in pair_counts, 'workload_job_account')
            shape(job['per_job'], ' '.join(PROVIDER_COUNTERS), 'workload_counters')
            need(all(capacity_integer(value, 1 if name == 'provider_requests' else 0)
                     for name, value in job['per_job'].items()), 'workload_counter_bound')
            if role in models:
                need(sum(job['per_job'][name] for name in PROVIDER_COUNTERS if name not in
                         ('provider_requests', 'output_tokens')) > 0 and job['per_job']['output_tokens'] > 0,
                     'workload_inference_tokens')
            jobs[job['id']] = job
        if sharded and role in models:
            need(role_accounts == set(pair_counts) and role_total == plan['attempts']//2,
                 'workload_role_mismatch')
    return jobs


def provider_demands(reference, plan, jobs, accounts):
    binding = read_json_pin(reference)
    shape(binding, 'schema plan_sha256 state routes', 'route_binding_shape')
    need(binding['schema'] == 'cairn.paired-route-binding/2' and
         binding['plan_sha256'] == plan['sha256'] and binding['state'] == 'complete',
         'route_binding_unknown')
    routes = binding['routes']
    need(isinstance(routes, dict) and set(routes) == set(jobs), 'route_workloads')
    totals, used_accounts, window_metrics = {}, set(), {}
    for ident, job in jobs.items():
        route = routes[ident]
        shape(route, 'account_id model windows', 'route_shape')
        need(capacity_name(route['account_id']) and route['model'] == job['model'], 'route_model_mismatch')
        if 'account_id' in job:
            need(route['account_id'] == job['account_id'], 'route_assignment_mismatch')
        need(route['account_id'] in accounts, 'account_route_unknown')
        used_accounts.add(route['account_id'])
        need(isinstance(route['windows'], list) and 0 < len(route['windows']) <= 64, 'route_windows')
        seen, covered = set(), set()
        for window in route['windows']:
            shape(window, 'pool_id window_id metrics', 'route_window_shape')
            need(capacity_name(window['pool_id']) and capacity_name(window['window_id']), 'route_window_identity')
            identity = (window['pool_id'], window['window_id'])
            need(identity not in seen, 'duplicate_window')
            seen.add(identity)
            metrics = window['metrics']
            need(isinstance(metrics, list) and 0 < len(metrics) <= len(CAPACITY_METRICS)
                 and all(isinstance(m, str) and m in CAPACITY_METRICS for m in metrics)
                 and len(set(metrics)) == len(metrics), 'route_metrics')
            metric_set = frozenset(metrics)
            need(identity not in window_metrics or window_metrics[identity] == metric_set,
                 'route_window_metrics_mismatch')
            window_metrics[identity] = metric_set
            for metric in metrics:
                counters = CAPACITY_METRICS[metric]
                covered.update(counters)
                key = (*identity, metric)
                units = job['count'] * sum(job['per_job'][counter] for counter in counters)
                totals[key] = totals.get(key, 0) + units
                need(capacity_integer(totals[key]), 'capacity_total_bound')
        need(covered == set(PROVIDER_COUNTERS), 'route_counter_coverage')
    need(used_accounts == accounts, 'account_route_unknown')
    return totals


def provider_resources(resources, totals):
    need(isinstance(resources, list) and 0 < len(resources) <= 1024, 'capacity_resources')
    seen = set()
    for resource in resources:
        shape(resource, 'pool_id window_id metric remaining required', 'capacity_resource_shape')
        need(all(capacity_name(resource[k]) for k in ('pool_id', 'window_id', 'metric')),
             'capacity_binding_unknown')
        key = tuple(resource[k] for k in ('pool_id', 'window_id', 'metric'))
        need(key in totals and key not in seen, 'capacity_resource_mapping')
        seen.add(key)
        need(capacity_integer(resource['remaining']) and capacity_integer(resource['required']), 'capacity_resource_bound')
        need(resource['required'] == totals[key], 'capacity_estimate_mismatch')
        need(resource['remaining'] >= resource['required'], 'capacity_insufficient')
    need(seen == set(totals), 'capacity_resource_mapping')


def capacity_accounts(reference):
    accounts = read_json_pin(reference)
    shape(accounts, 'schema accounts', 'account_binding_shape')
    need(accounts['schema'] == 'cairn.paired-account-binding/1' and
         isinstance(accounts['accounts'], list) and 0 < len(accounts['accounts']) <= 64,
         'account_binding_unknown')
    identities = set()
    for account in accounts['accounts']:
        shape(account, 'account_id dedicated interactive_use', 'account_shape')
        need(capacity_name(account['account_id']) and account['dedicated'] is True
             and account['interactive_use'] is False, 'dedicated_account_unknown')
        need(account['account_id'] not in identities, 'duplicate_account')
        identities.add(account['account_id'])
    return identities


def capacity_check(reference, plan, now, models):
    """Validate /2 declarations against the plan's separately pinned workload.

    This neither authenticates the declarations nor admits provider execution.
    """
    capacity = read_json_pin(reference)
    need(isinstance(capacity, dict) and capacity.get('schema') == 'cairn.paired-capacity-evidence/2', 'capacity_v2_required')
    shape(capacity, 'schema plan_sha256 state observed_at valid_until account_binding route_binding reservation resources', 'capacity_shape')
    need(capacity['state'] == 'declared', 'capacity_unknown')
    need(capacity['plan_sha256'] == plan['sha256'], 'capacity_plan_mismatch')
    need(utc(capacity['observed_at']) <= now < utc(capacity['valid_until']), 'capacity_stale')
    jobs = provider_workload(plan, models)
    accounts = capacity_accounts(capacity['account_binding'])
    if 'account_assignment' in plan:
        from trial_weekly_accounts import validate as validate_account_assignment
        assignment, _, _ = validate_account_assignment(plan['account_assignment'], plan)
        need(accounts == {account['account_id'] for account in assignment['accounts']},
             'capacity_account_assignment_mismatch')
    totals = provider_demands(capacity['route_binding'], plan, jobs, accounts)
    provider_resources(capacity['resources'], totals)
    reservation = read_json_pin(capacity['reservation'])
    shape(reservation, 'schema plan_sha256 resources_sha256 state valid_until', 'reservation_shape')
    need(reservation['schema'] == 'cairn.paired-reservation/2' and reservation['state'] == 'held'
         and utc(reservation['valid_until']) >= utc(capacity['valid_until'])
         and reservation['plan_sha256'] == plan['sha256']
         and reservation['resources_sha256'] == canonical_sha(capacity['resources']), 'reservation_unknown')


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


def permission_profile_check(profile):
    shape(profile, 'name source settings', 'permission_profile_shape')
    need(profile['name'] == 'implementation/1', 'permission_profile_unknown')
    source = read_json_pin(profile['source'])
    need(isinstance(source, dict) and source.get('commit') == IMPLEMENTATION_SOURCE_COMMIT
         and isinstance(source.get('files'), dict)
         and source['files'].get('scripts/trial_task_permissions.py') == IMPLEMENTATION_SOURCE_SHA,
         'permission_source_mismatch')
    settings = read_json_pin(profile['settings'])
    need(canonical_sha(settings) == IMPLEMENTATION_SETTINGS_SHA, 'permission_settings_mismatch')
    # Manifest assertions and settings are source declarations. The runner must
    # verify its actual loaded constructor, native settings and confinement.


def validate(request, *, now):
    shape(request, 'schema request_id campaign_id occurrence_id scheduled_at expires_at frozen_input plan interfaces dispositions limits capacity corpus_selection grading output reply supervisor_source permission_profile', 'request_shape')
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
    shape(plan, 'instrument cases arms seeds attempts workload sha256', 'plan_shape')
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
    permission_profile_check(request['permission_profile'])
    grading_check(request['grading'], {review.get('reviewer_id') for review in request['dispositions'].values()
                                     if isinstance(review, dict)})
    subjects = dict(task=hashlib.sha256(read_bytes(input_root/'input.json')).hexdigest(),
        oracle=request['grading']['rubric']['sha256'],
        comparator=canonical_sha(dict(plan=plan, interfaces=request['interfaces'])),
        supervisor=request['supervisor_source']['sha256'],
        native=canonical_sha(dict(binary_sha256=bundle['document']['native']['binary']['sha256'],
                                  permission_profile=request['permission_profile'])),
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


def check_input_files(root):
    count, total = 0, 0
    for directory, names, files in os.walk(root, followlinks=False, onerror=scan_refusal):
        count += len(names) + len(files)
        need(count <= 4096, 'input_file_count')
        for name in names:
            need(not (Path(directory)/name).is_symlink(), 'path_indirection')
        for name in files:
            total += len(read_bytes(Path(directory)/name))
            need(total <= 64*1024*1024, 'input_byte_limit')


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


def checked_request(request, *, now, companion=None, review=None):
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    bundle = validate(request, now=now)
    evidence, retained = None, {}
    if request['plan']['instrument'] == 'weekly-frozen-cases':
        need(companion is not None and review is not None, 'weekly_companion_required')
        # Lazy boundary: mapping reuses this module's validators. The CLI below
        # dispatches through the canonical import so Refusal has one identity.
        from trial_paired_companion import validate_companion
        evidence, retained = validate_companion(request, companion, review, now=now,
            native_model=bundle['document']['native']['model'])
    else:
        need(companion is None and review is None, 'supplemental_companion_unsupported')
    return request, bundle, evidence, retained


def check(request, *, now, companion=None, review=None):
    request, _, evidence, _ = checked_request(request, now=now, companion=companion, review=review)
    result = dict(status='validated_for_review', runtime_admitted=False,
                  request_sha256=canonical_sha(request))
    if evidence is not None:
        result['provider_evidence'] = evidence
    return result


def prepare(request, *, registry, now, companion=None, review=None):
    request, bundle, evidence, retained = checked_request(request, now=now, companion=companion, review=review)
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
    if evidence is not None:
        selected['provider_evidence'] = evidence
        for name, content in retained.items():
            write_selected_bytes(output/name, content)
    write_selected(output/'preparation.json', selected)
    return selected


def main(argv=None):
    class PublicParser(argparse.ArgumentParser):
        def error(self, message):
            raise Refusal('invalid_arguments')

    parser = PublicParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    checking = commands.add_parser('check')
    checking.add_argument('request')
    preparation = commands.add_parser('prepare')
    preparation.add_argument('request')
    preparation.add_argument('--registry', required=True)
    for command in (checking, preparation):
        command.add_argument('--companion')
        command.add_argument('--companion-sha256')
        command.add_argument('--review')
        command.add_argument('--review-sha256')
    commands.add_parser('launch').add_argument('request')
    try:
        args = parser.parse_args(argv)
        need(args.command != 'launch', 'provider_execution_unavailable')
        references = {}
        for name in ('companion', 'review'):
            path, digest = getattr(args, name), getattr(args, name+'_sha256')
            need((path is None) == (digest is None), 'explicit_pin_required')
            references[name] = None if path is None else dict(path=path, sha256=digest)
        request = read_json(args.request)
        now = dt.datetime.now(dt.timezone.utc)
        if args.command == 'prepare':
            selected = prepare(request, registry=args.registry, now=now, **references)
        else:
            selected = check(request, now=now, **references)
    except Refusal as error:
        selected = dict(status='refused', code=str(error))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        # Public errors omit paths, payloads and the original exception text.
        selected = dict(status='refused', code='invalid_or_unavailable_input')
    print(json.dumps(selected, sort_keys=True))
    return 2 if selected['status'] == 'refused' else 0


if __name__ == '__main__':
    from trial_paired_request import main
    raise SystemExit(main())
