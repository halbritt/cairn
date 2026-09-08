# Evidence-attached review proposals

`cairn generate-proposals` accepts `request_id`, `repo`, and optional `offset`.
It scans at most 100 eligible failures per page and returns `more`/`next_offset`.
The generator pairs an explicitly assessed task rejection with the first later
acceptance under the same declared repository/task, task class, binding and
capability identity. Unknown comparison labels, missing error-signature digests,
and binding failures do not generate proposals. If no matching acceptance exists,
the failure becomes a standalone proposal (`kind: failure`, method
`standalone-task-failure/1`). Its recovery receipt is empty and recovery version
is zero. A paired proposal has `kind: failure_recovery`.

A pair is a review candidate, not proof that an action fixed the failure. It
retains both receipt/assessment versions, selected evidence IDs and the detector
method. Identical source pairs reuse one proposal. Later assessment corrections
remove stale pairs from the active docket; history remains inspectable. This
operates on structured observations and never mines raw prompts or outputs.

A standalone proposal retains the failure's exact assessment version and selected
evidence. Repeated and concurrent generation reuses its identity. If a recovery
later becomes available, generation creates a separate pair; it does not rewrite
the standalone source or its review history. The current pair takes precedence
on the docket, even if that pair is deferred, dismissed or converted. If its
recovery assessment is corrected, the still-current standalone proposal can
return according to its own disposition. This is duplicate suppression for one
failure source, not cross-task clustering or a claim that the failure is novel.

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
- `dismissed`: retain the sources and review decision without active demand.
- `converted`: link an existing record in the same repository.

Review decisions retain versions and actors. Conversion does not create or
promote a record. Stale source assessments cannot be reopened or converted;
review locks their receipt boundaries against concurrent assessment changes.
Conversion also checks that selected evidence is currently resolvable and digest-valid.

The docket prioritizes attribution contradictions and incomplete delegated work
above ordinary blocked demand. New failure/recovery proposals appear as
`FAILURE_RECOVERY`; standalone failures appear as `TASK_FAILURE`. Deferred
proposals become due at their review time; dismissed, converted and stale-source
proposals do not appear as due. Existing `ESCALATION_BLOCKED` groups remain.
General novel-failure clustering and measured next-run benefit are still open.

Migration 019 permits an absent recovery source while preserving existing pairs.
Deploy the updated binary with this migration before generating standalone
proposals; older binaries assume every proposal has a recovery.
