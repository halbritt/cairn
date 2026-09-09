# Evidence capture transport limit

The store accepted evidence bodies up to 1 MiB, but three transport boundaries
restricted authenticated capture to a 128 KiB JSON envelope. The agent CLI,
`localapi.Client.Call` and server decoder each imposed that cap independently.
An actual disposable client/API check failed before reaching the store when a
valid 1 MiB decoded source needed sixfold JSON escaping.

Evidence capture now has an 8 MiB encoded envelope at all three boundaries,
with one shared `localapi.RequestBodyLimit` definition. This provides room for
the existing 1 MiB source plus escaping and bounded metadata. Other operations
retain their 128 KiB cap and existing error text. The decoded store limit, source
bytes, retry identity, repository/sensitivity checks and retrieval budgets are
unchanged. This does not add streaming or managed large-object storage.

## Checks and outcome

- The initial disposable API test failed with `request exceeds 128 KiB`.
- The repaired client/API captured exactly 1,048,576 NUL bytes, each represented
  by six bytes in JSON. The returned SHA-256 and complete stored source matched;
  an identical retry returned the same evidence ID.
- A decoded source of 1,048,577 bytes was refused. A different valid body with
  that refused request ID then succeeded, demonstrating that the refusal did not
  reserve capture intent.
- Direct authenticated HTTP requests bypassed client checks. An evidence envelope
  exceeding 8 MiB was refused before mutation, including excess trailing whitespace
  after an otherwise valid object. A non-evidence request above 128 KiB was also
  refused. Different valid requests could reuse both refused IDs.
- The client independently rejected an evidence request above 8 MiB and retained
  the other-operation 128 KiB rejection. The agent CLI captured the exact escaped
  1 MiB source twice with stable identity while its database address was unusable.
- The full disposable PostgreSQL integration suite with race detection, all Go
  tests, 30 Python tests, vet and build passed. No model calls or operational large
  fixture captures were made.

The repair admits more encoded bytes into an authenticated local request, up to
8 MiB in transport buffers; the actual stored body remains at most 1 MiB. A larger
envelope for every operation was unnecessary. Keeping the old cap would leave
most of the existing evidence range inaccessible to ordinary clients. Streaming
and a new storage backend would add mechanisms beyond this mismatch.

This completes access to the existing inline capture range. It does not establish
source freshness, source truth, better task outcomes or the remaining L4 lifecycle
requirements. A recalled evidence-span procedure was inspected during the review;
current code confirmed that pull budgets and full-source identity remain separate
from capture limits. The review does not isolate a causal benefit from that recall.

The [manifest](evidence-limit-2026-09-09.json) retains source/check hashes and
decision provenance. Deployment is recorded separately when completed.
