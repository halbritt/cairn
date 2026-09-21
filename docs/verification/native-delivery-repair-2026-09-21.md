# Native delivery repair — September 21

The September 20 rollout removed Claude terminal wakeups before activating the
replacement channel for agent 65. Its pending messages consequently waited for
ordinary turns; publication was prompt. See the
[measured delay diagnosis](agent-delivery-delay-2026-09-21.md).

## Source repair and review

OpenCode 1.18.31 rejects a Cairn delivery UUID used as its `messageID` with HTTP
400. The plugin now omits this optional field so OpenCode allocates its own
chronological ID. Cairn retains its delivery correlation separately. A proposed
hash-derived ID was rejected during review because it breaks native chronological
ordering. Twenty queue tests pass, including refusal and uncertain-response
retention. An isolated instance of the installed native server confirms that an
omitted ID passes schema validation; its expected missing-session response is
not evidence of model execution.

Claude's optional `claude_channel_sessions` binding field selects existing native
conversations for channel activation. Selected conversations retain closed
admission when the channel is unavailable. Unselected conversations retain
ordinary boundary delivery, and omission preserves the existing global behavior.
The helper copies effective configuration rather than changing the shared binding.
Independent review caught missing-directory configuration allowing ordinary
claims; the follow-up rejects missing, empty, non-string, and relative channel directories.
The regression failed in all 18 malformed cases before the fix and passes after it.
An independent review of the final frozen revision cleared the blocker and
confirmed all 43 configuration/idle tests pass.

The initial 38 OpenCode/configuration/Claude channel tests passed; the final
configuration and idle suites pass all 43 tests including the added regression. The idle-wakeup
suite now passes all 34 tests after correcting five obsolete terminal-fallback
expectations. Positive terminal-wake coverage remains on supported Agy; Claude
and Codex without their native channel/queue explicitly produce no terminal input.
The same five obsolete tests failed on the unchanged parent revision. Resource
warnings about fixture handles remain; the tests did not skip those cases.
`make check` passed. These changes do not alter the PostgreSQL schema or core
binary; the prior disposable-cluster integration result remains scoped to that
unchanged core.

## Deployment and remaining live evidence

Source repairs were committed and pushed at `66faa97`. The coordinator installed
byte-verified Python helpers and OpenCode plugin atomically, retaining previous
files under the private release directory, then restarted the presence watcher.
The API and presence services remained healthy. Core CLI/API remain the clean
`b5aab96` build and schema 49.

Under the owner's existing approval, OpenCode agents 24 and 67 were gracefully
closed and resumed again in their existing native conversations and original
panes. Both retained their Cairn UUIDs and received new executions through normal
native hooks. Their new process IDs were 3250772 and 3250390. The original
agent-67 notice, which the old native server rejected before execution, was
claimed once through a normal turn in the resumed execution; no manual claim,
state clearing, or uncertain wake replay was used.

A new deployment-correction notice was published at 14:03:01 PDT for automatic
queue verification. The native store records a new OpenCode-allocated user message at 14:03:41.550
containing that delivery UUID, distinct from the earlier manual verification
prompt. The notice was explicitly acknowledged at 14:04:57.843 PDT after exactly one
claim. This establishes automatic queue acceptance and model-side handling in
the existing conversation. The initial 40-second wait included the preceding
verification turn; no one-hour timer or manual follow-up prompt was involved.

The owner explicitly approved agent 65's restart. Its old Claude process exited
gracefully through `/exit`; the coordinator waited for the original shell to
become foreground before resuming the exact same native conversation, account,
and project directory. New PID 3294521 retained agent UUID
`4fc393b8-424a-4b3a-80bc-134406dbfa3b` and native conversation
`1ac9ed1b-7184-4551-87bf-fd853e6e05bf`, with new execution
`38f6a82f-7a90-44f0-91f6-190f36f15f41`.

The account's actual default global configuration is `~/.claude.json` while
`CLAUDE_CONFIG_DIR` is unset. The coordinator merged only `mcpServers.cairn-events`
into a fresh read, preserved unrelated current keys and MCP servers, and kept
the variable unset. The development-channel startup confirmation applied to the
locally built Cairn server the owner had authorized activating.

The live channel registry identified bridge PID 3295251 as a child of the new
Claude process. After checking that exact process identity, the coordinator
enabled the selector for agent 65's native conversation at 14:16:05 PDT. Other
Claude conversations and the second account remain outside this activation.

SessionStart had already supplied one backlog notice at 14:15:40 before the
selector was enabled. One bounded activation prompt asked agent 65 to handle
that exact supplied context. That startup completion was excluded from automatic-delivery evidence.
Subsequent messages were claimed in native `claude-channel:` turns without
further coordinator prompts. Independent read-only store verification found
automatic handling at 14:17:58.648 and 14:18:36.838 PDT; the latter claim began
at 14:18:17.420, so recipient handling took about 19 seconds. Native turn cleanup
was confirmed at 14:18:44.880. The channel socket was mode 0600, with peer PID,
UID, process start time, and boot identity matching its registry. The second
Claude account binding remained byte-identical. No manual inbox claim, replacement conversation, or uncertain wake
replay was performed.

This is a bounded delivery repair, not full coordination-v1 acceptance or native
exclusive cancellation acceptance. The agent 65 wakeup gap is repaired and automatic backlog handling is verified.
Older messages retain their historical wait times; this does not claim that the
whole backlog has drained or guarantee a fixed future delivery latency.
