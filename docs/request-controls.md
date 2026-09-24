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

## Native interactive cancellation (OpenCode trial gate)

Native cancellation remains disabled in the installed configuration and has
not passed a real-model trial. The OpenCode host adapter can opt in with the
installer's `--opencode-cancel-trial` flag (which writes
`opencode_cancel_enabled: true`) only after the request-specific native route,
tool-capture plugin, and process scanner are installed together. At admission
it probes the peer-verified bridge for both `cancel_request` and
`tool_capture`; otherwise it claims the turn without an exclusivity attestation,
so `work-cancel` returns `UNSUPPORTED_CONTROL`. Codex, Claude and Hermes do not
attest cancellable turns. A joined Claude channel prompt shares the owner
prompt ID; owner input can join an admitted Codex queue turn.

For an attested attempt, `work-cancel` records an
idempotent pending cancellation fencing complete/renew/retry with
`REQUEST_CANCELLED`. Reconciliation releases the hold only after a positive
turn stop (`interrupted`/`ended`; `ambiguous` holds), terminal captured tools
and a final clear terminal scan. Exclusive attempts use `cancel_confirmed`.
Revoked attempts require the same cleanup evidence and use
`exclusivity_revoked`, leaving cancellation confirmation unset. A clear scan is
invalidated by the cancellation decision, every later capture, a new turn-stop
observation, a real capture-loss transition and any host-observed owner join
(which also revokes the exclusivity attestation one-way). A latched
`capture_gap` preserves lost coverage.
Host operations are `session-inbox-control`, `session-tool-capture` and
`session-tool-stop`. The OpenCode watcher polls control, issues one exact native
stop outside its state lock, records the pinned `TurnEnd`, scans marked tool
processes, reports terminal captures and reconciles only after a fresh clear
scan. It never kills the shared native process. An uncertain stop is not
resent automatically. A live or unreadable marked process keeps the hold.

The OpenCode bridge exposes the native stop as `session/cancel_request`. It
takes `session_id`, `request_id` and `expected_turn_id`
from the pinned attempt. It is advertised as `cancel_request` by
`session/capabilities` only when the patched native `cancelRequest` exists.
It refuses a turn that is not the plugin's current owner turn and a missing
session before any native call. It then calls native `cancelRequest` by request
ID. It never uses a whole-session abort (`session/abort` stays refused) and
never kills a process.

`opencode_queue.cancel_request` returns three outcomes:

- **accepted**: the native runner's cancellation settled.
- **`CancelRefused`**: native code definitely did not act. Its reason is
  `unavailable`, `unsupported`, `invalid`, `session_unavailable`,
  `turn_mismatch`, `not_active` or `native_refused`.
- **`CancelUncertain`**: native code may have acted. Never automatically resend.

Acceptance is not evidence of a turn stop or tool cleanup; reconciliation still
needs those host observations.

For an admitted exclusive turn, and never for an owner turn, the plugin's
`shell.env` hook injects three variables into every spawned shell:
`CAIRN_REQUEST_ID`, `CAIRN_NATIVE_TURN_ID` and `CAIRN_TOOL_CALL_ID`.
Descendants inherit them, including processes that call setsid or
double-fork, which a process-group scan misses.

`process_scan.markers(name, value, since)` finds live same-user processes by
that marker in `/proc/*/environ`. It reports coverage as unknown, and so never
clear, when a same-user process started at or after `since` has an unreadable
environment. Zombies are excluded. A process that clears its own environment
evades the scan.

Tool capture is opt-in by the host. The plugin sends `ToolStart` and `ToolEnd`
hook events, with `tool`, `call_id` and `request_id`, only when the TurnStart
reply carries `cairn.tool_capture: true`. An exclusive turn refuses tools if
the host did not advertise capture or if `ToolStart` fails. The host currently
admits only marked `bash` tools for this trial. `ToolEnd` alone does not prove
process termination; the host scans before reporting terminal tool state.
Environment clearing can evade this scan, so this adapter does not establish
general cleanup for arbitrary tool workloads. The trial fixture preserves the
marker, and its independent `/proc` ledger checks for false release.

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
