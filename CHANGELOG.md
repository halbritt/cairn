# Changelog

Cairn is an untagged local alpha. Entries below summarize user-visible development
changes by source-commit date; they are not numbered releases. The
[implementation history](docs/implementation-status.md#implementation-history)
retains the complete chronology, including failed trials, corrections and
superseded work.

The previous binary installation, recorded on 2026-09-11, used clean CLI/MCP `75eb01e`,
API `bb600c5`, native adapter from `2ef6e04`, and database migration 034. Declared native task
scope and explicit native capture scope use the existing API; task/run report
filters remain installed. The
optional semantic worker is from `ed29cbc` for passage reuse across idle release.
The 2026-09-13 review repair updates the CLI and API together; the native adapter
and semantic worker are unchanged. Check an installation with `cairn version`; use
`cairn agent --token-file TOKEN_FILE version` to compare the client and running API.
See [build identity](docs/build-identity.md) for unstamped builds and MCP processes.

## 2026-09-23

- Automatic wakes preserve unsubmitted drafts on every native queue route.
  Idle and busy-owner-turn trials passed on both Claude and both Codex
  accounts, Hermes and OpenCode. See the
  [trial report](docs/verification/draft-preservation-2026-09-23.md).
- Use `--dangerously-load-development-channels=server:cairn-events` (the `=`
  form). With a space the variadic flag also consumes a following prompt.
  Refusal text, installer hint and docs now say so.
- The Codex launcher accepts Codex 0.156's `--no-daemon`; it no longer fails
  with "--no-daemon cannot be used with --remote".
- The Hermes engine-timeout test no longer fails intermittently under load.

## 2026-09-22

- Claude channel wakes are now admitted by detecting the live process's launch
  flags instead of a hand-maintained conversation list. A Claude process not
  launched with `--dangerously-load-development-channels server:cairn-events`
  (or `--channels` naming it) keeps ordinary turn-boundary delivery, so
  `claude_channel_dir` can be enabled per account without suppressing delivery.
  The optional `claude_channel_sessions` list remains an additional restriction;
  `claude_channel_server` overrides the expected server name.
- The presence watcher logs once per delivery and reason when a ready delivery
  has no automatic wake transport, instead of returning silently. On 2026-09-22
  that silence had hidden that no live Claude conversation was wake-eligible:
  the only listed conversation had exited and the second account had no channel
  directory. See [native inbox delivery](docs/native-inbox.md) and the
  [detection deployment report](docs/verification/claude-channel-detection-2026-09-22.md).

## 2026-09-16

- Native Codex queue claims now bind an exact delivery and native turn with
  migration 048. Owner prompts cannot acquire that queue's work, and changed
  readiness cannot redirect a delayed wake to another request. Interactive
  cancellation remains unimplemented.
- Wake cleanup rechecks the final unit state when systemd's stop command fails,
  covering units collected after their deadline. Unconfirmed cleanup retains
  the hold.
- Basic automatic wakeup exchanges passed across both Codex and both Claude
  accounts, Agy, OpenCode and Hermes. [The account report](docs/verification/native-wakeup-accounts-2026-09-16.md)
  retains earlier failures and separates this coverage from remaining draft
  preservation and interactive cancellation work.

- Claude wake workers clear an earlier rate limit when the terminal API error
  reports another or missing HTTP status. An installed-native loopback probe
  reproduced the stale classification and verifies slot health after recovery.
- Agy wake workers recognize selected native rate-limit results. Step ordering
  prevents an old 429 diagnostic from suspending a recovered account; partial
  timeout results remain separate from task success. Recovery stays explicit.

- Codex wake workers recognize the installed CLI's subscription-limit diagnostics
  and suspend the owning slot. Displayed reset times remain informational;
  recovery stays explicit. Native loopback probes cover Plus/Pro variants.

- **Collect explicit replies.** Migration 047 adds response groups with fixed
  recipients, correlation, deadlines and all/partial policies. Duplicate, late
  and unmatched replies remain observable; current result availability stays
  separate from historical collection state. See [the contract](docs/response-groups.md).

- **Control request lifetime.** Migration 046 adds admission expiry, managed task
  deadlines and operator cancellation. Completion and cancellation have one
  durable winner; process holds remain until the supervisor confirms termination.
  Interactive/native hard stops remain unsupported. See [the contract](docs/request-controls.md).

- **Schedule one-shot publication.** Migration 045 retains due times, stable
  occurrence IDs, misfires and cancellation of unpublished intent. Concurrent
  scheduler ticks commit one event and its fanout together; restore fences
  pending intent for review. See [the contract](docs/event-scheduling.md).

## 2026-09-15

- **Register continuing agent sessions.** Store-assigned UUIDs and `agent-N`
  display names identify conversations with scoped inboxes; harness, model,
  project and selected task remain reported metadata. `cairn agents` adds
  `register`, `context`, `list`, `heartbeat` and `leave`, backed by the
  `agent-register`, `agent-context`, `agent-directory`, `agent-heartbeat` and
  `agent-leave` API operations. Apply migration 037 and update CLI/API together.
  Registration alone does not install presence or native delivery adapters.
  [Identity, commands and recovery](docs/agent-sessions.md).

- **Separate launch slots from accounts.** Seven ordinal wakeup bindings cover
  both Codex and both Claude accounts. Each worker receives its profile name;
  legacy inboxes retain their history. The additional Claude account passed a
  live check; the additional Codex account reported its usage limit. Recorded
  the [coordination v1 plan](docs/plans/agent-coordination-v1.md) for live agent
  identity, metadata-based routing and the deferred feature inventory.

- **Enable the remaining wakeup profiles.** Codex, Claude Code and Agy now use
  the same supervised fresh-worker path. Live completion checks passed against a
  disposable database. Claude's binding uses Sonnet 5 after its configured Fable
  model returned exhausted credits. Existing interactive settings are preserved.

- **Wake fresh agents for durable requests.** Owner-configured OpenCode and Hermes
  bindings poll their inboxes and launch systemd-managed workers. Durable attempts
  hold deliveries across lease expiry, link runner receipts, and reconcile stopped
  process groups before another launch. Explicit completion is required; uncertain
  execution is not replayed. Apply migration 036 and update CLI/API together.
  [Contract and installation](docs/agent-wakeups.md).

- **Deliver durable agent notifications.** Direct inboxes and topic subscriptions
  use PostgreSQL-backed polling, exact source versions, expiring leases and
  retry-safe publication. Atomic completion saves one result with its handling
  record. Event history, status and counters remain available after restart;
  restore fencing invalidates old leases. Upgrade CLI and API together and apply
  migration 035 after a backup. Provision named hosted profiles for distinct
  inboxes. [Contract and commands](docs/agent-event-fabric.md),
  [verification](docs/verification/agent-event-fabric-2026-09-15.md).

## 2026-09-13

- **Connect Hermes CLI and messaging gateways to shared memory.** The native
  provider adds bounded ambient recall, selected completed-turn capture and
  compaction checkpoints. Explicit tools use the existing hosted MCP profile;
  native Hermes memory and task-model settings remain available. Install the
  Python provider and restart Hermes; no API upgrade or migration is required.
  [Setup](docs/hermes-lifecycle.md) and
  [verification](docs/verification/hermes-integration-2026-09-13.md).

- **Repair the ultrareview findings.** Top-level help prints text; runner cleanup
  errors preserve outcomes and recovery requests. CLI `list` accepts `--limit`
  and `--offset`, and `impact` accepts `--offset`. Missing conflicts and invalid
  inspection inputs have consistent error codes; new refusals retain candidate
  totals. Tight search responses keep structured pull arguments while omitting
  redundant shell commands. Update the CLI and API; no database migration is
  required. [Verification](docs/verification/ultrareview-repairs-2026-09-13.md).

- **Make lifecycle memory selective and continuous across agents.** Claude and
  OpenCode use file/error hints, omit weak matches, retain bounded context and
  avoid repeating unchanged capture. Reusable notes update separately from named
  workstream checkpoints. Native OpenCode continued the same handoff written by
  Claude. Install/update the Python hooks and optional OpenCode plugin; no store
  migration or API upgrade is required. [OpenCode setup](docs/opencode-lifecycle.md),
  [verification](docs/verification/lifecycle-improvements-2026-09-13.md), and
  [Codex/Agy assessment](docs/lifecycle-hook-assessment.md).

- **Recall and checkpoint memory at Claude lifecycle boundaries.** Optional user
  hooks inject bounded memory on startup, submitted tasks, resume and compaction.
  Before compaction or exit, a tool-free model call selects a concise checkpoint
  for the existing authenticated store. The installer preserves other settings;
  no binary or database upgrade is required. [Setup and limits](docs/claude-lifecycle.md).
- **Save useful memory proactively across agents.** The deployed Cairn skill now
  includes owner corrections, decisions with reasons, verified fixes, and context
  loss as capture triggers while excluding routine progress and lookup-only saves.

## 2026-09-11

- **Use shared memory across projects in all four agents.** Codex, OpenCode,
  Agy, and Claude Code now have user-level Cairn installations on the owner's
  host. They save and recall the same collection without per-project setup.
  Native reads and writes from Pincite and Striatum Next verified the connection.
  [Use shared memory](docs/shared-memory.md).

- **Inspect everyday agent command flags offline.** `cairn agent search`,
  `remember`, `pull`, and `pull-evidence` now provide readable `--help` and `-h`
  output from the same flag definitions used for execution. Help needs no token,
  API, home directory, or stdin; malformed calls remain errors. Upgrade only the
  CLI. Clean `75eb01e` is installed after successful exact-source CI and an
  installed offline check; the API, store, adapter, configuration, and ordinary
  note inventory were preserved. [Checks, installation, and failed model trial](docs/verification/agent-flag-help-2026-09-11.md).

## 2026-09-10

- **Inspect ordinary JSON requests from the CLI.** `cairn agent --help` and
  `cairn agent OPERATION --help` explain note corrections, citations, history,
  qualitative reviews and reconstruction with request examples. Help works
  without a token, API, home directory or stdin. Upgrade only the CLI.
  [Supported operations](docs/local-api.md#find-an-ordinary-request-format).

- **Expose the review tools in Codex configuration.** `codex-config` now includes
  `cairn_assessments` and `cairn_assess`. Existing allowlists need both names and
  a fresh session; upgrading the binary alone does not add them. Native Codex
  discovery and the review workflow passed.
  [Correction and checks](docs/verification/codex-review-allowlist-2026-09-10.md).

- **Review a task through native memory tools.** MCP and the OpenCode adapter
  add `cairn_assessments` and `cairn_assess` for owned assessment history and
  qualitative review writes. Reasons, earlier revisions and uncertainty remain;
  agent reviews remain testimony. Upgrade the tool executable/adapter and use
  the API binding repair; no migration is required.
  [Workflow and limits](docs/use-outcome-loop.md#native-review-tools).

- **Keep assessment writes and retries within the current profile.** The API
  now rejects an earlier receipt after its principal's repository or destination
  changes, including requests whose responses were already cached. Matching
  retries and earlier history remain intact. Update the API; no migration is
  required. [Repair and compatibility](docs/verification/assessment-binding-2026-09-10.md).

- **Write a qualitative memory review.** The existing assessment guide now
  includes an authenticated write/read-back example with stable request retries
  and conflict handling. Unknown acceptance remains compatible with qualitative
  value. No upgrade is required. [Example](docs/use-outcome-loop.md#record-a-qualitative-review).

- **Keep observed expansion visible in use reports.** `expansion_observed`
  preserves the fact of a retained instrumented expansion alongside later
  testimony or citation. Existing usage labels, witnesses and unknown outcomes
  remain. Update the API or direct-store CLI; no migration is required.
  [Meaning and checks](docs/verification/expansion-observation-2026-09-10.md).

- **Identify client setup and connection failures.** CLI and MCP now distinguish
  token-file I/O failure from an unavailable API connection, with actionable
  messages that omit private paths and credentials. Unknown errors remain
  masked. Upgrade the CLI/MCP executable; the current API and schema suffice.
  [Behavior and verification](docs/verification/client-diagnostics-2026-09-10.md).

- **Keep failed integration artifacts for diagnosis.** `make test-integration`
  stops its disposable PostgreSQL cluster and prints the retained directory when
  a check fails. Successful runs still remove their temporary files. This needs
  only the updated checkout, with no CLI/API installation.
  [Checks and native investigation](docs/verification/failed-integration-artifacts-2026-09-10.md).

- **Reuse passage vectors after an idle worker exits.** The streaming API can
  retain a bounded snapshot in memory while releasing the loaded semantic model.
  A compatible fresh worker restores matching-model vectors; current eligibility
  and scores remain unchanged. Upgrade the API and prepared worker; no database
  migration or disk cache is added. [Behavior and limits](docs/semantic-discovery.md).

- **Refuse unusable harness settings before installation.** MCP startup and
  configuration generators enforce the existing scope byte limit. OpenCode
  setup now refuses malformed UTF-8 instead of silently replacing characters;
  configuration text and paths reject NUL. Valid Unicode remains unchanged.
  [Repair and checks](docs/verification/harness-configuration-text-2026-09-10.md).
  Update the CLI; the existing API and database suffice.

- **Save guidance for one task or run.** Native MCP and OpenCode
  `cairn_remember` accept `scope: "task"` or `"run"`, using the host's search
  labels. Repository-wide capture remains the default. Update the MCP binary or
  bundled OpenCode adapter; the existing API/schema suffice.
  [Usage](docs/mcp.md#choose-capture-scope).

- **Continue a named task across OpenCode sessions.** `opencode-install --task`
  gives native searches a stable declared task while retaining native run IDs.
  Add `--run` to fix both labels, for example to match a compact launcher.
  Defaults remain session-derived. [Usage](docs/opencode-tools.md#continue-a-task-across-sessions).
  Update the CLI and bundled adapter; existing API and database versions suffice.

- **Reuse unchanged passages after note edits.** The optional semantic worker
  caches exact decoded passages within its existing lifetime. Localized edits
  only re-embed changed windows; current eligibility and cache limits remain.
  [Measurements and limits](docs/verification/semantic-chunk-cache-2026-09-10.md).
  Update the prepared worker script and restart the API; no binary or schema
  upgrade is required.

- **Review one task across runs.** `use-report` and `run-report` accept exact
  `--task` and `--run` filters before pagination, including their authenticated
  JSON equivalents. [Usage](docs/use-outcome-loop.md#follow-one-task-across-runs).
  Existing report access and outcome interpretation remain; update the CLI/API,
  with no database migration.

- **Lower the room for one native search.** MCP and OpenCode `cairn_search`
  accept `available_tokens` beneath the configured host ceiling. Defaults and
  mandatory-context checks remain; changed room needs a new search request ID.
  [Usage and limits](docs/search-room.md). Update the CLI/MCP executable and
  native OpenCode adapter; no API upgrade is required.

- **Use structured pull arguments directly.** `agent pull` and `agent pull-evidence`
  now accept JSON on stdin when no trailing arguments are supplied. The shell
  forms, retry IDs and existing API expansion checks are preserved.
  [Usage](docs/index-and-pull.md); no API upgrade is required.

- **Integrate the preparatory Striatum path.** Native capture, observation, build
  inputs and supervisor correspondence are merged into Striatum main at
  `5848b23`, with historical-contract compatibility repairs. Native contracts
  remain disabled in the accepted catalog; this does not change the installed
  Cairn builds. [Scope and checks](docs/verification/native-integration-2026-09-10.md).

- **Discover harness setup flags offline.** `mcp`, `opencode-config`, `codex-config`
  and `claude-config` now print useful `--help`/`-h` output and exit successfully
  (`af43ac7`).
  [Command help](docs/mcp.md#command-help) uses the registered flags and needs no
  credentials, running API or database.

- **Inspect a passage from a long earlier note.** [History excerpts](docs/record-history.md#read-a-passage-from-an-earlier-version)
  (`4999caa`) add optional byte ranges to exact-version reads through CLI/API, MCP and native
  OpenCode. Results retain full-source identity and lossless selected bytes;
  current access rules and per-call output budgets still apply.
- Documented [retiring obsolete advice](docs/ordinary-delete.md#retire-obsolete-advice-while-retaining-history)
  through existing guarded retraction. The guide distinguishes stopping future
  retrieval from hard deletion and explains the ordinary hosted-tool limitation.

- **Correct notes without rewriting unrelated instructions.** The existing edit
  tool supports [exact passage replacement](docs/local-api.md#replace-one-exact-passage)
  (`96e8eae`) and [verbatim append](docs/local-api.md#append-selected-guidance)
  (`664472c`). Both preserve other text, metadata, citations and earlier versions;
  stale edits refuse and identical retries retain their original results.
- **Find context by known code or error text.** Added
  [quoted search](docs/quoted-search.md) (`7dcf3dc`),
  [file and symbol associations](docs/entity-search.md) (`3ff1fcd`),
  optional [recent OpenCode file hints](docs/recent-file-hints.md) (`670cf19`), and
  [reviewed failure-signature retrieval](docs/failure-signature-search.md) (`61627b3`).
- **Supply context for an individual search.** Native tools can fill context
  fields the host left unset, without changing later calls or overriding host
  pins. [Per-call context](docs/search-context.md), `9f43b00`.
- **Inspect prior and competing guidance.** Added native
  [retained history](docs/record-history.md#native-tools) (`9b888e1`), explicit
  [qualified advisory conflicts](docs/advisory-conflicts.md) (`4df17af`), and
  authenticated [historical receipt reconstruction](docs/currentness-and-replay.md#recompile-a-retained-read-set)
  (`3f17cf2`). Historical content does not gain current authority.
- **Preserve reviewed lesson references.** Proposal conversions pin the linked
  lesson version, and retained review history survives later changes.
  [Proposal history](docs/demand-review.md#preserve-the-linked-lesson-version), `97816e9`.
- **Fix misleading or lossy behavior.** Refusal diagnostics retain known mandatory
  gates and distinguish category-budget refusal from selection (`c5fa55a`);
  native OpenCode arguments reject malformed Unicode before transport (`6c48411`).
  Refusal traces remain partial. [Diagnostics](docs/refusals.md),
  [Unicode repair](docs/verification/opencode-unicode-2026-09-10.md).
- **Reuse semantic workers across spaced searches.** Hosts can configure the idle
  lifetime of the optional local worker (`72e7c24`).
  [Semantic discovery](docs/semantic-discovery.md) retains labelled lexical fallback.

## 2026-09-09

- **Browse, refine and continue retrieval.** Added bounded browsing and continuation,
  kind filters, ranked-search continuation and optional local semantic discovery.
  [Index and pull](docs/index-and-pull.md), [semantic discovery](docs/semantic-discovery.md).
- **Read and maintain selected passages.** Added bounded note/evidence excerpts,
  preview source positions, authenticated retained history, and body-only edits
  that preserve metadata. [Record history](docs/record-history.md),
  [body revisions](docs/local-api.md#body-only-revisions).
- **Retain precise supporting sources.** Added binary evidence, selected-file
  capture, digest/passage citations and ordinary-note citations. JSON transport
  now accommodates the existing 64 KiB note and 1 MiB evidence limits even when
  escaping enlarges the request. Malformed Unicode requests refuse before decoding.
  [Evidence citations](docs/evidence-citations.md), [local API](docs/local-api.md).
- **Make harness setup repeatable.** Added the bundled OpenCode adapter installer,
  Codex and Claude configuration generators, and native OpenCode argument
  validation. [OpenCode](docs/opencode-tools.md), [Codex](docs/mcp.md#codex-example),
  [Claude Code](docs/claude-code.md).
- **Carry task intent into retrieval and execution.** Added phase/applicability
  capture, compact startup, saved task-file input, observed index execution and
  selected run-file fingerprints. Fixed missing-context precedence and refused
  oversized argv input before claiming a run.
  [Currentness](docs/currentness-and-replay.md), [compact startup](docs/compact-start.md),
  [observed indexes](docs/observed-index.md).
- **Inspect the running system and its records.** Added build identities,
  authenticated assessment history, use-history pagination, evidence impact,
  richer retirement previews and partial refusal traces.
  [Build identity](docs/build-identity.md), [use/outcome observations](docs/use-outcome-loop.md).

## 2026-09-08

- **Use ordinary memory through agent harnesses.** Added authenticated remember,
  search and paired pull commands; MCP search/pull/capture/edit; Codex conversation
  scope; native OpenCode session tools; and OpenCode configuration generation.
  [MCP](docs/mcp.md), [OpenCode tools](docs/opencode-tools.md).
- **Connect retrieval to observed work.** Added the authenticated runner, exact
  host-attempt binding, dynamic retrieval associations and retained-package
  execution with fresh eligibility checks.
  [Authenticated runner](docs/authenticated-runner.md),
  [use/outcome join](docs/use-outcome-loop.md).
- **Review repeated failures and govern changes.** Added failure proposal groups,
  record supersession, scope authorization, versioned policy changes and instruction
  category limits. [Demand review](docs/demand-review.md),
  [supersession](docs/supersession.md), [governed policy](docs/governed-policy.md).
- **Reconcile explicit restores.** Added guarded restore admission, withdrawal
  reapplication and forgotten-source dependency repairs.
  [Restore admission](docs/restore-admission.md).

## 2026-09-07

- Built the local note, context compilation and process-delivery loop (`e3b47c7`),
  then added the authenticated API, use/outcome join, currentness checks,
  historical recompilation and bounded index/pull.
- Added evidence-attached review, standalone failed-task proposals, conflict
  inspection, durable refusal records, ordinary deletion, audited forgetting and
  guarded lifecycle operations. [Full history](docs/implementation-status.md#implementation-history)
  records the individual changes and verification limits.

## 2026-09-06

- Started the PostgreSQL record/version store and delegation-attribution
  reconciliation (`8cac00e`). This was the first source commit, before the later
  local-memory loop and harness interfaces.

## Upgrading an older alpha

Back up the dedicated store before applying forward migrations. Upgrade the CLI,
running API and applicable native adapter together before using new arguments.
Existing MCP processes retain their original executable until restarted.
Follow the [local-store instructions](README.md#get-started) and feature-specific
compatibility notes; replacing a binary is not a database rollback.

- Upgrade every database reader **before writing task-phase constraints**: old
  readers can ignore those constraints. [Phase compatibility](docs/currentness-and-replay.md#declared-task-phases).
- Upgrade writers and ordinary clients **before using file/symbol associations**:
  old writers can drop metadata they do not understand. Associations use migration
  034. [Association compatibility](docs/entity-search.md).
- Review the migration and reader requirements for
  [citation metadata](docs/evidence-citations.md) and
  [failure-signature sharing](docs/failure-signature-search.md) when upgrading
  across those additions. Earlier receipts keep their historical representations.

The [task-value inventory](docs/verification/usefulness-status-2026-09-08.md)
separates useful observations, failed trials and unknown net benefits. This
changelog describes implemented capabilities; the [roadmap](docs/roadmap.md)
retains the unfinished requirements.
