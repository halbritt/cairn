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
limits-and-account change, blockers 1 and 2 are closed in source. Blocker 3
is addressed by the retained pre-actor baseline (86298af, e83914b), which the
runner verifies (5b52a0f):

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
3. **Writable workspace snapshot, addressed.** The actor could write the
   `.eval-snapshot.json` that grading read. 86298af binds original grading to
   a privately retained pre-actor baseline, and the runner verifies each
   attempt's baseline (5b52a0f). See `docs/original-attempt-custody.md` for
   its scope; that document, not this lane, makes the claim. The artifact and
   evaluator channels remain unavailable and refused.

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
| Valid outcomes: snapshot integrity | `:667,1303` | Addressed by the retained pre-actor baseline (86298af, e83914b) | The runner verifies every attempt's baseline (5b52a0f) |
| Subscription-only routing, no OpenRouter | `AGENTS.md` Model routing | Yes: `claude -p` under `bwrap --clearenv` (`:1012,1089`) | The executor also strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `OPENAI_*` and `OPENROUTER_*` |
| Six review dispositions | `docs/candidate-paired-request.md:82,148-149` | Not in the original issue | Optional; not on the runner path |
| Physical RSS, SDK selected-input custody | `docs/sdk-native-launch.md:75-80` | Not applicable to the CLI route | Not runner gates. Their figures describe the SDK path and are not live legacy blockers |

## sdk-weekly-compatibility defects against current legacy source

Ownership, per agent-341: this lane owns R1 (`TrialStore.__init__` cleanup),
R2 (the second wait in `TrialStore.stop`) and R3 (the `cmd_agent` store
setup and import-provenance block). The default TRIAL workspace and the
setup/commit/snapshot join were agent-504's; its baseline work has landed
(86298af, e83914b). Artifact and evaluator channel capture remain open.

1. **Default TRIAL workspace and latest version.** Live.
   `load_cases`, `load_corpus` and `verify_frozen` default to the latest
   version (`:87`, `:188`, called at `:1462-1463` and `:1071`).
   `prepare_workspace` defaults to `source_root=TRIAL` (`:642`, `:1067`).
   This does not block the runner. It runs a pinned exported harness commit
   and checks that latest equals the pinned `label_version` and frozen hash,
   so "latest" equals v12 by construction.
2. **Owned setup, commit and snapshot join.** None of the 21 v12 cases
   declares `workspace_commit` (counted only), so the setup result is never
   checked against a commit (`:658-661`). The writable snapshot is superseded
   for grading by the retained pre-actor baseline (86298af), verified per
   attempt by the runner (5b52a0f).
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
  `attempt_baseline_invalid`. So does a malformed retained document whose
  pin matches its bytes: JSON `null`, a scalar, a list, wrong or missing
  keys, wrong types, invalid JSON or JSON nested too deeply. The runner
  catches only these data errors (`BaselineError`, `TypeError`,
  `ValueError`, `KeyError`, `RecursionError`) around the unchanged load, so
  the receipt is terminal, and a started slot is never retried.
- No baseline is ever reconstructed.
- A complete receipt records `baseline_custody: {verified: 126, proof:
  harness_retained_baseline}`.

This is harness custody under the existing mount boundary, as
`docs/original-attempt-custody.md` states. It is not independent
attestation, source-version evidence or final-artifact custody. The
prospective path is unchanged.

## Original execution seam

This implements design revision 2
(`agent501-original-execution-20261021/DESIGN.md`) with root's corrections.
The runner profile gains an `execution` pin to the reviewed
`cairn.weekly-execution/1` declaration. `trial_original_execution.declared`
keeps every originally selected control and checks it with the unchanged
`trial_weekly_input._execution`. It also checks:
- the frozen source;
- that the model equals the capacity models;
- that the arm API revisions equal the run's commits;
- that the shared embedding worker equals the profile's.

Without the pin, the executor is blocked with `original_execution_unbound`.

**Carried today (`LEGACY_CAPABILITIES`):**
- **Native binary.** `trial_task_eval.py agent --legacy-fixtures
  --original-limits --original-native-path P --original-native-sha256 S
  --reasoning-effort E` takes structured flags, never `PATH:SHA`. Before any
  store starts, `trial_native_copy.retain` copies the binary to
  `out/native/claude` (mode 0500). The copy is bounded: a source that grows
  after hashing refuses before anything past the hashed size is written. The
  copy is re-hashed and checked against the declaration. Inputs open with
  `O_NONBLOCK | O_NOFOLLOW` and the opened descriptor must be a regular file,
  so a FIFO or device (including one swapped in after hashing) refuses
  without blocking. Every descriptor is closed on every error path.
  `--version` runs through the existing owned-process capture. Its result is
  accepted only when classification is `completed`, the leader was reaped,
  cleanup is `observed_clear` and the exit code is 0.
  `VERSION_LIMIT=4096` is an acceptance check applied after capture: stdout
  or stderr longer than 4096 bytes refuses. It does not limit what the
  generic capture allocates, which keeps its own larger finite limits. It
  is not a bound on the original aggregate memory or traffic envelope.
- **Per attempt.** The copy is re-hashed before launch and mounted read-only
  at the sandbox target `~/.local/bin/claude`. The provider runs that
  absolute target path with `--model` and `--effort`. The plan records
  `original_native.observed.source` (the retained host copy) and
  `original_native.observed.target` (the child path) separately. Each row
  records `original_native {binary_sha256, effort, target}`.
- **Credentials.** The legacy bind is unchanged: read-write, or the
  explicitly selected account. The tests assert this.
- **Model and effort.** These are compared on the plan and on every row.
- **Embedding worker.** This is the runner's shared worker.
- **Embedding model identity.** The declaration file must be the typed
  `semantic.EmbeddingIdentity` shape
  `{schema: cairn.embedding-model-identity/1, model_sha256, algorithm,
  dimensions}`. Anything opaque or untyped refuses
  `original_execution_unsupported:model_identity_declaration`.
  `TrialStore.wait_for_full_coverage` now keeps the backend's
  `{model_sha256, algorithm}` from the same discovery response it already
  reads. The runner compares those values per arm with the declared values,
  never with the declaration file's own SHA-256. Dimensions stay declared:
  discovery does not expose them, so the receipt records
  `dimensions: unobserved`. Three things stay separate: the declaration pin,
  the declared values and the observed values.

**Per-arm recall controls and embedding config (carried since the
recall-controls lane):**
- **Literal controls.** `recall_mode` and `semantic_fallback` reach each
  arm's hook configuration exactly as declared. An `agent_tools` arm is
  bound with the existing `bind_ordinary_claude` (inbox-recall binding,
  launcher task key), as the prospective route binds it. Before the provider
  starts, the attempt sandbox runs the same `BINDING_CHECK` that prospective
  prerequisites use: the engine's `RECALL_TASK_VERSION` and `recall_task_key`,
  then `inbox_recall.validate_binding(..., require_enabled=True)`. Any failure
  refuses `original_recall_binding_invalid` and the provider never runs. The
  controls document's hook and bridge pins are checked for their closed
  `{path, sha256}` shape first (`original_arms_shape`). Prospective input,
  its `validate_arms` and its schedule are not used; the legacy schedule and
  the original 21/126/1900 and caps are unchanged.
- **`recall_revision`.** It must resolve to exactly that full commit, with
  Git replacement objects disabled. The runner retains the raw
  `integrations/lifecycle/memory.py` and `inbox_recall.py` blobs at that
  commit. Each blob must equal the declared hook or bridge pin; otherwise the
  slot fails `original_execution_recall_mismatch` or `_recall_unresolved`.
  The harness mounts those retained blobs (the `--memory` hook path), and
  `trial_original_arms` checks them against the controls document
  (`cairn.original-arm-controls/1`, passed as `--original-arms` with its
  SHA-256). Each attempt re-hashes the hook and bridge before launch and
  records `original_arm {recall_mode, semantic_fallback, recall_revision,
  hook_sha256, bridge_sha256}`.
- **`embedding_config`.** The runner calls agent-504's
  `trial_embedding_launch.prepare(config, {CURRENT: worker, CAND: worker},
  slot/embedding)` and passes the retained launcher as the single
  `--embedding-worker`. The spec keeps `declared_worker`, `selected_config`,
  the retained `config` and the preparation receipt in separate fields. A
  helper refusal fails the slot as `original_execution_embedding_<code>`.
  The helper's limits are documented in `docs/weekly-embedding-launch.md`:
  no transitive Python identity, concurrent custody or admission.
- **Identity before attempts.** The harness receives the typed identity
  declaration (`--original-embedding-identity` with its SHA-256). After each
  store reaches full coverage, and before any attempt, it compares
  discovery's `model_sha256` and `algorithm` with the declared values. A
  mismatch or missing identity refuses
  `original_execution_identity_mismatch`. Dimensions stay declared only.

**Still refused** (named follow-on): an `ambient` arm with
`semantic_fallback: true` calls the hook's selector model, and the execution
contract declares neither that model nor its binary. It refuses
`original_execution_unsupported:semantic_fallback_selector`, both in the
runner and in the harness, and is never replaced by an ambient selector.

**Runner comparison** (`check_observed`):
- An absent observation gives `incomplete`: `original_execution_unobserved`
  or `original_execution_identity_unobserved`.
- A malformed observation gives `rejected` /
  `original_execution_observation_malformed`, never an escaped exception.
  This covers a non-object backend, readiness or identity value. It also
  covers a retained-copy record that lacks the producer's `source` (an
  absolute `.../native/claude`, distinct from the target), `observed_at`
  (UTC), `sha256`, `version` or `target`.
- A contradiction gives `rejected`: `original_execution_mismatch` or
  `original_execution_identity_mismatch`.
- The receipt records
  `original_execution {declared, observed, proof: harness_observed_copy}`.
  The proof is the hash of the mounted copy, not provider or model
  attestation.

**Replay.** A stored slot is replayed only if its receipt's
`original_execution.declared` equals the current declaration. Otherwise the
runner refuses `stored_slot_execution_mismatch`. That includes a receipt
written before the contract existed, and a current profile that dropped a
recorded execution pin. Nothing is re-executed and no receipt is
modified.

**Bridge** (`trial_candidate_run_bridge`):
- The runner profile's `execution` must be the reviewed request's
  `weekly_input.execution` (`bridge_execution_mismatch`).
- A stored claim must carry the same execution pin.
- Before execution, the spec's declared native pin, model and effort must
  equal the reviewed declaration.
- A complete receipt's observed native SHA-256 and effort must equal the
  declared ones (`bridge_runner_execution_mismatch`).

### Semantic-fallback selector identity (seam only; still refused)

**What the selector is.** An `ambient` arm with `semantic_fallback: true`
takes `memory.recall`'s `verified_candidate` path
(`integrations/lifecycle/memory.py`, the `elif
memory.config.get("semantic_fallback")` branch). It calls `select_json` for
the `preview` and `recall` stages. `select_json` runs `config["claude"]
--print --output-format json ... --tools ""` with `--model
selector_model(config, stage)`. `selector_model` reads `preview_model` or
`recall_model` first, then `model`. An `agent_tools` arm never calls it, and
neither does `inbox_recall` (it only uses `semantic` search).

Today the legacy harness writes `claude=/tmp/trial/selector/claude` and
`model=args.selector_model` (default `sonnet`), and binds `--selector-binary`
read-only at that target. These are ambient flag values, not declarations.
`cairn.weekly-execution/1` has a closed `memory_arms` shape with no selector,
and `native` is the actor's binary and model, not the selector's.

**Root decisions (2026-10-21, agent-341 review of `e22945d`).**
- A new `cairn.weekly-execution/2` adds one memory-arm field,
  `semantic_selector`. `/1` stays exactly as it is: no defaults and no
  reinterpretation. Dispatch on the schema is explicit.
- `semantic_selector` is a `{path, sha256}` pin. It is required when
  `recall_mode == ambient` and `semantic_fallback` is true, and null
  otherwise. Its content is the closed `cairn.semantic-selector-identity/1`.
- Each attempt is compared on its own evidence.
- A complete, authenticated observation with zero selector calls is
  `not_invoked` with the model `unobserved`. It is never `matched`, and no
  call is forced. Missing or incomplete hook evidence refuses.
- `original_execution_unsupported:semantic_fallback_selector` stays until
  full-boundary wiring and tests cover all of this.

**Declaration** (`typed`, `load_declaration`):

```json
{"schema": "cairn.semantic-selector-identity/1",
 "binary": {"path": "/abs/claude", "sha256": "<64 lowercase hex>"},
 "model": "<selector model name>"}
```

There are exactly these keys. `model` follows the hook's own `selector_model`
rule. The single `model` is written to the hook configuration's `model` key,
so it covers both stages. Anything else raises
`ValueError('semantic_selector_declaration')`. An unreadable or mismatched pin
raises `semantic_selector_unavailable`. The pin's SHA-256 is the declaration
file's, never the binary's.

**`/2` arm shape** (`execution_arm_fields`, `check_arm_selector`):
- `/1` fields equal the current `trial_weekly_input` literal (a test reads it
  from source).
- `/2` is those fields plus `semantic_selector`.
- An unknown schema refuses `weekly_execution_schema`.
- Refusals: `weekly_selector_required`, `weekly_selector_forbidden`,
  `weekly_selector_pin` and `weekly_arm_shape`. A `/1` arm returns no
  selector, so nothing lifts the refusal.

**Binary custody before effects** (`retain`, `mount`). These mirror
`trial_native_copy`:
- `retain` copies the declared binary to `out/selector/claude` (mode 0500) and
  re-hashes the copy. It executes nothing.
- `mount` re-hashes the copy before each attempt and returns the read-only
  bind `(copy, /tmp/trial/selector/claude, ro)` and the observed
  `{source, target, sha256}`. A changed or missing copy refuses
  `semantic_selector_binary_changed`.

**Configuration and exact argv** (`hook_config`, `selector_argv`):
- `hook_config` sets `claude` and `model`. It refuses
  `semantic_selector_config_conflict` if `preview_model` or `recall_model` is
  present (either would replace the declared model), or if a different
  `claude` or `model` is already set.
- `selector_argv` never imports or runs hook source on the host (agent-341
  review of `be9fcc1`). It reads the bytes and hashes them. A hash not in
  `REVIEWED_HOOKS` refuses `semantic_selector_hook_unreviewed` before the
  source is even parsed.
- `REVIEWED_HOOKS` maps a reviewed `memory.py` SHA-256 to its git revision and
  argv contract (`ARGV_CONTRACT`). It holds one entry, `43c4b61`'s blob
  (`53d826f6...`), and that entry is **proposed for root review**. Root adds or
  strikes entries, including the original `recall_revision` blobs.
- For an allow-listed hash, `argv_contract` uses `ast.parse` alone. It checks
  four things in the parse tree:
  - the `command` list in `select_json`;
  - the single guarded `command += ["--model", model]`;
  - no other `command` writes or method calls;
  - the `selector_model` key order and its resolution loop.

  The extracted contract must equal the reviewed one. Otherwise it refuses
  `semantic_selector_argv`.
- The argv is then built from the contract. It refuses if `claude` is not the
  target, if `preview_model` or `recall_model` is set, or if the resolved model
  is not the declared one.
- A test writes a hook with a top-level sentinel file and an environment
  variable. Unlisted, it refuses as unreviewed. Even force-listed, it refuses
  by static extraction. Neither side effect ever occurs.

**The capture consumer of `config.model`** (`registered_events`):
- `memory.handle_event` calls `capture` for `PreCompact`, `SessionEnd` and
  `Stop`. `capture` calls `select_json(..., stage="capture")`, which uses
  `config["model"]` and records no process observation.
- The legacy route registers only `SessionStart`, `UserPromptSubmit`,
  `PostToolUse` and `PostToolUseFailure` for claude (`HOOK_EVENTS`), and only
  the first two for codex. Its capture demand is therefore zero.
- `registered_events` returns `capture_demand: 0` for that registration. It
  refuses `semantic_selector_capture_demand` if a capture event is
  registered.
- `classify_attempt` refuses the same code if any capture or `unknown`
  observation row appears. The capture model is never silently charged to, or
  changed by, the selector declaration.

**Observation parsing** (`parse_observations`):
- It parses one attempt's raw `observations.jsonl` bytes, with unique JSON
  keys and a 16 MiB bound.
- Every row is checked before any record is read:
  - `schema` must be `cairn.task-hook-observation/1`.
  - `event` must be in the observer's closed set.
  - `recall_attempted` must be a boolean.
  - `stdout_sha256` must be 64 lowercase hex, and `exit_code` an integer or
    null.
  - `recall` may appear only on an attempted `SessionStart` or
    `UserPromptSubmit` row, with keys from the observer's `RECALL_FIELDS`.
- A process record must have only `bounded_command` and `select_json` keys,
  typed, with a known `outcome`.
- An empty process record means `select_json` never reached the child, so it
  is not a call.
- Any violation refuses the whole attempt with
  `semantic_selector_observation_malformed`.

**Per-attempt classification** (`classify_attempt`) takes only that attempt's
evidence:
- `mounted`;
- the raw observation bytes;
- its native `hook_response` identities `{hook_id, event, stdout_sha256}`;
- an `authentication` record `{method, observations_sha256}`.

The capture is complete only when all of the following hold:
- Every native hook finished (it has a digest).
- Hook ids are unique.
- The `(event, stdout_sha256)` multisets of native hooks and observer rows
  are equal. This is the correlation `trial_native_observation` already uses.
- Both recall events are present.

Authentication requires `method` in `AUTHENTICATION_METHODS` and the digest
of exactly these bytes.

`result` is one of:
- `matched`: requested model equals the declared model.
- `not_invoked`: complete and authenticated, with zero calls; the model is
  `unobserved`.
- `mismatched`: binary or requested model differ, including a null request,
  which means the CLI default ran.
- `incomplete`.

`refusals` names every failed part. `not_observed` is always `served_model`
and `provider_account`. `requested_model` is never called served.

**Authentication gap.** `AUTHENTICATION_METHODS` is empty in current source,
so every real attempt refuses `semantic_selector_capture_unauthenticated`:
- The observer appends `observations.jsonl` inside the read-write
  `/tmp/trial/hookstate` mount (`trial_task_eval.py:1189`, `:1223-1227`). The
  actor's tools can also write that mount.
- Native correlation binds each row's event and stdout digest through the
  owner-held native stdout (`trial_native_observation.py:339-367`, `:461-494`).
  It does not bind the row's `recall` content.

#### Proposed protected producer for selector observations

**Today: actor-invoked wrapper.**
- The native CLI runs the hook command, `bash -c 'python3 -B
  /tmp/trial/hook-observer.py ... | tee -a calls.jsonl'`. It runs inside the
  actor's sandbox, as the actor's uid, in the actor's PID namespace.
- No owner-held channel can be handed to it, because tool processes inherit
  the CLI's descriptors and can reach `/proc/<cli>/fd`.
- So no transport bound to this process separates the hook from the actor.
  This rules out a socket with peer credentials (`docs/sdk-memory-broker-socket`
  binds a live peer, not its executable, source or causing callback), an
  `OwnedWorker` stream, and a `--json-status-fd`/`pass_fds` FD.

**Existing SDK coordinator: not reusable without changing original
semantics.**
- `trial_sdk_control.SDKControl` receives `hook_callback` requests on
  owner-held native stdio and runs recall host-side through
  `EngineMemoryAdapter`. The incompatibilities are:
  1. **Engine binding.** `EngineMemoryAdapter` requires `recall_mode ==
     agent_tools` and `recall_observations is False`
     (`trial_sdk_engine_adapter.py:58-62`). It replaces `run_json` and
     `bounded_command` with a gated broker channel. An ambient selector's
     `select_json` would go through that broker instead of running the
     selector binary, and no `preview_process`/`model_process` record would
     exist.
  2. **Hook kind and stdout contract.** The original route uses a command
     hook whose stdout JSON the CLI parses, timed by the hook `timeout`.
     SDK callbacks are a `control_request`/`control_response` exchange with
     `hookCallbackIds` registered in `initialize`
     (`trial_sdk_control.py:204-208`). That changes the native launch mode,
     the event ordering on the stream (`hook_started`/`hook_response` versus
     control frames) and the response bytes.
  3. **Failure behaviour.** A command hook's nonzero exit or timeout is a CLI
     hook error. An SDK callback failure is a control error or
     `sdk_callback_timeout` refusal (`trial_sdk_control.py:673-679`). The two
     have different native consequences.
  4. **Timing.** Both use 5 s (`HOOK_SECONDS`, `LIMITS['hook_seconds']`), but
     the SDK clock starts at the control request, not at the command spawn,
     and includes the owner's producer start-up (`trial_sdk_fixed_run.py:240-251`).

**Proposal: owner-launched engine, legacy command hook kept.**

Ownership:
- The legacy harness process that already owns `capture()` and the native
  stdout (`trial_task_eval.run_agent`) creates, per attempt, an *owner hook
  executor*. It holds:
  - a listening `SOCK_SEQPACKET` socket in an owner directory, bind-mounted
    read-only into the sandbox at `/tmp/trial/hook-signal`;
  - an owner-only `hookstate` directory, which is no longer mounted into the
    actor sandbox;
  - an append-only ledger and observations file outside every actor mount.

Interface:
- The actor's command hook becomes a reviewed, read-only *signal stub*. It
  sends one bounded frame `{event bytes}`, waits, writes the returned stdout
  bytes unchanged and exits with the returned code.
- For each frame, the owner runs the unchanged pinned `memory.py` under the
  unchanged `trial_hook_observer`. It runs in an owner-launched bwrap that
  mirrors today's hook view: the same paths, ro hook, config and selector,
  the store socket, the workspace, `--clearenv` with the same variables, and
  `state_dir` on the owner-only hookstate. Its deadline is the matching
  `hook_started` line's
  receipt time plus 5 s, never frame receipt plus 5 s (see the deadline
  bullet below).
- The owner appends the observer's row to its observations file. It appends
  one `cairn.selector-owner-execution/1` ledger row (`check_owner_executions`)
  binding:
  - `attempt_id` and a contiguous `sequence`;
  - `event` and `hook_input_sha256` (the bytes the owner received, not
    proven to be the CLI's input);
  - `stdout_sha256` and `exit_code`;
  - the observation line's SHA-256;
  - the engine, observer, config and selector SHA-256 values it mounted;
  - `frame_received_seconds`, `deadline_seconds` and `completed_seconds`, in
    the capture clock.
- It then returns stdout and the exit code to the stub.

Binding points:
- **Reviewed source and config:** the owner mounts the retained blobs and
  config that `trial_original_arms`, `retain`/`mount` and `selector_argv`
  checked, and records their digests in every ledger row.
- **Attempt:** one executor, socket, ledger and `attempt_id` per attempt.
- **Owner execution** (`check_owner_executions`): the owner ran this
  engine, with these bindings, on these bytes, producing this observation
  line. This is the owner's own claim, and it is kept apart from the next
  three.
- **Native correlation** (`correlate_native`; agent-341 reviews of `ceff369`
  and `22e052e`). Matching is staged. Each stage searches for *any*
  one-to-one assignment of ledger rows to native hooks under all constraints
  so far (augmenting paths). It refuses only when no valid assignment exists
  at that stage. The order of rows and native events never changes the
  result. The claims stay separate:
  1. `output_correlated`: event and stdout digest. An extra replayed frame or
     a dropped stub frame breaks the counts.
  2. `window_correlated`: each frame arrived after its `hook_started` line
     and finished before its `hook_response` line, in the owner's capture
     clock. A replay outside the window refuses.
  3. `deadline_from_native_start_receipt`: each deadline equals that
     `hook_started` line's *owner receipt* time plus 5 s, and the execution
     finished by then. Receipt is later than the CLI's own spawn by an
     unobserved delay, so this is not the CLI's actual hook deadline. With no
     valid assignment, it refuses `semantic_selector_owner_deadline_restarted`
     (no assignment has receipt-based deadlines) or
     `semantic_selector_owner_deadline_exceeded`.
  4. `input`: checked only when every native hook carries an input digest
     (none does today). `anchored` means some assignment also matches it;
     `mismatched` means none does; `unanchored` otherwise.
- **`assignment`** is `unique` or `ambiguous` at the deepest stage reached,
  shown by trying to exclude each chosen pair. A match, unique or ambiguous,
  is never causal authentication. Ambiguous valid assignments are reported,
  never turned into a false mismatch.
  - agent-341's counterexample: two PostToolUse hooks with the same stdout and
    overlapping windows, told apart only by deadline and input. It now yields
    the valid pairing in every row and event order.
- **What the native stream exposes.** Retained Claude CLI streams show
  `hook_started` = `{type, subtype, hook_id, hook_name, hook_event,
  session_id, uuid}`. `hook_response` adds `{stdout, stderr, output,
  exit_code, outcome}`. Neither carries the hook's input bytes, an input
  digest or a CLI timestamp. The only clock is the owner's per-line receive
  time (`trial_native_capture.capture` timings).
- **Consequence: a one-for-one replacement is invisible.** An actor frame
  that replaces the stub's, with different input but the same stdout, passes
  the output and window claims. A replay inside the window does too. So
  `input` stays `unanchored`, with gap `native_hook_input_unobserved`. "The
  owner executed X" never stands in for "X was the CLI's hook input". The
  tests show both cases, and show that a synthetic native input digest would
  expose the replacement (`semantic_selector_owner_input_mismatch`).
- **Named missing native observations** (each needs the CLI to emit it; not
  derivable from current data):
  1. **Input anchor.** `hook_started.input_sha256`: the SHA-256 of the exact
     stdin bytes the CLI writes to the hook command, or the input bytes
     themselves. Without it, input provenance is unprovable.
  2. **Start anchor.** A CLI-side hook start time, or the CLI's own timeout
     deadline, on `hook_started`. Today `hook_started` receipt is later than
     the CLI's spawn by an unobserved emission and pipe delay (gap
     `native_hook_spawn_time_unobserved`). The owner deadline is therefore
     at most that delay later than the CLI's, never a restarted 5 s.
  3. **Clock bridge.** The executor needs the capture's monotonic start (or
     absolute monotonic receive times) to share the timing clock. Today
     `capture` keeps `started` local (`trial_native_capture.py:206-215`).
- **Completeness accounting:** the owner owns the ledger, the native stream
  and the comparison.

Residual and unresolved:
- Until observations 1 and 2 exist, the proposal can claim owner execution,
  output and window correlation, and a native-start-bounded deadline. It
  cannot claim input provenance. Root must decide whether that is enough. If
  not, the producer stays blocked on the CLI, or on another input witness
  the actor cannot reach.
- Whether the CLI writes `hook_started` before or after it spawns the hook
  is unobserved. If it spawns first, a genuine frame can arrive before its
  `hook_started` line. The owner then has no native start for the deadline,
  and window correlation refuses. This fails closed, but it could refuse
  legitimate attempts. Start anchor 2 would remove this.
- Running the engine outside the CLI's process tree also changes these
  properties, each of which needs root's acceptance:
  - which process the CLI kills on timeout: the stub, so the owner must kill
    the engine at the native-start deadline;
  - stderr routing;
  - the hook's view of `/proc` and PIDs.
- Nothing is added to `AUTHENTICATION_METHODS`, and no call site is wired,
  until these points are resolved.

Owned-file changes this needs (not made):
- `trial_task_eval.py`:
  - :1185-1214: take the hookstate bind out of the actor binds, replace
    `hook_cmd` with the stub, and add the signal mount.
  - :1223-1227: the binds list.
  - :1334/:1370/:1414: read the owner files.
- A new owner executor module, in the writer worker's lane.
- `trial_native_capture.capture` (:204-215): expose the capture's monotonic
  start, or absolute receive times, so ledger times share its clock. Also
  retain `hook_started`/`hook_response` events with their line receive times
  for `correlate_native`.
- `trial_native_observation.py:277-367`: expose the per-hook `{hook_id,
  event, stdout_sha256}` it already builds.
- `trial_original_execution.check_observed` (:150-184): consume the
  per-attempt classification.

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

This section is a proposal only. Neither (1) nor (2) is implemented, no
reviewer role is made mandatory, and no self-tests run at runtime. Both
need a separate design decision by root: (1) needs an issuer role and a
schema, and (2) would change the runner's pre-claim cost and needs a
test-selection contract.

## Joins to an actual campaign

Each join from this runner to an actual 21-case / 126-attempt /
1900-distractor workload, with its state:

| Join | Anchor | State |
| --- | --- | --- |
| Capacity adapter to runner pre-check | `trial_capacity_adapter.adapt`/`verify_sources` to `trial_weekly_run.capacity_gate` | **Implemented** (synthetic). Real input missing: no absolute-allowance observation and no account binding for the executor's account exist (blocker 2). The absolute unit is the current adapter constraint |
| Live selection to runner | `cairn.weekly-run-selection/1` to `selection_gate` | **Implemented**. No selection issued (root decision) |
| Runner to harness | `LegacyHarnessExecutor.execute` to `trial-task-eval.sh agent --legacy-fixtures --original-limits` plus the original-execution flags | **Implemented**, fake harness only. Carries every declared control except an ambient arm with `semantic_fallback` (`semantic_fallback_selector`). Needs a selected account |
| Harness content hashes | `trial_task_eval.run_agent` `stream_sha256`/`prompt_sha256` (`:1323-1324`, 04ba225) to `_agent_result` | **Implemented**: required on every row. Present only in runs from a harness commit at or after 04ba225; older runs, including 2026-09-29, can never be complete |
| Runner receipt to pair binding | `_pair_binding` writes `cairn.legacy-pair-binding/1` | **Implemented**. Declared, not reviewed issuance; the issuer's identity is not authenticated |
| Pair binding to converter | receipt `pair_binding` + `agent`/`plan` pins to `trial_legacy_blind_selection.convert` | **Implemented and tested end to end** (synthetic: 63 pairs, 15 changed). The conversion request's case materials (contracts, task statements, grading instructions) still need a real, private issuer |
| Action channel | `trial_legacy_blind_selection` conversion/2 `action_trace` from the retained, hash-verified stream | **Supported** as of agent-504's 7d4333f (accepted candidate 1c4a9e9) |
| Artifact and evaluator channels | `trial_legacy_blind_selection` refuses `legacy_channel_unbound` | **Unavailable and refused**: `artifact_bundle` and `evaluator_observation` need actor-final capture before grading |
| Baseline custody per attempt | `baseline_custody` (agent-504, `trial_attempt_custody.AttemptBaseline.load`) to `_limit_result` | **Implemented**: required and verified for every attempt before `complete` or a binding (harness custody, not attestation) |
| Snapshot integrity for outcomes | retained pre-actor baseline (86298af, e83914b) verified by the runner (5b52a0f) | **Implemented** (harness custody, not attestation) |
| Grader delivery release authority | `trial_grader_delivery._check_authority` (`cairn.grader-release-authority/1`) | **Not implemented here**. Requires an owner- or coordinator-issued authority and fresh eligible grader sessions; none exists |
| Grades to grading join | `trial_legacy_grading_bundle` (6cf32c0) and the occurrence orchestration (8192157) into `trial_grading_custody.join_results` | **Implemented synthetically**. Actual returned grades are still missing |
| `trial_run_report` input | receipt `report_input.agent` to `trial_run_report.py render --agent --join` | **Agent half implemented** (document checked with the report's own `_agent` reader, arms `CURRENT`/`CAND`). The `--join` half depends on the grading join above |
| Two-week completion | two consecutive `complete` receipts, reports and notes | **Not started**. Requires every join above plus two live slots |
| Live campaign | an actual 21-case / 126-attempt / 1900-distractor weekly run | **Unproven**. No live run, provider call or actual grade exists; every result above is source and synthetic evidence only |

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
- `test_trial_semantic_selector` (38 synthetic tests) covers the declaration
  and malformed declarations, the `/1` and `/2` arm rules, binary custody,
  configuration conflicts, the reviewed hook's argv (static only; a hook
  with top-level effects is refused and never runs), the owner ledger,
  staged native correlation (replacement, replay, window, deadline,
  concurrency, agent-341's counterexample with permutations, ambiguity),
  capture demand, schema-checked parsing, and per-attempt `matched`,
  `not_invoked`, `mismatched`, `incomplete` and unauthenticated results. It
  uses fake binary files and fabricated hook rows only.

## Remaining work

- Evidence for the dedicated-account declaration, and real single-account
  capacity evidence for the selected `account_id`.
- The artifact and evaluator channels (actor-final capture before grading).
- The hook selector for an ambient arm with `semantic_fallback`
  (`semantic_fallback_selector`). The seam is in `trial_semantic_selector`.
  Still to do: `/2` dispatch in the owned files, harness and runner wiring,
  root review of `REVIEWED_HOOKS`, and the proposed owner-launched engine
  (or another accepted channel) for hook observations.
- Real capacity-adapter input for the bound account, and a root-issued live
  selection for one concrete slot.
- Real conversion materials, a grader release authority, delivery, the
  grading join, `trial_run_report` render and publish, and two consecutive
  complete weekly runs.
