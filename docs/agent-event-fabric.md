# Agent event fabric v1

Status: selected for implementation, 2026-09-15. The owner authorized finishing
the specification, choosing the implementation, and delivering the functionality.
This supersedes the transport recommendation and underspecified contracts in
`/tmp/cairn-agent-event-fabric-spec-v0.md`.

## Purpose and boundary

Agents publish small durable notifications pointing at selected Cairn record
versions. Direct inboxes and topic subscriptions support agents that poll later.
Cairn retains communication history, recipient deliveries and reported handling.
The first transport is PostgreSQL-backed polling. No broker is required; losing
an API process or client connection does not lose pending work. A future broker
is a replaceable notification accelerator and cannot own unique delivery state.

This is an operational subsystem, separate from searchable memory notes. It does
not schedule or launch agents, promote memory, authorize actions, prove that work
succeeded, or capture conversations automatically. A request remains testimony.
An acknowledgment is a report of handling, not independent evidence of success.

## Identity, scope and references

The authenticated channel principal is the publisher and inbox owner. Destination
names are exact principal strings; `--agent` is an optional assertion of the
caller's own principal, never impersonation. Existing tokens with the same
principal share an inbox by default. Registered [sessions](agent-sessions.md)
select separate UUID inboxes through the same existing profile.
Topics use 1–128 ASCII letters, digits, dots, underscores or hyphens. Principals
use the existing channel identity contract. The session directory lists reported live context. Direct publication still
accepts an exact principal without checking its presence; publishing to a
not-yet-provisioned principal is valid and retains its delivery.

All operations name one collection repository (defaulted from the authenticated
profile). This is the collection identity, independent of the working directory.
Topics do not introduce additional project security boundaries. Existing
repository and local/shareable restrictions apply, including metadata delivery.

Every event has an immutable UUID, commit-ordered collection position, publisher,
store timestamp, kind, exact `(record_id, version)`, destination, and optional
causation/correlation UUIDs. The source version must exist and be readable by the
publisher at publication. Only active records can initiate a new event. Its
sensitivity is retained. A causal parent must be visible to the publisher in the
same collection. Correlation is a grouping label, not proof of causality.

The event identifies the version that prompted it. Consumers inspect that exact
version using existing history reads, subject to current forgetting and
destination checks. Historical text does not confer current authority. Source
and result references prevent ordinary deletion; audited forgetting can remove
payloads while retaining minimal communication references. Event tables and
cached mutation responses contain no source or result bodies.

## Publication and subscriptions

Publish requires a client-generated request UUID. The existing caller/operation/
request idempotency contract applies: a retry returns the original event; changed
intent under the same key refuses. Success means the event and all recipient
deliveries committed together. There is no second broker publication step.

A collection-level transaction lock serializes publication and subscription
changes. Positions are allocated under that lock and cannot be observed before
an earlier allocated event commits. Gaps from rollbacks are allowed. UUIDs and
timestamps are not pagination cursors.

Direct publication creates one recipient delivery. Topic publication creates one
for each active subscriber at that transaction's position. Each is independent.
Subscriptions are durable and their changes retain actor/time/history. New
subscriptions receive future events only. Unsubscribe stops future fanout and
preserves already-addressed work. Resubscribe does not replay old events.
Repeating the same active/inactive state is harmless. Subscription mutations use
request UUIDs. A caller manages only its own subscriptions.

## Delivery, leases and completion

`inbox next` atomically claims one pending or expired delivery. It returns the
event, delivery UUID, a fresh lease UUID, lease deadline and attempt count. The
default lease is five minutes; callers can choose 1–3600 seconds and renew it.
Concurrent pollers sharing a principal compete for the same deliveries. Topic
subscribers never compete with other principals. A new lease fences stale
completion attempts. No strict processing order is promised.

Losing a claim response delays that delivery until lease expiry. It does not
lose work. A consumer may receive an event more than once and must not perform
unguarded external side effects. `retry` releases a current lease for redelivery;
`renew` extends it; both require its current token. Expired tokens refuse.

`ack` records handled status with a stable request UUID. `complete` optionally
creates one ordinary Class A result in the same transaction as handling. A result
uses the normal Draft validation and store-assigned writer/witness, stays in the
same collection, and is linked to its source delivery. One delivery can complete
only once. Lost completion responses are retried with the same request UUID and
identical arguments. Different attempts cannot create duplicate results.

Consumers can instead record terminal `ignored` or `failed` dispositions with a
bounded code (`unsupported_kind`, `source_unavailable`, `processing_failed`).
Transient errors remain retryable. Unknown kinds are returned unchanged; a
consumer can handle or explicitly ignore them. The API does not silently ack.
No automatic dead-letter threshold is needed in v1; failed dispositions remain
inspectable. Unavailable source payloads do not block delivery of their minimal
already-addressed event; consumers can record `source_unavailable`.

Atomic completion protects Cairn result creation. Other Cairn effects require
their own stable operation request IDs, and external effects require an
idempotent destination. The service does not promise exactly-once arbitrary
agent behavior. Multiple result effects are outside the atomic completion API.

## Inspection and recovery

`events` returns a bounded page after a numeric collection position, with the
next position. Callers see their addressed events and their publications; a
topic filter further narrows that set. It does not give late subscribers old
messages. Status inspection shows a caller's own delivery or, for the publisher,
its recipients' reported dispositions and result references. Local content is
excluded from hosted callers. Lists return at most 100 entries.

An API restart uses existing PostgreSQL state, preserving subscriptions,
unacknowledged deliveries, lease deadlines, consumption and request retries.
Database backup/restore uses Cairn's existing lifecycle and admission fencing.
The restore fence returns leased deliveries to pending and invalidates their old
tokens. New claims serialize against that fence. No transport state outside that
backup needs reconstruction. Durability does not
exceed the database backup's recovery point. There is no automatic event pruning
in v1. Retention policy changes require a separate contract.

Counters expose committed publications, recipient deliveries, handled/ignored/
failed totals, pending and leased backlog, expired leases, redelivery attempts,
and oldest outstanding delivery age within the caller's visible scope. Publish
errors are returned directly; there is no pending broker-publication queue.

## Agent interface

Named hosted profiles live in `~/.local/share/cairn/event-profiles/NAME.token`.
Use `--profile worker-01` through `worker-07` for configured fresh-worker slots;
the recipient address is `agent/worker-01`, etc. The legacy `codex`, `opencode`,
`agy`, `claude` and `hermes` profiles remain available for manual inbox access.
The profile selects an authenticated token, not a caller-supplied identity field.
Operators can provision them with:

```sh
python3 scripts/provision-event-profiles.py --repo "$HOME/git/cairn" \
  worker-01 worker-02 worker-03 worker-04 worker-05 worker-06 worker-07
systemctl --user restart cairn-api.service
```

Provisioning preserves existing profiles and reuses existing matching event
tokens. Ordinary retrieval configuration is unchanged. A session using one named
profile shares that principal's inbox with other sessions using the same profile.

All commands use the authenticated local API and accept `--token-file` and
`--socket`. Top-level commands are convenience aliases for the event operations
under `cairn agent`; they never use the operator database channel.

```
cairn publish --request-id UUID --to PRINCIPAL --kind request --version N RECORD_UUID
cairn publish --request-id UUID --topic TOPIC --kind update --version N RECORD_UUID
cairn inbox next [--agent PRINCIPAL] [--lease-seconds N]
cairn ack --request-id UUID --lease UUID DELIVERY_UUID
cairn complete --request-id UUID --lease UUID --shareable --stdin DELIVERY_UUID
cairn retry --lease UUID DELIVERY_UUID
cairn renew --lease UUID [--lease-seconds N] DELIVERY_UUID
cairn subscribe --request-id UUID --topic TOPIC
cairn unsubscribe --request-id UUID --topic TOPIC
cairn subscriptions
cairn events [--after POSITION] [--topic TOPIC] [--limit N]
cairn event-status EVENT_UUID
cairn event-stats
```

`complete` accepts selected result text as an ordinary note; structured API calls
accept a complete Draft. `ack --disposition ignored|failed --code CODE` records
terminal failure/ignore. Read the referenced body using `cairn agent history`.
Optional kind values include update, request, response and notice. Event kind
strings are bounded to the same topic-name syntax. CLI output follows the
existing `cairn.response/1` envelope; an empty inbox returns `delivery: null`.

[Automated wakeups](agent-wakeups.md) are a separate host-side layer with a
durable hold on claimed deliveries; ordinary claims respect those holds and retry
delays. Environment-based wake context, watch, priority, TTL, wildcard
subscriptions, scheduling, response aggregation, distributed
transactions and a NATS adapter are deferred. Polling provides the complete v1
delivery contract without them. The [coordination v1 plan](plans/agent-coordination-v1.md)
assigns these follow-ups to milestones and records the live identity, metadata
and interactive-session routing requirements.

## Acceptance

Required tests cover direct/offline delivery; independent topic fanout;
subscribe/unsubscribe/resubscribe boundaries; unknown kinds; authenticated
publisher/consumer identity and destination filtering; exact source versions;
lost publish/complete responses; changed retry intent; concurrent claims;
lease expiry and stale acknowledgments; atomic result rollback and duplicate
completion; retry/renew/failure dispositions; cursor pagination under concurrent
publication; API restart; forgotten source/result handling; and durable history,
status and counters. Store changes run `make test-integration` and `make check`.
An actual CLI flow against a disposable cluster must exercise two authenticated
principals and an API restart. No production database is used for tests.

Delivery acceptance is separate from measuring whether notifications improve
real agent work. No automatic task launch or usefulness claim follows from tests.

## Watch arrivals

Use [`cairn watch`](inbox-watch.md) for read-only, resumable inbox arrivals.
Its delivery cursor includes late pool assignments; publication history positions
from `events --after` are not inbox arrival cursors.

## Review failed work

The local [operator recovery view](event-recovery.md) exposes retained attempts,
reported task assessments and traceable new requests after explicit reissue.
