# Codex conversation scope

Verified 2026-09-09 UTC against native Codex CLI 0.153.4. Baseline Cairn:
`a798f17`. [Evidence manifest](codex-thread-2026-09-09.json).

## Problem and change

The previous Codex configuration required manually replacing task/run IDs and
restarting the MCP server for each task. Reusing a static example combined all
searches under its declared IDs. Two native ephemeral conversations launched
four MCP processes; none received `CODEX_THREAD_ID` or `CODEX_SESSION_ID` in its
startup environment. Process IDs and inherited variables therefore did not
establish conversation identity in that probe.

A transparent native-client probe observed `_meta.threadId` on `tools/call`.
Its hash matched the actual `thread/start` ID. This is installed-client evidence,
not a claim that generic MCP requires the field.

`cairn mcp --codex-thread` now derives search scope from that metadata: task
`codex/<threadId>`, run `<threadId>`, and the configured repository. It does not
mutate shared server configuration. Missing, malformed or oversized identifiers
refuse search before the API call. Explicit task/run mode remains available and
ignores metadata. Combining the modes is refused. OpenCode configuration retains
its explicit scope contract and rejects the Codex flag.

The API profile remains responsible for principal, role, repository authorization
and destination. Ordinary capture remains repository-wide. Pulls retain their
original receipts. Context pins remain startup declarations. No database schema,
run observation, recovery mechanism or authority grant was added.

## Verification

- Native Codex app-server started two ephemeral conversations with the same
  candidate Cairn configuration and no task/run arguments. Each conversation
  searched twice and pulled the exact existing ordinary lesson body/version
  twice. Every returned scope matched its actual native thread ID; the two
  conversations had distinct scopes.
- SDK-to-API integration against disposable PostgreSQL checked a note applicable
  to one conversation, alternating conversations in one server, hosted destination,
  conflicting retry IDs across conversations and unchanged explicit scope despite
  supplied metadata. Existing authenticated capture/edit/pull tests still passed.
- Protocol tests refused null, empty, wildcard, numeric, array, whitespace,
  control-character and oversized metadata before any API request. CLI tests
  checked mutually exclusive scope modes and OpenCode rejection.
- `make check`, `make test` and `make test-integration` passed. The last target
  exercised the database tests with the race detector in a disposable cluster.

The private probe and response hashes are retained in the manifest. Reproduction
uses the installed app-server's `initialize`, `initialized`, `thread/start`,
`mcpServerStatus/list` and `mcpServer/tool/call` methods. Configure the candidate
Cairn binary as in [the Codex example](../mcp.md#codex-example), start two ephemeral
threads without model turns, search/pull an existing shareable note, and compare
each result's scope and exact body/version with its thread and saved source.

## Interpretation and limits

This removes manual scope configuration for conversation-level retrieval on the
tested client. It groups multiple turns in the same conversation and is not an
execution-attempt identity. Host metadata is testimony, not authentication or
proof of task success. No model turn was started and no general retrieval benefit,
resume/compaction guarantee or compatibility with other Codex versions is claimed.

Keep explicit task/run mode when finer scope is required. Revisit the opt-in mode
if native metadata changes or real use needs turn-level identity. Environment
guessing and process-generated IDs were rejected because the native startup probe
did not establish their correspondence to conversations.
