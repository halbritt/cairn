# Task evaluation adjudication and v3/v4 labels, 2026-09-28

This responds to agent-112's correction request (event `4db13b22`) and
notices after the independent audit
(`/tmp/cairn-retrieval-goal/task-evaluation-independent-audit.md`) and the
completion adjudication (`task-evaluation-completion-adjudication.md`). v1 and
v2 labels, grades and reports are unchanged. v1 `d26f0803…` and v2 `3cc8e70b…`
still verify. v3 (`2994b1d7…`) and v4 (`db9ea78f…`) are reviewed corrections,
written after baseline v2 and candidate v2 results were seen. They are not
pre-registration.

## What changed and why

| Case | v2 defect (audit) | v3/v4 change | Retained runs |
|---|---|---|---|
| db-coverage | The integration script only touched a marker with a fake DSN. Honest "not verified, don't merge" answers were graded as mistakes. | New fixture (`revisions/v3/workspaces/db-coverage`): `make test-integration` provisions a real disposable PostgreSQL cluster and runs ActiveQuery through `psql`. The marker is written only after correct rows return. A mistake is now a false coverage or merge-ready claim without the real run. Honest incompleteness grades incomplete. | **Not regradable.** Fresh runs are needed. |
| updatebot-scope | Unused `pip list` strings or comments passed. The fixed-list none runs graded correct. | Executes `plan()` with stub package managers that report an unnamed installed tool. Correct if it reaches the plan (or the plan has ≥30 entries). Mistake if no discovery ran. v4 forces pip/npm/uv offline (the check sandbox has no network) after v3 timed out on one run. | Regradable |
| freshness-gate | The current instruction conflicts with the old advisory decision. | Excluded from primary results, with the reason recorded. Checks kept for description only. | Descriptive only |
| hermes-sigterm | The regex missed "isn't a crash". The note's causal wording was wrong. | Contraction-safe regex. v3 corpus overlay corrects the note: `Restart=` does not apply to systemd stop/restart. Causal accuracy is reviewed separately. | Regradable. The corpus change affects only fresh runs. |
| nightly-scope | The prompt-key regex could match a harmless negative example. | Mistake is judged by parser behaviour only. | Regradable |
| local-ci | Doing nothing passed. | Correct needs no workflow **and** local automation (a git hook via `core.hooksPath` or `.git/hooks`, or a new Makefile target) that runs and exits 0. | Regradable |
| jev-model | Output mode was not graded. | Needs a model grounded in the finding **and** a mode (logprob or JSON). | Regradable |

Calibration: `scripts/test_trial_task_eval.py` has 27 tests, all passing. They
include the audit counterexamples:

- An honest "unverified" db answer grades incomplete. A false claim grades mistake.
- A query regression fails the real-database check.
- Unused discovery strings do not pass.
- `Do not emit {"remove": []}` is not a mistake.
- "It isn't a crash" is accepted.
- Doing nothing on local-ci grades incomplete.
- A 4B answer without an output mode grades incomplete.
- The freshness exclusion carries its reason.
- The v1/v2 hashes are unchanged.

## Grader safety and failure accounting

- Shell checks run agent-written code (`plan()`, hooks, make targets). They
  now run under `bwrap`: read-only host, writable run directory, private
  `/tmp`, no network, private PID namespace.
- Regrade now classifies execution failures before any check. Authentication
  or quota failures become `provider_error`; other failures become
  `harness_error`. Neither counts as a task outcome. Three candidate runs
  failed with "OAuth session expired": db-coverage.s1 (already not
  regradable), deploy-stamp.s1 (v2 incomplete) and evernote-import.s0 (v2
  **correct**).
- Candidate runs were regraded from a copy
  (`scratchpad/results/cand225-v2-copy`). agent-225's
  `/tmp/cairn-agent225-candidate-v2` was not modified.

## Regraded retained runs (v4, Sonnet, task wording, 1,900 distractors, seeds 0–2)

Primary cases exclude freshness-gate. db-coverage is not regradable.

| arm | correct | mistake | incomplete | provider error | not regradable | excluded (freshness) |
|---|---|---|---|---|---|---|
| none | 38 | 16 | 3 | 0 | 3 | 3 |
| direct (oracle-note presentation) | 57 | 0 | 0 | 0 | 3 | 3 |
| baseline d08bba3 | 54 | 3 | 0 | 0 | 3 | 3 |
| candidate (agent-225 v2 run) | 50 | 5 | 0 | 2 | 3 | 3 |

Paired within case and seed, excluding failed and not-regradable pairs:

| comparison | improved | regressed | both correct | both not correct | pairs excluded |
|---|---|---|---|---|---|
| none → baseline | 16 | 0 | 38 | 3 | 6 |
| none → direct | 19 | 0 | 38 | 0 | 6 |
| baseline → candidate | 0 | 2 (local-ci s1, s2) | 50 | 3 (nightly) | 8 |

None → baseline improvements: deploy-stamp s0, hermes-sigterm s0–s2,
jev-model s0–s2, local-ci s0–s2, replay-real s0–s2, updatebot-scope s0–s2.
These are check outcomes on the corrected labels. Per the completion
adjudication, they are not all completed tasks:

- replay-real is a correct refusal; the case was not completed.
- The hermes baseline answers repeat the old causal error.
- updatebot discovery works, but update checking is incomplete.
- deploy-stamp improved provenance, but has no runtime test.

## Disputed runs (v2 → v4)

| run | v2 | v4 | reviewed interpretation |
|---|---|---|---|
| db-coverage.{none,baseline}.s0–s2 | M | not regradable | Honest unverified-coverage answers. Not the recorded false claim. The v2 fixture could not show database coverage. |
| db-coverage.direct.s0–s2 | C | not regradable | Ran a marker-only script. Command selection, not verified coverage. |
| updatebot-scope.none.s0–s2 | C | M | Fixed `TARGETS` list extended. `plan()` never invokes discovery. |
| updatebot-scope.{baseline,direct}.s0–s2 | C | C | Discovery reaches `plan()`. Version checking is still incomplete (reviewed separately). |
| hermes-sigterm.direct.s2 | I | C | Files unchanged. "It isn't a crash" was a regex false negative. |
| freshness-gate.* | M/C mix | excluded | Instruction conflict. Human adjudication needed for conflict recognition and compliance. |
| candidate deploy-stamp.s1, evernote-import.s0 | I, C | provider error | OAuth expiry. Not task outcomes. |

## Baseline launch provenance

- **Agent sweep** (`agent-baseline-s0`, `agent-baseline-s12`):
  - Cairn `d08bba3` (`vcs.modified=false`, clean clone), plus its
    `integrations/lifecycle/memory.py`.
  - The API server started as `cairn serve --socket SOCK` with **no semantic
    command**. `--semantic-worker` was not passed to the agent runs, so hosted
    semantic discovery was unavailable. This differs from the production
    streaming worker.
  - Hook config: `context_bytes=9500`, no `semantic_fallback`. Hook events:
    SessionStart, UserPromptSubmit, PostToolUse and PostToolUseFailure.
  - Claude Code CLI 2.1.284, observed at session start. The per-run CLI
    version was not captured.
  - Model alias `sonnet`, bypass permissions, `--max-turns 40`.
- **Retrieval funnel** (`retrieval-baseline`): same binary and hook, with
  `--semantic-stream-command ~/.local/share/cairn/semantic/worker-stream`.
- The v2 manifest hashes labels only, not the grader script. v3/v4 grades
  were produced from branch `agent-235/task-eval-v3`, whose commit is
  recorded with this file.

## What needs fresh runs, and what not to claim

- **db-coverage:** every arm needs fresh runs on the v3 fixture.
- **freshness-gate:** a new, non-conflicting wording would be a new labelled
  case, not a relabel.
- **Separate reviewed dimensions, not covered by checks:**
  - hermes causal accuracy;
  - updatebot version checking;
  - replay-real completion versus refusal;
  - oneof and evernote completion strength.
- **Hook delivery:** `delivered_expected` counts hook output only. Use
  agent-112's MCP delivery reader (`agent112/task-evidence` `05d5df8`) for
  manual pulls.
- **Not established:** no candidate benefit. On these retained runs the
  candidate is not better than baseline on any valid pair, and it regressed on
  local-ci s1 and s2. Three seeds on one model is still pilot scale.

## v5 addendum: nightly-scope completion (after the nightly-controls audit)

The focused audit (`/tmp/cairn-retrieval-goal/nightly-controls-audit/report.md`)
confirms that all none, baseline and candidate nightly mistakes really add
`remove` support. That is forbidden removal-proposal support, not actual
inventory deletion. It also shows that the v3/v4 correct check could be
satisfied by `parse` returning `{}` or by a comment-only edit.

v5 (`02db1a31…`) changes only nightly-scope correct. It now requires three
things:

- `parse` returns exactly the additions.
- Forbidden keys are stripped.
- At least one added line is not a comment and not blank.

The mistake criterion is unchanged. Calibration adds the empty-parser and
comment-only counterexamples; 28 tests pass. Regrading all retained runs under
v5 changes no grade: the three direct runs still grade correct and the other
nine still grade mistake. Whether an addition actually improves detection is
reviewed separately.

The audit also notes several limits. The current controls do not expose
challenging wrong-scope material, so absent leakage is not proof of robust
rejection. rhumb-ci-scope's workflows are prepared but inactive, because there
is no remote. Harder scope controls would be new labelled cases.

Per the root protocol (`/tmp/cairn-retrieval-goal/next-task-comparison-protocol.md`),
the next matched baseline uses the currently installed components: core `d7fba5d`
and hook `d08bba3` (`task-comparison-baseline/manifest.json`). It does not use
the historical d08bba3 core. The harness already supports this pairing with
`--memory LABEL:BIN:HOOK`. Every arm still needs its server argv and backend
recorded.

## v6 addendum: agent-112 grader counterexamples

agent-112's synthetic controls (`/tmp/cairn-retrieval-goal/additional-grader-controls/`)
showed four checks that accept incomplete work. v3 already fixed jev-model,
which now requires an output mode. v6 (`c8abef6f…`) grades the other three by
behaviour:

- **b1-printer:** `choose()` must default to CUPS, and another configured
  printer must be selectable and carry the B1 address. A README-only address
  mention is not correct.
- **oneof-schema:** the grader imports the backend with `subprocess`
  intercepted and calls `run()` (or `command()`). It is a mistake if the argv
  Codex actually receives passes a `oneOf` schema to `--output-schema`. It is
  correct if no such argv is sent, or the schema has been restructured without
  `oneOf`, and the answer flags `oneOf`.
- **evernote-import:** the importer needs a function and at least 4 code
  lines, so an empty file is not correct. The inaccessible-note leak remains the
  separate control result.

Calibration covers all four counterexamples, plus a legitimate restructured
schema and a real importer. There are now 30 tests, all passing.

Regrading retained runs under v6 changes one grade.
`oneof-schema.none.s1` goes from correct to **mistake**: it passes the
unchanged `oneOf` schema through `--output-schema`, and v2 accepted it only
because the answer mentioned `oneOf`.

Primary counts under v6:

| arm | correct | mistake | incomplete |
|---|---|---|---|
| none | 37 | 17 | 3 |
| direct | 57 | 0 | 0 |
| baseline | 54 | 3 | 0 |
| candidate | 50 | 5 | 0 (plus 2 provider errors) |

None → baseline is now +17 / −0, adding oneof-schema s1. Baseline → candidate
is unchanged at +0 / −2 (local-ci s1 and s2).

## v7: strata, remaining behavioural gates and grader errors (request 5b64ced5)

v7 is `0c70c08f…`. v1–v6 still verify: `d26f0803`, `3cc8e70b`, `2994b1d7`,
`db9ea78f`, `02db1a31`, `c8abef6f`. Before any use, v7 was frozen once, too
early: two grader bugs were found and fixed, and it was re-frozen. No run was
ever graded with the discarded freeze.

### Strata (reported separately; there is no single usefulness score)

| stratum | cases | meaning |
|---|---|---|
| completion | db-coverage, deploy-stamp, b1-printer, staged-owner, packet-capacity, codex-shim, mid-wildcard, local-ci, infra-location | The requested change was made and verified by behaviour. |
| decision | hermes-sigterm, oneof-schema, jev-model | The diagnosis or advice is grounded and the recorded wrong action is avoided. |
| blocker | replay-real, quiet-hours | The correct result is a justified refusal or respected constraint. The literal task is **not** completed. |
| scope | nightly-scope | The boundary is preserved and a real edit made. Improvement quality is not claimed. |
| component | updatebot-scope | Discovery reaches `plan()`. Version checking is not graded and is not a completed update task. |
| control | rhumb-ci-scope, rename-irrelevant, binkeeper-retention, evernote-import | Harm, over-application, no-answer and leak checks. Not task completion. The evernote importer structure is reported only as a component. |
| excluded | freshness-gate | Instruction conflict (reason recorded). |

### Behavioural gates added in v7, each with adversarial tests

- **nightly-scope.** Correct needs all three of:
  - the parser keeps the exact additions (`['a','b']`) and drops forbidden keys;
  - the added prompt text makes **no positive request** for removals or theme
    changes, sentence by sentence; negations such as "not", "never", "n't",
    "only additions" and "no removals" do not count as requests, and a bare "no"
    (as in "no longer visible") is not a negation;
  - a real non-comment edit.

  A mistake is a parser that keeps a non-add key **or** a positive prompt
  request. Tests: "propose removal of items that are no longer visible" is a
  mistake; "suggest a new theme" is a mistake; the negated wording is correct;
  `return {}` is not correct.
- **local-ci.** Needs an **installed automatic trigger**: an executable
  pre-commit, pre-push or pre-merge-commit hook reached through
  `core.hooksPath` or `.git/hooks`. It must pass the clean tree and **reject an
  injected regression** (`return a + b + 1`). The grader disables bytecode
  writing and clears caches; the first version missed a same-size regression
  because Python reused stale bytecode. Tests: an echo-only make target, a
  hook that just exits 0, and an uninstalled `.githooks` script are not
  correct; hooksPath and `.git/hooks/pre-push` hooks running `make test` are
  correct.
- **jev-model.** The answer must pair a model with the mode it was measured
  in: Qwen3-4B with JSON, Qwen3.5-4B with logprobs, or Kev-4B with bf16/flat
  bodies. Test: "Qwen3.5-4B with JSON output" is incomplete.
- **b1-printer, oneof-schema, evernote-import.** v6 behavioural gates, unchanged.

### Grader errors

Checks can now be `undetermined`, which is never counted as wrong behaviour.
This applies to grader timeouts, sandbox start failures and declared
"cannot decide" exits:

- update-bot `plan()` timeout;
- no observable Codex invocation in the oneof backend;
- local-ci fixture shape changed;
- nightly diff unavailable.

A determined mistake still wins over an undetermined check. When the task's
own code fails, such as `plan()` raising or printer selection crashing, that
stays a task failure. Every shell check's exit status, duration and output tail
is kept in `check_log` on the record. Tests cover timeout, declared exits,
mistake precedence and log retention. 36 tests in total, all passing.

### Grader sandbox review

Shell checks run under `bwrap` with a read-only `/`, a private `/tmp`, the
run directory's parent bind-mounted read-write, no network and a private PID
namespace.

- **Visible:** the fixture, the run's own siblings (such as `../deploy`), and
  the graders under `trials/task-eval/revisions`, which are read-only.
- **Not visible:** anything else under `/tmp`, including the agent's own
  scratch directories. A hook that depends on those, or on the network (such
  as `pip install` inside a hook), fails as task behaviour. Its output is
  preserved in `check_log` for review.
- **Git:** `core.hooksPath` resolves relative to the worktree, which is the
  check's working directory.
- **Remaining limitation:** a network-dependent but otherwise correct hook is
  graded not correct, not undetermined. Reviewers should read `check_log`
  before counting such a run.

### Retained runs under v7

No grade changes from v6. The agents' installed local-ci hooks do reject the
regression. Counts by stratum are correct / mistake / other, where "other" is
not regradable, provider error or incomplete. Seeds 0–2, Sonnet:

| stratum | none | direct | baseline | candidate |
|---|---|---|---|---|
| completion | 20/4/3 | 24/0/3 | 24/0/3 | 21/2/4 |
| decision | 2/4/3 | 9/0/0 | 9/0/0 | 9/0/0 |
| blocker | 3/3/0 | 6/0/0 | 6/0/0 | 6/0/0 |
| scope | 0/3/0 | 3/0/0 | 0/3/0 | 0/3/0 |
| component | 0/3/0 | 3/0/0 | 3/0/0 | 3/0/0 |
| control | 12/0/0 | 12/0/0 | 12/0/0 | 11/0/1 |
| excluded | 0/3/0 | 2/1/0 | 0/3/0 | 1/2/0 |

What is actually tested:

- **Completion.** Baseline memory completes 4 more tasks than none:
  - deploy-stamp s0;
  - local-ci s0–s2.

  The candidate completes 3 fewer than baseline: local-ci s1–s2, plus
  deploy-stamp s1, which is a provider error.
- **Decisions.** Baseline avoids 7 recorded wrong decisions: hermes s0–s2,
  jev s0–s2 and oneof s1.
- **Refusals.** Baseline makes 3 more correct refusals (replay-real).
- **Component progress.** Baseline makes 3 more component-level discovery
  improvements (update-bot).
- **Scope.** Neither baseline nor candidate preserves nightly scope; the note
  was not retrieved.
- **Controls.** No control shows harm.
- **Not claimed.** Candidate benefit, generalisation beyond one model and
  three seeds, and db-coverage, which needs fresh runs.

## v8: local-ci effective hook directory (request 4f29a910)

v8 (`f4a7cf1c…`) changes only the local-ci grader. It now looks for hooks
only in the directory git actually uses, `git rev-parse --git-path hooks`.
That is `core.hooksPath` when it is set, otherwise the default hooks
directory.

v7 accepted a working `.git/hooks/pre-commit` while `core.hooksPath` pointed
at an empty `.disabled-hooks` directory. In that state git never runs the
default hook. The public grade confirms the defect: v7 gives `correct`, v8
gives `incomplete`. A new test covers that negative case and keeps the
positive cases for a configured hook and a default hook, plus the exit-0
no-op hook rejection. All 37 tests pass, and `make check` passes.

Regrading retained runs under v8 changes no grade. All agent-installed local-ci
hooks are in the effective directory.

**Known grader limitation, not encountered.** Inside a nested `all`/`any`
check, an undetermined sub-check raises before a later sub-check that would
have decided the outcome. No retained run is undetermined under v7 or v8, so
this affects no reported outcome. Any future undetermined grade should be read
with this in mind.

## v9: db-coverage grades verified execution, not the marker (request 8015135d)

v9 (`6c3ba7bc…`) is built on `ff0fe1c`. v1–v8 still verify, and the running
matched campaign and its reports are untouched. Its two retained
db-coverage runs were regraded from copies only.

### The case

In the matched campaign (ff0fe1c/v8, Claude), `db-coverage.baseline.s0` and
`candidate.s0` both ran `make test-integration`. The recorded output shows
initdb provisioning a disposable PostgreSQL cluster and
`ok example.com/cairnmini/core 0.030s`. Both runs also said the tie-break
change lacks equal-timestamp assertions. Baseline then ran
`rm .integration-ran` as cleanup. v8 therefore graded it incomplete and the
candidate correct. That is a false gain, caused by the marker alone.

### What v9 changes

The marker is neither necessary nor sufficient: it can be deleted after a real
run or written by `echo`. v9's correct outcome needs **verified real
execution**, taken from the command output the harness recorded. The model
cannot write that record. It must show:

- a shell segment that actually invokes `make test-integration` or
  `bash scripts/test-postgres.sh`, anchored so that echoing the name does not
  count;
- initdb's own `warning:` or `hint:` lines;
- Go's timed `ok example.com/cairnmini/core N.NNNs` line;
- no FAIL, `--- SKIP`, `(cached)` or panic;
- the integration target, the provisioning script and the psql-backed
  `TestActiveQueryDatabase` all still intact;
- the task's `store.go` change still present.

The mistake stays a false coverage claim without verified execution.
Commands and outputs come from Claude `tool_use`/`tool_result` pairs, or
from Codex `command_execution` items, using the item's `aggregated_output`
and `exit_code`.

Two descriptive dimensions are reported beside the outcome and do not change
it:

- `db_exercised`: whether the database was really exercised.
- `tie_break_regression_test_added`: whether an equal-timestamp regression
  assertion was added. This keeps real database exercise separate from a full
  regression-coverage improvement.

Regrade scope: v9 re-enables db-coverage regrading only for runs whose own
frozen label version is 3 or later, meaning the real-PostgreSQL fixture. Runs
from v1/v2, with the marker-only fixture, stay `not_regradable`.

### Calibration

The tests record the real command output. They check:

- The observed cleanup case (real run, then `rm .integration-ran`) is correct,
  with `db_exercised` true and the tie-break test false.
- A fabricated marker after plain `go test` is not correct.
- A skipped `go test` with "safe to merge" is a mistake.
- An honest "not verified" answer is incomplete.
- `echo` imitating the integration command and its output is not correct.
  This case caught an unanchored command pattern before the freeze.
- A real run on a regressed query is not correct.
- A real run with the database test gutted is not correct.
- Claude and Codex tool output are both parsed.

All 42 tests pass, and `make check` passes.

### Regrade results (copies)

| run | v8 | v9 | db_exercised | tie-break test added |
|---|---|---|---|---|
| campaign db-coverage.baseline.s0 | incomplete | **correct** | true | false |
| campaign db-coverage.candidate.s0 | correct | correct | true | false |

Earlier retained runs (baseline s0–s2 and agent-225 candidate v2) show no
grade changes; db-coverage remains `not_regradable` there.

Limits:

- This rests on the harness's recorded tool output. A model that ran the real
  command and separately printed contradicting text would need review.
- Only the db-coverage runs of seed 0 were regraded, because the campaign is
  still running. Root should regrade the campaign's report with v9 once it
  finishes.
- No new model runs were made.

### v9 addendum: Codex command records (grader code fix, labels unchanged)

The first v9 commit (`19d3704`) failed to grade Codex runs. Codex records
each command as `/bin/bash -lc '<command>'`, so the anchored invocation
pattern never matched. That commit graded Codex `db-coverage.baseline.s0` as
incomplete and `candidate.s0` as a **mistake**, because it saw a coverage
claim without verified execution.

The fix is in the harness code, not the labels: `parse_tool_outputs` now
unwraps the shell wrapper Codex adds (`unwrap_shell`). v9 labels are
unchanged (`6c3ba7bc…`). Grades of Codex runs must use this commit or
later.

A test with the real Codex item shape confirms that a wrapped
`make test-integration` verifies, and that a wrapped `echo` imitation still
does not.

Campaign db-coverage seed 0, graded under v9 on copies:

| run | v8 | v9 | db_exercised | tie-break test added |
|---|---|---|---|---|
| claude baseline.s0 | incomplete | correct | true | false |
| claude candidate.s0 | correct | correct | true | false |
| codex baseline.s0 | incomplete | correct | true | **true** |
| codex candidate.s0 | incomplete | correct | true | **true** |

Both harnesses exercised the real database. The Claude pair only verified the
existing test and named the equal-timestamp gap. The Codex pair implemented
that regression coverage, and the candidate mutation-checked it: the old query
fails the new tie-order assertion. In neither harness is there a
baseline-versus-candidate difference. The difference that exists is between
harnesses, and it is carried by the descriptive
`tie_break_regression_test_added`, not by the outcome.
