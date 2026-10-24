"""Pinned per-pair account assignments for the Codex weekly evaluation."""
from pathlib import Path

import trial_paired_request as paired

SCHEMA = 'cairn.weekly-account-assignment/1'
ACCOUNT_FIELDS = 'account_id auth_file auth_sha256 config_home'
ATTEMPT_FIELDS = 'run_id case arm seed account_id'
ARMS = ('CURRENT', 'CAND')


def validate(reference, plan):
    """Read and validate the complete, hash-pinned case/seed to account map."""
    paired.pin(reference)
    document = paired.read_json_pin(reference)
    paired.shape(document, 'schema strategy accounts assignments', 'account_assignment_shape')
    paired.need(document['schema'] == SCHEMA and
                document['strategy'] in ('balanced-case-seed-rotation/1', 'explicit/1'),
                'account_assignment_schema')
    accounts = document['accounts']
    paired.need(type(accounts) is list and 1 <= len(accounts) <= 64, 'account_assignment_accounts')
    account_ids = []
    for account in accounts:
        paired.shape(account, ACCOUNT_FIELDS, 'account_assignment_account')
        paired.need(paired.capacity_name(account['account_id']), 'account_assignment_account')
        auth = account['auth_file']
        home = account['config_home']
        paired.need(type(auth) is str and type(home) is str and Path(auth).is_absolute()
                    and Path(home).is_absolute() and Path(auth).parent == Path(home)
                    and Path(auth).name == 'auth.json', 'account_assignment_account')
        digest = account['auth_sha256']
        paired.need(type(digest) is str and len(digest) == 64
                    and all(ch in '0123456789abcdef' for ch in digest), 'account_assignment_account')
        account_ids.append(account['account_id'])
    paired.need(len(account_ids) == len(set(account_ids)), 'account_assignment_duplicate_account')
    cases, seeds = plan['cases'], plan['seeds']
    paired.need(plan.get('arms') == list(ARMS), 'account_assignment_plan')
    paired.need(type(cases) is list and cases and all(paired.capacity_name(case) for case in cases)
                and len(set(cases)) == len(cases), 'account_assignment_plan')
    paired.need(type(seeds) is list and seeds and all(type(seed) is int and seed >= 0 for seed in seeds)
                and len(set(seeds)) == len(seeds), 'account_assignment_plan')
    expected_attempts = [(f'{case}.{arm}.s{seed}', case, arm, seed)
                         for seed in seeds for case in cases for arm in ARMS]
    rows = document['assignments']
    paired.need(type(rows) is list and len(rows) == len(expected_attempts), 'account_assignment_coverage')
    counts = {account_id: 0 for account_id in account_ids}
    pair_accounts = {}
    for row, expected in zip(rows, expected_attempts):
        paired.shape(row, ATTEMPT_FIELDS, 'account_assignment_row')
        paired.need(tuple(row[key] for key in ('run_id', 'case', 'arm', 'seed')) == expected
                    and paired.capacity_name(row['account_id'])
                    and row['account_id'] in counts,
                    'account_assignment_coverage')
        pair = (row['case'], row['seed'])
        previous = pair_accounts.setdefault(pair, row['account_id'])
        paired.need(previous == row['account_id'], 'account_assignment_pair_split')
    for account_id in pair_accounts.values():
        counts[account_id] += 1
    paired.need(all(count > 0 for count in counts.values()), 'account_assignment_unused_account')
    if document['strategy'] == 'balanced-case-seed-rotation/1':
        paired.need(len(account_ids) == 3, 'account_assignment_strategy')
        for case_index, case in enumerate(cases):
            for seed_index, seed in enumerate(seeds):
                paired.need(pair_accounts[(case, seed)] == account_ids[(case_index + seed_index) % len(account_ids)],
                            'account_assignment_strategy')
    return document, dict(reference), counts


def balanced(case_ids, seeds, accounts, arms=None):
    """Build a deterministic balanced 3-account map, keeping each pair on one account."""
    paired.need(type(case_ids) is list and len(case_ids) > 0 and len(set(case_ids)) == len(case_ids),
                'account_assignment_cases')
    paired.need(type(seeds) is list and len(seeds) > 0 and len(set(seeds)) == len(seeds),
                'account_assignment_seeds')
    paired.need(type(accounts) is list and len(accounts) == 3, 'account_assignment_strategy')
    arms = list(ARMS) if arms is None else arms
    paired.need(arms == list(ARMS), 'account_assignment_plan')
    return dict(schema=SCHEMA, strategy='balanced-case-seed-rotation/1', accounts=accounts,
                assignments=[dict(run_id=f'{case}.{arm}.s{seed}', case=case, arm=arm, seed=seed,
                                  account_id=accounts[(case_index + seed_index) % len(accounts)]['account_id'])
                             for seed_index, seed in enumerate(seeds)
                             for case_index, case in enumerate(case_ids) for arm in arms])


def account_for(document, case, arm, seed):
    for row in document['assignments']:
        if row['case'] == case and row['arm'] == arm and row['seed'] == seed:
            return next(account for account in document['accounts'] if account['account_id'] == row['account_id'])
    raise paired.Refusal('account_assignment_missing_attempt')


