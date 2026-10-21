# Weekly runner reconciliation (CAIRN-107)

Status: source-only, 2026-10-21. This change adds `scripts/trial_weekly_run.py`
and the user units `integrations/systemd/cairn-weekly-eval.{service,timer}`.
The units are not installed or enabled. This work made no live run, no
provider or model call, no account change and no campaign execution, and it
read no real case or oracle. It does not assert runtime admission. The source
acceptance of this runner, the blind converter or any other piece does not
make the campaign ready. See [Joins to an actual campaign](#joins-to-an-actual-campaign).

Line anchors below refer to `scripts/trial_task_eval.py` unless another
file is named.

## Verdict

The original harness route (`trial-task-eval.sh agent --legacy-fixtures`) can
produce the original shape: 21 frozen v12 cases, two memory arms, 3 seeds,
126 attempts and 1900 distractors. It uses one shared embedding worker and a
disposable PostgreSQL cluster. Under the later constraints, three concrete
blockers kept it from producing valid weekly outcomes. As of the
limits-and-account change, blockers 1 and 2 are closed in source and blocker
3 is with agent-504:

1. **`original_limits_unenforced`, closed in source.** The legacy `run_agent`
   did not apply `trial_paired_request.ORIGINAL_LIMITS`. It had a 13 s hook
   timeout, no MCP tool room, hook context that did not charge the task wire,
   no per-line timing, and no search, pull, answer or last-memory bound.
   `trial_task_eval.py agent --legacy-fixtures --original-limits` now does
   the following, for Claude with task wording only. Without the flag, the
   legacy configuration is unchanged.
   - It sets the hook timeout to `ORIGINAL_LIMITS['hook_seconds']` (5 s).
   - It appends `--tokens 32000 --max-tokens 32000` to the MCP server
     arguments (`trial_legacy_limits.mcp_tool_room_arguments`, the same
     values as `trial_paired_request.mcp_arguments`).
   - It sets hook context to `9500 - (task wire + 512 + memory instruction)`,
     the prospective formula.
   - It runs the actor through the existing `trial_native_capture.capture`,
     which writes `stream-timing.json` and `native-process-custody.json`.
   - It checks each memory-arm attempt with `trial_legacy_limits.assess`
     against all seven original values. Each timing row must match its line's
     index, byte count and SHA-256. Malformed, missing or truncated evidence,
     a missing terminal `result`, and invalid timings are `unknown`, never
     within limits.
   - It records `original_limits` and `process_custody` on the row, and lists
     every memory-arm row that is not `within_original_limits` in
     `measurement_failures` (exit code 2).
   - If capture custody is unknown, the row is `harness_error` /
     `process_custody_unknown` and is never graded (`grade` and `evaluate`
     are not called). This covers exit 0 with cleanup `unknown`, an
     unreaped leader, or missing custody metadata. `observed_clear` stays
     the capture's scoped observation; it is not a claim that every
     descendant was reaped.

   The runner passes `--original-limits`. It refuses a harness export without
   that support (`harness_original_limits_unsupported`). It then re-checks
   every attempt itself from the retained stream, hook observations and
   timing, and reports `original_limit_exceeded` or
   `original_limit_unobserved` as `incomplete`, never `complete`.
   Remaining limitation: the legacy prompt is passed as an argument, so the
   charged task wire is the prospective JSON-wire formula applied to the same
   prompt, not an observed stdin wire.
2. **`dedicated_account_unbound`, closed in source when an account is
   selected.** The sandbox used to bind the host `~/.claude/.credentials.json`
   read-write. Now the account is selected explicitly:
   - The profile field `account: {account_id, config_home}` names a Claude
     config home by reference.
   - `trial_account_selection.selected_credentials` checks it by metadata
     only. Credential content is never opened. The path must be absolute,
     resolve to a directory and contain a regular `.credentials.json`. If it
     resolves to the ambient `~/.claude`, even through a symlink, the runner
     refuses `ambient_credentials_refused`.
   - The executor passes it to the harness as
     `CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR`. Any inherited value is replaced.
   - `trial_task_eval.sandbox_command` binds that file in place of the
     ambient one. A bad selection refuses; it never falls back.
   - The executor's `account_ids` is the selected `account_id`. It must equal
     the capacity evidence's account binding (`capacity_account_mismatch`).
     The legacy harness binds one credential for both arms, so the capacity
     evidence must have exactly one account.
   - A harness export without the selector refuses
     `harness_account_selector_unsupported`.
   - With no account selected, the executor still refuses
     `dedicated_account_unbound`.

   Still declared, not observed: which account the file belongs to, and the
   `dedicated: true, interactive_use: false` flags in the capacity evidence.
3. **Writable workspace snapshot, owned by agent-504.** The actor could write
   the `.eval-snapshot.json` that grading read. agent-504's 86298af binds
   original grading to a privately retained pre-actor baseline; see
   `docs/original-attempt-custody.md` for its scope. That document, not this
   lane, makes the claim. Full artifact, action and evaluator channel
   custody remains unproven there.

**Authorization is a bound input, not a blocker.** The `--legacy-fixtures`
help says the flag is "not authorization to replay" (`:1807`). Selecting the
fixtures therefore authorizes nothing. That does not mean authority cannot
exist: the owner has delegated campaign decisions. The runner requires an
explicit, pinned `cairn.weekly-run-selection/1` in profile field
`selection`. It must name this exact campaign, slot start, current,
candidate and harness commits, executor, and capacity-receipt hash, plus
the selecting identity. Without one, the runner refuses
`live_invocation_not_selected`. If any field differs, it refuses
`live_selection_mismatch`. The selection is recorded as
`declared_unauthenticated`. No selection exists today, because root has not
selected a live invocation.

**Executor path.** `LegacyHarnessExecutor.execute` is real. It builds the
original argv with `--original-limits` (`legacy_argv`) and runs the exported
harness's `trial-task-eval.sh` with a cleaned environment and the selected
account (`legacy_environment`). It returns the `agent.json` path and exit
code. The tests run this path from start to finish against a fake
`trial-task-eval.sh`. With a selected account, `check` reports no blockers.
Without one, it refuses `weekly_executor_blocked`,
`blockers: ["dedicated_account_unbound"]`, before any effect. A live run
still needs real capacity evidence for that one account and a root-issued
selection.

## Ledger

| Guard or constraint | Source | Legacy route satisfies? | Action |
| --- | --- | --- | --- |
| `--legacy-fixtures` is not replay authorization | `:1807`; weekly launch refuses `provider_execution_unavailable` (`trial_weekly_request.py:193-195`) | The flag carries no authorization | Explicit bound selection (`selection_gate`) |
| Do not call legacy `cmd_agent`/`run_agent` directly as the weekly adapter | `docs/sdk-weekly-compatibility.md:37-48` | Partly. See the five defects below | Not called in-process: the runner exports a pinned harness commit and runs its CLI |
| Same frozen cases | CAIRN-107 Proposal 1 (v12, 21 cases, cases hash `383f70fb`) | Yes, but only as "latest" of the checkout it runs from | Pins `harness_commit` and `weekly_source.{label_version, frozen_sha256}`. Checks `latest_version == label_version`, the FROZEN hash, `verify_frozen`, and that the 21 case IDs equal the capacity plan's cases |
| Case and oracle privacy | `docs/plans/task-evaluation.md:76`; `trial_run_report.py` allowlist | The run directory is private (`:1502`) | All output stays under a 0700 `runs_root`. The receipt holds commits, hashes, counts and codes only. Tests use synthetic IDs |
| Grader independence | CAIRN-107 Proposal 3; `trial_paired_request.grading_check` (`:292-303`) | The harness grades only with the deterministic oracle | The runner never grades. It emits the pair binding for the converter; the second grader is downstream |
| Original counts 21/126/1900 | `check_weekly_paired_eval.py:24-28` | Yes | Fixed constants; exact planned identity set enforced on output |
| Same embedding worker, disposable PostgreSQL | CAIRN-107 Proposal 1; `trial-task-eval.sh:14-47` | Yes | One pinned worker; `CAIRN_PG_BIN` from the profile; `CAIRN_*` stripped from the environment |
| Current caps (`ORIGINAL_LIMITS`) | `trial_paired_request.py:17-19,329` | Yes, with `--original-limits` (blocker 1 closed in source) | Harness applies and checks them; the runner re-checks every attempt and refuses a harness export without the flag |
| Provider-cap stop | f376915; `:913-925,1656` | Yes | `provider_cap_stop`; a started slot never runs again. Any row with a provider failure is never complete |
| Comparable whole-run usage pre-check | CAIRN-107 Proposal 2: estimate from the last run and refuse unless the remaining budget covers the whole run | Not part of the harness | `capacity_gate` over existing `trial_capacity_adapter` evidence (see below) |
| Absolute allowance units | `trial_capacity_adapter.py:76` (current implementation) | n/a | A constraint of the current implementation, not an original acceptance requirement. The adapter refuses percentage units; a calibrated comparable estimate would need its own adapter change |
| Dedicated account | CAIRN-107 Notes | By explicit selection (blocker 2 closed in source); dedication is declared | `account.config_home` bound by reference; `account_id` must equal the capacity account binding |
| Valid outcomes: snapshot integrity | `:667,1303` | **No**, blocker 3 (agent-504) | Named; not handled in this lane |
| Subscription-only routing, no OpenRouter | `AGENTS.md` Model routing | Yes: `claude -p` under `bwrap --clearenv` (`:1012,1089`) | The executor also strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `OPENAI_*` and `OPENROUTER_*` |
| Six review dispositions | `docs/candidate-paired-request.md:82,148-149` | Not in the original issue | Optional; not on the runner path |
| Physical RSS, SDK selected-input custody | `docs/sdk-native-launch.md:75-80` | Not applicable to the CLI route | Not runner gates. Their figures describe the SDK path and are not live legacy blockers |

## sdk-weekly-compatibility defects against current legacy source

Ownership, per agent-341: this lane owns R1 (`TrialStore.__init__` cleanup),
R2 (the second wait in `TrialStore.stop`) and R3 (the `cmd_agent` store
setup and import-provenance block). Agent-504 owns the default TRIAL
workspace, the setup/commit/snapshot join, case workspace creation, and
artifact, action and evaluator channel recording.

1. **Default TRIAL workspace and latest version (agent-504).** Live.
   `load_cases`, `load_corpus` and `verify_frozen` default to the latest
   version (`:87`, `:188`, called at `:1462-1463` and `:1071`).
   `prepare_workspace` defaults to `source_root=TRIAL` (`:642`, `:1067`).
   This does not block the runner. It runs a pinned exported harness commit
   and checks that latest equals the pinned `label_version` and frozen hash,
   so "latest" equals v12 by construction.
2. **Owned setup, commit and snapshot join (agent-504).** Live. None of the
   21 v12 cases declares `workspace_commit` (counted only), so the setup
   result is never checked (`:658-661`). The snapshot hash is not retained,
   and the snapshot is writable by the actor (blocker 3). The runner does not
   implement any of this.
3. **Fixture-to-record ID maps (R3).** Live before this change: the maps
   existed only in memory (`:291-292`). **Fixed:** legacy runs now retain
   `import-provenance.json` (`:1541-1542`) and keep their original 8-worker
   import.
4. **Import-then-supersede, entities, readiness.** Faithful: `seed_corpus`
   (`:392`), entity verification (`:370`), `grow` (`:398`) and
   `wait_for_full_coverage` (`:420`). Recorded, not blocking: the parallel
   import order is nondeterministic, and corpus fingerprints are taken only
   in prospective mode (`:1570`).
5. **`TrialStore.stop` and the constructor gap (R1, R2).** Live before this
   change. **Fixed:**
   - `stop` waits again after the kill. If the exit is still unobserved, it
     raises and keeps the handle and socket (`:452-466`).
   - A failed construction removes its socket directory, token and identities
     (`:308-331`).
   - Legacy runs now register cleanup of the token and identities
     (`:1537-1539`).

## Runner sequence and decisions

These are delegated decisions made in this lane.

- **Read-only checks, in order.** Profile, slot window, commits, then stored
  slot state, which only reads `claim.json` and `receipt.json`. Then the
  executor's blockers, executor limits, capacity and selection. None of these
  checks writes anything, including on a retry. A refused retry leaves a
  failed slot unchanged and retryable.
- **Serialized ownership.** From the stored-slot read through the recorded
  claim, the runner holds an exclusive non-blocking `flock` on the
  `runs_root` directory itself (`_runs_lock`); it creates no file. A second
  invocation in that window refuses `weekly_run_in_progress`. The retry
  decision, retirement and claim are therefore one critical section: a slot
  that another invocation completed or is executing is never archived or run
  again. Execution itself runs after the lock is released, guarded by the
  claimed slot directory.
- **Effects.** Only after every check passes does the runner retire a
  `failed_before_execution` slot to `failed/SLOT.N` (`_retire`) and claim the
  slot directory with an exclusive `mkdir`. Effects then run in this order:
  1. build both arms;
  2. export the harness;
  3. verify the frozen labels;
  4. write `spec.json`;
  5. write `execution-started.json`;
  6. execute;
  7. write `receipt.json`.
- **Idempotence.**
  - A recorded slot returns its receipt with `replayed: true`.
  - A different pair of commits refuses `weekly_slot_other_pair`.
  - A claim without a receipt refuses `weekly_run_in_progress`.
  - A slot with `execution-started.json` is never executed again.
- **Pinned arms and harness.** Arms are built with
  `trial_candidate_request.build_arm`. The harness is a `git archive` of
  `harness_commit`, so a candidate cannot change the harness that measures it.
  That commit must include 04ba225 so that each row records `stream_sha256`
  and `prompt_sha256`.
- **Exact output check (`_agent_result`).** The spec retains all 126 planned
  identities (`run_id`, `case`, `arm`, `seed`) and their `schedule_sha256`.
  Header and row checks:
  - The header must match the selected arms `[CURRENT, CAND]`, model,
    `task` wording, label version and hash, and 1900 distractors.
  - Every raw row must carry the selected `model`, `harness: claude` and
    `wording: task`, read directly because `report._agent` drops those fields.
    A mismatch is `rejected` / `agent_row_not_selected`.

  Coverage and status:
  - Records must be unique and must be planned. Together with the unstarted
    rows, they must be exactly the planned set, and `plan.json` must list the
    same set. Duplicates, missing rows, replacements, extra rows and wrong
    seeds give `rejected` / `agent_rows_not_planned`, whatever the counters
    say.
  - A run is `complete` only if all 126 rows are present. In addition, no row
    may show a provider failure or a `harness_error`, `provider_error`,
    `not_regradable` or unknown outcome. There must be no measurement
    failures, every row must have both content hashes, and the harness must
    exit with 0 or 2.
  - Other codes: `provider_cap_stop`, `attempts_unfinished`,
    `attempt_failures`, `original_attempt_provenance_unavailable` and
    `harness_exit_unexpected`.
- **Pair binding.** For a complete run only, the runner writes
  `pair-binding.json` (`cairn.legacy-pair-binding/1`). It contains:
  - `agent_sha256` and `plan_sha256`;
  - `request_id` = slot ID and `request_sha256` = SHA-256 of `spec.json`;
  - `campaign_id`, a UUID5 `occurrence_id` and `schedule_sha256` over the
    planned identities;
  - `arm_roles {CURRENT: CURRENT, CAND: CAND}`.

  The receipt pins it as `{path, bytes, sha256}` with
  `pair_identity: declared_binding`. The runner issues the binding, but it is
  a declaration, not reviewed issuance.
- **Timer.** `Mon 06:00 UTC`, which must equal the profile schedule;
  `Persistent=false`. Not installed.

## Retained baseline verification

agent-504's 86298af makes `run_agent` capture a pre-actor baseline outside
the writable work tree and record its pin as `baseline_custody`. Before this
change, the runner never checked it: a run could be `complete` and issue a
pair binding with no retained baseline at all.

Now, before a run can be `complete` or issue a binding, every attempt row
must carry `baseline_custody`. The runner loads that baseline with the
unchanged `trial_attempt_custody.AttemptBaseline.load`, which checks:
- the exact bytes and SHA-256 against the pin;
- the private file policy (a 0700 directory, a 0600 file with a single
  link, and the runner's uid);
- the schema;
- the identity `{run_id, case, arm, seed, cwd}` of this exact attempt.

The `cwd` comes from the reviewed harness's case metadata, carried in
`spec.tasks[case].cwd`.

Outcomes:
- A missing pin gives `incomplete` / `attempt_baseline_missing`.
- A missing file, tampered bytes, another attempt's baseline (swapped), a
  wrong identity or a wrong file policy gives `rejected` /
  `attempt_baseline_invalid`.
- No baseline is ever reconstructed.
- A complete receipt records `baseline_custody: {verified: 126, proof:
  harness_retained_baseline}`.

This is harness custody under the existing mount boundary, as
`docs/original-attempt-custody.md` states. It is not independent
attestation, source-version evidence or final-artifact custody. The
prospective path is unchanged.

## Verifying the selected harness before launch (analysis)

Question: before launch, can the runner verify that the exact exported
harness implements the required instruments? Those are the original-limits
configuration, capture and process custody, baseline custody and the content
hashes.

Today it cannot. The runner checks three things:
- an exact `harness_commit` exported with `git archive`;
- the frozen label pins;
- two marker strings (`selected = credentials_source()` and
  `"--original-limits"`) plus the presence of two module files.

The markers show only that the text exists. They do not show that the code
paths behave correctly, so they are not proof. Proposal, in order of
strength:

1. **Reviewed instrument manifest (recommended first step).** An
   independent reviewer, not the engineer, issues a pinned
   `cairn.weekly-harness-review/1`. It names `harness_commit` and the SHA-256
   of each instrument file: `trial_task_eval.py`, `trial_legacy_limits.py`,
   `trial_account_selection.py`, `trial_native_capture.py`,
   `trial_process_custody.py`, `trial_attempt_custody.py`,
   `trial_hook_observer.py`, `trial_task_runtime.py` and
   `trial-task-eval.sh`, with the reviewed test results. The runner
   re-hashes those files in the export and refuses on any difference. The
   live selection also pins the review's hash. This binds the launch to
   reviewed bytes, and it also makes the marker checks redundant.
2. **Pre-launch inert conformance run of the export itself.** Before
   claiming the slot, the runner would run the exported tree's own synthetic
   boundary suites (`test_trial_original_limits_boundary`,
   `test_trial_attempt_custody` and `test_trial_account_selection`) in a
   subprocess. They use fakes only, with no provider, case or oracle. Their
   pass/fail and counts would be recorded in the claim. This gives behavioral
   evidence for the exact bytes, but the bytes test themselves, so it
   complements (1) and does not replace it.
3. Neither shows how a native binary or account behaves at runtime; that
   still needs the live run's own evidence.

Neither (1) nor (2) is implemented here. (1) needs an issuer role and a
schema decision from root, and (2) changes the runner's pre-claim cost and
needs a test-selection contract.

## Joins to an actual campaign

Each join from this runner to an actual 21-case / 126-attempt /
1900-distractor workload, with its state:

| Join | Anchor | State |
| --- | --- | --- |
| Capacity adapter to runner pre-check | `trial_capacity_adapter.adapt`/`verify_sources` to `trial_weekly_run.capacity_gate` | **Implemented** (synthetic). Real input missing: no absolute-allowance observation and no account binding for the executor's account exist (blocker 2). The absolute unit is the current adapter constraint |
| Live selection to runner | `cairn.weekly-run-selection/1` to `selection_gate` | **Implemented**. No selection issued (root decision) |
| Runner to harness | `LegacyHarnessExecutor.execute` to `trial-task-eval.sh agent --legacy-fixtures --original-limits` | **Implemented**, fake harness only. Needs a selected account |
| Harness content hashes | `trial_task_eval.run_agent` `stream_sha256`/`prompt_sha256` (`:1323-1324`, 04ba225) to `_agent_result` | **Implemented**: required on every row. Present only in runs from a harness commit at or after 04ba225; older runs, including 2026-09-29, can never be complete |
| Runner receipt to pair binding | `_pair_binding` writes `cairn.legacy-pair-binding/1` | **Implemented**. Declared, not reviewed issuance; the issuer's identity is not authenticated |
| Pair binding to converter | receipt `pair_binding` + `agent`/`plan` pins to `trial_legacy_blind_selection.convert` | **Implemented and tested end to end** (synthetic: 63 pairs, 15 changed). The conversion request's case materials (contracts, task statements, grading instructions) still need a real, private issuer |
| Artifact, action and evaluator channels | `trial_legacy_blind_selection` refuses `legacy_channel_unbound` | **Not implemented**; owned by agent-504 (workspace setup and channel recording). Only task statement, grading instructions and final answer are bound |
| Baseline custody per attempt | `baseline_custody` (agent-504, `trial_attempt_custody.AttemptBaseline.load`) to `_limit_result` | **Implemented**: required and verified for every attempt before `complete` or a binding (harness custody, not attestation) |
| Snapshot integrity for outcomes | `prepare_workspace` `:667` / grade `:1303` | **Blocker 3**, agent-504 |
| Grader delivery release authority | `trial_grader_delivery._check_authority` (`cairn.grader-release-authority/1`) | **Not implemented here**. Requires an owner- or coordinator-issued authority and fresh eligible grader sessions; none exists |
| Grades to grading join | `trial_grading_custody.join_results(bundle, submissions, dispositions)` | **Not implemented** for legacy-derived selections. Needs returned grades from delivery and a bundle that matches the converted selection; unverified |
| `trial_run_report` input | receipt `report_input.agent` to `trial_run_report.py render --agent --join` | **Agent half implemented** (document checked with the report's own `_agent` reader, arms `CURRENT`/`CAND`). The `--join` half depends on the grading join above |
| Two-week completion | two consecutive `complete` receipts, reports and notes | **Not started**. Requires every join above plus two live slots |

## Tests

`scripts/test_trial_weekly_run.py` has 35 tests. They use an owned temporary
Git repository with synthetic case IDs, a fake compiler, a fake
`trial-task-eval.sh` that writes synthetic legacy output (agent, plan, runs,
streams), and the capacity adapter's synthetic 21-case/126-attempt evidence.
They cover:

- computed blockers, and refusal before effects;
- selection absence and mismatch;
- unenforced limits;
- tampered adapter sources; account, window and plan mismatch;
- the slot window;
- argv and environment stripping;
- the complete path through the real `execute`;
- replay; another pair; in-progress state; retry after a preparation
  failure; a refused retry that leaves the failed slot untouched (agent-341's
  reproduction); a started slot that is never retried; agent-341's
  interleaved retry (inner run inside the outer retry's checks) and two
  interleaved fresh claims, each executing once; a held lock refusing
  without writes;
- a later label revision;
- the provider-cap stop;
- the CLI;
- exact coverage: agent-341's 126-duplicate reproduction, one extra
  duplicate, a missing row, a missing row with a replacement, an extra row,
  wrong seeds and an unplanned unstarted row;
- the header model, and per-row model, harness and wording, including
  missing values (agent-341's single-row model reproduction);
- row-level provider, harness-error and measurement failures;
- missing content hashes;
- the receipt-to-binding-to-`convert` chain.

Each new coverage, row-binding and retry test failed before its fix. The
retry, row-model and duplicate reproductions were also checked against the
pre-fix source. Targeted mutations of the window, environment, selection,
account, source, limits, retry, cap, label-version, uniqueness, coverage and
row-binding guards are all detected.
`scripts/test_trial_task_eval.py` (`StoreLifecycleTest`, 4 tests) covers
R1-R3.

Limits and account selection:
- `test_trial_legacy_limits` (14 tests) covers each original value, absent
  or malformed evidence, and capture-row timing binding.
- `test_trial_account_selection` (5 tests) checks the bind mounts at
  `sandbox_command` and the ambient, missing and invalid refusals, and that
  only metadata is read.
- `test_trial_original_limits_boundary` (7 tests) runs the inert full
  `run_agent` boundary. It checks the applied 5 s timeout, tool room, charged
  wire and captured timing, and limit failures. It covers exit 0 with
  `process_scan_unknown` cleanup and missing custody, each never graded,
  the unchanged path without the flag, and `measurement_failures` in
  `cmd_agent`.
- The runner tests also cover account refusals and mismatch, a harness
  without the selector or the flag, and limit, late-memory and unobserved
  rows.

## Remaining work

- Evidence for the dedicated-account declaration, and real single-account
  capacity evidence for the selected `account_id`.
- Blocker 3: snapshot integrity, plus the artifact, action and evaluator
  channels (agent-504).
- Real capacity-adapter input for the bound account, and a root-issued live
  selection for one concrete slot.
- Real conversion materials, a grader release authority, delivery, the
  grading join, `trial_run_report` render and publish, and two consecutive
  complete weekly runs.
