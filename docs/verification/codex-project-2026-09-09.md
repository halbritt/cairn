# Local Codex adoption

Verified 2026-09-09 UTC with Codex CLI 0.153.4 and Cairn `be5f945`.

Previous native tests supplied MCP server configuration directly to ephemeral
threads. The owner's trusted Cairn project had no `.codex/config.toml`, and its
user configuration had no Cairn entry. The working native interface therefore
was not configured for ordinary project startup.

The host-local `.codex/config.toml` now launches the installed Cairn executable
with the existing ordinary hosted profile, canonical repository and
`--codex-thread`. It enables search, body pull, evidence pull, ordinary capture
and edit. Memory is optional at startup (`required = false`), consistent with
the repository's existing rule that an unavailable service does not block work.
The file contains installation paths, not token bytes, is owner-only, and is
excluded through the local Git exclude file. The user's global config is unchanged.

This uses Codex's documented
[trusted-project MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The repository instructions now prefer native Cairn retrieval when available,
with the existing scoped CLI as fallback. Configuration installation is local
to this checkout, not automatically distributed to clones or other repositories.

## Observed behavior

`codex mcp get cairn --json` resolved the intended executable, arguments and five
tools. A separate native app-server check then started two fresh ephemeral
conversations in the actual Cairn repository, with no MCP overrides in either
process arguments or thread configuration. Both discovered all five tools.
Each searched and pulled the exact current saved Codex procedure twice; each
search scope matched its actual native thread ID. All probe processes exited.

The [evidence manifest](codex-project-2026-09-09.json) retains hashes of the
local configuration, probe, resolved configuration and native report. It also
records matching before/after hashes of the user configuration. No model turn
was started. Discovery and direct native calls prove local adoption and working
retrieval, not that a model will choose the tools or that task outcomes improve.

For another installation, adapt the [Codex example](../mcp.md#codex-example) in
that trusted project's `.codex/config.toml`. Verify resolved configuration and
actual tool contact separately. Remove the local entry to undo adoption; no
database state or credentials need to be changed. Existing conversations may
need a client restart before the tools become available.
