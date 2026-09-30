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

Within a provider, all arms use the same model, prompt, limits and sandbox. Arm order is
randomised per case and seed. Failed and harness-error runs stay in the record.

- `none`: no hooks, no MCP and no memory instruction.
- `direct`: the case's expected note bodies are prepended to the prompt. This is
  an oracle-note intervention, not an established ceiling on task completion.
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

Providers run under `bwrap` with an explicit runtime filesystem. System
binaries/libraries, Git templates, CA certificates and resolver files are
read-only. HOME and /tmp are private. The chosen provider executable/install,
its credential file, Go, the workspace and the trial store's socket, token and
hook are bound separately. Host application directories, service configuration,
service socket directories and sysfs are absent. Claude receives its resolved
executable rather than the host's entire `~/.local/bin` directory.

Shell graders use the same runtime filesystem, a selected environment, their
fixture workspace and grader sources, with networking disabled. They execute
agent-written code and therefore require the same filesystem restrictions.

### Native Codex

`agent --harness codex --model MODEL` runs the npm entry point directly, so the
host's coordination launcher cannot register a trial agent or deliver production
inbox work. `--codex-install` names the directory containing `codex/bin/codex.js`
and its platform packages. `--codex-auth-file` supplies existing account auth,
mounted read-only into a fresh profile. Neither the host configuration nor its
rules, hooks, skills, history or production MCP configuration is mounted.

Codex memory arms use native `SessionStart` and `UserPromptSubmit` recall hooks
with the installer's 12,000-byte context limit, a trial-only MCP server and the
same shared-memory instruction. Only these reviewed fixture hooks receive the
invocation's trust bypass. None/direct arms have no hooks or MCP. Capture is
disabled in every arm.

The evaluator reads native JSON completion events for commands, file changes,
Cairn tool calls, answers and usage. Codex's turn count describes exec turns;
it is not comparable to Claude's `num_turns`. Codex does not expose the same
turn limit: `--max-turns` is rejected for this provider and `--timeout` bounds
the process. Reasoning effort is explicit (`--reasoning-effort`, default high).
Plans retain provider, version, evaluator hash, model, effort and limits. A
paired plan with another provider or effort is rejected.

For example, after choosing an available model:

```sh
python3 scripts/trial_task_eval.py agent --legacy-fixtures --harness codex --model MODEL \
  --output OUT/codex --arms none direct --cases local-ci --parallel 1
```

Memory arms still require the disposable-cluster wrapper shown below. The npm
installation and auth paths are host-specific defaults; they can be overridden.
Use `--semantic-recall LABEL` for each memory arm that should run the semantic
recall/applicability route. Its default is off, matching the frozen Claude
baseline. This setting must match the intended adapter configuration: passing a
different `memory.py` alone does not change it. `--selector-model` selects the
tool-free Claude applicability model. `--selector-binary` supplies the reviewed
CLI; Codex mounts that resolved executable and `--selector-auth-file` without
the host's Claude settings or hooks. Plans retain each arm's recall mode and
selector model plus selector version and binary hash when enabled. This allows
comparison of the complete recall configuration rather than an accidental
disabled-selector run. Selector failures remain visible through hook outcomes;
they do not mean that the task provider itself failed.

Provider networking remains shared with the host for model access. This is not
complete host-service isolation: loopback/private TCP services and abstract Unix
sockets are not excluded by the filesystem mounts. Fixtures must remain reviewed,
nonsensitive tasks. Historical runs made with the earlier read-only host-root
mount do not gain these restrictions retroactively.

Nonzero process exits, provider errors, timeouts and unterminated streams are
reported as `harness_error`, even when partial answers satisfy a check. Their
check results remain as `check_outcome` for diagnosis. Existing reports are
unchanged; older reports can undercount provider failures. Raw output remains
local. Hook injection metrics exclude manually pulled MCP result bytes.
Command/tool lists describe observed attempts; a command appearing there does
not establish its success, test coverage or successful memory delivery. Task
checks and independent review must establish those claims.

For retained runs, the [delivery evidence reader](../verification/task-delivery-evidence.md)
correlates native MCP results and separates previews, bodies, excerpts, failed
calls and incomplete calls. Its output supplements hook metrics without
changing the original grades.

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
   bash scripts/trial-task-eval.sh agent --legacy-fixtures --output OUT/agent --arms none direct \
     --memory baseline:BASE_BIN:BASE_SRC/integrations/lifecycle/memory.py \
     --memory candidate:CAND_BIN:CAND_SRC/integrations/lifecycle/memory.py \
     --distractors 1900 --model sonnet --seeds 3
   python3 scripts/trial_task_eval.py report OUT/ret-*/retrieval.json OUT/agent/agent.json
   ```

4. Compare paired outcomes per case, not one aggregate score. A case where
   `none` is already correct cannot show benefit for that model. Report it as
   insensitive rather than as a win. Include cost: injected bytes, hook latency,
   turns and tokens.

Other local evaluators can reuse the same disposable PostgreSQL lifecycle with
`bash scripts/trial-task-eval.sh -- COMMAND [ARG ...]`. The command runs from
the Cairn checkout root and receives `CAIRN_TASK_EVAL_PG` (the private socket
directory) and `CAIRN_TASK_EVAL_PG_BIN` (the PostgreSQL tools directory). Its
arguments and exit status are preserved, including when PostgreSQL has already
crashed. A cleanup failure after an otherwise successful command returns nonzero;
if PostgreSQL remains alive or `pg_ctl status` cannot establish whether it is
running, its directory is retained and the error names it. Only status 3 means
not running; status 4 (inaccessible data) and other command failures do not
authorize removing the directory.
When cleanup succeeds, the wrapper removes its cluster. Without a leading `--`,
the native evaluator remains the default. This lets CAPLAB supply its own corpus
and measurement procedure while reusing the existing cluster lifecycle.

For cancellation, start the wrapper in a new process group and send SIGTERM to
that whole group, including the command. Signalling only the wrapper PID can
leave its child running. Give the EXIT cleanup time to finish; SIGKILL cannot
run cleanup. CAPLAB uses a new session and group signalling for this reason.

## Interface needs for candidate retrieval (agent-112)

- The harness consumes the hook's `additionalContext` JSON view (`index`,
  `selected`, `expanded`). A candidate that changes it should keep record IDs
  visible, or report the delivered record/version list separately.
- Distinct discovery reasons (capacity, worker absent, busy, deadline) would
  let the retrieval report separate capacity misses from ranking misses. Today
  `unavailable` conflates them.
- A candidate hook must stay a drop-in `memory.py --config` with the same
  config keys, or document the new ones.
- Persistent candidates selected with `--embedding-worker` must reach full
  eligible coverage before agent runs; reports retain cold indexing duration.
  `agent --cold-readiness-timeout SECONDS` selects a positive setup wait
  (default1800), independently of the task's `--timeout`. Both `plan.json`
  and `agent.json` retain `cold_readiness_timeout_seconds`. The full-coverage
  predicate is unchanged; searches already in flight can finish after the wait
  deadline. A setup timeout occurs before any task admission. Preserve that
  failed output and verify cleanup before a separately recorded fresh setup;
  changing this limit does not authorize replaying an admitted task.

## Limits

### Hook measurements

New task runs use `scripts/trial_hook_observer.py` around the pinned engine.
The observer keeps hook stdout unchanged and records metadata for each completed
invocation in `hook-state/observations.jsonl`. It snapshots a recall's status
while the engine still holds the session lock. Startup measurements therefore
survive a later prompt recall, and tool hooks do not repeat old recall costs.
The report records the observer's source hash and separates invocation timing
from model-reported timing and cost. No prompt, note body, token or transcript
is written to this metadata log.

`process_seconds` starts after argument parsing and ends before the observation
file is locked and written. It includes engine loading but excludes final logging
overhead; it is not the complete native hook wall time. `recall_seconds` measures
the wrapped recall call before the metadata snapshot is copied.

`memory.recall_observation` is `per_invocation` for these records. Reports from
older runs are explicitly labelled `last_state_only`; their overwritten startup
measurements cannot be recovered. Missing timeout costs remain absent, not zero.
A failed invocation may have no fresh recall metrics. Killing the process before
its observer finalizer runs can also lose a measurement, so these records do not
establish complete provider billing. Invalid JSON, unsupported schemas and invalid
recall payload shapes fail analysis instead of being silently discarded.
Freeze the observer together with the
evaluator for each comparison; existing campaigns are not changed retroactively.

### Evaluation scope

- Claude Code (Sonnet by default) and native Codex are supported. A passing
  provider smoke test establishes harness operation, not retrieval usefulness.
  OpenCode and Hermes task outcomes are not covered.
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

### Stop admitting tasks after confirmed provider failure

The evaluator keeps at most `--parallel` tasks admitted at a time. A confirmed
provider authentication or account-cap failure stops replacement admissions;
already admitted tasks finish and retain their own outcomes. It does not retry,
restart, cancel in-flight work, or create replacement attempts.

The current classifier recognizes Claude's terminal `is_error=true`,
`terminal_reason=api_error`, HTTP401 authentication failure. For HTTP429 it also
requires the native assistant `error=rate_limit`, the same terminal message,
and the explicit weekly-limit/reset format observed in the retained incident.
A bare429, retry event, quoted/tool-output error, ordinary task failure, or
success after a transient failure does not stop admission. Other provider error
shapes, including Codex errors, remain recorded execution failures without an
inferred account-wide stop. Extending classification requires native evidence
and tests; reset text never schedules a retry.

On a confirmed stop, stdout emits `admission_stop`. After admitted work drains,
`agent.json` retains the usual actual-attempt records and summary, plus an
`admission` section containing the stop reason, planned/admitted counts and
`not_started` plan rows. Unstarted tasks are not fake failed attempts and are not
included in outcome or latency denominators. The evaluator exits2, including
when the failure happened on the last admitted task. Existing per-attempt failure
grades and costs are preserved. A later grading exception still permits the
controller to inspect the already retained native stream for the stop signal;
unreadable or malformed evidence is reported, not treated as a confirmed cap.
The complete pre-run plan remains unchanged. Reports describe a partial campaign,
not a completed paired comparison. Store cleanup retains its existing owner.

## Prospective native input (CAIRN-120)

New `agent` executions must choose `--prospective-input DIR` or explicitly
`--legacy-fixtures`. The latter preserves the historical reader/executor path;
it does **not** authorize replaying a closed campaign. Frozen historical files
and reports are unchanged.

The prospective path reuses `run_agent`, `TrialStore`, the existing disposable
PostgreSQL wrapper, graders and `cairn.task-eval.agent/1` reports. It currently
supports one ordinary Claude prompt per case. It does not resume conversations,
publish inbox work or invent an admitted delivery. Codex, interactive correction
turns, automatic hosted selector arms and unknown input fields are refused.
Include **all already-known issue corrections** in the exact `wordings.task`;
no summarizer shortens it. This integration is not task-benefit evidence.

### Freeze new inputs

A reviewed directory contains `input.json`, `corpus.json` and
`workspaces/NAME/setup.sh` plus source files. `input.json` uses schema
`cairn.task-eval.input/1` with:

- `baseline_commit`: full baseline API revision, distinct from the workspace
  revision. A memory arm named `baseline` must have this API revision.
- `native`: `binary: {path, sha256}`, exact `model`, and explicit `effort`.
- `arms`: ordered names, and `memory_arms`: configuration for every name except
  explicitly chosen `none`/`direct`. These controls never replace the original
  baseline by inference. Each memory arm supplies `binary` and `hook` descriptors
  (`{path, sha256}`), `api_revision`, `recall_revision`, `recall_mode` (`ambient`
  or `agent_tools`), and boolean `semantic_fallback`. `agent_tools` also requires
  a pinned `bridge` descriptor. Semantic discovery requires a pinned
  `embedding_worker` launcher; its adjacent dependencies/models remain an
  operator verification obligation. Binary identity must report the declared
  revision and an unmodified build. Recall revision is provenance; its actual
  bytes are checked by the hook digest.
- `corpus_policy`: an object with a nonempty `description` plus chosen selection,
  eligibility and provenance details. No note-kind filter is inferred. This is
  where the prospective natural-cohort rule belongs, before reading outcomes.
- `cases`: existing case/check vocabulary, but exactly one `wordings.task` and
  a nonempty `preflight` list of argv lists. `workspace`, `copy_to` and `cwd` are
  contained relative paths; symlinks are refused. Optional `workspace_commit`
  is checked after setup. Otherwise the manifest attests source bytes, not a
  claimed repository HEAD. Setup/grader/preflight commands are trusted operator
  code, never untrusted agent output. `category`, `provenance`, `expected`,
  `correct` and `mistake` retain their existing meanings; empty expected guidance
  is legitimate and is not a positive task-benefit claim.

`corpus.json` is `{ "notes": [...] }`. Every note has `id`, `body`, `kind`;
optional fields are `shareable`, `repo`, `supersede_with` and `provenance`.
Lessons and other supported ordinary-note kinds are accepted without filtering.
This representation creates fresh A records/version1 and keeps an import map;
it does not recreate historical authority. Unsupported pins, entities, citations
or authority fields are refused, not silently flattened. Declare exclusions in
corpus policy; do not add expected-ID notes or refill after seeing rankings.

```sh
python3 scripts/trial_task_eval.py freeze-input --input /absolute/fresh-input
scripts/trial-task-eval.sh agent --prospective-input /absolute/fresh-input \
  --output /absolute/new-output --model EXACT_MODEL --reasoning-effort high \
  --distractors 0 --parallel 1
```

Freezing is exclusive and makes no provider call. Execution verifies every input
file and component, snapshots the bundle into the new output directory, and
uses no historical overlays or generated distractors. Inputs with the selected
model/effort differing from the frozen native contract refuse before launch.
All arms seed the same supplied cohort serially. Per-case prerequisites run in
the task runtime before its provider launch; a Go module additionally gets
`go version` and `go list ./...` with `GOTOOLCHAIN=local`. Require task-specific
build/dependency checks in `preflight`: the tiny capability smoke does not prove
that an arbitrary repository builds. Failed prerequisites remain recorded and
are never replaced by successful model assertions. Python tasks can use the
existing `python3 -m pytest` and `python3 -m ruff` permission route, with explicit
interpreter/module/version checks in `preflight`. Bare `pytest`, `ruff` and
virtual-environment executable paths are not in the native allowlist; do not
assume a successful out-of-model prerequisite grants them native permission.

### Permissions and measurements

Prospective Claude uses the reviewed explicit local toolchain/read/edit allowlist,
`--permission-prompts none`, empty inherited setting sources, disabled auto-memory,
no production MCP and no capture hooks. Account auth is mounted read-only. Native
safety checks remain active; a broad allowed Python command is **not** a hostile
code sandbox. Provider networking has the existing sandbox's documented limits.
Native denial metadata retains fixed categories, counts and validated tool IDs,
not arbitrary command/error strings. Pending/background Bash acknowledgments
cannot satisfy successful `tool_output` checks. Fixed authentication/quota stop
metadata is saved before grading, so a grading exception cannot admit more tasks
after a confirmed provider stop. This record contains no provider error text.

Ambient baseline configuration remains explicit. For a supported current
`agent_tools` arm the existing ordinary Claude bound-memory path gets an owned,
pinned, otherwise unused coordinator descriptor. No coordinator hook or durable
inbox admission is fabricated. This is ordinary task execution, not an inbox
bridge effectiveness test.

The prospective measurement contract is fixed at 9,500 selected input bytes,
5 seconds per hook, 30 seconds from native process start to its last native Cairn
result, two native searches and four actual body-inspection calls including
hook pulls. Hook searches are recorded separately. Positive-version history
reads count as body inspection; metadata history does not. Error envelopes,
repeated results and required context after compaction count again. Initial
hook room subtracts the full prompt envelope, common memory instruction and a
512-byte framing reserve. No allowance is inferred from model context capacity.

The observer consumes the stream in memory, retaining selected timings/digests,
permission metadata, hook wire counts, technical grades and task artifacts.
No prospective raw native stream/reasoning or raw stderr is retained. Native
hook start/response metadata must match per-invocation observations, including
later compact/resume callbacks; missing, malformed or unmatched evidence is
unknown, never zero. Native reported model is checked against the frozen model.
These selected counts are conservative observable context accounting, **not**
provider-wire attestation or the full model window (system/tool schemas and
ordinary source exploration remain outside this selected task/memory measure).
A byte/time/call/permission failure remains separate from task correctness: the
engineering task may finish, but the report's measurement fails. Unknown or
failed measurement returns nonzero. No-memory-attempt is explicit, not successful
retrieval or proof of usefulness.

System/status string messages are not assistant/user content. Malformed relevant
message shapes make observation unknown. If selected-input measurement or origin
binding raises, the prospective report records a fixed phase and exception
category, with unavailable counters null; it retains no exception text or raw
payload. Technical grading can still complete independently. Supplemental hook
summary failures also remain explicit. Missing measurements cannot be reconstructed
from these error records, and the failed original run is not retrospectively healed.

Only executor-created disposable stores are fingerprinted. The semantic/content
projection excludes `memory_record.use_generation`, while a separate operational
hash retains its changes; versions, bodies, applicability, entities, lifecycle,
sensitivity and relations remain covered. Unreviewed memory-record columns
refuse. This is a five-table corpus check, not a complete DB/security snapshot or
proof that no intermediate mutation occurred. Store routing never falls back to
production. First failures and before/after stamps remain evidence; no reseeding
or retrospective repair of older invalidated reports occurs.

CAPLAB `retrieval import-task` continues to accept the unchanged report schema,
original plan and frozen `output/input/corpus.json`. It retains original grades
and memory measurements. Because no raw prospective stream exists, its
stream-derived delivery is **missing/unknown**, not observed zero; parser replay
cannot manufacture that evidence. Historical reports/streams remain readable.

### Selected source-delivery evidence

Prospective reports retain `cairn.source-delivery/1` metadata extracted from actual
native Cairn tool results and successful hook stdout. State `seen` entries,
inspected candidates and API call counts do not establish delivery. The observer
retains fixed record IDs/versions, preview versus whole-body versus partial-span
extent, actual displayed-byte SHA256/length, declared full-source SHA256 where
available, and span offsets. Whole-record bodies have no advertised digest;
the observer computes UTF-8 bytes only for that known schema and checks them
against the frozen import provenance. Span bytes must match their declared digest
and the exact frozen source slice. Summaries may contain omission marks, so their
location is a hint, not proof of a canonical source slice. History metadata does
not count as a body delivery; historical bodies remain explicitly historical.

References are mapped to frozen input IDs with the origin-map digest. No raw
body, summary, query, arbitrary error string or reasoning is retained. Unknown
payloads/encodings/hash mismatches remain unknown, never stringified as source
text. Evidence-object pulls are currently unknown source representations rather
than guessed note identities. Parsing is capped at1MiB/128items per payload and
128delivery events/512items per run; hitting the cap marks missing evidence.
Selected measurements are saved before task grading and survive grader errors.
This is exposure evidence, not applicability, actual model use or currentness
attestation; a reviewer must inspect the frozen source and action consequence.

Natural prospective cases default to `relevance_status: unknown_unlabelled`:
empty `expected` does not label every returned note irrelevant. Such records omit
`irrelevant` and `delivered_expected`. Authors may explicitly set the boolean
`relevance_labels_complete: true` only for prospectively exhaustive labels;
otherwise relevance remains unclassified. Explicit forbidden-reference labels
still report matching exposure. Historical labelled behavior and the CAPLAB
report/import schema remain unchanged; selected metadata resides under the
existing original-memory envelope.

The explicit prospective `direct` control inserts notes in the task prompt rather
than these observed source channels. If used with notes, its source-delivery
measurement is unknown; an empty reference list must not be called zero exposure.
Historical direct-control reports are unchanged.

### One prospective task, one optional grant

A prospective current `agent_tools` arm now requires a pinned engine declaring
`RECALL_TASK_VERSION = 1`. Before launching its single native child, the launcher
creates one UUID `recall_task_key` in the bound hook configuration and validates
that contract. Native prompt IDs may change without issuing another optional
grant. A fresh separately admitted task gets a fresh key; changing the key during
a child is unsupported. Original ambient baseline components retain their pinned
behavior. Older current-arm engines without this contract refuse before launch;
this is an explicit component compatibility change, not a relabelled old result.

The hook observer records only a bounded prompt-ID digest, an allowlisted native
origin (or absent/unknown), and exact equality with the frozen submitted prompt.
Selected native text-input metadata has the same fields plus boolean synthetic
and replay flags and a bounded parent-tool ID digest, capped at 32 events with an
omission count. Missing provenance does not prove an owner prompt. No prompt text
is retained by these additions, and these fields confer no admission authority.
Hook output/error bytes, required refreshes and subsequent tool results still
count toward aggregate measurement. One optional grant prevents repeated prompt
boundaries from renewing that allowance; it does not host-enforce all tool calls
or make a run within budget by construction. Earlier failed runs remain unchanged.

The observer counts hook stdout and forwarded engine stderr bytes separately,
without retaining stderr content or claiming its native consumption. Any hook missing that count remains unknown;
an observer exception or host timeout is not reconstructed as zero error output.
