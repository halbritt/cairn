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
