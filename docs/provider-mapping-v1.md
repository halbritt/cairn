# Original107 provider mapping bridge

`scripts/trial_provider_mapping.py` joins the existing complete provider workload,
capacity/2 routes and conservative spend projection. It refuses account/quote
and quota-window mismatches that the separate validators cannot detect. It is an
offline source validator with local pinned-file reads; it performs no provider,
account, reservation, launch or admission operation.

```python
project_provider_mapping(
    plan, models, capacity_reference, spend_request_reference, mapping_reference,
    now='2026-10-14T00:00:00Z',
)
```

There is no caller-supplied job inventory or automatic estimate. The bridge calls
`provider_workload(plan, models)` itself. It reuses `capacity_check`,
`capacity_accounts`, `provider_demands`, strict pinned JSON loading and
`project_spend`; it does not implement another pricing or quota arithmetic path.
It raises the existing `trial_paired_request.Refusal` or `SpendRefusal` codes;
unavailable filesystem operations can raise `OSError`, as in the existing file
interface. A public caller must use the existing sanitized refusal boundary.

## Original-count and privacy boundary

The plan retains its existing closed shape and canonical hash. This bridge
requires `weekly-frozen-cases`, 21 distinct case IDs, three distinct valid seeds,
`[CURRENT, CAND]` arms and 126 actor attempts. The workload validator requires
63 job instances for each arm and each grader (one declaration with `count: 63`
per core role), reserving both graders for every possible
changed pair. All nine workload roles remain explicit, with verified basis pins:
CURRENT, CAND, primary, secondary, selector, control, retry, dispute and auxiliary.
Every included job declares positive provider-request demand and all four token
categories. An optional role can be `not_applicable` only under the existing
explicit empty-group/basis contract. No missing counter, fee or role becomes zero.

These are **count and declaration checks**, not frozen-case identity acceptance.
The bridge does not require/open the frozen input tree or inspect case/oracle/
corpus records. Operator-owned workload/basis/price evidence paths are read and
hashed: callers must keep them appropriate, non-secret and quiescent. Inputs are
borrowed plain JSON values; callers must not mutate them concurrently. Full
`request/2` validation still binds actual frozen IDs, models, independent graders,
review dispositions and the other acceptance criteria in the owning producer.
The bridge cannot replace that validation. Supplemental fresh tasks cannot be
passed as this bridge's weekly instrument. The original 1900-distractor corpus
requirement remains; the bridge does not inspect or prove corpus identity/count.

All declaration files use the existing strict JSON reader: duplicate keys,
nonfinite constants, bad file pins, non-regular files and path indirection refuse.
Price-source and basis files are opaque pinned evidence bytes, not parsed claims
or authenticated testimony. Their content can still be false or inapplicable.
This change leaves request/2 mandatory, capacity/2 dispatch unchanged, historical
`capacity_check_v1` untouched, and every existing original107 limit unchanged.

## Account/quote mapping document

`mapping_reference` is a normal `{path, sha256}` file pin. Its closed document is:

| Field | Meaning |
|---|---|
| schema | `cairn.provider-account-quotes/1` |
| plan_sha256 | Existing canonical plan hash, including the workload file pin |
| capacity_sha256 | Exact bytes hash from `capacity_reference` |
| spend_request_sha256 | Exact bytes hash from `spend_request_reference` |
| observed_at, valid_until | Exact UTC timestamps covering the entire projection |
| coverage | `complete` |
| unlisted_routes | `forbidden` |
| spend_binding, prices | File pins for the existing spend routes/1 and prices/1 documents |
| quotes | Exact map of all price quote IDs to the entries below |

Each quote entry contains exactly `price_source` and `accounts`.
`price_source` is a file pin whose bytes hash must equal the existing price
quote's `source_sha256`. `accounts` is a nonempty map of at most 64 declared
capacity account IDs. One public quote may be permitted for multiple explicitly
mapped accounts; a quote is not owned by a unique account.

Each account permission has exactly `provider`, `model`, `endpoint`, `tier`,
`basis` and `windows`. The four identity fields must equal the referenced quote.
`basis` pins the source supporting permission for that account/route throughout
the mapping interval. `windows` is a nonempty, duplicate-free list of at most
64 capacity window objects (`pool_id`, `window_id`, `metrics`).

For every derived job:

1. Capacity and spend route maps must each cover exactly the same complete jobs.
2. Both routes and every quote must match the job's declared model.
3. Every quote alternative must explicitly permit the capacity route's account.
4. The union of windows required by those quote/account alternatives must equal
   the job's capacity windows. Window objects compare by canonical content;
   list order between windows is irrelevant, but metric-list order must agree.
5. Every declared account/quote pair must actually be selected by at least one
   job. Extra quote IDs, missing permissions, unknown accounts and unused pairs
   refuse. This is a plan-specific permission inventory, not a general catalog.

Capacity still charges each job's full demand to every mapped pool/window, using
the existing global summation across accounts and models. Quota allowances from
overlapping windows are never added. Endpoint/tier alternatives may require
different pools; the explicit union ensures those constraints are retained.
A fallback to another account or model needs a separately accounted job and
route; it cannot silently use the current job's quota or cheaper price.

`now < spend_request.valid_until` and all capacity, mapping, spend binding and
quote validity must cover that same interval. The existing capacity validator
also requires the reservation declaration to last at least as long as capacity.
This validates a held-reservation **declaration** only; it creates no hold or
reservation. Mapping timestamps cover the account-permission inventory as a
whole; opaque basis files have no independently inferred freshness.

File pins hash exact bytes. The existing spend request's jobs/binding/prices
fields use canonical JSON hashes. They are different contracts; the producer
must compute both correctly. No boolean authentication flag changes either one.

## Result and worked synthetic checks

The result is `cairn.provider-mapping-projection/1`: input pins and interval,
the unchanged conservative spend result, per-job account/quote/window bindings,
derived shared `pool_demands`, and `account_totals`. Each account's `spend_units`
is the sum of that account's already upward-rounded per-job costs; its `demands`
sum all five counters. Currency and unit come from the nested spend result.
`runtime_admission` is always false.

An aggregate budget fit does not show that **any** account has that balance.
Per-account totals are requirements for later funding checks, not available
credits. Account declarations also do not prove dedication or actual routing.
No aliases, percentages or provider summary fields are resolved by inference.

The full-count synthetic fixture uses four core job declarations of count 63 and
five auxiliary job declarations of count two. Core request bounds 8/8/1/2 plus the explicit
auxiliary requests total 1227; disjoint token categories total 40450 in one shared
pool. Those arbitrary auxiliary/token bounds are test data, not actual campaign
calibration. Same-model account prices yield 362870 and 293580 milliUSD, totaling
656450. Sharing one explicitly permitted public quote yields 509660. Adding a
long-context endpoint alternative uses component-wise price maxima and charges
the account's full work to its additional pool, yielding 846150. Tests use these
independent worked expectations, not a second implementation of the arithmetic.

## Producer wiring proposal (outside this lane)

The bridge is deliberately not wired into `trial_paired_request.prepare` here.
Root can add a required pinned companion review document binding
`canonical_sha(request)` to the spend-request and account/quote mapping pins.
That companion needs its own closed schema and review coverage; do not add
unrecognized fields to request/2 or accept a missing companion by fallback.

In `scripts/trial_paired_request.py:prepare`, the concrete insertion point is
after `bundle = validate(request, now=now)` and **before** any registry marker or
output write. Verify the companion's request hash, derive models exactly as the
existing capacity call in `validate` does (frozen native model for both arms;
request grading models for primary/secondary), then call this bridge with
`request['plan']` and `request['capacity']`. Pin the resulting projection in the
reviewed preparation output. Apply the same check to the prospective `check`
path. These are proposed source changes for root, not implemented side effects.

Before such a projection can support runtime admission, root needs actual
producer evidence for:

- Complete role/job inventory, retry and unknown-outcome policy, and calibrated
  positive provider-request plus disjoint token/cache upper bounds. A pinned
  analysis cannot make fabricated estimates or unjustified zeros true.
- Actual account identity/dedication, permitted provider/model/endpoint/tier
  controls and complete shared quota pools/windows. Source equality cannot
  establish these facts or stop a provider fallback at runtime.
- Current applicable price bounds for request fees, all cache variants,
  context/endpoint tiers, billing rounding and other charges. Root's public
  quote artifact, with authentication/account-access/pricing-bound attestations
  false, cannot directly supply qualified prices or zero missing fees. Cache
  write TTL and context-tier alternatives must all be bounded or refused.
- Absolute usable allowance and funded balance per actual account, real held
  reservations, and cumulative worst-case debit **before forwarding** every
  provider request. Reconciliation must retain unknown outcomes conservatively;
  this helper does not reserve, forward, refund or settle anything.

Known calibration gap at this source base: `scripts/trial_openrouter.py` retains
only flat prompt/completion/total token and cost usage fields, dropping nested
cache detail. `scripts/trial_task_eval.py:parse_codex_stream` and
`parse_claude_stream` expose token summaries with missing values but no
authenticated `provider_requests`; their turn counts use native turn units.
Do not infer one request per turn/attempt, disjoint cached/uncached semantics,
missing cache zero, or full retry/selector demand from those summaries. No edits
to those producers or actual calibration runs are part of this candidate.

Full original107 acceptance still includes the frozen 21-case, 1900-distractor,
three-seed, two-arm v12/current vs candidate same-worker comparison, disposable
PostgreSQL, both blind independent graders
and delegated dispute policy, dated reports/Cairn notes, two consecutive complete
weekly campaigns without losses, candidate self-service and an account separate
from interactive use. This projection establishes none of their runtime results.

Run the new and affected pure/local-file tests, then `make check`:

```sh
python3 -B -m unittest discover -s scripts -p test_trial_provider_mapping.py
PYTHONPATH=scripts python3 -B -m unittest test_trial_paired_capacity test_trial_paired_request test_trial_provider_spend
make check
```
