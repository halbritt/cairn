# Inspect evidence impact

Use `cairn evidence-impact EVIDENCE_UUID` to find the retained claim versions
that cite captured evidence, versions related to those claims, and their recorded
exposures. This answers which records and retrievals need inspection when evidence
changes. It does not decide that a claim is false, automatically withdraw anything,
or prove that an exposed record affected a task result.

```sh
cairn evidence-impact EVIDENCE_UUID
cairn evidence-impact --record-offset 100 --use-offset 100 EVIDENCE_UUID
```

The result contains:

- `evidence`: captured identity/digest, witness and current retained-byte state.
  Inspection checks the bytes without creating a new check generation.
- `records`: exact `record_id`/`version`, the historical `version_class`, current
  version/lifecycle, and `direct_evidence_reference`. Indirect links appear in
  `via`, preserving their exact parent versions and relation types. `contradicts`
  is a recorded relationship, not a supporting premise.
- `uses`: one row per recorded record/version/receipt exposure, with purpose,
  task/run scope and `exposure_kind`. `index` means the original exposure was a
  preview; later expansion or citation is reported separately by
  [use-report](use-outcome-loop.md). Neither kind proves process execution.
- Independent `records_truncated`/`next_record_offset` and
  `uses_truncated`/`next_use_offset` fields. Each collection returns at most 100
  rows. Follow the returned offsets while either collection is truncated.

Traversal starts from stored evidence references and follows `derived_from`,
`specializes` and `contradicts` relations in reverse. Multiple paths to the same
version do not duplicate it or its exposures. A corrected version with independent
support is not included merely because it shares a logical record ID with an old
citation. Retraction does not erase the earlier version or its observed uses.
Replacement alone, similar wording and a source label add no dependency edges.
No source labels, note/evidence bodies or raw query text are returned.

Records sort by UUID/version. Uses sort by recorded time, receipt UUID, record
UUID and version. Both collections share one repeatable-read snapshot per call.
Separate page calls read current state; concurrent changes can shift offsets.
Pagination bounds response size, not all recursive query work. Inspection has a
30-second deadline, including for direct CLI callers. A failed query returns an
error rather than certifying a partial traversal as complete.

## Authenticated local inspection

A local-destination agent profile can POST the same request through the Unix API:

```json
{"evidence_id":"EVIDENCE_UUID","record_offset":0,"use_offset":0}
```

Pass that JSON to `cairn agent --token-file /path/to/local-profile.token evidence-impact`.
The server checks repository scope. Hosted profiles receive `AUTHORITY_DENIED`
before the evidence is inspected; this protected view is not an ordinary hosted
memory tool. The operator CLI and local API return the same report.

This read-only report is not a retraction/deletion preview token. Existing
[retraction guards](relations-and-demotion.md), evidence availability rules and
separate authority decisions still apply. Managed external artifacts, as-cited
spans, source-version predicates and undeclared derivations are outside its
coverage. [Verification](verification/evidence-impact-2026-09-09.md) records the
fixture behavior and remaining limits.
