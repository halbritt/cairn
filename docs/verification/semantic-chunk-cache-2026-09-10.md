# Reuse unchanged passages after a note edit

Three appends to a long documentation note took 0.24–0.28 seconds to score with
passage reuse, compared with 4.15–4.53 seconds with the existing whole-note cache.
All eight paired requests returned identical complete scoring responses, including
model identity, source identities and integer scores. This is reduced retrieval
cost on a development workload; no answering-model task or net memory benefit was
measured.

## Change and preservation

`scripts/semantic_rank.py` now keys its existing in-process vector cache by the
SHA-256 of each exact decoded passage supplied to inference. A localized edit can
reuse unchanged windows. Identical passages within a request share inference.
Every occurrence still counts toward the 128-window limit, and scores reconstruct
the original occurrence order before normalization and per-note maximum selection.
The model, tokenizer, single-input batches and numerical scoring settings remain.

The cache retains only passage hashes and copied raw vectors from the last
successful request, at most 192 KiB of float32 vector payload plus map overhead.
It retains no query hashes, text or record IDs. Absent passages are dropped;
failed scoring does not publish a partial cache. The existing worker owns all
retention and still exits on idle expiry, failure or shutdown. There is no disk
cache. Tokenization now runs for every supplied body, including unchanged notes.

Eligibility still precedes scoring. A reused vector cannot add a candidate or
make an old handle current. No API schema, database migration, source-seal format,
worker protocol or runtime configuration changes. The unchanged model fingerprint
identifies the same scoring inputs/settings; it does not identify worker source.
Updating this optimization requires replacing the prepared worker script and
restarting the API so a new child loads it.

## Comparison and limits

Two real streaming processes used the same offline FastEmbed/BGE-small model and
stripped environment. Each received the same public `docs/semantic-discovery.md`
body plus one existing retrieval-quality fixture. Three selected verification
sentences were appended in sequence. Execution order alternated by request.
Timers covered request write through complete response read; process RSS was read
afterward. The prospective target required append calls below half baseline time,
complete response equality, warm scoring below 0.25 seconds and RSS below 300 MiB.

| Request | Whole-note cache | Passage cache |
| --- | ---: | ---: |
| Cold | 4.793 s | 5.216 s |
| Unchanged follow-up | 0.050 s | 0.064 s |
| First append | 4.281 s | 0.236 s |
| Second append | 4.525 s | 0.281 s |
| Third append | 4.152 s | 0.265 s |

Removal, reintroduction and duplicate-note requests also preserved complete
responses. Both workers remained below 218 MiB sampled RSS. A separate synchronous
profile of the first append counted ten model inputs before and two after, with
embedding wall time falling from 4.073 to 0.220 seconds and identical output hashes.
This supports avoiding repeated inference as the mechanism.

The three append pairs are dependent changes to a selected development document,
not independent replicated trials or a user-traffic distribution. The shared host
was not CPU-pinned. Cold scoring was slower in this pair, and unchanged warm
scoring has added tokenization work. Neither cold improvement nor a universal
latency guarantee is claimed. Cross-restart persistence remains deferred; this
change only improves work while the existing worker survives. Inserting or deleting
text earlier in a note can shift subsequent token windows and invalidate them;
a short edited note may still require embedding its entire passage.

## Verification

The append test failed against the old worker, then passed. Thirteen tests in the
prepared numerical environment cover framing, score preservation, changed inputs,
sharing, eviction, 128-vector retention, copy ownership and failed scoring. The
full disposable PostgreSQL/race integration passed with the actual candidate
semantic worker, including current edits/pulls, private-note exclusion, long-note
coverage and unconfigured fallback. Python checks and `make check` also passed.

Initial `make test` encountered `text file busy` when launching a temporary Go
test fixture for the cold deadline check. The unchanged isolated check and full
race integration passed afterward. Its root cause remains unestablished; no
retry or timing workaround was added. The initial failure remains in the evidence.

[Metadata](semantic-chunk-cache-2026-09-10.json) retains source hashes, raw paired
measurements, environment, check pointers and doctrine packet
`pkt-f10dd26b16b60102`. Thirteen remaining obligations concern broader benchmark
representativeness and structural claims outside this change. The decision receipt
and three citation closures validate. Scratch evidence is retained under
`/tmp/cairn-semantic-chunks-20260910/`.

The current Cairn semantic lesson recovered the earlier profile and cautioned
against repeating unchanged thread/batch experiments. Source inspection then
identified whole-body invalidation. That is observed guidance use; incremental
memory contribution and net task value remain uncertain.


## Installation and ordinary retrieval

Feature commit `d3edaa29bf82fce3572aebf786b69439b37869ca` passed CI run
34545885103, with both jobs and all steps checked. The prepared worker script
was replaced after retaining the old file; its installed SHA-256 is
`2d85715d25ea9dbc5deb86d37e3ad9e640ba11dfe08f4e920a52453e2aab240f`.
API PID 4138687 loaded the new worker. CLI/API remain clean `b171a8b`, with
unchanged executables, harness configuration, identities, launchers and host
five-minute idle setting. PostgreSQL remained PID 163669. All 81 retained note
versions matched before/after installation.

Two ordinary hosted-profile semantic searches against the installed API returned
ready results in 8.153 and 0.073 seconds. They used the same child and returned the
same complete score digest, source seal and selected versions. This verifies the
installed path; it is not a paired comparison against the earlier cache. These
queries precede the guidance revision below.

The saved semantic worker lesson advanced v9→v10 to describe passage reuse,
shifted-window limits and installed source provenance. The edit preserved all
unrelated text and metadata; a fresh pull and retained v9 body were verified.
That selected guidance revision intentionally adds one note version after the
installation inventory. The raw note bodies remain outside Git.
