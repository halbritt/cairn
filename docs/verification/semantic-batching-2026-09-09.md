# Lower-cost local semantic inference

The local CPU worker now embeds one query or passage per batch. It keeps the
same model, chunking, cosine arithmetic, two-thread limit and one-shot process
boundary. Batch size is now explicit in the scoring fingerprint. New requests
therefore identify the new configuration; old receipts retain their recorded
model identity and frozen scores.

## Investigation

The earlier API comparison measured 4–6 seconds per semantic query. A fresh
three-run baseline on twelve public documentation passages took 5.52, 5.51 and
5.28 seconds with identical responses. A separate cProfile run attributed
3.60 seconds to ONNX inference, about 0.50 seconds to imports and 0.14 seconds to
model initialization. These are cumulative profiler observations from one run,
not additive wall-time measurements from the ordinary runs. Model loading was
not the principal cost in that sample.

The installed FastEmbed 0.8.0 implementation pads inputs to the longest member
of each batch. A bounded probe of batch sizes 1, 4 and 8 took 2.57, 4.15 and
4.53 seconds respectively for the same query and produced identical integer
scores. This motivated a full comparison, rather than a persistent worker or
embedding cache. No new process lifetime, cache retention or service was added.

## Comparison contract

The reusable [worker comparison](../../scripts/compare_semantic_workers.py)
executes baseline and candidate scripts with the same prepared Python environment,
model and stripped environment. It alternates which condition runs first, applies
a 20-second worker deadline, and records wall time, user/system CPU time, peak RSS,
exit status and every source-bound integer score. The response model fingerprint
may differ because the candidate now includes batch size.

The workload contains the existing seventeen documentation queries over twelve
passages, one long-note case, a 64-note case made from repeated public passage
bodies, and two repeated storage queries. Seventeen queries include two no-answer
controls. These are development fixtures, not independent real-task acceptance.
The one long note places synthetic credential-location guidance after introductory
text; no operational notes, credentials or model API calls enter this comparison.

An exploratory paired run completed both conditions for twenty cases with exact
responses. The 64-note baseline timed out while its candidate finished in 10.23
seconds, so that pair cannot establish score equality. Across the seventeen
original queries, median wall time was 4.86 versus 3.11 seconds, median user CPU
7.79 versus 6.10 seconds and median peak RSS 330,856 versus 203,608 KiB.
The median paired speedup was 1.73x. The exploratory twofold target was not met
across that workload. The smaller reduction still justifies this limited batching
change; no broader optimization is claimed.

## Final-source results

The reusable command repeated all 21 cases against the final candidate source.
All twenty pairs that completed had identical source-bound integer scores. The
model fingerprint changed as intended to include the batch setting.

| Median over the original 17 queries | Batch 16 baseline | Batch 1 candidate |
| --- | ---: | ---: |
| Worker wall time | 5.22 s | 3.14 s |
| User CPU time | 8.22 s | 6.14 s |
| System CPU time | 0.17 s | 0.13 s |
| Peak RSS | 330,864 KiB | 203,624 KiB |

The median paired speedup was 1.68x. The 64-note baseline again timed out; the
candidate completed in 18.39 seconds, so no exact-score or completed-pair speedup
is claimed for that case. This is near the existing deadline and shows limited
headroom under load. The long-note pair took 6.04 versus 5.52 seconds. Latency
varied between runs on this shared host; these are observed worker costs, not
service percentiles or a throughput guarantee.

## Preservation and limits

Core scope, destination, eligibility, budget packing, source hashes, handle
validation and historical recompilation are unchanged. The worker still rejects
oversized queries, note sets and windows; the Go adapter still enforces one
worker, cancellation, bounded output and labelled lexical fallback. No note
vectors survive a worker invocation. A faster response does not establish a
better answer, calibrated confidence or downstream task benefit. Exact-score
comparisons establish the observed inputs only, not all possible floating-point
results or universal speedups.

The final worker passed `make test-integration` with the optional actual CPU/API
check, `make test` (all Go packages and 26 Python tests), and `make check`.
The API retained all fifteen labelled answers within three previews, exact storage
and long-note pulls, and labelled lexical fallback after its fixture scorer was
disabled. These checks used disposable PostgreSQL and no hosted model calls.
Source, logs and comparison hashes are retained in the adjacent JSON report.

## Local deployment

The installed worker was atomically replaced with the source from clean commit
`4bfe53a`, retaining the previous script privately. The API binary/process,
PostgreSQL, launcher, model files and dependency environment were unchanged.
Each subsequent request starts the newly installed script.

Two hosted-profile queries for the same storage question took 11.64 and 11.16
seconds before installation, then 8.99 and 9.35 seconds afterward. All four returned
identical indexed note versions and body hashes; the current storage note v2 was
pulled and its exact body hash verified. New responses carried the expected batch
fingerprint. These sequential live observations show a smaller gain than the
public fixture and are not a service latency distribution. Raw hosted responses
remain outside Git; the adjacent metadata records timings and deployment hashes.

To compare another worker change, retain the baseline source before editing and
use the model directory printed in the prepared `semantic/worker` launcher:

```sh
git show 19fbfb7:scripts/semantic_rank.py > /tmp/cairn-before-batching.py
python3 scripts/compare_semantic_workers.py \
  --python "$HOME/.local/share/cairn/semantic/venv/bin/python" \
  --model-dir "$semantic_model_dir" \
  --baseline /tmp/cairn-before-batching.py \
  --candidate scripts/semantic_rank.py \
  --output /tmp/cairn-worker-comparison-new
```

Set `semantic_model_dir` to that prepared model directory first. Each output
directory must be new. The comparison uses GNU `time` and `timeout` on Linux and
never edits the supplied workers or running service.
