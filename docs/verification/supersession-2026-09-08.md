# Cross-record supersession verification

Migration 021 adds retained replacement and affected-version references plus the
`superseded` lifecycle. The CLI exposes atomic supersession and metadata
inspection. Local agents can supersede ordinary A records after obtaining their
own impact preview; B supersession remains an authorized operator operation.

Disposable PostgreSQL verification establishes:

- Superseding a consumed B claim removes it from fresh compilation, selects the
  independently supported replacement, and preserves the earlier replay seal.
- Revising the replacement preserves the original pinned link. Revising a known
  dependent removes its current-version docket notice while retaining history.
- Forgetting the obsolete source does not invalidate a replacement with
  independent support and no derivation link to that source.
- Ordinary A supersession creates no authority event, permits narrower scope,
  and does not make the replacement visible outside that narrower scope.
- Stale source/replacement versions, missing or stale previews, wrong actors,
  B-to-A replacement, revoked replacement authority, unavailable evidence,
  scope/applicability/sensitivity expansion, expired validity, open conflicts,
  dependent C instructions and self-replacement refuse without retiring the
  source or creating a replacement link.
- Two concurrent replacements have one winner. An inactive source cannot be
  revived as a replacement to create a cycle. Identical retries return the
  original transition.
- The authenticated API preserves caller identity, refuses grant-bearing
  supersession and hosted access to protected inspection metadata.
- A real disposable backup/restore preserves ordinary supersession, its pinned
  replacement, inactive source and idempotent retry.

`make test-integration check test-lifecycle` passes, including race-enabled store
tests, authenticated Unix API smoke checks, formatting/vet, historical replay,
checkpoint verification, restore fencing and existing interrupted-deletion
drills. Initial new-feature tests failed to compile because the supersession API
did not exist. An extended fixture later missed lexical selection after its
body revision dropped the query term; correcting that fixture restored the
intended history/deletion check without changing selection behavior.

No operational claim was superseded for these checks. This establishes the
implemented lifecycle behavior, not broad memory usefulness or full lifecycle
acceptance. Scope broadening, notice acknowledgement, future discovery of
undeclared dependencies and fresh-restore A/B retirement reapplication remain
outside this slice.
