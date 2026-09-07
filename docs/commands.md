# Structured command examples

Every mutation has a caller-scoped request UUID. Reuse it only for retrying the
same intent. These examples use placeholder UUIDs; substitute IDs returned by
your own requests. CLI input is one JSON object, bounded to 128 KiB. The core
library permits up to 1 MiB per inline evidence object.

Create an ordinary note:

```json
{
  "request_id": "NEW_UUID",
  "draft": {
    "kind": "lesson",
    "body": "The compiler diagnostic identified the dependency mismatch.",
    "scope": {"repo": "/path/to/repo", "task_id": "*", "run_id": "*"},
    "claim_type": "self",
    "sensitivity": "local"
  }
}
```

Pass that object to `cairn create`. `cairn edit` additionally requires
`record_id` and `expected_version`, alongside the replacement `draft` and a new
`request_id`. Editing preserves the prior writer and bytes in the old version.

Capture evidence deliberately with `cairn capture-evidence`:

```json
{
  "request_id": "NEW_UUID",
  "repo": "/path/to/repo",
  "body": "Exact selected receipt or artifact bytes",
  "source": "Named source and revision; this is a label, not a fetch URL",
  "sensitivity": "local"
}
```

CLI evidence is testimony. Only an authenticated host's observed service channel
can create instrumented evidence. A matching digest establishes integrity of
captured bytes, not truth of the assertion they support.

Issue a scoped instruction with `cairn issue`:

```json
{
  "request_id": "NEW_UUID",
  "grant_id": "GRANT_UUID",
  "draft": {
    "kind": "instruction",
    "body": "Preserve user-authored files during this task.",
    "scope": {"repo": "/path/to/repo", "task_id": "*", "run_id": "*"},
    "claim_type": "self",
    "sensitivity": "local"
  },
  "mandatory": true,
  "requires_runtime": false,
  "policy_key": "preserve-user-files",
  "reason": "Record the operator's explicit workflow instruction"
}
```

`requires_runtime: false` requests delivery only. It does not assert the process
obeys the instruction. With `requires_runtime: true`, the current H0 wrapper
refuses a mandatory instruction because it has no runtime mediation.

Promote an independently authored note with `cairn promote`:

```json
{
  "request_id": "NEW_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "grant_id": "GRANT_UUID",
  "evidence_ids": ["EVIDENCE_UUID"],
  "reason": "Admit this scoped claim after reviewing its captured support"
}
```

The CLI operator cannot promote a note the same operator previously produced
or edited. Agent-originated records need a separately established host channel;
there is no CLI principal-override flag. `correct` uses the same record/version/
grant/reason fields plus a replacement `draft` and explicit evidence IDs.

Delegate authority with `cairn grant`:

```json
{
  "request_id": "NEW_UUID",
  "parent_id": "PARENT_GRANT_UUID",
  "principal": "service:reviewer",
  "repo": "/path/to/repo",
  "capabilities": ["promote", "correct"],
  "expires_at": "2026-10-01T00:00:00Z",
  "reason": "Authorize this service to review scoped claims"
}
```

The principal is the grant's recipient, not the caller identity. Child scope,
capabilities, and validity must fit the parent. Capabilities are `grant`,
`revoke`, `issue`, `promote`, `correct`, `retract`, `resolve`, and `redact`.
`redact` is reserved; its mutation path has not shipped.

`revoke-grant` takes `request_id`, target `grant_id`, authorizing `authority_id`,
`expected_version`, and `reason`. The authorizing grant must be an ancestor of
the target. Revocation invalidates descendants at subsequent operations.

Open a dispute with `cairn dispute`:

```json
{
  "request_id": "NEW_UUID",
  "record_ids": ["FIRST_RECORD_UUID", "SECOND_RECORD_UUID"],
  "reason": "These records give incompatible advice in the same scope"
}
```

`resolve` takes `request_id`, `conflict_id`, `expected_version`, `grant_id`, and
`reason`. It records the resolving actor and retains all original members.
Resolution does not silently retract a member. `retract` is an explicit later
transition with `request_id`, `record_id`, `expected_version`, `grant_id`,
`reason`, and `preview_id`. First run `cairn preview-retract RECORD_UUID` and
inspect its affected runs. Pass the returned token as `preview_id`. It expires
after one hour. A new exposure or version change requires a fresh preview.
The preview currently covers direct uses across all versions, up to 1,000 uses;
it does not claim transitive dependency coverage.

Compile with `cairn compile` (local binding only), or use `search` for the
operator-selected hosted binding:

```json
{
  "request_id": "NEW_UUID",
  "scope": {"repo": "/path/to/repo", "task_id": "current-task", "run_id": "current-run"},
  "query": "compiler dependency mismatch",
  "purpose": "context",
  "available_tokens": 32000
}
```

A caller cannot add a destination, principal, or instrumented flag to this
request. A host supplies destination configuration separately. Compile retries
return the same receipt while the semantic package is unchanged; changed source
state returns `STALE_PACKAGE` and requires a new request.

Query text is used transiently for lexical ranking. Semantic schema
`cairn.semantic/2` and `/3` retain `query` as `sha256:<hex>`, without raw query text.
The field name and CBOR shape stay compatible with historical v1 decoding;
old receipts still replay their original bytes and query text. Retained context
files likewise omit new raw task/query text. There is no raw-text opt-in.

`cairn explain RECEIPT_UUID` returns protected candidate versions, gate reasons,
ranking features, order and packing costs for the authenticated receipt owner.
Hidden destination records are excluded. Detail is separate from model rendering,
which exposes a fixed census with zero counts. Explanation version 0 means a
legacy receipt has no retained detail; version 1 covers the initial successful compilations; version 2 adds frozen
gate facts for [historical recompilation](currentness-and-replay.md),
including empty results. Hard compilation refusals do not yet have durable
explanations. `ESCALATION_BLOCKED` docket entries point to relevant current A
versions requested for consequential use. They are grouped by record/version;
editing or promoting the record removes that old version from current demand
without deleting its historical receipt.

Record an explicit citation with `cairn usage`:

```json
{
  "request_id": "NEW_UUID",
  "receipt_id": "RETRIEVAL_UUID",
  "record_id": "RECORD_UUID",
  "version": 2,
  "signal": "cited"
}
```

The version must have been exposed by that receipt. A citation remains testimony
about use and cannot grant authority or prove that memory caused success.

Exit codes: 0 command success, 2 invalid request/policy refusal, 3 not found,
4 conflict/stale request, 6 authority denial, 7 store or infrastructure failure.
A wrapped process's nonzero exit gives CLI exit 1; timeout gives 124 and
cancellation 130. The receipt records the observed child exit separately.
