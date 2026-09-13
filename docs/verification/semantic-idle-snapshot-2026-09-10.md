# Reuse vectors after releasing an idle model

A fresh scoring process restored a small passage-vector snapshot and completed
three document-scoring requests in 0.58–0.66 seconds, compared with 11.41–11.65
seconds without restoration. Every complete response matched, including model
identity, note versions/hashes and integer scores. The snapshot occupied 51,014
serialized bytes. This supports implementing reuse across idle worker release;
the API transport does not yet support it and nothing was installed.

## Observed need

An ordinary hosted-profile search began with no API-owned worker. It took 22.629
seconds and returned semantic results; an immediate repeat took 0.125 seconds
with the same complete score digest. The existing five-minute idle setting avoids
some cold starts, but the vector cache dies when the worker exits. The live child
occupied 224,836 KiB RSS when sampled afterward.

The existing [cold profile](semantic-deadline-2026-09-10.md) attributed nearly all
scoring time to inference. Retrieved current worker guidance led to that profile
and the rejected thread/batch experiments. Those unchanged experiments were not
repeated. The new candidate eliminates repeated passage inference after an idle
release. It keeps the model, tokenization, complete note coverage and arithmetic.

## Fixed comparison

Before scoring, the plan selected two current public repository documents:
`docs/semantic-discovery.md` and `docs/opencode-tools.md`. Their source hashes,
fixed query and candidate source were retained. These are development inputs;
they differ from the operational corpus behind the 22.629-second observation.

The scratch candidate serializes the existing cache as passage hashes and raw
float32 vectors, with model identity and a format tag. It retains no note/query
text or record IDs. A matching fresh scorer restores the vectors; a different
model identity recomputes them. Each successful score still replaces the cache
with exactly that request's passages. Both conditions run the same candidate;
one receives the seed snapshot and the other starts without it. The candidate
also serializes its outgoing snapshot in both conditions.

Each measurement starts a new process with the same prepared offline model,
packages and stripped worker environment. The first condition alternates across
three pairs. Wall time includes process launch, imports, model loading, scoring,
snapshot serialization and exit. GNU `time` records CPU and peak RSS. An embedding
wrapper counts the actual inputs passed to the unchanged model implementation.
Complete outputs from both conditions are retained.

| Pair | Without restored vectors | With restored vectors | Peak RSS without/with, KiB |
| --- | ---: | ---: | ---: |
| 1 | 11.409 s | 0.594 s | 222,900 / 196,848 |
| 2 | 11.410 s | 0.583 s | 222,904 / 196,724 |
| 3 | 11.653 s | 0.659 s | 222,784 / 196,860 |

All three pairs met the prospective target: restored latency below half the
paired baseline, exact complete responses, state below 600 KiB and peak RSS below
1.25 times baseline. Model input count fell from 25 to 1: only the query required
inference. One appended note recomputed one passage plus the query in 0.968 seconds,
versus 11.530 seconds cold, with identical output. Removing a note preserved scores
and dropped its absent passages. A deliberately mismatched snapshot model identity
forced all 25 inputs to be recomputed and returned the cold result.

These are dependent comparisons on one shared host and two selected documents.
They establish this candidate's behavior on those inputs, not service percentiles,
general model portability or downstream task value. Sampled worker RSS does not
measure a future API's retained state. The scratch evidence includes public test
vectors; no operational memory bodies were copied into the benchmark or Git.

## Implementation decision and remaining checks

Implement bounded snapshot retention in the existing streaming transport's memory
across **idle release only**. Keeping about 200 KiB of raw vectors can allow the
roughly 220 MiB loaded worker to exit. Keep model identity inside the worker-owned
format; the transport should enforce a byte cap without depending on vector layout.
Current candidate eligibility still precedes scoring, and snapshots cannot add
candidates or make old handles current.

The following remain required before installation:

- Verify actual idle child exit and restoration into a different child through
  the Go transport, with complete real-model results and retained-memory bounds.
- Discard saved state on transport failure, cancellation and owner shutdown;
  verify that failed exchanges cannot retain partial state or trigger retries.
- Preserve old workers that emit only `id` and `result`, and keep score output
  bounded independently from the optional snapshot. Choose an explicit upgrade
  path so an older API is not given an unsupported response field.
- Verify malformed snapshots, model mismatch, edited/removed/private candidates,
  and unchanged result/source identities through the normal API boundary.
- Keep snapshots out of receipts, model responses, persistent files and logs.
  Document the longer in-memory lifetime and lack of secure allocator erasure.

No disk cache, database schema, longer request deadline, new background service
or automatic task assessment is proposed. Increasing idle retention already
exists but keeps the full loaded model. Disk persistence would add a separate
retention/deletion contract; it is unnecessary for this candidate.

The [manifest](semantic-idle-snapshot-2026-09-10.json) records the measurements,
source identities and remaining limits. Scratch evidence is under
`/tmp/cairn-semantic-restart-20260910`; it is not a durable archive. Current installed
behavior remains CLI `208c1c8`, API `b171a8b` and worker `d3edaa2`.


Pincite packet `pkt-299df56f8e3a70cc` informed the work-count comparison and
preservation criteria. The decision receipt validates and both consumed packet
citation loops are closed. Eighteen residual obligations limit the experimental
conclusion: wider workload claims are unsupported, and lifecycle/compatibility
checks become material before deployment. The first receipt validation caught
missing verification dependencies/criteria; the corrected receipt explicitly
names them. No production test was run for this documentation-only checkpoint.
