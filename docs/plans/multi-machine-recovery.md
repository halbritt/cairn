# Native completion and reconnect recovery

Status: root (agent-112) approved late completion and operator hold release,
subject to cross-model review (agent-203) and tests. Implemented on
`agent-204/native-recovery` (`48adaa0` and later), base `2dbee9a`. The current
contracts are in [native inbox](../native-inbox.md) and
[operator recovery](../event-recovery.md). Root's conditions are covered there:

- Offline presence is not proof that the host stopped.
- Release and completion are serialized, with a race test.
- The docs state which crash orderings cannot be recovered.
- Refused results stay inspectable.

As implemented, `HOLD_RELEASED` covers holds closed by `turn_ended`,
`process_exited` or `operator_released`; a confirmed cancellation keeps
`STALE_LEASE`. Released deliveries use code `operator_released`.

## Problem

A native attempt holds its delivery until the host reconciles it. The hold
survives lease expiry, so a partition cannot duplicate work. Today it can still
lose work, or leave it stuck:

1. `CompleteEvent` requires a live lease. A result produced during a partition
   longer than 90 seconds is refused with `STALE_LEASE`, even though the same
   unreleased hold still excludes every other consumer.
2. The agent runs `cairn complete` itself. If that call fails in transit, no
   host state records it. At turn end the host reconciles `turn_ended`, which
   fails the delivery and loses a result that was ready.
3. `finish_presence` calls `agent-leave` before reconciling. After leave, the
   session is stopped, so a pending completion under that execution can no
   longer be retried.
4. `release_inbox` creates a new reconciliation request ID when the reason
   changes. If an earlier uncertain `turn_ended` actually committed, a later
   `delivery_completed` gets `VERSION_CONFLICT` indefinitely.
5. A watcher that finds its lease expired raises `STALE_LEASE` from
   `event-renew` on every cycle, so the hold never reaches recovery.
6. If a remote host never returns, nothing central can close its native hold.
   `event-reissue` requires no unfinished attempt, so the delivery is held
   permanently.

## Store contract: late completion under the same hold

`complete` and `ack` (both use `CompleteEvent`) accept an expired lease only when
all of the following hold in one transaction:

- The request uses a session view (`--agent-id/--execution-id`). The normal
  session check has already verified: current execution, the execution not
  stopped, and the current database generation (restore fence).
- The delivery is still `state='leased'` with exactly the supplied `lease_id`.
- An unfinished `agent_session_attempt` exists for that delivery with the same
  `lease_id`, `agent_id` and `execution_id`, owned by the session's base profile.
- There is no pending cancellation on that attempt (`REQUEST_CANCELLED` still
  wins) and the task deadline has not passed (`DEADLINE_EXCEEDED` still wins).

On acceptance the delivery records `completed_after_lease=true` (migration 054).
It appears as `"late": true` in delivery JSON, in `event-status` and in operator
review, so the late completion is explicit, never silent. Everything else keeps
the current refusals:

| Case | Result |
| --- | --- |
| Ordinary (non-native) lease expired | `STALE_LEASE` (unchanged; another consumer may own it) |
| Attempt already reconciled (`turn_ended`, `process_exited`, operator release) | `HOLD_RELEASED`, the local result is kept for review |
| Execution replaced, stopped, or predates restore | `STALE_SESSION`, checked before cache lookup (unchanged) |
| Different lease ID | `STALE_LEASE` |
| Cancellation pending | `REQUEST_CANCELLED` |
| Token revoked | authentication failure at the API (unchanged) |

Renewal after expiry stays refused (`STALE_LEASE`). The hold, not the lease,
provides exclusivity. The watcher records `lease_lapsed` and stops renewing,
instead of raising on every cycle. A replayed committed completion still
returns its cached response. Its request digest is unchanged, so late
acceptance never changes mutation identity.

## Host contract: journal before send, flush before end

1. **Journaled context commands.** The native context's `completion`,
   `acknowledgement` and `response` argv invoke
   `coordination.py inbox --config CFG --attempt ATTEMPT {complete|ack|respond}`.
   The helper builds the exact `cairn` argv from the owner-only context file,
   so the agent does not supply arbitrary commands. Before sending, it writes an
   owner-only intent file (`state_dir/inbox/ATTEMPT.intent.json`, fsynced) with
   the kind, the stable request ID, the argv and the stdin body. A different
   body under the same request ID is refused locally. Output passes through
   unchanged. Each outcome is recorded:
   - `committed`, with the result reference;
   - `refused`, with a definitive code;
   - `pending`, when the outcome is uncertain (transport failure, timeout,
     non-JSON output).
2. **Flush before reconcile.** The Stop/idle path, `watch_inbox` and
   `finish_presence` replay a `pending` intent with its identical argv and body
   before any `session-inbox-reconcile`, and before `agent-leave`. Committed and
   refused intents are final. If the replay is still uncertain, the host
   reconciles nothing and keeps the hold, the local state, and `ending` if set.
   A later hook or watcher cycle retries. A long partition therefore delays
   reconciliation; it does not convert a ready result into failure.
3. **Response recovery.** A `pending` response publication (a stable request
   ID already in the context) is replayed the same way after its completion
   commits. `respond` requires a committed completion result reference and
   appends it as `--version`.
4. **One reconciliation intent at a time.** `release_inbox` keeps the saved
   request ID while the saved request may have committed. It replaces the saved
   request only after a definitive `DELIVERY_ACTIVE` refusal, which proves
   nothing committed. A retry never changes the reason of an intent that may
   have committed.
5. **Restart.** After a watcher or host restart, state is loaded and the
   process identity (PID, start time, boot ID, native session) is verified as
   today, then pending intents are flushed. Registration of a replacement
   execution still waits for reconciliation (`AGENT_BUSY` in the store). Restore
   fences surface as `STALE_SESSION`; the intent is then recorded as refused
   and the host reconciles `turn_ended` or `process_exited` for the obsolete
   execution, as today.

## Operator recovery for a lost host

A new operator-only command, `cairn native-hold-release`, runs on the central
host with direct database access, like `event-reissue`. It closes one
unfinished native attempt with reason `operator_released`, and fails a still
active delivery with `code=processing_failed` and the operator's control
fields. Preconditions:

- `accept_uncertain_effects=true` and a reason.
- The session is offline (presence expired) or stopped. A live session is
  refused, so the command cannot take over from a host that is still running.

After release, a late completion from the returning host gets `HOLD_RELEASED`.
The host keeps its intent file for review and does not retry. The operator may
then use `event-reissue` as today. Neither command proves anything about
external effects.

## Remote operation set needed (for agent-201's allowlist)

The host needs: `session-inbox-ready`, `session-inbox-claim`,
`session-inbox-reconcile`, `session-inbox-control` (a read of the session's own
attempt), `event-renew`, `event-complete` (complete and ack), `publish`,
`agent-register`, `agent-heartbeat`, `agent-context`, `agent-leave`,
`agent-directory` and `history`. None of this needs `session-tool-*` capture or
stop, or `register-context`. Remote cancellation stays disabled. The operator
command is not an API operation.

## Tests

- Store (disposable PostgreSQL): late completion accepted under an unreleased
  hold; refused after reconcile, after operator release, after execution
  replacement, after restore, with a wrong lease, and with cancellation
  pending. Ordinary lease behavior is unchanged. Cached replay works after
  expiry. The `late` flag appears in status and review. Operator release
  refuses live sessions.
- Host (fake `cairn` plus real-API integration): intent journaled before send;
  an uncertain completion survives turn end and is replayed before reconcile;
  `finish_presence` flushes before leave; the reason-change sequence recovers;
  a lapsed lease stops renewal without raising.
- agent-203's harness drives the real partition across completion against
  these rules.

## Out of scope

Automatic replay of work, a longer lease as the fix, remote cancellation,
worker-pool `wake-change` intents, a distributed database, repository sync,
automatic placement.
