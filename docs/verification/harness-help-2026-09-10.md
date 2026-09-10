# Harness command help — 2026-09-10

An actual MCP installation probe omitted the required socket argument. Asking the
installed binary for help then returned only `cairn mcp: flag: help requested`,
with exit status 1. The three configuration generators shared the same silent
flag parsing. Their option declarations existed, but the CLI did not show them.

`mcp`, `opencode-config`, `codex-config` and `claude-config` now render their
registered flags for `--help` or `-h` and exit zero before connection or
configuration generation. [Command help](../mcp.md#command-help) lists the supported
invocations. The shared parser keeps option descriptions beside their definitions;
it does not introduce a separate help registry or change other command parsing.

## Evidence

- Before the change, all three configuration-help unit cases returned no text.
  The public CLI check reproduced the installed MCP exit-1 failure.
- The command package tests and `make check` pass. Help includes the required
  socket/token-file arguments and applicable defaults/options. Output failures
  propagate instead of becoming success.
- The public binary check exercises both help spellings on all four commands
  without credentials or a running store, and confirms no local files are created.
  Unknown/incomplete arguments and positional `--help` still fail with empty stdout.
  A flag value equal to `--help` remains data.
- Valid OpenCode, Codex and Claude configuration outputs match the previous
  binary byte-for-byte after normalizing only the executable path. Existing
  configuration-format and argument-preservation tests also pass.

The offline check is part of the existing authenticated CLI integration script.
Logs, compatibility observations and typed evidence are retained in
`/tmp/cairn-harness-help-20260910`; [metadata](harness-help-2026-09-10.json) records
source hashes and Pincite packet `pkt-eb5d8836e66724da`. Seven generic Go-interface
obligations are nonmaterial because no interface hierarchy or typed-nil absence
contract changed. Both citation-consumption loops are closed.

This repairs a setup obstacle encountered during ordinary work. Recalled project
direction informed the choice to address a concrete problem using the existing
harnesses. The defect and its fix are verified; memory's incremental contribution,
setup-time savings and broader task benefit are not measured. No model inference,
operational-note change or database migration was needed. This CLI-only change
requires no API restart; installation and CI are recorded separately when complete.
