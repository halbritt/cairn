# Stopped-session backlog and publisher notification

When an agent session explicitly stops (`leave`), its inbox retains any
unresolved coordination deliveries (`request`, `response`, and `notice`). Unlike
an active or transiently offline session—where ordinary notices and responses do
not trigger attention alerts—a confirmed-stopped session will not claim its
remaining inbox work unless the same binding resumes or an operator intervenes.

This contract surfaces stranded inbox work on confirmed-stopped sessions in
operator review, exposes stopped-recipient diagnosis to the original publisher
through `event-status`, and provides an opt-in, deduplicated publisher
notification during operator review without mutating the original deliveries.

## Why stopped sessions need review

A stopped session can retain notices and responses even when it has no pending
requests. Review all unresolved delivery kinds before closing a workstream.
Starting another agent on related work does not transfer the old inbox or prove
that its messages were handled. Use an explicit successor grant when a new
session must inspect predecessor events; see [successor continuity](successor-event-continuity.md).

## Presence boundaries

Store facts keep four session states strictly distinct:

- `online`: current execution, matching database generation, and unexpired
  presence lease.
- `offline`: `stopped=false`, matching generation, and expired presence lease.
  Transient offline presence never becomes a confirmed stop and does not enter
  `stopped_sessions` or trigger stopped-delivery notifications.
- `replaced`: session row belongs to an earlier database generation or an
  earlier execution of the same binding.
- `stopped`: explicit `stopped=true` recorded by `leave`.

Registering another session in the same repository, project, or workspace—or
with matching task text—does not imply a successor relationship and does not
mark prior deliveries handled. `successor_hint` is omitted unless an
independently authorized successor relation exists.

## Stopped-session backlog in `coordination-review`

When `attention` is requested on `coordination-review`, unresolved deliveries
(`pending`, `leased`, or `held`) to a confirmed-stopped session
(`diagnosis.recipient.presence == "stopped"`):

1. Form an `attention.groups` entry with `condition: "recipient_stopped"`,
   `freshness: "offline"`, and `applies: "session"`, regardless of
   `older_than_seconds` or whether the session has any pending `request`.
2. Appear in `attention.changed` when a delivery enters the stopped backlog or
   changes its stopped stage, execution, or retained wake signature, even when
   `requests == 0` (notice-only or response-only backlogs).
3. Populate `attention.stopped_sessions`, grouped by recipient inbox with:
   - `recipient`, `agent_id`, `execution_id`, and `presence: "stopped"`;
   - separate `requests`, `responses`, `notices`, `held`, and `unattempted`
     counts;
   - `oldest_waiting_seconds` and `oldest_delivery_id`;
   - `retained_wake` (`delivery_id`, `transport`, `status`, `attempted_at`,
     `freshness`, `applies`) when a delivery in the backlog carries a retained
     wake observation;
   - `admission_uncertainty` (`"unknown"` when any delivery is held, leased,
     attempted, `may_have_executed`, or carries an applicable `wake_retained` /
     `native_admission_retained` observation; `"not_admitted"` otherwise);
   - `decision_owner: "operator_or_publisher"`;
   - `complete` and `coverage` disclosing whether the review page is a full
     scan of matching deliveries or a partial cursor slice; and
   - `deliveries`, listing each unresolved delivery in position order with its
     `delivery_id`, `event_id`, `from`, `kind`, `state`, `stage`, `attempts`,
     `held`, `execution`, `admission`, `waiting_seconds`, `ref`,
     `reported_condition`, `freshness`, `applies`, and `retained_wake`.

## Publisher visibility (`event-status`)

An authenticated publisher inspecting its own event via `event-status`
(`AgentEventStatus`) receives `diagnosis` on each delivery whenever:

- the event is a `request`, or
- the delivery's recipient session is confirmed `stopped`
  (`diagnosis.recipient.presence == "stopped"`), including for `notice` and
  `response` events.

This lets a publisher see that its `notice`, `response`, or `request` remains
unresolved because the recipient session stopped, along with `stage`,
`waiting_seconds`, and any applicable host observation. Read access remains
bounded to the publisher's own event:

- Another publisher's delivery identifier on a session-scoped or
  `other_delivery` host observation is stripped (`host.delivery_id` is omitted).
- Observations on `local`-only deliveries are withheld from `hosted` readers
  (`freshness: "withheld"`).
- Ordinary `notice` and `response` deliveries to `online` or transiently
  `offline` sessions continue to omit `diagnosis` on `event-status`.

## Opt-in publisher notifications

An operator review can opt in to notifying original publishers of unresolved
deliveries to confirmed-stopped sessions:

```sh
cairn coordination-review <<'JSON'
{
  "repo": "/projects/shared-memory",
  "state": "pending",
  "limit": 100,
  "attention": {
    "older_than_seconds": 300,
    "state_file": "/absolute/private/directory/cairn-attention.json",
    "notify_publishers": true
  }
}
JSON
```

When `notify_publishers` is `true`, each unresolved delivery (`pending`,
`leased`, or `held`) to a confirmed-stopped session is evaluated under
the following invariants:

- **Stable identity and changed-condition suppression**: Each notification uses
  a deterministic request UUID (`uuid.NewSHA1` over collection, `delivery_id`,
  recipient `agent_id`, recipient `execution_id`, and the delivery's stopped
  condition signature `stage|execution|admission|reported|wake`). Increasing
  `waiting_seconds` or repeated observations do not change the signature.
  Deduplication is enforced in both the local `state_file` cache and the store's
  `cairn.mutation_request` log (`operation = 'stopped-delivery-notify'`).
- **Bounded volume**: At most 64 notifications are attempted per
  `coordination-review` call; any additional candidates on the page are counted
  in `publisher_notifications.deferred`.
- **No recursive loops or unavailable-recipient writes**:
  - Events published by the operator channel, self-addressed deliveries, and
    events whose record carries `"schema": "cairn.stopped-delivery-notice/1"`
    are skipped (`reason: "recursive_or_self"`).
  - If the original publisher is itself a session (`agent/<uuid>`) that is
    `stopped`, `offline`, `replaced`, or absent, no notice is published into
    that unavailable inbox (`status: "recipient_unavailable"`).
- **Privacy**: Each notice is addressed only to the original event's publisher,
  inherits the original event's sensitivity (`local` or `shareable`), and
  contains only facts about that delivery (`cairn.stopped-delivery-notice/1`).
  Delivery-scoped observations belonging to another delivery (`other_delivery`)
  and `local`-only observations on a `shareable` event are excluded.
- **Non-mutation of original work**: Publishing a notification never claims,
  acknowledges, fails, reissues, releases holds on, forwards, or clears the
  original delivery, and never restarts or switches the stopped session.
- **Ordinary admission, no reservation**: Each notice is published by the
  trusted operator channel principal, charging that principal's producer and
  owner rate buckets plus the collection's rate buckets, and takes no
  [closure reservation](closure-reservations.md). Under an `enforce`
  [admission policy](event-admission.md), a capacity, rate, or
  closure-reconciliation (`CLOSURE_UNRECONCILED`) refusal rolls back the whole
  notice—source record, event, delivery and retry identity—and returns
  `status: "deferred_capacity"` with `reason` set to the refused dimension (or
  `"CLOSURE_UNRECONCILED"`; a `RATE_UNRECONCILED` generation-invariant mismatch
  is not deferred and returns an error). The delivery's fingerprint is not
  marked notified, so a later `coordination-review` retries with the same
  deterministic request UUID. If retaining the refusal counter itself fails, the
  deferral still succeeds with a structured warning and the batch keeps its
  earlier results and fingerprints. The unresolved original delivery remains the
  durable record; a notice is not guaranteed to be delivered.

## Explicit expiry

Notification leaves the backlog in place. After review, an operator can fail the
backlog of sessions that stopped at least a day earlier with
`cairn stopped-inbox-expire`, which records code `consumer_retired`. See
[event recovery](event-recovery.md#expire-deliveries-stranded-on-a-stopped-session).
