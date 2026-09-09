# Precise supporting citations

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
