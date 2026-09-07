# Evidence-attached review proposals

`cairn generate-proposals` accepts `request_id`, `repo`, and optional `offset`.
It scans at most 100 eligible pairs per page and returns `more`/`next_offset`.
The generator pairs an explicitly assessed task rejection with the first later
acceptance under the same declared repository/task, task class, binding and
capability identity. Unknown comparison labels, missing error-signature digests,
and binding failures do not generate this kind of proposal.

A pair is a review candidate, not proof that an action fixed the failure. It
retains both receipt/assessment versions, selected evidence IDs and the detector
method. Identical source pairs reuse one proposal. Later assessment corrections
remove stale pairs from the active docket; history remains inspectable. This
operates on structured observations and never mines raw prompts or outputs.

```sh
cairn proposal PROPOSAL_UUID
cairn evidence EVIDENCE_UUID
```

Evidence inspection returns captured bytes, expected and actual digests, witness
and resolver state. Divergent bytes remain labelled divergent; inspection does
not silently revalidate them.

`cairn review-proposal` accepts `request_id`, `proposal_id`, `expected_version`,
`disposition`, `reason`, and optional `until` or `result_record`. Dispositions:

- `open`: return a current proposal to review.
- `deferred`: suppress it until the supplied future time.
- `dismissed`: retain the source pair and review decision without active demand.
- `converted`: link an existing record in the same repository.

Review decisions retain versions and actors. Conversion does not create or
promote a record. Stale source assessments cannot be reopened or converted;
review locks their receipt boundaries against concurrent assessment changes.
Conversion also checks that selected evidence is currently resolvable and digest-valid.

The docket prioritizes attribution contradictions and incomplete delegated work
above ordinary blocked demand. New failure/recovery proposals appear as
`FAILURE_RECOVERY`; deferred, dismissed, converted and stale-source proposals do
not appear as due. Existing `ESCALATION_BLOCKED` record/version groups remain.
General novel-failure clustering and measured next-run benefit are still open.
