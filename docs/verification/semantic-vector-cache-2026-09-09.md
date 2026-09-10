# Bounded note-vector reuse — 2026-09-09

Repeated semantic lookup now reuses exact note embeddings during the worker's
existing lifetime. Three consecutive live hosted searches before this change took
12.736, 12.745 and 12.911 seconds and returned identical candidate-score digests.
The worker re-embedded the eligible corpus each time, despite already retaining
the loaded model. This was a concrete cost encountered while inspecting retrieval.

## Change and preservation

The existing `Scorer` retains body-hash-keyed raw vectors for only its last
successfully scored request. It embeds each new query and uncached bodies, then
reconstructs the same complete matrix before normalization and scoring. Cache
entries contain neither note/query text nor record IDs. The cache is scoped to
one loaded model instance; no disk cache, database field or service is introduced.

Changed bodies miss the cache. Removed bodies leave it after the next successful
scoring request. Core eligibility still runs on every query before handing notes
to the worker, so retained vectors cannot add a candidate or bypass current scope,
destination, authority, lifecycle or applicability checks. When no worker call is
needed, its preceding cache may remain until the existing 30-second idle release.
This is bounded process retention, not guaranteed erasure from an allocator.

Cache hits still count toward the 128-chunk limit. At most 128 vectors are retained:
192 KiB of payload with the installed float32 model, plus map overhead. Temporary
matrices and the existing model consume additional memory. The scoring fingerprint
stays unchanged because the numerical algorithm and model are unchanged; worker
source hashes identify the implementation separately. Existing frozen-score replay
needs no new contract.

## Real-model comparison

The comparison loads the same prepared model in the retained baseline and candidate,
then alternates execution order for identical requests. Model initialization is
outside the measured synchronous `score` call. Twelve public documentation notes
and six queries come from the existing development workload. The pre-run target
was warm repeated scoring below half of baseline wall time with complete response
equality. No thread count, model, batch size or threshold was tuned.

| Case | Baseline seconds | Candidate seconds |
| --- | --- | --- |
| Initial 12-note embedding | 2.111 | 1.911 |
| Six warm queries | 1.852–2.294 | 0.034–0.044 |
| One changed note | 2.458 | 0.170 |
| One removed note | 2.543 | 0.070 |
| Reintroduced and reordered | 2.498 | 0.240 |
| Duplicate body | 2.293 | 0.046 |
| Initial 14,708-byte note | 3.528 | 4.229 |
| Repeated long note, new query | 4.056 | 0.050 |

All thirteen complete scoring responses matched, including every score and model
identity. The largest retained payload in this workload was 18,432 bytes. Six warm
pairs passed the target; these dependent pairs on a shared host are not latency
percentiles or independent task trials. One cold long-note pair was slower; cold
performance improvement is not claimed. Cache benefit ends when the worker exits
or the next eligible set no longer contains those bodies.

## Verification

The first numerical test failed because a repeated request embedded both notes
again. Six numerical tests now cover query-only reuse, metadata versus body changes,
removal/reintroduction, mixed cache hits/misses, duplicates and long notes, chunk
limits on hits, and refusal of zero embeddings without publishing partial cache.
They use a deterministic embedding substitute to check calls and numerical paths;
the separate real-model comparison supplies actual model parity evidence.

`make check`, `make test` (40 Python tests plus Go), and disposable PostgreSQL/race
integration passed. The integration included the actual candidate CPU streaming
worker: exact source pulls, long-note coverage, edits, stale-handle refusal,
superseded/local-only exclusion, unavailable fallback and worker shutdown all passed.
No client/API/store code, model, dependency or schema changed.

The performance history note was retrieved after the initial prototype and checked
against source. It reinforced retaining the current two-thread model and requiring
complete-response comparisons; this turn did not repeat the retired thread-count
experiment. Its earlier measurements are context, not proof of this change.

This improves the cost of repeated retrieval. No accepted answering-model task,
independent relevance judgment or sustained memory-task benefit is established.

## Decision and reproduction

The owner delegated routine implementation decisions under the active Cairn goal.
The validated Pincite release supplied repository precedence, test-first feedback,
numerical preservation and a measured performance target. One typed-evidence pass
satisfied the selected packet obligations; the decision receipt passed schema
validation and both citation loops closed. Final packet `pkt-f24c51266237ee6c`,
SHA-256 `f24c51266237ee6c08c78ee40a8547b8187f9ff459ae292bdec3b34164fefda2`.
The [metadata](semantic-vector-cache-2026-09-09.json) preserves exact versions,
measurements and local artifact paths/hashes. Operational note bodies stay outside Git.

Run `scripts/check_semantic_cache.py` with the prepared worker Python, a retained
baseline `semantic_rank.py`, an existing `--model-dir`, and a new `--output`
directory. It downloads nothing and uses no database. The six numerical unit tests
need the optional NumPy dependency; run them in that prepared environment.
For the real API check, set `CAIRN_SEMANTIC_STREAM_WORKER` to the candidate launcher
and `CAIRN_SEMANTIC_STREAM_REPORT` to a new output directory for `make test-integration`.
