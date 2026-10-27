"""Pure offline grading joins. Declared consistency never authenticates custody."""
import json
import re

from trial_paired_request import FILE_LIMIT, Refusal, canonical_sha, capacity_name, need, shape
import pinned_document as pinned
from trial_provider_mapping import original_plan


def _name(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value),
         'grading_identifier')


def _digest(value):
    pinned.hex_digest(value, 'grading_digest')


def _pair(row):
    _name(row['case'])
    need(type(row['seed']) is int, 'grading_seed')
    return row['case'], row['seed']


def _schedule(bundle):
    shape(bundle, 'schema binding plan schedule inventory selection packets policy', 'grading_bundle')
    if bundle['schema'] == LEGACY_SCHEMA:
        return _legacy_schedule(bundle)
    need(bundle['schema'] == 'cairn.grading-join-input/1', 'grading_schema')
    binding = bundle['binding']
    shape(binding, 'request_id campaign_id occurrence_id request_sha256 rubric_sha256 '
          'changed_pair_definition_sha256 blinding_policy_sha256 '
          'plan_sha256 schedule_sha256 inventory_sha256 selection_sha256 packets_sha256 policy_sha256',
          'grading_binding')
    for field, value in binding.items():
        (_digest if field.endswith('_sha256') else _name)(value)
    for field in ('plan', 'schedule', 'inventory', 'selection', 'packets', 'policy'):
        need(binding[field + '_sha256'] == canonical_sha(bundle[field]), 'grading_binding_mismatch')
    original_plan(bundle['plan'])
    schedule = bundle['schedule']
    shape(schedule, 'schema plan_sha256 source execution actor_api_base wording distractors runs sha256',
          'grading_schedule')
    need(schedule['schema'] == 'cairn.weekly-schedule/1' and schedule['wording'] == 'task'
         and schedule['plan_sha256'] == bundle['plan']['sha256'], 'grading_schedule_identity')
    need(schedule['sha256'] == canonical_sha({k: v for k, v in schedule.items() if k != 'sha256'}),
         'grading_schedule_hash')
    need(type(schedule['distractors']) is dict and type(schedule['distractors'].get('count')) is int
         and schedule['distractors']['count'] == 1900, 'grading_distractors')
    expected = {(case, seed, arm) for case in bundle['plan']['cases']
                for seed in bundle['plan']['seeds'] for arm in bundle['plan']['arms']}
    need(type(schedule['runs']) is list and len(schedule['runs']) == 126, 'grading_schedule_coverage')
    observed, positions = set(), set()
    for run in schedule['runs']:
        shape(run, 'case seed arm position', 'grading_run')
        pair = _pair(run)
        _name(run['arm'])
        key = (*pair, run['arm'])
        need(type(run['position']) is int and run['position'] in (0, 1), 'grading_position')
        position = (*pair, run['position'])
        need(key in expected and key not in observed and position not in positions,
             'grading_schedule_coverage')
        observed.add(key)
        positions.add(position)
    need(observed == expected, 'grading_schedule_coverage')
    return sorted({key[:2] for key in expected})


LEGACY_SCHEMA = 'cairn.legacy-grading-join-input/1'
LEGACY_PLAN = 'cairn.legacy-weekly-plan/1'
LEGACY_PAIR_BINDING = 'cairn.legacy-pair-binding/1'
LEGACY_ARMS = ['CURRENT', 'CAND']


def _legacy_schedule(bundle):
    """Separate branch for legacy harness output (docs/legacy-grading-bundle.md).

    The schedule is exactly the runner's planned identities, the list whose
    canonical hash is the runner's schedule_sha256; the plan carries the
    declared pair binding and the adapter's observations in separate fields.
    The SDK weekly-schedule/1 path above is not used or changed.
    """
    binding = bundle['binding']
    shape(binding, 'request_id campaign_id occurrence_id request_sha256 rubric_sha256 '
          'changed_pair_definition_sha256 blinding_policy_sha256 '
          'plan_sha256 schedule_sha256 inventory_sha256 selection_sha256 packets_sha256 policy_sha256',
          'grading_binding')
    for field, value in binding.items():
        (_digest if field.endswith('_sha256') else _name)(value)
    for field in ('plan', 'schedule', 'inventory', 'selection', 'packets', 'policy'):
        need(binding[field + '_sha256'] == canonical_sha(bundle[field]), 'grading_binding_mismatch')
    plan = bundle['plan']
    shape(plan, 'schema cases arms seeds attempts pairs distractors declared observed sha256', 'legacy_plan')
    need(plan['schema'] == LEGACY_PLAN and plan['arms'] == LEGACY_ARMS, 'legacy_plan')
    need(type(plan['cases']) is list and len(plan['cases']) == 21 and len(set(plan['cases'])) == 21
         and type(plan['seeds']) is list and len(plan['seeds']) == 3 and len(set(plan['seeds'])) == 3
         and plan['attempts'] == 126 and plan['pairs'] == 63 and plan['distractors'] == 1900, 'legacy_plan_shape')
    for case in plan['cases']:
        _name(case)
        need('.' not in case, 'legacy_plan_shape')
    need(all(type(seed) is int and 0 <= seed <= 2**31-1 for seed in plan['seeds']), 'legacy_plan_shape')
    need(plan['sha256'] == canonical_sha({k: v for k, v in plan.items() if k != 'sha256'}), 'legacy_plan_hash')
    declared, observed = plan['declared'], plan['observed']
    shape(declared, 'pair_binding pair_binding_pin', 'legacy_declared')
    pair_binding, pin = declared['pair_binding'], declared['pair_binding_pin']
    shape(pair_binding, 'schema agent_sha256 plan_sha256 request_id request_sha256 campaign_id occurrence_id '
          'schedule_sha256 arm_roles', 'legacy_declared')
    shape(pin, 'path bytes sha256', 'legacy_declared')
    need(pair_binding['schema'] == LEGACY_PAIR_BINDING and type(pin['path']) is str and type(pin['bytes']) is int
         and pair_binding['arm_roles'] == {arm: arm for arm in LEGACY_ARMS}, 'legacy_declared')
    for field in ('agent_sha256', 'plan_sha256', 'request_sha256', 'schedule_sha256'):
        _digest(pair_binding[field])
    _digest(pin['sha256'])
    assignment_fields = {'account_assignment_sha256', 'run_accounts'}
    has_assignment = bool(set(observed) & assignment_fields)
    observed_fields = 'agent_sha256 plan_file_sha256 conversion_receipt_sha256 distractors positions'
    if has_assignment:
        observed_fields += ' account_assignment_sha256 run_accounts'
    shape(observed, observed_fields, 'legacy_observed')
    for field in ('agent_sha256', 'plan_file_sha256', 'conversion_receipt_sha256'):
        _digest(observed[field])
    # Declared values must match what was observed and what the join binds.
    need(pair_binding['agent_sha256'] == observed['agent_sha256']
         and pair_binding['plan_sha256'] == observed['plan_file_sha256']
         and observed['distractors'] == plan['distractors'], 'legacy_declared_observed_mismatch')
    need(all(binding[field] == pair_binding[field]
             for field in ('request_id', 'request_sha256', 'campaign_id', 'occurrence_id', 'schedule_sha256')),
         'legacy_pair_binding_mismatch')
    expected = {(case, seed, arm) for case in plan['cases'] for seed in plan['seeds'] for arm in LEGACY_ARMS}
    account_by_run = None
    if has_assignment:
        _digest(observed['account_assignment_sha256'])
        account_rows = observed['run_accounts']
        need(type(account_rows) is list and len(account_rows) == 126,
             'legacy_account_assignment_mismatch')
        expected_run_ids = {f'{case}.{arm}.s{seed}' for case, seed, arm in expected}
        account_by_run = {}
        for row in account_rows:
            shape(row, 'run_id account_id', 'legacy_account_assignment_mismatch')
            _name(row['run_id'])
            need(capacity_name(row['account_id']), 'legacy_account_assignment_mismatch')
            need(row['run_id'] in expected_run_ids and row['run_id'] not in account_by_run,
                 'legacy_account_assignment_mismatch')
            account_by_run[row['run_id']] = row['account_id']
        need(set(account_by_run) == expected_run_ids, 'legacy_account_assignment_mismatch')
    schedule = bundle['schedule']
    need(type(schedule) is list and len(schedule) == 126, 'grading_schedule_coverage')
    observed_runs = set()
    for run in schedule:
        shape(run, 'run_id case arm seed account_id' if has_assignment else 'run_id case arm seed', 'grading_run')
        key = (*_pair(run), run['arm'])
        need(key in expected and key not in observed_runs
             and run['run_id'] == f"{run['case']}.{run['arm']}.s{run['seed']}", 'grading_schedule_coverage')
        if has_assignment:
            need(capacity_name(run['account_id']), 'legacy_account_assignment_mismatch')
            need(run['account_id'] == account_by_run.get(run['run_id']),
                 'legacy_account_assignment_mismatch')
        observed_runs.add(key)
    need(observed_runs == expected and schedule == sorted(schedule, key=lambda r: r['run_id']),
         'grading_schedule_coverage')
    need(canonical_sha(schedule) == pair_binding['schedule_sha256'], 'legacy_bundle_schedule_mismatch')
    # Each retained attempt must carry the exact planned run identity.
    planned = {(run['case'], run['seed'], run['arm']): run['run_id'] for run in schedule}
    need(type(bundle['inventory']) is list and all(
        type(row) is dict and row.get('run_id') == planned.get((row.get('case'), row.get('seed'), row.get('arm')))
        for row in bundle['inventory']), 'legacy_attempt_identity')
    positions = observed['positions']
    need(type(positions) is list and len(positions) == 126, 'grading_schedule_coverage')
    seen, slots = set(), set()
    for row in positions:
        shape(row, 'case seed arm position', 'grading_run')
        key = (*_pair(row), row['arm'])
        need(type(row['position']) is int and row['position'] in (0, 1), 'grading_position')
        need(key in expected and key not in seen and (*key[:2], row['position']) not in slots,
             'grading_schedule_coverage')
        seen.add(key)
        slots.add((*key[:2], row['position']))
    return sorted({key[:2] for key in expected})


def _snapshot(document):
    try:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf8')
        need(len(encoded) <= FILE_LIMIT, 'grading_input_bound')
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, Refusal):
            raise
        raise Refusal('grading_json_input') from None


def _agreement(numerator, denominator):
    return dict(numerator=numerator, denominator=denominator,
                rate=numerator / denominator if denominator else None)


def _selection(rows, expected):
    need(type(rows) is list and len(rows) == len(expected), 'grading_selection_coverage')
    selected = {}
    for row in rows:
        shape(row, 'case seed state', 'grading_selection')
        key = _pair(row)
        need(key in expected and key not in selected, 'grading_selection_coverage')
        need(row['state'] in ('changed', 'unchanged', 'undetermined'), 'grading_selection_state')
        selected[key] = row['state']
    return selected


def _inventory(rows, expected):
    need(type(rows) is list and len(rows) <= 126, 'grading_inventory_bound')
    attempts, runs = {}, set()
    for row in rows:
        shape(row, 'case seed arm run_id state outcome evidence_sha256', 'grading_attempt')
        pair = _pair(row)
        _name(row['arm'])
        _name(row['run_id'])
        _name(row['outcome'])
        key = (*pair, row['arm'])
        need(pair in expected and row['arm'] in ('CURRENT', 'CAND') and key not in attempts,
             'grading_attempt_identity')
        need(row['run_id'] not in runs, 'grading_run_reused')
        need(row['state'] in ('completed', 'failed'), 'grading_attempt_state')
        if row['state'] == 'completed' or row['evidence_sha256'] is not None:
            _digest(row['evidence_sha256'])
        attempts[key] = row
        runs.add(row['run_id'])
    return attempts


def _packets(rows, selected):
    need(type(rows) is list and len(rows) <= 63, 'grading_packets_bound')
    packets, identities, samples = {}, set(), set()
    for row in rows:
        shape(row, 'case seed pair_id packet_sha256 samples', 'grading_packet')
        key = _pair(row)
        _name(row['pair_id'])
        _digest(row['packet_sha256'])
        need(selected.get(key) == 'changed' and key not in packets, 'grading_packet_pair')
        need(row['pair_id'] not in identities, 'grading_packet_identity')
        need(type(row['samples']) is list and len(row['samples']) == 2, 'grading_packet_samples')
        for sample in row['samples']:
            _name(sample)
            need(sample not in samples, 'grading_sample_reused')
            samples.add(sample)
        packets[key] = row
        identities.add(row['pair_id'])
    return packets


def _policy(policy):
    shape(policy, 'comparison verdict_labels graders excluded_agents excluded_sessions coordinator',
          'grading_policy')
    need(policy['comparison'] == 'exact-sample-verdicts/1', 'grading_comparison')
    shape(policy['graders'], 'primary secondary', 'grading_graders')
    for field in ('verdict_labels', 'excluded_agents', 'excluded_sessions'):
        names = policy[field]
        need(type(names) is list and len(names) <= 1024, 'grading_policy_list')
        for name in names:
            _name(name)
        need(len(set(names)) == len(names), 'grading_policy_duplicate')
    need(0 < len(policy['verdict_labels']) <= 32, 'grading_labels')
    coordinator = policy['coordinator']
    shape(coordinator, 'agent_id session_id delegation_sha256', 'grading_coordinator')
    _name(coordinator['agent_id'])
    _name(coordinator['session_id'])
    if coordinator['delegation_sha256'] is not None:
        _digest(coordinator['delegation_sha256'])
    agents = set(policy['excluded_agents']) | {coordinator['agent_id']}
    sessions = set(policy['excluded_sessions']) | {coordinator['session_id']}
    for grader in policy['graders'].values():
        shape(grader, 'agent_id session_id', 'grading_grader')
        _name(grader['agent_id'])
        _name(grader['session_id'])
        need(grader['agent_id'] not in agents and grader['session_id'] not in sessions,
             'grading_not_independent')
        agents.add(grader['agent_id'])
        sessions.add(grader['session_id'])


def _submissions(rows, bundle, packets):
    need(type(rows) is list and len(rows) <= 1024, 'grading_submissions_bound')
    by_id = {packet['pair_id']: packet for packet in packets.values()}
    retained, identities = {}, {}
    for row in rows:
        shape(row, 'submission_id binding_sha256 pair_id packet_sha256 rubric_sha256 '
              'slot agent_id session_id state verdicts reason_sha256', 'grading_submission')
        for field in ('submission_id', 'pair_id', 'slot', 'agent_id', 'session_id'):
            _name(row[field])
        for field in ('binding_sha256', 'packet_sha256', 'rubric_sha256', 'reason_sha256'):
            _digest(row[field])
        packet = by_id.get(row['pair_id'])
        need(packet is not None and row['packet_sha256'] == packet['packet_sha256']
             and row['binding_sha256'] == canonical_sha(bundle['binding'])
             and row['rubric_sha256'] == bundle['binding']['rubric_sha256'], 'grading_submission_binding')
        need(row['slot'] in ('primary', 'secondary'), 'grading_slot')
        grader = bundle['policy']['graders'][row['slot']]
        need(all(row[field] == grader[field] for field in ('agent_id', 'session_id')), 'grading_grader_mismatch')
        owner = row['pair_id'], row['slot']
        need(identities.get(row['submission_id'], owner) == owner, 'grading_submission_id_reused')
        identities[row['submission_id']] = owner
        _verdicts(row, packet['samples'], bundle['policy']['verdict_labels'])
        digest = canonical_sha(row)
        retained[digest] = dict(sha256=digest, submission=row)
    return [retained[digest] for digest in sorted(retained)]


def _verdicts(row, samples, labels):
    need(row['state'] in ('determinate', 'undetermined'), 'grading_grade_state')
    if row['state'] == 'undetermined':
        need(row['verdicts'] is None, 'grading_undetermined_verdict')
    else:
        need(type(row['verdicts']) is dict and set(row['verdicts']) == set(samples),
             'grading_verdict_samples')
        need(all(verdict in labels for verdict in row['verdicts'].values()), 'grading_verdict_label')


def _dispositions(rows, bundle, pairs):
    need(type(rows) is list and len(rows) <= 256, 'grading_dispositions_bound')
    by_id = {pair['packet']['pair_id']: pair for pair in pairs if pair['packet'] is not None}
    retained, identities = {}, {}
    for row in rows:
        shape(row, 'disposition_id binding_sha256 pair_id grade_sha256 agent_id session_id '
              'delegation_sha256 state verdicts reason_sha256', 'grading_disposition')
        for field in ('disposition_id', 'pair_id', 'agent_id', 'session_id'):
            _name(row[field])
        for field in ('binding_sha256', 'delegation_sha256', 'reason_sha256'):
            _digest(row[field])
        need(row['binding_sha256'] == canonical_sha(bundle['binding']), 'grading_disposition_binding')
        coordinator = bundle['policy']['coordinator']
        need(all(row[field] == coordinator[field] for field in coordinator), 'grading_coordinator_mismatch')
        pair = by_id.get(row['pair_id'])
        need(pair is not None and pair['declared_comparison'] == 'disagreement', 'grading_disposition_pair')
        need(identities.get(row['disposition_id'], row['pair_id']) == row['pair_id'],
             'grading_disposition_id_reused')
        identities[row['disposition_id']] = row['pair_id']
        shape(row['grade_sha256'], 'primary secondary', 'grading_disposition_grades')
        need(row['grade_sha256'] == {slot: grades[0]['sha256'] for slot, grades in pair['grades'].items()},
             'grading_disposition_grades')
        _verdicts(row, pair['packet']['samples'], bundle['policy']['verdict_labels'])
        digest = canonical_sha(row)
        retained[digest] = dict(sha256=digest, disposition=row)
    ordered = [retained[digest] for digest in sorted(retained)]
    for pair in pairs:
        pair['dispositions'] = [entry for entry in ordered if pair['packet'] is not None
                                and entry['disposition']['pair_id'] == pair['packet']['pair_id']]
        count = len(pair['dispositions'])
        pair['disposition_state'] = 'missing' if not count else 'declared' if count == 1 else 'conflict'
        pair['resolution'] = 'unresolved' if pair['declared_comparison'] == 'disagreement' else 'not_applicable'
    return ordered


def _compare(pair, packet, submissions):
    pair['packet'] = packet
    pair['grades'] = {slot: [] for slot in ('primary', 'secondary')}
    if packet is not None:
        for entry in submissions:
            grade = entry['submission']
            if grade['pair_id'] == packet['pair_id']:
                pair['grades'][grade['slot']].append(entry)
    states = {slot: 'missing' if not grades else 'conflict' if len(grades) > 1
              else grades[0]['submission']['state'] for slot, grades in pair['grades'].items()}
    pair['grade_states'] = states
    pair['issues'] = []
    if pair['selection'] != 'changed':
        pair['declared_comparison'] = 'not_selected'
        return
    if any(attempt['state'] != 'completed' for attempt in pair['attempts'].values()):
        pair['issues'].append('attempt_unavailable')
    if packet is None:
        pair['issues'].append('missing_packet')
    pair['issues'].extend(slot + '_' + state for slot, state in states.items() if state != 'determinate')
    if pair['issues']:
        pair['declared_comparison'] = 'ineligible'
    else:
        first, second = (pair['grades'][slot][0]['submission']['verdicts'] for slot in ('primary', 'secondary'))
        pair['declared_comparison'] = 'agreement' if first == second else 'disagreement'


def _aggregate(pairs, attempts):
    agreements = sum(pair['declared_comparison'] == 'agreement' for pair in pairs)
    disagreements = sum(pair['declared_comparison'] == 'disagreement' for pair in pairs)
    changed = [pair for pair in pairs if pair['selection'] == 'changed']
    return dict(attempts=dict(expected=126, observed=len(attempts), missing=126-len(attempts),
                                  completed=sum(row['state'] == 'completed' for row in attempts.values()),
                                  failed=sum(row['state'] == 'failed' for row in attempts.values())),
                     selection={state: sum(row['selection'] == state for row in pairs)
                                for state in ('changed', 'unchanged', 'undetermined')},
                     selection_completeness='undetermined' if any(pair['selection'] == 'undetermined' for pair in pairs) else 'declared',
                     changed_pair_coverage=dict(denominator=len(changed),
                         packets=sum(pair['packet'] is not None for pair in changed),
                         primary_present=sum(bool(pair['grades']['primary']) for pair in changed),
                         secondary_present=sum(bool(pair['grades']['secondary']) for pair in changed),
                         unconflicted_dual=sum(all(len(grades) == 1 for grades in pair['grades'].values())
                                              for pair in changed),
                         determinate_dual=agreements + disagreements),
                     changed_pair_issues={issue: sum(issue in pair['issues'] for pair in changed)
                         for issue in ('attempt_unavailable', 'missing_packet', 'primary_missing',
                                       'secondary_missing', 'primary_conflict', 'secondary_conflict',
                                       'primary_undetermined', 'secondary_undetermined')},
                     disagreements=disagreements,
                     declared_dispositions=sum(pair['disposition_state'] == 'declared' for pair in changed),
                     disposition_conflicts=sum(pair['disposition_state'] == 'conflict' for pair in changed),
                     declared_raw_agreement=_agreement(agreements, agreements + disagreements),
                     verified_raw_agreement=_agreement(0, 0), custody='unknown',
                     admission=False, actual_campaign_success=False)


def join_results(bundle, submissions, dispositions):
    """Return an owned projection of borrowed JSON values; perform no I/O."""
    bundle, submissions, dispositions = _snapshot([bundle, submissions, dispositions])
    expected = _schedule(bundle)
    _policy(bundle['policy'])
    selected = _selection(bundle['selection'], expected)
    attempts = _inventory(bundle['inventory'], expected)
    packets = _packets(bundle['packets'], selected)
    retained = _submissions(submissions, bundle, packets)
    pairs = [dict(case=case, seed=seed, selection=selected[case, seed],
                  attempts={arm: attempts.get((case, seed, arm), dict(state='missing'))
                            for arm in ('CURRENT', 'CAND')}) for case, seed in expected]
    for pair in pairs:
        _compare(pair, packets.get((pair['case'], pair['seed'])), retained)
    retained_dispositions = _dispositions(dispositions, bundle, pairs)
    return dict(schema='cairn.grading-join-report/1', binding=bundle['binding'],
                binding_sha256=canonical_sha(bundle['binding']), input_sha256=canonical_sha(bundle),
                pairs=pairs, submissions=retained, dispositions=retained_dispositions, aggregate=_aggregate(pairs, attempts))
