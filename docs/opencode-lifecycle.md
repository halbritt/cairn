# Ambient memory in OpenCode

The optional native plugin uses Cairn's shared lifecycle engine for selective
retrieval, separate durable notes, named workstreams and unchanged-capture
suppression. Install it beside the existing native Cairn tools by staging a
lifecycle generation and publishing its OpenCode plugin rows; see
[lifecycle staging](operator/lifecycle-staging.md). Unbound OpenCode memory runs the checked Go launcher.
Bound OpenCode memory has no Go route, so a generation that selects it is
`/1` and runs the retained Python caller. The selector uses installed Claude
with its existing provider credentials. This does not switch OpenCode's task
model. Start a fresh OpenCode process after installation or update.

Installation explicitly enables ordinary hosted lifecycle reads and selected
writes. They run independently of model-selected memory tools; native tools keep
their normal permission checks. Use the same authenticated profile for lifecycle
retrieval and native pull. Repository-scoped saved notes remain shared across
agents and projects. Session labels use OpenCode's actual session ID; declared
native task/run labels are retained.

## Host boundaries

The plugin transforms task request messages, without modifying persisted user
messages or adding memory to auxiliary title requests. Each new user message
triggers selective search. A bounded set of memory blocks remains in subsequent
request copies, so tool continuations retain context without accumulating copies.
Combined memory context is at most 12,000 UTF-8 bytes per request. Replaced versions
and evicted blocks (including blocks whose message IDs are no longer in the
request) become eligible for fresh retrieval. At most 128 top-level sessions
are tracked per process; child sessions do not trigger automatic memory.

Successful read/edit/write paths and diagnostic identifiers from failed tools
supply the same short-lived hints as Claude. Before compaction, the plugin selects
a checkpoint and resets its request context. The compaction request itself does
not acquire memory; subsequent task requests can retrieve it again.

`session.idle` queues selected capture at turn completion. Disposal waits for
queued capture to finish. Each session serializes memory operations. Capture reads
up to 64 recent host messages, omits tool/reasoning/synthetic/compaction-summary
content, bounds serialized dialogue to 24,000 UTF-8 bytes, and passes it directly
over stdin without a transcript file. Hook subprocesses enforce a 150-second
(150,000 ms) timeout and a 1 MiB output buffer (`maxBuffer`). The selector and save criteria are shared with
[Claude lifecycle memory](claude-lifecycle.md). Completed selection, including
null, suppresses duplicate extraction for unchanged dialogue. A normal idle turn
can incur a separate model call; an abrupt kill can still lose a checkpoint.

Set `CAIRN_LIFECYCLE_DISABLED=1` for one launch, or create `.cairn-no-memory` to
disable reads and capture: the plugin skips invocation when `.cairn-no-memory` is
in the working directory or worktree root, while the lifecycle engine walks
ancestor directories above them. To uninstall, remove
only `plugins/cairn-lifecycle.ts` and restart OpenCode. Memory-service and selector
failures report a labelled warning; use explicit native tools when needed.

The plugin uses the task-message transformation boundary. Pinned OpenCode
1.18.21 sources document the
[task transform](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/prompt.ts),
[compaction boundary](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/compaction.ts)
and [plugin disposal](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/plugin/index.ts).
