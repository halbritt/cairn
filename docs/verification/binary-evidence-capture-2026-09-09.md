# Explicit binary evidence capture, 2026-09-09

Cairn's API can now capture arbitrary bytes through `body_base64` on its existing
evidence operation. Previously, that field was rejected and the wire contract
provided only a JSON text body, although retained binary evidence already had a
lossless inspection and pull representation. This closes the input gap without a
new storage backend, schema migration, native tool or authority path.

The failing authenticated test returned `INVALID_REQUEST` for the new field.
After implementation it verifies exact retained bytes and SHA-256, identical
retry identity, and a changed-byte refusal. A second test covers the exact decoded
1 MiB cap, malformed/noncanonical base64, conflicting source forms, over-limit
input, refusal without reserving the request ID, default local sensitivity and
cross-repository denial. The existing text capture test still covers maximum JSON
escaping. [Input contract](../evidence-refresh.md).

Review also identified a direct Go-call problem: invalid UTF-8 strings are not
losslessly represented by the JSON request hashing used for idempotency. The
failing regression confirms these bodies were accepted. `Body` now requires valid
UTF-8; binary Go callers must use `BodyBase64`. Invalid input refuses before the
mutation, including retries in that legacy form. Binary inspection/pull fixtures
now capture through the explicit representation, preserving their original byte
assertions. Previously stored evidence bytes and readers are unchanged.

## Verification and compatibility

- Targeted real PostgreSQL/API checks cover source bytes, retries and refusals.
- Actual hosted agent CLI capture runs with an invalid client DSN, then verifies
  operator inspection and a qualified hosted evidence pull. It covers direct
  operator CLI input too; existing ordinary attribution and destination rules apply.
- A text capture committed by installed `605be1a` retries unchanged with the new
  binary in a disposable store. Changed text still yields `IDEMPOTENCY_CONFLICT`.
- Static checks, Go tests, 40 Python tests and full disposable PostgreSQL/race
  integration pass. Existing stdio MCP workflows pass. Native adapter code did not
  change; no answering-model task was run.

Canonical base64 remains part of request identity; switching encodings under a
committed UUID is a different request, even for equivalent decoded bytes. The
new field is omitted from text-only serialization to retain earlier text request
hashes. Old APIs reject the new field. The API/agent CLI keeps its existing 8 MiB
encoded envelope and 1 MiB decoded cap; operator capture retains its separate
128 KiB encoded envelope. Base64 output is used only when retained bytes are not
valid UTF-8. It is not a claim that a model can interpret every binary format.

## Task meaning and remaining work

This is a practical storage capability and a request-identity correction, not a
completed memory-value trial. Discovery came from current source inspection.
The saved evidence guide was retrieved afterward to preserve applicable capture,
citation and pull constraints; it did not cause the discovery. The earlier saved
priority note supported returning to practical retrieval/storage work, but current
roadmap and conversation context already carried that direction.

L4 remains partial for managed large artifacts, upstream freshness and broader
source/dependent lifecycle. Capturing bytes does not establish source truth,
relevance, currentness, promotion or task benefit. Selected source bodies and raw
operational memory remain outside Git. [Metadata](binary-evidence-capture-2026-09-09.json)
retains verification and decision provenance. Installation is recorded separately.
