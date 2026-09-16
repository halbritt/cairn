# Cairn

Cairn carries useful, scoped memory between agent runs. It stores ordinary notes,
evidence-backed claims, and authorized instructions in PostgreSQL, then compiles
a bounded context package before a task starts.

**Status: usable local alpha.** Remember/search, manual authority and evidence,
context compilation, process wrapping, historical recompilation, authenticated
local access, bounded index/pull, evidence-attached review, and outcome joins work.
A [real OpenCode repair trial](docs/verification/opencode-recurrence-2026-09-07.md)
compared three context conditions; none produced a repair within its budget.
A later [reviewed-lesson recurrence](docs/verification/reviewed-recurrence-2026-09-08.md)
found that H0 selected and cited a lesson while avoiding the baseline's environment
regression within budget. Full repair-task acceptance, runtime mediation and general
memory benefit remain unestablished.

A [native MCP configuration follow-up](docs/verification/mcp-host-use-2026-09-08.md)
retrieved a curated procedure revision and produced a configuration that connected
after an earlier run produced an unusable one. Both tasks have retrievals linked
to observed host runs and separate task assessments. This is one accepted
configuration task, with broader memory benefit still unproved.

The [current task-value inventory](docs/verification/usefulness-status-2026-09-08.md)
also includes a qualitative adapter-repair case and continuity through guidance
maintenance. Value can accumulate across turns and tasks; a controlled single-turn
comparison is not required for every useful observation. The reports preserve
failures, alternative explanations and unknown net costs.

[Claude Code setup](docs/claude-code.md) can generate the same ordinary MCP
interface with explicit task/run scope.
[Claude lifecycle hooks](docs/claude-lifecycle.md), the [OpenCode lifecycle plugin](docs/opencode-lifecycle.md), and the [Hermes CLI/gateway provider](docs/hermes-lifecycle.md) provide selective ambient recall, reusable notes and named workstream checkpoints. Unchanged dialogue skips capture calls. [Codex/Agy hook routes](docs/lifecycle-hook-assessment.md) are assessed separately. Broader task-value evidence remains open.

The [roadmap](docs/roadmap.md) tracks accepted requirements, remaining gaps and
acceptance evidence.

[Agent events](docs/agent-event-fabric.md) add durable direct inboxes and topic
notifications over PostgreSQL. Agents publish an exact note version, poll with
an expiring lease, and optionally save a result atomically with acknowledgment.
`cairn inbox next --profile worker-01` uses the provisioned `agent/worker-01` inbox;
`cairn publish --help` lists the commands. Profiles are explicit and polling does
not launch agents. The [wakeup supervisor](docs/agent-wakeups.md) can launch fresh
Codex, Claude Code, Agy, OpenCode and Hermes workers for request events, with one worker per inbox,
lease renewal and systemd process cleanup. See the
[event plan](docs/plans/agent-event-fabric.md), [wakeup plan](docs/plans/agent-wakeups.md),
and [coordination v1 plan](docs/plans/agent-coordination-v1.md). Seven worker slots
cover both Codex and both Claude accounts; live session discovery and routing by
project/harness metadata are planned.
The [changelog](CHANGELOG.md) summarizes development changes and upgrade notes;
the [implementation status](docs/implementation-status.md) records the current
installation and complete history.

Agents can [read byte spans of captured evidence](docs/index-and-pull.md#index-and-expansion-contract)
when the whole source exceeds their context budget. Spans retain the full source
identity and carry a separate checksum for the selected bytes.

Long saved notes also support [explicit body excerpts](docs/index-and-pull.md#index-and-expansion-contract)
through `cairn agent pull --offset N --length N` and native `cairn_pull` span
arguments. Instructions retain whole-body delivery.
Search previews now include source byte positions for A/B notes, so the same
excerpt tool can locate a buried passage without first fetching the whole body.
For earlier versions, [history excerpts](docs/record-history.md#read-a-passage-from-an-earlier-version)
read selected bytes without returning the complete old note.

[Governed policy](docs/governed-policy.md) lets an authorized operator revise
optional-memory budgets, restore earlier rules as a new revision and inspect runs
by the policy they used. Existing repositories retain their original policy until
an explicit revision is issued. [Instruction category limits](docs/instruction-limits.md)
also bound mandatory and optional C instruction load when explicitly adopted.

[Authenticated host runs](docs/authenticated-runner.md) use `cairn agent ... run`
with a scoped observer token instead of direct database access.

The same profile can [recompile its historical receipts](docs/currentness-and-replay.md#recompile-a-retained-read-set)
with `cairn agent ... recompile`. Inspection preserves the original read set and
seal while enforcing receipt ownership, current privacy and forgetting rules.

## Get started

On the owner's host, [shared memory is installed across projects](docs/shared-memory.md)
in Codex, OpenCode, Agy, Claude Code, and Hermes. Start a fresh session and use the Cairn
tools; no project-specific registration is needed.

Use `cairn version` to identify the CLI; authenticated `cairn agent ... version`
reports the CLI and running API separately. [Build diagnostics](docs/build-identity.md)
explain unknown stamps and compatibility limits.

Use `cairn mcp --help` or the [configuration command help](docs/mcp.md#command-help)
to inspect the installed flags before connecting a harness.

Requires Go 1.25+, PostgreSQL 16+ client/server binaries, Bash, Python 3.11+, and
`flock`. Run as an ordinary user. The local store uses its own private Unix
socket and opens no TCP listener.

```sh
make build
bash scripts/local-store.sh start
bin/cairn remember 'When changing Cairn storage, run make test-integration.'
bin/cairn search 'changing storage'
bin/cairn run --prompt 'Inspect the supplied note' -- /bin/cat
bin/cairn report "$PWD"
bin/cairn use-report "$PWD"
bin/cairn run-report "$PWD"
```

The example wraps `cat`, so you can inspect the exact input without a model or
provider credentials. The repository defaults to the current directory. Notes
created by `remember` apply to all tasks and runs in that repository; empty scope
bindings are never treated as wildcards. Optional [capture pins](docs/currentness-and-replay.md#saving-guidance-with-explicit-applicability)
narrow when a saved note applies.

The default database and run artifacts live under `~/.local/share/cairn`.
`CAIRN_HOME` changes that directory. `CAIRN_DATABASE_URL` selects an existing
**dedicated Cairn database** for the CLI; it does not redirect the local-store
script. Use `CAIRN_PG_BIN` to select PostgreSQL binaries when necessary.

[Selected evidence files](docs/evidence-refresh.md#capture-a-selected-file) can be
captured with `agent evidence --file PATH --source LABEL` without preparing JSON.
Capture remains local by default and preserves the selected bytes.

[Explicit file and symbol associations](docs/entity-search.md) support retrieval by
a chosen repository-relative file or qualified symbol, including when a note body
does not name it. Capture and search accept `--entity-file` / `--entity-symbol`
through the CLI and `entities` through ordinary native tools.

OpenCode can optionally [collect recent file hints](docs/recent-file-hints.md)
for subsequent searches with `opencode-install --recent-files`. The normal
tool permissions still govern retrieval.

[Quoted search](docs/quoted-search.md) prefers a known path, identifier or error
message in the existing query field while retaining broader lexical matches.

[Optional semantic discovery](docs/semantic-discovery.md) adds `--semantic` to
authenticated agent search and `semantic: true` to native search tools. It can
find notes using different vocabulary; the separately prepared local CPU worker
preserves scope checks and budgets. Lexical search remains the default, with
labelled fallback when the optional backend is unavailable.

```sh
bash scripts/local-store.sh status
bash scripts/local-store.sh backup
bash scripts/local-store.sh stop
```

`start` also applies checksummed forward migrations. Back up before upgrading an
existing store. The database retains captured evidence bytes and semantic
packages, so a PostgreSQL backup covers those. Run-directory copies require
separate retention accounting. No backup rotation, timer, or automatic deletion
is enabled.
For recovery, follow the [explicit restore-session workflow](docs/restore-admission.md),
which pauses ordinary transactions until reconciliation and operator resume.

## Use an agent harness

MCP-capable harnesses can discover Cairn's search, pull and capture tools through
the [local MCP server](docs/mcp.md). An actual OpenCode model retrieved a note
saved by another agent session and used it to answer the storage questions; the
[verification report](docs/verification/mcp-2026-09-08.md) records that bounded result.

For OpenCode custom tools with automatic session scope, `cairn opencode-install`
installs the adapter bundled with the binary and explicit connection settings.
It requires no source checkout and leaves host tool permissions unchanged. See
the [native tools setup](docs/opencode-tools.md#install-in-a-project).

For a compact index in the initial task input, `cairn agent start` explicitly
preloads ordinary hosted memory and launches the harness. The existing native
tools handle later pulls under their normal permissions. See
[compact startup](docs/compact-start.md) for the OpenCode command and budget limits.

`cairn opencode-config` generates the native server configuration with explicit
connection and task scope. See the [OpenCode setup](docs/mcp.md#opencode-example)
for the command, context flags and optional memory-only permission policy.

`cairn codex-config` generates a Codex MCP TOML entry using the same connection
flags and either `--codex-thread` or explicit `--task`/`--run` scope. See the
[Codex setup](docs/mcp.md#codex-example). It prints the entry for review and leaves
existing host configuration in place.

For a remote model, explicitly allow a note to be delivered outside the local
machine when you create it:

```sh
bin/cairn remember --shareable 'Run make test-integration for storage changes.'
bin/cairn run --destination hosted --carrier argv \
  --query 'storage tests' --prompt 'Review the storage changes' -- \
  /path/to/opencode run -m provider/model
```

`run` appends the compiled context and task to the command for `argv`, or writes
it to the command's stdin for `stdin`. It executes argv directly, without a
shell. Use `--dir` for the process working directory and `--repo` for memory
scope when they differ. Only shareable memory reaches the hosted package.
The command's own filesystem/network access is outside this memory filter.

The `--tokens` value is the actual input room you reserve for memory after
allowing for the task prompt, tool schemas, session history, and output. Default:
32,000. The compiler limits optional memory to 10% of that room, capped at 6,000,
and counts UTF-8 bytes as a conservative token upper bound. Required instructions
must fit completely. Unsupported mandatory runtime enforcement blocks launch.

Process output streams to stdout/stderr. The final Cairn receipt envelope goes
to stderr. Cairn records process exit, timing, and output digests, without keeping
raw model outputs. Task and query text are transient: new semantic v2/v3 packages
retain only a SHA-256 query digest. Digests are not anonymization, and old v1
receipts retain their original query text unless their containing package is excluded through explicit [record forgetting](docs/deletion.md).
Per-run `context.txt` and `outcome.json` live in the owner-only
run directory named in the receipt. New context files are [registered for controlled deletion](docs/managed-context.md). A failed DB write retains
`outcome.pending.json` for `cairn recover-run RECEIPT_UUID` recovery. Do not retry a
process blindly: the same compile request UUID cannot launch twice.

If context preparation fails after the launch claim, `run` retains a
`launch_failed` outcome with task outcome `not_attempted`, when the store is
available. It preserves the preparation error and does not write an outcome file
into a refused artifact path. If that database write also fails, the error remains
explicit and no local recovery file is promised for the unsafe path.

The wrapper records supplied context as `available`. The separate OpenCode probe
established model-request contact for its tested fixture. Neither observation
proves obedience, internal tool coverage, compaction behavior, or task acceptance.
User native-memory files are left in place and their influence is declared
unisolated.

## Inspect memory and outcomes

```sh
bin/cairn list "$PWD"
bin/cairn get RECORD_UUID
bin/cairn search --purpose planning 'placement or planning question'
bin/cairn impact RECORD_UUID
bin/cairn evidence-impact EVIDENCE_UUID
bin/cairn replay RECEIPT_UUID
bin/cairn explain RECEIPT_UUID
bin/cairn preview-retract RECORD_UUID
bin/cairn docket "$PWD"
bin/cairn report "$PWD"
```

Planning, placement, capability, and security retrieval excludes Class A notes.
Consequential Class B reads require resolvable supporting evidence and valid
authority. Advisory reads can include degraded B with its evidence state visible. A revoked
parent grant invalidates its descendants at the next compile. Class C
instructions are issued through a separate authorized path. Disputed optional
material is omitted whole; binding conflicts refuse compilation.

`replay` is labelled historical and verifies the retained canonical package and
BLAKE3 seal. It cannot authorize a fresh delivery. `impact` reports exposure, with
pagination/truncation metadata; it does not claim causal influence. The report
keeps exit-zero observations separate from unknown task outcomes.

The docket surfaces attribution contradictions, evidence-unavailable B records,
launches lacking outcomes, and matching A notes blocked from consequential use. An unfinished run may still be executing. A
manual inspection is required before declaring it abandoned.

Due failure proposals with the same comparison fields and error-signature digest
share a [review group](docs/demand-review.md#review-matching-failures-together).
Use `proposal-group REPO GROUP_DIGEST` to inspect its paginated source proposals;
group size does not establish corroboration or authorize admission.

The [use/outcome loop](docs/use-outcome-loop.md) documents joined observations,
versioned task assessments and completed-task delegate findings.
[Authenticated local access](docs/local-api.md) gives agents and host observers
scoped Unix-socket access without the operator CLI or database credentials.
With a provisioned agent token, save notes and use
[search and pull commands](docs/index-and-pull.md#agent-commands-without-request-json)
without writing request JSON:

```sh
bin/cairn agent remember --kind lesson --shareable 'Run make test-integration for storage changes.'
bin/cairn agent search --repo "$PWD" --task TASK_ID --run RUN_ID 'relevant query'
```

To correct only a saved note's text, pull its current version and call native
`cairn_edit` with `record_id`, `expected_version`, a stable `request_id` UUID and
`body`. Cairn preserves its stored draft metadata. Full-draft edits remain
available. The [revision API](docs/local-api.md#body-only-revisions) also works
through `cairn agent revise` with JSON input. Use `cairn agent revise --help`
(or `replace`, `history`, or `assess-run`) for an offline request example. For an additive update, use native
`cairn_edit` with `append` instead of `body`, or `cairn agent append` with a JSON
`body` suffix. For a targeted correction, use `replace: {old_text, new_text}`
through that edit tool; [exact passage replacement](docs/local-api.md#replace-one-exact-passage)
preserves all text outside one unique match. [Append](docs/local-api.md#append-selected-guidance) preserves the
existing text verbatim; include separating whitespace and reconcile version conflicts.
For obsolete guidance that should stop appearing in new context, follow the
[retirement workflow](docs/ordinary-delete.md#retire-obsolete-advice-while-retaining-history).
Retirement preserves the record history and currently requires the appropriate
local/operator path.

To inspect available topics when the saved vocabulary is unknown, replace the
query with `--browse`. Native `cairn_search` accepts `{"browse": true}`. This
returns eligible previews within the same scope, destination and budget limits;
it is not a complete inventory. Pull a relevant entry or use its wording to
refine the next query.

When browsing returns `browse.next_offset`, continue with `--browse --offset N`
or `{"browse": true, "offset": N}` in the same task/session. Each page has its
own budget and repeats required instructions; the host must account for the
combined context. Pages read current state, so edits can shift positions.

Each result includes a complete `pull_command` and matching `pull_arguments`
for its full body. Pass the argument object on stdin to `agent pull` with no
trailing arguments, or run the supplied shell command.
The [native OpenCode tools](docs/opencode-tools.md) use the
structured form and derive search scope from the current session. Reuse the
host's task/run IDs across queries; the configured token controls destination
and access. With an ordinary agent profile, `agent remember` saves A testimony,
local-only unless
`--shareable` is explicit. Repository scope defaults to the current directory;
task/run scope defaults to `*`. Use `--stdin` for multiline text and choose a
`--request-id` before the first attempt when capture must be retryable. The
[save-note examples](docs/local-api.md#save-an-ordinary-note) cover both forms.

## Authority and structured commands

The CLI is trusted local operator administration, identified by its OS UID.
Embedded orchestrators supply a `core.Channel` after authenticating their caller;
request JSON cannot choose identity, witness, or operator status. Agents must
not receive database credentials or the operator CLI. This is a light local
trust model, not isolation against hostile processes sharing the operator's OS
account.

Initialize the root grant once:

```sh
printf '%s\n' '{"request_id":"1a8dd335-e71b-44f5-a0b3-65e391c22259","reason":"Install the local Cairn operator root"}' | bin/cairn bootstrap
bin/cairn grants
```

The same request UUID safely retries the bootstrap. A different request cannot
install another root. Promotion requires a live grant, deliberately captured
evidence, and an actor absent from the record's producing/editing history.
Attributing work to another agent cannot defeat that check. Grant chains have at
most two delegated levels. Direct instruction authoring requires the `issue`
capability and a policy key.

JSON request commands include `create`, `edit`, `compile`, `capture-evidence`,
`grant`, `revoke-grant`, `promote`, `issue`, `correct`, `supersede`, `retract`, `dispute`,
`resolve`, `delete`, `forget`, and `usage`. Use [ordinary deletion](docs/ordinary-delete.md)
for unreferenced A notes; retained history requires the audited forgetting path.
See [request examples](docs/commands.md).
Use new request UUIDs for new intent and reuse them for transport retries.
Mutation retries return the original result; use `get` for current state.

Ordinary edits retain previous versions and require `expected_version`. They
cannot change scope or sensitivity. Corrections replace active B content with
new evidence references and an audit link. C content changes use retraction followed by
new issuance. Retraction requires a caller-owned `preview-retract` token, valid
for one hour and invalidated by content/version changes or new exposure. The
preview covers retained versions, known versioned dependents and their runs
(at most 1,000 versions and 1,000 uses). See [relations and demotion](docs/relations-and-demotion.md). Explicit conflict resolution retains the original members and
reasoned audit history.

[Supersession](docs/supersession.md) retires A or B in favor of an independently
eligible pinned replacement. It preserves earlier exposures, emits known impact
notices for review, and does not widen scope or transfer authority. A retirement
is unaudited; B requires live correction authority. `supersession RECORD_UUID`
inspects the retained replacement link.

[`authorize-scope`](docs/scope-authorization.md) uses live `issue` authority and
an impact preview to expand applicability within the same repository. The record
keeps its class and independent qualification. Revoking its separate scope grant
excludes fresh use; the original scopes and grants remain visible in historical
packages. `scope-authorization RECORD_UUID` inspects the latest scope decision.

## Verify changes

```sh
make test-integration
make test-lifecycle
make check
```

The integration target starts its own temporary PostgreSQL cluster, gives each Go
package a separate database, and uses Go's race detector. It stops the cluster on
exit and removes its files after success. On failure it prints the retained
artifact directory for diagnosis. A failed database stop also retains the directory
and fails the command. CI runs
packages sequentially against its shared service database. Explicit concurrent
transaction tests remain enabled. The suite covers migration from the original schema,
concurrent edits/retries, grant revocation races, evidence and destination gates,
conflicts, package seals, process launch/timeout, and duplicate-launch refusal.
The lifecycle target checks startup, catalog-backed backup/restore, replay,
recompilation, restored-handle invalidation, and restart. It also checks
[external recovery expectations](docs/recovery-inspection.md) against a real
dump taken before later revocation and forgetting.
Neither target touches the running local or host PostgreSQL instance.

To repeat the real OpenCode ingress probe without provider credentials:

```sh
CAIRN_OPENCODE_BINARY=/path/to/opencode make test-integration
```

`make test` explicitly skips DB tests unless `CAIRN_TEST_DATABASE_URL` is set.
Only point that variable at a disposable database. Initial dependency downloads
need an enabled Go module proxy; dependencies and CI actions are pinned.

## Remaining work

The [decision record](docs/decisions/0002-local-memory-loop.md) settles the
operating defaults. Remaining implementation includes real host lane integration, broader intent
matching, evidence lifecycle jobs,
redaction/deletion effects and retention, richer conflict delivery, host-level context budgets, real-history usefulness trials, and Striatum orchestration wiring.
No automatic grooming, learned ranking, or automatic promotion is running.
Do not retain secrets until redaction and recovery obligations are implemented.

The [initial design evaluation](docs/design-review.md) is historical; the
[implementation status](docs/implementation-status.md) records current evidence.
