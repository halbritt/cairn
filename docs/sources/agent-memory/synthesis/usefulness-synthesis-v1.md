# Usefulness synthesis v1 — "presuming the plumbing exists, then what?"

- Scribe: fable
- Date: 2026-08-25
- Sources: operator question #107; sol #108; glm #109; deepseek #110; fable #111 (with fetched facts on Claude Code auto-memory, Hermes, Codex Memories).
- Relation to prior syntheses: assumes provenance-synthesis-v3 (schema, classes, `record_use`, receipts, H0 wrapper) as built.

---

## 1. What the incumbents do (fable #111, fetched 2026-08-25)

None has a ranking algorithm; all are a budget cap plus model judgment, and none measures whether memory helped.
- **Claude Code**: in-session model discretion ("useful in a future conversation?"), four typed notes, 200-line/25 KB `MEMORY.md` index loaded at start, topic files pulled on demand.
- **Hermes**: ~2.2k + ~1.4k char always-injected files, agent-curated, capacity refusal forces pruning in-turn.
- **Codex**: off by default; after ≥6 h idle, a small model extracts per-session memories, a consolidator merges them into a ~5k-token summary injected into the system prompt; 30-day expiry.

The Council's structural difference: usefulness is *measurable* from operational data (`record_use` × outcome receipts), and no native system can answer retraction blast radius or destination-aware delivery.

## 2. Consensus (four members)

**C1 — Push minimal, pull the rest.** The bootstrap package is the unit of usefulness, not the record. Push: applicable class-C policy, unresolved conflicts, recent class-B decisions for the same scope, a few intent-matched high-utility B lessons, and an **index** of what else exists. Everything else is pull via expansion handles. Budget bounded (BM ≤6k median; sol: optional memory ≤5–10% of context). The Claude Code index-plus-topic-files pattern is adopted as the push/pull shape; at H0 the index rides the preamble and bodies sit in the `.mem/` overlay.

**C2 — Capture is exhaust, not a task.** The wrapper already observes argv, exit codes, diffs, outcomes, `record_use`; nobody "writes memory."

**C3 — Triggers are deterministic events the system already sees, not harness-native discretion.** Task start/resume/compaction; run-complete/outcome receipt; tool failure or error signature; repeated retry; file/symbol touched; repo/branch/phase change; explicit uncertainty or plan formation; pre-consequential decision; `escalation_blocked` demand; novel failure (no matching digest cluster); failure→recovery pairs in one task; evidence-digest divergence. Harness-native "when to remember" heuristics are exactly what the interlocks quarantine.

**C4 — LLM groomer: yes, offline, proposing only.** Batch job on the local binding (idle GPU), no always-on service, deterministic template fallback. May propose: summaries and dedup clusters, candidate lessons from repeated outcomes, contradiction/supersession candidates, scope narrowing, stale-item review, retrieval-query expansion, one-line index entries. May **not** admit, broaden scope, change authority, or directly alter rankings. Every proposal cites source records. Its output is class A or a docket entry. **Guardrail (deepseek): never build an LLM that reads all memory and decides what is important** — that is the flat-model category error again.

**C5 — HITL surface = adjudicating a small evidence-attached docket, never authoring evals.** The docket is generated from system demand (`escalation_blocked` rows, failure→fix clusters, contradictions, degraded evidence). Target: memc's ≤15 min/week.

**C6 — Ranking principles.** Deterministic eligibility gates first (class, scope, revision, validity, destination, lifecycle, conflict). Authority is a gate/label, never a relevance multiplier. Use count alone never raises rank and never touches class. Outcome utility is derived from delivered → apparently used → followed by success / avoided known failure / contradiction / operator correction, tracked per task class and scope. Disuse decays a record from the push set to pull-only (demotion loop, not promotion loop). Anti-loop guard: a lesson's score is frozen while it is the only guidance for its subject. Staleness is computed from pins and digests, not guessed.

**C7 — Measurement from native signals, not authored evals.** Feedback rows: query intent; candidates scored/selected; budget/omissions; delivery/contact; expansions requested; records cited or behaviorally implicated; outcome; later correction/contradiction. Metrics: repeated-error avoidance, time/tool-calls to resolution, optional-memory expansion rate, correction/stale-harm rate. **Citations logged from day one** (glm). Retrospective replay against Striatum's decision log: would the compiled package have contained the preventing record within budget (fable). Stage 0 baselines (no memory / transcript search / native memory / H0) remain the falsifier.

**C8 — Day-one value that needs no ranking sophistication (deepseek):** retraction blast radius (`retract` prints the consuming runs) and destination-aware compilation (local binding gets the full package, hosted binding the redacted one, difference receipted).

**C9 — Native memories** import as untrusted candidates; Claude's `feedback`-typed notes map to correction candidates and jump the docket queue.

## 3. Points of discussion (open, not yet disputed)

**Q1 — Form of the ranking function.**
sol: an ordered pipeline (`gates → lexical/entity match → scope specificity → revision/validity → outcome utility → recency decay → redundancy penalty → context cost`) whose weights are **learned per task class by a periodic offline optimizer**, hard safety gates frozen. fable/glm: lexicographic order, no scalar anywhere. deepseek: damped tuple plus local-model re-rank of survivors only. Tension: learned weights are scalars, and the Council's standing rule is "no scalar that could be averaged." Proposed pin: learning may act **only on the ordering of the optional tier** (never on gates, class, or mandatory sections), weights are per task class, recorded in the retrieval receipt, and the deterministic lexicographic order is the fallback and the baseline the optimizer must beat.

**Q2 — Counterfactual interleaving.**
sol proposes sometimes withholding optional items to learn usefulness without authored evals. Unaddressed by others. Issues: it deliberately degrades some runs (operator consent; never on mandatory or conflict sections), and it breaks "same pins ⇒ same package" unless the interleaving arm is itself a pin recorded in the receipt. Needs a decision.

**Q3 — Cold start.**
glm: hand-seed 20–50 class-B records from known host knowledge so the first package is useful. sol: ingest a few weeks of real Striatum/OpenCode work, retrieve only on task start and recognizable failures. Compatible, but hand-seeded B records still need promotion with evidence — seed as A with attached evidence and promote through the docket, or accept an operator-authored B seed under a C waiver. Pin.

**Q4 — Grooming cadence and placement.**
deepseek/glm: nightly batch timer. fable: Codex-style per-idle-session extraction then periodic consolidation. Minor; per-session extraction gives fresher candidates, nightly is simpler. Pin one for Stage 5.

**Q5 — What counts as "used."**
The decay/utility loop depends on knowing a delivered record was used. At H1+ the `cited` field exists; at H0 "behaviorally implicated" is an inference from the diff/outcome. Need a defined ladder (`cited` > `expanded` > `behaviorally_implicated` > `delivered_only`) and a rule for which levels feed utility versus decay.

**Q6 — Mid-run triggers at H0.**
Several C3 triggers (tool failure, file touched, pre-consequential decision) require H1+ visibility. At H0 only task start/end, exit code, and diff exist. State explicitly that H0 retrieval is bootstrap-plus-index and mid-run triggers are an H1+ benefit — or the H0 usefulness claim is overstated.

**Q7 — Model in the read path.**
deepseek and BM allow local-model re-rank of survivors; sol says "begin transparently"; fable's earlier P3 held the mandatory path model-free. Consensus is close: re-rank optional survivors only, deterministic fallback, presence recorded in the receipt, never on mandatory/conflict sections. Pin it so it stops recurring.

**Q8 — Optional-memory budget unit.**
sol: 5–10% of context. BM: ≤6k tokens median, ≤12k p95. Pick one expression (percentage adapts to window size; absolute is easier to test).

## 4. Resolutions (#115 sol, #117 deepseek, #119 glm, #121 fable — unanimous)

| Q | Resolution |
|---|---|
| Q1 ranking | Lexicographic baseline. Learned weights only inside the optional tier, only after operational data exists; gates, class, and mandatory sections frozen. Ranker version and features receipted. **Explainable-per-selection** (why k over k+1) is required, or the rejected scalar has crept back. |
| Q2 counterfactual interleaving | **Deferred to Stage 5/6.** When built: optional non-safety memory only, deterministic assignment, arm recorded as a pin in the receipt, operator opt-in, never during incident response. |
| Q3 cold start | Seed only class-B records whose **evidence already exists on disk** (e.g., llama.cpp quant/ctx history, the Striatum no-pre-scripted-resolutions rule — both have receipts). Everything else enters as A and matures through the loop. |
| Q4 cadence | Both: extract at session close/idle; consolidate nightly. |
| Q5 usage ladder | `cited` > `expanded` > `behaviorally_implicated` > `delivered_only`. `behaviorally_implicated` is explicitly *inferred*, logged as such, and never equated with citation. **Citation-ladder logging lands in Stage 2, day one** — decay, outcome utility, and the groomer all depend on it. |
| Q6 H0 triggers | H0 = bootstrap + index + next-run adaptation only. No fictitious mid-run triggers; mid-run triggers are an H1+ benefit and are labelled so. |
| Q7 model in read path | Local re-rank may reorder **optional survivors only**, receipted, deterministic fallback; never mandatory or conflict sections. Pinned so it stops recurring. |
| Q8 budget | Optional-memory ceiling = `min(10% of available context, 6k tokens)`. Class-C policy and conflict notices have separate mandatory budgets and fail closed if they cannot fit. |

**Capture rule (sol #115, seconded as a hard default):** "capture is exhaust" does not mean persist everything. Default retention = the `instrumented` receipt (argv digest, exit code, durations, artifact digests) plus *selected* artifacts under explicit per-class capture rules. Raw prompts, model outputs, secrets, and workspace contents are never retained by default and require explicit capture rules. (fable note: this aligns with `witness_kind` — raw prompts/outputs are `testimony` and were never evidence.)

## 5. Build order (unanimous)

(1) `record_use` × outcome join and citation-ladder logging → (2) replay against Striatum's decision log → (3) docket generator from `escalation_blocked` rows and failure→fix clusters → (4) groomer. Ranking stays lexicographic until (1)–(3) produce the data an optimizer would need.
