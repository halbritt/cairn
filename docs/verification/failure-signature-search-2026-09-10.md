# Reviewed failure signature retrieval — 2026-09-10

Cairn previously retained a failure proposal's converted lesson version but could
not use that association in search. A PostgreSQL regression reproduced an empty
result when the known signature matched a reviewed lesson and the new query
shared no words with it.

The [new retrieval field](../failure-signature-search.md) uses current, exact
conversion pins as one optional preference. The lesson's own scope and
applicability govern reuse across task and harness labels. Hosted use requires
an explicitly shared review association as well as an eligible shareable lesson.
Migration 033 defaults that sharing choice to false, including older writers.

## Verified behavior

Targeted database tests and the full disposable PostgreSQL/race suite pass:

- Signature-only lookup and ranked paging, hexadecimal normalization, and
  malformed/browse/empty-semantic refusals.
- Body and compact-index selection, lexical fallback, ready semantic ordering
  and labelled semantic fallback, with required instructions retained.
- Destination, applicability and kind filtering before optional selection.
  Combined quoted-text and signature matches do not add another preference.
- Fresh matching stops after reopening, lesson edits or source assessment
  correction. Retained selection reproduces after reopening and lesson edits.
- Ordinary testimony remains unavailable for consequential use; signature-only
  relevance still creates protected blocked demand with a disjoint text query.

The shipped CLI/API, MCP stdio and actual native OpenCode custom tools retrieve
and pull an explicitly shared reviewed lesson on a different task from its
source failure. The CLI startup check supplies the signature without query text.
Fresh and retained runner checks carry the field; changed or missing retained
signatures refuse before binding or launch. Clients use unusable database URLs
to establish that these paths use the authenticated API.

Actual previous binary `97816e9` retains its review version pins on the migrated
store while leaving association sharing false. Previous mutation responses and
quoted-query package replay/retries remain exact. Go tests, 40 Python tests and
static checks pass. The optional OpenCode checks use scripted tooling and fixtures;
no answering model ran.

## Failures and corrections during implementation

The initial retrieval test failed as expected; a separate test reproduced missing
explicit hosted sharing before that field was implemented. Review then exposed
missing blocked demand for signature-matched A testimony with no lexical overlap;
the new regression failed before the compiler's relevance condition was fixed.

Four fixture issues were also corrected: a non-Git revision string, an outdated
expected MCP error message, missing required startup tool names/carrier, and a
new fixture run affecting an earlier aggregate report count. The added fixture
now runs after that earlier report's observations. Full integration and the
affected native/API checks were rerun. Original failures remain in the local
artifacts referenced by the [manifest](failure-signature-search-2026-09-10.json).

## Meaning and limits

This is retrieval capability for reviewed recurrence guidance. The operational
store had zero proposals and reviews during the installation preflight. No
synthetic conversion was added there, and no real-task recurrence reduction,
review-cost saving or durable task benefit is established by these checks.

The association remains fallible: supplied signatures are not automatically
normalized from logs, source labels do not prove physical execution identity,
and conversion does not grant authority. Historical matching preserves what was
selected; existing handles are not refreshed searches. General metadata redaction,
retention and automatic host intent collection remain open.

## Decision provenance

Pincite's validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` supplied
final packet `pkt-e1505300c9e82e37`. Typed evidence and the decision receipt
validate, and citation consumption is closed. Eight remaining obligations are
nonmaterial to this change: no asynchronous workflow or recovery protocol and no
Go interface/substitution boundary is introduced. Existing synchronous retry,
replay and delivery behavior was checked separately. The manifest records the
individual classifications, packet identity and local artifact hashes.
