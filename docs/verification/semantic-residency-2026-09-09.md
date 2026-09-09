# Bounded semantic worker reuse

The API can now reuse its local embedding model through
`serve --semantic-stream-command PATH`. Six paired API requests saved
0.56–0.83 seconds while preserving the returned source identities and complete
candidate-score digests. This is verified retrieval cost, not task benefit.

## Behavior and ownership

The existing one-shot `--semantic-command` remains supported. Streaming is a
separate, mutually exclusive host option. The installer prepares `worker-stream`
alongside `worker`; neither launcher changes a running service automatically.

The reusable child starts lazily and processes one request at a time. Busy
requests fall back immediately. The API owns the process group and pipes, bounds
each request to 20 seconds, and discards the child on cancellation or protocol
failure. The next request may start a fresh child; failed requests are not retried
automatically. Idle children exit after 30 seconds. Explicit owner shutdown waits
for cleanup. This adds no independent service or note-vector cache.

JSON-line envelopes bind each result to an internal request ID. Requests are
bounded to 8 MiB before the newline and responses to 64 KiB including it. Unknown
fields, missing results, wrong IDs, incomplete/oversized frames and changed
model identity within a child are refused. Core eligibility, destination checks,
candidate validation, ranking, budgets and receipt schemas remain unchanged.

The Python worker retains the initialized model/tokenizer and its fingerprint.
It re-embeds current eligible notes on every request and drops request/response
references before waiting. This does not promise physical allocator erasure.
Changing worker code, packages or model files requires an API restart for immediate
adoption; a surviving child continues to identify its loaded model until release.

## Verification

The initial Go check failed because the new interface did not yet exist. Final
process tests exercise reuse, idle release, repeated close, crash recovery,
malformed responses, model-identity changes, immediate busy refusal, caller and
owner cancellation, descendant termination and cancellation during a blocked
request write. The race detector passes. Review corrected a goroutine capture
before final checks; it was not an observed deployed failure.

Four Python framing tests cover lazy initialization, repeated current requests,
early hash/framing refusal and a bad second request without a second score/result.
The full Python suite has 34 tests. Go tests, disposable PostgreSQL/race integration,
vet and formatting pass. Targeted final transport tests include the blocked-write
case added after the full integration run. No embedding model is required for
these ordinary process/framing tests.

The optional real CPU/API check used the unchanged prepared model and packages.
One worker handled all seventeen development queries, preserved all fifteen
labelled answers within three previews, and found/pulled the 14,708-byte long
fixture's final guidance. No-answer neighbours remain possible. The same worker
then scored a revised note, refused its stale handle, excluded that note after
ordinary supersession and continued to exclude a separate local-only decision.
Graceful API shutdown removed the child; an unconfigured fixture API returned
labelled lexical fallback.

An initial fixture tried to change sensitivity through an ordinary edit and
correctly received `AUTHORITY_DENIED`. The fixture was corrected to use supported
A supersession and an independently local-only note. No runtime rule was relaxed.

## API comparison

Both modes used the same disposable database, identities, scope, query, budget
and source versions. The baseline used the retained installed one-shot worker;
the candidate used the new streaming worker. First condition alternated by pair.
The worker stayed warm throughout; first-request startup is not a claimed saving.

| Candidate notes | One-shot API seconds | Reusable API seconds |
| --- | --- | --- |
| 1, first pair | 0.903 | 0.301 |
| 3, first pair | 1.335 | 0.777 |
| 12, first pair | 3.103 | 2.419 |
| 12, second pair | 3.287 | 2.455 |
| 3, second pair | 1.169 | 0.556 |
| 1, second pair | 0.802 | 0.203 |

All six pairs had equal source/version/body hashes and discovery fingerprints,
including the digest of every candidate score. Two direct checks of the new
one-shot Python route also matched retained complete worker responses. The shared
host and two pairs per size limit generalization; these are not service latency
percentiles or throughput measurements. The preceding startup experiment observed
about 208 MiB resident memory while loaded. This run does not remeasure a fleet
memory budget. Model inference remains the larger cost on larger candidate sets.

## Evidence and limits

Raw fixture results are under `/tmp/cairn-semantic-residency-api-final`; logs and
source/environment hashes are indexed by the adjacent JSON. No protected
operational report or private note was benchmark input. The prior semantic lesson
was read before implementation and supplied the earlier measurements and limits.
Its influence on scope does not establish net memory benefit.

Doctrine packet `pkt-b3ffb522d1290195` has two typed evidence passes, a validated
decision receipt and citation closure. Four remaining consumer-interface
obligations are nonmaterial because the existing core ranker interface remains
unchanged and the new close operation has one explicit API owner. Local deployment
and exact-source CI are recorded separately after completion.
