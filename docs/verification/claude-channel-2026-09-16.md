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
