# Governed repository policy

Cairn retains effective repository policy as versioned C governance records.
`policy-revise` requires a live `issue` grant for the authenticated actor and
repository, the expected current revision, and a reason. Each revision and its
authority event commit atomically. An ordinary file edit or agent request cannot
change the effective policy.

The `local-loop/2` engine currently governs optional-memory budgets. Rules may
narrow the existing ceiling of 10% of available input room and 6,000 tokens.
Either limit can be zero to exclude optional memory for a governed baseline.
Mandatory instructions still have to fit and pass their enforcement checks.
The conservative UTF-8 byte token bound remains unchanged.

Inspect the current policy with `cairn policy REPO`. A repository without an
explicit revision retains `local-loop/1` and the original budget; migration does
not issue authority or rewrite existing receipts. To establish its first revision:

```json
{
  "request_id": "REQUEST_UUID",
  "repo": "/path/to/repository",
  "expected_revision_id": "",
  "grant_id": "ISSUE_GRANT_UUID",
  "rules": {"optional_percent": 10, "optional_max_tokens": 6000},
  "reason": "Adopt the reviewed optional-memory budget"
}
```

Send the request to `cairn policy-revise`, replacing placeholders with actual
identifiers. Later changes must name the current `revision_id` in
`expected_revision_id`. Supply either `rules` or `restore_revision_id`.
An explicit `rules: {}` means both limits are zero; omitted rules without a
rollback target are refused. Unknown fields are rejected by the CLI decoder.

Rollback creates a new revision under current authority:

```json
{
  "request_id": "REQUEST_UUID",
  "repo": "/path/to/repository",
  "expected_revision_id": "CURRENT_REVISION_UUID",
  "restore_revision_id": "EARLIER_REVISION_UUID",
  "grant_id": "ISSUE_GRANT_UUID",
  "reason": "Restore the previously reviewed optional-memory budget"
}
```

The target must belong to the same repository. The new record retains both the
immediately preceding revision and the revision whose rules it restores. It does
not edit the older record or reuse its authority. Restoring the current rules is
also permitted as explicit reauthorization. A revoked or expired effective grant
blocks fresh compilation with `POLICY_UNENFORCEABLE`; there is no implicit
fallback to an earlier revision or the built-in baseline.

`policy-revision REVISION_UUID` inspects a retained revision, including its
actor, audit event, previous/rollback references, rules and current authority
liveness. `policy REPO` identifies the effective head. A transport retry returns
the original mutation result; use inspection for current liveness. Inspection
of a historical revision does not imply it is still effective.

## Receipts and delivery

New packages pin the engine, exact revision, budget rules and frozen grant chain.
Administrative reasons and history are excluded from the delivered snapshot.
These structured policy pins are part of the semantic seal and available to the
configured destination. Existing schemas retain their seals when the new optional
field is absent. Historical recompilation uses frozen policy facts and does not
reinterpret an old package under today's policy.

A policy change requires a new compile request before new launch claims, run
bindings, context registration or body expansion. Cached binding/expansion retries
also recheck this gate. It affects only receipts in the changed repository.
Already delivered bytes and running processes are not recalled; delayed outcome
observations and historical inspection remain available.

A shared policy-generation row orders compilation and delivery against revision
commits, including the first revision in a repository. A compiler snapshot that
predates a committed revision must retry. Current grant locks are retained through
the fresh operation. Concurrent revisions use expected-head comparison and produce
one winner; they cannot publish incompatible effective heads.

## Find affected runs

```sh
cairn runs --policy-rev REVISION_UUID --limit 100 --offset 0 /path/to/repository
cairn run-report --policy-rev local-loop/1 /path/to/repository
```

Both commands use the same paginated run report. Each row identifies its retained
policy revision; `local-loop/1` identifies the original built-in policy. The
population includes launch claims and observed outcomes, including runs with zero
memory exposures. Pure retrieval is excluded. Policy updates and rollback do not
move older runs between revisions. The authenticated local run-report API accepts
the same `policy_revision` filter; authority mutations remain operator CLI only.

Policy records use dedicated typed storage rather than ranked memory bodies.
Their C events enter unsigned audit checkpoints; as with existing checkpoints,
membership verifies audit metadata, not policy-rule payload commitments. Backup
and restore retain policy revisions, rollback references and receipt joins.

This is the versioning, rollback and affected-run portion of roadmap L3. C waivers,
instruction-category caps and the full unenforceability decision table remain
open. This rule format cannot weaken authority, evidence, sensitivity, destination
or mandatory runtime gates, authorize broader sharing, or introduce learned ranking.
See [verification](verification/governed-policy-2026-09-08.md).
