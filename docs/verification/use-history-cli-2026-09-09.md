# Continue and filter CLI use history — 2026-09-09

`cairn use-report` now exposes the existing report's `--limit`, `--offset` and
`--record` controls. Previously it returned at most 100 exposure rows and rejected
extra arguments, even when the response said more rows existed. Because ordering
is oldest first, later activity became unreachable through that CLI. Direct API
pagination already existed.

The default remains 100 rows. Explicit pages accept 1–200 rows and a nonnegative
offset; an optional record UUID selects that record across retained versions.
The CLI forwards these fields to the existing core owner, preserving response
shape, ordering, validation, unknown observations and access boundaries. SQL,
schema, API permissions and assessment semantics are unchanged. See
[usage and interpretation](../use-outcome-loop.md#page-through-use-history).

## Verification

A fail-first disposable fixture created 102 exposures across two records and 51
receipts. The old CLI returned 100 rows with `more=true`, then refused offset 100.
The final CLI reaches the remaining two, matches the complete 102-record/receipt
pair set, and retrieves all 51 exposures of one record across smaller pages.
It preserves unknown usage and task outcome and rejects invalid limits, negative
offsets, malformed record IDs and invalid argument counts.

The fixture uses actual CLI processes and PostgreSQL. It runs in local disposable
integration and is added after build in CI. Local PostgreSQL/race integration,
all Go packages, 30 Python tests, vet and formatting pass. No protected operational
use report was read, and no model task or human acceptance was created.

The stored assessment-review procedure was retrieved during the work. Its warning
that aggregate outcome labels omit narrative evidence is reflected in the usage
documentation. The CLI defect itself was established by source inspection and
reproduction; memory's net contribution is not measured.

## Limits and review

Pages read current state. Concurrent exposures, backfills or deletions can shift
positions; this is not an atomic history export. Pagination cannot supply missing
observations, recover pruned data or turn exposure into evidence of benefit.
The protected API still requires a local profile and denies hosted profiles.

Test guard checked observable completeness, real storage and the absence of
mocked implementation details. Docs guard checked flags, limits, ordering, report
meaning and local-profile restrictions. Doctrine packet `pkt-44aa26a7a642bc03`
used one typed evidence pass; its decision receipt validates and citation
consumption is closed, with four interface obligations classified nonmaterial.
[Verification metadata](use-history-cli-2026-09-09.json) retains hashes of the
failure, completed checks and decision evidence. U4's broader recurrence and
resolution analysis, and demonstrated task benefit, remain open.
