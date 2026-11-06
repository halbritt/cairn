# Closure reservations

A closure reservation holds capacity for work that an accepted obligation still
needs in order to finish: the reply a request delivery may receive, or the
terminal notice a [coordination wait](operator/work-coordination.md#wait-for-an-exact-coordination-fact) owes its owner.
Source support starts with migration 081. Handling, retry, renewal,
cancellation and recovery are never gated by closure capacity. Closure limits
are enforced only under an `enforce` admission policy, which needs schema 084
and a completed reconciliation; see
[event pressure and admission policy](event-admission.md).

This describes source capability. It does not establish that a running API,
scheduler or native integration has been upgraded. Check the installed CLI and
API with `cairn version` and `cairn agent --token-file PROFILE version` before
relying on these operations.

## What is reserved

| Obligation | Created when | Recipient |
| --- | --- | --- |
| `request_reply` | a direct or topic request delivery is inserted | the request publisher |
| `group_reply` | a request with a response group snapshots its members | the request publisher |
| `pool_reply` | a pool request is admitted; assignment binds it to the delivery | the request publisher |
| `wait_notice` | a wait is created | the wait owner's inbox |

An operator reissue reserves a new reply for the reissued delivery; its
recipient is the reissuing operator. The original reservation is unchanged.
Notices, notes and replies create no reservation.

A reservation is consumed only by the exact reply: one published by the
original delivery's consumer, addressed to the original publisher, carrying the
request's correlation ID, and arriving before any declared group deadline or
task deadline. A group reservation is consumed only by a `response` kind, as the
response group counts it. A reply with another sender, causation, correlation
or destination, a duplicate, or a late reply is ordinary work. The `response`
kind alone earns nothing. A wait reservation is consumed when the wait outbox
publishes its notice.

A consumed reservation stays charged until the recipient settles the reply or
notice delivery.

## When a reservation is released

Completion does not release a reply obligation. A consumer that marks a request
`handled`, `ignored` or `failed` has not replied, so the reservation remains.
No expiry is invented for a request without a declared deadline.

A reserved row is released only by one of these terminals:

| Release reason | Terminal |
| --- | --- |
| `work_cancelled` | `work-cancel` of the delivery or unassigned pool request, or confirmed native cancellation |
| `request_expired` | the request's declared `task_deadline` passes, or its `admission_expires_at` fails the delivery |
| `stopped_expired` | reviewed `stopped-inbox-expire` fails the delivery |
| `operator_released` | `native-hold-release` fails an abandoned native turn's delivery |
| `group_closed` | the response group closes at its deadline |
| `restore_fenced` | a restore fence closes the group or wait |
| `wait_cancelled` | the wait is cancelled |
| `wait_restricted` | the wait outbox refuses its notice (binding, source or ceiling) |
| `publisher_closed` | the request publisher closes it explicitly |
| `operator_closed` | an operator closes it explicitly |

`request-control-sweep` applies declared task deadlines to reservations whose
delivery is already settled, at most 100 per call.

## Inspect your reservations

```sh
cairn closures --token-file PROFILE --repo COLLECTION --state reserved --limit 50
cairn closures --token-file PROFILE --repo COLLECTION --obligation request_reply --after-reservation RESERVATION_UUID
```

An agent sees only reservations whose recipient is its own inbox, and only
those its destination may read: a local-only request or wait is hidden from a
hosted profile. The consumed reply's event and delivery IDs are withheld when
that reply is too private, or when its exact source version was deleted or
forgotten; `consumed_redacted` is then `true`.

`counts` are absent, not zero, with `reason: unreconciled` until an operator has
completed reconciliation for the current restore generation. Before that,
obligations accepted before migration 081 are missing from the ledger. Agent
counts cover only the agent's own visible obligations. Pages hold 1–100
reservations; continue with `--after-reservation`. Each page reads current
state.

## Close reply obligations you no longer expect

A publisher that will not wait for a reply can release its reservation:

```sh
cairn close-replies --token-file PROFILE --repo COLLECTION \
  --request-id 6f1b8f2e-0d6b-4a8e-9f5c-2b9a7d3c4e10 \
  --reason "result obtained another way" RESERVATION_UUID:REVISION
```

Each argument names a reservation and the revision you last read; up to 100
arguments are accepted. The request is all-or-nothing and idempotent for the
same request UUID. It refuses with:

- `NOT_FOUND` when a reservation is not yours or not visible to your profile;
- `VERSION_CONFLICT` when one changed since you read it;
- `OPEN_CONFLICT` while an open response group, or a waiting `request_response`
  wait, still expects that reply. Let the group reach its deadline, or cancel
  the wait first;
- `INVALID_REQUEST` for a wait notice, which ends through wait cancellation.

A reply that arrives after an explicit close is ordinary work.

## Operator inspection, close and reconciliation

Operators use JSON commands, which have no agent API route:

```sh
printf '%s\n' '{"repo":"COLLECTION","state":"reserved","limit":50}' | cairn event-closures-collection
printf '%s\n' '{"request_id":"UUID","repo":"COLLECTION","reason":"publisher retired","items":[{"reservation_id":"UUID","expected_revision":1}]}' | cairn event-closure-close
printf '%s\n' '{"request_id":"UUID","repo":"COLLECTION","limit":500}' | cairn event-closure-reconcile
```

The collection view counts every obligation in the collection, regardless of
sensitivity, and reports `legacy_unreserved`.

`event-closure-reconcile` runs one bounded batch under the collection insertion
lock. Each step changes at most `limit` rows (default 500, at most 5000):

1. apply the release rule to reserved rows;
2. reserve open obligations accepted before migration 081 (pending or leased
   request deliveries, open response-group members without a response, open pool
   requests, and waits that still owe a notice), marked `legacy`;
3. adopt replies and wait notices that were published without consuming their
   reservation, by exact identity.

Each batch commits its own work, so a result with `more: true` has already
released, reserved and adopted the rows it reports. It does not record
completion. Run another batch with a new request UUID; completion is recorded
only by a batch in which every step stayed under its limit. Requests that a consumer settled before migration 081 without
a reply are counted in `legacy_unreserved`, not reserved, because their
publisher's intent is unknown; a late reply to one is ordinary work. A restore
fence starts a new generation, so reconciliation must be completed again.
Completion is evidence for enforcement, not enforcement itself: writer
compatibility is a separate requirement.

## Capacity and refusals

Reservations are recorded in every policy mode. They are the ledger of
accepted obligations, not a forecast: disabled and observe modes write the same
rows, never refuse, and keep no shadow counters or rate balances.

Policy limits `max_closure_reserved` (collection) and
`max_recipient_closure_reserved` (per recipient) apply only when a policy is in
`enforce` mode. `event-admission-configure` accepts `enforce` only on schema 084
with a completed reconciliation for the current restore generation, and refuses
otherwise with `FEATURE_UNAVAILABLE` or `CLOSURE_UNRECONCILED`. Under
enforcement a new obligation is refused atomically with:

- `CLOSURE_UNRECONCILED` when no completed reconciliation exists for the current
  restore generation, for example after a restore fence;
- `CLOSURE_RESERVE_FULL` when the collection or the recipient's closure
  capacity is full. The message names the dimension (`closure_collection` or
  `closure_recipient`) and the policy revision.

A matched reply is never refused for closure capacity. A scheduled occurrence
refused this way stays pending until its existing grace period ends. A wait
notice whose wait predates its reservation stays pending in the outbox with
`error_reason: closure_capacity` instead of publishing past the limit.

Stopped-delivery notices take no closure reservation; they are ordinary
notices to the original publisher. Under enforcement each one passes ordinary
admission before anything is inserted. A refusal rolls back the whole notice,
including its source record, event, delivery and retry identity, and returns
status `deferred_capacity` with the refused dimension. The delivery's
fingerprint is not marked as notified, so a later `coordination-review` retries
with the same deterministic request UUID. The unresolved original delivery
remains the durable record; a notice is not guaranteed to be delivered.

## Disposable verification

```sh
CAIRN_TEST_DATABASE_URL='host=/path/to/disposable/socket dbname=closure_tests' \
  go test ./core -run 'TestClosure' -count=1
```

Use a disposable PostgreSQL database; never an existing production store.
