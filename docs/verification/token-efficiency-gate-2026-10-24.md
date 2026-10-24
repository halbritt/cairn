# Token-efficiency acceptance register, 2026-10-24

This is the local gate for [CAIRN-227](../plans/token-efficiency-2026-10-23.md).
It records what the current source and retained observations establish. It does
not turn an opt-in source change or a byte reduction into rollout approval.

## Frozen comparison contract

Before admitting a provider run, retain a manifest with the exact fixture and
source-version hashes; baseline and candidate commits; model ID, effort and
subscription route; available account budget and per-run admission reserve;
accepted-task checks; elapsed/token ceilings; and selected harness/deployment
pins. Give every planned run a stable workflow ID and stage ID. A resumed or
failed run keeps its place in the denominator. Stop the affected provider
operation on auth, quota, route/model drift or a failed admission guard; do not
substitute an API or OpenRouter route.

For the same frozen tasks, compare baseline, compact envelope only, already
waiting batch only, and the combined candidate. Record source reads, tool
discovery/help, sender, recipient, coordinator, capture, compaction and recovery
calls. Join model usage by exact response and native attempt IDs, count a shared
turn once, and report input cache read/write, uncached input, output, reasoning,
latency, null usage and failed runs separately. The combined effect is measured
directly; individual byte or response reductions must not be summed as savings.

The paired panel has these **22** required situations. Every situation needs
an accepted task outcome and explicit failure disposition in the applicable
arms. `unrun` means no accepted paired evidence is present here.

| Cohort | Required situations | Current paired status |
| --- | --- | --- |
| Basic work | Single notice; actionable response; small request with durable result/reply; long source | unrun |
| Scheduling | Three routine notices plus urgent request; busy owner task; concurrent receivers; duplicate send | unrun |
| Delivery recovery | Completion committed/reply lost; source forgotten between admission/read; lease expiry; process crash; relay uncertain response; stale execution | unrun |
| Retrieval lifecycle | Required conflict group; semantic backend unavailable; wrong-project memory; corrected note; compaction before/after settlement | unrun |
| Capture | Unchanged capture; meaningful owner correction; unfinished handoff | unrun |

Passing source fixtures is necessary but cannot replace the paired task check.
Reject any candidate that loses an actionable item, leaks a source, duplicates
an external effect, reports false completion, or fails current correction and
required-source recovery. A task grader must also check usefulness; observed
tool calls and model assertions alone do not establish task success. Keep every
failed, refused and unknown run in the report rather than dropping it.

## Mechanical fixture gate

`scripts/token_efficiency_gate.py` freezes and checks this panel without calling
a provider. It complements `trial_task_eval.py`'s existing corpus freeze and
`workflow_model_report.py`'s native usage join. The private fixture is JSON with
schema `cairn.token-efficiency-fixture/1` and the 22 ordered case IDs, each with
a nonempty `acceptance_check`. The plan pins its file hash and each check's
canonical JSON hash. It also fixes the model, effort, subscription route,
account, limits, four arm commits, API/ordinary/inbox/client pins, and one
workflow per case and arm. Stage IDs are globally distinct; each stage names
its role and the pin it expects.

```sh
python3 -B scripts/token_efficiency_gate.py freeze \
  --plan private-plan.json --fixture private-fixture.json --output private-frozen.json
python3 -B scripts/token_efficiency_gate.py report \
  --frozen private-frozen.json --fixture private-fixture.json \
  --observations private-observations.json \
  --workflow-report private-workflows.json --output private-gate-report.json
```

The observation ledger names every planned workflow and stage. Sender stages
join by the exact opaque workflow event; recipient stages also join by the
exact opaque attempt. Its selected native workload label must equal the frozen
workflow ID, including the arm; an event from the same case in another arm
therefore fails the join. The native report must carry the matching provider
model, effort and selected pin. Every observed stage's declared route and four
pins must match the frozen plan. Failed stages need an explicit disposition; unrun,
missing, unknown and failed work remain in their denominators. A null usage
field remains unknown even if its provider turn joined. The gate never adds
shared-turn token fields into an event saving.

`ready_for_human_grade` means the mechanical panel is complete enough for an
independent task grader to review. It does **not** mean a task passed. The
output always states `independent_task_acceptance=unassessed` and
`release_decision=defer`, even when caller JSON asserts success. Missing joins,
byte-only evidence, route/model/pin drift or an unrun case yield `incomplete`.
The local test uses a synthetic 22-case, four-arm panel; no real paired plan
or provider result is frozen by that test.

## Evidence currently available

| Evidence | Established | Still missing for this gate |
| --- | --- | --- |
| [Baseline](token-efficiency-baseline-2026-10-23.json) and CAIRN-214 workflow join | Retrospective model usage and a partial exact workflow join; missingness is visible. | Matched candidate outcomes, complete sender/recipient/capture allocation and causal savings. |
| CAIRN-215/216/219/220/221/222/224 candidates | Opt-in or shadow source slices and local fixtures. CAIRN-220 and CAIRN-221 passed disposable PostgreSQL integration; CAIRN-224's local failure matrix passed. | Selected capability enablement, paired accepted tasks, live rollback and latency/actionability checks. |
| [CAIRN-223 pilot](source-fit-2026-10-24.md) | Six synthetic source cases; seven completed native task runs. | The eighth run exceeded the frozen ceiling and is unscored; seven arms were unrun. Native tool recovery and complete-source acceptance remain open. |
| [CAIRN-225 schema audit](schema-exposure-2026-10-24.md) | Read-only Claude declaration exposure and whole-response cache accounting; source compact profile preserves tool schemas. | Observed compact provider requests, Codex/OpenCode comparable request exposure, true retry/resume and accepted-task effects. |
| Effective recall deployment inventory | On 2026-10-24, `check_effective_recall_deployment.py --compact` verified four ordinary hooks, three inbox bindings and fixture renders without writing to them. | `live_observed=false`: the inventory does not prove a native turn loaded the newer opt-in code or wording. |

The owner held [CAIRN-107](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/3aed7adf-29fc-4d1b-a4a5-80bc1d0ea3f6/) weekly campaigns in Backlog on 2026-10-21 until
subscription quota is available. Those historical runs do not substitute for
this new frozen token-efficiency panel. The CAIRN-223 bounded pilot is also
incomplete and cannot be silently replayed under the same freeze.

## Release decision

**Defer combined rollout.** Current data supports continued local development
and reversibility tests, not a claim of provider token savings or equal useful
task outcomes. Keep full/default paths available. To change this decision,
attach the frozen manifest, complete paired ledger, independent task grades,
unknown-usage coverage, failure/fence results, one account per selected harness,
effective installed API/ordinary/inbox/client pins, latency distributions and
an exercised rollback. Distinguish each tested slice from full design acceptance
and measured usefulness in the final report.
