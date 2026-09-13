# Corrected native repair comparison

The corrected comparison produced no Driver-admitted repair in any condition.
It does not establish a memory advantage. The scratch permission correction
removed the observed temporary-directory denial, but repair completion remained
unobserved. Further runs of this unchanged task and binding are not the next
useful experiment.

This comparison used Cairn executor `409bbbc`, Striatum's proposed integration
`ee8a463`, and the same frozen task, source, lesson, model and limits as the
[original comparison](native-permissions-2026-09-08.md). The corrected actual
OpenCode permission check and complete native fixture passed before the run.
Each condition had one supervisor invocation, at most 32 forwarded provider
requests, 8,192 output tokens per request and 600 seconds. No condition was
restarted. Direct context contained the lesson twice and native context once.

| Condition | Forwarded requests | Failed responses | Tool permission denials | Terminal result |
|---|---:|---:|---:|---|
| No memory | 32 | 0 | 0 | Local request allowance exhausted; no admitted repair |
| Direct context | 29 | 3 | 0 | Invocation timeout; no admitted repair |
| Native context | 24 | 2 | 1 | Invocation timeout; no admitted repair |

The control received six additional 429 rejections after its forwarding allowance
was exhausted. Those client retries did not create another supervisor invocation
or additional forwarded requests. Its task outcome remains unknown, classified
as binding/quota. Direct and native task outcomes remain unknown. The independent
repair oracle had no admitted candidate to evaluate in any condition.

The control was still reproducing connection behavior at its end. Direct context
authored source and a new test in disposable scratch, then its final test failed
`TestAgentConnectionDefaultsMixedExplicitAndDefault`; formatting was also
unfinished. Native context was still inspecting source at timeout. Its one
permission denial concerned reading `/opt/go/src/os/file.go`; a subsequent bash
command read that file successfully. None of these observations supports a
Change Set packaging defect as the common cause of failure.

The native condition retained the exact admitted Cairn observation identity and
version, matching prompt hash, successful claim and delivery, and the observed
600-second process timeout. That establishes actual native delivery. It does not
prove the model used the lesson. The failed provider responses and differing
lesson counts also prevent a clean causal or efficiency comparison.

[Verification metadata](native-corrected-recurrence-2026-09-08.json) retains the
frozen pins, outcomes, relay failures and partial usage totals. Usage sums the
last retained object per request; missing usage is excluded, not zero. Raw
operational memory, model transcripts, private graphs and database dumps remain
under `/tmp/cairn-native-recurrence-model-b`. The original cohort remains intact.

## Consequence for the roadmap

These two cohorts justify the concrete harness correction and verify native
context transport. Neither cohort completed a repair, so these runs establish no task benefit
from memory.
Repeating this comparison unchanged, raising its limits until something passes,
or adding an output helper without a corresponding observed refusal would not
resolve the usefulness question.

Keep the implemented fixture and experiment available, but retire this unchanged
comparison from the active usefulness sequence. A further coding comparison needs
a materially different, justified execution condition and evidence that completed
tasks can reach the evaluator. Continue practical cross-harness use of the existing
retrieval interface with explicit task outcomes; the [Codex connection check](codex-mcp-2026-09-08.md)
is compatibility evidence toward that path, with independent model use still open.
Production native-contract adoption and the full Cairn usefulness goal remain open.
