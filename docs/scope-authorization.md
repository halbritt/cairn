# Authorize wider applicability

Scope broadening is a C authority transition. It changes where an existing
record may apply; it does not promote the record or replace its supporting
qualification. `authorize-scope` requires a live `issue` grant, an exact source
version, a current impact preview, an explicit target scope and applicability,
and a reason. The existing `issue` capability owns this C decision; installed
grants gain no new capability through migration.

After `cairn preview-retract RECORD_UUID`, submit JSON to `cairn authorize-scope`:

```json
{
  "request_id": "REQUEST_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 2,
  "scope": {"repo": "/path/to/repository", "task_id": "*", "run_id": "*"},
  "pins": {},
  "grant_id": "ISSUE_GRANT_UUID",
  "preview_id": "PREVIEW_UUID",
  "reason": "Authorize the reviewed claim for other tasks in this repository"
}
```

Substitute actual identifiers and the current version. Include each constraint
you intend to retain in `pins`; an empty object explicitly removes them all.

The target must contain the old applicability set within the same repository.
Task/run wildcards, removed context constraints and extended validity intervals
can expand it. Moving a claim to another task, changing repository or changing
sensitivity is a different operation. Every request includes `pins`, using `{}`
to explicitly request no applicability constraints. Omitted pins are refused.

A scope authorization creates a new version with the same content, class,
sensitivity, declared attribution and support links. The new version's observed
writer is the actual scope issuer. It records a separate current scope
grant. During the scope change, B/C retain their existing qualification/instruction
grant as an independent requirement. Both grants must remain live for fresh use; revoking either excludes
the current version. Ordinary edits and corrections within that scope retain its
scope authorization. A later explicit scope authorization can replace the scope
grant, including reauthorizing the same range, without reviving an independently
revoked B/C qualification. There is no implicit fallback to an older scope.

`scope-authorization RECORD_UUID` reports the most recent scope decision and
whether that scope grant is live now. This flag does not establish independent
B/C qualification. Fresh compilation reports `SCOPE_AUTHORITY_INACTIVE` when
the scope grant has expired or been revoked. Repeated mutation requests return
the original result; use inspection for current state.

All existing B evidence must remain resolvable for broadening. One surviving
reference can permit a consequential read, but does not permit broadening a
degraded claim. Original relation restrictions still apply: replace a narrow
source citation with appropriate broader support through the existing edit or
correction path before expanding its dependent. C content or support changes
continue to require retraction and issuance.

Open source conflicts refuse. Widening an instruction evaluates overlapping
policy keys and retains any newly exposed conflict. Record state, C audit event,
scope grant and new version commit atomically; CAS or stale preview failures
leave the record unchanged. Historical packages retain their original scopes,
qualification and scope-grant snapshots. The C checkpoint subset includes scope
authorization events for all three record classes.

The current scope grant is a projection of the latest explicit authorization,
not a chain of every earlier edit. Retained events and versions explain prior
decisions. Keeping scope authority separate prevents an issuer from silently
replacing B qualification; replacing the current scope grant avoids requiring
complete ancestry. A new version avoids changing an already consumed version's
scope. These decisions implement the accepted C broadening gate while preserving
the withdrawn universal-ledger boundary.

The C event and its version links are retained history. An otherwise ordinary A
record with scope-authorization history therefore uses D forgetting instead of
unreferenced ordinary deletion.

This operation remains local to the operator CLI. It does not change destination
access policy, automatically generalize evidence, certify that wider applicability
is scientifically justified, or establish complete restore admission.

See [verification](verification/scope-authorization-2026-09-08.md) for tested
claims and installation evidence.
