# Changelog

Cairn is an untagged local alpha. Entries below summarize user-visible development
changes by source-commit date; they are not numbered releases. The
[implementation history](docs/implementation-status.md#implementation-history)
retains the complete chronology, including failed trials, corrections and
superseded work.

The latest recorded installation on 2026-09-10 has clean CLI/API `ed29cbc`,
native adapter from `797aafe`, and database migration 034. Declared native task
scope and explicit native capture scope use the existing API; task/run report
filters remain installed. The
optional semantic worker is from `ed29cbc` for passage reuse across idle release.
Host configuration retains native session defaults. Later documentation commits do not change these
builds. Check an installation with `cairn version`; use
`cairn agent --token-file TOKEN_FILE version` to compare the client and running API.
See [build identity](docs/build-identity.md) for unstamped builds and MCP processes.

## 2026-09-10

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
