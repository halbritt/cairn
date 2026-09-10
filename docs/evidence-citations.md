# Precise supporting citations

## Ordinary notes

An ordinary A note can cite explicitly captured sources without promotion.
First [capture the selected source](evidence-refresh.md#capture-a-selected-file)
and retain its evidence ID and SHA-256. Then send JSON to `cairn agent cite`
with the same provisioned connection flags used for capture:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "NOTE_UUID",
  "expected_version": 1,
  "repo": "/path/to/repo",
  "evidence_citations": [
    {
      "evidence_id": "CAPTURED_EVIDENCE_UUID",
      "expected_sha256": "FULL_SOURCE_SHA256",
      "spans": [{"offset": 1200, "length": 240}]
    }
  ]
}
```

Use actual IDs, the current note version, repository, source digest and byte
ranges. The operator equivalent is `cairn cite`. MCP and native OpenCode use
`cairn_edit` with the same fields except `repo`, which comes from their configured
repository. Supply exactly one of `body`, `draft` or `evidence_citations`.
The native tools use already captured sources; source capture remains a separate
CLI/API operation.

Citation updates create a new retained note version, preserving its body, scope,
pins, relations, sensitivity and attribution fields. The authenticated editor is
stamped as its writer. The supplied list replaces the current references; an
explicit `[]` clears them. A missing or null list refuses. Earlier versions keep
their references. Ordinary text and full-draft edits preserve existing citations,
including degraded ones; editing text does not revalidate its sources. Use another
citation update when the sources or supporting passages should change.

The update is atomic and uses compare-and-swap and request idempotency. Refused
source checks do not create a new version or reserve a successful request ID.
An exact retry returns its original revision; changed intent under the same ID
refuses. A version conflict needs a fresh pull and reconciliation. Source capture
and note citation are separate transactions: a failed citation leaves the captured
object available for a corrected request.

Sources must verify, belong to the same repository and satisfy the note's
sensitivity. Shareable notes cannot cite local-only sources. A fresh search and
body pull return citation metadata in `selection.evidence`; use the original
handle with `cairn_pull_evidence` to inspect an exact source or passage. Older
handles become stale after citation changes. Evidence status, pull budgets,
destination checks and historical receipt semantics apply as usual. Degraded
ordinary sources remain labelled in advisory retrieval; unavailable bytes refuse
pull. Citations do not make A notes eligible for consequential planning.

These are the writer's asserted supporting references, not independent
qualification, proof of correctness or an upstream freshness check. Current note
pulls expose current references; retained receipts and local evidence-impact
inspection can show earlier cited versions. The ordinary `history` command
continues to return note body/history metadata, not source bodies or citations.

Upgrade the CLI, running API and native adapter together before using this path.
Older APIs do not implement `cite`; older edit implementations can drop current
A references and older compilers omit them. Do not mix old store writers with
cited ordinary notes. There is no database migration or new semantic payload
shape; existing qualified references and frozen receipts keep their behavior.
[Verification](verification/ordinary-citations-2026-09-09.md).

## Qualified claims

A qualified claim can retain the exact passages that supported its version.
`cairn promote` and `cairn correct` accept optional `evidence_citations` in their
existing JSON requests. Use this instead of `evidence_ids`:

```json
{
  "evidence_citations": [
    {
      "evidence_id": "CAPTURED_EVIDENCE_UUID",
      "expected_sha256": "FULL_SOURCE_SHA256",
      "spans": [{"offset": 1200, "length": 240}]
    }
  ]
}
```

This is the citation portion of a request; the existing request ID, record ID,
expected version, grant and reason fields remain required, plus the corrected
draft for `correct`. These are qualified operations through the core or direct
operator CLI. Hosted agent tools do not acquire promotion authority.

Each request can cite 1–32 distinct captured sources, each with up to 32 optional
byte ranges. Supply the source's complete lowercase SHA-256. An omitted or empty
`spans` list cites the whole source. Offsets count bytes from zero; length must be
positive and every range must fit entirely inside the captured bytes. Citation
ranges do not clip at EOF. Split UTF-8 ranges are valid; subsequent pulls return
their bytes as base64 when needed. Range order is retained, and overlapping ranges
are allowed. Invalid ranges, source mismatches and mixed nonempty `evidence_ids`
and `evidence_citations` refuse the transaction without advancing the claim or
caching a successful request. Identical successful retries retain their result;
changed citation intent needs a new request UUID.

New references retain a full-source digest even when the caller uses the existing
`evidence_ids` form. The source must verify, match the claim's repository and meet
its sensitivity requirements at linking time. These references have the existing
`supports` relation; no new evidence sufficiency or qualification policy is added.
Authorized scope expansion carries the same passages into the new claim version.
Correction supplies its own replacement support, preserving the earlier version.

Returned supporting evidence includes `citation.sha256`, `citation.relation` and
optional `citation.spans`. Its top-level `sha256` identifies the cited full source.
After pulling an indexed claim, pass a saved range as `span` to
`cairn_pull_evidence`, or use `agent pull-evidence --offset N --length N`, retaining
the indexed receipt, handle, evidence ID and full-source digest. The response
carries the citation metadata and a separate checksum for the returned bytes.
All existing scope, destination, currentness, credit and encoded-byte limits apply;
more citation metadata also consumes those budgets. A long cited passage may still
need smaller explicit pulls.

If the evidence object's current digest differs from the cited digest, that
reference is divergent even when the object's own byte check passes. Object-level
`check-evidence` describes the captured object; reference-level availability also
checks the citation-time identity. Historical recompilation uses frozen earlier
facts and does not reinterpret a prior receipt using today's source state.

Migration 030 adds nullable metadata without backfilling older links. On an older
reference, absent `citation` means its citation-time metadata was not retained;
its existing digest and availability behavior remain. Standalone capture and
evidence inspection also omit `citation` because they do not select a citing
claim. Old request hashes, cached responses and historical receipts remain
compatible after upgrade. Back up before migrating. Older binaries reject the
newer schema, so replacing the executable alone is not a schema rollback.

This makes retained support easier to locate and compare. It does not establish
that the passage justifies the claim, that an external source is current, or that
memory improved a task. Managed artifacts above the inline limit, broader source
relations and dependent qualification remain open.
