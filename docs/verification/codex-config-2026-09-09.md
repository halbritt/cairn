# Codex configuration generation — 2026-09-09

`cairn codex-config` now prints the MCP TOML entry for an ordinary provisioned
agent profile. The installed Codex CLI 0.153.4 loaded its output from files,
discovered all five tools and retrieved the exact saved procedure in three fresh
conversations. Two conversations used automatic conversation scope; one used
explicit task/run scope. No model turn was started.

## Change and boundary

Previously Codex setup required assembling the documented TOML entry. The new
command shares MCP connection, scope, budget and context flags with the existing
OpenCode generator, resolving executable/socket/token paths before rendering.
It does not read the token, connect to the service, merge configuration, set
project trust or change host permissions. It exposes the five ordinary tools,
uses optional startup by default and supports explicit `--required` startup.

The extraction of shared argv construction preserves OpenCode's existing JSON
shape, scope requirements and optional memory-only policy. The new command stays
in the CLI; it adds no database migration, server behavior, dependency or adapter
protocol. Existing store authority and delivery checks still apply.

The fixed table uses JSON string escaping plus explicit DEL escaping, after
rejecting invalid UTF-8. This follows the permitted basic-string escapes in the
[TOML 1.0 specification](https://toml.io/en/v1.0.0#string). Go string quoting alone
would allow escapes that TOML 1.0 rejects. Codex's
[official MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
describes the command, arguments, tool allow-list and startup fields; actual
client behavior was checked separately.

## Verification

- Existing OpenCode configuration tests passed before and after the shared
  construction change. Unit checks also refuse missing/conflicting scope,
  wildcard repository, invalid budgets, unsupported flags and invalid UTF-8
  before output, and retain output-writer errors.
- Four CLI tests use Python's independent `tomllib` parser. They verify exact
  values for quotes, literal shell syntax, Unicode, backslashes, every non-NUL
  ASCII control character and DEL; the input cannot inject a second MCP table.
  Missing credentials/socket, absent home configuration and an unusable database
  address do not prevent generation. Explicit scope and startup mode round-trip;
  equivalent OpenCode configuration retains identical argv. Python 3.11+ is now
  the documented verification prerequisite for the standard-library TOML parser.
- Native checks used generated files in isolated Codex homes and a copied Cairn
  executable whose path contains spaces, quotes and Japanese characters.
  `mcp get` resolved the entry. Actual `app-server` clients then listed the tools,
  searched and pulled the procedure with its previously observed version and
  SHA-256. MCP settings were not overridden in thread startup. Optional and
  required startup configurations both connected. Two automatic scopes were
  distinct; the explicit scope matched the supplied task/run exactly.
- `make test` passed all Go package tests and 30 Python tests. `make check build`
  passed vet, formatting and build. The local disposable integration script was not rerun for this
  CLI-only change; no database behavior changed. Native retrieval used the ordinary
  hosted-agent profile, without protected operator reports or test mutations.

This establishes a working setup path, not model-selected retrieval, completed
model-task work or durable cross-harness benefit. It does not establish native
resume/compaction handling or compatibility with untested Codex versions. Agy and
Claude interfaces remain separate open roadmap items.

The [verification manifest](codex-config-2026-09-09.json) retains source hashes,
check artifacts and doctrine decision provenance. Raw native replies and local
configuration are private temporary artifacts, not committed memory content.

## Local installation

Installed the clean CLI build from `9e6f6d3e3452e84b74006245489cb4dcf61134c2`,
SHA-256 `2c07838ee7d78b50b6ecb9c5cba3fd62194d32ed9fda1dd1944e2f0eadab6c67`.
The previous executable is retained under the private deployment directory.
The existing API process remains on `45ca33b`; no server restart, migration or
host configuration change was needed. API executable and Codex/OpenCode/semantic
configuration hashes remained unchanged.

A copy of the installed executable passed the same native generated-file check:
two distinct conversation scopes and one explicit scope, all five tools, exact
procedure retrieval and no model turn. The selected ordinary Codex procedure was
then revised to include the verified setup command and retrieved at version 6
through the scoped hosted profile. This is manual procedure maintenance.

[CI for installed source `9e6f6d3`](https://github.com/halbritt/cairn/actions/runs/34381023579)
also passed PostgreSQL integration with Go's race detector, all Python checks,
vet and build. This supplies integration coverage beyond the local CLI checks.
