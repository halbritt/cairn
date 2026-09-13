# Native maintenance of the assessment guide — 2026-09-10

OpenCode updated the existing operational assessment-history guide from the
[worked write procedure](../use-outcome-loop.md#record-a-qualitative-review).
The Cairn coding agent reviewed the result and retained the new instructions,
correcting only a missing paragraph break. The guide now covers the write path
as well as reading history, with its earlier text and versions intact.

This is a completed maintenance task with one formatting finding. Memory was
the work product being maintained; this does not isolate the effect of optional
memory on an unrelated task or establish downstream benefit or net savings.

## Task and observed result

The task named one existing procedure and the current committed source file,
required source inspection before editing, permitted one concise append, and
required a fresh search/pull afterward. Review criteria were saved before the
run. The source snapshot came from `22fb971`; the corrected scratch preparation
from the [earlier failed authoring attempt](qualitative-review-example-2026-09-10.md)
provided a Git project root and allowed only the selected source path. The failed
authoring task remains separate from this maintenance task.

OpenCode 1.18.21, using `deepseek/deepseek-v4-flash-0731`, completed in 100.549
seconds with exit 0. The trace contains two searches, two full note pulls, one
source read and one append. It pulled version 1 before editing and version 2
through a fresh search afterward. All six tool calls completed successfully.
Seven provider requests completed without relay refusal; their reported cost
was $0.004850411. The limit was eight requests, 8,192 output tokens per request
and 300 seconds. Local execution, preparation and review effort are unpriced.

The target was procedure `25adf0b0-7246-4f63-8687-7ccf2770f12a`, originally
maintained in this Codex workflow. The native append added 137 words describing
the current source location, chosen narrative, reviewed history version, saved
request, unknown acceptance, read-back and conflict handling. Current source
supports those claims. The model did not execute the example commands itself;
the [earlier literal example check](qualitative-review-example-2026-09-10.md)
and the present source review support their correctness.

## Independent review and correction

The root reviewer verified the saved body against the native append and earlier
body, checked metadata and historical v1 through the ordinary authenticated API,
and compared all ordinary-version fingerprints before and after the run. Only
the intended new version was added; all 89 preexisting versions survived unchanged.

The append omitted separating whitespace, joining its first sentence directly
to the prior paragraph. A root `replace` operation inserted two newline bytes
as version 3, preserving every word from v1 and the native addition. A fresh
search/pull verified current eligibility and the exact corrected body. The final
inventory contains 91 versions; the two additions belong only to this procedure.
Version 2 retains the native result and its formatting defect. The native final
answer also included prose around its JSON report; its substantive claims were
checked against actual tool results rather than accepted from that report.

The two native retrievals are linked to observed run
`a8a7e72b-dba6-48a9-88b3-6475ec414bc7`. Assessment v1 accepts the substantive
maintenance result with the formatting finding and root correction stated in
its reason. This is the coding agent's delegated review, not new human acceptance.
The host channel's instrumented witness records the observer route; it does not
turn editorial judgments into mechanically proved facts.

## What this contributes

The existing guide was maintained through native memory tools without replacing
its earlier instructions or changing its scope and sensitivity. That supports
using this interface for reviewed cross-harness maintenance. The prompt supplied
the target note and source revision, and the source supplied the new procedure.
No comparison establishes that retrieval improved reasoning or saved effort.
Later use of the amended guide remains untested. This extends the existing
maintenance case; it is not an independent replication of general task value.

The [selected manifest](native-guide-maintenance-2026-09-10.json) records the
checks, source hashes, receipt and evidence identities, and private scratch
locators. A selected reviewer summary was captured as evidence
`c917d680-b7f8-448c-beb0-d6cb068ac2c9`; note bodies, raw model output and credentials
were not committed. The raw native artifacts remain private under
`/tmp/cairn-native-guide-maintenance-20260910`. No production code, binary,
configuration, schema or assessment rules changed. The broader roadmap remains
open.
