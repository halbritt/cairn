# Semantic cold-workload cost and deadline — 2026-09-10

Cairn now permits twenty-five seconds for either semantic worker transport,
up from twenty. The current corpus repeatedly took 21–24 seconds to score;
the earlier budget discarded that work and returned lexical fallback. This is
an availability tradeoff, not faster inference: it permits up to five seconds
more work and waiting. Native OpenCode still has a thirty-second outer timeout,
and the API client and server retain thirty-five seconds.

## Investigation and rejected optimization

Ordinary hosted retrieval retained the current twenty eligible notes, including
worker guidance v6: 58,353 body bytes and fifty model chunks. That fits the
existing limits of sixty-four notes, one MiB and 128 chunks. The retrieved worker
guidance and its linked earlier experiments were consulted before intervention.

Fresh scorer setup took 0.433 seconds. A separate profiled score call took
20.412 seconds, with 20.321 cumulative seconds inside ONNX inference. Token
encoding, decoding and score aggregation were small in that observation.
Profiling distinguishes inference cost from startup and chunk-limit refusal;
it does not establish the exact cause of every previous fallback. A subsequent
actual API request returned `DEGRADED_NO_EMBEDDINGS` / `unavailable` after
20.056 seconds.

The earlier batching experiment used shorter documentation passages. On this
changed workload, thirty of fifty chunks have at least 386 encoded tokens.
One fixed candidate sorted inputs by token length, embedded batches of four,
and restored original order before scoring. All notes and chunks remained.
The plan required identical source-bound integer scores, at least 1.25× median
paired speedup, every candidate under twenty seconds, and peak RSS below 1.5×
baseline. No parameter or input was tuned after scoring.

| Pair | Baseline seconds | Candidate seconds | Baseline peak KiB | Candidate peak KiB |
| --- | ---: | ---: | ---: | ---: |
| 1 | 23.889 | 21.400 | 225,216 | 311,740 |
| 2 | 21.051 | 20.948 | 225,216 | 312,420 |
| 3 | 21.904 | 21.612 | 225,260 | 312,172 |

All three complete score/source comparisons matched; the fingerprint changed
because batch size is part of its identity. Median paired speedup was **1.014×**,
with all candidates still above twenty seconds and about 39% higher peak memory.
The candidate is rejected. The installed Python worker, model, two-thread limit,
single-input batching and numerical fingerprint remain unchanged.

These were sequential fresh processes on one shared host, alternating the first
condition. Wall time includes process startup, imports, model loading and scoring;
GNU `time` separately records CPU and peak RSS. The interpreter and prepared model
were fixed. Three dependent pairs on one corpus are not service percentiles or
a general workload distribution. Their purpose is to reject this particular
optimization, not to prove a universal best batching strategy.

## Separate availability adjustment

The two transports now share a twenty-five-second `workerTimeout`. No caller
configuration or new cache is introduced. The earlier caller deadline still wins.
Busy requests do not queue; cancellation, output limits, process-group cleanup,
thirty-second idle release, model identity checks and complete-input scoring
retain their existing behavior.

A regression sends a response after twenty-one seconds through the actual
one-shot and streaming transports. Both failed under the old budget; both now
complete, and the stream accepts the following request. The semantic race suite
also verifies cancellation, busy refusal, idle release, malformed output, model
changes, credential exclusion and child cleanup. Disposable PostgreSQL integration,
all Go package race tests, forty Python tests and static checks passed.

With the actual unchanged installed model and the production worker environment,
the new one-shot transport completed the retained corpus in **20.294 seconds**;
the stream completed it in **20.118 seconds**, then **0.033 seconds** for the
same request. Every complete result matched the profiled baseline, including
model identity, all source versions/hashes and integer scores. The owned worker
processes were closed after those probes.

This adjustment does not guarantee completion for larger corpora or a loaded
host. Requests that still exceed the budget return the same labelled fallback.
It addresses the observed interval without reducing source coverage or introducing
a durable embedding cache. Deployment and real-model transport observations are
recorded in the accompanying metadata and subsequent implementation history.

Validated doctrine packet `pkt-ba3bab962ec3ebec` supports this separate
availability decision. Typed evidence and the decision receipt validate; citation
consumption is closed. The metadata retains three unmet obligations concerning
broader benchmark representativeness and environment control. Those prevent a
general performance claim, which this adjustment does not make.

## Maintenance effects and evidence limits

The preceding worker-guidance update quoted the exact diagnostic setup query.
The fresh lexical fallback consequently ranked that performance note ahead of
both setup procedures. This is an observed cost of retaining incidental task
wording in general guidance; no answering-model mistake was observed. Current
guidance should retain the operational lesson, with the full diagnostic case in
the verification report and retained versions.

The first profiling script was named `profile.py`, shadowing Python's standard
module; it was renamed before the successful profile. Raw operational bodies,
model outputs, scripts and logs remain under `/tmp/cairn-cold-cost/`, outside Git.
The [metadata](semantic-deadline-2026-09-10.json) retains identities, measurements,
checks and artifact hashes. No new answering-model run, independent task
acceptance or net memory benefit is claimed.

## Installed behavior and guidance correction

Clean build `a848e3c0051b4dabc334613d8fcc3ce969b550dd` is installed in both CLI
locations and the running API. Configuration, adapter, model worker, identities
and PostgreSQL stayed unchanged; schema remains 033. Feature CI `34468167658`
completed successfully, with every job and step inspected.

Starting with no API-owned worker, actual native OpenCode debug tool execution
completed unfiltered semantic search in **25.210 seconds**, including OpenCode's
own startup. A fresh Codex app-server thread using the actual project MCP
configuration completed its search in **20.494 seconds**. Both returned `ready`,
ranked the current Codex setup procedure first, and pulled its exact v9 body.
Their complete corpus score digests matched each other and the retained direct
worker result. No answering model selected or acted on these results.

Worker guidance advanced v6→v7 through native MCP. The current paragraph now
states the installed budget and narrowing tradeoff, while removing the incidental
setup query. All other text and metadata are preserved; the exact v6 body remains
readable through native history. An identical edit retry and fresh current pull
passed. The note shrank from 6,319 to 6,296 bytes. Fresh lexical search again
returns the OpenCode and Codex procedures ahead of the general worker note.
The original wrong-harness first rank remains; this correction removes only the
additional displacement caused by the maintenance update. Timing and score-parity
observations above precede this final note edit.
