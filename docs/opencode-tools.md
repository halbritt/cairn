# Native OpenCode tools

The [native tool adapter](../integrations/opencode/cairn.ts) gives OpenCode
session-scoped search, body/evidence pulls and ordinary note maintenance through
the existing authenticated Cairn CLI. It uses OpenCode's `context.sessionID`;
the MCP alternative continues to require explicit task/run configuration.

Verified with OpenCode 1.18.21. Its
[custom-tool interface](https://opencode.ai/docs/custom-tools/) supplies session
context. Its pinned
[MCP adapter](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/mcp/catalog.ts#L38)
does not send that context in MCP tool-call metadata. Do not use `--codex-thread`
with OpenCode or substitute a process ID for a session ID.

## Install in a project

Build/install the current Cairn CLI and provision an ordinary API profile for the
intended repository and destination. From the target project, copy the adapter:

```sh
mkdir -p .opencode/tools
cp /absolute/path/to/cairn/integrations/opencode/cairn.ts .opencode/tools/cairn.ts
```

Create `.opencode/cairn.json` with installation-specific absolute paths:

```json
{
  "executable": "/absolute/path/to/cairn",
  "socket": "/absolute/path/to/api.sock",
  "token_file": "/absolute/path/to/hosted-agent.token",
  "repo": "/absolute/path/to/canonical/repository",
  "tokens": 32000
}
```

Keep this connection file out of Git and make it owner-only (`chmod 600
.opencode/cairn.json`). It contains the token path, never its bytes. Use the
hosted profile for a hosted model; do not substitute operator/observer credentials
or a local-destination token. The CLI verifies token custody and the API owns
principal, role, repository authorization and destination filtering.

OpenCode loads `@opencode-ai/plugin` for the TypeScript tool definition. The
verified host supplied version 1.18.21. No separate Bun executable is needed for
the tool; OpenCode runs it. On updates, copy the new adapter together with the
matching Cairn CLI. Search refuses a CLI result without structured pull arguments.

The names are `cairn_search`, `cairn_pull`, `cairn_pull_evidence`,
`cairn_remember` and `cairn_edit`. OpenCode's tool permissions apply, including
explicit requests through the native permission context. Choose permissions for
the intended task. These names differ from the `cairn_cairn_*` MCP names; an
existing MCP-only permission entry does not automatically allow native tools.

## Scope and behavior

Search uses the configured repository, task `opencode/<sessionID>` and run
`<sessionID>`. This groups a conversation, including multiple turns, and is not
an execution-attempt identity or observed outcome. Missing/invalid native session
IDs refuse search. No environment or random-ID fallback exists.

Search returns full mandatory `selected` context and an ordered index. Pass each
relevant entry's complete `pull_arguments` to `cairn_pull`. Retain those arguments
for retries. The CLI also retains its existing shell `pull_command`; the native
adapter omits that redundant command from the tool result. Its
`cairn.opencode-search/1` presentation preserves the underlying `source_schema`
and `source_seal` and is not itself a sealed package.

Capture saves selected reusable knowledge as repository-wide A testimony.
`kind` defaults to `note`; omitted `shareable` keeps the note local. Writes return
only IDs, version and retry ID. Edit takes the same full replacement draft as the
[MCP edit tool](mcp.md#tools): preserve scope, sensitivity, applicability,
relations and attribution. Stale versions require a fresh pull and reconciliation.
Neither capture nor edit grants authority or establishes task success.

Connection settings are read for each call. Optional `context` keys are
`revision`, `workspace_sha256`, `task_class`, `binding` and `capability`; they map
to the existing CLI search flags. These are host declarations, not attestations
of the physical checkout. Pulls continue to use their original receipts.

`tokens` defaults to 32,000, with the same 256–1,000,000 range as MCP. The adapter
uses the conservative UTF-8 byte bound and refuses oversized results rather than
returning a partial body. A refused result can follow a committed write or pull;
reuse the original request ID. OpenCode applies its own result truncation and
task budgets separately. Each CLI child has a 30-second timeout and receives the
native cancellation signal. API errors remain errors; connection failures do not
become empty search results.

## Verify

The opt-in native checks use a disposable PostgreSQL database and actual OpenCode
custom-tool execution, with a model catalog fixture that has no usable endpoint:

```sh
CAIRN_OPENCODE_TOOLS_BINARY=/absolute/path/to/opencode make test-integration
```

This variable enables no-model custom-tool checks. It is separate from the older
`CAIRN_OPENCODE_BINARY` model probe. Tests cover session scope, default local
capture, exact body/evidence pulls, retries, edits, stale handles, hosted filtering
and permission/repository refusals. See the
[verification record](verification/opencode-tools-2026-09-09.md) for evidence and
remaining limits. Remove the installed tool file to undo this integration; keep
ordinary memory records and the existing MCP/CLI alternatives.
