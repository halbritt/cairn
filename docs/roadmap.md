# Cairn roadmap

Updated 2026-09-07. Baseline: `e3b47c7` (local memory and task delivery loop).

Cairn has a transactional PostgreSQL core and a manually fed process wrapper.
The next product milestone is memory used in real Striatum/OpenCode builds, with
record-specific observations showing whether it prevents recurring failures. Passing the
current tests does not establish that result or full design acceptance.

Status: **open**, **partial**, **implemented/tested**. Completion means the stated
acceptance evidence exists; deployment and measured usefulness are separate.
Items can be delivered in smaller commits without marking the whole item complete.

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
| R3 — retraction preview | partial | Require an impact preview before retraction; transactionally reject stale version/dependency/use state. Show affected versions, records and runs, including evidence and known corrected/superseded dependents. Test new exposure between preview/commit and concurrent compile/retract, plus open-conflict refusal. Optional `impact` invocation is insufficient. Sources: v3 §2.1, design §9.3. |
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
No legacy copies were purged and no real model usefulness claim is made.

Baseline synthetic probes confirmed R1–R4 on `e3b47c7`; R5 is a source/schema
finding. `report`, `replay`, and `docket` currently provide narrower operations
than the accepted usefulness features with those names.

### 2. Establish the observed use/outcome join

Depends on capture repair and protected receipt boundaries. This is the first
accepted usefulness milestone, before historical evaluation and generated demand.

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| U1 — real host and agent ingress | partial | Integrate real Striatum/OpenCode H0 spawn/terminal boundaries with authenticated channel identity and stable task/run/attempt/binding IDs. Add narrow local agent access or in-process embedding. Current CLI stamps one OS UID, so its own A notes cannot independently self-promote to B. Keep operator administration distinct. Test real input route, terminal/recovery paths and one authorized wrapped build; synthetic adapter fixtures alone do not qualify. |
| U2 — bounded run evidence | partial | Add command/argv digest, explicitly selected artifact/diff digests, permitted failure signatures, retry correlation and externally verified task outcome with correction. Process exit/duration/stream digests already exist. Default raw prompts/outputs/secrets/workspace capture stays off. Test allowed fields and absence of raw bodies. |
| U3 — binding versus capability failures | implemented/tested | Represent execution binding separately from capability identity; distinguish quota, credentials, transport and adapter failures from task/model results. Enforce no capability inference from binding failure in schema/API tests. Exit zero continues to mean process status, not task acceptance. [Known pre-launch failures](verification/prelaunch-failure-2026-09-08.md) retain `not_attempted` without writing into refused artifact paths. |
| U4 — record-specific outcome join | partial | Join exposure, delivery, citations/expansions, outcomes and corrections by record/version and comparable task class/scope. Report coverage, unknowns, recurrence, resolution cost and uncertainty without multiplying rows across observations. Current independent counters are only operational telemetry. Verify multi-record/multi-observation fixtures and missing observations. |
| U5 — usage ladder | partial | Preserve cited > expanded > behaviorally implicated > delivered-only, with inference method/version and observation coverage. Connect actual citation/expansion ingress; manual testimony does not prove use. Do not infer unknown H0 tool actions or benefit from delivery. Test coverage-aware reporting. |
| U6 — completed tasks with open delegates | implemented/tested | Add task completion observations and detect still-open service-observed attempts at completion. Existing exact-attempt attribution/failure recovery stays intact. `UNFINISHED_RUN` is a separate wrapper-recovery hint. Test late terminal recovery and completed-task open attempts. |

The [observation join](use-outcome-loop.md) now associates each exposure with
reduced delivery/usage streams, process outcome and versioned task assessments;
repeated observations do not multiply rows. The
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
agent client, with role/repository/destination enforcement; actual Striatum
integration must respect its sealed dispatch-input boundary, as described in
[local API integration](local-api.md). No live Striatum lane is changed.

### 3. Make retrieval current and evaluate real history

| ID | Status | Work and acceptance evidence |
| --- | --- | --- |
| E1 — currentness and intent | partial | Add repository revision/dirty-workspace identity, validity, task type/phase, entities/files/error signatures and capability/binding pins. Define ordinary declared pin matching first; executable currentness predicates are an optional later experiment. Test obsolete guidance across code revisions and mismatched scopes. |
| E2 — compact index and pull | partial | Deliver minimal bootstrap plus bounded index; add authorized record/body/evidence expansion. H0 uses permitted overlay or fresh compile, never imaginary mid-run hooks. H1 later gets opaque expiring handles and expansion credits. Test budget, destination, revocation and stale-handle behavior. |
| E3 — cutoff-pinned recompilation | partial | Recompile historical selection under cutoff/revision/policy/evidence pins. Keep saved-byte replay as inspection. Distinguish original-time availability from recurrence scenarios. Test that later claims, revocations and evidence state cannot leak backward; test deterministic reconstructed seals where the contract promises equality. |
| E4 — real corpus and baselines | partial | Inventory authorized current Striatum history; seed justified B from existing evidence and include real correction/supersession/conflict cases. Do not use historical decision counts as current inventory. Compare no-memory, search/native and H0 runs on reproducible tasks, context cost and repeated failures. Publish bounded usefulness findings, including failures and unknowns. Three repository notes and a wrapped `cat` are not acceptance. |

[Currentness and replay](currentness-and-replay.md) now implement declared
revision/workspace/task-class/binding/capability and validity gates. Missing pins
cannot suppress a mandatory instruction. Semantic v3 seals context; old formats
remain readable. Explanation v2 preserves gate facts for historical ranking and
packing from the named receipt's read set, with input digest and result-seal
checks. Later B corrections and evidence changes cannot rewrite that selection.
Arbitrary historical cutoffs without retained observations, entity/file/error/phase
matching, and the real-history usefulness trial remain open.

[Index and pull](index-and-pull.md) now delivers a sealed bounded pointer index
with full mandatory bootstrap and expiring caller/destination-bound handles.
Pulls spend credits transactionally, recheck current authority and version even
on retries, and record expansion without rewriting the original index exposure.
Historical index recompilation works. H0 remains pre-launch body compilation;
actual H1 routing, evidence-body expansion and overall host task budgets remain open.

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
| D1 — evidence-attached demand docket | partial | Failure→recovery pairs and standalone explicitly assessed task failures now share evidence-attached review dispositions. Current pairs suppress duplicate standalone demand without rewriting source history. A [restored real-trial check](verification/standalone-demand-2026-09-08.md) verified generation and dismissal with zero memory exposures. Cross-task novel-failure clustering, richer conflict/promotion groups and measured review burden remain open. |
| D2 — adaptation acceptance | partial | A reviewed, evidence-backed lesson was promoted, selected and cited in a real H0 run that avoided the baseline's environment regression within budget. Preserve the bounded [recurrence result](verification/reviewed-recurrence-2026-09-08.md), negative results and unknown overall acceptance. Broader task/host acceptance and transfer remain open. Exposure alone must not amplify ranking. |

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
| L1 — class-proportional lifecycle | partial | Ordinary revisions and [unreferenced A deletion](ordinary-delete.md) work; deletion removes cached write bodies without allowing retry resurrection and refuses retained history. Cheap unaudited B→A preserves consumed history and refuses C/open-conflict citations. Explicit cross-record supersession and scope broadening remain open; typed relations exist. Test surviving history, notices and refusal boundaries. |
| L2 — conflicts and refusal records | partial | Add conflict inspection, qualified positions, interested-party resolution, acted-under-open-conflict outcomes and durable refusals. Whole-group omission of optional disputed content remains allowed. Test mandatory refusal, qualification and downstream notice; closing a group must not erase interested-party context. |
| L3 — governed policy | open | Versioned effective policy, revise/rollback, policy-dependent run query, C waivers, discrete instruction category caps and unenforceability decision table. Current hard-coded `local-loop/1` and runtime-required refusal are a restricted implementation. Test rollback and impossible-enforcement outcomes. |
| L4 — evidence lifecycle | partial | Evidence read/status, managed large artifacts, as-cited span/version relations, persisted check generations/history, freshness/refresh and dependent invalidation. Inline explicit capture and hash checks exist. Test changed/missing bytes, earlier-cutoff availability and invalidation propagation. |
| L5 — class D and deletion | partial | Record-body forgetting, cited-A D escalation, tombstones and database purge effects now work. Remaining: metadata/evidence redaction, access-policy transitions, historical-copy adoption and additional external effects. New registered context-file purge is implemented. Include context files, canonical receipts, selected content copies and backup residuals, including pre-R1 task/query text. Enumerations are not functionality. Test worker crashes, repeated requests, citation/delete races and truthful incomplete purge status. |
| L6 — retention | open | Enforce record_use's 90-day minimum with extension while decisions remain live/reviewable, audited B/use pruning and backup residual tracking. Infinite retention currently preserves the floor but does not implement forgetting. Test cutoff/dependency boundaries and recovery. |
| L7 — import and capture policy | partial | Explicit native/repository import quarantine, inherited source restrictions, per-class capture policy and secret handling. Existing advisory labels and hosted local-content exclusion remain. Test laundering attempts through imports and selected artifacts without adopting a hostile-agent threat model. |
| L8 — unsigned C/D checkpoints | partial | Reproducible checkpoint of the audit subset, expected restore-set catalog and verification. Dump checksum alone is insufficient. Signing/off-box keys stay withdrawn. Test missing/altered audit members and restore set. |
| L9 — restore reconciliation | partial | Rebuild projections and freshly recompile under identical pins; verify state/audit, reapply newer revocations/deletions, invalidate handles and recover external effects. Restore delivery generations now fence old launch/binding/pull paths while preserving history. External recovery exports now detect later withdrawals and missing context custody in actual older restores; reapplication, freshness and admission remain open. Test restore after revocation/deletion and interrupted effects. |
| L10 — fault and conformance acceptance | partial | Add abrupt process death around privileged commit, D-worker/citation races, task-close open loops, policy rollback, cutoff replay and adapter transformations. Keep CAS, identity, attribution, grant, evidence, budget and process tests. Record which gate each test establishes; green checks do not imply all-stage acceptance. |

[Versioned relations and demotion](relations-and-demotion.md) now implement
bounded derived/specializing/contradicting links with inherited scope, currentness
and sensitivity restrictions. B→A is unaudited, preserves consumed history and
refuses active C/open-conflict dependencies. Retraction previews include known
transitive uses and reject changed dependency/exposure state. Ordinary deletion
now preserves referenced and formerly privileged records by requiring the audited
forgetting path. Explicit supersession and scope-broadening operations remain open.

[Durable refusals](refusals.md) now preserve bounded metadata for compile policy
failures and blocked demotion/retraction, without raw queries or record bodies.
Transport repeats group by exact intent and status; failed observation writes
are explicit. Other policy paths and refusal analytics remain open.

[Evidence refresh](evidence-refresh.md) now records inline check generations and
history, atomically invalidates affected impact previews, and blocks proposal
conversion when selected evidence is unavailable. Historical gates remain frozen.
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
inputs and reject missing newer expectations. Automated scheduling, remaining D
operations, projection rebuilding and newer revocation/deletion reconciliation
remain open.

[Recovery inspection](recovery-inspection.md) now compares an isolated restore with
a separately retained later withdrawal/audit/custody record. A real older dump
correctly reports revived grants and content plus missing audit and file custody.
This is read-only consistency evidence. [Restore fencing](restore-fencing.md) now
invalidates old launch claims, binding retries and pulls without rewriting history.
Its authenticated host claim endpoint requires a fresh receipt after the fence.
Export freshness, auditable reapplication, projection rebuilding and complete
restore admission remain required.

### 6. Later extensions

| ID | Status | Entry condition and work |
| --- | --- | --- |
| X1 — H1/agy | open | After useful H0: query/expansion/candidate tools, expiring handles, agy driver integration and richer conformance profiles. |
| X2 — native interlocks | open | Claude/Codex observed resume/compaction handling, reinjection, per-run attestation and privilege forfeiture on failed refresh. Require adapter-specific evidence. |
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
