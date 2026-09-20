# Request expiry, deadlines and cancellation

Migration 046 adds three distinct controls to the existing event and supervisor
path. UUIDs still identify sessions and inboxes on the trusted host; these
controls introduce no session credentials.

| Control | Meaning | Supported destinations |
| --- | --- | --- |
| `admission_expires_at` | Do not admit work at or after this instant. Work already running may finish. | Request events to agents, topics and pools. |
| `task_deadline` | Fence completion and stop the managed process group when the deadline is reached. | Configured fresh-worker slots and pools only. |
| Operator cancellation | Record cancellation against completion, then stop managed work before releasing its hold. | Queued requests and managed wake attempts. |

## Publish with absolute times

`cairn publish` accepts `--admission-expires-at RFC3339` and
`--task-deadline RFC3339`. The JSON publication fields use the same names.
Supply an explicit UTC offset, such as `2026-09-17T15:00:00Z`. Only `request`
events accept these fields. Times remain absolute across retries and scheduled
publication; a schedule does not shift them relative to its firing time.
Past times are accepted as retained intent that cannot start.

Admission is the lease claim for manual/native consumers and the `enter`
transition for fresh workers. Expiry between wake claim and `enter` prevents
useful work. After `enter`, admission expiry alone does not stop the worker.
A manual lease that expires without completion can no longer admit work after
the admission limit. Expiry never deletes history.

A hard task deadline is refused with `UNSUPPORTED_CONTROL` for topics,
interactive session UUIDs and unmanaged inboxes. Their hosts do not have a
per-request process group they can safely terminate. Configuring a worker slot
permits deadline publication; it does not prove the slot is online. Manual and
native inbox polling cannot claim deadline-bearing work.

## Cancel accepted work

The existing local operator runs `cairn work-cancel` with JSON on stdin:

```json
{
  "request_id": "d761690f-8ba1-43bb-a3f4-7aa2da9d07a6",
  "repo": "/home/halbritt/git/cairn",
  "delivery_id": "bf97aab7-a199-42fd-b5b2-9732f080e971",
  "reason": "The requested work is no longer needed"
}
```

Replace example UUIDs with a new request UUID and the actual delivery ID. For a
pool request, use `pool_event_id` instead of `delivery_id`; cancellation resolves
assignment under the same collection lock as dispatch. Choose exactly one.
Topic cancellation selects one delivery, not every subscriber.

Retry an uncertain response with the identical request UUID and JSON. The cached
response describes the original decision, including whether its wake was held
then. Use `coordination-review` for current state. A new cancellation of terminal
work returns `VERSION_CONFLICT`. If completion wins the delivery-row lock, its
result remains handled; if cancellation wins, late completion gets `STALE_LEASE`.

Running native work and live manual leases return `UNSUPPORTED_CONTROL`, retaining
their work and holds. Ending a whole interactive CLI or shared Hermes gateway is
not a per-request stop contract. Reconcile the actual turn/process end before
recovery. `schedule-cancel` remains the separate operation for unpublished intent.

## Stop decisions and process cleanup

The database records `admission_expired`, `task_deadline` or `operator_cancelled`
as a failed delivery with control time and store-owned caller attribution.
Operator-selected reasons are available only in local control/review output;
ordinary agent status omits the reason. An unassigned pool request records the
same decision without creating a delivery and no longer consumes pending capacity.

The supervisor polls `wake-control` approximately once per second. A failed
control read stops the worker through the existing cleanup path. Worker context
and systemd `RuntimeMaxSec` also enforce a task deadline; the latter survives
supervisor death. These are bounded shutdown mechanisms, not a real-time promise:
polling, host scheduling and the unit's five-second stop grace can delay complete
termination. Context cancellation alone cannot prove descendants have stopped.

Cancellation fences completion immediately but retains the wake hold until the
supervisor observes an empty process group and records `finish`. A dead supervisor
can leave a stopped unit with a retained hold; restart reconciles it. Uncertain
execution is never automatically replayed. Deadlines also fence renew/completion
at database time with `DEADLINE_EXCEEDED` before a stop decision is persisted.
A result committed before a deadline remains handled; remaining processes must
still stop. A completion decision checks time under the delivery lock, not at
an external side effect or a later wall-clock observation.

A deadline-expired unit can disappear between the supervisor's status check and
its stop command. Even when that command fails, the supervisor checks the final
unit and cgroup state before deciding whether cleanup finished. A still-running
unit or an unreadable final state retains the hold.

## Native interactive cancellation (core contract)

`work-cancel` on a delivery held by a native session attempt refuses with
`UNSUPPORTED_CONTROL` unless that attempt explicitly attested exclusive
single-request ownership of one pinned native turn (`turn_exclusive` with a
`native_turn_id`, migration 049). No installed adapter attests exclusivity
today: a joined Claude channel prompt shares the owner prompt id, and owner
input can join an admitted Codex queue turn. Pinned-but-unproven 048-era
bindings keep their delivery semantics and stay non-cancellable.

For an attested attempt, `work-cancel` records a durable pending cancellation
and returns `native.state="cancel_pending"` with the exact agent, execution,
attempt and pinned turn. The fence applies immediately: `complete`,
`event-renew` and `event-retry` fail with `REQUEST_CANCELLED` until
reconciliation. While pending, only `cancel_confirmed` reconciliation can
release the hold, and only after a host reported a positive turn stop
(`interrupted`/`ended`; `ambiguous` holds), every durably captured tool
process terminal (`unavailable` means verified absent by a terminal scan),
and a final schema-valid terminal scan observed the thread clear. A latched
`capture_gap` records lost capture coverage for review and can never be
erased by a later complete report. Host operations: `session-inbox-control`,
`session-tool-capture`, `session-tool-stop`. Tool ownership is durable
because historical item reads omit running tools; the adapter-side capture
listener and native stop executor are a separate integration concern and are
not part of this core change. Killing a shared gateway or interactive
process is never part of this contract.

## Sweep, review and recovery

`cairn request-control-sweep` accepts `{"repo":"/home/halbritt/git/cairn"}`.
One transaction processes at most 100 unassigned pool requests, 100 deliveries
and 100 due [response groups](response-groups.md).
The scheduler service runs this sweep before each scheduling tick. Claim paths
also refuse overdue work, so a stopped scheduler does not permit late admission.
Terminal bookkeeping can lag while the scheduler is stopped or backlogged.

`coordination-review` includes controlled deliveries with their hold and selected
reason, and `closed_pool_requests` for work closed before assignment. The latter
has an independent position cursor and accepts `closed_after` and `closed_limit`
(1–100, default 50). `queued_requests` contains only open unassigned work.
Public `event-status` retains the pool failure or assigned delivery state.

After holds finish, existing `event-reissue` can create an explicitly reviewed
successor to a failed delivery. It does not copy old admission or deadline times.
An unassigned closed pool request has no delivery to reissue; publishing new
intent requires a new request UUID. Neither path erases the old failure.

Stop the scheduler, supervisors, presence and affected native services before
migration. Back up the store, apply 046 with the matching binary and restart API
before consumers. Backup/restore retains these fields with their event/delivery
history; existing restore fencing still governs executions. Do not downgrade to
a binary that ignores stop decisions or deadlines.
