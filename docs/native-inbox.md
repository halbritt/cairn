# Native session inbox delivery

Native adapters deliver into an existing conversation at supported turn
boundaries. They use its Cairn UUID and current execution through the existing
profile. No session credentials are created. Migration 039 retains native
delivery attempts and idempotent polls in PostgreSQL.

For the explicit same-delivery task-source and memory binding, see
[recall on an admitted inbox request](inbox-recall.md). It is disabled by default.
For optional compact notice and response handling, see [compact native inbox](compact-inbox.md).

## Boundaries and ownership

Enable with `scripts/install-agent-coordination.py --native-delivery` after
upgrading the API to schema 039 and the matching implementation. Each account
home keeps its own binding; agents keep their conversation UUIDs across resume.

- Codex and Claude receive context at their start/prompt hooks. A request arriving
  while busy can trigger one Stop continuation.
- Agy receives context at PreInvocation. A fully idle Stop can continue once to
  handle a queued message. It does not interrupt background work. A top-level
  Agy conversation is woken through its Herdr pane when idle. An Agy subagent
  runs as a further conversation inside its parent's process. It registers as
  its own session, but it runs only when its parent messages it, and Herdr
  identifies the pane only with the parent. The watcher therefore reports
  "Agy subagent conversation inside <parent>'s process" with
  `native_transport_unavailable` instead of `terminal_target_missing`, and never
  wakes it. Its deliveries wait for its next parent-initiated turn. Its own
  self-drain also needs such a turn.
- A finished Agy subagent leaves presence. The shared process would otherwise keep
  it online as an inbox target. The watcher reads Agy's own state: the parent's
  registry `brain/<parent>/.system_generated/subagents/<id>.json` must name the
  conversation, and the parent must be a registered conversation in the same
  process. The subagent is finished when that record is `SUBAGENT_STATE_KILLED`,
  or when it is `SUBAGENT_STATE_ALIVE`, Agy's last hook was a fully idle Stop, its
  `transcript.jsonl` ends with a completed `PLANNER_RESPONSE` at least one hour
  old, and no message waits in its `messages/undelivered`. A conversation with an
  inbox attempt or journal stays. The watcher then leaves through `agent-leave`
  with `ending_reason` `agy_subagent_finished` and records the evidence as
  `ending_evidence`. Running subagents are never stopped. A later
  parent-initiated turn registers the conversation again. Deliveries already
  pending for it stay pending; an operator can fail them with
  `cairn stopped-inbox-expire`.
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
session", with a reminder to end the turn at a safe point so the items can
arrive. The cue also names this session's exact self-drain command (`cairn inbox
next` with the profile token path and this session's agent and execution UUIDs):
if native delivery has not delivered for about 10 minutes, the agent may drain
its own inbox one item at a time, as described in the cairn skill's
`references/events.md`. The owner rejected the earlier rule against an agent
draining its own inbox on 2026-10-21. Claiming another session's inbox,
registering again, replaying failed or held deliveries, and starting a parallel
consumer for an attempt the hook already supplied remain forbidden. The automatic
wake prompt says the same: "Do not register again, claim another session's inbox,
or launch a replacement conversation." Every recognizer, including the memory
engine's `taskless_notification`, accepts this wording and the earlier one
("Do not register, manually claim an inbox, ..."). A wake queued before the
change therefore still binds and is still classified as a wake.

The supported wrapper is `coordination.py inbox drain --config BINDING --agent-id A
--execution-id E`; the cue names it with this hook's binding path. It refuses
(`NOT_THIS_SESSION`) unless the binding's state holds that exact live, registered
agent and execution. It also refuses (`NATIVE_ACTIVE`) while a native intent or
attempt exists. Otherwise it claims one item with `event-next` under a 900-second
manual lease. The store returns nothing while any native attempt or wake hold
exists, and native acquisition waits for a live manual lease, so the two routes
never hold one item at once. The wrapper writes an owner-only
`cairn.session-inbox/1` context with `route: drain` and prints the exact read,
completion, acknowledgement, reply and renew argv. Completion and reply go through
the same journaled `inbox` helper as native context, with stable request UUIDs.
The host does not replay drain journals: rerun the same command to retry an
uncertain outcome, and run drain again for the next item. The OpenCode coordination plugin instead prepends the same cue
to a completed native tool's output or an MCP result's text content. This enters
the next model continuation without submitting a user prompt or claiming work.

The cue is a hint, not delivery:

- At each supported tool boundary the hook makes one `session-inbox-pending`
  lookup for this busy existing-session execution, so a request that arrives
  after the watcher's last refresh shows at the next tool boundary rather than
  after the watcher's next 30-second cycle. The call counts what native
  delivery would hand this execution later (requests, notices, responses and
  the highest eligible position; counts cover the newest 100). It is the existing
  read-only,
  authenticated operation: it returns no senders, sources or bodies and claims,
  leases, acknowledges, renews, wakes, heartbeats, releases or cancels nothing,
  and the hook never replaces the session. Idle sessions keep the ordinary wake
  path and are not counted.
- The lookup is optional and bounded. It has a 0.5-second deadline inside the
  hook's own limit (2 seconds in Claude/Codex, 1 second for OpenCode's cue
  hook; OpenCode's exclusive-request tool capture keeps its existing timeout and
  runs before the cue). The hook takes no presence lock, so it never waits behind
  the watcher, and it does not write presence state. It checks the process,
  execution, account, workspace opt-outs (`.cairn-no-memory`,
  `.cairn-no-coordination`; see [privacy markers](#privacy-markers-and-hook-admission)),
  lifecycle-child and wake-context guards before
  asking, and again confirms afterwards that the execution did not change
  while the lookup ran and rechecks workspace opt-outs before emission. Fresh
  workers, idle or ending sessions, lifecycle
  children and other harnesses are never looked up.
- A failed, refused or malformed fresh lookup omits the cue and leaves tool
  output and the last valid deduplication marker unchanged. It never falls back
  to watcher counts: an older cached backlog could repeat an already-cued arrival
  after a successful lookup observed a reduction. Presence refresh is unchanged.
- A cue appears only for new arrivals: waiting work whose newest position or
  total count exceeds what the last lookup observed. An unchanged or
  shrinking backlog does not repeat, and the baseline follows a fresh lookup
  down so later arrivals compare against what was actually waiting. The API
  counts the newest 100 eligible deliveries, so a newer eligible arrival changes
  the position even with a saturated backlog. Counts describe this bounded sample,
  not the entire backlog or claim order; delivery still claims oldest first.
  `truncated` means at least 100, including exactly 100. The position is a
  current-set hint, not a durable arrival cursor: it can decrease when the newest
  item leaves, and an older item becoming eligible again need not change a
  saturated summary.
- One tool callback per conversation looks up at a time. Concurrent callbacks
  (parallel Claude/Codex tool hooks, simultaneous OpenCode completions) skip
  their lookup instead of queueing, so one arrival yields one cue; the skipped
  callbacks' own output is untouched, and the next boundary observes anything
  newer.

An arrival is cued at the next tool boundary only if that boundary's lookup
succeeds. A turn with no further tool calls gets no cue, and the cue is
consumed when emitted: if the host drops the hook output after the hook
returns, the same arrival is not cued again. OpenCode additionally allows one
in-flight cue lookup per tracked session. Unsupported result shapes, lookup
failures and disposed/deleted sessions leave tool output unchanged. MCP
cues precede the original content because OpenCode truncates it after the hook.
Updating the plugin file does not change instances already loaded into a running
OpenCode process; the updated plugin must be loaded before relying on this cue.
The cue does not clear an earlier submitted or uncertain channel wake, or prove
that waiting work was delivered or completed. Tests exercise the hook and the
plugin against disposable fixtures only; a live next-tool-call demonstration
followed by ordinary delivery in Claude, Codex and OpenCode remains separate
deployment evidence.

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

After a prior attempt settles, an exact fresh Claude channel wake can admit the
next delivery even if the prior Stop was missed or belonged to another prompt.
Active request ownership and joined-prompt rejection still apply; this does not
permit resending an unobserved submitted or uncertain wake.

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

Dedicated one-off assignments should use the
[task-scoped remember-and-publish recipe](agent-event-fabric.md#dedicated-assignment-sources-and-search-scope).
The task label limits ordinary search discovery, not exact History access. A
recipient with a different search task still reads the supplied source version;
it must not broaden or rewrite that source's scope to make it searchable. Current
privacy, repository and forgetting checks remain in force. Reusable guidance can
remain repository-scoped. This convention neither rescopes existing records nor
changes native source references or completion defaults.

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

A Claude channel wake can be claimed and then refused before its bridge grant (for example a
failed required search). Once the native client observes the hook's exit 2, it blocks that prompt;
there is no native `Stop`, and later prompts own different turns (`NATIVE_TURN_MISMATCH`). The hook settles that attempt itself as
`turn_ended`, which the store records as `processing_failed` with no automatic replay.

Under the trusted-host contract, a Claude attempt is owned by the native prompt that claimed it
(`claude-channel:<prompt_id>`), and the store's `turn_ended` means that owning turn ended without
completion. The refusing hook is that prompt's own `UserPromptSubmit`. It records settlement before
returning exit 2, so this is not an atomic transaction with the native client's observation of the
refusal. A crash in that interval can leave a context-free native prompt after the attempt is recorded
as failed. Settlement alone therefore does not prove that the model never saw the prompt. No `Stop`
is recorded or fabricated, no other turn ends the attempt, and failure never causes automatic replay.

It settles only when the claim belongs to exactly this unjoined, active refused prompt, no tool was
observed, the attempt's command journal holds nothing but the claim, and the bridge's own ledger and its
initialization marker exist and, read under the bridge's session lock, contain no grant for the attempt,
delivery or prompt. Any doubt (a grant, a missing, busy or unreadable ledger, a joined owner prompt, an
observed tool, or any other journal content) keeps the existing hold and records only
`wake_refusal_diagnostic` (phase, origin, error and cause class, identities, grant state, settlement).
When a grant is present, `grant_kind` separates inbox delivery evidence (`inbox_delivery`) from
ordinary memory context emitted into the same prompt (`ordinary_context`) and required-only wake
output (`unadmitted_wake`); every kind keeps the hold.

A bound Claude channel wake is refused before any claim when its native prompt cannot be a fresh
request boundary (CAIRN-173). The hook keeps a bounded set of prompt ids it has seen, which survives
`Stop`, so an earlier prompt id that reappears is a join, not a fresh prompt. It also reads the
bridge ledger under the bridge's session lock: any grant for that native prompt (ordinary context,
an earlier inbox delivery or required-only output), or an unreadable ledger, refuses the wake. A busy
ledger is left to the bridge's own refusal. In both cases the wake marker is cleared and the delivery
stays pending for a later idle cycle on a fresh prompt. Nothing is claimed, read, charged or deleted;
the hook records `wake_preclaim_refusal` (phase `before_claim`, reason `seen_prompt` or `ledger_grant`,
grant state and kind, delivery and prompt), which is not inbox delivery evidence.

A bound Claude channel wake can also be vetoed before `handle` runs, when the inbox activation
decision is unavailable (for example a busy bridge lock, `BlockingIOError`). Only the exact channel
rendering of this session's submitted or uncertain wake writes an owner-only
`cairn.preadmission-wake-refusal/1` receipt under `wake-refusals/`. The receipt names the wake, the
owner process and the native prompt. Nothing is claimed. The watcher's next idle cycle reconciles the
receipt and clears the retained wake marker, so a fresh wake can follow. It does this only when all of
these are true:

- the store shows the delivery `pending` with zero attempts;
- readiness still selects it;
- the bridge ledger, read under its lock, has no grant for the delivery or the prompt;
- the conversation has no inbox intent, attempt, result policy, close or active prompt.

The conversation's journal index must also hold no uncertain journal: a `pending` completion or
response slot, an unreadable journal, or a malformed index keeps the marker. Earlier attempts whose
agent never ran a journaled command (no intent file), or whose commands all settled (`committed` or
`refused`), do not. Such entries can stay indexed indefinitely while a later command or reply is still
possible, and they hold no delivery. Before 2026-10-21 any indexed entry kept the marker. A session
that once ended a turn without completion therefore never received another channel wake, however often
it went idle (agent-449, 28 deliveries). The reconciliation reads journals only. It does not replay or
remove them.

Unrelated attempts' journal entries do not establish progress on this refused attempt.
Recovery checks this attempt's own command journal; it does not discard unrelated unused entries,
pending commands, or late-response obligations. The existing journal flush still processes those
obligations independently.

### Claude daemon-managed background sessions

Claude Code's background daemon (`claude daemon run`) supervises `bg-pty-host` and
`bg-spare` processes and re-execs each conversation worker directly from
`~/.local/share/claude/versions/<version>`. Its short name is therefore a version
number, not `claude`. Hooks resolve their owner as the nearest ancestor that is
named in `process_names`, or whose executable lies in a native Claude versions
directory (`process_exe_dirs`, defaulting to `~/.local/share/claude/versions` for
Claude), and that is not a daemon, pty host or spare. A hook that finds only
supervisors refuses rather than binding the daemon (CAIRN-178). A conversation
that an earlier engine bound to its daemon is rebound to its worker on the
worker's next hook; existing holds are kept.

A daemon-managed worker claims nothing at `SessionStart`: a resumed background
session has no turn or `Stop` to own the claim. Its next real prompt is the
delivery boundary. The watcher records "daemon-managed background Claude session
has no wake transport" (`native_transport_unavailable`) instead of attempting a
wake. A start claim already stranded on the daemon can be settled with
`coordination.py recover-daemon-start-claim --config BINDING --native-session-id S
--attempt-id A`. Add `--check-only` first. The command requires all of the
following:

- the state is bound to the live daemon;
- the attempt is untouched: no native turn, tools, journal, close or active prompt;
- exactly one live worker resumes this conversation's transcript under that daemon;
- the transcript records no turn at or after the claim, only known metadata
  records after its last timestamped record.

It settles `turn_ended` (`processing_failed`, no replay) and rebinds the session to
the worker.

A refusal held that way can be settled later only from that host-written record:
`coordination.py recover-refused-wake --config BINDING --native-session-id S --attempt-id A
--native-turn-id T`. It repeats every check, decides before writing, and sends nothing to the native
session. A caller-written statement is not proof of a historical refusal.

An attempt stuck before this repair has no host record. It can be settled only from Claude Code's own
operational records, using `coordination.py recover-native-refused-wake --config BINDING --native-session-id S
--attempt-id A --enqueue-record OFF:LEN:SHA --dequeue-record OFF:LEN:SHA --refusal-record OFF:LEN:SHA`.
- **Which file it reads:** the session file is derived from the trusted binding's `config_home`, the held
  state's workspace and the session ID, never from the caller. It is opened without following symlinks,
  and only the three pinned byte ranges are read.
- **What it requires** (the shape observed on Claude Code 2.1.285; any other shape is refused):
  - one contiguous enqueue, dequeue and refusal sequence;
  - an enqueue whose content is exactly this session's channel wake for the held delivery;
  - a `system`/`informational` warning with `preventContinuation`, whose content is exactly "UserPromptSubmit
    operation blocked by hook", then this binding's hook command, then the post-claim bridge refusal message,
    then that same wake;
  - timestamps that bracket the delivery's single claim attempt.
- **How the attempt is joined:** the records do not name the native prompt UUID. So the attempt's own
  host-recorded owner is used, joined by the exact delivery, identities, command and chronology above.
- **What it still checks:** every settlement check applies (no grant, no tools, no journal progress, the
  owning prompt not active). It decides before writing.

For a Codex post-claim hook refusal, the host diagnostic alone does not establish that the native
turn ended. Use `coordination.py recover-codex-refused-wake --config BINDING --native-session-id S
--attempt-id A --start-record OFF:LEN:SHA --context-record OFF:LEN:SHA --end-record OFF:LEN:SHA`.
Add `--check-only` to validate without changing state or contacting the store. This recovery requires:

- the exact host-recorded hook refusal, matching held attempt, delivery, session and native turn;
- a live owner process holding exactly one matching rollout under its configured `sessions` directory;
- three contiguous, hash-pinned native records: a queued root `task_started`, its `turn_context`, and
  `task_complete` with no last message, bracketing the claim and refusal within the hook timeout;
- an initialized bridge ledger with no grant, no joined prompt, no observed tools and no command
  journal progress beyond the claim.

Bridges now write their empty ledger and marker under the session lock before the required lookup,
so a later refusal leaves an initialized ledger naming no grant. An attempt refused by an earlier
bridge at that lookup has neither file. This explicit recovery, and not the watcher's automatic
settlement, treats that case as absent only when the recorded diagnostic shows `recall_phase`
`required_search` with grant `not_checked`, neither file exists under the bridge's lock, and the
same directory's Codex turn ledger records the bridge's activation for this native turn without a
turn grant. One file without the other, any other phase or a missing activation record still
refuses. The result records `grant_evidence: pre_grant_refusal_without_ledger`; recovery never
creates either file.

Only those bounded native records are read; their bodies are not retained in coordination state.
Unknown record shapes, changed files or identities, and missing evidence refuse recovery before any
write. A successful recovery reconciles `turn_ended`; the delivery is failed, never acknowledged as
handled or automatically replayed. Store failure retains the ended-turn proof for the watcher's existing
reconciliation retry. This command is explicit recovery, not an automatic Codex refusal detector.
Post-claim diagnostics retain allowlisted cause codes and reasons when available, never raw error text.

A Codex queue turn can also end on a provider error after it received inbox context. For example,
`task_complete` can carry `error.codex_error_info: usage_limit_exceeded` with no last agent message.
Such a turn runs no `Stop` hook. The process stays alive and the watcher keeps renewing the lease, so
the hold never settles, and later owner turns meet `NATIVE_TURN_MISMATCH` (CAIRN-179). Settle it
explicitly with `coordination.py recover-codex-ended-turn --config BINDING --native-session-id S
--attempt-id A --start-record OFF:LEN:SHA --end-record OFF:LEN:SHA`. Add `--check-only` first; it
changes nothing. The command requires all of the following:

- the held attempt and intent name this native turn, with no close, cancellation or recorded turn end;
- a live owner holding exactly one rollout under its configured `sessions` directory;
- a forced `thread/read` reporting `systemError` or `idle`, not `active`;
- pinned records: a queue-triggered `task_started` for the turn, and a `task_complete` for it with no
  last agent message and an allowlisted-shape `codex_error_info`;
- a contiguous span between those records holding only bookkeeping and input: turn context, world
  state, user and developer messages, the user-message item and token counts. Any tool call,
  assistant output or unknown record refuses;
- no later rollout record naming the turn;
- chronology that brackets the claim.

Settlement then follows the `Stop` path: it replays the attempt's journal and reconciles
`delivery_completed` if a completion committed, otherwise `turn_ended` (`processing_failed`). The proof
is kept as `codex_ended_turn` in host state. Nothing is replayed or reissued, and a store failure
leaves `inbox_turn_ended` for the watcher's existing retry.


A provider error can also end an ordinary Codex turn and leave the thread in `systemError` with busy
presence and no held attempt. The watcher reads the latest `task_complete` of the live owner's open
rollout (bounded tail, pinned by offset, length and SHA-256) and records its decision in host state as
`codex_error_wake` (CAIRN-188):

- `persistent`: `usage_limit_exceeded`, auth, request, HTTP 4xx (including 429) and unknown kinds keep
  the suppression above;
- `transient`: `server_overloaded`, `internal_server_error`, HTTP 5xx and stream-connection kinds allow
  one normal idle wake at least 5 minutes after the error, then 10, 20 and 40 minutes after each later
  wake, capped at 60 minutes; a woken turn that fails again keeps its place in that backoff;
- `held` or `unverified`: a held attempt, queued native items, a later `task_started`, or a missing or
  unpinnable record keep the suppression. Held work is never replayed.

A later quota reset does not clear a persistent native error by itself. Pending deliveries remain in
that session's inbox and visible in `coordination-review`; the watcher neither claims nor replays them.
After provider access returns, the owner session can start a new turn or drain its own inbox using the
existing session token and UUIDs. A tool-boundary waiting-work cue requires another supported tool
callback, so the errored turn alone cannot emit one after it ends.

When a wake is allowed behind busy presence, the watcher first corrects presence to idle, because the
pinned record shows the native turn has ended. It also clears the errored turn's per-turn delivery
latch, which only `Stop` would have cleared; otherwise the woken turn's hook admits no work. It then
issues the normal wake.

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

Current Codex fixtures interpret supported tool-result wrappers through
[`native_tool_results.py`](../scripts/native_tool_results.py). Source confirmation
requires a successful structured reply with the selected record, version and
exact body. Settlement confirmation requires a successful handled reply with
the matching delivery or compact handle and attempt count. The fixtures also
read Store state independently to verify the exact delivery and its attempts.
Diagnostic classifications remain bounded metadata; they do not alone establish
successful source reading or settlement. Historical verification records retain
the checks and limitations of their original runs.

The native wake probe explicitly attaches its fixture observer to the queue
module loaded by the validated installed bundle. Both its default and
`--scripted-handled` modes require observer attachment and invocation, a queue
receipt joined to the native user-message item and the hook's Store attempt,
and successful watcher and runner exits. Scripted mode additionally requires
the exact source read and one handled settlement. A missing observer is reported
separately from a queue refusal; receipt acceptance followed by idle
auto-dispatch can still establish the join when a later explicit start reports
that the submission no longer exists. See the
[native wake verification record](verification/codex-app-server-wake-join-2026-10-25.md).

## Privacy markers and hook admission

One module, `integrations/lifecycle/hook_admission.py`, decides whether any
lifecycle hook runs: the environment guards, the account home, the event
vocabulary, the session identity shape and the privacy walk. The memory engine,
the coordination hook and the bound inbox recall binding consume that one
decision instead of keeping their own copies. The marker rule is:

- `.cairn-no-memory` or `.cairn-no-coordination` in the working directory or
  any directory above it excludes coordination (presence, inbox delivery, the
  waiting-work cue) and bound inbox recall.
- `.cairn-no-memory` in the working directory, in an explicit `project_path`
  (Hermes and OpenCode may pass one), or in any directory above either excludes
  the memory engine and bound inbox recall.
- `.cairn-no-coordination` never silences the independent memory hook, and a
  marker in an explicit `project_path` does not stop presence registration for
  the working directory.

`CAIRN_LIFECYCLE_DISABLED=1` and `CAIRN_LIFECYCLE_CHILD=1` stop every hook.
`CAIRN_COORDINATION_DISABLED=1` stops coordination and bound inbox recall only.
`CAIRN_WAKE_CONTEXT` stops coordination and bound inbox recall unless the named
wake context is accepted, in which case the fresh worker's coordination hook runs
even under the lifecycle-child guard. Hooks installed for one account home skip
when another account's merged configuration invokes them. Memory requires each
harness's own session identity shape (UUIDs for Claude and Codex, `ses_…` for
OpenCode, printable labels for Hermes); coordination accepts any printable
native conversation ID within the API byte limit.
