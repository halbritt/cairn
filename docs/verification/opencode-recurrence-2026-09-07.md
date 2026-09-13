# OpenCode repair trial — 2026-09-07

This trial demonstrated no repair benefit from Cairn. Three real OpenCode
1.18.21 processes attempted the same historical Striatum cache defect using
`qwen3.8-27b` under one renewing GPU fleet lease. None produced a patch or passed
the independent cache check. The corrected Cairn arm did receive both intended
memory records.

| Arm | Supplemental context | Process result | Seconds | Changed files | Cache check |
| --- | --- | --- | ---: | ---: | --- |
| Repository only | Empty Cairn scope | Timeout | 300.108 | 0 | Failed |
| Native excerpt | Two earlier source excerpts in the task | Exit 0; last recorded step hit output limit | 213.701 | 0 | Failed |
| Cairn H0 | The same excerpts as two evidence-backed B records | Timeout | 300.097 | 0 | Failed |

The [machine-readable result](opencode-recurrence-2026-09-07.json) retains source,
model-binary, controller, scenario and gate identities; actual receipt seals;
tool/finish counters; gate digests; and subsequent assessment joins. It contains
no model prose, tool arguments, candidate source changes or operational memory.

## What was checked

The [protocol](../experiments/opencode-recurrence.md) starts from the parent of
Striatum fix `56840cf694ecf451980abbd694fd3c8c01cbc7d7`. A real supervisor/runtime
test observes a usable cache inside the lane workspace and its removal with
workspace reclamation. Before model execution, it failed on the broken snapshot
and passed on the historical repair. The model filesystem contained neither the
later fix nor the held-out check. The committed gate was subsequently formatted
and the same broken/reference distinction was verified again.

The earlier source texts were captured now under explicit later-recurrence
semantics. Separate collector and operator identities authored and promoted the
two records in a disposable PostgreSQL store. No live Striatum graph, dispatch
contract or operational Cairn record was changed.

Before model launch, the corrected controller verified exact record IDs,
versions and body digests for all arms. The H0 launch receipt independently
confirmed both records afterward. Its rendered Cairn context was 4,091 bytes,
versus 1,020 and 1,030 bytes for the empty-scope envelopes. These byte counts
exclude the task and native excerpts; they are not total model token costs.
Reported step input counts omit cached input and unfinished steps, so the result
does not compare full token expenditure.

All arms made real tool calls and each reported one failed shell call. The
native arm's error was inspected before its private session database was removed:
the trial's shell allowlist denied the call. Other reads and shell calls worked.
That restriction, the 8,192-token per-response limit and the 300-second budget
constrain interpretation; this run does not isolate which limit prevented repair.

The postprocessing path attaches deliberately selected gate metadata as local
evidence. All three necessary-condition failures become rejected trial tasks,
with operator testimony as the assessment witness. Repeated assessment requests
return the same result. The H0 use join has exactly two rows for its actual
receipt; two separate selection-preflight rows are excluded from that analysis.
Both records are marked available with usage still unknown. No UUID citations
were observed, and missing H0 telemetry is not evidence that the notes were unused.

## Invalid attempts retained

These attempts preceded the corrected run and are excluded from memory-effect
comparison, rather than counted as model failures:

| Attempt | Observed problem | Correction |
| --- | --- | --- |
| A | Bubblewrap could not create `/work` on the read-only root; all launches failed. | Build an empty sandbox root with explicit mounts; verify OpenCode starts there. |
| B | The fleet revoked the route during execution; Cairn recorded cancellation. | Align the stale declared model with the model already served by the endpoint. Verify renewal under subsequent leases. |
| C | Both B records were omitted: their 1,375- and 1,711-unit serialized costs each exceeded the 1,200-unit optional allowance. | Require exact selection before launch and reserve 32,000 units of memory room, producing a 3,200-unit optional allowance. |

The private artifacts remain under `/tmp/cairn-opencode-recurrence-20260907`
and its `-b`, `-c` and `-d` siblings. Attempt C has a separate review marking
its treatment invalid. Attempt D retains its executed controller, scenario and
gate, original report, and a separate assessment-join file and assessed dump.
The committed controller includes the assessment path added after the model
invocation; both controller identities are recorded. Trial databases were stopped,
native session homes removed, and the lease released after completion.

## Interpretation and next step

One fixed-order task on one model cannot establish general comparative benefit.
The native arm is a controlled excerpt baseline, not a full native-history search
evaluation. The supplied task already describes the desired cache ownership;
the supplemental texts provide context rather than a hidden solution.

Before spending more runs on this comparison, establish that the chosen harness,
tool policy and response budget can complete a historical repair. Preserve this
negative result when changing those conditions. Then test a further recurrence
where earlier experience is relevant to an independently checked failure. Real
Striatum sealed-input integration, broader baselines and an avoided recurring
failure remain open. This result does not justify grooming or learned ranking.
