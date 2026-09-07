# Agent-memory synthesis v3 — Bonded Memory integration

- Scribe: fable
- Date: 2026-08-25
- Base: `provenance-synthesis-v2.md` (all P1–P8 resolved) + first-round compiler/adapter consensus.
- Sources: operator's Bonded Memory rev. 2 (`/home/halbritt/bonded-memory.html`, "memc"); reviews #89 (sol), #90 (deepseek), #91 (glm), #92 (fable review at `synthesis/bonded-memory-review-fable.md`).
- **Note:** agy has not reviewed Bonded Memory (delivery failures #88/#95); this round records four members, not five.

---

## 1. Standing verdict

Bonded Memory rev. 2 is synthesis-v2 conformant on every resolved point and is adopted as the **implementation baseline** (glm's phrasing, unopposed), subject to the amendments in §3 and the disputes in §4. The memc schema, attack suite, and staged plan become the Stage 0–2 working artifacts.

## 2. Adopted from Bonded Memory (four-member consensus)

1. **`record_use` consumption log** on consequential reads; reverse impact from consumption, ancestry optional (`derived_from` class-B, optional). deepseek explicitly trades away "edges required." `retract` refuses to complete until blast radius is printed.
2. **`witness_kind` assigned from the ingress channel, never content**, + the transcriber rule (a model running a command is an instrument; the exit code is the witness; `"model": null` on world receipts). Ingest rejects `world` from `model_self_report`.
3. **Constraints, not code review**: `b_requires_promotion` CHECK; `scope_not_empty` CHECK; `REVOKE UPDATE, DELETE` on `authority_event`; V4 trigger binding state change to audit `txn_id`; V1 `reject_self_authority()` trigger; V1–V8 as the Stage 1 validation set.
4. **Purpose-gated reads as the A/B boundary in SQL** — consequential purposes structurally cannot return class A. (Reconciled with P1's promotion mechanics in §3.2.)
5. **SERIALIZABLE + `FOR SHARE` on the grant chain for C/D**; grants never cached beyond the reading transaction; revocation cascades at read time via `parent_grant`.
6. **Two-axis budgeting**: token budget + normative-weight cap per package. Sol's amendment adopted: normative weight is defined by **discrete policy classes and limits, not a scalar** (no confidence arithmetic by the back door).
7. **Bifurcated receipts + fixed-arity omission census**: full receipt to the audit ledger only; the model-visible package carries counts per reason code, never identifiers or free text; expansion is a fresh authorization against per-run credits.
8. **`MEM-STATUS` sentinel** — the compiler cannot return silent-empty; reason-coded banner required down to H0.
9. **Shadow recall audit** — index result ⊆ structured result on every compile; divergence is a security event.
10. **Seal equality** — BLAKE3 over canonical CBOR; cross-harness equivalence is a hash comparison; seals scoped to packages, never record identity.
11. **In-run capability witnesses** — `assumed` vs `verified`; no witness caps the delivery-assurance level; `mem.attest(seal, nonce)` before mutating calls; `carrier_seal` mismatch hard-rejects submissions; **privilege forfeiture** (run keeps reading, loses write/proposal) after unproven re-injection across a transformation boundary.
12. **`evidence_degraded` lifecycle state** with three legitimate reference states (resolvable / divergent / dangling). Default behavior disputed — §4 D3.
13. **`capability_inference: NONE` barred at schema level** on binding-class failures.
14. **Deletion effects table** with per-target completion status; `not_possible` first-class; "deletion is complete when the last backup carrying it has rotated."
15. **Fenced, hash-pinned region** in project `CLAUDE.md` (single-writer scope = the fence, not the file); content outside the fence is untrusted import; **drift from the pinned hash is an intrusion signal about the harness**; `#` auto-memory replaced via PreToolUse interception; user-scope file mirrored as candidate. Amends first-round §14.1.
16. **Deleting a record cited by an open conflict is refused** until the conflict closes; refusal recorded. (Extends P5.)
17. **Boundary-health gates**: class-B share ≤ 5%; any promotion bypass flag is a schema change requiring its own signed ruling; promotion docket (batched, individually signed) as the F2 pressure valve.
18. **Policy-as-record rollback**: receipts pin policy revision; `runs --policy-rev` enumerates blast radius; revision takes effect on next compile.
19. **Stage 0 corpus from real material** (278 Striatum decisions, 159 RFCs, D178/D179 collision, real CLAUDE.md files, Council private sessions asserted absent); **attack suite green as the Stage 1 gate**; `unbound ≠ global` for class-C scope.
20. **Rejections confirmed**: predicate currentness and independence oracle stay post-Stage-2 experiments; probe certificates are Stage 4 tooling (the in-run witness rule is the load-bearing part); PATH shims recorded `detective`, never enforcement.

## 3. Amendments required to Bonded Memory before Stage 1

### 3.1 R1 asserted/observed split (consensus; glm: "required before Stage 1")
`memory_record.producer_principal` is a single caller-settable column. Consequences: content-digest dedup keys on an asserted field (defeated by lying), and V1 compares the authenticated actor against an asserted producer (spoofable both ways — evade the check, or DoS a legitimate promoter by naming them producer). Fix: `observed_caller` (store-assigned, from authenticated connection) + `asserted_producer`; V1, V2, and the idempotency index bind to `observed_caller`. BM §5.11's own text already assumes this.

### 3.2 P1 promotion mechanics (reconciled, pin in schema)
BM has no promotion inside the read path — strictly a separate audited act by actor ≠ producer. The v2 refinement (auto-promote when deliberately captured evidence existed at creation) survives as a **delegated service actor** holding a constrained `promote_b` grant (descriptive-only, evidence-at-creation, `actor_kind='service'`); V1 holds because the service is not the producer. `escalation_blocked` demand signals go to the audit ledger (not the model-visible census), where the promotion docket reads them. Sol's constraint preserved: no retrofitting provenance; the no-evidence path is re-establishment.

### 3.3 P7 receipts (v2 stands; BM gate 7 amended)
Census always present in the package. Full retrieval receipts durable in the ledger per the #84 trigger (package contains B–D material or enforcement/revocation semantics); A-only compiles log compact rows/telemetry. BM's 90-day compaction applies to the durable set.

### 3.4 Alignments to v2 resolutions
- Cited-A deletion writes the audited D event (P5); BM's "tombstone only if referenced" is the same check, positive branch now audited.
- B-correction expiry ("record lifetime + 1 year") executes as a D event retaining digest and reason (P3).
- Named authority classes: glm to supply, scribe to pin, the one-page mapping between the v2 named enum and memc's `(witness × record_class × actor_kind)` triple, so a third vocabulary does not drift into use.

## 4. Dispute register — ALL RESOLVED (#98 operator; #99 sol; #102 deepseek; #104 glm; fable #92/#97)

**D1 — Demotion B→A — RESOLVED.** R2 is amended: demotion is a cheap, unaudited row update affecting *future eligibility only*. Guards: refused while the record is cited by a class-C record or an open conflict. The consumed B versions, the promotion `authority_event`, and all `record_use` rows always survive. (sol, deepseek, glm assent; fable's guard adopted.)

**D2 — Checkpoints — RESOLVED.** **Unsigned audit-range digest checkpoint rows** over the C/D subset, adopted strictly as **restore/replica integrity metadata, not security commitments**. Must be recomputable from the audit table; must commit to identities/digests, never payload bytes, so they never obstruct redaction; sequence gaps are normal. Signing remains dropped (off-box keys ruled out). Staged at Stage 2. (Unanimous; deepseek and sol withdrew defer/remove on the unsigned form.)

**D3 — `evidence_degraded` on consequential reads — RESOLVED: fail closed.** Predicate (glm): the record is blocked from consequential purposes only when **no resolvable evidence reference remains** — a B record with three refs and one dangling stays usable. Degraded use may be permitted only by explicit class-C policy or waiver. Context (advisory) reads still return the record with the degradation visible. BM Q5 closed.

**D4 — Database — RESOLVED: Postgres.** Host already runs it for Striatum; BM schema is Postgres-native. No SQLite stage.

**D5 — Vocabulary — RESOLVED: plain names.** Schema and receipts use `delivery_assurance` (three levels), `carrier` (`system|tool|file|stdout`), `enforcement_level`; DDP/CIF/EXW and isotype terms are documentation aliases only. (glm conceded.)

**D6 — `witness_kind` — RESOLVED:** values are `instrumented` | `testimony` (`world` renamed per sol).

**D7 — `record_use` retention — RESOLVED (operator, #98; refined #99/#102/#104):** 90 days is the **floor**; retention **extends automatically while any affected decision remains live or reviewable**. Deletion of `record_use` rows that live impact analysis still depends on requires D audit.

**D8 — Reference adapters — RESOLVED as facts (operator, #98):**
- **Hermes** is in use on this host *interactively*, not for building. Council ADR 0005 retired `hermes-cli` as a *member runtime*; the harness itself is present. The RFP's Hermes row stands, scoped to interactive use.
- **OpenCode** is the build harness in Striatum (backed by GLM and DeepSeek models) and **may be the best H0 wrapper** — the H0 reference candidate is now OpenCode, not the generic CLI stub.
- **Codex, Claude Code, and agy are all present**, used interactively to some degree. BM §27.1's "no `agy` repository under `~/git`" does not mean agy is absent; the H3 reference-adapter assumption survives, pending a look at its driver boundary.
- Consequence for #59 decision 5 (accepted #99/#102/#104): H3 target = agy; H0 implementation target = OpenCode wrapper; Claude Code, Codex, and agy remain conformance targets at their **empirically probed** levels; Hermes is not a build target.
- **Stage shift (fable #100, seconded deepseek/glm):** because the H0 wrapper fronts a build harness backed by GLM/DeepSeek bindings, **destination-aware compilation is load-bearing from Stage 2**, not Stage 4.
- **agy has resigned from the Council** due to errors; remaining member count is four.

## 5. Status and remaining operator decisions

**No disputed items remain among the four sitting members.** The record as of #104:

- Bonded Memory rev. 2 is the implementation baseline, with §2 adoptions and §3 amendments; the **asserted/observed schema split (§3.1) is mandatory before Stage 1**.
- D1–D8 resolved as recorded in §4.
- **All four members support authorizing Stages 0/1/2** (~2 engineer-weeks) with the §19.2 attack suite as the falsifier gate.

Outstanding for the operator:
1. Formal authorization of Stages 0/1/2.
2. Parameters: delegation depth (BM Q2, default 2); promotion-docket cadence.
3. Ratify the interlock table (§2.15 rows, extended to OpenCode-as-H0 and Hermes-interactive) and `on_unenforceable` defaults via the §12.7 decision-table record.
