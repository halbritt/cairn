# Paired provider capacity declarations, version 2

`scripts/trial_paired_request.py:capacity_check` checks a pinned capacity
declaration against a separately pinned complete workload and route inventory.
It rejects undercounted requests, omitted counters and missing mapped windows.
It does not observe provider traffic, authenticate accounts, verify available
funding, create a reservation or admit a runtime. Successful request preparation
still reports `runtime_admitted: false`; `launch` still refuses.

The defect in v1 was semantic: an actor attempt was charged exactly one call,
even though it could make multiple provider requests, and a role could omit
token demand entirely. v2 derives every resource's required amount from the
workload. For one paired case with request bounds 8, 8, 1 and 2 for CURRENT,
CAND, primary and secondary, the shared pool requires 19 provider requests.
Charging CURRENT only once would produce 12 and is refused.

## Plan and workload

The request schema is now `cairn.paired-eval.request/2`. Its plan adds a
`workload` file pin, and its canonical `sha256` includes that pin. The existing
instrument, cases, arms, seeds and actor attempt count retain their meaning.
Changing workload bytes requires a new pin, plan hash and matching review,
route, capacity and reservation bindings. A digest does not authenticate any
of these declarations.

The workload is a closed object with exactly:

```json
{"schema":"cairn.paired-provider-workload/2","roles":{}}
```

`roles` must contain exactly CURRENT, CAND, primary, secondary, selector,
control, retry, dispute and auxiliary. Each group has exactly `state`, `basis`
and `jobs`. `basis` is a file pin to the selected workload analysis explaining
counts, bounds and any inapplicable work; its bytes are verified, its claims
are not. There is no default zero-work classification.

- `included` requires 1–64 jobs.
- `not_applicable` requires an empty job list and is forbidden for the four
  actor/grader roles. A pinned basis is required even for an empty group.
- Unknown states, missing groups and extra groups refuse. Multiple distinct
  selectors, controls or other auxiliary operations use separate jobs within
  the appropriate group, rather than introducing unknown role names.

Each job has exactly `id`, `model`, `count` and `per_job`. IDs are unique across
all groups. Names are 1–256 ASCII characters, start with an alphanumeric
character and otherwise allow alphanumerics and `._:/-`. Each of the four core
roles has exactly one job; its count must equal `plan.attempts / 2`, and its
model must match the frozen native or corresponding grader model. Thus both
graders are reserved for every possible changed pair. Auxiliary job counts
are explicit whole-plan bounds, not implicitly multiplied by actor attempts.

`per_job` must contain all five counters:

| Counter | Meaning |
| --- | --- |
| `provider_requests` | Positive upper bound on actual provider requests over the entire job, not turns or actor launches. |
| `input_tokens` | Upper bound on uncached input tokens, excluding the next two input categories. |
| `output_tokens` | Upper bound on generated output tokens under the declared provider accounting semantics. |
| `cache_read_input_tokens` | Upper bound on input tokens read from cache. |
| `cache_creation_input_tokens` | Upper bound on input tokens written to cache. |

All values are explicit integers, at most 2^63−1; booleans, floats, nulls and
negative numbers refuse. Token bounds may be zero when the pinned basis
establishes non-use. Core actor/grader jobs require positive total input
(uncached plus cache-read plus cache-creation) and positive output. Fully cached
input may therefore have zero uncached input. A token-free auxiliary operation
may explicitly declare all four token counters zero and positive requests.
No omitted counter is interpreted as zero. Counts are positive bounded integers.

Bounds are for the whole job, not per request. Count retries, compaction,
selector and grading work exactly once: either within a core job's bound or in
explicit auxiliary jobs, with the partition explained in `basis`. If retries
are included inside actor bounds, the retry group may be `not_applicable` only
with a basis explaining that no additional retry jobs exist. The validator
cannot discover hidden SDK traffic or certify a false inapplicability claim.
Actual producer observations and review must verify this inventory before use.

## Routes and shared windows

`route_binding` pins a closed object with `schema:
"cairn.paired-route-binding/2"`, the exact `plan_sha256`, `state: "complete"`
and `routes`. The route keys must equal the complete set of included job IDs;
extra or absent routes refuse. Each route has exactly `account_id`, `model`
and `windows`. Model must match that job's model. Each account must occur in
the account declaration, and unused declared accounts refuse.

Every route has 1–64 windows. Each window has exactly `pool_id`, `window_id`
and `metrics`, with unique pool/window identities within that route. Pool IDs
identify provider constraint pools across the entire plan, independently of
account aliases or model names. If two accounts or models consume the same
organization pool, their routes must name the same pool ID. If they also share
the same window, their demands add into the same resource. Splitting a real
pool into fictional IDs remains a producer mapping error that source validation
cannot authenticate away.

Every occurrence of the same pool/window must declare the same metric set.
One model cannot silently drop a shared token constraint while another model
still declares it, even if its counters are mapped to another window instead.

Metrics are a nonempty unique list selected from these fixed projections:

| Metric | Counters added per job |
| --- | --- |
| Each of the five counter names | That counter only. |
| `total_tokens` | input + output + cache-read + cache-creation. |
| `input_and_cache_creation_tokens` | input + cache-creation. |

Across each route's windows, the metric projections must cover all five
counters, including explicit zero counters. This prevents a requests-only
or input-only capacity claim. Unknown metrics and percentage conversion are
unavailable. These are declared arithmetic projections, not universal claims
about provider rate-limit units. An actual provider's limits and funding must
support the chosen projections, or a new reviewed contract is needed.

Overlapping windows are checked independently; their allowances are never added.
This version conservatively charges every declared job to every mapped window.
It does not prorate by reset, model a schedule across windows, subtract cache
discounts, reserve dollars, or infer refill capacity. The producer must supply
the full applicable-window inventory and exact model mapping. `complete` is a
required declaration, not evidence that the inventory is actually complete.

## Capacity and reservation

The closed `cairn.paired-capacity-evidence/2` object has exactly `schema`,
`plan_sha256`, `state`, `observed_at`, `valid_until`, `account_binding`,
`route_binding`, `reservation` and `resources`. State must be `declared`;
observation time ≤ check time < validity end, in UTC. Unknown/stale declarations
refuse. `account_binding`, `route_binding` and `reservation` are file pins.
The old quota-policy pin and caller-supplied per-resource demand list are absent:
v2 defines its units explicitly and derives demand from the plan.

Accounts retain `cairn.paired-account-binding/1`, with exactly `schema` and
`accounts`; 1–64 unique account entries have exactly `account_id`, `dedicated:
true` and `interactive_use: false`. These flags remain testimony. Nothing here
verifies provider identity, separation from interactive use or account control.

Each of 1–1024 resources has exactly `pool_id`, `window_id`, `metric`,
`remaining` and `required`. Resource identity is the first three fields, with
no account or model partition. Resource identities must equal the union of
mapped route identities; duplicate, missing and unreferenced resources refuse.

For each resource:

```text
required = sum(job.count * sum(job.per_job[counter] for counter in metric)
               for every job whose route maps to this pool/window/metric)
```

The supplied required amount must equal this independently derived sum exactly.
Remaining must be at least required. Both values and aggregate totals must be
nonnegative integers ≤ 2^63−1; zero demand is permitted for explicit unused
token categories. Available amounts are declarations, never fabricated from
percentages or historical averages.

Reservation is a closed `cairn.paired-reservation/2` with `schema`, exact
`plan_sha256`, canonical `resources_sha256`, `state: "held"` and `valid_until`.
Its validity must cover the entire capacity declaration interval. This checks
bindings only: actual reservation ownership, exclusivity, absolute allowances,
fresh observations, authorization and concurrent consumption belong to root's
producer/admission work. All applicable shared limits must be observed there.

## Preservation and verification

`capacity_check_v1` retains the original /1 validation body and historical
one-call/no-mandatory-token semantics. It is not dispatched by v2 preparation;
v1 capacity cannot prepare a prospective request. There is no automatic upgrade.
The legacy description remains in [the request/work-product document](paired-request-work-product-v1.md).

The original selected-input, hook, MCP, resource and final-answer budgets are
unchanged. This contract adds no provider launcher or account operation. The
original21 weekly instrument, same-worker comparison, independent grading,
two consecutive complete weekly campaigns and candidate self-service remain
separate acceptance work; Fresh8 does not replace them.

Run locally from `scripts`:

```sh
python3 -B -m unittest -v test_trial_paired_capacity test_trial_paired_request
```

The tests use synthetic file pins and known arithmetic totals. They cover eight
requests versus one, mandatory counters/roles, auxiliary work, shared pools
across accounts/models, overlapping windows, unknown/stale declarations and
reservation binding. They establish source validation, not capacity availability.
