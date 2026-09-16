# Claude native channel observations — September 16

These are acceptance probes of Claude Code 2.1.273 in an owned test conversation.
The production Cairn runtime remains `6fdffe0`; this report does not establish
deployed channel delivery or interactive cancellation.

## Native transport and draft

The new Go MCP bridge was built from the delegated implementation in
`/tmp/cairn-claude-channel-build`. Only the owned idle Claude test process was
restarted, resuming the same native conversation with the bridge configured as
`cairn-events` and the explicit development-channel launcher flag.

The native MCP handshake completed and the bridge registered the actual Claude
parent process. A notification received a transport `written` response. Claude
then produced the requested bounded chat reply while the pre-existing unsubmitted
draft remained in the composer. The response and draft were observed separately
from the transport result.

## Busy messages share a prompt

A first notification requested one bounded `sleep 15` tool call. After observing
its native `PreToolUse` hook, the probe waited two seconds and sent a second
notification requesting a chat reply. The native tool completed before the second
notification was admitted. Claude replied to both messages and the draft remained
unchanged.

The selected hook sequence was:

1. First `UserPromptSubmit`.
2. `PreToolUse`, display output, then `PostToolUse` for the bounded command.
3. Second `UserPromptSubmit`.
4. Display output and one `Stop`.

Both submission hooks and the Stop carried the same `prompt_id`. Thus a channel
message can join an active prompt after a tool boundary. The identifier does not
provide exclusive ownership of one notification or one Cairn request. A watcher
observing idle before submission cannot rule out this race by itself.

A second probe started the bounded tool from an ordinary owner prompt, then sent
a channel notification during the tool call. The first submission hook labelled
the prompt as ordinary input; the second labelled it as channel input. Both used
the same `prompt_id`, followed by one Stop and replies to both messages. A new
unsubmitted test draft remained unchanged. This directly establishes that the
shared prompt can include unrelated owner work, rather than only two channel
notifications. Selected evidence is `owner-busy-verification.json` in the same
local probe directory.

The adapter needs a consumption-time admission contract that prevents a wake from
claiming work inside an unrelated active prompt, with explicit handling of a
refused notification. Per-request cancellation also needs exclusive ownership
and confirmed tool cleanup. These remain implementation and verification work.

## Bridge review finding

The bridge's separate live-registry check and atomic rename allow concurrent
writers to replace each other. A review test using live owned child-process
identities reproduced two successful writers for one parent. Atomic file
replacement alone does not establish single-bridge ownership. An initial repair
used a temporary lock file whose age was treated as evidence of abandonment. A
second regression showed that this displaced a still-active publisher.

Publication now uses a per-parent OS file lock and atomic create-if-absent link.
The lock inode remains in place; the kernel releases ownership when its descriptor
closes or the process exits. Tests cover simultaneous publishers, an aged lock
with a live owner, and acquisition after terminating an owned child process.
The Go package passes its race-enabled tests, the CLI tests and `make check`.
This fixes registry publication; the adapter admission and cancellation work
above still prevents deployment of the complete feature.

Selected local evidence is in `/tmp/cairn-claude-go-probe/verification.json` and
`busy-requests.json`, with hook metadata under
`/tmp/cairn-claude-channel-probe/hook-events.jsonl`. The registry regression uses
`/tmp/cairn-channel-review-overlay.json`; its temporary child processes were
cleaned up. No raw model transcript or generated binary is committed.

## Native rejection of a joined channel message

A separate owned Claude Code 2.1.273 session tested `UserPromptSubmit` exit code
2. Its observer remembered the active prompt ID and rejected a channel submission
only when that ID was already active. The observer recorded event metadata and
prompt hashes, without storing prompt or tool-input bodies.

The owner prompt requested a bounded `sleep 20`, a second tool call writing a
test marker, and a final reply. A channel notification was written about one
second after the first tool's `PreToolUse`. The observed sequence was:

1. The owner tool finished normally.
2. The channel's submission hook ran with the owner's existing prompt ID and
   exited 2. Claude displayed the hook rejection for that channel message.
3. The owner's second tool call wrote its marker, and the owner received the
   requested final reply. Both tool calls and the final Stop used the original
   prompt ID.
4. The unsubmitted test draft remained in the composer. The channel's requested
   reply was not produced as an assistant reply.

This establishes that rejecting the joined channel did not erase or stop the
active owner task in this native trial. The adapter can now implement a durable
refusal followed by a later idle retry, instead of relying on instructions in a
notice that reaches the model. Integration still needs to prove the actual
Cairn delivery stays pending and is claimed once on a fresh prompt. This trial
does not establish cancellation, the inverse owner-input ordering, or deployment.

Selected evidence and the probe hook are under
`/tmp/cairn-claude-join-rejection-probe/verification.json` and `observe.py`.
The isolated bridge binary had SHA-256
`5826e9e6792ac146cd7d35c1f19fb65320879eab88e0af090609cb36212ea8f0`;
this was a native protocol probe, not a rollout of the current registry repair.
The earlier owned conversation and its draft were untouched.

## Owner input can join a channel-started prompt

The inverse ordering was also observed in the isolated session. After explicit
owner authorization for a bounded channel test, a fresh channel prompt started a
`sleep 20` tool call. An ordinary owner prompt was submitted while that tool was
running. Its `UserPromptSubmit` used the same prompt ID as the channel's earlier
submission. The channel's remaining tool call and the separate owner tool call
both completed, followed by their replies and one Stop. A new unsubmitted draft
remained intact.

Consequently, first-sighting ownership of a channel prompt cannot establish
permanent request exclusivity. Rejecting a channel that joins owner work solves
one admission case; it does not stop later owner input from joining channel work.
Cancellation needs a native request boundary that remains valid through later
input. Interrupting this shared prompt would also affect owner work. The selected
event sequence is `inverse-verification.json` in the same local probe directory.

## Integrated rejection and idle retry

The current Python coordinator and Go channel bridge were exercised with the
deployed Cairn API in a separate owned Claude conversation. A real pending
request was prepared while idle. Before submitting its channel notification,
the probe started an owner prompt, then submitted the delayed notification.
This deliberately exercised the interval between checking for idle and writing
the notification.

The joined notification used the owner's prompt ID and was rejected by the
native hook. The owner completed a bounded sleep, a second marker-writing tool,
and its final reply. The request still had **zero claims** after that Stop, and
the unsubmitted composer draft remained visible.

One subsequent idle watcher cycle submitted the same pending delivery. Claude
admitted it under a fresh prompt ID and explicitly completed it. Operator review
showed **one claim, handled, native attempt finished, and no remaining hold**.
The composer draft was still intact. Selected metadata is retained locally in
`/tmp/cairn-claude-live/parent-race-verification.json`.

A related regression now keeps channel admission in force when the configured
bridge is temporarily unavailable: neither an ordinary owner prompt nor its
Stop can claim work through that fallback. Forty targeted wakeup/channel tests
passed. This trial establishes the rejection-and-retry path for the tested
Claude account; it does not establish shared-prompt cancellation, all-account
rollout, or deployment of these adapter changes.
