# Proposal conversion versions — 2026-09-10

A converted failure proposal previously retained a logical lesson ID but no
version. Later lesson edits made that ID insufficient to identify the text linked
at conversion. Reopening also cleared the current result link, while the retained
review row held only disposition, reason, actor and time.

New reviews retain the linked record/version and applicable deferral time. An
optional `result_version` guards the lesson the caller inspected; omission keeps
the previous record-only interface and pins the version current at conversion.
`proposal-history` exposes the retained decisions through bounded version-cursor
paging. [Contract](../demand-review.md#preserve-the-linked-lesson-version).

## Verification

The initial behavioral regression failed because the conversion response had no
lesson version. It now passes before and after a lesson edit and on exact retries.
The history test initially could not compile because the public operation did not
exist; after implementation it verifies both the cleared current link and the
retained original conversion after reopening. A subsequent test used the wrong
return shape for body-only revision; that fixture error was corrected before the
behavioral checks ran.

The database tests establish stale lesson-version refusal with no review write,
retry after inspection, bounded history, repository refusal, retained-reference
protection against ordinary deletion, and successful audited forgetting/purging
without lesson bytes in review history. Full disposable PostgreSQL/race checks,
Go tests, 40 Python tests and static checks pass.

The real operator CLI converts a lesson, edits it, reads the exact original body
through its pinned version, reopens the proposal and pages the retained review.
The actual previous binary, clean `7dcf3dc`, then creates a record-only conversion
on the migrated store. Its result pin remains unknown, an earlier known pin is
not carried into that review, and its original mutation response retries exactly
through the new binary. Existing authenticated CLI/stdio checks also pass.
The prior-binary quoted-search check now handles both old lexical v4 and already
upgraded v5 baselines, preserving their respective retry contracts.

## Scope and limits

Migration 032 adds nullable fields to each review event. Pins derive from that
exact event, never from the current lesson or the proposal's previous disposition.
The new fields do not copy bodies or create authority. Existing old events remain
unknown; this implementation does not invent their missing historical references.
A retained review reference extends ordinary deletion protection, while the
existing audited forgetting path can still erase lesson bytes.

This is a provenance and inspection improvement needed before using reviewed
failure links in exact-signature retrieval. That retrieval is not implemented by
this change. No model task, real-task benefit, review-burden saving or automatic
learning result is claimed. Installation is recorded separately when verified.


## Decision provenance

Pincite's validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` supplied
packet `pkt-5117c43b460d6a45`. Typed requirements, source, test and review evidence
support the bounded decision. The decision receipt validates and the citation
trace is closed. Four Go-interface obligations are nonmaterial because no
interface or substitution boundary changes. The [manifest](proposal-versions-2026-09-10.json)
retains packet identity, classifications and hashes of local evidence artifacts.


## Local installation

Clean `97816e95514850c63379d2bdc3ffa4c61dba8f0d` is installed in the CLI, API and
project binary. SHA-256:
`d9098cbd818ce5f41a808460b329eeb0d74eb9718a4f01291ca8a5eabfd7a5b4`.
Migration 032 was applied after the standard store backup; the dump checksum and
readable restore catalog are recorded in the verification manifest. API PID
1583844 serves the new build. PostgreSQL PID 163669, connection settings, native
adapter and semantic worker source remain unchanged.

The operational store has no proposals or review rows. The installed history
command therefore correctly returns NOT_FOUND for an absent ID; no synthetic
conversion was added to production. Ordinary hosted quoted search still returns
the existing applicability note through the restarted API. This verifies the
installation and unchanged ordinary retrieval, while the conversion/history
behavior is established by the disposable tests above.

[CI 34453317076](https://github.com/halbritt/cairn/actions/runs/34453317076)
completed successfully for installed `97816e9`, including PostgreSQL/race tests,
40 Python tests, static/build checks and authenticated CLI/stdio workflows.
