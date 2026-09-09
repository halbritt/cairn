# Claude Code configuration — 2026-09-09

Cairn now generates Claude Code's MCP JSON with `claude-config`, reusing the
existing ordinary server and shared connection/context argument assembly.
The installed Claude Code 2.1.265 accepted the generated server entry through
`mcp add-json --scope local` and reported a successful connection through
`mcp get cairn`. This was an isolated home/project with disposable Cairn data,
no provider credentials and no model inference.

The native registration check establishes configuration compatibility and
connection. An independent MCP client separately launched the generated command,
searched the fixture and pulled the exact revised note. It also verified all
five tool definitions. This is not a native Claude-selected tool call or a
completed Claude task; those parts of U8 remain open. Codex/OpenCode remain
sufficient for the higher-priority cross-harness task-value work.

## Contract and checks

- Requires explicit repository/task/run scope; rejects `--codex-thread`.
- Resolves executable/socket/token paths without reading credentials or
  connecting during generation. Existing budget and context flags are forwarded.
- Encodes paths and arguments as JSON. Invalid UTF-8 and `${` interpolation
  syntax refuse before output, preventing Claude from reinterpreting literals.
- Emits server configuration without permissions, trust settings or file edits.
  Encoder failures propagate. The MCP/API owns authentication and filtering.
- Go tests cover the emitted shape, path/argument fidelity, invalid invocation
  with no output, and writer failure. The initial expected argument list assumed
  the original flag order; it was corrected to the existing shared canonical
  order without changing the command builder.
- `make test check build` passed Go tests, 30 Python tests, vet, formatting and
  build. `CAIRN_CLAUDE_BINARY=/home/halbritt/.local/bin/claude make test-integration`
  passed disposable PostgreSQL/race, CLI/API/MCP checks and native Claude
  registration/connection. Final static/build checks passed after the help edit.

The old installed binary's `claude-config` refusal documents the absent command,
not a defect baseline or task-value comparison. No store, migration, recovery,
permission or accepted outcome semantics changed. Owner Claude configuration
was not installed or edited by these checks.

The [setup guide](../claude-code.md) includes the invocation and its limits.
[Claude's official MCP reference](https://code.claude.com/docs/en/mcp) and the
installed CLI help supplied the configuration contract. Reliable per-call native
session metadata has not been established, so explicit grouping remains required.

The doctrine review used packet `pkt-e955701fa38db8bc`, one typed-evidence pass,
a schema-valid decision receipt and a closed citation trace. Nine nonmaterial
obligations remain, covering unchanged interfaces, unclaimed cost/recurrence
measurements, additive-command baseline and separate refactoring procedures.
[Metadata](claude-config-2026-09-09.json) retains verification artifact hashes.
