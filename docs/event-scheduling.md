# One-shot event scheduling

Migration 045 adds durable one-shot schedules. The existing local operator can
schedule publication of an exact source version to an agent UUID, explicit topic,
or fresh-work pool. The scheduler uses PostgreSQL time and ordinary event fanout.
A scheduled request can wake a configured worker; a notice or response does not
launch one. Existing-session delivery still follows supported native boundaries.

## Commands and input

These commands use the local operator store channel and accept JSON on stdin:

| Command | Required fields | Optional fields |
| --- | --- | --- |
| `event-schedule` | `request_id`, `not_before`, `grace_seconds`, `publication` | None |
| `schedule-list` | `repo` | `occurrence_id`, `state`, `after`, `limit` |
| `schedule-cancel` | `request_id`, `repo`, `occurrence_id` | None |
| `schedule-tick` | `repo` | `limit` |

`publication` is the ordinary publication object: `repo`, `kind`, exact
`ref: {record_id, version}`, and `destination: {type, name}`. Pool destinations
also require `pool` selectors. Optional causal/correlation references follow the
existing [event contract](agent-event-fabric.md). Omit its nested `request_id`.
Live `resolution` objects are refused: their freshness cannot be promised for a
future instant. Resolve at execution time as a separate task when that is needed.

Use a new UUID for the outer `request_id`, and reuse the identical request after
an uncertain response. `not_before` is an RFC 3339 timestamp with an explicit UTC
offset; `grace_seconds` must be 1–604800. Both are required. UTC normalization
preserves the instant, including the two distinct offset-qualified instants in
a daylight-saving fall-back hour. There is no local-time recurrence parser.

The response contains a server-allocated `occurrence_id`, publication metadata,
due time, grace and state. No source body is copied into the schedule.
Creation validates the source and publication shape. Pool capacity and current
source eligibility are checked when firing; schedule acceptance does not reserve
pool capacity. At most 1000 pending schedules per operator and collection are
accepted. Lists and ticks default to 50 and are bounded to 100.

`schedule-list` reads current state; its UUID cursor is an exclusive ordering
cursor, not an arrival stream. Lists, cancellation and ticks select only schedules
created under the same operator principal. Cached creation/cancellation responses
describe the original mutation; use the list to reconcile subsequent transitions.
There is no hosted-agent scheduling endpoint or new session credential.

## Firing and failure

The occurrence UUID is also the eventual event UUID. A transaction holds the
collection insertion lock, locks due schedules and commits event creation,
fanout/pool queue, and terminal occurrence state together. Concurrent ticks and
lost-response retries cannot create a second event for that retained occurrence.
Topic recipients are the subscriptions active at firing. The publisher is the
current store-owned operator channel, which must match the schedule creator.

| State / code | Meaning |
| --- | --- |
| `pending` | Waiting for due time or pool admission. No event exists yet. |
| `pending` / `POOL_FULL` or `POOL_PAUSED` | Last attempt could not enter the pool; later ticks may retry within grace. |
| `fired` | Event and deliveries or pool queue committed; `event_id` equals `occurrence_id`. |
| `skipped` / `misfire` | Database time exceeded due time plus grace. No catch-up event. |
| `skipped` / `NOT_FOUND` or `DESTINATION_PROHIBITED` | Current source, causal parent or destination could not be used. |
| `skipped` / `restore_fenced` | Restore may have lost later execution history; review before creating new intent. |
| `cancelled` / `operator_cancelled` | Cancellation committed before firing. |

Pool-blocked occurrences rotate behind unchecked due work, preserving accepted
schedules without starving unrelated due publications. Unexpected database errors
roll back the entire bounded tick and propagate. The service retries after exit;
it does not classify infrastructure failure as a skipped occurrence.

Cancellation and firing share the collection lock and have one durable winner.
Cancelling an already terminal occurrence returns `VERSION_CONFLICT`. Cancellation
here applies only to unpublished intent; it does not stop an already-fired task.
Grace limits late publication, not later worker admission or running time.
Admission TTL, task deadlines and running-work cancellation remain separate work.

## Service and restore

`cairn schedule-serve --repo COLLECTION` ticks up to 100 due occurrences once per
second, with a 20-second transaction-call budget. The service reports readiness
after its first successful tick and logs occurrence UUID/state/code only.
The [user unit](../integrations/systemd/cairn-scheduler.service) uses the owner's
canonical shared collection and requires the existing local store service.
It uses the same OS-user operator identity as local commands.

Stop the scheduler alongside dispatch before migration or restore. Back up the
store, migrate with the matching binary, then start the store/API and scheduler.
Restore fencing atomically marks all retained pending schedules `restore_fenced`;
a backup cannot establish whether they fired after it was taken. Fired history
and occurrence IDs in that backup remain intact. Review uncertain external
effects before explicitly scheduling replacements. This is not an exactly-once
guarantee for external effects or work lost from a backup.

See [verification](verification/event-scheduling-2026-09-16.md) for tested claims
and installed revision. Recurrence requires a separate timezone, DST and misfire
contract before it can be enabled.
