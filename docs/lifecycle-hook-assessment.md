# Codex and Agy lifecycle hook assessment — 2026-09-13

Both installed harnesses expose useful lifecycle hooks. Porting Cairn is feasible,
but the Claude adapter is not directly compatible. This assessment completes the
requested investigation after the OpenCode integration; neither harness received
new Cairn hooks in this change.

## Codex

Installed `codex-cli 0.154.0` reports `hooks` as stable and enabled. The existing
`~/.codex/hooks.json` already contains a separate SessionStart hook. These are
local installation observations, not evidence that a new Cairn adapter has run.

The official contract supports SessionStart, UserPromptSubmit, PreCompact,
PostCompact, Stop and SessionEnd, with transcript paths and JSON event input.
SessionStart/UserPromptSubmit can return additional context. SessionEnd is
synchronous and allows at most three seconds, too short for Cairn's current
selector. A port should capture at Stop and PreCompact and reserve SessionEnd
for already-prepared work. It must normalize Codex transcript records and tool
names, preserve existing hooks and validate context delivery in a native fixture.
See the [official hook reference](https://learn.chatgpt.com/docs/hooks).

## Agy

Installed `agy 1.2.2` exposes plugin management; the existing shared Antigravity
configuration already has a separate PreInvocation hook. The CLI documentation
confirms hook configuration through plugins/settings and inspection with `/hooks`.
See [CLI plugins and skills](https://antigravity.google/docs/cli/plugins/).

The shared harness documents PreInvocation, PostInvocation, PostToolUse and Stop.
PreInvocation can inject an ephemeral message. Input uses camelCase fields such
as conversationId, workspacePaths and transcriptPath. Stop supplies fullyIdle,
which can distinguish completed background work. No pre-compaction event is
listed in this contract. A port should deduplicate PreInvocation retrieval and
capture at fully idle Stop, then verify the installed CLI's transcript shape and
hook timing. Do not assume Claude event names or output fields apply. See the
[Antigravity hook contract](https://antigravity.google/docs/hooks/).

These findings establish documented integration routes and installed surfaces.
They do not establish native Cairn injection/capture, compaction coverage or task
benefit in Codex or Agy. Their current Cairn tools and proactive skill remain the
available memory path.
