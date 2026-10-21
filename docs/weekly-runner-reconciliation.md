# Weekly runner reconciliation (CAIRN-107)

Status: source-only, 2026-10-21. The runner `scripts/trial_weekly_run.py` and
the user units `integrations/systemd/cairn-weekly-eval.{service,timer}` exist.
The units are not installed or enabled. No live run, provider or model call,
real case or oracle read, account change or campaign execution was made.
Nothing here asserts runtime admission.

## Verdict

The original harness route (`trial-task-eval.sh agent --legacy-fixtures`) can
produce the original shape: 21 frozen v12 cases, two memory arms, 3 seeds,
126 attempts, 1900 distractors, one shared embedding worker and a disposable
PostgreSQL cluster. Two concrete source blockers keep it from being the
weekly executor under the later constraints:

1. **`original_limits_unenforced`.** The legacy `run_agent` does not apply
   `trial_paired_request.ORIGINAL_LIMITS` (`scripts/trial_paired_request.py:17-19`;
   required unchanged by `docs/weekly-request-draft-v1.md:29` and
   `trial_paired_request.py:329`). It uses a 13 s hook timeout
   (`trial_task_eval.py:1143`) against `hook_seconds=5`. It bounds hook
   context at 9500 bytes (`:50`) but does not count the task wire. Its MCP
   server arguments have no `--tokens/--max-tokens 32000` tool room
   (`:1145-1147`; compare `trial_paired_request.py:74-75`). It also enforces
   no limit of 2 native searches, 4 pulls, an 8192-byte final answer or a
   30 s last memory call. The prospective branch measures these, but only for
   `--prospective-input` with `--distractors 0` (`:1844-1847`), which does
   not fit 21/126/1900.
   *Missing integration:* a legacy-mode `run_agent` that applies the paired
   MCP ceiling (`trial_paired_request.mcp_arguments`), sets the hook timeout
   to 5 s and charges the task wire, and stops or marks an attempt that
   exceeds the native search, pull, answer or last-memory bound. This means
   reusing `trial_native_observation.measure` on the legacy stream. The work
   changes measured behavior relative to the 2026-09-29 run, so it needs its
   own reviewed change.
2. **`dedicated_account_unbound`.** The sandbox binds the host
   `~/.claude/.credentials.json` read-write (`trial_task_eval.py:1041-1043`).
   That file belongs to the shared interactive account that ended the last run
   (CAIRN-107 Notes). The harness has no selector for another account, so the
   capacity evidence's accounts (`dedicated: true, interactive_use: false`,
   `trial_paired_request.py:253-266`) cannot be bound to what the executor
   charges.
   *Missing integration:* a harness option that binds a chosen dedicated
   credentials file, read-only where possible, in place of the host file. The
   executor must then declare that account ID so that it equals the capacity
   evidence's account binding. The owner still has to provision the account.

**Authorization is an input, not a blocker.** `--legacy-fixtures` help says
the flag "is not authorization to replay" (`trial_task_eval.py:1801`). That
means selecting the fixtures does not authorize a run. It does not mean
authority can never exist; the owner has delegated campaign decisions. The
runner therefore requires an explicit, pinned
`cairn.weekly-run-selection/1` (profile field `selection`). The selection
names this exact campaign, slot start, current, candidate and harness
commits, executor name, and capacity-receipt hash, plus the selecting
identity. Without it the runner refuses `live_invocation_not_selected`. If
any field differs, it refuses `live_selection_mismatch`. The selection is
recorded as `declared_unauthenticated`. No selection exists today.

The runner is complete source with a real executor path. `LegacyHarnessExecutor.execute`
builds the original argv, runs the exported harness with a cleaned
environment and returns the agent document. The tests run it end to end
against a fake `trial-task-eval.sh`. `LegacyHarnessExecutor.check` computes
its blockers from the declared legacy limits and account selector, and
refuses `weekly_executor_blocked` with `blockers:
["original_limits_unenforced", "dedicated_account_unbound"]` before any
effect. Once both blockers are closed, the remaining gaps are the explicit
live selection, real adapter capacity evidence for the dedicated account, and
an installed timer.

## Ledger

| Guard or constraint | Source | Legacy route satisfies? | Action |
| --- | --- | --- | --- |
| `--legacy-fixtures` is not replay authorization | `trial_task_eval.py:1801`; weekly launch refuses `provider_execution_unavailable` (`trial_weekly_request.py:193-195`) | No authorization is carried by the flag | Explicit bound selection input (`selection_gate`); refuse `live_invocation_not_selected` until root selects one invocation |
| Do not call legacy `cmd_agent`/`run_agent` directly as the weekly adapter | `docs/sdk-weekly-compatibility.md:37-48` | Partly; see the five defects below | Not called in-process; the runner exports a pinned harness commit and runs its CLI. Defects fixed or named below |
| Same frozen cases | CAIRN-107 Proposal 1; v12, 21 cases, cases hash `383f70fb` | Yes, but only as "latest" of the checkout it runs from | Runner pins `harness_commit` and `weekly_source.{label_version, frozen_sha256}`, and checks `latest_version == label_version`, the FROZEN file hash, `verify_frozen` and that the 21 case IDs equal the capacity plan's cases before execution |
| Case and oracle privacy | `docs/plans/task-evaluation.md:76`; `trial_run_report.py` allowlist | Run directory is private (`out.mkdir(0o700)`, `:1496`); `agent.json` holds answers and traces | Runner keeps all run output under a 0700 `runs_root`. The receipt holds commits, hashes, counts and codes only, never case content. Tests use synthetic IDs only |
| Grader independence | CAIRN-107 Proposal 3; `trial_paired_request.py:292-303` | Harness grading is the deterministic oracle only | The runner never grades. The second grader is the sibling blind-packet lane plus `trial_grader_delivery`. The receipt's `report_input` hands `agent.json` to `trial_run_report` |
| Original counts 21/126/1900 | `check_weekly_paired_eval.py:24-28`; `sdk-weekly-compatibility.md:28-33` | Yes: `--arms` empty, two `--memory`, `--seeds 3`, `--distractors 1900` | Fixed constants in the spec and argv; the agent document must report planned 126 and 1900 distractors |
| Same embedding worker, disposable PostgreSQL | CAIRN-107 Proposal 1; `trial-task-eval.sh:14-47` | Yes: one `--embedding-worker` for both stores; the wrapper owns the cluster | One pinned worker; `CAIRN_PG_BIN` from the profile; `CAIRN_*` environment stripped |
| Current caps (`ORIGINAL_LIMITS`) | `trial_paired_request.py:17-19,329` | **No**: blocker 1 | Runner refuses `executor_limits_unenforced` unless the executor declares exactly `ORIGINAL_LIMITS`; legacy declares its real limits |
| Provider-cap stop | f376915; `trial_task_eval.py:913-925,1650` | Yes: a confirmed 429 weekly limit stops admission | Receipt `incomplete` / `provider_cap_stop`; a started slot is never re-executed |
| Usage pre-check, whole run | CAIRN-107 Proposal 2; `trial_capacity_adapter.py` (5aac8a6) | Not part of the harness | `capacity_gate`: `verify_sources`, plan hash, original 21/126/3-seed plan, existing `capacity_check`, validity to the slot end, accounts equal to the executor's. Percentages never reach it (the adapter refuses them) |
| Dedicated account | CAIRN-107 Notes | **No**: blocker 2 | Capacity accounts must equal executor `account_ids`; legacy has none |
| Subscription-only, no OpenRouter | `AGENTS.md` Model routing | Yes: `claude -p` under `bwrap --clearenv` (`:1012,1089`) with the subscription credential file; no API key in its environment | The executor also strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `OPENAI_*` and `OPENROUTER_*` from the harness process |
| Six review dispositions | `docs/candidate-paired-request.md:82,148-149` | Not required by the original issue | Optional; not on the runner path. `trial_candidate_request` remains available |
| Physical RSS bound, SDK selected-input custody | `docs/sdk-native-launch.md:75-80`; `sdk-weekly-compatibility.md:80-87` | Not applicable to the legacy CLI route | Not runner gates; its historical figures are SDK-path figures and are not treated as live legacy blockers |

## sdk-weekly-compatibility defects against current legacy source

Agent-341 directed that the readiness review's single-reason dismissal of
`sdk-weekly-compatibility.md:37` be replaced by this per-defect check.

1. **Default TRIAL workspace and latest version.** Live.
   `load_cases`/`load_corpus` apply every revision up to latest (`:87-104`).
   `verify_frozen()` defaults to latest (`:188`), and `cmd_agent` calls both
   with defaults (`:1456-1457`, `:1071`). `prepare_workspace` defaults
   `source_root=TRIAL` (`:642`, called at `:1067`). TRIAL is the checkout the
   harness runs from. The runner does not block on this. It runs an exported
   pinned harness commit and checks that latest equals the pinned
   `label_version` and frozen hash, which makes "latest" equal v12 by
   construction. A later v13 on `main` cannot change the cases of a
   consecutive run.
2. **Owned setup, commit and snapshot join.** Live in part. In legacy mode
   `prepare_workspace` is the owned constructor. It copies without root
   `setup.sh`/`__pycache__`, runs setup with the fixed identity and date, tags
   `eval-base` and writes `.eval-snapshot.json` (`:642-667`). `run_agent` uses
   that same directory, so the join holds by construction (`:1067`). Gaps:
   none of the 21 v12 cases declares `workspace_commit` (checked by count
   only), so the setup result is not checked against a commit (`:658-661`).
   The snapshot hash is not retained in the record. The snapshot also sits in
   the agent-writable root, while grading reads it from there (`:1300`,
   `:1742`). These gaps do not block the legacy route. The recommended
   follow-up is to retain the snapshot hash outside the writable bind and
   compare it at grading. That changes the regrade input path, so it was not
   folded into this lane.
3. **Fixture-to-record ID maps.** Live before this change. `TrialStore.ids`
   and `.names` are kept in memory (`:291-292`, filled at `:355-370`), but
   `import-provenance.json` was written only for prospective runs. **Fixed:**
   legacy runs now retain `import-provenance.json` too (`:1535-1536`). They
   keep their original parallel import (8 workers); prospective runs keep 1.
4. **Import-then-supersede, entities, readiness.** Faithful in current source.
   `seed_corpus` imports all notes and then supersedes (`:392-396`).
   `remember` verifies declared entity associations and refuses a drop
   (`:370`). `grow` adds the deterministic distractors (`:398`), and
   `wait_for_full_coverage` gates warm timing on full eligible coverage
   (`:420`). Remaining legacy behaviors, recorded but not blocking: parallel
   import order is nondeterministic, and corpus pre/post fingerprints are
   taken only in prospective mode (`:1564`).
5. **`TrialStore.stop` and the constructor gap.** Live before this change.
   `stop` killed the API on timeout without waiting again. The constructor
   acquired the `/tmp/cte-sock-*` directory, migrated, and wrote the token and
   identities before `cmd_agent` registered `stop` (`:1533`); in legacy mode
   it never removed the token. **Fixed:** `stop` waits again after the kill.
   If exit is still unobserved, it raises and keeps the handle and socket for
   recovery (`:452-466`). A failed construction removes its socket directory,
   token and identities (`:308-331`). Token and identities cleanup is now
   registered for legacy runs as well (`:1531-1532`). The database stays
   inside the wrapper-owned disposable cluster.

## Runner design decisions

These were delegated by the owner and made here.

- **One slot per invocation.** The slot is the latest scheduled start not
  after `now` (reusing `trial_candidate_request.next_occurrence`). Outside
  its window the runner refuses `outside_weekly_window`, so a late timer
  fire records nothing. The slot ID is UUID5 of the campaign and the slot
  start.
- **Order.** Profile, window, commits, stored slot state, executor check,
  executor limits, capacity, then selection. All of these are read-only.
  Only then does the runner claim the slot directory (exclusive `mkdir`).
- **Idempotence.** A recorded slot returns its receipt with `replayed: true`
  without rebuilding or executing. A different commit pair refuses
  `weekly_slot_other_pair`. A claim without a receipt refuses
  `weekly_run_in_progress`. A `failed_before_execution` slot without an
  execution marker is moved to `failed/SLOT.N` and retried. Once
  `execution-started.json` exists, the slot is never executed again.
- **Pinned arms.** Both arms come from `trial_candidate_request.build_arm`
  (`git archive`, `-buildvcs=false`, hook and bridge pins). The harness is a
  `git archive` of the profile's `harness_commit`, so a candidate cannot
  change the harness that measures it.
- **Receipt.** `cairn.weekly-run-receipt/1` contains the claim (commits,
  executor, capacity summary, selection), status, code, harness exit code,
  attempt counts and the pinned `agent.json`. `report_input` names that
  `cairn.task-eval.agent/1` document for `trial_run_report.py render`. The
  document is checked with the report's own `_agent` reader, plus label
  version, labels hash, 1900 distractors and 126 planned. The status is
  `complete` only for a complete 126-of-126 admission;
  `provider_cap_stop`, `attempts_unfinished`, `harness_exit_unexpected`,
  `agent_document_not_original` and `executor_failed` are `incomplete`.
  `runtime_admission` is always false.
- **Timer.** `cairn-weekly-eval.timer` fires at `Mon 06:00 UTC`, which must
  equal the profile schedule. `Persistent=false` because a catch-up start
  would be outside the window anyway. It is not installed.

## Tests

`scripts/test_trial_weekly_run.py` has 19 tests. They use an owned temporary
git repository with synthetic case IDs, a fake compiler, a fake
`trial-task-eval.sh` that writes a synthetic agent document, and the
capacity adapter's synthetic 21-case/126-attempt evidence. They cover:

- the computed legacy blockers, and refusal before effects;
- an absent or mismatched selection;
- unenforced limits;
- tampered adapter sources;
- account, window and plan mismatches;
- outside-window starts;
- the argv and the environment stripping;
- the complete path through the real `execute`;
- replay;
- another pair in the same slot, in-progress, retryable preparation failure and a never-retried started slot;
- a later label revision in the harness;
- the provider-cap stop;
- the CLI.

Targeted mutations of the window, environment, selection, account, source,
limits, retry, cap and label-version guards were all detected.
`scripts/test_trial_task_eval.py` (`StoreLifecycleTest`, 4 tests) covers the
harness fixes. Two existing fixtures were updated for the new legacy
provenance and token cleanup.

## Remaining work

- Close blockers 1 and 2 with the named integrations. Each is a
  behavior-changing harness change that needs its own review.
- The owner must provision a dedicated subscription account and produce real
  absolute-allowance evidence through `trial_capacity_adapter`.
- Root must select an explicit live invocation (`cairn.weekly-run-selection/1`)
  for a concrete slot.
- Install the timer, then run two consecutive complete campaigns. Grade them
  blind with a second grader (sibling lane) and render and publish them with
  `trial_run_report`.
