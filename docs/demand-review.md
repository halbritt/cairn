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
Exact-signature grouping is described below. General novel-failure discovery and
measured next-run benefit are still open.

## Review matching failures together

When multiple due proposals share repository, task class, binding, capability
and error-signature digest, `docket` emits one `FAILURE_CLUSTER` row. Task IDs may
differ. The row includes `proposal_group_key`, `proposal_count`, `failure_count`
and `task_count`. A singleton includes these fields while keeping its existing
`TASK_FAILURE` or `FAILURE_RECOVERY` row and proposal UUID.

```sh
cairn docket REPO
cairn proposal-group --limit 100 --offset 0 REPO GROUP_DIGEST
```

Copy `GROUP_DIGEST` from the docket. Group inspection returns its comparison
fields, counts, first/last proposal times and source members. Each member retains
its proposal UUID/version, failure and optional recovery receipt/assessment
versions, evidence IDs, review state, failure scope and both available assessment
witnesses/observers. Inspect selected evidence and review each proposal with the
existing commands. Group inspection does not copy evidence or record bodies.

`proposal_count` counts due proposals; `failure_count` counts their distinct
failure receipts; `task_count` counts distinct task IDs. Instrumented/testimony
counts classify the proposals' failure assessments. Several proposals can refer
to one failure. None of these counts establishes independent observations,
corroboration, cause or benefit. The method is `exact-failure-signature/1`; it
groups declared digests and does not infer semantic similarity or novelty from
raw logs. Different comparison fields remain separate even with an equal digest.

Groups are built from all currently due proposals before the docket's 100-item
limit. Existing urgent findings retain their priority; proposal groups order by
their oldest proposal time, then group key. A large group cannot consume every
docket row merely by having many members. Inspection defaults to 100 members,
accepts 1–200, and returns `more` and `next_offset`; member ordering is proposal
creation time followed by UUID. Follow each returned offset to inspect the rest.

Summary and members share one database snapshot and due-time cutoff per request.
New observations or reviews can change membership between pages. A disappeared
group returns `NOT_FOUND`; refresh the docket. A key identifies the comparison
tuple, not a frozen membership list or authority to review it. Existing per-source
version and currentness checks still apply when a reviewer acts.

Only open or now-due deferred proposals with current source assessments
participate. A current recovery pair suppresses its standalone predecessor even
when that pair is deferred, dismissed or converted. Correcting the pair's source
can make the still-current standalone proposal due again. Reviews never apply to
future members merely because their group key matches. No group-level mutation,
automatic admission, rank adjustment or new persisted authority is introduced.

The command is local operator CLI inspection and is not exposed through the
agent API. Core callers retain repository scope checks. No migration is required;
groups are a read-time projection over retained proposals and assessments.

Migration 019 permits an absent recovery source while preserving existing pairs.
Deploy the updated binary with this migration before generating standalone
proposals; older binaries assume every proposal has a recovery.
