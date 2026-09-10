# Ranked search continuation, 2026-09-09

Agents can now continue through ranked search results with an explicit offset.
Previously only unfiltered browsing could continue; a budget-limited ranked
search required a new query or larger input room. The existing packer now serves
ranked pages through the authenticated CLI, MCP and native OpenCode tools.
[Usage and limits](../index-and-pull.md#agent-commands-without-request-json).

Start with `offset: 0` or CLI `--offset 0`, then follow `page.next_offset` with the
same query and settings. Omitting the offset preserves unpaged behavior. Every
page includes required instructions and uses its own retrieval/pull budget.
Eligibility, destination, kinds and task phase are checked before page positions;
semantic discovery scores the full eligible set before paging. This increases
reachability, not the quality of ranking or the evidence for sustained task value.

## Verification

The database tracer first failed because the new wire field produced no page.
Its initial generated source had a syntax error, corrected before that behavioral
red run. The CLI test then found explicit zero was dropped; MCP and native OpenCode
tests likewise found missing page metadata before their forwarding changes.

Final checks cover:

- All matching notes reached across lexical and deterministic semantic pages,
  in the same order as the complete ranked result. The semantic test checks that
  every call receives all eligible candidates, including those beyond the page.
- Required instructions on every page, private and wrong-phase notes excluded,
  kind filtering, lexical nonmatches, exact body pulls and bounded offsets.
- Identical retries retain their receipt; reusing an ID for another offset
  conflicts. Each historical page reproduces its seal without invoking a scorer.
- CLI and MCP forwarding of explicit zero and later offsets. MCP and native
  OpenCode exercise semantic fallback and empty terminal pages; the native client
  pulls the exact record from a ranked page.
- Static checks, Go and 40 Python tests, and full disposable PostgreSQL/race
  integration including native OpenCode checks. Additional core retry, phase and
  nonmatch assertions passed after integration; production source was unchanged.

The native checks use OpenCode 1.18.21 without answering-model inference. The
semantic ordering test uses a deterministic scorer to isolate paging; it does
not assess embedding relevance or claim an additional real-model experiment.

## Compatibility and limits

Raw index requests use `page_offset`; existing `browse_offset` is unchanged.
Ranked pages use semantic format v11, with a sealed `page` and `PAGE_OFFSET`
omission count. Older formats omit the new field and retain historical behavior.
There is no database migration. Update the API, CLI/MCP binary and native adapter
before using ranked pages; older binaries cannot reconstruct v11 receipts.

Each page reads current state. Captures, revisions, eligibility changes and a
semantic fallback can shift the ordering. The caller should restart and deduplicate
records when necessary. Offsets are not snapshot cursors, and each page adds to
the host's total context consumption. Paging cannot recover absent vocabulary,
override an individual page budget, or change the semantic worker's candidate cap.

The live source-freshness investigation supplied the workflow context and returned
budget omissions, but its relevant evidence note was already on the first page.
This is a verified navigation capability; it is not a demonstrated rescue of that
task or an independently assessed memory contribution.

[Metadata](search-pages-2026-09-09.json) retains checks, source and decision
identities. Doctrine packet `pkt-20e4d66b311b2515` supported repository precedence,
test-first feedback, preservation of historical behavior and reuse of the existing
allocation owner. Four remaining interface obligations are nonmaterial: this
feature introduces no Go interface or package dependency. Typed evidence and the
decision receipt were schema-validated, and both consumption observations closed.
