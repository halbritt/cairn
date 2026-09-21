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

### Selective Claude channel rollout

A Claude binding can set `claude_channel_dir` and an optional
`claude_channel_sessions` list of native conversation IDs. With no list, every
conversation in that binding retains the configured channel admission rules.
The selector requires an absolute, nonempty `claude_channel_dir`, including
when the list is empty. An empty list selects none. Listed conversations use
the channel and refuse
ordinary owner-prompt/Stop inbox claims even while its bridge is unavailable.
Unlisted conversations retain ordinary turn-boundary delivery and receive no
automatic channel wake. The selector changes host routing configuration, not
authorization, the binding name, Cairn agent UUID or inbox consumer.

For a staged rollout, register `cairn-events` in the account's actual global MCP
configuration, enable the channel on the selected conversation's native resume,
and list its existing native session ID in the binding. Preserve its original
account environment: default Claude uses `~/.claude.json`; an explicit
`CLAUDE_CONFIG_DIR` uses that directory's `.claude.json`. Do not substitute an
account configuration merely to activate the channel. Selection survives PID
replacement because it uses the native conversation ID; the bridge registry
still independently checks the current process identity. Do not remove a
selection to bypass an unavailable bridge or an uncertain in-flight wake.

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

Native host operations:

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
continue to apply.

Tests use disposable PostgreSQL and the real API/CLI. Native Codex and Claude
fixtures verify busy arrival, Stop continuation, source reading and explicit
completion through their installed execution tools. Native OpenCode and Hermes
fixtures verify next-turn handling. An opt-in Agy test uses its existing account
and a real model. See [verification](verification/agent-sessions-2026-09-15.md).
