# Semantic startup cost on small candidate sets

Keeping the model loaded saves enough time on small candidate sets to justify
an API-owned resident-worker candidate. The running service still uses one-shot
workers. This experiment verifies valid-input scoring and worker timing; it
does not qualify the scratch protocol for deployment or establish task benefit.

## Why revisit initialization

The existing semantic performance lesson pointed to the earlier
[batching profile](semantic-batching-2026-09-09.md), where inference dominated.
That report was checked before running this experiment. Recent kind filtering
can produce much smaller candidate sets, so the question here is whether imports
and model setup become material with one or three notes. The negative
[thread-count experiment](semantic-threads-2026-09-09.md) was not repeated.

The source and installed worker both have SHA-256
`d042326bc0f1bea838434b354fa21484222277092e6db1a7c1f475212e410954`.
All requests use the same prepared FastEmbed/BGE-small CPU environment, two
threads, single-input batching, stripped environment and public documentation
fixtures. The query is “Where does Cairn keep its data?” with the first one,
three or twelve fixture notes. These are selected development inputs, not a
sample of operational query frequency. No private note is benchmark input.

## Measurements

Disjoint timers inserted at existing statement boundaries measured request/hash
preparation, imports, model/tokenizer setup, tokenization, embedding and output.
An AST check confirms the original main statements survive unchanged after
removing the instrumentation. Six paired baseline/instrumented runs produced
identical complete JSON responses, including model identity and every score.

Imports and model/tokenizer setup took 0.51–0.63 seconds. Their share of the
instrumented worker wall time was 51–57% for one note, 35–37% for three notes,
and about 18% for twelve. This crossed the recorded investigation trigger of
250 ms and 30% for small sets. That trigger selects an experiment; it does not
define usefulness or authorize deployment.

A scratch prototype moved invariant package/model initialization to process
startup and retained the original per-request validation, chunking and scoring.
It retained no note-vector cache. Two prototype processes each completed six
requests with exact baseline responses. Their separated timing blocks suggested
larger savings, but shared-host drift made that comparison insufficient.

A final comparison interleaved one-shot baseline and already-started worker
requests, alternating which ran first. One cold priming request is recorded
separately. All six pairs preserved complete responses:

| Candidate notes | One-shot seconds, two pairs | Warm seconds, same pairs |
| --- | --- | --- |
| 1 | 0.734 / 0.758 | 0.180 / 0.170 |
| 3 | 1.104 / 1.136 | 0.559 / 0.558 |
| 12 | 2.481 / 2.434 | 2.142 / 1.857 |

Observed savings were 0.34–0.58 seconds per warm request. The resident process
peaked at 212,756 KiB RSS, about 208 MiB. That memory remains occupied while the
process lives; a cold first request still pays initialization. These are two
pairs per size on a shared host, not latency percentiles or a throughput test.
The twelve-note result also illustrates the remaining inference cost.

## Decision and unfinished work

Develop a bounded, optional resident-model route owned by the API. Preserve the
existing one-shot route for custom workers. Verify response framing, cold startup,
one active request, busy fallback, per-request deadline, failed-child cleanup,
idle/shutdown release, model identity over the process lifetime, and current
eligibility after note edits. Repeat an API-level timing comparison before
installing it. Do not add a note-vector cache or another independent service.

The scratch worker is not that implementation. Successful serial requests and
EOF exit do not verify its failure paths, retention behavior or API lifecycle.
Production worker, launcher, API PID 3917563 and store PID 163669 remain unchanged;
both services were active after the experiment. No production code or database
changed, so another full integration suite was not run for this investigation.

The saved lesson helped recover the earlier measurement and narrow this task to
the changed workload. That is observed influence on the investigation, without
an estimate of net memory benefit. The existing lesson is extended with these
conditional results, preserving the earlier observations.

Raw inputs, sources, phase data, prototype responses, paired results and resource
measurements remain under `/tmp/cairn-semantic-startup-small`; the adjacent JSON
records hashes. Doctrine packet `pkt-818118d2c022b1b9` supports measurement before
intervention and explicit resource ownership. The decision receipt and citation
closure validate; 25 remaining obligations are classified as nonmaterial to this
experiment, with lifecycle and compatibility checks explicitly required before
deployment. No broader implementation acceptance is inferred from the packet.
