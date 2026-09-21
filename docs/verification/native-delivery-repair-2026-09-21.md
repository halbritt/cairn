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
prompt. This confirms actual native queue acceptance; explicit handling remains
required before claiming the complete live route fixed.

Agent 65 remains in its original idle process pending the owner's answer to the
specific restart question. Its exact resume command, additive default-account
MCP configuration, and one-conversation selector are staged privately. The actual
default Claude global configuration is `~/.claude.json` when
`CLAUDE_CONFIG_DIR` is unset; setting that variable to `~/.claude` would change
which global configuration is loaded. The prepared restart preserves the unset
variable, account, project directory, and native conversation ID. Other Claude
processes are excluded from the selector and are not restarted.

This is a bounded delivery repair, not full coordination-v1 acceptance or native
exclusive cancellation acceptance. The delay is not yet declared repaired.
