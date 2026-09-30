# Native session inbox delivery

Native adapters deliver into an existing conversation at supported turn
boundaries. They use its Cairn UUID and current execution through the existing
profile. No session credentials are created. Migration 039 retains native
delivery attempts and idempotent polls in PostgreSQL.

## Boundaries and ownership

Enable with `scripts/install-agent-coordination.py --native-delivery` after
upgrading the API to schema 039 and the matching implementation. Each account
home keeps its own binding; agents keep their conversation UUIDs across resume.

- Codex and Claude receive context at their start/prompt hooks. A request arriving
  while busy can trigger one Stop continuation.
- Agy receives context at PreInvocation. A fully idle Stop can continue once to
  handle a queued message. It does not interrupt background work.
- OpenCode receives context in its next main request transformation. Hermes
  receives it before its next turn. Their idle/post-turn callbacks reconcile
  existing handling; they do not start another turn themselves.

The host delivers at most one inbox item during a native turn/Stop continuation.
Further messages wait for the next supported boundary. With `--idle-wakeup`
enabled at installation, the presence watcher can create that boundary by
prompting an eligible idle Herdr conversation with pending inbox work.
Installation verifies Herdr's native integration in that account home and
installs it through the installed `herdr` CLI when needed; see
[native presence adapters](agent-sessions.md). See
[the idle wakeup contract](plans/idle-session-wakeups.md) for process/session
checks, focused-pane deferral and host limitations. Codex processes with an
explicit native Unix listener use the native queue described there, preserving
the TUI draft. Claude uses its configured native channel; OpenCode and Hermes
use their native queue endpoints. These harnesses never fall back to terminal
input when a native endpoint is missing. The native hook still owns the claim.
Sessions without an enabled host capability wait for another
boundary. Native delivery never starts a fresh worker to consume a conversation's inbox.

### Waiting-work cue during long turns

A busy conversation cannot see its inbox until its turn ends, so requests could
wait silently behind one long turn. Claude and Codex bindings therefore also
install a `PostToolUse` hook that adds one short line of context when work is
waiting, for example "Cairn inbox: 1 request, 6 notices waiting for this
session", with a reminder to end the turn at a safe point rather than claim the
inbox manually.

The cue is a hint, not delivery:

- The watcher calls `session-inbox-pending` for busy existing-session
  executions on its normal 30-second cycle. The call counts what native
  delivery would hand this execution later (requests, notices, responses and
  the newest position; truncated at 100). It returns no senders, sources or
  bodies, and it claims, leases and acknowledges nothing. Idle sessions keep
  the ordinary wake path and are not counted.
- The hook reads only that conversation's local state. It makes no API call,
  takes no session lock and has a 2-second timeout. It stays silent for stale
  counts (older than 90 seconds), another execution or live process, fresh
  workers, lifecycle children and other harnesses.
- A cue repeats only when the counts or the newest position change, not after
  every tool.
- Against an API without the operation, the watcher drops the counts and the
  cue never appears; delivery is unchanged.

### Claude channel activation and selection

Claude Code admits channel notifications only from servers named on its launcher
flags (`--dangerously-load-development-channels=server:cairn-events`, or
`--channels` naming that server); registering `cairn-events` in `.claude.json`
alone is ignored. Use the `=` form in launchers and aliases: the flag accepts
several values, so with a space it also consumes a following positional prompt
as a channel entry and Claude exits with "entries must be tagged". The watcher therefore detects activation per process: it reads
the live Claude process's command line from `/proc`, rechecks its identity, and
keeps the binding's `claude_channel_dir` only for a process launched with the
configured server (`claude_channel_server`, default `cairn-events`). A process
launched without the flag retains ordinary turn-boundary delivery and receives
no channel wake, so enabling `claude_channel_dir` for an account never suppresses
delivery to conversations that cannot receive channel wakes. Detection follows
the process, so a resumed conversation is re-evaluated with its new launch.

The bridge serves only the legacy MCP handshake. Since 2026-09-25, Claude Code
(observed on 2.1.282 and 2.1.283) negotiates the sessionless revision
(2026-07-28) when a server offers it,
and then log "Channel notifications skipped: connection negotiated a modern
protocol revision with no unsolicited notification path". The bridge still
reported each wake `written`. The bridge therefore answers `server/discover`
with "method not found", as a pre-2026 server does. Claude then falls back to
`initialize`, and the bridge publishes its registry only after
`notifications/initialized`. To check a session, look in Claude's
`mcp-logs-cairn-events` log for `"protocolEra":"legacy"` and "Channel
notifications registered".

A binding may still set an optional `claude_channel_sessions` list of native
conversation IDs. With the list present, a conversation must be listed *and*
channel-enabled to use the channel; an empty list selects none. Without the
list, detection alone governs. The selector requires an absolute, nonempty
`claude_channel_dir`. Channel-eligible conversations refuse ordinary
owner-prompt/Stop inbox claims even while the bridge is unavailable; they wait
for the bridge rather than admitting work on an unrelated prompt. Neither
detection nor the list changes authorization, the binding name, the Cairn agent
UUID or the inbox consumer. Preserve the account's original configuration
environment when resuming: default Claude uses `~/.claude.json`; an explicit
`CLAUDE_CONFIG_DIR` uses that directory's `.claude.json`.

When a delivery is ready for an idle conversation but no wake transport exists,
the watcher logs one line per delivery and reason to the presence journal,
naming the agent, native conversation, process ID and cause: an unflagged Claude
launch, an unlisted conversation, a binding without `claude_channel_dir`, a
missing bridge registry, a missing Codex/OpenCode/Hermes queue endpoint, or a
prior wake for the same delivery still submitted or uncertain. The record is
kept in session state so a 30-second cycle does not repeat it; a new delivery
or changed reason logs again, and an emptied inbox clears it. Silence in the
journal previously hid an account whose activation was incomplete for days.

Migration 048 adds an optional exact delivery and native turn pair to native
claims. The explicit Codex Unix-listener route uses it: only the matching queued
wake prompt can acquire the indicated delivery. An ordinary owner prompt does
not consume pending inbox work on that route. If readiness changed before the
wake runs, the claim stays empty instead of acquiring another delivery. Retrying
that poll cannot change its delivery or turn, including after an API restart.
Another turn cannot receive or finish the retained context. The context and
operator review expose the selected `native_turn_id`; raw prompts are not stored.
Other native routes retain their existing boundary behavior. A recorded turn
ID pins delivery identity only; it does NOT establish exclusive request
ownership, and interactive cancellation remains operationally unsupported.
The dormant migration 049 contract allows cancellation solely for attempts
that attest exclusive single-request turn ownership, which no installed
adapter demonstrates. See [request controls](request-controls.md).

One unfinished native attempt owns the session inbox. A normal inbox consumer
cannot claim another item while that hold exists. Fresh-worker claims reject
existing-session inboxes. Native acquisition also waits for a live manual lease
or wake hold to finish. The registered execution cannot be replaced until its
native attempt is reconciled.

Requests, responses and notices all reach the conversation. Responses/notices
are read and explicitly acknowledged; they do not recursively launch workers
or require reply messages.

## Source and completion

The adapter writes an owner-only `cairn.session-inbox/1` context file containing
agent/execution IDs, attempt/delivery/event IDs, sender, event kind, exact source
reference, read command/input, completion arguments, response arguments and
acknowledgment arguments.
It contains token-file locations, never credential values or copied transcripts.

The agent reads the selected source using the supplied command and JSON input.
For a request, `completion` records a concise selected result and handling in one
transaction. The supplied `response` argv preserves the native UUID, a stable
publication request ID, recipient, causation and any existing correlation ID.
Append `--version RESULT_VERSION RESULT_RECORD_UUID` using completion's result.
Reply publication remains explicit.
For a response/notice, `acknowledgement` records handling without creating another
request. Completion request UUIDs remain stable after an uncertain response.
Handling is a report; neither zero exit nor acknowledgment proves task success.

The `completion`, `acknowledgement` and `response` argv run the lifecycle
helper's `inbox` command. It builds the exact Cairn command from the context's
`commands` and `request_ids`, so the agent chooses only which command to run,
its stdin, and a reply's `--version`. Before anything leaves the host, the
helper writes an owner-only intent file, `STATE_DIR/intents/ATTEMPT.json`
(mode 0600, fsynced). It holds the command, request UUID and stdin body; token
values are never stored. It then records the outcome:

- `committed`, with the response;
- `refused`, for a refusal that proves nothing committed (for example
  `HOLD_RELEASED`, `STALE_SESSION`, `REQUEST_CANCELLED`);
- `pending`, when the outcome is uncertain (transport failure, timeout or an
  unknown status).

Running the same command again with the same input returns the committed
response, or retries the same request UUID. A different body or command for
the same attempt is refused locally with `IDEMPOTENCY_CONFLICT`.

Intent files are not removed when the attempt is reconciled, so a refused
result is never silently discarded. The owner can read it there.

The watcher renews the 90-second delivery lease while its native process lives.
An API outage can expire the lease, but the durable hold prevents another
consumer from replaying work. The adapter does not interrupt the user's native
process. A stale lease refuses completion and requires review after the turn or
process ends.

## End and recovery

A turn that ends without explicit completion becomes failed. Confirmed process
exit has the same conservative outcome for unfinished work. The host does not
automatically replay an attempt that may have produced effects. Completed
deliveries retain their result when the host releases the hold.

End intent is persisted before API calls. The watcher retries it instead of
reviving a finished gateway turn when the API recovers. It stops presence before
reconciling an uncertain claim on process/session end, preventing delayed claims
from committing afterward. Empty polls are durable too: retrying a lost empty
response cannot claim a message that arrived later.

Restore invalidates old executions and leases while retaining native holds.
The base profile can reconcile an obsolete execution's own attempt after a
confirmed end. It cannot use that attempt to finish a newer execution. A new
registration resumes the conversation only after reconciliation.

### Journaled commands settle first

Before any reconciliation, and before `agent-leave`, the host replays each
`pending` intent with its identical argv and stdin. This happens at Stop or
idle, in the watcher's cycle, and in presence finish. If an outcome is still
uncertain, the host reconciles nothing and keeps the hold, the local state and
any `ending` marker. It retries on the next hook or watcher cycle. A turn that
ends during a partition is remembered (`inbox_turn_ended`). After the replay
settles, it is reconciled as `turn_ended`.

Journals outlive their attempt. Each context adds its attempt to the
conversation's persisted `inbox_journals` list before the agent can act. For
example, the watcher may commit a completion and reconcile the attempt while the
agent is still running; a reply the agent journals afterwards is still replayed
under its original execution and request UUID. It is never re-claimed or
rebuilt. Replay happens in every watcher cycle and hook, whether or not an
attempt is active. An entry leaves the list once nothing in its journal is
pending and no reply can follow. Presence finish does not leave while any
journal of the execution is uncertain.

A saved reconciliation request keeps its UUID and reason until the store
answers. Only a definitive `DELIVERY_ACTIVE` refusal, which proves nothing
committed, lets a different reason use a new UUID. A retried `turn_ended` that
had already committed therefore never turns into a `delivery_completed` that
conflicts forever.

### Late completion under the same hold

A native lease lasts 90 seconds, and a partition can outlast it. The hold does
not lapse with the lease. While the conversation's own unfinished attempt
still holds the delivery, `complete` and `ack` under its session accept the
exact lease it was claimed with, even after the lease has expired. The
following must also be true:

- the execution is current and has not left;
- the database generation is current (restore fence);
- no cancellation is pending;
- the task deadline has not passed.

The result records `late: true` in delivery JSON, `event-status` and operator
review. Renewal after expiry is still refused with `STALE_LEASE`. The watcher
marks the attempt `lease_lapsed` and stops renewing instead of failing every
cycle. Once the hold has closed without completion (`turn_ended`,
`process_exited` or an operator release), completion is refused with
`HOLD_RELEASED`. A confirmed cancellation keeps its existing `STALE_LEASE`.
Ordinary (non-native) leases keep the plain `STALE_LEASE` rule, because another
consumer may own an expired ordinary lease.

What recovery cannot do:

- It cannot recover a result that no command journaled. If the agent never
  ran completion, the ended turn fails the delivery as before.
- A reply exists only after the agent runs `response` with the completion's
  result version. If the host or agent stops after completion commits but
  before that, the requester sees `handled` with the result reference through
  `event-status`, but no reply is published.
- A host that never returns needs the operator's `native-hold-release`
  ([operator recovery](event-recovery.md)).
- Reconnection of the transport is automatic; replay of work never is.

### Native host operations

```sh
cairn agent --token-file EXISTING_TOKEN session-inbox-claim < claim.json
cairn agent --token-file EXISTING_TOKEN session-inbox-reconcile < end.json
```

`claim.json` contains `request_id` and `session: {agent_id, execution_id}`.
Use a new request UUID for each new poll and retain it for retries, including
empty polls. `end.json` adds `attempt_id` and a reason: `delivery_completed`,
`turn_ended` or `process_exited`. These are trusted host observations, not new
authentication or execution attestation. A profile can reconcile only its own
registered session/attempt association. Collection and hosted/local restrictions
continue to apply. A same-reason retry of a closed attempt returns its retained
result; a different closure reason returns `VERSION_CONFLICT`.

Tests use disposable PostgreSQL and the real API/CLI. Native Codex and Claude
fixtures verify busy arrival, Stop continuation, source reading and explicit
completion through their installed execution tools. Native OpenCode and Hermes
fixtures verify next-turn handling. An opt-in Agy test uses its existing account
and a real model. See [verification](verification/agent-sessions-2026-09-15.md).
