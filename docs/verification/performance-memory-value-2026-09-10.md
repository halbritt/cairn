# Experiment continuity during retrieval work

Cairn kept earlier experimental findings available during work that reduced
semantic scoring cost after note edits. My assessment as the coding agent is
that this is a qualified example of memory contributing context to useful
engineering work. The improvement is measured; memory's incremental contribution
and net benefit are not.

This is a retrospective review of the completed
[passage-reuse task](semantic-chunk-cache-2026-09-10.md), informed by its earlier
experiments. It is one related sequence, not several independent successes.
The reviewer also authored the implementation and its original report. No new
human acceptance or historical task assessment is implied.

## What happened and what the evidence supports

| Step | Inspected evidence | Interpretation |
| --- | --- | --- |
| Recover earlier findings | The original search returned semantic-worker lesson v9. Its body hash matches the exact retained version inspected in this review. It identifies earlier thread/batch comparisons and advises profiling the actual workload before changing concurrency, retention or caching. | Prior results and their applicability limits remained available as selected guidance. The index proves availability; the coding-agent account and contemporaneous report describe its use in the investigation. |
| Check the proposed direction | The investigator read the linked startup/thread reports and current worker source. The source used whole-body cache keys, so a small edit invalidated every window in a long note. | Memory supplied experiment history. Source inspection identified the passage-reuse opportunity; the note did not supply that implementation insight. |
| Measure and implement | Three public-document append pairs fell from 4.15–4.53 seconds to 0.24–0.28 seconds. A separate profile counted ten inference inputs before and two after. The worker was implemented, checked and installed. | The completed change avoids repeated work in the measured editing pattern. Passing its prospective speed target is not a measurement of memory's causal contribution. |
| Carry the result forward | Worker guidance advanced v9→v10, describing passage reuse, shifted-window limits and current source provenance while retaining v9 in history. | The new finding is available for later work. This review does not claim that another model or task has benefited from the revision. |

The prior negative comparisons were conditional evidence, not a permanent ban on
thread or batch changes. A different workload or resource constraint can justify
revisiting them. Keeping that condition matters: memory should preserve useful
experience without turning yesterday's experiment into an unconditional rule.

The review checked all eleven artifact entries referenced by the original
manifest, eight frozen request/response identity sets and their hashes, the
three paired append measurements, and the profile's counts and result hashes.
The retained comparison script asserts complete equality between the two workers
but saves one response per pair; it does not retain a second independent output
for this reviewer to compare. This limitation is recorded alongside the original
instrumented result. No benchmark or model cohort was rerun.

## Contribution, alternatives and cost

The useful role I attribute to memory is carrying prior decisions and experimental
constraints into the investigation. That is an inference about contribution,
supported by the selected guidance and work account. It is narrower than saying
memory caused the optimization or saved the measured number of seconds.

The conversation and handoff already contained overlapping facts and pointers.
The repository reports remained available without Cairn. Direct source review,
profiling, tests and Pincite guidance also contributed. The evidence cannot
separate these influences or show that the same result would have been missed
without memory. This sequence was chosen retrospectively because its artifacts
and outcome were available; it is not a representative sample of memory use.

The practical improvement is conditional too. Earlier insertions or deletions
can shift many token windows; short notes may need full re-embedding. Cold scoring
was slower in the recorded pair, and unchanged warm scoring gained tokenization
work. The original report retains these costs and its initial temporary-worker
launch failure. Retrieval, note revision, investigation, testing, installation
and this review all cost time and context. Complete accounting is unavailable.

These limits leave room for a positive qualitative judgment about continuity,
while preventing a claim of established net advantage. They also keep the example
separate from the earlier maintenance case, where memory helped recover guidance
that the same investigator had first removed.

## Consequence for continued work

Include this as one qualified case in the [task-value inventory](usefulness-status-2026-09-08.md).
Continue using retained experiment context on independently worthwhile tasks.
Do not rerun this selected case merely to strengthen its score, or treat its
speedup as evidence for boosting the lesson's rank or authority. Further retrieval
changes need an observed problem or a useful requirement; additional capability
alone does not close the broader task-value question.

[Review metadata](performance-memory-value-2026-09-10.json) records the inspected
versions, hashes, artifact correspondence and limits. Raw operational note bodies
remain outside Git under `/tmp/cairn-performance-value-20260910/`. Existing
performance reports, results and assessments are unchanged.
