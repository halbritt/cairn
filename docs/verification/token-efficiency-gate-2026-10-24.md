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

The capture selector uses Claude CLI even when the task actor uses another
harness. Freeze its subscription account and model separately from the task
model, plus its client/config pin in each arm. The hook does not set selector
effort; record that configuration as unset and report missing observed effort
as unknown. For `unchanged_capture`, plan a provider-backed seed stage and a
distinct repeat stage that records an unchanged-snapshot skip without a
provider response.

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
model, effort and selected pin. Its joined turns carry opaque response references,
not provider IDs or model content. Every observed workflow needs at least one
observed sender turn or exact recipient attempt with a completed native turn and
nonempty response references. Caller-selected coordinator, capture and recovery
metadata cannot substitute for that native join; a workflow that has only those
stages remains incomplete without inventing a sender turn. An explicitly failed
native publication or recipient settlement also makes an observed stage
incomplete. Older native reports without response references must be regenerated.
Every observed stage's declared route and four pins must match the frozen plan.
Failed stages need an explicit disposition; unrun,
missing, unknown and failed work remain in their denominators. A null usage
field remains unknown even if its provider turn joined. The gate never adds
shared-turn token fields into an event saving.

The unchanged-capture fixture now supplies a controller-only synthetic first
selection for a local hook check. It is not a provider result. A real seed
stage needs selected response evidence under the frozen capture selector; a
repeat skip is selected hook testimony with no provider response. The present
gate counts that skip but keeps it incomplete until a verifiable capture
observation and ordinary actor turn can be joined. No caller-selected zero-call
field substitutes for that join.

`ready_for_human_grade` means the mechanical panel is complete enough for an
independent task grader to review. It does **not** mean a task passed. The
output always states `independent_task_acceptance=unassessed` and
`release_decision=defer`, even when caller JSON asserts success. Missing joins,
byte-only evidence, route/model/pin drift or an unrun case yield `incomplete`.
Explicitly failed runs with a disposition and null token fields can still be
structurally ready for review; neither is silently converted to a successful
task or known cost. The local test uses a synthetic 22-case, four-arm panel; no
real paired plan or provider result is frozen by that test.

## Evidence currently available

| Evidence | Established | Still missing for this gate |
| --- | --- | --- |
| [Baseline](token-efficiency-baseline-2026-10-23.json) and CAIRN-214 workflow join | Retrospective model usage and a partial exact workflow join; missingness is visible. | Matched candidate outcomes, complete sender/recipient/capture allocation and causal savings. |
| [CAIRN-215 capture hook probe](capture-observation-hook-2026-10-24.md) | The real PreCompact hook, authenticated CLI/API and disposable PostgreSQL retain synthetic selector usage and outcomes. A refused observation after a confirmed note write leaves the write and marker committed, with no observation row. | Live subscription usage and cost, task usefulness, and an installed opt-in canary; the synthetic usage is not provider billing. |
| CAIRN-216 [native inbox probe](../../scripts/check_agent_coordination.py) | A disposable CLI/API probe under a native owner process checks small notice/response source identity, oversized scoped read, explicit settlements, identical committed retry and stale-handle refusals. A [mocked history failure test](../../scripts/test_inbox_recovery.py) makes a handled-settlement retry refuse when source history becomes unavailable. | Paired Codex/Claude notice and response decisions, complete sender/recipient/capture usage, latency, live opt-in and rollback; the unavailable-source test does not exercise store forgetting. |
| CAIRN-219 compact recall and [CAIRN-222 capture shadow evaluation](capture-admission-evaluation-2026-10-24.md) | Compact/full preservation fixtures and a seven-case shadow admission panel passed locally. The capture gate remains off. | Paired relevant-source and correction/handoff outcomes, net provider work, and selected canaries. Byte differences and shadow decisions are not savings. |
| CAIRN-220 notice policy and `notice-flush` | An opt-in routine deadline and publisher-authenticated flush preserve the original event and audit the first release decision; disposable PostgreSQL integration passed. | Actual wake reduction, busy/focused/restart behavior, actionability and p50/p95 latency, one-account canary and exercised live rollback. |
| CAIRN-221 native notice batch | Opt-in store handoff caps a turn at three already-waiting routine notices, keeps per-item settlement and stops for an eligible request or urgent notice; disposable PostgreSQL integration and lifecycle fixtures passed. | Paired batch versus compact-only accepted tasks, source access, tokens, p95 latency and an opt-in canary. |
| [CAIRN-223 pilot](source-fit-2026-10-24.md) and [loopback tool preflight](source-fit-tool-recovery-2026-10-24.md) | Six synthetic source cases; seven completed native task runs. A local scripted Claude CLI session exposed Cairn search/pull, recovered a whole shareable source and excluded a local-only source. | The eighth pilot run exceeded its frozen ceiling and is unscored; seven arms were unrun. Subscription-route permission, model-initiated complete-source recovery, independent task acceptance and paired cost remain unproved. A new provider panel needs a new budget and route guard. |
| CAIRN-224 compact/resume [failure matrix](../../scripts/test_inbox_recall_bridge.py) | Opt-in local fixtures cover a current unfinished checkpoint, mandatory correction, source refusal, partial settlement and same-ID uncertain reply recovery; full mode remains available. | Paired accepted resume tasks and provider usage, effective canary and rollback across a live resume. |
| [CAIRN-225 schema audit](schema-exposure-2026-10-24.md) | Read-only Claude declaration exposure and whole-response cache accounting; source compact profile preserves tool schemas. Current OpenCode plugin hooks do not expose the final outgoing per-request tool set. | Observed compact provider requests, Codex/OpenCode comparable request exposure, true retry/resume and accepted-task effects. |
| [CAIRN-226 recovery diagnostics](recovery-model-2026-10-24.md) | A local report joins selected native/model work by exact attempt and labels missing phase evidence unknown. A metadata-only refusal selector found 13 retained refused settlements with exact delivery/attempt joins in a frozen native sample, but zero provider-turn joins. | Live failure-phase turns, recovery time, correctly settled outcomes and state-aware reminder acceptance. |
| CAIRN-227 [mechanical gate](../../scripts/token_efficiency_gate.py) and [panel setup probes](../../trials/token-efficiency/README.md) | Synthetic 22-case, four-arm plans and exact native joins are checked. The false-ready fix requires a completed native sender or recipient turn with response references; failed publication/settlement stays incomplete. Disposable Store tests check six source/event setups and selected source or transition paths for 15 other fixture IDs. A local hook test checks the final capture setup; a disposable authenticated controller materializes `baseline.single_notice`, `baseline.actionable_response`, `baseline.duplicate_send`, `baseline.small_request_result_reply`, and `baseline.concurrent_receivers` with exact source/event receipts. The small request path has one synthetic Store claim/completion/result/reply with stable retry IDs and `attempt_reconciled:false`; it is not an actor answer or native turn. The concurrent receiver path retains two separately owned pending deliveries and exact read-only readiness/refusal evidence; no actors ran concurrently. Its Codex sessions are synthetic setup identities, not selected live harness or deployment evidence; it records no provider or task outcome. | A real frozen paired ledger, capture-observation and ordinary-task joins, independent task grades, effective route and deployment pins, canary and rollback. No setup probe grades an actor task; `ready_for_human_grade` is structural only and release stays `defer`. |
| Effective recall deployment inventory | On 2026-10-24, `check_effective_recall_deployment.py --compact` verified four ordinary hooks, three inbox bindings and fixture renders without writing to them. | `live_observed=false`: the inventory does not prove a native turn loaded the newer opt-in code or wording. |

The CAIRN-227 disposable controller also materializes all four
`routine_burst_urgent_request` arms in separate databases. Its receipt verifies
four exact source/event/delivery identities, sender-owned routine policy in
batch and combined, and fixed 30-second notice availability. It retains a
one-time selected native readiness hint with its observed time before the first
routine deadline; a post-deadline retry checks durable identities without
reasserting that earlier hint. This is setup evidence only: native batch
transfer, actor handling, provider usage, task acceptance and effective arm
deployment remain unobserved.

The `concurrent_receivers` controller binds two worker tokens in its immutable
intent, verifies each exact ready delivery, and observes `NOT_FOUND` when worker
A queries worker B's session. B's exact delivery remains pending with zero
attempts; a repeat retains both event and delivery IDs. This proves isolated
pending ownership in the disposable Store. Native actor concurrency and both
task results remain unrun. The separate [relay uncertain response
probe](../../scripts/check_panel_relay_uncertain_response.py) observes a
committed response after one dropped relay receipt and resolves it by retrying
the same journaled intent. Its completion text is synthetic; no actor or model
produced a result.

The separate [completion/reply-loss probe](../../scripts/check_panel_completion_reply_lost.py)
commits a synthetic result through a hook-generated context, leaves its first
reply intent pending during a disposable API socket outage, and replays the
same intent after reconnection without another result, claim or reply. The
outage precedes publication; actual redelivery of the handled request, actor
handling and task acceptance remain unmeasured.

The separate [source-forget probe](../../scripts/check_panel_source_forgotten.py)
forgets an admitted source through the operator workflow after a synthetic
claim and records one exact `source_unavailable` failed settlement. It did
not run an actor task or requester explanation.

The [required conflict probe](../../scripts/check_panel_required_conflict_group.py)
issues both frozen mandatory C sources in a disposable database. Operator
history reads both whole versions; authenticated agent index and search return
`OPEN_CONFLICT` without a source body or partial selected answer. The search
refusal carries diagnostic metadata with `selected: null` and no index entry.
No actor decided the threshold or ran a provider.

The [process-crash child probe](../../scripts/check_panel_process_crash_child.py)
observed the synthetic claim child exit from SIGKILL and then explicitly reported
`process_exited` reconciliation; it establishes no native actor/model outcome or
automatic host recovery.

The `busy_owner_task` panel input now freezes a synthetic parser source and
sample under `setup.active_task`. Its private check grades the parser conclusion
and the order of review and notice handling. Preflight verifies the source and
pause contract and keeps check clauses out of actor material. The Store probe
still changes reported busy/idle metadata without running the review; no
native source-read pause, parser conclusion, or notice ordering was observed.

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
