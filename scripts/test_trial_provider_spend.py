"""Synthetic declarations only; worked costs are independent of the helper."""
import copy
import hashlib
import json
import unittest

from trial_provider_spend import project_spend, SpendRefusal

NOW = '2026-10-14T00:00:00Z'
END = '2026-10-15T00:00:00Z'
START = '2026-10-13T00:00:00Z'
COUNTERS = ('provider_requests', 'input_tokens', 'output_tokens',
            'cache_read_input_tokens', 'cache_creation_input_tokens')


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def ratio(n, d=1):
    return dict(numerator=n, denominator=d)


def declarations():
    jobs = {'actor': dict(id='actor', model='model-version-1', count=2,
                         per_job=dict(zip(COUNTERS, (8, 100, 20, 30, 40))))}
    quote = dict(provider='provider-A', model='model-version-1', endpoint='endpoint-A', tier='standard',
                 source_sha256='a'*64, observed_at=START, valid_until=END,
                 pricing='linear-upper-bound/1', other_fees='included',
                 rates=dict(zip(COUNTERS, (ratio(1, 10), ratio(1, 100), ratio(2, 100),
                                          ratio(3, 1000), ratio(4, 1000)))),
                 rounding_quantum=ratio(0))
    prices = dict(schema='cairn.provider-prices/1', currency='USD', quotes={'q1': quote})
    binding = dict(schema='cairn.provider-spend-routes/1', plan_sha256='b'*64,
                   observed_at=START, valid_until=END, coverage='complete',
                   unlisted_routes='forbidden', routes={'actor': dict(model='model-version-1', quotes=['q1'])})
    request = dict(schema='cairn.provider-spend-request/1', plan_sha256='b'*64,
                   jobs_sha256=sha(jobs), binding_sha256=sha(binding), prices_sha256=sha(prices),
                   currency='USD', units_per_currency=1000, budget_units=100000, valid_until=END)
    return jobs, binding, prices, request


def repin(args):
    jobs, binding, prices, request = args
    request.update(jobs_sha256=sha(jobs), binding_sha256=sha(binding), prices_sha256=sha(prices))
    return args


class SpendTests(unittest.TestCase):
    def test_eight_requests_and_all_token_categories_are_charged(self):
        # Per job USD: .8 + 1 + .4 + .09 + .16 = 2.45; two jobs = 4.90.
        result = project_spend(*declarations(), now=NOW)
        self.assertEqual(result['total_units'], 4900)
        self.assertEqual(result['jobs'], {'actor': 4900})
        self.assertFalse(result['runtime_admission'])

    def test_auxiliary_and_second_model_share_one_budget(self):
        args = declarations()
        jobs, binding, prices, request = args
        jobs['control'] = dict(id='control', model='model-version-2', count=3,
                               per_job=dict(zip(COUNTERS, (2, 0, 0, 0, 0))))
        prices['quotes']['q2'] = copy.deepcopy(prices['quotes']['q1'])
        prices['quotes']['q2'].update(model='model-version-2')
        prices['quotes']['q2']['rates']['provider_requests'] = ratio(1, 4)
        binding['routes']['control'] = dict(model='model-version-2', quotes=['q2'])
        repin(args)
        # Actor 4.90 + six auxiliary requests at .25 = 6.40 USD.
        request['budget_units'] = 6400
        result = project_spend(*args, now=NOW)
        self.assertEqual(result['total_units'], 6400)
        self.assertEqual(result['jobs']['control'], 1500)
        request['budget_units'] = 6399
        with self.assertRaisesRegex(SpendRefusal, '^spend_budget_exceeded$'):
            project_spend(*args, now=NOW)

    def test_fractional_cost_rounds_up_and_rounding_can_happen_per_request(self):
        args = declarations()
        jobs, binding, prices, request = args
        jobs['actor'].update(count=1, per_job=dict(zip(COUNTERS, (8, 8, 0, 0, 0))))
        quote = prices['quotes']['q1']
        quote['rates'] = {c: ratio(0) for c in COUNTERS}
        quote['rates']['input_tokens'] = ratio(1, 10_000_000_000)
        request['units_per_currency'] = 1_000_000_000
        repin(args)
        # Eight .1 nanoUSD tokens: .8 rounds upward to one, never zero.
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 1)
        quote['rounding_quantum'] = ratio(1, 1_000_000_000)
        repin(args)
        # Distribution is unknown: eight requests can each round .1 to one.
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 8)

    def test_all_applicable_tiers_and_endpoints_use_component_maxima(self):
        args = declarations()
        jobs, binding, prices, request = args
        expensive = copy.deepcopy(prices['quotes']['q1'])
        expensive.update(endpoint='endpoint-B', tier='long-context')
        expensive['rates']['input_tokens'] = ratio(2, 100)
        expensive['rates']['output_tokens'] = ratio(0)
        prices['quotes']['q2'] = expensive
        binding['routes']['actor']['quotes'].append('q2')
        repin(args)
        # Max input adds USD 1 per job; output keeps q1's .40, not q2's zero.
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 6900)

    def test_missing_counters_prices_and_unknown_domains_refuse(self):
        mutations = [
            (lambda a: a[0]['actor']['per_job'].pop('cache_read_input_tokens'), 'spend_counter_shape'),
            (lambda a: a[2]['quotes']['q1']['rates'].pop('cache_creation_input_tokens'), 'spend_price_shape'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(output_tokens=None), 'spend_rate_shape'),
            (lambda a: a[2]['quotes']['q1'].update(other_fees='unknown'), 'spend_pricing_unsupported'),
            (lambda a: a[2]['quotes']['q1'].update(pricing='tier-threshold-unknown'), 'spend_pricing_unsupported'),
            (lambda a: a[1].update(coverage='partial'), 'spend_routing_unsupported'),
            (lambda a: a[1].update(unlisted_routes='fallback'), 'spend_routing_unsupported'),
        ]
        for mutate, code in mutations:
            args = declarations()
            mutate(args)
            repin(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)

    def test_exact_inventory_route_quote_and_model_coverage(self):
        mutations = [
            (lambda a: a[1]['routes'].clear(), 'spend_route_coverage'),
            (lambda a: a[1]['routes'].update(extra=copy.deepcopy(a[1]['routes']['actor'])), 'spend_route_coverage'),
            (lambda a: a[1]['routes']['actor'].update(model='alias'), 'spend_model_mismatch'),
            (lambda a: a[1]['routes']['actor'].update(quotes=['missing']), 'spend_quote_coverage'),
            (lambda a: a[1]['routes']['actor'].update(quotes=['q1','q1']), 'spend_quote_coverage'),
            (lambda a: a[2]['quotes'].update(unused=copy.deepcopy(a[2]['quotes']['q1'])), 'spend_quote_coverage'),
            (lambda a: a[2]['quotes']['q1'].update(model='unknown-version'), 'spend_model_mismatch'),
            (lambda a: a[0]['actor'].update(id='different'), 'spend_job_identity'),
        ]
        for mutate, code in mutations:
            args = declarations()
            mutate(args)
            repin(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)

    def test_pins_schema_currency_and_validity_cover_the_whole_projection(self):
        mutations = [
            (lambda a: a[3].update(jobs_sha256='c'*64), 'spend_pin_mismatch'),
            (lambda a: a[3].update(binding_sha256='c'*64), 'spend_pin_mismatch'),
            (lambda a: a[3].update(prices_sha256='c'*64), 'spend_pin_mismatch'),
        ]
        for mutate, code in mutations:
            args = declarations()
            mutate(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)
        changes = [
            (lambda a: a[1].update(plan_sha256='c'*64), 'spend_plan_mismatch'),
            (lambda a: a[2].update(schema='cairn.provider-prices/0'), 'spend_schema'),
            (lambda a: a[2].update(currency='EUR'), 'spend_currency_unsupported'),
            (lambda a: a[2]['quotes']['q1'].update(source_sha256='not-a-pin'), 'spend_pin_shape'),
            (lambda a: a[1].update(observed_at=END), 'spend_stale'),
            (lambda a: a[1].update(valid_until=NOW), 'spend_stale'),
            (lambda a: a[2]['quotes']['q1'].update(valid_until=NOW), 'spend_stale'),
            (lambda a: a[2]['quotes']['q1'].update(observed_at=END), 'spend_stale'),
            (lambda a: a[3].update(valid_until='2026-10-16T00:00:00Z'), 'spend_stale'),
            (lambda a: a[3].update(valid_until=NOW), 'spend_stale'),
            (lambda a: a[1].update(observed_at='invalid'), 'spend_timestamp'),
        ]
        for mutate, code in changes:
            args = declarations()
            mutate(args)
            repin(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)

    def test_no_float_boolean_negative_zero_request_or_overflow(self):
        changes = [
            (lambda a: a[0]['actor']['per_job'].update(provider_requests=0), 'spend_demand_bound'),
            (lambda a: a[0]['actor']['per_job'].update(input_tokens=True), 'spend_demand_bound'),
            (lambda a: a[0]['actor']['per_job'].update(output_tokens=-1), 'spend_demand_bound'),
            (lambda a: a[0]['actor'].update(count=0), 'spend_demand_bound'),
            (lambda a: a[0]['actor'].update(count=2**63), 'spend_demand_bound'),
            (lambda a: a[0]['actor']['per_job'].update(input_tokens=2**62), 'spend_overflow'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(input_tokens=ratio(0.1)), 'spend_rate_bound'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(output_tokens=ratio(-1)), 'spend_rate_bound'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(output_tokens=ratio(1, 0)), 'spend_rate_bound'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(output_tokens=ratio(2**63)), 'spend_rate_bound'),
            (lambda a: a[2]['quotes']['q1'].update(rounding_quantum=ratio(True)), 'spend_rate_bound'),
            (lambda a: a[3].update(units_per_currency=0), 'spend_unit_bound'),
            (lambda a: a[3].update(budget_units=-1), 'spend_unit_bound'),
            (lambda a: a[2]['quotes']['q1']['rates'].update(input_tokens=ratio(2**62)), 'spend_overflow'),
        ]
        for mutate, code in changes:
            args = declarations()
            mutate(args)
            repin(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)

    def test_closed_shapes_and_empty_inventory_refuse(self):
        changes = [
            (lambda a: a[0].clear(), 'spend_jobs'),
            (lambda a: a[3].update(credit_balance=100000), 'spend_request_shape'),
            (lambda a: a[0]['actor'].update(role='unknown'), 'spend_job_shape'),
            (lambda a: a[0]['actor']['per_job'].update(hidden_tokens=1), 'spend_counter_shape'),
            (lambda a: a[1]['routes']['actor'].update(fallback_model='alias'), 'spend_route_shape'),
            (lambda a: a[2]['quotes']['q1'].update(cache_ttl_unknown=True), 'spend_quote_shape'),
            (lambda a: a[2]['quotes']['q1']['rates']['input_tokens'].update(discount=.5), 'spend_rate_shape'),
        ]
        for mutate, code in changes:
            args = declarations()
            mutate(args)
            repin(args)
            with self.subTest(code=code), self.assertRaisesRegex(SpendRefusal, '^'+code+'$'):
                project_spend(*args, now=NOW)

    def test_zero_prices_are_explicit_and_projection_does_not_mutate_inputs(self):
        args = declarations()
        for c in COUNTERS:
            args[2]['quotes']['q1']['rates'][c] = ratio(0)
        args[3]['budget_units'] = 0
        repin(args)
        before = copy.deepcopy(args)
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 0)
        self.assertEqual(args, before)

    def test_nondividing_billing_quanta_do_not_undercharge(self):
        args = declarations()
        jobs, binding, prices, request = args
        jobs['actor'].update(count=1, per_job=dict(zip(COUNTERS, (1, 1, 0, 0, 0))))
        q1 = prices['quotes']['q1']
        q1['rates'] = {c: ratio(0) for c in COUNTERS}
        q1['rates']['input_tokens'] = ratio(7, 10)
        q1['rounding_quantum'] = ratio(6, 10)
        q2 = copy.deepcopy(q1)
        q2.update(tier='other', rounding_quantum=ratio(1))
        prices['quotes']['q2'] = q2
        binding['routes']['actor']['quotes'].append('q2')
        repin(args)
        # .70 rounded to .60 quantum is 1.20, exceeding rounding to 1.00.
        # The mixed-quantum bound is .70 + 1.00 = 1.70 USD, safely >= either.
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 1700)

    def test_projection_carries_exact_declaration_and_time_bindings(self):
        args = declarations()
        result = project_spend(*args, now=NOW)
        self.assertEqual(result['request_sha256'], sha(args[3]))
        self.assertEqual(result['plan_sha256'], 'b'*64)
        self.assertEqual(result['currency'], 'USD')
        self.assertEqual(result['units_per_currency'], 1000)
        self.assertEqual(result['projected_at'], NOW)
        self.assertEqual(result['valid_until'], END)

    def test_fraction_beyond_float_precision_cannot_round_down(self):
        args = declarations()
        jobs, binding, prices, request = args
        jobs['actor'].update(count=1, per_job=dict(zip(COUNTERS, (1, 0, 0, 0, 0))))
        prices['quotes']['q1']['rates'] = {c: ratio(0) for c in COUNTERS}
        prices['quotes']['q1']['rates']['provider_requests'] = ratio(27021597764222977, 3)
        request.update(units_per_currency=1, budget_units=2**63-1)
        repin(args)
        # 9007199254740992 + 1/3. Binary float loses the fraction here.
        self.assertEqual(project_spend(*args, now=NOW)['total_units'], 9007199254740993)

    def test_aggregate_cost_overflow_refuses_even_when_each_job_fits(self):
        args = declarations()
        jobs, binding, prices, request = args
        jobs['actor'].update(count=1, per_job=dict(zip(COUNTERS, (1, 0, 0, 0, 0))))
        jobs['aux'] = copy.deepcopy(jobs['actor'])
        jobs['aux']['id'] = 'aux'
        binding['routes']['aux'] = copy.deepcopy(binding['routes']['actor'])
        prices['quotes']['q1']['rates'] = {c: ratio(0) for c in COUNTERS}
        prices['quotes']['q1']['rates']['provider_requests'] = ratio(2**62)
        request.update(units_per_currency=1, budget_units=2**63-1)
        repin(args)
        with self.assertRaisesRegex(SpendRefusal, '^spend_overflow$'):
            project_spend(*args, now=NOW)

    def test_unknown_nonfinite_values_refuse_before_hashing(self):
        for value in (float('nan'), float('inf'), None, 'unknown', False):
            args = declarations()
            args[2]['quotes']['q1']['rates']['cache_creation_input_tokens']['numerator'] = value
            with self.subTest(value=value), self.assertRaisesRegex(SpendRefusal, '^spend_rate_bound$'):
                project_spend(*args, now=NOW)


if __name__ == '__main__':
    unittest.main()
