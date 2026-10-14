"""Pure declaration-based spend projection. No I/O, prices, funding or admission."""
from fractions import Fraction
import re
import datetime as dt
import hashlib
import json

MAX_INTEGER = 2**63 - 1

COUNTERS = ('provider_requests', 'input_tokens', 'output_tokens',
            'cache_read_input_tokens', 'cache_creation_input_tokens')


class SpendRefusal(ValueError):
    """Fixed refusal code, without caller content."""


def need(condition, code):
    if not condition:
        raise SpendRefusal(code)


def shape(value, fields, code):
    need(type(value) is dict and set(value) == set(fields.split()), code)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def name(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value) is not None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def pin(value):
    need(type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None, 'spend_pin_shape')


def timestamp(value):
    need(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value) is not None,
         'spend_timestamp')
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise SpendRefusal('spend_timestamp') from None


def fresh(document, now, until):
    need(timestamp(document['observed_at']) <= now < until <= timestamp(document['valid_until']), 'spend_stale')


def validate(jobs, binding, prices, request, now):
    shape(request, 'schema plan_sha256 jobs_sha256 binding_sha256 prices_sha256 currency units_per_currency budget_units valid_until', 'spend_request_shape')
    shape(binding, 'schema plan_sha256 observed_at valid_until coverage unlisted_routes routes', 'spend_binding_shape')
    shape(prices, 'schema currency quotes', 'spend_prices_shape')
    need(request['schema'] == 'cairn.provider-spend-request/1'
         and binding['schema'] == 'cairn.provider-spend-routes/1'
         and prices['schema'] == 'cairn.provider-prices/1', 'spend_schema')
    need(integer(request['units_per_currency'], 1) and integer(request['budget_units']), 'spend_unit_bound')
    need(request['currency'] == prices['currency'] == 'USD', 'spend_currency_unsupported')
    for field in ('plan_sha256', 'jobs_sha256', 'binding_sha256', 'prices_sha256'):
        pin(request[field])
    pin(binding['plan_sha256'])
    need(binding['plan_sha256'] == request['plan_sha256'], 'spend_plan_mismatch')
    current, until = timestamp(now), timestamp(request['valid_until'])
    fresh(binding, current, until)
    need(binding['coverage'] == 'complete' and binding['unlisted_routes'] == 'forbidden', 'spend_routing_unsupported')
    need(type(jobs) is dict and 0 < len(jobs) <= 576, 'spend_jobs')
    need(type(prices['quotes']) is dict and 0 < len(prices['quotes']) <= 64, 'spend_quotes')
    need(type(binding['routes']) is dict and set(binding['routes']) == set(jobs), 'spend_route_coverage')
    for ident, job in jobs.items():
        shape(job, 'id model count per_job', 'spend_job_shape')
        need(name(ident) and job['id'] == ident and name(job['model']), 'spend_job_identity')
        shape(job['per_job'], ' '.join(COUNTERS), 'spend_counter_shape')
        need(integer(job['count'], 1) and all(integer(value, 1 if counter == 'provider_requests' else 0)
             for counter, value in job['per_job'].items()), 'spend_demand_bound')
        need(all(job['count'] * value <= MAX_INTEGER for value in job['per_job'].values()), 'spend_overflow')
    for ident, quote in prices['quotes'].items():
        need(name(ident), 'spend_quote_identity')
        shape(quote, 'provider model endpoint tier source_sha256 observed_at valid_until pricing other_fees rates rounding_quantum', 'spend_quote_shape')
        need(all(name(quote[k]) for k in ('provider', 'model', 'endpoint', 'tier')), 'spend_quote_identity')
        need(quote['pricing'] == 'linear-upper-bound/1' and quote['other_fees'] == 'included', 'spend_pricing_unsupported')
        pin(quote['source_sha256'])
        fresh(quote, current, until)
        shape(quote['rates'], ' '.join(COUNTERS), 'spend_price_shape')
        for rate in list(quote['rates'].values()) + [quote['rounding_quantum']]:
            shape(rate, 'numerator denominator', 'spend_rate_shape')
            need(integer(rate['numerator']) and integer(rate['denominator'], 1), 'spend_rate_bound')
    used = set()
    for ident, route in binding['routes'].items():
        shape(route, 'model quotes', 'spend_route_shape')
        need(route['model'] == jobs[ident]['model'], 'spend_model_mismatch')
        selected = route['quotes']
        need(type(selected) is list and 0 < len(selected) <= 64
             and all(name(q) and q in prices['quotes'] for q in selected)
             and len(set(selected)) == len(selected), 'spend_quote_coverage')
        need(all(prices['quotes'][q]['model'] == route['model'] for q in selected), 'spend_model_mismatch')
        used.update(selected)
    need(used == set(prices['quotes']), 'spend_quote_coverage')
    need(all(digest(document) == request[field] for document, field in
             ((jobs, 'jobs_sha256'), (binding, 'binding_sha256'), (prices, 'prices_sha256'))), 'spend_pin_mismatch')


def ceiling(value):
    return -(-value.numerator // value.denominator)


def counter_cost(units, requests, rates, quanta):
    rate = max(rates)
    if units == 0 or rate == 0:
        return Fraction(0)
    exact = units * rate
    quantum = max(quanta)
    if not quantum:
        return exact
    nonempty = min(units, requests)
    if len(set(quanta)) == 1:
        return (ceiling(exact / quantum) + nonempty - 1) * quantum
    # Different quanta need not divide one another. A larger quantum alone
    # does not necessarily produce a larger rounded charge.
    return exact + nonempty * quantum


def project_spend(jobs, binding, prices, request, *, now):
    """Project a pinned inventory returned by provider_workload(plan, models).

    Inputs are borrowed plain JSON objects, never mutated; callers must not
    mutate them concurrently. Complete workload validation is an upstream
    precondition. Declarations and their hashes do not establish their truth.
    """
    validate(jobs, binding, prices, request, now)
    costs = {}
    for ident, job in jobs.items():
        quotes = [prices['quotes'][key] for key in binding['routes'][ident]['quotes']]
        quanta = [Fraction(q['rounding_quantum']['numerator'], q['rounding_quantum']['denominator'])
                  for q in quotes]
        cost = sum(counter_cost(job['per_job'][counter], job['per_job']['provider_requests'],
                                [Fraction(q['rates'][counter]['numerator'],
                                          q['rates'][counter]['denominator']) for q in quotes], quanta)
                   for counter in COUNTERS)
        cost *= job['count'] * request['units_per_currency']
        costs[ident] = ceiling(cost)
    need(sum(costs.values()) <= MAX_INTEGER, 'spend_overflow')
    if sum(costs.values()) > request['budget_units']:
        raise SpendRefusal('spend_budget_exceeded')
    return dict(schema='cairn.provider-spend-projection/1', jobs=costs,
                total_units=sum(costs.values()), currency=request['currency'],
                units_per_currency=request['units_per_currency'],
                plan_sha256=request['plan_sha256'], request_sha256=digest(request),
                projected_at=now, valid_until=request['valid_until'], runtime_admission=False)
