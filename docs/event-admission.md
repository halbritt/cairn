# Event pressure and admission policy

The event pressure interface reports retained queue state and offers a bounded,
read-only admission preview. Source support starts with migration 080. Existing
publication, handling, retry, renewal, cancellation and recovery behavior is
unchanged. Enforcement is unavailable until closure reservations and every
alternate delivery writer meet the admission contract.

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

## Select an observation policy

The schema is [event-admission-policy.schema.json](event-admission-policy.schema.json).
An absent policy has revision 0, disabled mode and no implicit numeric defaults.
An operator sets a policy with `event-admission-configure`, an explicit collection,
`expected_revision`, and a stable `request_id`. A committed retry with identical
intent returns its original result. A different edit needs the current revision.
Setting `mode: enforce` returns `FEATURE_UNAVAILABLE` in this slice.

[The observation fixture](../fixtures/event-admission-observe.json) declares a
synthetic coordinator and two workers, with two queued publications per worker.
It selects a topic bound of two, inbox and producer/inbox bounds of two, collection
bound of four, closure bound of four and recipient closure bound of two. The
rates and bursts are two units/second and four units. These small values expose
boundaries in a disposable test; they are not measured production defaults.
Use a new request UUID and the actual revision when adapting the fixture.

All counts, rates and bursts must be positive finite integers at most one billion;
topic fanout is at most 10,000. Rates have separate publication and delivery units
for producer, validated profile owner and collection. These fields define the
policy contract; persisted token-bucket evaluation is unavailable in this slice.
A new session or correlation UUID is not a new profile owner.

A preview examines at most the topic bound plus one subscribers. Exact recipient
counts are withheld from authenticated agent previews, including the publisher.
A local operator preview can return the count or a labelled lower bound;
`recipient_count_exact: false` marks a bounded snapshot that crossed the cap. It counts pending and
leased deliveries and queued quiet notices for collection, recipient and
producer/recipient forecasts. It never returns another recipient's identity or
queue count. `would_refuse` is a list of dimensions, not admission, a reservation,
a backoff promise or evidence that a subsequent publication will succeed.
`unsupported` lists the unfinished closure, rate and alternate-writer checks.
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
| Closure reserved | `null` with `closure_state: unsupported` until a reservation store exists |

Do not sum every row: stopped deliveries overlap inbox counts, while future
schedules and waits have different capacity units. Leased counts include expired
leases until settlement or retry changes the row. Oldest age uses publication
time, includes deferred work, and is clamped at zero. Existing delivery diagnosis
explains deliberate deferral and holds.

The 60-second publication count is an observed window for the effective
producer, or the collection for operator inspection. The delivery fanout of
those publications uses currently materialized delivery rows; it is not a
materialization-rate measurement or a token-bucket balance. `rate_evaluation`
therefore remains `unavailable`. No latency or efficiency improvement is claimed.

## Writer inventory

Every path below must participate before enforcement becomes available:

| Writer | Current source path and reservoir |
| --- | --- |
| Direct/topic publication | `core/agent_events.go`: event and recipient snapshot, routine deferral, pool or quiet queue |
| Scheduled firing | `core/agent_schedules.go`: pending occurrence, then `publishEventTx` using membership at firing |
| Operator reissue | `core/agent_event_reissue.go`: direct event/delivery or pool request |
| Pool assignment | `core/agent_pools.go`: unassigned request becomes one worker delivery |
| Quiet admission / continuation | `core/agent_organic_notices.go`: queued notice becomes leased delivery |
| Quiet explicit flush | `core/agent_organic_notices.go`: queued notice becomes pending delivery |
| Routine notice flush | `core/agent_notice_flush.go`: releases existing availability; creates no delivery |
| Wait resolution / timeout | `core/agent_waits.go`, `core/agent_wait_scheduler.go`: retained wait to outbox to event/delivery |
| Stopped-session notification | `core/agent_stopped_backlog.go`: deduplicated direct event/delivery; no retained pending outbox yet |
| Restore / reconciliation | `core/restore_fence.go` and restore rebuild paths: fence and reconcile existing obligations; never replay execution |

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

These checks establish this observation slice's tested behavior. They do not
establish full event-scaling acceptance, deployment readiness, or measured benefit.
