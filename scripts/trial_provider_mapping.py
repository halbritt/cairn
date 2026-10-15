"""Offline original107 declaration join; no calibration, funding or admission.

Reuse the existing pinned-file validators and spend arithmetic. This does not
require/open the frozen input tree or inspect case/oracle/corpus records.
Callers must keep operator-owned evidence paths appropriate and quiescent.
"""
import trial_paired_request as paired
import trial_provider_spend as spend


def original_plan(plan):
    paired.shape(plan, 'instrument cases arms seeds attempts workload sha256', 'mapping_plan_shape')
    paired.need(plan['instrument'] == 'weekly-frozen-cases' and plan['arms'] == ['CURRENT', 'CAND'],
                'mapping_original_instrument')
    paired.need(type(plan['cases']) is list and len(plan['cases']) == 21
                and all(paired.capacity_name(case) for case in plan['cases'])
                and len(set(plan['cases'])) == 21, 'mapping_original_cases')
    paired.need(type(plan['seeds']) is list and len(plan['seeds']) == 3
                and all(type(seed) is int and 0 <= seed <= 2**31-1 for seed in plan['seeds'])
                and len(set(plan['seeds'])) == 3 and type(plan['attempts']) is int
                and plan['attempts'] == 126, 'mapping_original_attempts')
    paired.need(plan['sha256'] == paired.canonical_sha({k:v for k,v in plan.items() if k != 'sha256'}),
                'plan_mismatch')


def project_provider_mapping(plan, models, capacity_reference, spend_request_reference,
                             mapping_reference, *, now):
    """Join complete plan-derived jobs, capacity/2 routes and account-price routes.

    Inputs are borrowed and must not be mutated concurrently. Pinned paths use
    the existing bounded file interface. This validates original *counts*, not
    frozen case identity or the other request/2 acceptance prerequisites.
    """
    original_plan(plan)
    current = spend.timestamp(now)
    paired.capacity_check(capacity_reference, plan, current, models)
    jobs = paired.provider_workload(plan, models)
    capacity = paired.read_json_pin(capacity_reference)
    accounts = paired.capacity_accounts(capacity['account_binding'])
    totals = paired.provider_demands(capacity['route_binding'], plan, jobs, accounts)
    capacity_routes = paired.read_json_pin(capacity['route_binding'])['routes']
    request = paired.read_json_pin(spend_request_reference)
    mapping = paired.read_json_pin(mapping_reference)
    paired.shape(mapping, 'schema plan_sha256 capacity_sha256 spend_request_sha256 observed_at valid_until '
                          'coverage unlisted_routes spend_binding prices quotes', 'mapping_shape')
    paired.need(mapping['schema'] == 'cairn.provider-account-quotes/1', 'mapping_schema')
    paired.need(mapping['coverage'] == 'complete' and mapping['unlisted_routes'] == 'forbidden',
                'mapping_routes_unknown')
    paired.need(mapping['plan_sha256'] == plan['sha256']
                and mapping['capacity_sha256'] == capacity_reference['sha256']
                and mapping['spend_request_sha256'] == spend_request_reference['sha256'], 'mapping_pin_mismatch')
    binding = paired.read_json_pin(mapping['spend_binding'])
    prices = paired.read_json_pin(mapping['prices'])
    projection = spend.project_spend(jobs, binding, prices, request, now=now)
    paired.need(request['plan_sha256'] == plan['sha256'], 'mapping_plan_mismatch')
    until = spend.timestamp(request['valid_until'])
    spend.fresh(mapping, current, until)
    paired.need(current < until <= paired.utc(capacity['valid_until']), 'mapping_capacity_interval')
    # capacity_check already requires reservation validity >= capacity validity.
    declared = mapping['quotes']
    paired.need(type(declared) is dict and set(declared) == set(prices['quotes']), 'mapping_quote_coverage')
    permitted = set()
    for ident, entry in declared.items():
        paired.shape(entry, 'price_source accounts', 'mapping_quote_shape')
        quote = prices['quotes'][ident]
        paired.pin(entry['price_source'])
        paired.need(entry['price_source']['sha256'] == quote['source_sha256'], 'mapping_price_source_mismatch')
        paired.need(type(entry['accounts']) is dict and 0 < len(entry['accounts']) <= 64,
                    'mapping_accounts_shape')
        for account, route in entry['accounts'].items():
            paired.need(account in accounts, 'mapping_account_unknown')
            paired.shape(route, 'provider model endpoint tier basis windows', 'mapping_quote_route_shape')
            paired.need(all(route[field] == quote[field] for field in ('provider','model','endpoint','tier')),
                        'mapping_quote_route_mismatch')
            paired.pin(route['basis'])
            paired.need(type(route['windows']) is list and 0 < len(route['windows']) <= 64,
                        'mapping_windows')
            window_pins = [paired.canonical_sha(window) for window in route['windows']]
            paired.need(len(set(window_pins)) == len(window_pins), 'mapping_windows')
            permitted.add((account, ident))
    used, job_bindings = set(), {}
    account_totals = {account:dict(spend_units=0, demands={c:0 for c in paired.PROVIDER_COUNTERS})
                      for account in sorted(accounts)}
    for ident, job in jobs.items():
        route, quote_ids = capacity_routes[ident], binding['routes'][ident]['quotes']
        account = route['account_id']
        capacity_windows = {paired.canonical_sha(window) for window in route['windows']}
        quoted_windows = set()
        for quote in quote_ids:
            paired.need((account, quote) in permitted, 'mapping_account_quote_mismatch')
            used.add((account, quote))
            # Equality against already validated capacity windows reuses their
            # complete shape/metric contract, without a second window parser.
            quoted_windows.update(paired.canonical_sha(window)
                                  for window in declared[quote]['accounts'][account]['windows'])
        paired.need(quoted_windows == capacity_windows, 'mapping_window_mismatch')
        job_bindings[ident] = dict(account_id=account, model=job['model'],
                                  quotes=list(quote_ids), windows=route['windows'])
        account_totals[account]['spend_units'] += projection['jobs'][ident]
        for counter in paired.PROVIDER_COUNTERS:
            account_totals[account]['demands'][counter] += job['count'] * job['per_job'][counter]
            paired.need(paired.capacity_integer(account_totals[account]['demands'][counter]), 'capacity_total_bound')
    paired.need(used == permitted, 'mapping_unused_account_quote')
    return dict(schema='cairn.provider-mapping-projection/1', plan_sha256=plan['sha256'],
                jobs_sha256=paired.canonical_sha(jobs), capacity_sha256=capacity_reference['sha256'],
                spend_request_sha256=spend_request_reference['sha256'], mapping_sha256=mapping_reference['sha256'],
                projected_at=now, valid_until=request['valid_until'], spend=projection,
                job_bindings=job_bindings, account_totals=account_totals,
                pool_demands=[dict(pool_id=pool, window_id=window, metric=metric, required=amount)
                              for (pool, window, metric), amount in sorted(totals.items())],
                runtime_admission=False)
