# Exact-signature proposal review groups

Verified 2026-09-08 against PostgreSQL 17. This implements duplicate review
grouping from design §15.4. D1 remains partial: general novel-failure detection,
richer conflict/promotion groups, and measured operator review cost remain open.

## Implemented behavior

Matching due proposals now occupy one docket row, with paginated source
inspection through `cairn proposal-group`. Matching requires equal repository,
task class, binding, capability and declared error-signature digest. Source
proposal UUIDs, assessment versions, evidence references and witnesses remain
available. Review still applies to each proposal independently.

The [review contract](../demand-review.md#review-matching-failures-together)
defines ordering, counts, currentness, deferrals and recovery-pair suppression.
There is no migration or new agent endpoint. The group key identifies a live
comparison tuple, not a persisted group or permission to change its members.

## Verification

- Seven focused PostgreSQL tests pass: six group tests and the existing
  standalone-failure lifecycle regression. They check differing comparison
  fields and repositories, exact source identity and attribution, live source
  corrections, individual deferral/dismissal, new members after earlier reviews,
  and current recovery-pair suppression.
- With 105 matching source proposals plus one different signature, the docket
  returns two rows without truncation. Inspection returns the matching sources
  in pages of 100 and five, with no repeated proposal. This tests grouping before
  the row limit; it is not a large-store performance benchmark.
- `make test-integration check` passes across all five Go packages with the race
  detector, disposable databases, CLI and authenticated local API checks, vet
  and formatting. All 12 Python tests pass separately.
- The standing CLI check wraps two `/bin/false` commands under different task
  IDs, explicitly assesses them as synthetic task failures, inspects both source
  pages, then dismisses one proposal. The remaining singleton retains its own
  open disposition and the same group key. Manual assessments stay testimony.

The tests assert returned records and review behavior. An early test incorrectly
assumed task order from UUID order when proposals shared a creation timestamp;
it was corrected to compare source identities across pages. The original
grouping regression failed against the prior docket implementation.

## Retained real-history compatibility

An isolated restore of the [completed model trial's reviewed dump](observed-model-failure-2026-09-08.md)
contained the real failure proposal already converted at version 2. It was
absent from the docket. Reopening that source at version 3 exposed one group
member with its original receipt, assessment version, selected evidence,
signature and instrumented `host:recurrence:cairn_h0` attribution. Reconverting
it at version 4 removed the group; direct inspection returned `NOT_FOUND`.

No new failure, proposal or lesson was created by this drill. Original trial
report and dump hashes remained unchanged. The disposable cluster was stopped
and removed. This verifies one real source's compatibility with grouped
inspection, not cross-task recurrence benefit or reduced review time.

## Evidence and limits

Private terminal evidence is retained at
`/tmp/cairn-proposal-groups-final-focused.log`,
`/tmp/cairn-proposal-groups-integration.log`,
`/tmp/cairn-proposal-groups-python.log`, and
`/tmp/cairn-proposal-groups-restore.json`. Reproduce the standing checks with
`make test-integration check` and `make test`; use disposable stores. The
fixed real-history drill helper is private and refuses repeating a completed
drill without inspecting its retained result.

Doctrine packet `pkt-dd124d8bf22438a6` supported the bounded implementation
review; its schema-validated receipt is
`/tmp/cairn-proposal-groups-decision-receipt.json`. Two evidence passes closed
material obligations for this claim. Generic UI/configuration, match-text,
serving-parity and named-procedure obligations are recorded as nonmaterial to
this synchronous local inspection slice. Release parity is a separate check.

The implementation groups declared digests. Counts establish neither independent
observations nor corroboration, cause, admission eligibility or ranking value.
Membership can change between page requests. Large-store aggregation cost,
general semantic similarity and actual weekly review burden remain unmeasured.
