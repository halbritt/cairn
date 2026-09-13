# Find saved direction by kind — 2026-09-09

Cairn can narrow optional retrieval to existing record labels, such as decisions
and preferences. This addresses an ordinary repository lookup: the first browse
page showed recent procedures/lessons, while the relevant direction records were
on page two. The query `project decisions preferences` returned setup procedures
and one value decision, missing the priority decision and history preference.

## Implemented behavior

The existing compile/index requests and native `cairn_search` accept `kinds`;
agent and trusted search accept repeated `--kind` flags. Filtering works with
lexical queries, browsing and semantic discovery. Empty means all kinds; up to
eight exact labels are accepted. Sorting and duplicate removal preserve equal-set
retry identity. Invalid labels refuse before store access.

Mandatory instructions, applicability, conflicts, destination and authority checks
remain. Filtering precedes optional ranking and paging; filtered candidates do
not reach the semantic worker. Hosted results expose no local-only identities or
counts. Kind labels confer no authority and can be wrong. All context and pull
budgets remain in force.

Filtered packages use semantic format v9, retaining the labels and historical
selection against original versions and frozen scores. Unfiltered requests retain
formats v3/v8 and their existing encoded intent. There is no database migration,
new native tool or model dependency. See [the usage contract](../index-and-pull.md).

## Verification

Fail-first real PostgreSQL tests observed extra kinds in body/index/browse/fallback
results and excluded candidates reaching the semantic worker. The final disposable
PostgreSQL suite with Go's race detector passes, including no-candidate semantic
handling, required/private instruction behavior, filtered multi-page browsing,
changed-kind history and retry intent. All Go packages, 30 Python tests, vet and
formatting pass. Previous-binary unfiltered compile/index retries and reconstructed
packages match exactly. The independent MCP client and a normal native OpenCode
session exercise the new input and returned selection. Native malformed kinds
are refused; responses are scripted on loopback, with no model inference.

Two intermediate failures were new test mistakes: adding a check after its token
cleanup, and comparing the historical CLI wrapper instead of its package. Both
were corrected without runtime changes. Their logs remain in the private artifact
manifest rather than being erased from the record.

Test guard reviewed behavior-level assertions, real store use and the semantic
worker boundary. Docs guard checked the flags, fields, schema compatibility and
preserved history. Doctrine packet `pkt-9bf566d0dee72fd3` used two typed evidence
passes; its decision receipt validates and citation consumption is closed.
Thirteen residual obligations are classified nonmaterial in the retained receipt.

## Limits

This establishes retrieval behavior, not model-selected use or net task benefit.
Unfiltered search is still useful when labels are incomplete or mistaken. Existing
clients and the API must be upgraded together; old binaries cannot reconstruct
filtered v9 receipts. The local installation check below exercises the ordinary hosted profile.

[Verification metadata](kind-filter-2026-09-09.json) retains hashes of the baseline,
failed and passing checks, decision evidence and source observations. Private
operational note bodies and raw native traffic are not committed.

## Local installation

Clean source `2d6718112551095caa4fa1c65048702de70d450c` is installed as CLI and API. The
running API executable matches SHA-256
`7dd724bbf6878bb011d753167eba1447c5ab0e6812ff3bfa7c56e0b6906c3173`.
The bundled native OpenCode adapter is updated. Codex/OpenCode connection settings,
semantic service configuration and the two-thread worker are unchanged. The store
process stayed running; PostgreSQL still has migrations 001–030. Both user services
are healthy. The previous binary and adapter are retained outside the repository.

At the same 32,000-token input allowance as the baseline, one ordinary hosted
browse with `--kind decision --kind preference` returned all three existing
project-direction notes. Before filtering, those notes were on the second browse
page. Each full body matched its indexed checksum; exact retries preserved the
responses, leaving one of four expansion credits after three distinct pulls.
A fresh MCP session returned the same versions and checksums. An actual filtered
query through the installed CPU semantic worker returned discovery state `ready`.
No new notes or task assessments were created.

This is a verified reduction in preview-page navigation for this lookup. It does
not establish general task improvement, model-selected retrieval or net benefit.

[CI for the installed source](https://github.com/halbritt/cairn/actions/runs/34405630245)
passed PostgreSQL/race, Python, vet and build. Native OpenCode and operational
profile checks were performed locally and are retained separately.
