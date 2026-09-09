# Semantic worker thread comparison

Keep the installed two-thread worker. A one-thread candidate preserved the
source-bound scores but missed the prospective latency target. No worker,
launcher, model, package, API or storage change was deployed.

## Question and method

The preceding batching change left two measured hosted queries near nine seconds.
Its profile attributed most sampled time to ONNX inference. This experiment
asked whether setting ONNX thread counts to one could reduce latency without adding a cache
or persistent process.

Before running, the decision plan required at least a 20% reduction in median
paired wall time over the seventeen original documentation queries, exact
integer scores for completed pairs, no added timeout and no material memory
regression. The comparison used the existing twenty-one cases: seventeen queries
on twelve public passages, a long note, a 64-note case and two repeated storage
queries. They are development fixtures, not independent task acceptance.

The baseline is the unchanged worker at `fec0f2c`. The temporary candidate uses
one thread instead of two and includes that setting in its model fingerprint.
The prepared model, package environment, single-input batch size, query text,
passages, chunking and arithmetic remain the same. Inspection of installed
FastEmbed 0.8.0 confirmed that its `threads` argument sets both ONNX intra-op and
inter-op counts. Other library threads were not counted or globally capped. This
does not change Cairn's one-worker-at-a-time API limit.

The existing comparison script alternates the first condition across cases and
measures each complete one-shot process using GNU `time`, with a twenty-second
worker deadline. Wall time includes process launch, imports, model loading and
scoring. CPU and peak RSS are per process. The host was shared and not CPU-pinned;
this is a bounded comparison, not a service percentile or throughput estimate.
The script's plan metadata was corrected to derive worker settings from the
supplied source files instead of unconditionally claiming both used two threads.

## Results

All twenty-one pairs completed with identical source-bound integer scores.
Neither condition timed out. Over the original seventeen queries:

| Metric | Two threads | One thread |
| --- | ---: | ---: |
| Median wall time | 3.23 s | 3.12 s |
| Median user CPU | 6.03 s | 3.75 s |
| Median system CPU | 0.12 s | 0.11 s |
| Median peak RSS | 203,784 KiB | 203,844 KiB |

The median **paired** speedup was 0.972x, missing the target of at least 1.25x.
The ratio of separately aggregated medians is different; it does not establish
an improvement for a typical paired query. Individual timings varied in both
directions. The long-note pair took 4.01 versus 4.11 seconds, and the 64-note
pair took 10.68 versus 11.31 seconds. Repeated storage queries took 2.38/2.36
seconds with two threads and 2.49/2.49 seconds with one.

One thread reduced CPU consumption in this sample, but the selected experiment
was about interactive latency. Retain two threads and reconsider this tradeoff
only if CPU contention or a different workload becomes an actual constraint.
No further thread-count tuning or new runtime option was added. The negative
result does not establish that two threads are optimal for every host or workload.

## Evidence and limits

The completed comparison is retained under `/tmp/cairn-semantic-threads`, with
its pre-run decision plan, both worker sources, request/result pairs, resource
measurements and summary. The adjacent JSON records hashes and metric values.
The comparison log is `/tmp/cairn-semantic-threads-comparison.log`.

The full comparison exercises the metadata correction; the Python script also
passed a syntax check. No store code or deployed behavior changed, so another
PostgreSQL integration run was not needed for this experiment. The prior
implementation's checks remain separate evidence. No answering model, protected
operational note or private trace was used as benchmark input, and no downstream
memory benefit is claimed.
