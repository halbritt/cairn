# Event pressure and admission policy

The event pressure interface reports retained queue state and offers a bounded,
read-only admission preview. Observation support starts with migration 080.
The complete enforcement contract requires migration 084 and a compatible binary.
Policies start disabled; installation does not enable a production policy.
Handling, retry, renewal, cancellation and accepted-work recovery remain available
when fresh-work capacity or rate budgets are exhausted.

This describes source capability. It does not establish that a running API,
scheduler or native integration has been upgraded, or enable a deployed policy.
Check the installed CLI and API with `cairn version` and
`cairn agent --token-file PROFILE version` before relying on a new operation.

## Read pressure

Authenticated agent commands use the existing profile and optional session:

```sh
cairn event-pressure --token-file PROFILE --repo COLLECTION --limit 50
cairn event-fanout --token-file PROFILE --repo COLLECTION --after 0 --limit 50
cairn event-admission-preview --token-file PROFILE --repo COLLECTION --topic exact-topic
```

`event-pressure` shows the effective inbox's deliveries, subscriptions and queued
quiet notices, plus that producer's pending pool requests and publications.
Session UUIDs select an owned, current registration; they grant no new authority.
It also includes pending waits owned by that session. Operator workload text and
policy author identity are withheld from agent pressure output.

`event-fanout` shows only publications by the effective authenticated producer.
Its recipient count comes from retained deliveries and quiet-notice membership,
so a later unsubscribe or quiet-notice expiry cannot shrink the original
snapshot. Queued quiet notices are separate from materialized deliveries.
An unassigned pool event says `awaiting_assignment` with a null recipient count;
its zero current deliveries do not mean it has no outstanding work.

Both views have bounded pages of 1–100 items. Continue pressure pages with
`--after-recipient` and fanout pages with `--after`. Each page reads a current
snapshot; it does not pin a multi-page historical snapshot. A pressure recipient
with `presence: stopped` is explicitly stopped. `offline` means its presence
expired. Neither case removes an active topic subscription or excludes its
recipient from publication.

Operators can inspect the whole collection through JSON commands:

```sh
printf '%s\n' '{"repo":"COLLECTION","limit":50}' | cairn event-pressure-collection
printf '%s\n' '{"repo":"COLLECTION","after":0,"limit":50}' | cairn event-fanout-collection
printf '%s\n' '{"repo":"COLLECTION"}' | cairn event-admission-policy
printf '%s\n' '{"repo":"COLLECTION","destination":{"type":"topic","name":"exact-topic"}}' | cairn event-admission-preview-collection
```

These commands require the operator channel and have no agent API route.
Operational event bodies and memory text are omitted from diagnostics.
The operator-only `event-admission-refusals` command reports bounded counters by
dimension, including refusals whose publication transaction rolled back. Each
`refusal_count` counts individual refused admission attempts (including repeated
scheduler ticks or worker claims that re-evaluate a still-pending item), not
distinct logical work items.

## Select an observation policy

The schema is [event-admission-policy.schema.json](event-admission-policy.schema.json).
The schema is preliminary validation: any limit requires a declared workload,
and the API additionally enforces a 2000 UTF-8 **byte** cap on the workload text,
while the schema's `maxLength` counts Unicode characters.
An absent policy has revision 0, disabled mode and no implicit numeric defaults.
An operator sets a policy with `event-admission-configure`, an explicit collection,
`expected_revision`, and a stable `request_id`. A committed retry with identical
intent returns its original result. A different edit needs the current revision.
Setting `mode: enforce` requires the complete schema and binary capability, plus
a completed closure reconciliation for the current restore generation. Partial
writers cannot enable it; incomplete schema or binary support returns
`FEATURE_UNAVAILABLE`. New ordinary publications and waits also check the
reconciliation certificate after restore. Exact accepted closure and transfers
of already charged pool or quiet work retain their drain paths.

[The observation fixture](../fixtures/event-admission-observe.json) declares a
synthetic coordinator and two workers, with two queued publications per worker.
It selects a topic bound of two, inbox and producer/inbox bounds of two, collection
bound of four, closure bound of four and recipient closure bound of two. The
rates and bursts are two units/second and four units. These small values expose
boundaries in a disposable test; they are not measured production defaults.
Use a new request UUID and the actual revision when adapting the fixture.

All counts, rates and bursts must be positive finite integers at most one billion;
topic fanout is at most 10,000. Separate persisted buckets charge the
publication/root count and actual delivery fanout for the effective producer,
validated registration owner and collection. Tokens refill at the database clock. A zero-recipient topic still costs
one publication unit. A new session or correlation UUID does not reset its
profile owner's bucket. A fanout larger than a configured burst returns
`retryable: false`; the policy or workload must change before retrying that intent.
Other rate refusals return a bounded backoff hint, never promised acceptance.

A first accepted wait costs one root unit and one future terminal-notice delivery
unit, including a wait already satisfied at creation. Its UUID retry does not
charge again. Wait outbox drain, validated replies and other accepted closure
operations take no new debit. Quiet materialization and pool assignment transfer
existing charges. A caller-selected response kind alone grants no exemption.

A policy revision clamps retained tokens to the new burst and discards any
uncredited refill before that policy's update time. It then refills from the later
of the retained refill time and policy update time. Disable/re-enable and rapid
policy edits do not grant a fresh burst. A backward database clock grants no
refill until it catches up.

A restore fence atomically sets existing bucket balances to zero and anchors
refill at the fence's database time. Rejected work cannot roll that retained
anchor forward and starve refill. A key absent from the restored backup receives
its explicit first-use burst only after fresh closure reconciliation; existing
owner and collection buckets still constrain session churn. This bounds
initialization but does not reconstruct spending lost after a backup.

A preview examines at most the topic bound plus one subscribers. Exact recipient
counts are withheld from authenticated agent previews, including the publisher.
A local operator preview can return the count or a labelled lower bound;
`recipient_count_exact: false` marks a bounded snapshot that crossed the cap.
It counts pending and leased ordinary deliveries, queued quiet notices, and
open unassigned pools for collection capacity. Recipient and producer/recipient
forecasts use the already known recipient units. It never returns another recipient's identity or
queue count. `would_refuse` is a list of dimensions, not admission, a reservation,
a backoff promise or evidence that a subsequent publication will succeed.
`unsupported` lists actual missing support. The destination-only request cannot
evaluate source authorization or validated closure eligibility; `unevaluated`
names those limits. Capacity forecasts use ordinary charges and do not grant a
closure exemption.
The preview does not authenticate a source record or replace publication's
source, destination or forgetting checks. Disabled mode does not run forecasts.

## Reservoir accounting and limits

Pressure reports keep these reservoirs separate:

| Reservoir | Meaning |
| --- | --- |
| Pending / leased deliveries | Materialized outstanding inbox work, including deferred routine notices and expired leases |
| Queued organic notices | Quiet work whose reserved delivery UUID has not materialized |
| Unassigned pool requests | Accepted requests without an assigned worker delivery |
| Pending schedules | Future occurrences; recipient membership and fanout remain unknown until firing |
| Waiting terminal notices | Waiting durable predicates that retain a future terminal-notice obligation |
| Pending wait outbox | Resolved waits whose notification has not been published |
| Stopped deliveries | Outstanding deliveries in the inspected inbox or collection addressed to explicitly stopped sessions, for any event kind; a subset, not an additional delivery charge |
| Closure reserved / delivered outstanding | Actual protected charges after complete reconciliation in the current generation; null with a labelled unavailable reason before that certificate |
| Normal outstanding | Source-visible pending/leased deliveries excluding consumed closure deliveries, plus queued quiet notices and open unassigned pools; null until closure reconciliation is complete |

Do not sum every row: stopped deliveries overlap inbox counts, while future
schedules and waits have different capacity units. Physical pending/leased counts include delivered closure traffic; do not add
those deliveries to the normal charge again. The separate `closure` object
reports reconciliation state and retained legacy gaps. Leased counts include
expired leases until settlement or retry changes the row. Oldest age uses
publication time, includes deferred work, and is clamped at zero. Existing delivery diagnosis
explains deliberate deferral and holds.

The 60-second publication count is an observed window for the effective
producer, or the collection for operator inspection. The delivery fanout of
those publications uses currently materialized delivery rows; it is not a
materialization-rate measurement or a token-bucket balance.

Observe mode never creates or debits rate buckets. A rate preview says
`uninitialized` when the required real enforced balances do not exist; it does not
infer a token-bucket decision from averages. With retained balances, the preview
reads and refills them hypothetically without writing them. Pressure labels
`retained_buckets` when its scope has real budget state; no private owner or
collection balance is included in a session view. No latency or efficiency
improvement is claimed.

## Writer inventory

The complete source contract covers these writers:

| Writer | Current source path and reservoir |
| --- | --- |
| Direct/topic publication | `core/agent_events.go`: event and recipient snapshot, routine deferral, pool or quiet queue |
| Scheduled firing | `core/agent_schedules.go`: pending occurrence, then `publishEventTx` using membership at firing (published by the trusted operator channel principal) |
| Operator reissue | `core/agent_event_reissue.go`: direct event/delivery or pool request (published by the trusted operator channel principal) |
| Pool assignment | `core/agent_pools.go`: unassigned request becomes one worker delivery |
| Quiet admission / continuation | `core/agent_organic_notices.go`: queued notice becomes leased delivery |
| Quiet explicit flush | `core/agent_organic_notices.go`: queued notice becomes pending delivery |
| Routine notice flush | `core/agent_notice_flush.go`: releases existing availability; creates no delivery |
| Wait resolution / timeout | `core/agent_waits.go`, `core/agent_wait_scheduler.go`: retained wait to outbox to event/delivery |
| Stopped-session notification | `core/agent_stopped_backlog.go`: deduplicated direct event/delivery published by the trusted operator channel principal (charging that operator's producer/owner rate buckets and the collection rate buckets); no retained pending outbox yet |
| Restore / reconciliation | `core/restore_fence.go` and restore rebuild paths: fence and reconcile existing obligations; never replay execution |

Pool assignment scans at most 100 eligible publisher candidates per claim.
A declined publisher rotates while its request and reply reservation remain
pending. A wake result with `attempt: null` means no currently admissible
assignment; it does not certify an empty pool or completion. Keep the existing
polling interval. Assignment transfers the collection charge from the queued
pool request and adds only its newly selected recipient charge.

Quiet initial admission, continuation and explicit flush transfer their already
charged known recipient unit. They do not run a fresh capacity gate, so accepted
quiet work can drain after limits are lowered.

Existing pool, organic-notice, schedule and wait limits remain effective.
The observation policy does not weaken them or add a gate to closure operations.

## Disposable verification

The `TestEventPressureDisposableMixedWorkload` integration fixture uses a separate
database on the test cluster. It sends direct and topic traffic, queues a pool
request, schedules a future occurrence, and queues a quiet notice using a real
registered native owner capability. It verifies each reservoir, committed retry
identity, recipient privacy, bounded fanout pages and overload forecasts without
model calls. Other admission tests cover revision races, finite bounds, local
sensitivity, stopped versus expired presence, and observe-mode publication past
the proposed cap.

```sh
make test-integration
make check
```

The rate fixtures also exercise concurrent owner budgets, fractional refill,
policy revisions, clock rewind, overflow limits, committed UUID retries, rejected
multi-bucket rollback, a new store connection, and a real restore fence with
subsequent refill. The campaign verification records distinguish integrated
correctness from deployment and measured benefit.
