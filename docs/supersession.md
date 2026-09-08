# Replace an obsolete record

`supersede` retires one active A or B record in favor of an explicit current
version of another record. It names both expected versions, a current
`preview-retract` token for the old record, and a reason. A B source requires
the `correct` capability and an independently qualified B replacement. An A
source can name A or qualified B; its retirement creates no authority event.
C changes continue to require retraction and a new issuance.

For example, after `cairn preview-retract OLD_UUID`, send this JSON to
`cairn supersede` (use the actual UUIDs and versions):

```json
{
  "request_id": "REQUEST_UUID",
  "record_id": "OLD_UUID",
  "expected_version": 2,
  "replacement": {"record_id": "REPLACEMENT_UUID", "version": 2},
  "grant_id": "CORRECT_GRANT_UUID",
  "preview_id": "PREVIEW_UUID",
  "reason": "Replace the obsolete workaround with the reviewed correction"
}
```

Omit `grant_id` for A. Local agent profiles can use `agent preview-retract`,
`agent supersede` and `agent supersession`, with JSON requests. The two
inspection commands accept `record_id`. The API refuses grant-bearing
supersession; B retirement stays on the operator CLI. Hosted profiles cannot
inspect protected impact or replacement metadata.

The replacement must stay within the old record's repository, task/run scope,
applicability and sensitivity restrictions. Narrowing is permitted: the old
record retires everywhere, while the replacement applies only within its own
scope. Supersession does not copy or widen the replacement. A B replacement
must have reconciled attribution, live authority and resolvable evidence at the
transition. Future reads still evaluate its current eligibility and applicability.
The old record may have lost support; that does not prevent replacing it.

Both records are locked and version checked in one transaction. Open conflicts
in the old record's known dependency set, an active dependent C instruction,
or an open conflict on the replacement refuse. Original versions, citations,
evidence and exposures survive. The new inactive version makes no fresh
dependency citations. A replacement link is retained separately from derivation
links: replacing a claim does not assert that the new claim derives from it.

`supersession RECORD_UUID` inspects the pinned replacement and transition
metadata. Later replacement revisions or retirements do not rewrite that link.
`use-report` continues to show the exact consumed version and current lifecycle.
The review docket names known active dependent versions and prior exposed
versions; revision removes a dependent's current-version notice. Notices do not
claim influence, automatic correction or completed human review. Their retained
source sets are bounded by the impact preview's 1,000-version limit; docket
truncation remains explicit. Unknown derivations are not inferred.

The transition metadata remains readable after body forgetting. Retained
replacement and affected-version references prevent ordinary deletion from
erasing this history; the D forgetting path still applies. Reasons are retained
metadata and remain within the unfinished metadata-redaction scope.

The implementation uses a dedicated transition because a plain relation leaves
the old claim eligible, and separate retire/link calls can leave an unlinked
retirement after a crash. `correct` remains the operation for revising the same
logical claim. Keeping the replacement link separate from derivation prevents
later forgetting of the obsolete source from invalidating independent support.
These choices preserve the existing authority and historical-read contracts;
they do not introduce a general event ledger.

This implements the class-proportional replacement path in the accepted design
§8.5. It does not implement scope broadening, automatic dependent qualification,
notice acknowledgement, or fresh-restore reapplication. The external recovery
record continues to cover governance/C/D withdrawals, not A/B retirement history.
