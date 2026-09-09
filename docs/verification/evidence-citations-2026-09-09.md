# Precise supporting citation verification — 2026-09-09

A qualified claim can now identify the exact passage in a retained source. The
existing body/evidence pull flow returned that passage without searching through
the whole object in a disposable fixture. This verifies the capability; it does
not measure downstream task benefit.

The [contract](../evidence-citations.md) and [manifest](evidence-citations-2026-09-09.json)
describe the limits and retain source/log hashes. Base commit: `53b5c3d`.

## Observed behavior

- The initial precise-citation promotion failed before implementation.
- Promotion and correction accept exact full-source digests and multiple byte
  passages. Returned metadata drives the actual CLI/API evidence span pull.
- Correction and authorized scope expansion preserve the intended version
  boundaries. Historical recompilation retains the earlier citation and seal.
- Invalid, empty, overflowing and past-EOF ranges, duplicate sources, missing or
  mismatched digests and mixed reference forms refuse atomically. Same-intent
  retries succeed; changed successful intent conflicts.
- Replacing both source bytes and their stored digest does not revalidate an
  earlier citation. Context marks it divergent; consequential retrieval excludes
  an unsupported claim. Object checks and reference checks remain distinct.
- The former oversized-pull fixture replaced a source after citation, which now
  correctly fails earlier. It now captures a large source before promotion and
  still proves budget refusal without spending credit. A separate test covers
  source replacement.
- An actual clean `283142b` binary created schema-29 promotion, compile and cached
  pull records. The new binary migrated that disposable database to schema 30;
  promotion retry, cached body/evidence responses and historical recompilation
  remained exact. Fresh retrieval of the legacy link omitted citation metadata.

`make test-integration` passed with disposable PostgreSQL and race detection,
including actual CLI/API and MCP checks. `make test` passed Go and 30 Python
tests; `make check build` passed. No model calls or operational evidence fixtures
were used. The separate migration script remains at
`/tmp/cairn-citation-upgrade.py` with its log hash in the manifest.

## Review limits

Migration 030 adds nullable columns and does not manufacture earlier citation
facts. Existing omitted-field request intent and frozen receipt encodings remain
compatible. Go clients using positional literals for the expanded request types
must append the new optional field or use keyed literals. Previous binaries
reject the newer schema; deployment requires a backup.

Doctrine packet `pkt-40693dafb44d4d54` informed placement in the existing evidence
owner and preservation of historical behavior. The schema-validated decision
receipt is `/tmp/cairn-citation-span-decision.json`; citation consumption is
closed. Six unmet obligations are retained as nonmaterial in the manifest: four
concern a Go interface/dependency boundary not introduced here, one asks for
recurring-change history beyond this accepted missing feature, and one names a
no-change procedure whose alternative was considered explicitly. No material
obligation remains for this bounded implementation claim.
