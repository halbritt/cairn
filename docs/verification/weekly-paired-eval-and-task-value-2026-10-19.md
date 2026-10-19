# Weekly Paired Task Evaluation (`CAIRN-107`) and Durable Task Value Ledger (`CAIRN-10`), 2026-10-19

## Scope and architectural boundary

This verification report records the completed implementation and verification boundary for:

- **CAIRN-107** ("Run paired task evaluation weekly with a usage pre-check and a second grader")
- **CAIRN-10** ("Demonstrate durable task value across turns, sessions and tasks")

Per `AGENTS.md`, implemented capabilities and tested claims are reported separately from full design acceptance and measured usefulness.

## 1. `CAIRN-107` weekly evaluation architecture on `main`

The weekly paired evaluation pipeline on `main` enforces pre-launch quota validation, blinded dual-grader custody, and deterministic dispute routing across the 21-case x 2-arm (`CURRENT` vs `CAND`) x 3-seed (126-attempt / 63-pair) frozen weekly instrument:

1. **Frozen Weekly Input Bundle (`scripts/trial_weekly_input.py`)**:
   - Validates `cairn.weekly-input-reference/1`, `cairn.weekly-execution/1`, and `cairn.weekly-schedule/1` against the 21 frozen cases, 1,900 deterministic distractors (`GENERATOR_SEED = 20260928`), and 3 distinct seeds (126 attempts).
   - Ensures `CURRENT` and `CAND` memory arms share identical embedding worker, config, and model identity pins.
2. **Weekly Request & Companion Spend Projection (`scripts/trial_weekly_request.py`, `scripts/trial_paired_companion.py`, `scripts/trial_provider_mapping.py`, `scripts/trial_provider_spend.py`)**:
   - Verifies the six review dispositions (`task`, `oracle`, `comparator`, `supervisor`, `native`, `integration`), permission profiles, and provider capacity/spend projections before preparation holds are registered.
3. **Blind Packet Drafts & Grader Delivery Plans (`scripts/trial_blind_packet_draft.py`, `scripts/trial_grader_delivery_plan.py`, `scripts/trial_grading_custody.py`)**:
   - Strips arm identity (`CURRENT` / `CAND`) from sample and comparison packets (`cairn.blind-sample-draft/1`, `cairn.blind-comparison-draft/1`), enforces independent `primary` and `secondary` grader agent/session exclusions, and validates coordinator dispute dispositions (`cairn.grading-join-report/1`).
4. **Transactional Debit & Quarantine State Machine (`internal/experimentaldebit`)**:
   - Enforces fencing, recovery generation increments, and quarantine accounting on experimental debit state transitions in disposable PostgreSQL stores.
5. **Unified Usage Pre-check, Dual-Grader Agreement & Task-Value Checker (`scripts/check_weekly_paired_eval.py`)**:
   - **Usage Pre-check Gate (`check_usage_precheck`)**: Verifies remaining provider token and request allowances across `primary_actor`, `secondary_actor`, `primary_grader`, `secondary_grader`, and the shared `weekly_pool` before admitting a 126-attempt run. Explicitly refuses launch with `PRECHECK_INSUFFICIENT_HEADROOM` when a weekly limit or HTTP 429 cutoff (such as the 2026-09-29 15/126 attempt cutoff) would truncate the cohort.
   - **Dual-Blind Second Grader & Dispute Adjudication (`evaluate_dual_grader_agreement`)**: Joins primary and independent secondary blind packet judgments, refuses any packet that leaks `CURRENT`/`CAND` arm labels or reuses grader identities, computes per-criterion agreement rates and nominal Cohen's $\kappa$ (`sample_verdict_kappa = 0.666667`, `preference_kappa = 0.571429` on the reference cohort), and routes disagreements to `coordinator-adjudicator` without unblinding.


## 2. `CAIRN-10` cumulative durable task-value ledger across turns, sessions, and harnesses

`summarize_durable_task_value` in `scripts/check_weekly_paired_eval.py` aggregates source-linked task-value evidence across turns, sessions, and harnesses (`Codex`, `Claude Code`, `OpenCode`, `Agy`, `Hermes`), keeping positive transfers and negative/null paired trials distinct:

### Verified positive transfers and cross-agent coordination outcomes (4 observations)

| Observation ID | Horizon | Harnesses | Source | Net context bytes | Latency (ms) | Bounded scope |
|---|---|---|---|---:|---:|---|
| `obs-cross-harness-storage-transfer` | `cross_harness` | Codex, OpenCode | `docs/verification/mcp-2026-09-08.md` | 1,420 | 110 | Curated storage note captured in Codex and retrieved in OpenCode to answer source questions accurately |
| `obs-opencode-config-procedure-repair` | `cross_session` | Codex, OpenCode | `docs/verification/mcp-host-use-2026-09-08.md` | 1,680 | 140 | Revised procedure pulled in a fresh session to produce a working OpenCode MCP configuration after initial failure |
| `obs-reviewed-lesson-recurrence-avoidance` | `cross_session` | Codex | `docs/verification/reviewed-recurrence-2026-09-08.md` | 1,850 | 190 | Reviewed lesson cited to avoid an environment regression on a repeated repair (matches direct-context control) |
| `obs-predeploy-cross-agent-review-defects` | `cross_harness` | Codex, Claude Code, OpenCode, Agy, Hermes | `docs/verification/task-eval-adjudication-2026-09-28.md` | 2,400 | 320 | Cross-agent review and audit coordination caught grader regex/fixture defects (`db-coverage`, `updatebot-scope`, `nightly-scope`) prior to acceptance |

### Retained negative, null, or truncated paired trials (5 observations)

| Ticket | Horizon | Harnesses | Source | Net context bytes | Latency (ms) | Recorded outcome |
|---|---|---|---|---:|---:|---|
| `CAIRN-40` | `session` | Claude Code | `docs/verification/task-eval-baseline-2026-09-28.md` | 1,881 | 500 | Baseline memory recovered 13/22 memory-sensitive failures vs none, but lagged direct oracle context (20/22) |
| `CAIRN-49` | `session` | Codex | `docs/verification/native-permissions-2026-09-08.md` | 2,150 | 1,300 | Native Striatum recurrence comparison reached invocation timeout without an admitted repair across conditions |
| `CAIRN-109` | `cross_session` | Claude Code | `docs/verification/task-eval-adjudication-2026-09-28.md` | 1,881 | 1,300 | Regraded v4 candidate vs baseline comparison showed 0 improved pairs and 2 regressions (`local-ci` s1, s2) |
| `CAIRN-113` | `turn` | Claude Code | `docs/verification/task-eval-baseline-2026-09-28.md` | 1,881 | 2,450 | At 1,900 distractors, recency displacement and two-word preview relevance gating reduced hook delivery (11/17 task wording, 7/17 paraphrase) |
| `CAIRN-115` | `cross_session` | Claude Code, Codex | `docs/verification/task-eval-adjudication-2026-09-28.md` | 1,881 | 1,800 | Weekly cohort truncated at 15/126 attempts by provider HTTP 429 quota exhaustion; refused as effectiveness evidence |

### Ledger summary & separation of claims

- **Cost summary across ledger**: `total_context_bytes = 17,024`, `median_context_bytes = 1,881`, `median_latency_ms = 500`, `p95_latency_ms = 2,450`.
- **Tested and verified claims**:
  - Pre-check gate deterministically refuses truncated or under-provisioned 126-attempt weekly runs (`PRECHECK_INSUFFICIENT_HEADROOM`).
  - Dual-blind grader join computes per-criterion agreement and Cohen's $\kappa$ while preserving arm blinding and routing disputes to `coordinator-adjudicator`.
  - Curated cross-harness note transfer, procedure repair, and cross-agent review coordination have produced bounded, verified operational value.
- **Unestablished claims (`automatic_retrieval_net_benefit_established: false`)**:
  - Automatic ambient retrieval has not demonstrated a statistically significant net positive task-completion advantage over baseline/no-memory across coding cohorts once null/negative paired trials (`CAIRN-40`, `CAIRN-49`, `CAIRN-109`, `CAIRN-113`, `CAIRN-115`) and context/latency costs are accounted for.

## 3. Verification commands executed

```sh
chmod go-w scripts/check_weekly_paired_eval.py scripts/test_check_weekly_paired_eval.py docs/verification/weekly-paired-eval-and-task-value-2026-10-19.md
python3 -B scripts/check_weekly_paired_eval.py --compact
python3 -B -m unittest discover -s scripts -p "test_check_weekly_paired_eval.py"
make check
```
