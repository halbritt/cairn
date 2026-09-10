# Cairn roadmap

Updated 2026-09-10. Baseline: `e3b47c7` (local memory and task delivery loop).

Cairn has a transactional PostgreSQL core and a manually fed process wrapper.
The next product milestone is memory used in real Striatum/OpenCode builds, with
record-specific observations showing whether it prevents recurring failures. Passing the
current tests does not establish that result or full design acceptance.

The [MCP facade](mcp.md) now exposes ordinary memory tools through the existing
authenticated API. A [real OpenCode model](verification/mcp-2026-09-08.md) searched
and pulled a note saved in a Codex session, then correctly answered the
storage questions with its source. This adds native tool contact and one useful
operational transfer. A [later configuration task](verification/mcp-host-use-2026-09-08.md)
now has native retrievals associated with observed host runs: the first generated
an unusable configuration, then a fresh run retrieved a curated procedure revision
and produced a configuration that OpenCode connected successfully. This is a
bounded correction-and-reuse observation. Native Striatum ingress and broader
task benefit remain open.

The configuration task now has a [deterministic setup command](mcp.md#opencode-example):
`cairn opencode-config` emits the verified native shape and forwards the existing
scope/context flags. Both output modes connected in OpenCode without a model
call. This removes routine JSON assembly; host launch and outcome association
remain explicit responsibilities.

The [Codex configuration generator](verification/codex-config-2026-09-09.md) now
emits a TOML entry from the same connection/context flags, using conversation or
explicit task/run scope. The actual Codex client loaded generated files and pulled
exact saved content in both modes. It leaves existing host configuration intact;
native connection and setup capability do not establish a completed model task.

The [native OpenCode adapter](opencode-tools.md) now uses actual custom-tool
session context for search scope and the existing authenticated CLI for all five
ordinary tools. CLI results now include structured pull arguments alongside shell
commands. [Native verification](verification/opencode-tools-2026-09-09.md) covers
default local capture, exact body/evidence pulls, edits and refusals without a
model call. The adapter is installed locally; its session grouping adds no
observed-run authority or model-task benefit claim.
The [bundled installer](verification/opencode-install-2026-09-09.md) now writes
the matching native adapter and explicit connection settings directly from the
CLI binary, preserving host permissions. Native integration checks consume its
output. This removes source-checkout copying from setup; automatic host adoption
and task benefit remain open.
The [argument-validation repair](verification/opencode-validation-2026-09-09.md)
also closes an observed malformed-sharing-flag defect: the adapter now validates
its own schemas before calling Cairn. A normal session with scripted completions
checks the host behavior without model inference.

The [native Codex MCP check](verification/codex-mcp-2026-09-08.md) now exercises
search and exact lesson pull through Codex's actual client with no model turn.
The [setup example](mcp.md#codex-example) now supports opt-in Codex conversation
scope from native tool-call metadata. A [native check](verification/codex-thread-2026-09-09.md)
retrieved exact saved content in two conversations without manual task/run IDs.
Explicit task/run configuration remains available for finer scope. A [native maintenance follow-up](verification/codex-maintenance-2026-09-08.md)
saved and revised a useful setup procedure, refused its stale handle and retrieved
the exact revision in a fresh task. This adds harness compatibility and ordinary
maintenance; it does not close X2's native resume/compaction interlocks or
establish model-selected use.

Cairn is now [enabled in this host's trusted Codex project](verification/codex-project-2026-09-09.md).
Fresh native conversations discover all five ordinary tools from local project
configuration and retrieve current notes without MCP overrides. Repository
instructions prefer those tools when available. This closes the local adoption
gap between temporary probes and normal project startup; durable task benefit
and other hosts' adoption still require evidence.

The [MCP context extension](verification/mcp-currentness-2026-09-08.md) lets hosts
supply existing revision/workspace/task-class/binding/capability pins at startup.
Public stdio checks verify matching retrieval and missing/mismatched withholding.
This closes a facade capability gap; it adds no model-task benefit evidence.

[Current task-value evidence](verification/usefulness-status-2026-09-08.md) includes
one exploratory avoided-regression case, a real failure-to-lesson loop, an
operational note transfer, one accepted configuration follow-up after a
procedure revision, a qualitative adapter-repair case and cumulative recovery of
lost guidance. The inventory distinguishes current evaluation guidance from its
retained historical comparison plan. No baseline advantage or durable task improvement
across harnesses is established. Prioritize a real
accepted task, retrieval quality and transfer evidence. Further recovery
engineering is deferred for this experimental stage unless an observed problem
requires it; the existing implementation remains available.

The [explicit connection repair](verification/agent-env-2026-09-08.md) is installed
and integration-tested. Its matched model experiment exhausted the request
allowance in every condition; retrieval was never exercised. This adds a usable
CLI fix, with corrected unknown/binding-quota assessments, but no transfer or
completed model-task result. Use task-relevant memory in the next real build
and verify actual tool contact before drawing a benefit conclusion.

[Build diagnosis](build-identity.md) now reports CLI and authenticated API
identities separately; MCP initialization identifies its own facade. This helps
distinguish installation drift from remembered guidance without treating equal
revisions as capability proof or different revisions as automatic staleness.
Unknown stamps remain explicit. [Checks](verification/build-identity-2026-09-09.md)
cover all provisioned profile types and existing stdio tool workflows.

[Ordinary note transport](verification/note-transport-2026-09-09.md) now accepts
maximum 64 KiB bodies even when JSON escaping exceeds the old envelope, through
existing capture and edit interfaces. Overlimit refusal and previous mutation
retries are verified; qualified operator limits remain separate. This repairs a
storage obstacle without establishing memory's incremental task contribution.

[Authenticated note capture](verification/agent-remember-2026-09-08.md) is now
installed: `agent remember` and explicit stdin use the existing create API, with
shared operator parsing. A scoped hosted-agent profile and repository guidance
support everyday use. This coding session saved and retrieved selected A notes
through the operational service; that is manual use, not demonstrated automatic
learning, independent cross-model benefit or native Striatum ingress.

Status: **open**, **partial**, **implemented/tested**. Completion means the stated
acceptance evidence exists; deployment and measured usefulness are separate.
Items can be delivered in smaller commits without marking the whole item complete.

The owner requested ordinary **Agy and Claude Code interfaces** on 2026-09-09.
The owner subsequently clarified that **Codex and OpenCode are sufficient to
demonstrate cross-harness task benefit**. Increasing task value in those existing
harnesses takes priority over adding adapters. U7/U8 retain the requested scope at
lower priority; implement them after useful task progress, or when a concrete
task requires a missing harness. Their initial functionality does not depend on
advanced H1 mediation or native resume/compaction interlocks. The
[implementation status and history](implementation-status.md) must retain dated
changes, superseded approaches, negative results and corrections as work proceeds.

## Governing requirements

The [source manifest](sources/manifest.json) pins the consolidated design,
provenance v3, producer-attribution correction, and usefulness synthesis. All four
matched their live sources during the 2026-09-07 review. Operator decisions and
recorded resolutions govern; a consolidated planning document cannot override
them. The local implementation defaults in [decision 0002](decisions/0002-local-memory-loop.md)
settle mechanics without canceling unfinished requirements.

| Source | Authority and supersession |
| --- | --- |
| Council transcript `/home/halbritt/.council/topics/agent-memory/COUNCIL.md`, substantive messages through #130 | Operator decisions and recorded acceptance. Transport failures and member conjecture are not requirements. Keep private sessions out of this repository. |
| Original four proposals | Superseded. Operator #67 withdrew universal ledger, hash-chain, content-addressed identity and full-ancestry requirements. Surviving semantics are carried forward in syntheses. |
| Provenance v1/v2 and Fable Bonded Memory review | Historical inputs. v2 P1–P8 accepted in #87 after #84/#86, then amended by v3 and the operator correction. Withdrawn producer-spoofing and harness-absence premises do not survive. |
| [Provenance v3](sources/agent-memory/synthesis/provenance-synthesis-v3.md) | Operative; D1–D8 closed in #106. Postgres, cheap B→A demotion, unsigned C/D checkpoints, advisory evidence degradation, plain delivery fields, testimony distinction, 90-day use retention floor, OpenCode H0 target are settled. |
| [Producer-attribution correction](sources/agent-memory/synthesis/producer-attribution-correction.md) | Overrides v3 §3.1 and universal asserted/observed blocks. Trusted-channel writer stamps, independent attribution reconciliation, failure recovery and open-loop detection remain required. |
| [Usefulness synthesis](sources/agent-memory/synthesis/usefulness-synthesis-v1.md) | §4 resolves §3's historical questions; #121 records acceptance. §5 requires **use/outcome join → real-history replay → generated demand docket → groomer**. |
| Bonded Memory revision 3, `/home/halbritt/bonded-memory.html` | Accepted detail subject to the above amendments. SHA-256 `edb3b0fab032f0490f0612c99adcdbdd41da2445daf346ff08df65b4de2a1703`. The consolidated design's relative link is stale; this is the actual source. |
| Original RFP, `/tmp/agent-memory-RFP.md` | Residual adapter/scenario detail only. SHA-256 `ee6d2b2a1651c7ff16bede7afd9ca9bcae5246ada0bf7ce1f28813863ee52b68`. This temporary locator is not a durable copy. |
| [Consolidated design](sources/agent-memory/design/agent-memory-design.md) | Implementation planning synthesis requested by #129. Read alongside the amendments, not as a fresh vote. |

## Delivery sequence

### Assess task value without narrowing it to an evaluator

Owner clarification, 2026-09-09: the desired result is convincing evidence that
memory creates task value. Mechanical provability is not a requirement for every
form of value. The evaluation must not reward only benefits that happen to be easy
to count, nor make a particular evaluator's preferences the system's objective.

Useful outcomes can include a better decision or artifact, less rediscovery,
earlier recognition of a problem, preserved constraints, a more appropriate
question, or continuity across tasks and harnesses. Some benefits are qualitative,
indirect or delayed. Keep them eligible for investigation rather than assigning
them zero value because a task oracle cannot score them.

Evaluate sequences of meaningful work across turns, sessions and related tasks
when the benefit accumulates there. The owner cited Pincite as an example: no
single-turn value proof, but confidence from repeated use that guidance helps.
That is practitioner judgment supporting a hypothesis worth examining, not an
established causal result or evidence already demonstrating Cairn's value. Look
for durable changes in decisions, consistency, rediscovery and correction burden,
including counterexamples. Do not require each retrieval to justify itself as an
isolated intervention, or make evaluation overhead consume the benefit sought.

For substantive claims, retain the actual task context, relevant memory and its
provenance, the observed behavior or artifact, and the reasoning connecting them.
Include contrary evidence and plausible alternatives such as the model's prior
knowledge, direct context, task differences or ordinary iteration. Separate what
was observed from the judgment about its value and from stronger causal claims.
An informed reviewer or the owner's assessment can support a qualitative value
claim; label who made that judgment and its limits. Never invent human acceptance.

Use a test, comparison or repeated trial when it answers a material question.
Use source-backed case analysis and artifact review when those better fit the
benefit. Controls and repeated observations can strengthen attribution, but every
useful case need not become a mechanically scored benchmark. Unexpected benefits
can be examined after the fact if identified as exploratory. Preserve failures,
unknowns and the cost of retrieving, reading, maintaining and correcting memory.

Choose tasks because the work matters, then choose evidence appropriate to the
claimed benefit. Avoid a single aggregate success score, training toward one known
oracle, or treating citation/delivery counts as a target. Revisit the evaluation
when it excludes plausible value or encourages superficial compliance. Current
Codex/OpenCode interfaces are sufficient for this work; additional adapters remain
lower priority. This clarification broadens admissible evidence, not the claims
supported by the trials already completed.

A [retrospective adapter-repair case](verification/value-case-validation-2026-09-09.md)
now distinguishes a verified useful repair from the plausible contribution of
recalled guidance, unavailable counterfactual and unmeasured net costs. The
[existing assessment path](use-outcome-loop.md#qualitative-and-cumulative-review)
supports narrative judgments; multi-task reviews can remain source-linked cases.
This adds qualitative evidence without closing E4/D2 or changing earlier outcomes.

Authenticated agents and observers can now inspect their own complete assessment
versions with `agent assessments` and an exact receipt ID, including narrative
reasons and evidence references. The profile must match the receipt's owner,
repository and destination. This completes access to the existing per-receipt
review data without adding a value score or widening protected aggregate reports.

A [selected procedure-maintenance pass](verification/procedure-maintenance-2026-09-09.md)
consolidated accumulated Codex/OpenCode setup updates while preserving prior note
versions. Current bodies are smaller and two setup queries retain their first-place
answers. Clearer organization is an editorial judgment; task benefit remains
unmeasured. Maintain useful current guidance when successive updates obscure it;
do not turn note length or maintenance counts into another value target.
A retained-version follow-up found missing browse arguments and semantic setup
prerequisites and restored them. This qualifies the original preservation claim:
smaller notes still need review for lost instructions, and upkeep has a cost.

A [cumulative maintenance review](verification/cumulative-maintenance-value-2026-09-09.md)
follows the contiguous consolidation → history access → correction → preview
location sequence. It supports a concrete continuity case and retains the
self-created rework, available alternative sources and unknown net cost. Fresh
CLI task/run scopes do not establish independent agent sessions. Next, use the
current interfaces on an accepted defect repair or required behavior change whose
outcome matters independently of documenting the memory loop. Give additional
inspection/reporting machinery lower priority when it only makes this same case
easier to evaluate; concrete task needs can still justify retrieval changes.
This prioritization does not impose a mechanical value gate or close open items.

Repair current contract violations first. Then build the smallest real host
integration and the observation join; add currentness, index/pull and historical
evaluation; generate evidence-attached demand; only then add grooming. Lifecycle,
deletion and recovery work proceeds alongside this sequence and gates consequential
or sensitive operation at scale. A protected record of a blocked request is a
contract repair, not completion of the later generated-demand product.

### 1. Repair the existing surface

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| R1 — transient task text | implemented/tested | Stop default raw task/query retention in canonical receipts, replay and context files. Keep transient lexical ranking and a retained digest. Version the semantic contract and preserve old seal verification. A synthetic CLI canary must be absent from all newly retained receipt/artifact bodies. Track old copies under L5; changing future capture does not erase history or backups. Sources: usefulness §4, design §§12.2/15.2. |
| R2 — degraded advisory evidence | implemented/tested | Return otherwise eligible B in advisory context with evidence state visible; exclude consequential B only when no resolvable reference remains. Test dangling/divergent references, a surviving reference, and both purposes. C waivers belong to L3. Source: v3 D3. |
| R3 — retraction preview | implemented/tested | Require an impact preview before retraction; transactionally reject stale version/dependency/use state. Show affected versions, records and runs, including evidence and known corrected/superseded dependents. Test new exposure between preview/commit and concurrent compile/retract, plus open-conflict refusal. Optional `impact` invocation is insufficient. Sources: v3 §2.1, design §9.3. [Requirement audit and evidence inventory](verification/retraction-evidence-2026-09-09.md) verify the required gate, exact retained-version support, exposure/dependency invalidation and protected CLI/API inspection. Complete previews refuse beyond 1,000 versions, evidence references or uses; unknown derivations remain outside recorded impact. |
| R4 — protected blocked demand | implemented/tested | Persist relevant A requests blocked from consequential use as `escalation_blocked` with record/version, request scope and receipt. Surface unresolved current demand in the bounded operator docket; retry must not duplicate it. Hosted model output must contain only safe census counts. Sources: v2 P1, v3 §3.2. |
| R5 — full retrieval explanation | partial | Persist protected considered/selected/withheld versions, eligibility, lexical/scope/recency/stable-ID ordering and budget decisions. Add authorized inspection distinct from model rendering. Use a fixed omission-bucket census. Verify access boundaries, retries and no hidden destination content; legacy receipts must disclose missing detail. Sources: v2 P6/P7, design §11. |

[Verification for the first repair slice](verification/contract-repairs-2026-09-07.md): R1/R2/R4 regression tests, a synthetic CLI
capture canary, and frozen v1 replay compatibility. R3 now requires caller-owned
one-hour previews, invalidates on new exposure and version changes, and serializes
concurrent compile/retract; coverage is all retained versions and direct uses,
with refusal above 1,000 uses. Known relation traversal is also capped at
1,000 retained versions, with no partial preview token. Known versioned record relations now participate in previews; evidence-object
dependencies and broader lifecycle operations remain under L1/L4. R5 now stores
candidate IDs/versions, eligibility reason, lexical/scope/recency/stable-ID ranking
and packing cost, with owner-authorized `explain` and fixed census buckets. Named hard
policy refusals now return a durable caller-owned observation ID and a partial
protected trace; full refusal explanations and the remaining policy paths are
tracked by L2.
Compiler refusals now retain the already-computed, visibility-filtered candidate
reasons and ranking/allocation features in a bounded
[partial diagnostic trace](refusals.md). Missing phases remain explicit; evidence
snapshots, note bodies and query text are excluded. R5 remains partial for complete
refusal explanations. [Evidence impact inspection](evidence-impact.md) now lists
exact captured-reference roots, known transitive relation types and retained uses
with independent pagination. It does not infer source-record edges from labels or
extend mutation authorization. R3/L4 remain partial for the broader lifecycle
contracts; this report is not a replacement for a guarded mutation preview.
Optional [evidence byte spans](index-and-pull.md#index-and-expansion-contract)
let agents inspect selected portions of captured sources that exceed their pull
budget. Full-object identity checks, destination checks and shared credits remain.
The [verification](verification/evidence-spans-2026-09-09.md) covers CLI/API,
MCP, native OpenCode and retry compatibility with the previous binary.
[Note excerpts](verification/note-spans-2026-09-09.md) now make selected A/B body
ranges readable when a retained note exceeds the 24,000-byte expansion cap.
The existing pull path preserves full-source guards and shared budgets; Class C
instructions remain whole. CLI, MCP and native OpenCode checks cover the path.
This retrieves retained bytes; L4 remains partial for managed large artifacts,
as-cited span relations and broader dependent invalidation.
[Body-pull reasons](verification/pull-reason-2026-09-08.md) now describe the
current eligibility recheck instead of reporting a zero match count from a
queryless pass. Original protected ranking and committed retry responses remain
unchanged; this is a narrow R5 explanation repair.
No legacy copies were purged and no real model usefulness claim is made.

Baseline synthetic probes confirmed R1–R4 on `e3b47c7`; R5 is a source/schema
finding. `report`, `replay`, and `docket` currently provide narrower operations
than the accepted usefulness features with those names.

### 2. Establish the observed use/outcome join

Depends on capture repair and protected receipt boundaries. This is the first
accepted usefulness milestone, before historical evaluation and generated demand.

Within this section, prioritize U1–U5's useful task outcomes in the existing Codex
and OpenCode interfaces. U7/U8 are lower-priority breadth work, not prerequisites
for proving transfer or reducing recurring task failures. Adapter count is not
an acceptance measure for task value.

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| U1 — real host and agent ingress | partial | Integrate real Striatum/OpenCode H0 spawn/terminal boundaries with authenticated channel identity and stable task/run/attempt/binding IDs. Add narrow local agent access or in-process embedding. Current CLI stamps one OS UID, so its own A notes cannot independently self-promote to B. Keep operator administration distinct. Test real input route, terminal/recovery paths and one authorized wrapped build; synthetic adapter fixtures alone do not qualify. |
| U2 — bounded run evidence | partial | Implemented: observer-bound pre-carrier argument-vector digest, rendered-input and output-stream digests, process exit/duration, transport retry identities, optional supplied error-signature digests and versioned evidence-linked task assessments. These do not establish complete execution identity or task value. [Selected file fingerprints](run-artifact-evidence.md) now add an observer-generated manifest with run IDs, labels, byte counts and SHA-256 through existing evidence capture; local by default, with no contents or actual paths retained in the manifest. Remaining: convincing native-task outcome evidence, including supported qualitative judgment; fingerprints alone do not establish creation, correctness or memory contribution. Default raw prompts/outputs/secrets/workspace capture stays off. See [digest semantics and current verification](use-outcome-loop.md#command-and-delivery-digests); selected evidence capture is separate from automatic host observation. |
| U3 — binding versus capability failures | implemented/tested | Represent execution binding separately from capability identity; distinguish quota, credentials, transport and adapter failures from task/model results. Enforce no capability inference from binding failure in schema/API tests. Exit zero continues to mean process status, not task acceptance. [Known pre-launch failures](verification/prelaunch-failure-2026-09-08.md) retain `not_attempted` without writing into refused artifact paths. |
| U4 — record-specific outcome join | partial | Join exposure, delivery, citations/expansions, outcomes and corrections by record/version and comparable task class/scope. Report coverage, unknowns, recurrence, resolution cost and uncertainty without multiplying rows across observations. Current independent counters are only operational telemetry. Verify multi-record/multi-observation fixtures and missing observations. |
| U5 — usage ladder | partial | Preserve cited > expanded > behaviorally implicated > delivered-only, with inference method/version and observation coverage. Connect actual citation/expansion ingress; manual testimony does not prove use. Do not infer unknown H0 tool actions or benefit from delivery. Test coverage-aware reporting. |
| U6 — completed tasks with open delegates | implemented/tested | Add task completion observations and detect still-open service-observed attempts at completion. Existing exact-attempt attribution/failure recovery stays intact. `UNFINISHED_RUN` is a separate wrapper-recovery hint. Test late terminal recovery and completed-task open attempts. |
| U7 — Agy interface | partial | Inspect the actual Agy driver/tool/configuration contract and ship ordinary search, record/evidence pulls, selected capture and compare-and-swap edit through its supported native interface. Reuse Cairn's authenticated API and existing core gates. Provide repeatable setup and repository/task/run scope from observed host context, with explicit scope when native context is unavailable. Verify the installed Agy client can discover tools, retrieve exact saved versions across sessions, revise selected notes, and honor destination, stale-handle and version-conflict refusals. Then complete a real Agy task using relevant memory saved in another harness and retain its observed use and independently checked outcome. Record client version and unsupported lifecycle hooks; generic MCP compatibility or a scripted protocol test alone does not close the item. Owner request, 2026-09-09. |
| U8 — Claude Code interface | partial | Inspect the installed Claude Code MCP/configuration and permission contract; provide repeatable project setup for Cairn's five ordinary tools. Preserve existing host settings and keep credentials outside the checkout. Use verified native session scope where supported, otherwise require explicit task/run scope. Verify actual discovery, search, exact record/evidence pulls, selected capture/edit, fresh-session reuse, destination refusal and stale-version/handle behavior. Then complete a real Claude task using memory from another harness and retain observed use with an independently checked outcome. Keep attempt attribution distinct from session grouping. Resume/compaction mediation remains X2; an MCP stanza or successful connection alone does not close U8. Owner request, 2026-09-09. |

The [Agy setup recipe](agy.md) reuses the existing MCP server with explicit
repository/task/run arguments. Agy 1.2.0 registration, argument preservation and
configuration updates pass in an isolated home. Its observed settings are
user-wide; no automatic conversation scope or native tool execution is established.
[Verification](verification/agy-configuration-2026-09-09.md) records the remaining
U7 requirements. Further Agy work stays below task value in existing harnesses.

The [Claude configuration generator](claude-code.md) supplies the existing five
MCP tools with explicit task/run scope. Claude Code 2.1.265 accepted its output
and connected in an isolated native check; an independent MCP client retrieved
exact revised content through that generated launch. Native model-selected use,
reliable native session scope and a useful Claude task remain open under U8.
This is the requested lower-priority setup work, not a new task-value result.
A [native execution check](verification/claude-native-tools-2026-09-09.md) now
verifies all five tools and correction/reuse across two scripted Claude sessions,
including destination and permission/refusal behavior. It retains a corrected
assumption about boolean-string coercion; no production fix was needed. A useful
Claude task and model-selected use remain unverified.

The [observation join](use-outcome-loop.md) now associates each exposure with
reduced delivery/usage streams, process outcome and versioned task assessments;
repeated observations do not multiply rows. The
[explicit dynamic-retrieval association](use-outcome-loop.md#retrieval-during-an-observed-run)
now lets the observing host connect agent-owned retrieval receipts to its run's
outcome and latest assessment, while preserving the source exposure and citation
testimony. Store and real-child Unix API tests cover the join and one-execution
population; this is integration evidence, not an additional model-benefit result.
The [use-history CLI](verification/use-history-cli-2026-09-09.md) now exposes the
core's existing page and record controls, reaching later exposure rows beyond
the default oldest-first 100-row page. It preserves unknown observations and
does not supply missing recurrence/resolution analysis or task-value evidence.
The
[run report](use-outcome-loop.md#inspect-runs-including-no-memory-baselines)
also includes zero-memory baselines and claims without outcomes, with separate
claim/process/assessment fields and bounded pagination. The
[summary counter](verification/summary-assessments-2026-09-08.md) now also honors
the latest task assessment, including corrections on runs with no memory exposure.
Task class and binding/capability
metadata are recorded by the wrapper with command digest. Service inference is
labelled with its method and coverage; H0 remains unknown. Closed-task open
delegates have their own docket finding. U4 still needs recurrence/resolution
analysis and real-history evaluation. U1 now has an authenticated Unix API and
agent client, with role/repository/destination enforcement. The
[authenticated process runner](authenticated-runner.md) now carries ordinary H0
launches through an observer identity without database access, preserving managed
context custody and ambiguous-response recovery. [Owner-only run status](run-status.md)
lets hosted observers inspect a possibly committed claim or outcome without
reading protected memory reports or authorizing another launch. Optional
[host attempt linkage](host-attempt-link.md) now validates an already observed
attempt and reserves at most one wrapped execution across prepared receipts,
while leaving host terminal and task acceptance observations independent. A
[real linked Cairn build](verification/host-attempt-link-2026-09-08.md) now verifies
spawn coordination, receipt/attempt correspondence and host result finalization
through the deployed API. Actual Striatum
integration must respect its sealed dispatch-input boundary, as described in
[local API integration](local-api.md). No live Striatum lane is changed. The
[native admission assessment](verification/native-admission-assessment-2026-09-08.md)
reconciles the existing knowledge-promotion source with the historical fixture
proof and records the remaining contract, provenance, sealed-input and real-host
evidence needed to close U1. A recorded Verified target does not supply that
evidence by itself. The [native context direction](native-striatum-context.md)
proposes a declared Cairn observation consumed as an exact packet/run input,
with scope, delivery and host-outcome correspondence preserved. Its opening
capture has an accepted intent head in Striatum's graph; the producer/consumer
extension has no accepted runtime force yet. Its owning RFC 0004/0007 amendments
are now [concrete proposals](native-striatum-context.md#evidence-and-next-contract-work),
with accepted Striatum source pins preserved. The [launch freshness check](launch-freshness.md)
now refuses changed selected memory and required context before a launch claim,
using the existing compiler eligibility rules. [Retained execution](retained-execution.md)
now consumes an exact observer-owned receipt without recompiling; the native
observation producer, admitted ECR resolution and host correspondence are
implemented in the later branch checkpoints below, pending owning contract acceptance. The [installed interface and verification](verification/retained-execution-2026-09-08.md)
also identify Striatum's existing supervisor as the owner of native execution:
use authenticated Cairn operations there without adding a second process wrapper
or another memory copy outside the declared input. The
[host acquisition tool](verification/native-capture-2026-09-08.md) now confirms
the exact observer-owned response and retains an offline-readable capture. It
passed real local/hosted service and child-rendering checks. A
[draft observation producer](native-striatum-context.md#evidence-and-next-contract-work)
now pins captured bytes, emits the typed ECR offline, and validates the submitted
body against its source. Its [trusted request frontend](verification/native-observation-2026-09-08.md)
also passes a Driver request-to-admission fixture and public CLI acquisition
against a real disposable Cairn service. Both branch checkpoints passed full
Striatum checks. The [native build-input checkpoint](verification/native-build-input-2026-09-08.md)
now resolves packet-named evidence, pins and renders the complete context, and
checks its presence in retained launch bytes. The
[native host checkpoint](verification/native-host-2026-09-08.md) now binds that
receipt at the existing supervisor, claims once, observes a real confined child,
and retains delivery/outcome correspondence. A changed selected note refuses
launch. The [complete-chain test](verification/native-chain-2026-09-08.md) now
connects actual observation and build admission through the Driver with a real
Cairn service and a confined shell fixture. A distinct
[native recurrence preflight](verification/native-recurrence-preflight-2026-09-08.md)
now verifies a complete frozen repair workspace and the existing oracle against
both its defective baseline and reviewed fix. Its task-relevant lesson and
three prospective conditions are pinned. The
[native experiment executor](verification/native-executor-2026-09-08.md) now
passes all three model-free conditions through the Driver and supervisor with
usable source, exact prompt receipt and independent repair rejection. The
[model comparison](verification/native-permissions-2026-09-08.md) now records
three invocation timeouts with no admitted repair. Native model contact and
exact delivery are observed; successful repair and memory benefit remain open.
An actual OpenCode scratch-permission defect is reproduced and corrected with
a scripted harness check added to calibration. The [corrected comparison](verification/native-corrected-recurrence-2026-09-08.md)
also produced no admitted repair. This unchanged task/binding comparison is
retired from the active sequence; further usefulness work needs completed real
tasks and practical cross-harness retrieval. Accepted native contracts and
real-build usefulness remain open. A
[current-build check](native-striatum-context.md#current-build-verification-2026-09-10)
confirms that Cairn `a848e3c` still supports the native watch's schema-3 body
capture and supervised launch, including stale-selection refusal. Kind filters,
task phase, failure signatures, semantic discovery and index/pull are not exposed
by that watch. The refreshed guide separates implemented branch work from owning
contract acceptance and the retired model comparison; do not duplicate the
existing producer/consumer/host path. The
[OpenCode trial host](verification/opencode-host-observation-2026-09-08.md) now
uses per-arm authenticated observers, exact package/attempt joins and pending
spawn/terminal recovery. Its full real-harness fixture verification includes
exit-zero with no returned candidate; further real-model usefulness and native
Striatum ingress remain open.

A [real-model observed failure](verification/observed-model-failure-2026-09-08.md)
now exercises that host's timeout finalization, exact attempt/receipt/result
correspondence, instrumented rejection and citation testimony. The candidate
failed the cache and explicit-override checks despite citing the selected lesson.
Independent review isolated an environment-order defect and converted its
generated failure proposal to a local, unpromoted A lesson in the disposable
trial store. This adds real host and review-loop evidence; it does not close
native ingress, full task acceptance or general usefulness.

### 3. Make retrieval current and evaluate real history

[Configurable semantic idle retention](verification/semantic-idle-2026-09-10.md)
now avoids repeated cold embedding during spaced follow-ups on this host. A
five-minute setting reduced one follow-up after 35 idle seconds from 24.851 to
0.063 seconds with matching complete results, retaining about 220 MiB RSS. The
thirty-second default, current eligibility and request deadline remain intact.
This reduces observed retrieval cost; cold inference, larger workloads and
sustained task-value evidence remain open.

A [currentness repair](verification/applicability-precedence-2026-09-09.md) prevents
an earlier missing pin from masking a known mismatch in a later pin. Inapplicable
instructions no longer block unrelated tasks; unknown applicability, private
exclusion and historical decisions retain their contracts. E1 remains partial
for its other context dimensions and broader task evidence.

[Per-call native context](search-context.md) now lets MCP and OpenCode searches
declare a phase, revision or other existing context field that the host left
unset. This connects native capture of restricted notes to retrieval as a task
changes, without restarting an unpinned facade. Fixed host values cannot be
overridden; declarations do not carry into later calls. [Verification](verification/search-context-2026-09-10.md)
covers applicability and existing tool boundaries. Automatic context observation
and task benefit remain open.

A [native argument repair](verification/opencode-unicode-2026-09-10.md) now refuses
malformed Unicode before process launch, after a real fixture returned a context
label different from the supplied declaration. Valid Unicode and refused-ID
recovery pass through native debug and scripted normal sessions. Retained review
guidance informed this repair; incremental memory benefit remains uncertain.

The [evidence capture transport repair](verification/evidence-limit-2026-09-09.md)
allows authenticated clients to use the store's existing 1 MiB inline source limit,
including heavily escaped JSON. Evidence alone has an 8 MiB encoded request cap;
other requests, decoded storage limits and pull budgets are unchanged. This closes
a capture-access mismatch, not managed large artifacts or source freshness under L4.

The [documentation retrieval experiment](verification/retrieval-quality-2026-09-08.md)
freezes fifteen answerable questions and two no-answer controls before changing
the ranker. Recognizing underscore-separated identifier words improves one
measured case without lowering other labelled answer ranks. Vocabulary mismatch,
common-word noise and recency ties still cause misses or unrelated results.
This is retrieval development evidence; E4 task benefit and transfer remain open.

The [question-word comparison](verification/question-words-2026-09-08.md) removes
an observed false match on “what” and reduces extra results on two answerable
questions. It also records a storage-location rank regression and continuing
vocabulary misses. Ranker v4 retains historical v1–v3 behavior for recompilation;
these development results do not close E4.

The [fixed BM25 screen](verification/bm25-screen-2026-09-09.md) produced mixed
ranks on that same reused workload: top-three counts 13/13 versus lexical 13/12,
but first-place counts 10/10 versus 11/10. Production lexical v4 remains. Stop
tuning these questions; revisit length-aware ranking with actual long-note
failures and relevance labels fixed before scoring. This does not establish or
reject sustained task value.

The [long-note prefix screen](verification/leading-context-2026-09-10.md)
reproduces an actual setup query that ranks OpenCode ahead of Codex. One fixed
tie-break improves dedicated-note ranks on twelve questions, but subject-derived
labels favor it; production lexical v4 remains. Do not tune that new question
set either. Revisit ranking with naturally arising detail questions. Existing
semantic procedure filtering resolves the setup query, while the unfiltered
twenty-note corpus returns lexical fallback and a separate worker run takes
20.015 seconds against the API's twenty-second deadline. The live fallback cause
is unconfirmed. Investigate current cold semantic cost before changing ranking
or timeout defaults; retain full notes, eligibility and score semantics.

The [cold-workload follow-up](verification/semantic-deadline-2026-09-10.md)
finds inference dominates the current fifty-chunk corpus. A fixed batching
candidate preserves scores but misses its target and increases memory, so it is
rejected. A separate twenty-five-second worker budget lets the unchanged model
finish that corpus through both transports while remaining below native/API
outer deadlines. This allows five seconds more work and waiting; it does not
reduce inference cost or establish task benefit. Larger-corpus cost remains open.

[Ranked search continuation](verification/search-pages-2026-09-09.md) now reaches
later eligible matches through opt-in lexical and semantic pages in CLI, MCP and
OpenCode. Required instructions, per-page budgets and historical replay remain;
each page reads current state. This removes a navigation limitation without
changing ranking or establishing task benefit.

The [local semantic comparison](verification/semantic-retrieval-2026-09-09.md)
recovers every labelled documentation answer within three results, including the
lexical vocabulary misses. It also worsens some first-place answers and retrieves
unrelated neighbours for no-answer controls. This supports an optional semantic
discovery experiment that preserves lexical search, structured eligibility,
mandatory selection and budgets. The integrated follow-up below now supplies
store and long-note evidence; downstream benefit remains open.

[Optional semantic discovery](semantic-discovery.md) now connects the CPU worker
to actual structured eligibility and budgeted index packing. The integrated
development corpus retains all fifteen labelled answers within three results;
a 14 KB note's final guidance is found and pulled intact. Mandatory selection,
hosted filtering, stale handles, frozen-score recompilation, bounded workers and
labelled lexical fallback are checked. Native search tools expose the opt-in
route, and the installed API now enables the prepared worker. Fresh Codex and
OpenCode clients both found and pulled the exact updated storage note through
that route. Larger-scale behavior, independent source use and task benefit remain open;
this does not close E4.
A [one-thread comparison](verification/semantic-threads-2026-09-09.md) preserved
all twenty-one score sets but missed its latency target. The installed two-thread
worker remains; reduced CPU cost alone did not justify the proposed latency change.

A [small-set startup experiment](verification/semantic-startup-2026-09-09.md)
addresses the different workload enabled by kind filtering. Six interleaved pairs
preserved complete responses while model reuse saved 0.34–0.58 seconds.
[Optional API-owned residency](verification/semantic-residency-2026-09-09.md)
now implements idle/shutdown release, framed responses, one active request,
per-request cancellation and labelled fallback. One-shot custom workers remain
supported. Six paired API requests saved 0.56–0.83 seconds with matching candidate
score digests; real-worker tests verify current edits, retirement and local-only
exclusions. This closes that bounded implementation checkpoint, not task-value
evaluation or performance at larger scale. That checkpoint retained no note-vector cache.

[Bounded vector reuse](verification/semantic-vector-cache-2026-09-09.md) now avoids
re-embedding unchanged eligible notes while that worker lives. Thirteen real-model
comparisons preserve complete scores; six warm queries drop from 1.85–2.29 seconds
to 0.034–0.044 seconds on the public 12-note workload. Current edits/exclusions and
worker cleanup pass the real API checks. This reduces repeated retrieval cost; it
does not establish better answers or sustained task benefit.

[Task-file guidance maintenance](verification/task-handoff-guidance-2026-09-09.md)
repaired an observed missed question by adding the existing `--prompt-file` option
to the saved OpenCode procedure. The selected question now retrieves that note
first in lexical and semantic modes. This is coverage maintenance with explicit
comparison limits; E4 and sustained task value remain open.

[Explicit browsing](verification/browse-2026-09-09.md) now exposes the existing
empty-query index through the agent CLI, MCP and native OpenCode. It provides
bounded topic previews when the saved vocabulary is unknown, with normal scope,
destination, mandatory-context and pull checks. It does not resolve lexical
mismatches or guarantee that older notes fit the preview budget.

[Browse continuation](verification/browse-pages-2026-09-09.md) now reaches older
notes over multiple pages at the unchanged per-call budget. Installed CLI, native Codex and
normal OpenCode checks reached the storage note on page two. Each page reads current
state and repeats required context; aggregate context remains the host
responsibility. This closes the observed preview access gap, not E4 task benefit.

[Kind selection](verification/kind-filter-2026-09-09.md) lets a caller use existing
labels to find decisions and preferences without first paging through setup
procedures. It applies to browsing, lexical and semantic discovery through the
existing interfaces, preserving required context and access checks. Labels remain
fallible, and passing retrieval checks does not close task-value requirements.

[Matching index previews](verification/index-previews-2026-09-08.md) address an
observed operational search whose preview hid the matching command later in the
note. Bounded source excerpts make that match visible while preserving full-body
pulls and old index recompilation. Ranking and vocabulary misses remain; reduced
pull cost and downstream task benefit have not been measured.

[Ordinary MCP edits](verification/mcp-edit-2026-09-08.md) let a harness revise a
pulled A note through the existing authenticated edit contract. This closes the
MCP correction-interface gap exposed by the configuration procedure revision;
independent agent review and automatic learning remain open.

An [operational note review](verification/operational-note-review-2026-09-09.md)
corrected an ambiguous OpenCode permission example and updated both harness
procedures with the deployed discovery options. Fresh native clients pulled the
exact revised bodies. This is manual maintenance of useful guidance; independent
correction and downstream task benefit remain open.

[Authenticated retained history](record-history.md) now lets a reader list version
metadata and read one exact earlier body for comparison without operator database
access. Current repository/destination and deletion restrictions remain; historical
inspection supplies no current authority. This supports selected maintenance and
does not close cutoff-recompilation or task-value requirements.

The same history read is now available as `cairn_history` through MCP and native
OpenCode, so comparing earlier guidance does not require shell access. The facade
fixes repository scope, preserves historical labels and applies its output budget.
Current search/pull and edit-version checks remain separate. The
[native history verification](verification/native-history-2026-09-10.md) records
capability and a review of existing guidance; no new model-task benefit is claimed.

[Preview source positions](verification/preview-locations-2026-09-09.md) now connect
matching A/B previews to existing bounded body pulls. Agents can request the
identified passage without guessing its offset or first reading the whole note.
The new metadata counts toward index budgets; historical results remain exact.
This improves access to retained guidance without closing E4's task-value work.

[Body-only revision](verification/body-revision-2026-09-09.md) now lets ordinary
MCP/OpenCode callers correct text without reconstructing scope, applicability,
relations or attribution. The store preserves those fields under the existing
version and retry checks. Full-draft edits remain supported; independent review
and measured correction-task benefit remain open.

The [stale-note correction exercise](verification/note-correction-2026-09-09.md)
returned a wrong answer and invalid draft with stale guidance. Its model corrector
hit the output limit without editing, so downstream and direct-source conditions
were not run. A compliant no-memory abstention is retained as unknown after
assessment review. Separately, source review corrected obsolete guidance in the
live Codex procedure and verified a fresh native pull. This is actual maintenance;
independent correction and downstream benefit remain open. Keep the existing
body-only interface and seek observed reuse before extending storage machinery.

A subsequent [actual OpenCode tool experiment](verification/tool-retrieval-2026-09-08.md)
recovered from the failed query and completed the source-answering task through
index/pull. Pairing each result with a complete pull command removed three observed
UUID-confusion errors in one follow-up. This remains an opt-in experimental adapter;
native integration, broader harness use and learned-lesson transfer remain open.

The reusable [agent search/pull CLI](index-and-pull.md#agent-commands-without-request-json)
now brings complete pull commands into the shipped client, with explicit task/run
scope, mandatory-context preservation, quoted paths and ordinary API authentication.
It replaces request-JSON construction for shell-capable agents. This packaging
does not itself establish task benefit, global task budgets or native H1 admission.
An [actual OpenCode invocation](verification/agent-search-2026-09-08.md) now uses
these commands directly and joins four retrievals to one observed execution.
Its source-supported answer failed the prospective JSON parser because of
surrounding prose; the original rejected assessment remains. Broader task value
and transfer remain open.

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| E1 — currentness and intent | partial | Declared repository revision/dirty-workspace identity, validity, task class/phase and capability/binding pins are implemented. [Task phases](currentness-and-replay.md#declared-task-phases) now narrow guidance within one task class across CLI, MCP and native OpenCode; missing mandatory context, inheritance, retained-run intent and old receipt seals are verified. Ordinary CLI/MCP/OpenCode capture accepts explicit applicability pins without inheriting search context. All database readers must be upgraded before phase constraints are written. [Quoted search](quoted-search.md) now prefers exact body text for supplied identifiers, paths and error messages, without changing eligibility. [Reviewed failure signatures](failure-signature-search.md) now retrieve exact converted lesson versions through CLI/MCP/OpenCode, with explicit association sharing and historical replay. Structured entities/files and automatic host intent collection remain open; executable currentness predicates are an optional later experiment. Continue testing obsolete guidance across code revisions and mismatched scopes. |
| E2 — compact index and pull | partial | Deliver minimal bootstrap plus bounded index; add authorized record/body/evidence expansion. H0 uses permitted overlay or fresh compile, never imaginary mid-run hooks. H1 later gets opaque expiring handles and expansion credits. Test budget, destination, revocation and stale-handle behavior. Core and native-tool index/pull are implemented. [Observed index execution](observed-index.md) adds fresh and retained runner input with a designated ordinary expansion reader; observer receipt ownership and current pull checks remain. [Explicit compact startup](compact-start.md) now supplies an initial index through an ordinary hosted profile; native OpenCode stdin delivery and permission-checked pulls are verified. [Task-file input](verification/start-task-file-2026-09-09.md) also preserves a saved specification without shell newline stripping. [Observed task-file input](authenticated-runner.md#read-a-saved-task) extends saved specifications to fresh and retained body/index runs while keeping retrieval intent separate; oversized argv input refuses before binding. Aggregate task budgeting remains incomplete. Native OpenCode allowed/denied/stale delivery checks pass with a scripted provider; [verification](verification/observed-index-2026-09-09.md) establishes capability, not task benefit. See the [acceptance review and intent repair](verification/retained-kinds-2026-09-09.md). The [native startup investigation](verification/opencode-startup-hook-2026-09-09.md) rejects an unconditional OpenCode system-hook fetch: the hook also reaches auxiliary requests and is not gated by tool permissions. The explicit launcher preserves receipt caller identity and mandatory context without installing that hook; [verification and remaining limits](verification/compact-start-2026-09-09.md). |
| E3 — cutoff-pinned recompilation | partial | Recompile historical selection under cutoff/revision/policy/evidence pins. Keep saved-byte replay as inspection. Distinguish original-time availability from recurrence scenarios. Test that later claims, revocations and evidence state cannot leak backward; test deterministic reconstructed seals where the contract promises equality. |
| E4 — real corpus and baselines | partial | Inventory authorized current Striatum history; seed justified B from existing evidence and include real correction/supersession/conflict cases. Do not use historical decision counts as current inventory. Compare no-memory, search/native and H0 runs on reproducible tasks, context cost and repeated failures. Publish bounded usefulness findings, including failures and unknowns. Three repository notes and a wrapped `cat` are not acceptance. |

The [semantic batching comparison](verification/semantic-batching-2026-09-09.md)
checks a lower-cost local inference path against the same public development
workload. This changes execution cost, not the evidence for task benefit or the
status of E4.

[Currentness and replay](currentness-and-replay.md) now implement declared
revision/workspace/task-class/binding/capability and validity gates. Missing pins
cannot suppress a mandatory instruction. Semantic v3 seals context; old formats
remain readable. Explanation v2 preserves gate facts for historical ranking and
packing from the named receipt's read set, with input digest and result-seal
checks. Later B corrections and evidence changes cannot rewrite that selection.
Declared task-phase matching, quoted-text preference and reviewed failure-signature
matching are implemented. Arbitrary historical cutoffs without retained
observations, structured entity/file matching and broader real-history usefulness
remain open.

A [retained real-failure check](verification/real-signature-retrieval-2026-09-10.md)
now verifies signature lookup against the original local lesson after a new
exact-version review in an isolated copy. It also exposes the applicability limit:
the lesson requires its historical revision. Missing or different revision omits
it; the lesson remains local. All three diagnostic text queries already rank it
first, so this case shows no ranking advantage or added task benefit. Keep further
signature expansion behind an observed retrieval need. Prioritize ordinary task
use, including qualitative and cumulative benefit across turns, with the existing
Codex/OpenCode interfaces. This does not close E1/E4/D1/D2 or supersede the original
failed trial.

[Index and pull](index-and-pull.md) now delivers a sealed bounded pointer index
with full mandatory bootstrap and expiring caller/destination-bound handles.
Pulls spend credits transactionally, recheck current authority and version even
on retries, and record expansion without rewriting the original index exposure.
Historical index recompilation works. H0 supports pre-launch body compilation or
explicit observed index input with an ordinary tool route;
[Supporting-evidence pulls](index-and-pull.md) now return exact selected captured
bytes with a digest pin, current authority/destination checks and shared expansion
credits. The real Unix CLI/API probe verifies hosted disclosure without client DB
access. Actual H1 model routing and overall host task budgets remain open.

A [native-history coverage trial](verification/history-coverage-2026-09-07.md)
now uses three committed accepted clauses and 12 native commit subjects in a
disposable store. All read sets recompile exactly; the scenario availability is
explicitly later recurrence. A subsequent [real OpenCode repair trial](verification/opencode-recurrence-2026-09-07.md)
compares repository-only, native-excerpt and Cairn H0 inputs on one historical
defect. No arm produced a repair; a corrected preflight now refuses missing
memory treatment. Real receipt/outcome joins are retained, with usage unknown.
Two [harness calibrations](verification/opencode-calibration-2026-09-07.md)
then disabled thinking and increased context; neither produced a repair. A
provider-free probe now verifies actual read/edit execution through the wrapper.
Two [hosted calibrations](verification/opencode-hosted-calibration-2026-09-08.md)
also timed out without patches. The second removed relay request-size refusals,
but one response reached the relay's elapsed limit and the run reached its
configured step allowance. The hosted route keeps credentials outside the model
sandbox and records actual provider/request failures. Further calibration must
account for these execution limits before diagnosing model capability.
An [extended-budget calibration](verification/opencode-extended-calibration-2026-09-08.md)
then completed normally within its 900-second/60-step allowance, with no relay
refusals and still no patch. One output-length finish remains in the trace.
The unchanged binding/task condition is not a useful next experiment.
Three [Pro calibrations](verification/opencode-pro-calibration-2026-09-08.md)
then separated a routing refusal from response exhaustion. The larger-response
run produced a cache repair, but violated the declared file scope and introduced
a reproduced inherited-environment regression. Post-run checks also exposed an
overly narrow cache-location gate: dispatch workspace and runtime working
directory are distinct. Preserve the original result and declare corrected
criteria before another run. This supplies a real failure for a later-recurrence
lesson; avoided failure remains unobserved. Full native/search baselines,
relevance judgments and memory-benefit evidence remain open.

A [reviewed-lesson recurrence](verification/reviewed-recurrence-2026-09-08.md)
now has a positive bounded observation: the no-lesson candidate repeated an
inherited-environment failure, while both lesson candidates avoided it. H0
selected the exact reviewed B version, the model cited it, and the run finished
within 900 seconds. The direct-lesson run timed out. All original gate results
and later reviews remain separate; overall acceptance is unknown for the lesson
runs. This is one known-case comparison, not general or causal benefit evidence.

### 4. Turn observed demand into next-run improvement

Depends on U4/U5 and E3/E4. R4's early protected event does not satisfy this stage.

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| D1 — evidence-attached demand docket | partial | Failure→recovery pairs and standalone explicitly assessed task failures share evidence-attached review dispositions. [Exact-signature review groups](demand-review.md#review-matching-failures-together) combine current due proposals across tasks with matching repository/task-class/binding/capability fields, preserving each source and witness. [Verification](verification/proposal-groups-2026-09-08.md) covers 105-member pagination and inspection of the restored real model failure. Current pairs suppress duplicate standalone demand without rewriting source history. A [restored real-trial check](verification/standalone-demand-2026-09-08.md) verified generation and dismissal with zero memory exposures. [Versioned conversion history](demand-review.md#preserve-the-linked-lesson-version) now retains the exact linked lesson version and earlier review decisions after reopening or editing. Legacy pins remain unknown. [Signature retrieval](failure-signature-search.md) now uses current exact conversion links on later tasks; hosted use requires explicit association sharing and normal lesson eligibility. General novel-failure discovery, richer conflict/promotion groups and measured review burden remain open. |
| D2 — adaptation acceptance | partial | A reviewed, evidence-backed lesson was promoted, selected and cited in a real H0 run that avoided the baseline's environment regression within budget. Preserve the bounded [recurrence result](verification/reviewed-recurrence-2026-09-08.md), negative results and unknown overall acceptance. Broader task/host acceptance and transfer remain open. Exposure alone must not amplify ranking. |

D2's value evidence may include reviewed qualitative or indirect improvements,
with sources, reasoning and alternative explanations under the evaluation guidance
above. Mechanical task acceptance is one kind of evidence, not the definition of
value or a universal prerequisite for a supported qualitative claim.

The [demand review path](demand-review.md) now generates bounded, source-attached
failure/recovery proposals from assessed task outcomes. It excludes binding
failures and unknown comparison labels, groups duplicate pairs, and retains
versioned deferral/dismissal/conversion decisions. New source assessments make
old proposals stale. The docket prioritizes existing correction/recovery work.
Novel-failure grouping and demonstrated next-run benefit remain open; no groomer
or automatic promotion has been added.

### 5. Complete lifecycle, governance and recovery gates

These are remaining accepted requirements, not an invitation to restore the
withdrawn universal ledger architecture. Complete them before declaring full
Stage 1–2 acceptance or scaling sensitive/consequential retention.

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| L1 — class-proportional lifecycle | implemented | Ordinary revisions, [unreferenced A deletion](ordinary-delete.md), cheap B→A and [cross-record supersession](supersession.md) preserve retained history and apply their class-specific guards. [Scope authorization](scope-authorization.md) now expands applicability within a repository through an audited C decision, retains independent B/C qualification and supports explicit scope reauthorization. Tests cover surviving history, known impact notices, authority/dependency/refusal boundaries, concurrent CAS and backup/restore. This does not close the separate governance, deletion, retention or restore-admission gates below. |
| L2 — conflicts and refusal records | partial | [Conflict inspection](conflict-inspection.md) now exposes original versioned positions, participation and resolution history, with scope/destination checks and forgotten-body exclusion. Qualified positions, richer interested-party resolution and acted-under-open-conflict outcomes remain open. Durable policy refusals exist. Whole-group omission of optional disputed content remains allowed; closing a group preserves its original context. |
| L3 — governed policy | partial | [Versioned repository policy](governed-policy.md) now implements authorized revisions, rollback as a new revision, sealed policy/budget pins and policy-dependent run queries. Fresh delivery refuses stale or revoked policy; historical packages and late outcomes remain available. The initial rule format narrows optional-memory budgets within existing ceilings; [instruction category caps](instruction-limits.md) add explicit count/body-token limits, mandatory overflow refusal and bounded index admission. C waivers and the complete unenforceability decision table remain open; mandatory runtime refusal remains enforced. |
| L4 — evidence lifecycle | partial | Inline explicit text/binary capture, read/status, hash checks and check history, reverse impact, byte-span pulls and [precise supporting citations](evidence-citations.md) now exist. New qualified references pin a full-source digest and optional passages on each claim version; older links retain absent citation metadata. [Ordinary source citations](evidence-citations.md#ordinary-notes) now let agents retain and pull exact support without qualification; citation edits and ordinary text revisions preserve earlier source history. Managed large artifacts, broader source relations, upstream freshness and richer dependent invalidation remain open. |
| L5 — class D and deletion | partial | Record-body forgetting, cited-A D escalation, tombstones and database purge effects now work. Remaining: metadata/evidence redaction, access-policy transitions, historical-copy adoption and additional external effects. New registered context-file purge is implemented. Include context files, canonical receipts, selected content copies and backup residuals, including pre-R1 task/query text. Enumerations are not functionality. Test worker crashes, repeated requests, citation/delete races and truthful incomplete purge status. |
| L6 — retention | open | Enforce record_use's 90-day minimum with extension while decisions remain live/reviewable, audited B/use pruning and backup residual tracking. Infinite retention currently preserves the floor but does not implement forgetting. Test cutoff/dependency boundaries and recovery. |
| L7 — import and capture policy | partial | Explicit native/repository import quarantine, inherited source restrictions, per-class capture policy and secret handling. Existing advisory labels and hosted local-content exclusion remain. Test laundering attempts through imports and selected artifacts without adopting a hostile-agent threat model. |
| L8 — unsigned C/D checkpoints | partial | Reproducible checkpoint of the audit subset, expected restore-set catalog and verification. Dump checksum alone is insufficient. Signing/off-box keys stay withdrawn. Test missing/altered audit members and restore set. |
| L9 — restore reconciliation | partial | Rebuild projections and freshly recompile under identical pins; verify state/audit, reapply newer revocations/deletions, invalidate handles and recover external effects. Restore delivery generations now fence old launch/binding/pull paths while preserving history. External recovery exports detect later withdrawals and missing context custody in actual older restores. [Deletion dependency repair](verification/deletion-dependencies-2026-09-08.md) propagates exclusions through later relations, backfills older missed descendants and verifies the retained projection. [Auditable reapplication](recovery-reapplication.md) now applies known revocations/retractions/forgetting, retains original missing-history expectations and queues post-backup custody for the ordinary deletion worker. [Restore admission](restore-admission.md) now gates ordinary core/API transactions, rebuilds known deletion exclusions, checks retained state and compiler fixtures, and requires current-root explicit resume. [Verification](verification/restore-admission-2026-09-08.md) covers a real older dump and refusal before effects finish. External-source freshness, out-of-procedure restore detection and reconstruction of unknown lost state remain open. |
| L10 — fault and conformance acceptance | partial | Add abrupt process death around privileged commit, D-worker/citation races, task-close open loops, policy rollback, cutoff replay and adapter transformations. Keep CAS, identity, attribution, grant, evidence, budget and process tests. Record which gate each test establishes; green checks do not imply all-stage acceptance. |

[Versioned relations and demotion](relations-and-demotion.md) now implement
bounded derived/specializing/contradicting links with inherited scope, currentness
and sensitivity restrictions. B→A is unaudited, preserves consumed history and
refuses active C/open-conflict dependencies. Retraction previews include known
transitive uses and reject changed dependency/exposure state. Ordinary deletion
now preserves referenced and formerly privileged records by requiring the audited
forgetting path. Supersession now preserves explicit pinned replacements and
known impact notices. Scope authorization expands applicability through a
separate revocable C grant without renewing independent claim qualification.

A [withdrawal regression](verification/withdrawal-after-forgetting-2026-09-08.md)
is repaired: an authorized retraction can retire an instruction after a cited
source is forgotten, without creating a new citation or erasing the old link.

A [hosted-policy regression](verification/private-policy-applicability-2026-09-08.md)
is repaired: expired or context-mismatched private instructions no longer refuse
unrelated hosted compilation. Applicable or unknown mandatory requirements still
refuse; private candidates remain outside the hosted explanation and census.

[Durable refusals](refusals.md) now preserve bounded metadata for compile policy
failures and blocked demotion/retraction, without raw queries or record bodies.
Transport repeats group by exact intent and status; failed observation writes
are explicit. Other policy paths and refusal analytics remain open.

[Evidence refresh](evidence-refresh.md) now records inline check generations and
history, atomically invalidates affected impact previews, and blocks proposal
conversion when selected evidence is unavailable. Historical gates remain frozen.
[JSON Unicode validation](verification/json-unicode-integrity-2026-09-09.md)
rejects serialized inputs that would otherwise capture replacement characters,
before reserving request identity. Valid text and binary representations remain.
[Selected-file capture](evidence-refresh.md#capture-a-selected-file) reads and encodes
chosen regular files through the existing CLI capture operation, with a required
source label and local default. It retains no automatic file locator or freshness
claim. [Explicit binary capture](evidence-refresh.md) preserves arbitrary source bytes
through API/CLI base64 input, including retry identity and the decoded 1 MiB limit.
Managed artifacts, scheduled checks, source-span constraints and deletion remain open.

[Record forgetting](deletion.md) now excludes retained record bodies, cached
mutation responses and canonical package copies atomically. A retryable database
purge worker preserves per-target progress, bounded failures and explicit residuals.
Tests cover citation races, dependency exclusion, abrupt worker death and restored
pending effects. Existing operational records have not been forgotten. Historical run-file adoption, metadata/evidence redaction, backup inventory, newer-deletion restore
reconciliation and automatic retention remain required.

[Managed context files](managed-context.md) now register new run slots before
writing, serialize writers and purgers with a verified directory lock/ownership
marker, and retain retryable file effects. Actual worker death after unlink but
before DB completion is covered by the lifecycle drill. Old unregistered paths
remain explicit residuals; registration does not invent custody of those files.

[Audit checkpoints](audit-checkpoints.md) now cover the emitted governance/C audit
subset, including implemented forgetting events, with explicit membership and no payload/reason commitment. Backups retain
an external expectation catalog. Restore tests recompile from restored candidate
inputs and reject missing newer expectations. Automated scheduling and remaining D operations remain open. Known newer
withdrawals and deletion projections are reconciled by the explicit restore
workflow; unknown lost state cannot be reconstructed from checkpoint hashes.

[Recovery inspection](recovery-inspection.md) now compares an isolated restore with
a separately retained later withdrawal/audit/custody record. A real older dump
correctly reports revived grants and content plus missing audit and file custody.
This is read-only consistency evidence. [Restore fencing](restore-fencing.md) now
invalidates old launch claims, binding retries and pulls without rewriting history.
Its authenticated host claim endpoint requires a fresh receipt after the fence.
Auditable reapplication and imported custody now have an actual older-dump drill.
[Restore sessions](restore-admission.md) now block ordinary transactions until
current-root verification and explicit resume under `local-restore/1`. The drill
checks pause, reapplication, purge, known projection rebuilding and retained
compiler fixtures. Independently establishing export freshness, detecting a
restore outside that procedure and complete reconstruction remain open.

### 6. Later extensions

| ID | Status | Entry condition and work |
| --- | --- | --- |
| X1 — advanced H1 integration | open | After useful ordinary ingress: richer query/expansion/candidate tools, expiring handles and driver conformance profiles. Ordinary Agy integration is tracked separately under lower-priority U7; it does not require this extension. |
| X2 — native interlocks | open | Claude/Codex observed resume/compaction handling, reinjection, per-run attestation and privilege forfeiture on failed refresh. Require adapter-specific evidence. Ordinary Claude Code access is tracked separately under lower-priority U8. |
| X3 — groomer | open | Only after join → replay → demand docket: bounded offline proposals from instrumented session-close/idle extraction and nightly consolidation, with review and no automatic authority gain. |
| X4 — ranking experiments | optional | Learned ranking/decay only after measurable outcomes and explicit policy. Counterfactual experiments opt-in and never during incidents. Deterministic lexical ranking is the baseline. |
| X5 — selective H3 and sharing | open | Runtime mediation for selected high-stakes paths, then broader sharing with enforced destinations. Hermes remains interactive outside initial build adapters. |

## Deliberately excluded

Do not reintroduce universal event sourcing/hash chains, universal content-addressed
identity, complete ancestry, immutable retention for every note, signed checkpoints,
off-box keys, compulsory SQLite or per-agent database roles. Universal
asserted/observed payload blocks were replaced by trusted-channel attribution.
Deliberate identical resubmissions may create distinct rows; bounded grouping and
no authority gain from repetition are the requirements. No automatic promotion,
embedding service or new distributed infrastructure is needed for the first loop.

## Verification and operating boundaries

Use disposable PostgreSQL for behavioral and migration tests: `make test-integration`
and `make check`; use `make test-lifecycle` for backup/restore changes. Preserve
applied migration checksums and advance schema with new migrations. Do not test
against the local operational store. Make accepted source references, tests,
remaining limits and status updates part of each completed slice. Real model
usefulness needs E4/D2 evidence beyond these checks.
Append each completed slice to `implementation-status.md`'s history, including
deployment and negative evidence. Refresh its current summary without deleting
prior entries; add dated corrections when earlier claims become obsolete or wrong.
