"""Pinned synthetic capacity declarations; no account, provider or store access."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_paired_request as paired


NOW = dt.datetime(2026, 10, 9, 20, tzinfo=dt.timezone.utc)
MODELS = dict.fromkeys(('CURRENT', 'CAND', 'primary', 'secondary'), 'fixture-model')


def write_pin(path, document):
    path.write_text(json.dumps(document))
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def capacity_fixture(root, plan=None):
    """One pair: 8 + 8 actor requests, 1 + 2 grader requests; 4 * 160 tokens."""
    if plan is None:
        plan = dict(instrument='weekly-frozen-cases', cases=['fixture'],
                    arms=['CURRENT', 'CAND'], seeds=[1], attempts=2)
    basis = write_pin(root/'workload-basis.json', {'fixture_only': True})
    roles = {}
    for role, requests in zip(MODELS, (8, 8, 1, 2)):
        roles[role] = dict(state='included', basis=basis, jobs=[dict(
            id=role, model='fixture-model', count=plan['attempts']//2,
            per_job=dict(provider_requests=requests, input_tokens=100, output_tokens=20,
                         cache_read_input_tokens=30, cache_creation_input_tokens=10))])
    for role in ('selector', 'control', 'retry', 'dispute', 'auxiliary'):
        roles[role] = dict(state='not_applicable', basis=basis, jobs=[])
    workload = dict(schema='cairn.paired-provider-workload/2', roles=roles)
    plan['workload'] = write_pin(root/'workload.json', workload)
    plan['sha256'] = paired.canonical_sha({k: v for k, v in plan.items() if k != 'sha256'})
    accounts = write_pin(root/'account-v2.json', dict(schema='cairn.paired-account-binding/1',
        accounts=[dict(account_id='fixture-account', dedicated=True, interactive_use=False)]))
    routes = dict(schema='cairn.paired-route-binding/2', plan_sha256=plan['sha256'],
        state='complete', routes={role: dict(account_id='fixture-account', model='fixture-model',
            windows=[dict(pool_id='shared', window_id='campaign',
                          metrics=['provider_requests', 'total_tokens'])]) for role in MODELS})
    resources = [dict(pool_id='shared', window_id='campaign',
        metric=metric, remaining=amount, required=amount)
        for metric, amount in [('provider_requests', 19*plan['attempts']//2),
                               ('total_tokens', 640*plan['attempts']//2)]]
    reservation = dict(schema='cairn.paired-reservation/2', plan_sha256=plan['sha256'],
        resources_sha256=paired.canonical_sha(resources), state='held',
        valid_until='2026-10-09T20:05:00Z')
    capacity = dict(schema='cairn.paired-capacity-evidence/2', plan_sha256=plan['sha256'],
        state='declared', observed_at='2026-10-09T19:59:00Z', valid_until='2026-10-09T20:05:00Z',
        account_binding=accounts, route_binding=write_pin(root/'routes-v2.json', routes),
        reservation=write_pin(root/'reservation-v2.json', reservation), resources=resources)
    return plan, capacity, workload, routes


def save_capacity(root, capacity):
    reservation = json.loads(Path(capacity['reservation']['path']).read_text())
    reservation['resources_sha256'] = paired.canonical_sha(capacity['resources'])
    capacity['reservation'] = write_pin(root/'reservation-v2.json', reservation)
    return write_pin(root/'capacity-v2.json', capacity)


def bind_workload(root, plan, capacity, workload, routes):
    plan['workload'] = write_pin(root/'workload.json', workload)
    plan['sha256'] = paired.canonical_sha({k: v for k, v in plan.items() if k != 'sha256'})
    capacity['plan_sha256'] = routes['plan_sha256'] = plan['sha256']
    capacity['route_binding'] = write_pin(root/'routes-v2.json', routes)
    reservation = json.loads(Path(capacity['reservation']['path']).read_text())
    reservation['plan_sha256'] = plan['sha256']
    capacity['reservation'] = write_pin(root/'reservation-v2.json', reservation)
    return save_capacity(root, capacity)


class CapacityTests(unittest.TestCase):
    def test_eight_request_actor_requires_eight_requests_in_shared_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, capacity, _, _ = capacity_fixture(root)
            paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)
            # Replacing CURRENT's eight requests with one would produce 12, not 19.
            capacity['resources'][0].update(required=12, remaining=12)
            with self.assertRaisesRegex(paired.Refusal, '^capacity_estimate_mismatch$'):
                paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)

    def test_every_role_requires_all_provider_counters(self):
        for role in MODELS:
            for counter in ('provider_requests', 'input_tokens', 'output_tokens',
                            'cache_read_input_tokens', 'cache_creation_input_tokens'):
                with self.subTest(role=role, counter=counter), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    plan, capacity, workload, routes = capacity_fixture(root)
                    del workload['roles'][role]['jobs'][0]['per_job'][counter]
                    reference = bind_workload(root, plan, capacity, workload, routes)
                    with self.assertRaisesRegex(paired.Refusal, '^workload_counters$'):
                        paired.capacity_check(reference, plan, NOW, MODELS)

    def test_workload_inventory_rejects_omitted_unknown_and_undercounted_roles(self):
        for mutation in ('missing_secondary', 'missing_selector', 'unknown_role',
                         'zero_actor', 'half_graders', 'unexplained_empty_auxiliary'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan, capacity, workload, routes = capacity_fixture(root)
                roles = workload['roles']
                if mutation.startswith('missing_'):
                    del roles[mutation.removeprefix('missing_')]
                elif mutation == 'unknown_role':
                    roles['unknown'] = roles['auxiliary']
                elif mutation == 'zero_actor':
                    roles['CURRENT']['jobs'][0]['count'] = 0
                elif mutation == 'half_graders':
                    plan['attempts'] = 4
                else:
                    roles['auxiliary']['state'] = 'included'
                reference = bind_workload(root, plan, capacity, workload, routes)
                with self.assertRaises(paired.Refusal):
                    paired.capacity_check(reference, plan, NOW, MODELS)

    def test_every_mapped_window_is_charged_once_and_no_route_is_missing(self):
        for mutation in ('missing_route', 'extra_route', 'wrong_model', 'unknown_mapping',
                         'missing_resource', 'duplicate_resource', 'duplicate_window',
                         'duplicate_metric', 'unknown_metric', 'missing_tokens'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan, capacity, workload, routes = capacity_fixture(root)
                route = routes['routes']['CURRENT']
                if mutation == 'missing_route':
                    del routes['routes']['secondary']
                elif mutation == 'extra_route':
                    routes['routes']['unplanned'] = route
                elif mutation == 'wrong_model':
                    route['model'] = 'unplanned-model'
                elif mutation == 'unknown_mapping':
                    routes['state'] = 'unknown'
                elif mutation == 'missing_resource':
                    capacity['resources'].pop()
                elif mutation == 'duplicate_resource':
                    capacity['resources'].append(capacity['resources'][0])
                elif mutation == 'duplicate_window':
                    route['windows'].append(route['windows'][0])
                elif mutation == 'duplicate_metric':
                    route['windows'][0]['metrics'].append('total_tokens')
                elif mutation == 'unknown_metric':
                    route['windows'][0]['metrics'][1] = 'percent'
                else:
                    for entry in routes['routes'].values():
                        entry['windows'][0]['metrics'] = ['provider_requests']
                    capacity['resources'].pop()
                reference = bind_workload(root, plan, capacity, workload, routes)
                with self.assertRaises(paired.Refusal):
                    paired.capacity_check(reference, plan, NOW, MODELS)

    def test_unknown_stale_account_capacity_and_reservation_refuse(self):
        cases = [('state', 'capacity_unknown'), ('stale', 'capacity_stale'),
                 ('future', 'capacity_stale'), ('account', 'dedicated_account_unknown'),
                 ('route_account', 'account_route_unknown'), ('reservation', 'reservation_unknown'),
                 ('short_reservation', 'reservation_unknown'), ('old_reservation', 'reservation_unknown')]
        for mutation, code in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan, capacity, _, routes = capacity_fixture(root)
                if mutation == 'state':
                    capacity['state'] = 'unknown'
                elif mutation == 'stale':
                    capacity['valid_until'] = '2026-10-09T20:00:00Z'
                elif mutation == 'future':
                    capacity['observed_at'] = '2026-10-09T20:01:00Z'
                elif mutation == 'account':
                    accounts = json.loads(Path(capacity['account_binding']['path']).read_text())
                    accounts['accounts'][0]['dedicated'] = False
                    capacity['account_binding'] = write_pin(root/'account-v2.json', accounts)
                elif mutation == 'route_account':
                    for route in routes['routes'].values():
                        route['account_id'] = 'unknown-account'
                    capacity['route_binding'] = write_pin(root/'routes-v2.json', routes)
                else:
                    reservation = json.loads(Path(capacity['reservation']['path']).read_text())
                    if mutation == 'reservation':
                        reservation['state'] = 'unknown'
                    elif mutation == 'short_reservation':
                        reservation['valid_until'] = '2026-10-09T20:01:00Z'
                    else:
                        reservation['plan_sha256'] = '0'*64
                    capacity['reservation'] = write_pin(root/'reservation-v2.json', reservation)
                with self.assertRaisesRegex(paired.Refusal, '^' + code + '$'):
                    paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)

    def test_auxiliary_models_share_pool_and_overlapping_windows_without_borrowing_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, capacity, workload, routes = capacity_fixture(root)
            for role in ('selector', 'control', 'retry', 'dispute', 'auxiliary'):
                group = workload['roles'][role]
                group.update(state='included', jobs=[dict(id=role, model='other-model', count=2,
                    per_job=dict(provider_requests=3, input_tokens=7, output_tokens=3,
                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))])
                routes['routes'][role] = dict(account_id='fixture-account', model='other-model',
                    windows=[dict(pool_id='shared', window_id='campaign',
                                  metrics=['provider_requests', 'total_tokens'])])
            # Five auxiliary roles, two jobs each, three requests and ten tokens per job.
            capacity['resources'][0].update(required=49, remaining=49)
            capacity['resources'][1].update(required=740, remaining=740)
            for route in routes['routes'].values():
                route['windows'].append(dict(pool_id='shared', window_id='overlapping',
                    metrics=['input_tokens', 'output_tokens', 'cache_read_input_tokens',
                             'cache_creation_input_tokens']))
            capacity['resources'] += [dict(pool_id='shared',
                window_id='overlapping', metric=metric, required=amount, remaining=amount)
                for metric, amount in [('input_tokens', 470), ('output_tokens', 110),
                                       ('cache_read_input_tokens', 120), ('cache_creation_input_tokens', 40)]]
            reference = bind_workload(root, plan, capacity, workload, routes)
            paired.capacity_check(reference, plan, NOW, MODELS)
            capacity['resources'][0]['remaining'] = 48
            with self.assertRaisesRegex(paired.Refusal, '^capacity_insufficient$'):
                paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)
            capacity['resources'][0]['remaining'] = 49
            capacity['resources'][-1]['remaining'] = 39
            with self.assertRaisesRegex(paired.Refusal, '^capacity_insufficient$'):
                paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)

    def test_unknown_noninteger_and_unbounded_counters_never_become_zero(self):
        for counter in ('provider_requests', 'input_tokens', 'output_tokens',
                        'cache_read_input_tokens', 'cache_creation_input_tokens'):
            for value in (None, False, 1.0, -1, 2**63):
                with self.subTest(counter=counter, value=value), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    plan, capacity, workload, routes = capacity_fixture(root)
                    workload['roles']['CURRENT']['jobs'][0]['per_job'][counter] = value
                    reference = bind_workload(root, plan, capacity, workload, routes)
                    with self.assertRaisesRegex(paired.Refusal, '^workload_counter_bound$'):
                        paired.capacity_check(reference, plan, NOW, MODELS)

    def test_distinct_accounts_and_models_charge_one_shared_organization_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, capacity, workload, routes = capacity_fixture(root)
            accounts = json.loads(Path(capacity['account_binding']['path']).read_text())
            accounts['accounts'].append(dict(account_id='second-account', dedicated=True, interactive_use=False))
            capacity['account_binding'] = write_pin(root/'account-v2.json', accounts)
            routes['routes']['CURRENT']['account_id'] = 'second-account'
            for resource in capacity['resources']:
                resource.pop('account_id', None)
            reference = bind_workload(root, plan, capacity, workload, routes)
            paired.capacity_check(reference, plan, NOW, MODELS)
            # Two separate accounts do not supply two copies of the pool's allowance.
            capacity['resources'][0].update(required=11, remaining=11)
            with self.assertRaisesRegex(paired.Refusal, '^capacity_estimate_mismatch$'):
                paired.capacity_check(save_capacity(root, capacity), plan, NOW, MODELS)

    def test_explicit_zero_uncached_tokens_and_token_free_auxiliary_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, capacity, workload, routes = capacity_fixture(root)
            counters = workload['roles']['CURRENT']['jobs'][0]['per_job']
            counters.update(input_tokens=0, cache_read_input_tokens=130)
            workload['roles']['control'].update(state='included', jobs=[dict(
                id='control-request', model='fixture-model', count=1,
                per_job=dict(provider_requests=1, input_tokens=0, output_tokens=0,
                             cache_read_input_tokens=0, cache_creation_input_tokens=0))])
            routes['routes']['control-request'] = routes['routes']['CURRENT']
            capacity['resources'][0].update(required=20, remaining=20)
            reference = bind_workload(root, plan, capacity, workload, routes)
            paired.capacity_check(reference, plan, NOW, MODELS)

    def test_shared_window_cannot_drop_one_models_token_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, capacity, workload, routes = capacity_fixture(root)
            routes['routes']['CURRENT']['windows'] = [
                dict(pool_id='shared', window_id='campaign', metrics=['provider_requests']),
                dict(pool_id='different', window_id='campaign', metrics=['total_tokens'])]
            # This remains arithmetically consistent and covers all counters, but
            # changes the definition of the shared window for just one consumer.
            capacity['resources'][1].update(required=480, remaining=480)
            capacity['resources'].append(dict(pool_id='different', window_id='campaign',
                metric='total_tokens', required=160, remaining=160))
            reference = bind_workload(root, plan, capacity, workload, routes)
            with self.assertRaisesRegex(paired.Refusal, '^route_window_metrics_mismatch$'):
                paired.capacity_check(reference, plan, NOW, MODELS)


if __name__ == '__main__':
    unittest.main()
