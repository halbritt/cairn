"""Synthetic whole original-count declarations; no cases, accounts or providers."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_paired_request as paired
from trial_provider_spend import SpendRefusal
from trial_provider_mapping import project_provider_mapping
from test_trial_paired_capacity import capacity_fixture, write_pin

NOW = '2026-10-09T20:00:00Z'
END = '2026-10-09T20:05:00Z'
START = '2026-10-09T19:59:00Z'
MODELS = dict.fromkeys(('CURRENT', 'CAND', 'primary', 'secondary'), 'fixture-model')


class Fixture:
    def __init__(self, root):
        self.root = root
        plan = dict(instrument='weekly-frozen-cases', cases=['synthetic-'+str(n) for n in range(21)],
                    arms=['CURRENT', 'CAND'], seeds=[1, 2, 3], attempts=126)
        self.plan, self.capacity, self.workload, self.routes = capacity_fixture(root, plan)
        self.accounts = dict(schema='cairn.paired-account-binding/1', accounts=[
            dict(account_id=a, dedicated=True, interactive_use=False) for a in ('account-A', 'account-B')])
        for role in ('selector', 'control', 'retry', 'dispute', 'auxiliary'):
            group = self.workload['roles'][role]
            group.update(state='included', jobs=[dict(id=role, model='fixture-model', count=2,
                per_job=dict(provider_requests=3, input_tokens=7, output_tokens=3,
                             cache_read_input_tokens=2, cache_creation_input_tokens=1))])
            self.routes['routes'][role] = copy.deepcopy(self.routes['routes']['CURRENT'])
        for role, route in self.routes['routes'].items():
            route['account_id'] = 'account-B' if role == 'CAND' else 'account-A'
        # 63*(8+8+1+2) + 5*2*3 requests; 4*63*160 + 5*2*13 tokens.
        for resource, amount in zip(self.capacity['resources'], (1227, 40450)):
            resource.update(required=amount, remaining=amount)
        self.prices = dict(schema='cairn.provider-prices/1', currency='USD', quotes={})
        self.binding = dict(schema='cairn.provider-spend-routes/1', plan_sha256='',
            observed_at=START, valid_until=END, coverage='complete', unlisted_routes='forbidden',
            routes={role: dict(model='fixture-model', quotes=['qB' if role == 'CAND' else 'qA'])
                    for role in self.routes['routes']})
        self.mapping = dict(schema='cairn.provider-account-quotes/1', plan_sha256='',
            capacity_sha256='', spend_request_sha256='', observed_at=START, valid_until=END,
            coverage='complete', unlisted_routes='forbidden', spend_binding={}, prices={}, quotes={})
        for ident, multiplier, account in [('qA', 1, 'account-A'), ('qB', 2, 'account-B')]:
            price_source = write_pin(root/(ident+'-price.json'), {'synthetic_price_only': ident})
            basis = write_pin(root/(ident+'-basis.json'), {'synthetic_permission_only': ident, 'account_id': account})
            quote = dict(provider='provider-P', model='fixture-model', endpoint='endpoint-1', tier='standard',
                source_sha256=price_source['sha256'], observed_at=START, valid_until=END,
                pricing='linear-upper-bound/1', other_fees='included',
                rates={counter: dict(numerator=n*multiplier, denominator=d) for counter, (n, d) in zip(
                    paired.PROVIDER_COUNTERS, [(1,10),(1,100),(2,100),(3,1000),(4,1000)])},
                rounding_quantum=dict(numerator=0, denominator=1))
            self.prices['quotes'][ident] = quote
            self.mapping['quotes'][ident] = dict(price_source=price_source, accounts={account:dict(
                **{k:quote[k] for k in ('provider','model','endpoint','tier')}, basis=basis,
                windows=copy.deepcopy(self.routes['routes']['CURRENT']['windows']))})
        self.spend_request = dict(schema='cairn.provider-spend-request/1', plan_sha256='', jobs_sha256='',
            binding_sha256='', prices_sha256='', currency='USD', units_per_currency=1000,
            budget_units=656450, valid_until=END)

    def save(self):
        self.plan['workload'] = write_pin(self.root/'workload.json', self.workload)
        self.plan['sha256'] = paired.canonical_sha({k:v for k,v in self.plan.items() if k != 'sha256'})
        self.capacity['account_binding'] = write_pin(self.root/'accounts.json', self.accounts)
        self.routes['plan_sha256'] = self.capacity['plan_sha256'] = self.plan['sha256']
        self.capacity['route_binding'] = write_pin(self.root/'routes.json', self.routes)
        reservation = dict(schema='cairn.paired-reservation/2', plan_sha256=self.plan['sha256'],
            resources_sha256=paired.canonical_sha(self.capacity['resources']), state='held', valid_until=END)
        self.capacity['reservation'] = write_pin(self.root/'reservation.json', reservation)
        capacity = write_pin(self.root/'capacity.json', self.capacity)
        self.binding['plan_sha256'] = self.spend_request['plan_sha256'] = self.plan['sha256']
        jobs = {job['id']:job for group in self.workload['roles'].values() for job in group['jobs']}
        self.spend_request.update(jobs_sha256=paired.canonical_sha(jobs),
            binding_sha256=paired.canonical_sha(self.binding), prices_sha256=paired.canonical_sha(self.prices))
        spend_request = write_pin(self.root/'spend-request.json', self.spend_request)
        self.mapping.update(plan_sha256=self.plan['sha256'], capacity_sha256=capacity['sha256'],
            spend_request_sha256=spend_request['sha256'],
            spend_binding=write_pin(self.root/'spend-binding.json', self.binding),
            prices=write_pin(self.root/'prices.json', self.prices))
        mapping = write_pin(self.root/'mapping.json', self.mapping)
        return self.plan, MODELS, capacity, spend_request, mapping


class MappingTests(unittest.TestCase):
    def test_complete_original_counts_shared_pools_and_account_specific_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            inputs = fixture.save()
            before = copy.deepcopy(inputs)
            result = project_provider_mapping(*inputs, now=NOW)
            self.assertEqual(result['spend']['total_units'], 656450)
            self.assertEqual(result['account_totals']['account-A']['spend_units'], 362870)
            self.assertEqual(result['account_totals']['account-B']['spend_units'], 293580)
            self.assertEqual(result['pool_demands'], [
                dict(pool_id='shared', window_id='campaign', metric='provider_requests', required=1227),
                dict(pool_id='shared', window_id='campaign', metric='total_tokens', required=40450)])
            self.assertEqual(set(result['job_bindings']), set(paired.CAPACITY_ROLES))
            self.assertFalse(result['runtime_admission'])
            self.assertEqual(inputs, before)

    def test_endpoint_tier_alternatives_take_price_maxima_and_include_all_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            quote = copy.deepcopy(fixture.prices['quotes']['qA'])
            quote.update(endpoint='endpoint-2', tier='long-context')
            quote['rates']['input_tokens'] = dict(numerator=2, denominator=100)
            quote['rates']['output_tokens'] = dict(numerator=0, denominator=1)
            fixture.prices['quotes']['qAlt'] = quote
            entry = copy.deepcopy(fixture.mapping['quotes']['qA'])
            permission = entry['accounts']['account-A']
            permission.update(endpoint='endpoint-2', tier='long-context')
            window = dict(pool_id='endpoint-pool', window_id='campaign',
                          metrics=['provider_requests','total_tokens'])
            permission['windows'] = [window]
            fixture.mapping['quotes']['qAlt'] = entry
            for ident, route in fixture.routes['routes'].items():
                if route['account_id'] == 'account-A':
                    route['windows'].append(copy.deepcopy(window))
                    fixture.binding['routes'][ident]['quotes'].append('qAlt')
            fixture.capacity['resources'] += [dict(pool_id='endpoint-pool', window_id='campaign',
                metric=metric, required=amount, remaining=amount)
                for metric, amount in [('provider_requests',723),('total_tokens',30370)]]
            # Max input adds $1 per A core job (3*63) and $.07 per aux job (10).
            # Output keeps standard-tier prices even though qAlt declares zero.
            fixture.spend_request['budget_units'] = 846150
            result = project_provider_mapping(*fixture.save(), now=NOW)
            self.assertEqual(result['spend']['total_units'], 846150)
            self.assertEqual(result['account_totals']['account-A']['spend_units'], 552570)
            self.assertEqual(len(result['pool_demands']), 4)
            fixture.mapping['quotes']['qAlt']['accounts']['account-A']['windows'] = []
            with self.assertRaisesRegex(paired.Refusal, '^mapping_windows$'):
                project_provider_mapping(*fixture.save(), now=NOW)

    def test_same_public_quote_can_be_explicitly_permitted_for_two_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.binding['routes']['CAND']['quotes'] = ['qA']
            del fixture.prices['quotes']['qB']
            del fixture.mapping['quotes']['qB']
            allowed = fixture.mapping['quotes']['qA']['accounts']
            allowed['account-B'] = copy.deepcopy(allowed['account-A'])
            allowed['account-B']['basis'] = write_pin(fixture.root/'public-shared.json',
                {'synthetic_only': True, 'public_price_permitted_for': 'account-B'})
            result = project_provider_mapping(*fixture.save(), now=NOW)
            self.assertEqual(result['spend']['total_units'], 509660)
            self.assertEqual(result['account_totals']['account-B']['spend_units'], 146790)
            self.assertEqual(result['account_totals']['account-A']['demands']['provider_requests'], 723)
            self.assertEqual(result['account_totals']['account-B']['demands']['provider_requests'], 504)

    def test_quote_fallback_cannot_change_account_model_identity_or_windows(self):
        changes = [
            (lambda f: f.binding['routes']['CURRENT'].update(quotes=['qA','qB']), 'mapping_account_quote_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts'].update(unknown=copy.deepcopy(
                f.mapping['quotes']['qA']['accounts']['account-A'])), 'mapping_account_unknown'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A'].update(model='wrong-model'), 'mapping_quote_route_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A'].update(provider='wrong-provider'), 'mapping_quote_route_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A'].update(endpoint='wrong-endpoint'), 'mapping_quote_route_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A'].update(tier='wrong-tier'), 'mapping_quote_route_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A']['windows'][0].update(pool_id='invented-pool'), 'mapping_window_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A']['windows'][0].update(metrics=['provider_requests']), 'mapping_window_mismatch'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A']['windows'].append(
                dict(pool_id='extra', window_id='extra', metrics=['total_tokens'])), 'mapping_window_mismatch'),
        ]
        for change, code in changes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                fixture.spend_request['budget_units'] = 10_000_000
                change(fixture)
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)

    def test_extra_unused_quote_account_pairs_and_missing_permissions_refuse(self):
        for mode, code in [('extra','mapping_unused_account_quote'), ('missing','mapping_accounts_shape'),
                           ('unknown-quote','mapping_quote_coverage'), ('missing-quote','mapping_quote_coverage')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                if mode == 'extra':
                    fixture.mapping['quotes']['qA']['accounts']['account-B'] = copy.deepcopy(
                        fixture.mapping['quotes']['qA']['accounts']['account-A'])
                elif mode == 'missing':
                    fixture.mapping['quotes']['qA']['accounts'].clear()
                elif mode == 'unknown-quote':
                    fixture.mapping['quotes']['unknown'] = copy.deepcopy(fixture.mapping['quotes']['qA'])
                else:
                    del fixture.mapping['quotes']['qA']
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)

    def test_all_nine_roles_mandatory_and_both_graders_cover_every_pair(self):
        for role in paired.CAPACITY_ROLES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                del fixture.workload['roles'][role]
                with self.assertRaisesRegex(paired.Refusal, '^workload_roles$'):
                    project_provider_mapping(*fixture.save(), now=NOW)
        for role in MODELS:
            with self.subTest(undercount=role), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                fixture.workload['roles'][role]['jobs'][0]['count'] = 62
                with self.assertRaisesRegex(paired.Refusal, '^workload_role_mismatch$'):
                    project_provider_mapping(*fixture.save(), now=NOW)

    def test_original_counts_and_weekly_instrument_cannot_be_replaced(self):
        changes = [
            (lambda f: f.plan.update(instrument='supplemental-fresh-tasks'), 'mapping_original_instrument'),
            (lambda f: f.plan.update(cases=f.plan['cases'][:8]), 'mapping_original_cases'),
            (lambda f: f.plan.update(seeds=[1]), 'mapping_original_attempts'),
            (lambda f: f.plan.update(attempts=128), 'mapping_original_attempts'),
            (lambda f: f.plan.update(arms=['CURRENT']), 'mapping_original_instrument'),
            (lambda f: f.plan.update(seeds=[1,1,2]), 'mapping_original_attempts'),
        ]
        for change, code in changes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                change(fixture)
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)

    def test_no_missing_requests_cache_counter_basis_or_price_becomes_zero(self):
        changes = [
            (lambda f: f.workload['roles']['auxiliary']['jobs'][0]['per_job'].pop('cache_creation_input_tokens'), 'workload_counters'),
            (lambda f: f.workload['roles']['CURRENT']['jobs'][0]['per_job'].update(provider_requests=0), 'workload_counter_bound'),
            (lambda f: f.workload['roles']['retry'].pop('basis'), 'workload_group'),
            (lambda f: f.workload['roles']['control'].update(jobs=[]), 'workload_unknown'),
            (lambda f: f.prices['quotes']['qA']['rates'].pop('cache_read_input_tokens'), 'spend_price_shape'),
            (lambda f: f.prices['quotes']['qA'].update(other_fees='unknown'), 'spend_pricing_unsupported'),
            (lambda f: f.prices['quotes']['qA'].update(pricing='unbounded-context-tiers'), 'spend_pricing_unsupported'),
            (lambda f: f.mapping['quotes']['qA']['accounts']['account-A'].pop('basis'), 'mapping_quote_route_shape'),
        ]
        for change, code in changes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                change(fixture)
                with self.assertRaisesRegex((paired.Refusal, SpendRefusal), '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)

    def test_one_complete_interval_covers_capacity_mapping_binding_and_quotes(self):
        short = '2026-10-09T20:04:00Z'
        changes = [
            (lambda f: f.capacity.update(valid_until=short), 'mapping_capacity_interval'),
            (lambda f: f.capacity.update(valid_until=NOW), 'capacity_stale'),
            (lambda f: f.mapping.update(valid_until=short), 'spend_stale'),
            (lambda f: f.mapping.update(observed_at=END), 'spend_stale'),
            (lambda f: f.binding.update(valid_until=short), 'spend_stale'),
            (lambda f: f.prices['quotes']['qA'].update(valid_until=short), 'spend_stale'),
            (lambda f: f.prices['quotes']['qA'].update(observed_at=END), 'spend_stale'),
            (lambda f: f.spend_request.update(valid_until=NOW), 'spend_stale'),
            (lambda f: f.capacity.update(state='unknown'), 'capacity_unknown'),
            (lambda f: f.mapping.update(coverage='unknown'), 'mapping_routes_unknown'),
        ]
        for change, code in changes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                change(fixture)
                with self.assertRaisesRegex((paired.Refusal, SpendRefusal), '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            inputs = list(fixture.save())
            reservation = json.loads(Path(fixture.capacity['reservation']['path']).read_text())
            reservation['valid_until'] = short
            fixture.capacity['reservation'] = write_pin(fixture.root/'short-reservation.json', reservation)
            inputs[2] = write_pin(fixture.root/'short-capacity.json', fixture.capacity)
            with self.assertRaisesRegex(paired.Refusal, '^reservation_unknown$'):
                project_provider_mapping(*inputs, now=NOW)

    def test_declared_pins_and_actual_file_bytes_must_match(self):
        for field in ('plan_sha256','capacity_sha256','spend_request_sha256'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                inputs = list(fixture.save())
                fixture.mapping[field] = '0'*64
                inputs[4] = write_pin(fixture.root/'wrong-mapping.json', fixture.mapping)
                with self.assertRaisesRegex(paired.Refusal, '^mapping_pin_mismatch$'):
                    project_provider_mapping(*inputs, now=NOW)
        for path in ('mapping.json','spend-request.json','routes.json','workload.json',
                     'workload-basis.json','qA-price.json','qA-basis.json'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                inputs = fixture.save()
                (fixture.root/path).write_text('{}')
                with self.assertRaisesRegex(paired.Refusal, '^pin_mismatch$'):
                    project_provider_mapping(*inputs, now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.mapping['quotes']['qA']['price_source'] = fixture.mapping['quotes']['qB']['price_source']
            with self.assertRaisesRegex(paired.Refusal, '^mapping_price_source_mismatch$'):
                project_provider_mapping(*fixture.save(), now=NOW)

    def test_spend_subset_or_other_plan_cannot_be_projected_through_bridge(self):
        for field, value in [('jobs_sha256','a'*64), ('binding_sha256','b'*64), ('prices_sha256','c'*64)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                inputs = list(fixture.save())
                fixture.spend_request[field] = value
                inputs[3] = write_pin(fixture.root/'wrong-spend.json', fixture.spend_request)
                fixture.mapping['spend_request_sha256'] = inputs[3]['sha256']
                inputs[4] = write_pin(fixture.root/'wrong-mapping.json', fixture.mapping)
                with self.assertRaisesRegex(SpendRefusal, '^spend_pin_mismatch$'):
                    project_provider_mapping(*inputs, now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            inputs = list(fixture.save())
            # The spend helper can consistently bind to another plan; the join
            # must independently require the actual plan being projected.
            fixture.binding['plan_sha256'] = fixture.spend_request['plan_sha256'] = 'd'*64
            fixture.spend_request['binding_sha256'] = paired.canonical_sha(fixture.binding)
            inputs[3] = write_pin(fixture.root/'other-plan-spend.json', fixture.spend_request)
            fixture.mapping['spend_binding'] = write_pin(fixture.root/'other-plan-binding.json', fixture.binding)
            fixture.mapping['spend_request_sha256'] = inputs[3]['sha256']
            inputs[4] = write_pin(fixture.root/'other-plan-mapping.json', fixture.mapping)
            with self.assertRaisesRegex(paired.Refusal, '^mapping_plan_mismatch$'):
                project_provider_mapping(*inputs, now=NOW)
        for kind in ('capacity','spend'):
            for mode in ('missing','extra'):
                with self.subTest(kind=kind, mode=mode), tempfile.TemporaryDirectory() as directory:
                    fixture = Fixture(Path(directory))
                    routes = fixture.routes['routes'] if kind == 'capacity' else fixture.binding['routes']
                    if mode == 'missing':
                        del routes['auxiliary']
                    else:
                        routes['unplanned'] = copy.deepcopy(routes['CURRENT'])
                    code = 'route_workloads' if kind == 'capacity' else 'spend_route_coverage'
                    with self.assertRaisesRegex((paired.Refusal,SpendRefusal), '^'+code+'$'):
                        project_provider_mapping(*fixture.save(), now=NOW)

    def test_duplicate_json_keys_closed_shapes_and_v1_capacity_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            inputs = list(fixture.save())
            path = fixture.root/'mapping.json'
            path.write_text('{"schema":"cairn.provider-account-quotes/1",'+path.read_text()[1:])
            inputs[4]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(paired.Refusal, '^duplicate_json_key$'):
                project_provider_mapping(*inputs, now=NOW)
        for change, code in [
            (lambda f:f.mapping.update(authenticated=True), 'mapping_shape'),
            (lambda f:f.mapping.update(schema='cairn.provider-account-quotes/0'), 'mapping_schema'),
            (lambda f:f.capacity.update(schema='cairn.paired-capacity-evidence/1'), 'capacity_v2_required'),
            (lambda f:f.spend_request.update(budget_units=656449), 'spend_budget_exceeded'),
            (lambda f:f.capacity['resources'][0].update(required=1226), 'capacity_estimate_mismatch'),
        ]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                change(fixture)
                with self.assertRaisesRegex((paired.Refusal,SpendRefusal), '^'+code+'$'):
                    project_provider_mapping(*fixture.save(), now=NOW)


if __name__ == '__main__':
    unittest.main()
