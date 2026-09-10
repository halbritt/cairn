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
`disposition`, `reason`, and optional `until`, `result_record`, `result_version` or `signature_shareable`. Dispositions:

- `open`: return a current proposal to review.
- `deferred`: suppress it until the supplied future time.
- `dismissed`: retain the sources and review decision without active demand.
- `converted`: link an existing record in the same repository.

Review decisions retain versions and actors. Conversion does not create or
promote a record. Stale source assessments cannot be reopened or converted;
review locks their receipt boundaries against concurrent assessment changes.
Conversion also checks that selected evidence is currently resolvable and digest-valid.
New conversions retain the linked lesson version; see the review-history contract
below.

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


## Preserve the linked lesson version

New conversions record the lesson version linked in that transaction. Editing the
lesson later does not change this pin. Supply `result_version` with
`result_record` when converting a version you inspected:

```json
{
  "request_id": "REVIEW_UUID",
  "proposal_id": "PROPOSAL_UUID",
  "expected_version": 1,
  "disposition": "converted",
  "result_record": "LESSON_UUID",
  "result_version": 3,
  "reason": "Link the inspected lesson to this reviewed failure"
}
```

Replace the UUID placeholders with real IDs and pass this object to
`cairn review-proposal` on stdin. The proposal's
`expected_version` guards its disposition; `result_version` independently guards
the lesson. A changed lesson returns `VERSION_CONFLICT` without committing a
review. Inspect the change before retrying. If `result_version` is omitted, the
existing record-only request remains supported: Cairn links and returns the
current lesson version while holding the record lock. This does not prove that
the caller read that version. Only conversions accept result fields.

The `proposal` response includes `result_version` for a pinned current
conversion. Use `cairn history` with that record ID and exact version to inspect
the original text under the existing availability rules. A pin is a historical
reference; it does not assert current eligibility, correctness, authority or task
benefit. Later edits, retirement or forgetting do not retarget it.

`cairn proposal-history` accepts an object on stdin:

```json
{"proposal_id":"PROPOSAL_UUID","limit":20}
```

It returns the current `proposal` snapshot and retained `reviews`, newest first.
Each review includes its version, disposition, reason, observer and time. New
reviews also preserve the conversion record/version or deferral time, where
applicable. Reopening, deferring, dismissing or converting again preserves earlier
review entries. Creation is proposal version 1; review entries begin at version 2.

Paging defaults to 20 reviews and accepts 1–100. When `more` is true, pass
`next_before_version` as `before_version` on the next request. This exclusive
version cursor avoids shifting older pages when a new review is appended. The
current proposal header can change between pages. Inspection is local CLI work;
the agent API and ordinary harness tools do not expose proposal review history.
Trusted core callers retain repository scope checks. No lesson body is copied
into the review history.

A retained conversion reference prevents ordinary deletion even after a proposal
is reopened. Use the existing `preview-delete` and audited forgetting workflow
when removal is required. Forgetting and purging can remove the lesson bytes while
retaining review IDs and version metadata. Review reasons retain their existing
policy; this change does not add general metadata redaction or retention pruning.

Apply migration 032 with the new binary before using the new read/write paths.
It adds nullable metadata to individual review rows and does not backfill old
reviews from today's lesson. Legacy conversions keep unknown version pins;
missing `result_version` means unknown, not the current version. Their per-review
result IDs and deferral times may also be unavailable. A genuinely older writer
can still create an unpinned review after migration; the new reader does not carry
an earlier pin into that review. Upgrade active review writers to obtain new pins.
Previously committed mutation retries retain their original responses.

[Verification](verification/proposal-versions-2026-09-10.md) covers conversion,
source edits, reopen/history paging, stale-version refusal, body forgetting and
an actual older writer. Error-signature retrieval using these links remains a
separate E1 implementation step.


## Retrieve a converted lesson on a later task

[Failure signature search](failure-signature-search.md) uses current, exact-version
conversion links as optional retrieval hints. The association remains local unless
the conversion explicitly sets `signature_shareable: true`; hosted delivery still
requires an eligible shareable lesson. Ordinary note sharing does not publish
private review associations. Source assessment corrections, reopening and lesson
edits remove old links from fresh matching. Historical retrieval remains distinct.
