# Memory Utility Evaluation & Native Stdio Approval Host Verification (2026-10-19)

## Scope and Issues Covered

- **CAIRN-157**: Finish native approval repair and validate counted trial 3 (`rhumb-changelog.current.s0`).
- **CAIRN-156**: Experiment: measure memory utility on task outcomes.
- **CAIRN-158**: Execute remaining frozen memory utility trials (ledger accounting and continuation validation for slots 3..72).
- **CAIRN-159**: Review task outcomes and publish memory build-or-defer decision.

This report separates implemented, deterministically verified harness/evaluation artifacts from live provider execution, which was paused by the owner cost gate on 2026-10-16 after 2/72 pre-repair slots (`rhumb-changelog.none.s0` and `rhumb-changelog.direct.s0`) failed on native permission prompts (`--permission-prompts none`).

---

## 1. Native Stdio Approval Host (`CAIRN-157`)

### Implementation (`scripts/trial_approval_control.py`)

`scripts/trial_approval_control.py` implements the per-call root approval controller for the official Claude stdio control protocol without broadening sandbox mounts, modifying task prompts, or enabling automatic permission bypass:

1. **Atomic Hardlink Publication**: Requests (`requests/<sha256>.json`) and decisions (`decisions/<sha256>.json`) are written to a mode-`0700` `.staging` directory, `fsync`ed, and hardlinked into place (`os.link(..., follow_symlinks=False)`) before unlinking the staging path. Readers validate regular-file ownership and `0600` permissions while tolerating `st_nlink == 2` while the publisher holds the staging link.
2. **Policy Hash and Per-Call Identity Binding**: Every decision binds `run_id`, `run_execution_id`, `freeze_sha256`, `root_execution_id`, `admission` (3..72), `approval_policy_sha256`, `session_id`, `request_id`, `tool_use_id`, `input_sha256`, `request_sha256`, and `pending_nonce`. `allow` requires clause `A1..A3`; `deny` requires clause `D1..D3`; both require a non-empty host-only `review_note`. Clauses and review notes never enter the native wire response.
3. **Monotonic Wait Interval Accounting**: Tracks per-request `pending_monotonic_ns`, `decision_observed_monotonic_ns`, `response_complete_monotonic_ns`, `ended_monotonic_ns`, and `wait_seconds`. `total_wait_seconds` measures the union of concurrent pending intervals, whereas `total_request_wait_seconds` sums individual request intervals.
4. **Cancellation and Wall-Deadline Enforcement**: `control_cancel_request` immediately ends the pending wait (`native_permission_cancelled`) and fails closed. `stop('timeout')` caps wait intervals at `wall_deadline_monotonic_ns` before process teardown and writes `stopped.json`, rejecting any late root decision (`no_longer_pending`).
5. **Counted Slot-3 Admission Gate**: Enforces `3 <= admission <= 72` in `ApprovalControl`, preserving consumed pre-repair slots 1 and 2 (`ac621601384aa2d9a90b83e2ff6a3e17f913a2fef12e2747567062696c8e2454`) and binding slot 3 (`rhumb-changelog.current.s0`) as the first post-repair admission.

### Unit Verification (`scripts/test_trial_approval_control.py`)

Verified via `python3 -m unittest discover -s scripts -p "test_trial_approval_control.py"`:
- Concurrent reader/publisher hardlink barrier (`st_nlink == 2` during publication, `st_nlink == 1` after unlink).
- Policy clause and identity bindings (`A1..A3` allow, `D1..D3` deny) and fail-closed rejection across 14 tampering/malformed/symlink mutations.
- Exact monotonic overlap accounting (`4s` union vs `6s` sum across overlapping pending requests).
- Timeout (`3s`) and cancellation (`2s`) freezing pending intervals before teardown and rejecting late decisions.
- Counted slot-3 (`rhumb-changelog.current.s0`) admission validation and CLI `show`/`decide` round-trip.

---

## 2. Frozen Memory Utility Design, Outcome Analyzer & Build/Defer Decision (`CAIRN-156`, `CAIRN-158`, `CAIRN-159`)

### Frozen Cohort and Preserved Prefix (`utility/DESIGN-FREEZE.json`)

- **Cohort**: 12 source-backed cases (6 documented incident cases: `rhumb-changelog`, `pgpassfile-guard`, `herdr-update-verify`, `systemd-exit-check`, `pgrep-wait`, `stale-supercov`; 6 control cases: `unsup-rhumb-first-picker`, `unsup-pgpass-port`, `unsup-herdr-restart`, `unsup-systemd-reload`, `unsup-pgrep-pattern`, `superseded-supercov-flag`).
- **Arms & Schedule**: 3 arms (`none`, `direct`, `current`) x 2 replicates = 72 scheduled admissions (`utility/SCHEDULE-DRAFT.json`).
- **Preserved 2/72 Prefix**: Predecessor ledger SHA256 `ac621601384aa2d9a90b83e2ff6a3e17f913a2fef12e2747567062696c8e2454` retains `slot 1: rhumb-changelog.none.s0` and `slot 2: rhumb-changelog.direct.s0` as consumed, unscored `harness_error` entries (`native_permission_denial`). Replay is prohibited; slot 3..72 continuation requires byte-identical prefix preservation and epoch separation (`ALLOW/2+root-per-call-stdio/1`).

### Deterministic Evaluator & Arm-Masked Outcome Analyzer (`scripts/check_memory_utility_outcomes.py`)

Verified via `python3 -m unittest discover -s scripts -p "test_check_memory_utility_outcomes.py"`:
1. **Prefix & Continuation Accounting**: Verifies the 72-slot schedule, enforces byte-identical preservation of the 2/72 consumed prefix, and validates slot 3..72 admissions without replay.
2. **Blinded Arm-Masked Packets**: Assigns deterministic HMAC `arm_masked_id` (`pkt-<hex>`), strips `mcp__cairn__*` memory tool calls, masks raw run/arm/note identifiers, replaces host/root approval denial messages with `[tool call not executed]`, and records unblinding flags exclusively in the root-only map.
3. **Paired Contrasts & Epoch Isolation**: Computes `direct-none` (available memory utility), `current-none` (realized utility), and `direct-current` (retrieval/recovery gap) deltas for task completion, harmful mistakes avoided, and turn/token/cost overhead while refusing to pool across distinct permission/approval epochs.
4. **Published Decision (`DEFER`)**: Under `PROTOCOL-DRAFT.md`, because live provider execution remains paused at the 2026-10-16 owner cost gate (2/72 consumed unscored, 70 unrun), the published recommendation is **`DEFER`** broad ranking/retrieval expansion until live usefulness exceeds the `direct`-note control without control regressions or excess token/turn overhead.
