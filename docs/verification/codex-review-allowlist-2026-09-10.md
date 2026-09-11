# Codex review-tool allowlist correction — 2026-09-10

The native review tools shipped in `2ef6e04`, but `codex-config` and this
project's existing Codex allowlist still named only six tools. Updating the
executable therefore left `cairn_assessments` and `cairn_assess` unavailable in
Codex. The earlier direct MCP and native OpenCode checks did not cover this
configuration boundary. This corrects the prior installation report's implication
that every fresh harness session would load the new tools.

The generator now explicitly includes both review tools. The integration check
starts the generated MCP command and compares the parsed Codex allowlist with
actual `tools/list` discovery. A future mismatch fails even if separate generator
and server tests agree with their own outdated expectations. The explicit list
remains under source control.

## Verification

- The updated parser expectation failed against the original generator, then all
  four parser tests passed with the fix.
- A fresh native Codex 0.153.4 conversation using the old installed generator
  exposed six tools and failed the review-tool assertion. The candidate's
  generated configuration exposed all eight.
- Through native `mcpServer/tool/call`, the candidate completed search and owned
  assessment history, an unknown qualitative review with selected evidence,
  exact retry, reason/evidence read-back, changed-intent and stale-version
  refusals, accepted-without-evidence refusal, and a retained correction. Retrying
  the original request after that correction returned its original version.
  Search used the actual conversation ID for task/run scope.
- `make check` and the full disposable PostgreSQL integration suite passed,
  including the new generated-configuration/discovery comparison.

The native check used an isolated configuration and synthetic data in a disposable
PostgreSQL cluster. It started no answering-model turns. This proves tool exposure
and interface behavior; it adds no task-benefit or human-acceptance result.
The [manifest](codex-review-allowlist-2026-09-10.json) retains selected identities,
source/log hashes and local scratch pointers; raw native exchanges remain local.

## Upgrade

Upgrade the CLI/MCP executable, then add `cairn_assessments` and `cairn_assess`
to an existing Cairn `enabled_tools` list, or merge the regenerated Cairn table.
Start a fresh Codex session. For retrieval-only access, omit `cairn_remember`,
`cairn_edit` and `cairn_assess`. See the [configuration example](../mcp.md).
The API binding repair in `bb600c5` already supports these operations; no API
restart, adapter update or migration is required for this correction.

This project's ignored configuration was updated by adding only the two tool
names. Parsed settings otherwise match its saved predecessor, and
`codex mcp get cairn --json` resolves all eight. That command checks resolved
configuration; the separate native check above establishes actual discovery.
Installation of the clean source build is recorded below when completed.

## CI correction before installation

[CI for `087be85`](https://github.com/halbritt/cairn/actions/runs/34567025918)
failed in the existing semantic command test: it expected the output writer's
limit error but received worker exit status 141. Go 1.25 `Cmd.Wait` prioritizes
unsuccessful process exit over copying errors, so closing an overflowing pipe
can report producer SIGPIPE instead. The original test passed 100 local race
repetitions; that does not invalidate the recorded CI failure.

The replacement tests valid JSON at 65536 bytes, 65537 bytes and one MiB. The
first must survive intact; larger responses must fail in the worker transport.
It does not depend on which pipe error wins. Thirty focused race repetitions,
the full semantic race suite and `make check` passed. A temporary Go overlay
removing only the cap made both oversized cases fail because they were accepted.
The production worker, cap, cancellation and fallback behavior remain unchanged.
This is a test correction, not an additional installed feature.
