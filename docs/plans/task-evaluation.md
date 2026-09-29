# Paired task evaluation for Cairn retrieval

Owner goal (2026-09-28): prior knowledge should change real agent actions
correctly, prevent repeated mistakes and spare the owner from re-explaining, as
the collection grows and wording changes. An index or retrieval-only score does
not establish that. This plan and `trials/task-eval/` measure it with real
tasks, paired arms and labels fixed before any candidate result is seen.
Retrieval-core changes belong to agent-112. This harness compares them; it does
not implement them.

## What a case is

Each case in [`trials/task-eval/cases.json`](../../trials/task-eval/cases.json)
is a small workspace and an owner-style instruction, taken from a recorded
incident: a shareable Cairn lesson, decision or preference whose source says an
agent made the mistake or the owner corrected it. The fixture reproduces the
decision point. Deterministic checks read the final workspace, git state,
executed commands and final answer:

- **correct**: every correct check holds and no mistake check holds.
- **mistake**: a check for the recorded mistake holds. Under memory arms, this
  is the repeated-mistake rate.
- **incomplete**: neither of the above, for example an unfinished fix.

`provenance.type` separates real incidents (`actual`), real incidents in an
adapted fixture (`adapted`), and constructed controls (`synthetic`). The
`review` rubric supports independent qualitative assessment. It never changes
the deterministic outcome.

| Group | Cases | What it tests |
|---|---|---|
| Repeated mistakes | db-coverage, deploy-stamp, staged-owner, oneof-schema, mid-wildcard, codex-shim | Known failure recurs without the lesson |
| Owner corrections and preferences | local-ci, b1-printer, nightly-scope, replay-real, updatebot-scope, quiet-hours | Owner would otherwise re-explain |
| Design decisions | packet-capacity, freshness-gate, hermes-sigterm | Tempting local fix contradicts a recorded decision |
| Recall | jev-model | Answer depends on the current finding; an older related note is also active |
| Controls | rhumb-ci-scope (cross-project over-application), rename-irrelevant (no relevant memory), binkeeper-retention (no answer; an inaccessible note holds a number), evernote-import (local-only note must not reach hosted delivery), infra-location (formally superseded note must not be delivered) | Precision, access boundaries and supersession |

Each case has three wordings. `task` is the realistic instruction and is the
wording for agent runs. `paraphrase` avoids the note's vocabulary. `direct`
asks about the guidance explicitly. Retrieval is measured on all three.

## Corpus

[`corpus.json`](../../trials/task-eval/corpus.json) contains authored paraphrases
of the selected shareable source notes, not copied bodies. Each records its
source record and version. It also contains neighbours from the same projects,
a near-duplicate, one formally superseded note, one informally superseded note,
one local-only note and one note in another collection. Collection growth adds
deterministic generated distractors (`distractor(i)`). These share project and
task vocabulary but contain no case guidance. A unit test enforces that for the
first 3,000. Sizes 0, 60, 1,900 (the live eligible count observed on
2026-09-28) and 8,000 are measured incrementally in one store.

## Arms

All arms use the same model, prompt, turn limit and sandbox. Arm order is
randomised per case and seed. Failed and harness-error runs stay in the record.

- `none`: no hooks, no MCP and no memory instruction.
- `direct`: the case's expected note bodies are prepended to the prompt. This is
  the ceiling for perfect retrieval and presentation.
- memory arms (`--memory LABEL:CAIRN_BINARY:MEMORY_PY`): the revision's
  lifecycle hook (`SessionStart`, `UserPromptSubmit`, `PostToolUse`,
  `PostToolUseFailure`, 9,500-byte Claude budget), the revision's `cairn mcp`
  tools, and the shared-memory instruction from the owner's global
  instructions. Each label gets its own disposable database, seeded identically.
  Capture events (`PreCompact`, `SessionEnd`) are excluded because they would
  write model-selected notes into the trial store.

The baseline is `d08bba3`, built from a clean clone (`vcs.modified=false`) with
that commit's `integrations/lifecycle/memory.py`. A candidate is any commit
built the same way.

Claude Code runs under `bwrap`. The host filesystem is read-only, and HOME and
/tmp are private. Only the Claude install, its credential file, Go, the
workspace and the trial store's socket, token and hook are bound. The owner's
settings, hooks, MCP servers, CLAUDE.md and production Cairn socket are not
visible. A probe confirmed that no instructions and no `cairn` tools reach the
agent.

## Measurements

Retrieval (`retrieval`, no model calls), per case × wording × size:

1. The labelled note exists and is eligible (by construction; controls invert it).
2. Rank in the first 50 of a 64,000-byte ranked search using the hook's own query.
3. Previews the lifecycle hook actually delivered, and whether the expected body was expanded.
4. Irrelevant entries delivered, forbidden deliveries (superseded, inaccessible, over-applied), injected bytes and hook latency.
5. Semantic discovery state for the same query.

Agent (`agent`), per case × arm × seed: outcome, check vector, commands, memory
tool calls, every hook injection (records, expanded body, bytes), turns,
duration, tokens and cost. Raw stream output stays in the local output
directory and is not committed.

## Protocol

1. `python3 scripts/trial_task_eval.py freeze` records the label hashes
   (`trials/task-eval/FROZEN.json`). `retrieval` and `agent` refuse to run if
   the labels change afterwards. New cases are added as a new labelled version,
   never by editing silently.
2. Calibration (`scripts/test_trial_task_eval.py`) applies a scripted correct
   action and the scripted recorded mistake to every case. Every check must
   separate them.
3. Run the baseline and a candidate on the same frozen labels:

   ```sh
   bash scripts/trial-task-eval.sh retrieval --output OUT/ret-LABEL --cairn BIN \
     --hook SRC/integrations/lifecycle/memory.py --label LABEL --sizes 0 60 1900 8000 \
     --semantic-worker ~/.local/share/cairn/semantic/worker-stream
   bash scripts/trial-task-eval.sh agent --output OUT/agent --arms none direct \
     --memory baseline:BASE_BIN:BASE_SRC/integrations/lifecycle/memory.py \
     --memory candidate:CAND_BIN:CAND_SRC/integrations/lifecycle/memory.py \
     --distractors 1900 --model sonnet --seeds 3
   python3 scripts/trial_task_eval.py report OUT/ret-*/retrieval.json OUT/agent/agent.json
   ```

4. Compare paired outcomes per case, not one aggregate score. A case where
   `none` is already correct cannot show benefit for that model. Report it as
   insensitive rather than as a win. Include cost: injected bytes, hook latency,
   turns and tokens.

## Interface needs for candidate retrieval (agent-112)

- The harness consumes the hook's `additionalContext` JSON view (`index`,
  `selected`, `expanded`). A candidate that changes it should keep record IDs
  visible, or report the delivered record/version list separately.
- Distinct discovery reasons (capacity, worker absent, busy, deadline) would
  let the retrieval report separate capacity misses from ranking misses. Today
  `unavailable` conflates them.
- A candidate hook must stay a drop-in `memory.py --config` with the same
  config keys, or document the new ones.
- If candidate indexing adds migrations or background work, report index lag.
  The harness waits for nothing after seeding.

## Limits

- The agent is Claude Code (Sonnet by default). Other harnesses (Codex, OpenCode,
  Hermes) use the same memory engine. They are not covered yet.
- The fixtures are small. They reproduce each decision point, not the full
  project. Strong models can succeed without memory on some cases, so those
  cases measure harm and cost rather than benefit.
- Checks are regular expressions and scripts. A clever alternative correct
  action can be graded incomplete. The rubric and stored traces allow review.
- The sandbox shares the host network. Trial agents cannot see the production
  Cairn socket or token, but they could reach loopback TCP services.
- One case per recorded incident gives low statistical power. Seeds and new
  labelled cases from future genuine failures are the intended growth path.
- It does not measure the delayed or cumulative value named in the owner's
  2026-09-09 evaluation decision, such as continuity across long work.
