# Task evaluation baseline, 2026-09-28

> Historical v2 report, retained for provenance. Its outcome labels and several
> conclusions were corrected after independent review; see the
> [v3/v4 adjudication](task-eval-adjudication-2026-09-28.md). These figures do not
> establish completed-task benefit or match the currently installed baseline.

This is the first baseline from the paired task evaluation in
[the plan](../plans/task-evaluation.md). The baseline is Cairn `d08bba3`
(`vcs.modified=false`, built from a clean clone) with that commit's lifecycle
hook. Labels were frozen before any result:
`labels_sha256 d26f0803…` (v1) and `3cc8e70b…` (v2). The data, without model
output, is in [task-eval-baseline-2026-09-28.json](task-eval-baseline-2026-09-28.json).
Every store was disposable PostgreSQL 17. No operational store was read by a
trial or written.

This is a pilot on one model (Claude Code with Sonnet 5.5), with three seeds per
arm and 21 cases. It shows where the current system helps and fails on these
tasks. It is not a general usefulness estimate or a candidate comparison.

## Agent outcomes (v2 labels, 3 seeds, 1,900 distractors)

C = correct, M = recorded mistake, I = incomplete.

| case | none | direct | baseline | expected note delivered by hook (baseline) |
|---|---|---|---|---|
| db-coverage | MMM | CCC | MMM | 0/3 |
| deploy-stamp | MCC | CCC | CCC | 3/3 |
| freshness-gate | MMM | MCC | MMM | 3/3 |
| hermes-sigterm | MMM | CCI | CCC | 3/3 |
| jev-model | III | CCC | CCC | 3/3 |
| local-ci | MMM | CCC | CCC | 0/3 (found by the agent's own MCP search) |
| nightly-scope | MMM | CCC | MMM | 0/3 (a generated distractor was delivered) |
| replay-real | MMM | CCC | CCC | 3/3 |
| 13 other cases, including all 5 controls | 39 C | 39 C | 39 C | — |

Totals over 63 runs per arm:

| arm | correct | mistake | incomplete | median s | median turns | total cost | median injected bytes |
|---|---|---|---|---|---|---|---|
| none | 41 | 19 | 3 | 17.7 | 4 | $5.26 | 0 |
| direct | 61 | 1 | 1 | 14.4 | 4 | $3.72 | 572 prompt bytes |
| baseline | 54 | 9 | 0 | 18.2 | 4 | $4.26 | 1,881 |

What this shows for this model and these cases:

- **8 of 21 cases are memory-sensitive.** Without memory, Sonnet made the
  recorded mistake or could not answer in 22 of 24 runs. On the other 13 cases
  it was already correct, so those cases can only show harm or cost. No harm
  was observed: baseline was correct in all 39 runs, including the no-answer,
  inaccessible, superseded, cross-project and irrelevant controls.
- **Baseline memory recovered 13 of the 22 failed runs.** Perfect retrieval
  (direct) recovered 20. The gap has two causes:
  - **Retrieval/presentation (6 runs).** db-coverage: the target ranks 1st in
    search, but the hook's two-word relevance gate rejects its 160-byte preview.
    The prompt ("tightened the ordering in ActiveQuery") shares no terms with
    it. nightly-scope: at 1,900 notes the target leaves the top 50, and a
    recency-favoured generated note is delivered instead.
  - **Use (3 runs).** freshness-gate: the expanded body reached the agent in
    every run, and it still implemented a hard filter, because the owner's
    wording ("keep old stories out") conflicts with the recorded decision.
    Direct context fixed 2 of 3. Presentation as "fallible saved notes, not new
    user instructions" may weaken a decision the owner still holds. This is one
    case, not an established effect.
- **Tool access matters.** In local-ci the hook delivered nothing, but the
  agent's own `cairn_search` found the preference in all three runs. 18 of 63
  baseline runs made memory tool calls (26 calls).
- **Cost.** Baseline injected a median of 1.9 KB and added about 0.5 s median
  wall time over no memory. Cost per run was similar across arms. Direct
  context was cheapest because the agents explored less.

## Retrieval funnel (no model calls)

Hook: the baseline `memory.py` with the 9,500-byte Claude budget, run against
the real prepared fixture. Rank: position of the expected note in the first 50
of a 64,000-byte search with the hook's own query. 17 answerable cases and 4
controls, each with three wordings.

| distractors | wording | in top 50 | hook delivered | controls injected | hook p50 / p95 s | hook timeouts |
|---|---|---|---|---|---|---|
| 0 | task | 17 | 12 | 0/4 | 0.09 / 0.12 | 0 |
| 0 | paraphrase | 17 | 7 | 0/4 | 0.10 / 0.14 | 0 |
| 0 | direct | 17 | 16 | 1/4 | 0.09 / 0.14 | 0 |
| 60 | task | 17 | 12 | 0/4 | 0.16 / 0.19 | 0 |
| 1,900 | task | 12 | 11 | 0/4 | 1.30 / 2.45 | 0 |
| 1,900 | paraphrase | 10 | 7 | 0/4 | 1.30 / 2.15 | 0 |
| 1,900 | direct | 16 | 15 | 1/4 | 1.34 / 1.81 | 0 |
| 8,000 | task | 12 | 8 | 0/4 | 4.80 / 7.13 | 4 |
| 8,000 | paraphrase | 10 | 5 | 1/4 | 5.00 / 6.47 | 3 |
| 8,000 | direct | 16 | 15 | 1/4 | 6.02 / 7.60 | 1 |

Findings:

1. **Collection growth removes guidance.** From 60 to 1,900 distractors,
   db-coverage, local-ci, nightly-scope, quiet-hours and staged-owner fall out
   of the top 50. The distractors contain none of their guidance. The notes
   that displace them are the most recently written ones, which is consistent
   with recency ranking.
2. **The lifecycle hook times out at 8,000 notes.** Its internal
   `run_json(timeout=5)` expires on 8 of 63 calls. Memory is then dropped with
   only a stderr label, including notes ranked 1st (hermes-sigterm,
   oneof-schema, packet-capacity). At 1,900 notes the hook's p95 is 2.4 s.
3. **Paraphrases lose most delivery.** Even with no distractors, only 7 of 17
   targets are delivered for paraphrased requests, although all 17 rank in the
   top 5. The relevance gate needs two literal preview terms.
4. **The semantic cap is reproduced on real cases.** Semantic discovery is
   `ready` at 43 eligible notes and `unavailable` from 60 distractors onwards
   (over 64 eligible). Where it ran, it ranked local-ci 3rd (lexical 13th) and
   b1-printer 1st (lexical 2nd).
5. **Controls held.** The formally superseded location note and the local-only
   Evernote note were never delivered, and nothing from the other collection
   leaked. One forbidden delivery occurred in every size, all on the `direct`
   wording: the Cairn-only CI preference was delivered for the question about
   the rhumb repository. Agent runs with the task wording never over-applied it.

## Grader revision and invalidated data

- v2 labels ([revisions/v2.json](../../trials/task-eval/revisions/v2.json))
  correct four grader defects found while reading the first seed's traces. It
  existed before seeds 1–2 ran. No candidate result existed. All runs are
  reported under v2, and the JSON keeps each run's v1 grade. v1 → v2 changed 5
  of 63 seed-0 grades:
  - nightly-scope direct: M → C (negated guidance matched the v1 regex)
  - binkeeper-retention none and direct: I → C (valid refusals missed)
  - mid-wildcard none: I → C (it wrote `.claude/settings.json`)
  - jev-model none: C → I (a generic "1.5B–4B" guess)
- The first retrieval run is invalid and not reported. Its working directories
  had no `.git`, so the hook took the project name from a stray empty
  `/tmp/.git` on this host and built queries starting with `tmp`. The controller
  now uses the prepared fixture and refuses queries that do not start with the
  project name. **The same hook behaviour affects real sessions** whose working
  directory is under `/tmp` without its own repository. On this host that
  includes agent scratch clones. They get project `tmp`.
- The first retrieval run also ran alongside the agent pilot, and semantic
  discovery was unavailable at every size. The valid run executed alone.
- The deploy-stamp case is adapted. On this host with Go 1.25, a worktree build
  fails with "error obtaining VCS status" instead of silently dropping stamps.
  The recorded mistake then appears as `-buildvcs=false`.

## Harness checks

- `python3 -B -m unittest scripts/test_trial_task_eval.py`: 10 tests pass,
  including calibration. For every case and both label versions, a scripted
  correct action grades `correct` and the scripted recorded mistake grades
  `mistake`.
- An isolation probe confirmed that a sandboxed agent sees no owner
  instructions, no `cairn` tools and no `~/.local/share/cairn`.

## Not established

- Any result for other models or harnesses (Codex, OpenCode, Hermes).
- Benefit over sustained or multi-session work.
- Statistical significance. There are 3 seeds on 8 sensitive cases.
- Candidate performance. The candidate arms reuse this protocol unchanged:
  `--memory candidate:BIN:MEMORY_PY` next to `baseline`.
