# Retained-run intent review — 2026-09-10

No additional intent guard is warranted by this review of current Cairn source.
The newer retrieval fields have explicit checks before retained execution binds
or launches. The recalled prior missing-kind defect supplied a useful review
question; it did not establish that the defect had recurred.

## Reviewed boundaries

| Declared intent | Current owner and treatment |
| --- | --- |
| Receipt, seal, mode, destination and scope | `runner.Run` compares them with the retained package. |
| Revision/workspace, task class/phase and binding/capability | `Run` compares the complete context value with declared run metadata. |
| Query, failure signature, advisory-conflict intent, purpose and memory room | `Run` compares the query digest, normalized signature and explicit values. |
| Kind filters and file/symbol associations | `Run` compares normalized kinds and the normalized entity-intent digest. |
| Browse/ranked-page position and semantic discovery | `Run` compares offsets and the requested discovery mode. |
| Expansion reader and index expiry | `renderRunIndex` checks these before input reaches binding. It also validates the declared tool names. |
| Request UUID | A new operation identity, not an assertion that the original compile request is being repeated. The retained receipt/seal remain explicit. |

Policy, ranking and selected content belong to the exact retained package; the
runner does not silently compile replacements. Source pointers and hashes are in
the [review metadata](retained-intent-review-2026-09-10.json).

Existing disposable PostgreSQL tests passed for exact retained execution without
compilation, changed-context refusal before binding/child, a currentness recheck
after loading, kind normalization and fresh/retained index delivery with a real
child and reader pull. The helper did **not** enable the race detector. The table
is a static field review, not an exhaustive new dynamic matrix, full authorization
audit or proof of task acceptance. No product or test source changed.

## Native integration state

Live inspection found Striatum main at `5ea87ca`, its native integration branch
at `ee8a463`, and the installed Driver stamped clean `a6b1ae7`. Main's catalog
still declares `observation@1` and `build@3`; the owning Cairn amendments remain
separate proposals. The live status projection reports opening request 408328
satisfied at target `captured`. That establishes the opening intent capture,
not native contract acceptance or a completed useful build.

The producer, consumer and supervisor already exist on the integration branch.
Their remaining adoption path is described in [native context direction](../native-striatum-context.md).
This review creates no second producer, supervisor or opening request and changes
no graph acceptance, deployed runtime or contract.

For a machine-readable observer read, use `striatum --json status`: the JSON flag
is global and precedes the verb. This inspection first placed it after `status`
and got human-readable output. It also incorrectly tried `ledger cat --help`,
which performed an unnecessary full ledger read. That reader completed normally;
no graph mutation or process restart occurred. Only the relevant opening-request
row is retained in the public review metadata; full status remains outside Git.

## Decision and limits

Continue toward useful native task execution through the existing contract path,
and revisit intent guards when a new request field or an observed discrepancy
requires it. A replacement adapter or another field gate is not supported by the
current findings. The source review and tests do not close U1, E4 or D2.

This coding agent used the existing native-capture and retained-kind lessons to
orient the review, then checked current source and state. This is an observed
application of remembered guidance. Avoided rework, net benefit and independent
model task improvement are not established; source documentation was also
available. No operational note changed and no model inference ran.

Pincite packet `pkt-1422f59f1bf8ee44` supported the bounded no-change decision.
Its validated receipt, exact evidence and both closed citation-consumption loops
are under `/tmp/cairn-retained-intent-review-20260910`. Thirteen nonmaterial
obligations concern broader authority/interface/presentation decisions that this
review does not make; the metadata retains their exact requirements and rationale.
The first receipt validation caught a missing verification dependency; it was
corrected before validation and publication.
