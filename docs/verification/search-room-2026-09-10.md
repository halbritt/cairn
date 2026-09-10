# Native search input room — 2026-09-10

MCP and native OpenCode searches can now request a smaller input allowance through
`available_tokens`. The configured ceiling remains fixed. [Usage](../search-room.md)
describes byte accounting, retries and remaining whole-task limits.

The investigation began with E2's aggregate-context gap. Current source already
limits each index receipt and its pulls. The recalled startup procedure
`8ab6f87a-ff0d-46f2-9ae3-4f4c0cfa033c` v1 and current startup/runner documentation
distinguish their initial input allowances from whole-task accounting. No current
integration measures the complete remaining model window across generation and
compaction. This change exposes the existing per-retrieval control through native
tools; it does not replace or complete aggregate accounting.

The previous MCP schema and actual OpenCode adapter both rejected the new
argument. After implementation:

- A real disposable PostgreSQL/API/MCP race test honors 8,000-byte room, retains
  mandatory context, preserves receipt/budget on retry, rejects changed intent,
  and restores the configured default on a fresh call. Invalid values do not
  reserve request IDs; insufficient mandatory room refuses.
- The full CLI/API/MCP and native OpenCode suite passes. OpenCode 1.18.21 honors
  the smaller room, pulls the exact selected body with shared retry credits,
  rejects invalid values and keeps later default searches unchanged. Existing
  destination, permission, stale-handle and edit checks also pass. The suite uses
  disposable storage and scripted completions or native tool debugging, without
  model inference.
- Actual Codex 0.153.4 loaded the changed facade through its MCP client. With an
  8,000-byte allowance it retrieved the existing procedure's 154-byte passage,
  retained the receipt and pull budget on retry, rejected changed room under
  the same UUID and rejected a value above the configured 32,000 ceiling.
- `make check` and CLI race tests pass.

The first Codex probe incorrectly compared regenerated pull request UUIDs on a
repeated search. That is documented existing presentation behavior. A corrected
probe compared retained receipt, seal, handles and budget, and passed. Preserve
the original pull arguments when retrying a pull.

The [metadata](search-room-2026-09-10.json) retains checksums, source-note identities,
the decision-receipt locator and doctrine packet `pkt-38206ac1ff63b9db`.
Its 18 missing obligations are classified individually as nonmaterial domain-model
or Go-interface work. Pincite's validated release is
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`.

This verifies control over retrieval room. It does not establish better decisions,
lower total task cost or durable cross-harness task benefit. The API and database
require no upgrade.


## Installation

Clean CLI/MCP `f405aeb9c388ceb40e4d95d994b9130dd04e906d` and its bundled native
OpenCode adapter were installed
after [CI run 34541726630](https://github.com/halbritt/cairn/actions/runs/34541726630) passed both jobs and every step.
API `4999caa` and PostgreSQL kept their process IDs and executable hashes.
Migration 034, host settings, recent-file plugin and all
80 retained note-version payloads were unchanged. Previous binaries and adapter are backed up.

Actual Codex and OpenCode clients loaded the installed change and read the same
154-byte passage from startup procedure v1 with 8,000-byte room. Their source and
span hashes match. Both reject room above the host ceiling, keep identical pull
retries and use the configured 32,000-byte default on a later search. These checks
performed no model inference and changed no operational note content.
