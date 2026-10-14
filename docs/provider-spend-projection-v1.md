# Provider spend projection v1

`scripts/trial_provider_spend.py` calculates a conservative USD projection from
**declared** workload and pricing bounds. It does no I/O and imports only the
standard library. It does not query prices, credentials, accounts or balances.
A successful projection means the supplied declarations fit a supplied spend
ceiling; it does not establish funding, price validity in the provider's system,
account dedication, reservation, capacity, authorization or runtime admission.

## Interface and completeness boundary

```python
project_spend(jobs, binding, prices, request, now='2026-10-14T00:00:00Z')
```

Returns `cairn.provider-spend-projection/1`, or raises `SpendRefusal` with a fixed
code. Inputs are borrowed plain JSON dictionaries/lists/scalars and are not
mutated. The caller must not mutate them concurrently. No filesystem paths or
callback functions are accepted. Parse serialized inputs strictly before calling,
including rejection of duplicate keys; a dictionary cannot reveal discarded keys.

`jobs` must be the **complete result of the existing validated
`trial_paired_request.provider_workload(plan, models)`**, including actors, both
graders and every included selector/control/retry/dispute/auxiliary job. Retain
that validator's full pinned plan/workload evidence. This helper revalidates the
job shapes/counters and exact price-route coverage. It cannot reconstruct roles
or prove that an inventory was complete before it was supplied. Do not hand it a
subset and label the resulting total a whole-plan projection. This new helper is
not wired into paired request preparation and changes no v1/v2 fallback behavior.

All objects have closed shapes. Identifiers match
`[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}`; hashes are lowercase SHA-256 hex. Models must
be exact pinned identifiers, not unresolved aliases. Equality checks do not
resolve aliases or authenticate actual provider routing.

## Exact declaration shapes

The existing job map is keyed by `id`. Each job contains exactly:

- `id`, `model`, `count`, `per_job`.
- `per_job`: `provider_requests`, `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`.

`count` and per-job provider requests are positive integer upper bounds. Token
counters are explicit nonnegative integer upper bounds, **not** an implicit
one-request-per-attempt estimate. Every real provider request, including retries
and fallback attempts, must be included upstream. Token categories are disjoint:
`input_tokens` means uncached input, with cache reads and writes separate. An
upstream usage schema that cannot establish that mapping is unsupported.

`binding` contains exactly:

| Field | Value |
|---|---|
| schema | `cairn.provider-spend-routes/1` |
| plan_sha256 | Exact plan declaration hash |
| observed_at, valid_until | UTC timestamps |
| coverage | `complete` |
| unlisted_routes | `forbidden` |
| routes | Exact job-ID map; each value has `model`, `quotes` |

`quotes` is a nonempty list of distinct price-quote IDs for **all** permitted
provider/endpoint/tier alternatives for this job. The route's model must equal
the job and every selected quote model. Cross-model fallback requires separately
counted jobs; it is not inferred. Unlisted routing must be prevented by the
runtime owner. Every quote in the price document must be used by some route;
missing, unknown, duplicate or unused quotes refuse.

`prices` contains exactly `schema: cairn.provider-prices/1`, `currency: USD`,
and a `quotes` map. Each quote contains exactly:

- `provider`, `model`, `endpoint`, `tier`: explicit identities.
- `source_sha256`: pin of the supporting price source; syntactically checked,
  not fetched or authenticated by this helper.
- `observed_at`, `valid_until`: quote validity declarations.
- `pricing: linear-upper-bound/1`, `other_fees: included`.
- `rates`: the same five mandatory counter names, each a rational USD price per
  individual counter unit: `{"numerator": N, "denominator": D}`.
- `rounding_quantum`: another rational USD amount, described below.

An explicit `0/1` price means a declared zero; missing/null/unknown prices refuse,
including cache prices when declared cache demand is zero. The request rate
includes all per-request charges. All other applicable charges (endpoint,
fallback, cache TTL/storage, minimum charges, taxes, etc.) must have conservative
bounds included in these rates/request fees. This is a source declaration that
needs evidence, not a flag that makes unmodelled fees free. If a domain cannot be
bounded this way, do not set `other_fees: included`; the helper refuses unsupported
pricing. For tiered prices enumerate all applicable tiers/endpoints, or refuse;
no volume discount or currently cheap tier is inferred.

`request` contains exactly:

| Field | Value |
|---|---|
| schema | `cairn.provider-spend-request/1` |
| plan_sha256 | Must match binding |
| jobs_sha256, binding_sha256, prices_sha256 | Exact canonical document pins |
| currency | `USD`; FX is unsupported |
| units_per_currency | Positive integer, e.g. 1000000000 for nanoUSD |
| budget_units | Nonnegative declared whole-plan ceiling, not a balance |
| valid_until | End of the required projection interval |

Canonical hashing is SHA-256 over UTF-8
`json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
allow_nan=False)`, with no newline. The caller pins the request separately;
the result includes its hash, so it transitively binds every input document.
Hashes show agreement with a declaration, not truth or admission authority.

Timestamps use exact `YYYY-MM-DDTHH:MM:SSZ` form. Every binding/quote must satisfy
`observed_at <= now < request.valid_until <= valid_until`. Missing, future,
expired or too-short evidence refuses; `now` is supplied by the caller, not read
from a clock. Declared validity is not a future price guarantee.

## Conservative arithmetic and rounding

All arithmetic uses integers and `Fraction`; float/Decimal values are not part
of this wire contract. Decimal source prices must be converted exactly into
integer ratios by the declaration producer, never through binary floats.

For each job and counter, choose the maximum declared rate across its permitted
quotes. Thus mixed routing cannot exploit choosing the cheapest total quote.
For counter demand D, provider-request bound R and maximum rate P:

- With all billing quanta zero: charge exactly D × P.
- With a common positive quantum Q: charge
  `(ceil(D × P / Q) + min(D, R) - 1) × Q` for positive demand/rate.
- With differing quanta: charge `D × P + min(D, R) × max(Q)`.
- Zero demand or a zero maximum rate costs zero for that counter.

A nonzero quantum declares billing rounds each request/counter charge upward at
most once to that quantum (rounding one combined request charge is also covered).
The common-quantum formula bounds the sum of ceilings over at most min(D, R)
nonempty requests. Differing quanta use an additive margin: simply taking the
largest quantum is unsafe when quanta do not divide one another. A zero quantum
asserts exact billing at that stage. Unbounded repeated rounding/extra billing
lines require a conservative fee bound or are unsupported.

Sum all five counter bounds per job instance, multiply by job count and
units-per-USD, and round **up** to the integer output unit per job. Sum those job
amounts across every model/provider against the one declared budget. Never round
down, net discounts, subtract credits or compare each model separately.

Bounds: at most 576 jobs, 64 quotes and 64 quote references per job. All integer
inputs are at most 2^63−1; rational numerators are nonnegative, denominators
positive. Each count × counter product and the final summed spend must also fit
2^63−1. Rational intermediates use exact arbitrary-precision arithmetic over
this bounded input graph. Overflow refuses, never wraps or saturates.

Worked synthetic example: two actor jobs each declare eight requests, 100 input,
20 output, 30 cache-read and 40 cache-creation tokens. Rates are respectively
$0.10, $0.01, $0.02, $0.003 and $0.004 per unit, with exact billing. Each costs
$2.45; together $4.90. Three auxiliary jobs with two $0.25 requests each add
$1.50. At 1000 units/USD the shared ceiling must cover 6400 units. These numbers
are test data, not vendor prices or demand estimates.

## Integration and limits

The result includes the per-job and total integer costs, currency/unit,
plan/request pins, projection time/validity end, and `runtime_admission: false`.
Important refusals include `spend_pricing_unsupported`, `spend_routing_unsupported`,
`spend_model_mismatch`, `spend_quote_coverage`, `spend_stale`,
`spend_pin_mismatch`, `spend_overflow` and `spend_budget_exceeded`. Malformed shapes,
counters, rates and identifiers have distinct fixed codes as exercised in tests.

Root owns actual provider-cost schema evidence, quote-source verification,
complete workload validation, maximum-current-pricing enforcement and cumulative
budget debit **before each request is forwarded**, plus reconciliation on errors,
retries and unknown outcomes. This helper cannot enforce any of those operations.
It does not establish token headroom or credit availability. The original
8500/9500/8192/32000 budgets and full original107 evaluation remain unchanged.

Run locally: `python3 -B -m unittest discover -s scripts -p test_trial_provider_spend.py`,
then `make check`. Tests use only synthetic declarations and local arithmetic.
