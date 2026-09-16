# Implementation status — 2026-09-16

Current installed CLI/API: clean `26c60ce`, migration 046. Native session identity,
presence, exact metadata resolution, turn-boundary inbox delivery, worker pools,
selected provider-failure handling, watch, operator recovery and one-shot
scheduling are deployed. [Request controls](request-controls.md) now add admission
expiry, managed task deadlines and cancellation while retaining process holds.
See [the request-control report](verification/request-controls-2026-09-16.md).
The API, scheduler, presence, seven workers and Hermes gateway were verified
running after rollout.

Native interactive hard stops, response groups and the remaining live routing
and provider-coverage checks in the [v1 plan](plans/agent-coordination-v1.md)
remain open. The snapshots below retain earlier implementation states.

## Historical implementation snapshots

The [wakeup supervisor](agent-wakeups.md) adds fresh Codex, Claude Code, Agy, OpenCode and Hermes workers
for request events. PostgreSQL holds each claimed delivery until its systemd unit
has stopped; explicit result completion, process observations and task acceptance
remain separate. Seven ordinal worker-slot services cover both Codex and both
Claude accounts; the former five harness-named services are disabled. The first
Codex account (`worker-01`) hit a provider usage limit during its live check.
The installed runtime is clean build `e4fa703`, with migration 036. See the [verification report](verification/agent-wakeups-2026-09-15.md)
for test coverage, installation details and limits. The
[coordination v1 plan](plans/agent-coordination-v1.md) covers live identity,
harness/model/project metadata, session routing and all previously deferred work;
these planned capabilities are not yet installed.

The [agent event fabric](agent-event-fabric.md) adds direct inboxes, topic fanout,
durable subscriptions, leased polling and atomic result completion. Events are
operational records outside memory search. The
[verification report](verification/agent-event-fabric-2026-09-15.md) maps the
contract to disposable database, CLI/API restart and backup/restore checks, and
records the installation status. Delivery does not launch agents or establish
task success. Existing implementation history below is retained.

The [Hermes provider](hermes-lifecycle.md) adds explicit tools, ambient recall and
selected capture for interactive CLI and the configured Slack gateway. Native
fixtures cover both entry points, cross-agent continuation, compression, routing,
restart and failures; 90 Python tests and the full Cairn integration suite pass.
The [Hermes verification report](verification/hermes-integration-2026-09-13.md)
records the installed revision and distinguishes native mechanics from usefulness.

The [2026-09-13 ultrareview repair](verification/ultrareview-repairs-2026-09-13.md)
addresses outcome recovery, CLI help and pagination, error classification, refusal
counts and tight index presentation. The disposable integration suite, race checks
and 74 Python tests pass. The CLI and API need the repaired build; no migration
is required. Earlier installation identities below are historical snapshots.

[Claude lifecycle hooks](claude-lifecycle.md) and the optional [OpenCode lifecycle
plugin](opencode-lifecycle.md) now share selective recall, separate reusable notes,
named workstreams and unchanged-capture suppression. Native OpenCode continued
and revised a Claude-created handoff. The proactive Cairn skill remains concise
and deployed across all four agents. [Verification](verification/lifecycle-improvements-2026-09-13.md)
separates native transport, real-model selection and broader usefulness.
[Codex/Agy hooks](lifecycle-hook-assessment.md) are assessed but not installed for
Cairn.

Cairn is a usable local alpha for explicitly selected agent memory. It stores
versioned notes and supporting evidence, makes them available through CLI,
Codex and OpenCode interfaces, and records supplied context and observed outcomes.
The installed system supports ordinary cross-session use today.
As of 2026-09-11, [shared memory is available across projects](shared-memory.md)
through global Codex, OpenCode, Agy, and Claude Code installations. Native reads
and writes from Pincite and Striatum Next used the same existing collection.
The [changelog](../CHANGELOG.md) summarizes user-visible development changes and
upgrade requirements; the full implementation history remains below.

Usefulness evidence includes one successful knowledge transfer between harnesses,
one accepted configuration task after a stored procedure was corrected, and a
[qualitative repair case](verification/value-case-validation-2026-09-09.md) where
recalled guidance plausibly helped focus an investigation that fixed a real bug.
The repair is verified; memory's incremental contribution and net benefit remain
uncertain. Durable improvement across coding tasks and harnesses remains
unestablished. A [cumulative maintenance case](verification/cumulative-maintenance-value-2026-09-09.md)
adds actual recovery of lost guidance alongside self-created rework and unknown
net cost. Task value may be qualitative, indirect or delayed; mechanical
proof is not the sole admissible evidence. The [roadmap](roadmap.md) retains the complete requirements and
acceptance boundaries; full Stage 1–2 completion is not claimed.

[Native review tools](use-outcome-loop.md#native-review-tools) now support owned
assessment history and qualitative writes in MCP and OpenCode. Disposable
interface checks passed. CLI/MCP `75eb01e` includes the
[Codex allowlist correction](verification/codex-review-allowlist-2026-09-10.md);
this project's configuration includes both review tools. The API remains at
`bb600c5` and the native adapter at `2ef6e04`. The saved Codex setup procedure is
corrected to v12, including the global installation and shared collection. The missed installation step and subsequent CI test repair
remain in the history below.

[Ordinary CLI request help](local-api.md#find-an-ordinary-request-format) now
explains JSON-based corrections, history and reviews without connecting to the
API. Its copied examples and full integration checks pass; clean CLI/MCP
`75eb01e` is installed after successful CI and an installed offline check. That
build also exposes the actual flag options and positional forms for ordinary
search, remember, pull, and evidence pull without requiring a token, HOME, API,
or stdin.

A [worked qualitative review](use-outcome-loop.md#record-a-qualitative-review)
now covers authenticated assessment writes, exact retry and history read-back.
Its literal commands passed against a disposable API. The associated native
OpenCode authoring attempt timed out after source-read permission denials;
[the retained review](verification/qualitative-review-example-2026-09-10.md)
records that failure separately from the root-authored documentation.

A [native guide-maintenance task](verification/native-guide-maintenance-2026-09-10.md)
now adds one reviewed OpenCode update to an existing operational procedure.
The old guide survived intact; a root formatting correction followed the native
append. This supports practical memory maintenance, with downstream benefit and
net savings still unknown.

An [assessment binding repair](verification/assessment-binding-2026-09-10.md)
now checks the current profile before cached write responses as well as new
writes. API `bb600c5` is installed after passing CI and ordinary hosted
retrieval checks; the initial CI failure and test correction remain in the history.

The installed CLI/MCP is clean `2ef6e04`, the API is clean `bb600c5`, and the
native OpenCode adapter is from `2ef6e04`. [Use-report expansion observations](verification/expansion-observation-2026-09-10.md)
keep a retained instrumented expansion visible alongside later testimony or
citation, without claiming delivery or task benefit. [Client diagnostics](verification/client-diagnostics-2026-09-10.md)
identify token-file I/O and Unix socket dial failures without private paths
or credentials. The [harness setup repair](verification/harness-configuration-text-2026-09-10.md)
rejects malformed configuration text and overlong scope before producing unusable
settings. [Declared native task scope](opencode-tools.md#continue-a-task-across-sessions)
is installed and verified with that API. [Explicit native capture scope](mcp.md#choose-capture-scope)
now saves a note for the repository, current task or current task/run. Repository
capture remains the default. The optional semantic worker is
from `ed29cbc`: [passage reuse across idle release](verification/semantic-idle-cache-2026-09-10.md)
keeps a bounded vector snapshot in API memory after the model worker exits. One
installed search after the existing five-minute idle interval took 0.612 seconds,
compared with 24.228 seconds cold, with matching scores and source identities.
Initial cold scoring remains expensive.
[Task/run report filters](use-outcome-loop.md#follow-one-task-across-runs) select
one task’s retained runs and memory exposures before pagination. Broader
recurrence analysis and task-value evidence remain open.
[Native search room](search-room.md) is installed in MCP and OpenCode: a search
can request a smaller allowance beneath its configured ceiling. Whole-task context
accounting remains open.
[Structured CLI pulls](verification/pull-json-2026-09-10.md) accept returned
arguments directly as JSON while preserving shell forms and API checks. The
[help repair](verification/harness-help-2026-09-10.md) prints harness setup flags
without requiring a connection or an API upgrade. The OpenCode adapter supports
explicit advisory-conflict retrieval, returning complete qualified competing
positions through the existing search/pull tools. Default omission and binding
refusals remain. It also retains optional recent-file hints, versioned file and
symbol associations, entity search, retained history, Unicode validation, per-call
context, reviewed failure signatures and quoted search. The host keeps its
five-minute semantic idle lifetime and twenty-five-second worker budget.
The database remains at migration 034.
[Refusal diagnostics](verification/refusal-gates-2026-09-10.md) now retain known
mandatory instruction gates before returning an error. Existing refusal histories
remain unchanged, and traces still disclose their partial coverage.

The installed system supports [selected append updates](verification/note-append-2026-09-10.md)
through the ordinary CLI/API and existing native edit tools. This preserves prior
text exactly for additive maintenance. One selected update extended the saved
history procedure without changing its earlier text; independent downstream task
benefit remains unmeasured.

[Exact passage correction](verification/note-replace-2026-09-10.md) is also
installed through the existing edit tools: supply one unique `old_text` and its
`new_text` to preserve the rest of the note. Stale or ambiguous edits refuse.
This supports targeted maintenance alongside full-body edits and append.

The [historical replay audit](verification/replay-requirements-2026-09-10.md)
clarifies E3: retained-read-set recompilation works, including exclusion of later
notes and instructions. A preserved real-run preflight reproduces its host seal.
[Authenticated host-receipt reconstruction](verification/authenticated-recompile-2026-09-10.md)
now works with the original profile and current privacy checks. Original Striatum
incident availability remains open; this is not additional task-benefit evidence.

This snapshot assesses the source changes recorded below and the local installation
on 2026-09-10. Feature reports below preserve their own verification dates and limits.
The [implementation history](#implementation-history) retains the earlier narrative
and subsequent recorded changes, including failed trials and superseded work. Future updates should append dated history and corrections while refreshing
the current summary; do not replace the historical record.

The installed system supports [historical byte excerpts](verification/history-spans-2026-09-10.md)
through the existing history tool. An agent can inspect selected earlier bytes
within its current output room; whole-body behavior and current access checks
remain. Downstream task value remains unmeasured.

[Native Striatum implementation is now merged](verification/native-integration-2026-09-10.md)
to Striatum main at `5848b23`. The integration repairs two historical-contract
regressions and passes full repository and focused native race checks. Its
accepted catalog remains `observation@1`/`build@3`; native adoption and real-build
benefit remain open. That integration left the installed Striatum Driver unchanged.

The [native adoption check](native-striatum-context.md#shared-rfc-adoption-check--2026-09-10)
also found an unfinished, separate amendment on RFC 0004. Its request and
escalations do not advance Cairn; the shared RFC requires sequencing before
Cairn's proposed contracts can enter the accepted stages.

## Working capabilities

| Area | Implemented behavior | Details |
| --- | --- | --- |
| Ordinary memory | Authenticated capture of selected notes up to 64 KiB with room for JSON escaping, retained versions and attribution, repository/task/run scope, compare-and-swap edits, body-only corrections that preserve metadata, and authenticated retained-version inspection. | [Capture and use](../README.md), [ordinary revision](mcp.md#tools), [record history](record-history.md) |
| Retrieval | Lexical search with optional kind selection, matching previews with source byte positions, ranked and browse continuation, currentness and destination filtering, mandatory context, and whole or explicit partial A/B body pulls with live checks on retries. | [Index and pull](index-and-pull.md) |
| Semantic discovery | Optional local CPU scoring for vocabulary mismatches, after eligibility checks; bounded work and labelled lexical fallback. Historical recompilation uses retained scores. | [Semantic discovery](semantic-discovery.md) |
| Supporting evidence | Explicit capture of up to 1 MiB, SHA-256 verification, lossless binary inspection, persisted check generations, citation-time digests and optional byte passages on qualified claims and ordinary note versions, and whole-object or byte-span pulls through existing indexed handles and budgets. | [Evidence refresh](evidence-refresh.md), [precise citations](evidence-citations.md), [span verification](verification/evidence-spans-2026-09-09.md) |
| Harness access | Six ordinary tools for search, body/evidence pulls, capture, edit and retained history through MCP; Codex conversation scope and native OpenCode session scope. OpenCode configuration generation and a bundled native-tool installer are shipped; Claude configuration and the original five tools have scripted native-session verification with explicit scope; native Claude history remains unverified. | [MCP](mcp.md), [OpenCode tools](opencode-tools.md) |
| Host execution | Authenticated observer access, compiled or exact retained-package execution, freshness checks before launch, process observations and separately versioned task assessments. Hosts can associate agent retrievals with observed runs. | [Authenticated runner](authenticated-runner.md), [retained execution](retained-execution.md), [use/outcome join](use-outcome-loop.md) |
| Inspection and review | Historical replay/recompilation, record and evidence impact, versioned relation paths, use/run reports, conflict inspection, bounded refusal diagnostics and evidence-attached failure review groups. | [Evidence impact](evidence-impact.md), [refusals](refusals.md), [demand review](demand-review.md) |
| Authority and lifecycle | Operator grants/revocation, independent B promotion and correction, C instructions, guarded retraction, B→A demotion, supersession, scope authorization, governed policy revisions and instruction category limits. | [Relations and demotion](relations-and-demotion.md), [supersession](supersession.md), [scope authorization](scope-authorization.md), [policy](governed-policy.md) |
| Deletion and restore | Unreferenced A deletion, audited record-body forgetting and purge, managed context-file deletion, unsigned checkpoints, explicit restore fencing, known withdrawal reapplication and guarded restore admission. | [Ordinary deletion](ordinary-delete.md), [forgetting](deletion.md), [managed context](managed-context.md), [restore admission](restore-admission.md) |

Ordinary notes remain fallible testimony. Supporting evidence and authority are
separate requirements for consequential use. A source locator is not retained
proof, a citation is not evidence of compliance, and process exit zero is not
task acceptance. Native conversation/session scope does not identify individual
execution attempts or automatically associate retrieval with a host outcome.

## Recent retrieval and integration work

[Qualified advisory conflict delivery](advisory-conflicts.md) is implemented and
verified through the core, MCP and native OpenCode tools. Explicit requests return
complete competing positions with currentness and privacy checks; default omission
and binding refusals remain. Clean `4df17af` is installed in the CLI, API and adapter. Task benefit is
unmeasured.

[OpenCode guidance is separated by workflow](verification/procedure-maintenance-2026-09-09.md#2026-09-10--separate-ordinary-tools-from-harness-launch-instructions):
the current setup/tool note is 9,270 bytes, while launcher instructions have a
separate 6,229-byte procedure. Every original passage remains verbatim in a
current note and the former v16 body remains in history. Setup-only pulls are
34.2% smaller; pulling both costs 1,412 bytes more. Task benefit remains unmeasured.


[Optional recent-file hints](recent-file-hints.md) now let OpenCode collect
successful native text-file reads for fresh ordinary searches. The native
five-case check retrieved and pulled associated guidance without an explicit
filename hint, preserved retry intent after another read, and enforced both
global and repository-specific permission denials. Capture remains explicit.
This is retrieval capability evidence; sustained task benefit remains open.
The feature is installed locally at `670cf19`. A native read of
`core/currentness.go` followed by empty-argument search pulled the existing
applicability guide through the ordinary hosted profile.


- **File and symbol associations distinguish deliberate links from mentions.**
  Ordinary capture/search/edit/history now carry explicit versioned associations.
  The existing applicability lesson is retrieved and pulled by its file in Codex
  and its symbol in OpenCode, with identical source version and body hash.
  Body-only edits preserve associations; scope, authority and historical replay
  retain their boundaries. Automatic host context collection and task benefit
  remain open. [Contract](entity-search.md), [verification and installation](verification/entity-search-2026-09-10.md).

- **Spaced semantic follow-ups reuse cached vectors.** After a 35-second pause,
  a five-minute host lifetime reduced one follow-up from 24.851 to 0.063 seconds
  with matching sealed context and complete score digest, at about 220 MiB RSS.
  This host uses five minutes; the default remains thirty seconds. Cold work and
  model-task benefit remain open. [Comparison and installation](verification/semantic-idle-2026-09-10.md).

- **Cold semantic scoring can finish the observed corpus.** Both transports now
  allow twenty-five seconds, within native/API outer limits. OpenCode and Codex
  retrieved and pulled the intended setup note with identical full-corpus scores;
  the tested model and inputs are unchanged. This permits more computation and
  waiting rather than reducing inference cost; larger-corpus cost and task benefit
  remain open. [Investigation and deployment](verification/semantic-deadline-2026-09-10.md).

- **Long-note ranking remains under evaluation.** A fixed prefix tie-break
  improves dedicated-note ranks on twelve questions, but the subject-derived
  workload does not justify a default change. Existing semantic procedure
  filtering retrieves the intended Codex setup note first. Unfiltered semantic
  search fell back; a separate installed-worker run on the twenty-note corpus
  took 20.015 seconds, near the API deadline. Cold cost needs investigation;
  the specific live fallback cause and task benefit remain unconfirmed.
  [Comparison and current guidance](verification/leading-context-2026-09-10.md).

- **Retained versions are readable through native tools.** `cairn_history` exposes
  existing metadata pages and exact historical bodies in MCP and OpenCode.
  Repository, destination and output limits remain enforced; earlier wording
  does not replace current retrieval or the version required for an edit.
  [Contract](record-history.md#native-tools),
  [verification](verification/native-history-2026-09-10.md).

- **Native argument transport preserves supplied text.** OpenCode now refuses
  malformed Unicode before it can become a different query or context label.
  Native debug and scripted normal-session checks preserve valid Unicode and
  recover with a refused request ID. This repair followed recalled validation
  guidance; incremental memory benefit remains uncertain.
  [Verification](verification/opencode-unicode-2026-09-10.md).

- **Native searches accept changing task context.** MCP and OpenCode can declare
  context for one call when the host left those fields unset. Host-fixed values
  remain enforced. This makes phase-restricted guidance reachable without changing
  configuration between phases; task benefit remains unmeasured.
  [Contract](search-context.md), [verification](verification/search-context-2026-09-10.md).

- **Reviewed failure signatures can retrieve lessons.** An optional supplied digest
  finds the exact current lesson version linked by a reviewed failure, including
  on another task or harness. Hosted associations require explicit review sharing;
  currentness and destination gates, historical replay and retained-run intent are
  verified and installed.
  [Contract](failure-signature-search.md),
  [verification](verification/failure-signature-search-2026-09-10.md).

  A [check of the retained real failure](verification/real-signature-retrieval-2026-09-10.md)
  retrieves its local lesson after an exact-version review in an isolated copy,
  with the historical revision required. Three ordinary text queries already
  rank it first. This adds contract evidence without a retrieval advantage in
  that case or a new model-task benefit claim; further signature expansion awaits
  an observed need.

- **Proposal conversions retain lesson versions.** The operator can require the
  inspected version and inspect earlier conversions after reopening. Legacy pins
  stay unknown; audited forgetting can still erase lesson bodies. This source
  change is verified and installed.
  [Contract](demand-review.md#preserve-the-linked-lesson-version),
  [verification](verification/proposal-versions-2026-09-10.md).

- **Quoted search prefers exact text.** Known paths, identifiers and error messages
  can be quoted in existing queries. Matching source previews, lexical/semantic
  fallback and old receipt behavior are verified through CLI, MCP and native
  OpenCode. The change is installed; E1 remains partial.
  [Contract](quoted-search.md), [verification](verification/quoted-search-2026-09-10.md).

- **Retraction previews include supporting evidence.** Protected local inspection
  now shows source identities, exact citation metadata and integrity state for
  affected retained versions. Complete inventories remain bounded, with no
  preview token on overflow. [Requirement audit](verification/retraction-evidence-2026-09-09.md).

- **Observed runs accept saved task files.** `run --prompt-file` preserves exact
  text through fresh and retained body/index delivery, with an explicit retrieval
  query kept separate. Oversized argv input refuses before consuming a launch.
  [Contract](authenticated-runner.md#read-a-saved-task),
  [verification](verification/run-task-file-2026-09-09.md).

- **Observed runs can deliver compact indexes.** Fresh and retained execution
  supplies previews and direct pull arguments to ordinary harness tools while
  the observer keeps receipt and outcome ownership. Native OpenCode allowed,
  denied and stale-source cases passed with a scripted provider. Aggregate task
  budgeting and task benefit remain open. [Contract](observed-index.md),
  [verification](verification/observed-index-2026-09-09.md).

- **Ordinary notes can retain exact source citations.** Explicit citation updates
  preserve earlier versions; text edits retain references and current pulls expose
  exact captured passages. Notes remain A testimony. Upgrade writers together.
  [Contract](evidence-citations.md#ordinary-notes),
  [verification](verification/ordinary-citations-2026-09-09.md).

- **Malformed JSON Unicode refuses before capture.** Raw invalid UTF-8 and
  lone surrogate escapes can no longer be silently replaced during request
  decoding. Valid Unicode and binary capture remain supported.
  [Verification](verification/json-unicode-integrity-2026-09-09.md).

- **Selected files can be captured directly.** `agent evidence --file` and
  operator `capture-evidence --file` preserve exact bytes up to 1 MiB, with a
  chosen source label and local default. Existing JSON mode remains.
  [Commands](evidence-refresh.md#capture-a-selected-file),
  [checks](verification/file-evidence-capture-2026-09-09.md).

- **Binary evidence has an explicit capture path.** API and CLI accept canonical
  base64 without changing decoded source bytes or the 1 MiB limit. Existing text
  retries remain compatible. [Contract](evidence-refresh.md),
  [verification](verification/binary-evidence-capture-2026-09-09.md).

- **Agy setup reuses the existing MCP server.** Native 1.2.0 registration and
  configuration updates preserve explicit scope in an isolated home. User-wide
  settings require care with concurrent tasks; native tool execution and task
  benefit remain unverified. [Setup](agy.md),
  [evidence](verification/agy-configuration-2026-09-09.md). U7 remains lower priority.

- **Build diagnosis distinguishes CLI, API and facade versions.** Local
  `version` needs no database; `agent version` reports client and server
  independently. MCP initialization identifies its own binary. Unknown stamps
  stay explicit. [Commands](build-identity.md), [checks](verification/build-identity-2026-09-09.md).

- **Ranked search can continue through later matches.** Explicit `offset: 0`
  starts lexical or semantic pages; CLI, MCP and native OpenCode forward later
  `page.next_offset` values. Required instructions and budgets remain per page.
  [Verification and limits](verification/search-pages-2026-09-09.md).

- **Fixed BM25 screening is inconclusive.** On the reused public corpus it
  improves one top-three count but loses some first-place answers. Lexical v4
  remains; further work needs representative long-note failures rather than
  tuning these same questions. [Comparison](verification/bm25-screen-2026-09-09.md).

- **Task-file guidance is now recoverable from saved procedures.** The existing
  OpenCode note now explains `--prompt-file` and newline preservation. A previously
  missed handoff question retrieves it first in lexical and semantic modes, with
  a targeted 897-byte passage. [Coverage correction](verification/task-handoff-guidance-2026-09-09.md).

- **Repeated semantic scoring reuses exact note vectors.** A bounded cache within
  the existing worker avoids re-embedding unchanged eligible notes. Thirteen
  real-model responses match the baseline; six warm queries take 0.034–0.044 seconds
  versus 1.85–2.29 seconds. Current API edits/exclusions and cleanup pass.
  [Comparison and limits](verification/semantic-vector-cache-2026-09-09.md).

- **Ordinary capture accepts explicit applicability.** CLI `--pins` JSON and native
  `cairn_remember` can save phase, revision and other existing restrictions. Omitted
  pins remain unpinned even under a constrained search context. Public MCP and native
  OpenCode verify retries, constrained retrieval and revision preservation.
  Explicitly empty CLI arguments now refuse before capture instead of becoming
  unpinned notes. [Verification](verification/capture-pins-2026-09-09.md).

- **Guidance can be constrained to a task phase.** `--task-phase` distinguishes
  implementation and validation within the same task class. Phase matching covers
  ordinary and mandatory retrieval, inherited restrictions, retained execution
  and existing harness configuration paths. [Contract](currentness-and-replay.md#declared-task-phases),
  [verification and reader upgrade](verification/task-phase-2026-09-09.md).

- **Selected task files can be fingerprinted.** The runner captures labels, sizes
  and SHA-256 after the process outcome through existing observer evidence. File
  contents and actual paths stay out of the manifest; local is the default.
  This identifies reviewed bytes without inferring task acceptance or memory value.
  [Guide](run-artifact-evidence.md), [verification](verification/run-artifact-evidence-2026-09-09.md).

- **Known applicability mismatches take precedence over missing pins.** An
  instruction pinned to a build at revision A no longer blocks a review merely
  because revision is absent. Truly unknown applicability still refuses; private
  exclusions and historical replay remain intact.
  [Repair](verification/applicability-precedence-2026-09-09.md).

- **Explicit compact startup is implemented.** `cairn agent start` preloads an
  ordinary hosted index and task through stdin or argv, then replaces itself
  with the harness. Native OpenCode stdin delivery, direct pulls, permission
  denial and stale-source refusal are verified without model inference. Initial
  budgeting is bounded; whole-task budgeting and observed index runs remain open.
  [Guide](compact-start.md), [report](verification/compact-start-2026-09-09.md).
- **Unconditional system-hook preload remains unsuitable.** Two isolated
  OpenCode sessions showed that its system hook runs for title and main requests
  even when Cairn tools are denied. Existing tool filtering works. The explicit
  launcher provides a separate preload; no unconditional retrieval plugin ships.
  [Investigation](verification/opencode-startup-hook-2026-09-09.md).
- **Optional model reuse is implemented and verified.** Six paired API requests
  saved 0.56–0.83 seconds with identical source identities and candidate-score
  digests. The API releases idle workers and owns cancellation/shutdown; current
  note edits and exclusions remain effective. One-shot workers remain supported.
  [Report](verification/semantic-residency-2026-09-09.md).
- **Use history is accessible beyond its first page.** The installed CLI accepts
  --limit, --offset and --record, preserving the core's oldest-first ordering
  and unknown observations. A 102-exposure fixture verifies continuation and
  filtering. [Report](verification/use-history-cli-2026-09-09.md).
- **Retained runs check kind intent.** The previous filter feature omitted this
  comparison. The installed runner now refuses changed or missing filters before
  launch and supports repeated `--kind` in fresh and retained run commands.
  [Repair and E2 audit](verification/retained-kinds-2026-09-09.md).
- **Saved direction can be found by kind.** Filtered browsing returns the three
  existing priority, value and history notes on one page; previously they were
  on page two. Full pulls and fresh MCP/semantic searches work through the
  installed system. Required context, privacy and budgets remain.
  [Report](verification/kind-filter-2026-09-09.md).
- **Full ordinary note bodies cross JSON transport.** Capture, full-draft edits
  and body-only revisions now allow enough encoded space for maximum 64 KiB
  bodies with sixfold escaping. Decoded limits, other operation caps and retry
  identity remain. The fix is installed and its saved procedure was corrected.
  [Report](verification/note-transport-2026-09-09.md).
- **Evidence spans are deployed.** A 63,044-byte captured source refused a whole
  pull under the ordinary context budget; the CLI/API then retrieved its exact
  31-byte tail with three credits remaining. Selected bytes have their own
  checksum, while full-object identity and destination checks remain. MCP and
  native OpenCode calls, UTF-8/binary boundaries, stale-source refusal, deletion
  exclusions and previous-binary retry compatibility were verified.
  [Report](verification/evidence-spans-2026-09-09.md).
- **Evidence impact is inspectable.** The local operator surface lists exact
  citing versions, known related versions and recorded uses, with independent
  pagination. It preserves historical identities and relation types. Hosted
  profiles cannot read this protected report. It does not authorize mutations
  or infer dependency edges from source labels.
  [Report](verification/evidence-impact-2026-09-09.md).
- **Semantic search is usable but still costs seconds.** All fifteen labelled
  answers in the development corpus were retrieved within three previews; a
  long note's final guidance was found and pulled. No-answer neighbours and some
  first-place regressions remain. Single-input batching reduced median worker
  time from 5.22 to 3.14 seconds on the original seventeen-query comparison.
  The subsequent one-thread experiment preserved all twenty-one score sets but
  missed its latency target, so the installed worker retains two ONNX threads.
  [Discovery](verification/semantic-discovery-2026-09-09.md),
  [batching](verification/semantic-batching-2026-09-09.md),
  [thread comparison](verification/semantic-threads-2026-09-09.md).
- **Everyday harness setup and maintenance work.** Fresh native Codex and
  OpenCode clients have retrieved current notes; both support ordinary edits.
  Browsing reaches older notes across pages. OpenCode's bundled installer removes
  source-checkout copying, and its adapter validates arguments before effects.
  Codex uses project TOML configuration; `codex-config` now generates the entry
  with conversation or explicit task/run scope. Its actual client loaded generated
  files and retrieved exact saved content. The generator does not edit host settings.
  [Codex adoption](verification/codex-project-2026-09-09.md),
  [Codex generator](verification/codex-config-2026-09-09.md),
  [OpenCode installer](verification/opencode-install-2026-09-09.md).

## What has demonstrated usefulness

| Observation | Supported conclusion | Limit |
| --- | --- | --- |
| A real OpenCode model retrieved a storage note saved in a Codex session and correctly answered the source questions. | One useful transfer of selected knowledge across sessions and harnesses. | No baseline advantage or associated host-run outcome. [Report](verification/mcp-2026-09-08.md) |
| After an unusable OpenCode configuration, a stored procedure was corrected; a fresh run retrieved that revision and produced a configuration that connected. | One accepted configuration task after correction and reuse, with retrievals linked to observed runs and separate assessments. | Exploratory before/after observation without no-memory or direct-context controls. [Report](verification/mcp-host-use-2026-09-08.md) |
| A Cairn-fed repair attempt selected and cited a reviewed lesson and avoided an environment regression seen in the baseline. | A bounded positive recurrence observation. | Overall task acceptance remained unknown; direct context also avoided the regression. [Report](verification/reviewed-recurrence-2026-09-08.md) |
| A real failed model run generated an evidence-linked proposal that was reviewed into an ordinary lesson. | The failure-to-review loop works on a real failure. | That reviewed lesson has not demonstrated a successful subsequent task. [Report](verification/observed-model-failure-2026-09-08.md) |

The repair comparisons and model-selected stale-note correction also retained
negative results. Their fixed task/binding cohorts are retired from the active
sequence. Native protocol tests, exact source retrieval, manual note maintenance
and green checks establish narrower capabilities; they do not prove automatic
learning, fewer recurring failures or general task benefit. The
[usefulness inventory](verification/usefulness-status-2026-09-08.md) preserves
those distinctions and the negative evidence.

## Current local installation

Live checks on 2026-09-10 found:

- Installed CLI and project `bin/cairn`: clean `670cf19`; native adapter and
  optional recent-file plugin match that build. API **3ff1fcd**, PID **3062046**,
  remains active without a restart; its existing entity support is sufficient.
  [Entity installation evidence](verification/entity-search-2026-09-10.md)
  records the build, schema and native Codex/OpenCode retrievals.
- Dedicated PostgreSQL **17.10**, migrations **001–034**, PID **163669**.
  The store and API services are active. Data and sockets remain under
  `~/.local/share/cairn`; tests use separate disposable clusters.
- The ordinary hosted-agent profile, Codex configuration, native OpenCode
  connection settings and semantic worker configuration remain in place.
  Native OpenCode uses conversation scope and the six ordinary tools. Existing
  processes keep their loaded adapters and executable builds.
- The semantic worker retains its host-selected five-minute idle lifetime and
  twenty-five-second deadline. The earlier [vector-cache comparison](verification/semantic-vector-cache-2026-09-09.md)
  and [idle-lifetime installation](verification/semantic-idle-2026-09-10.md)
  retain their own measured scope; those results are not a fresh performance
  benchmark of this snapshot.
- The optional recent-file plugin is installed in this project. Native OpenCode
  1.18.21 read a real repository file and retrieved/pulled its existing associated
  note without an explicit query or filename hint. Configuration hashes and
  API/store processes were preserved. See [installed evidence](verification/recent-file-hints-2026-09-10.md#local-installation).

Earlier deployment details remain in the dated verification reports and complete
implementation history below. The summary above replaces the stale September 9
installation summary; it does not revise that historical evidence.

## Verification coverage

The vector-cache extension passed 13 complete real-model comparisons, six numerical
tests, full Go/Python/static and disposable PostgreSQL/race checks with the real
streaming worker. [Implementation CI](https://github.com/halbritt/cairn/actions/runs/34426620798)
passed for `d633c68`. Installed warm queries preserved the complete candidate-score
digest while avoiding repeated corpus embedding. [Report](verification/semantic-vector-cache-2026-09-09.md).

The capture extension passed static/Go/Python and full disposable PostgreSQL/race
integration, including actual native OpenCode tool execution. The installed CLI
revised the existing capture procedure to v2, retried identically and freshly
retrieved the exact body through the unchanged API. The procedure stays unpinned.
[Implementation CI](https://github.com/halbritt/cairn/actions/runs/34425510304)
passed for exact source `215ecbf`. [Capture report](verification/capture-pins-2026-09-09.md).

The installed phase extension passed local Go/Python/static checks and full
PostgreSQL/race integration, with scripted native OpenCode forwarding. Existing
body/index/browse/filtered packages remain identical in old/new retry and replay
checks. [Exact-source CI `8f6864a`](https://github.com/halbritt/cairn/actions/runs/34423942112)
passed every step. Live phase-aware hosted semantic retrieval preserved the saved
applicability lesson; the new usage procedure was captured and freshly retrieved.
[Report and reader-version limit](verification/task-phase-2026-09-09.md).

The installed selected-file extension passed local static checks, Go/Python tests
and full disposable PostgreSQL/race integration. [Exact-source CI `c3256de`](https://github.com/halbritt/cairn/actions/runs/34421756291)
passed every step. A real clean build through the installed observer runner matched
the installed executable; stored manifest and idempotent capture retry were checked
against the unchanged API. [Report](verification/run-artifact-evidence-2026-09-09.md).

The installed applicability repair passed full disposable PostgreSQL/race
integration, all Go and 34 Python tests, vet/format and an old/new binary replay
comparison. [Exact CLI/API-source CI `63f63ed`](https://github.com/halbritt/cairn/actions/runs/34419842234)
passed every step. Local service checks confirmed exact executable bytes,
preserved configuration and hosted semantic retrieval/body identity.
[Repair and deployment](verification/applicability-precedence-2026-09-09.md).

The preceding task-file startup extension passed [exact-source CI `2915571`](https://github.com/halbritt/cairn/actions/runs/34418879903),
including PostgreSQL/race and the authenticated CLI fixture. Local checks preserve
exact file text through both carriers and reject invalid sources before retrieval.
[Report and installation](verification/start-task-file-2026-09-09.md).

The compact-start CLI passed disposable PostgreSQL/race integration, Go unit
and 34 Python tests, vet/format checks, and real CLI/API/native transport checks.
[Earlier compact-start CLI-source CI `a92820a`](https://github.com/halbritt/cairn/actions/runs/34417847517)
passed, including the authenticated startup suite after its database was isolated
from Go test authority state. The [report](verification/compact-start-2026-09-09.md)
retains the initial CI failure, native quoting failure, final checks and deployment.
Native tests used scripted responses with no inference or task-value claim.

The resident-worker source passed disposable PostgreSQL/race integration with
actual CPU scoring, same-store paired API timings and current-source exclusion
checks, plus all Go packages, 34 Python tests, vet and formatting. Additional
transport tests cover cancellation during a blocked write. Local installation
verified exact compiled bytes, hosted retrieval/body identity, reuse and idle
release. Its [report](verification/semantic-residency-2026-09-09.md) separates
those checks from task-value claims.
[Earlier resident-worker API-source CI cf66e1c](https://github.com/halbritt/cairn/actions/runs/34412010428)
passed PostgreSQL/race, Python, vet/build and the CLI history fixture.

The installed use-history CLI passed disposable PostgreSQL/race integration,
Go, 30 Python, vet and formatting. [Exact source CI `ab3306c`](https://github.com/halbritt/cairn/actions/runs/34408184103)
passed those checks and the new 102-exposure CLI history check after build.
The [report](verification/use-history-cli-2026-09-09.md) retains the failed
continuation baseline, verified page/filter coverage and interpretation limits.

The installed retained-kind CLI repair passed real PostgreSQL/child-process and
CLI/Unix API checks, all Go packages, 30 Python tests, vet and formatting.
[Earlier CLI-source CI `3256023`](https://github.com/halbritt/cairn/actions/runs/34406955835)
passed PostgreSQL/race, Python, vet and build. The
[repair report](verification/retained-kinds-2026-09-09.md) retains both the failed
baseline and completed checks.

The installed kind-filter source passed disposable PostgreSQL integration with
Go's race detector, CLI/API, independent MCP and scripted native OpenCode checks.
All Go package tests, 30 Python tests, vet and formatting pass. Unfiltered packages
created by the previous binary retry and reconstruct exactly. The
[feature report](verification/kind-filter-2026-09-09.md) retains the failing
baseline, corrected test mistakes and final result. Its operational check finds
and pulls saved direction through the ordinary hosted profile and exercises the
installed semantic worker. These checks use local embeddings and scripted native
tool responses; they do not run an answering-model task.

[Earlier API-source CI `2d67181`](https://github.com/halbritt/cairn/actions/runs/34405630245)
passed PostgreSQL/race, Python tests, vet and build. Earlier
[native Claude checks](verification/claude-native-tools-2026-09-09.md) retain
separate two-session correction/reuse and refusal evidence. No production
database was used for tests; operational installation checks read existing
notes, service/build information and database schema metadata.

## Remaining work and priorities

1. **Demonstrate durable task value.** Observe meaningful work across turns,
   sessions and tasks, with evidence appropriate to the claimed benefit. Include
   qualitative judgment, contrary observations and costs; an isolated retrieval
   need not prove its own value. Broader native Striatum
   ingress, task/host acceptance and cross-harness transfer remain partial
   (U1, U4/U5, E4, D2). Do not rerun retired cohorts unchanged or infer benefit
   from delivery alone. Codex and OpenCode are sufficient for cross-harness task
   benefit evidence; Agy and Claude Code interfaces (U7/U8) are lower priority
   than increasing task value in those existing harnesses.
2. **Finish practical retrieval and evidence lifecycle.** Managed artifacts above
   the inline limit, broader source relations, richer dependent invalidation and
   source freshness remain open (L4). Byte-span reads do not
   complete that lifecycle. Per-retrieval budgets do not enforce aggregate task
   context; native resume/compaction interlocks remain unverified.
3. **Complete governance and capture boundaries where required.** Richer conflict
   outcomes, complete refusal explanations, C waivers and the full enforcement
   table, plus import quarantine and inherited source restrictions, remain
   partial (R5, L2/L3, L7). Ordinary selected capture is implemented.
4. **Complete retention and deletion scope before broader sensitive use.** Metadata
   and evidence redaction, access-policy transitions, historical-copy adoption,
   audited pruning and the 90-day use-retention policy remain incomplete (L5/L6).
   Keeping data indefinitely preserves the minimum but does not implement forgetting.
5. **Keep recovery work proportional to observed need.** Explicit fencing,
   reapplication and restore admission now exist; reconstructing unknown lost
   state, external-source freshness and broader conformance remain unproved
   (L8–L10). Further speculative recovery engineering is deferred for this
   experimental stage.

The overall goal remains active. Scope and destination checks govern Cairn's
memory delivery; they do not confine a harness's independent filesystem or
network access. There is no H3 claim or automatic grooming/promotion loop.

## Implementation history

This section is a historical record. Earlier descriptions of gaps, migrations,
installed binaries and services describe their original checkpoints; later entries
may supersede them. The current summary above governs today's installation claims.
Keep unsuccessful experiments and corrected assessments visible.

### Earlier narrative, preserved from `37da317`

The following text was the accumulated implementation-status record before the
2026-09-09 summary rewrite. Its original header was dated 2026-09-07, but it had
received additions through 2026-09-08. It is retained here with only heading levels
adjusted; conflicting installation snapshots are historical, not current claims.

Cairn is a usable local alpha. It can retain project notes, retrieve them on a
later run, supply a bounded context package to a process, and show the retained
package and observed outcome afterward. This advances the initial record-only
slice; it does not complete every Stage 1–2 design contract.

The [roadmap](roadmap.md) is the complete requirements and acceptance tracker.

[Cross-record supersession](supersession.md), added on 2026-09-08 in migration
021, preserves exact replacement links and known impact notices while retiring
obsolete A/B records. Its [verification](verification/supersession-2026-09-08.md)
covers authority/scope refusals, concurrency, preserved history and an actual
backup/restore. Migration 022 adds [scope authorization](scope-authorization.md):
a separate C decision can expand applicability while preserving independent
claim qualification and the original read history.

Migration 023 adds [governed repository policy](governed-policy.md): authorized
revision/rollback, frozen policy pins, fresh-delivery checks and affected-run
queries. The first rule format narrows optional-memory budgets while retaining
mandatory enforcement. Waivers and the full instruction-policy table remain open.

#### Contract repairs — 2026-09-07

New semantic v2 receipts retain a query digest; task/query text still reaches
the wrapped process transiently. Advisory B includes visible evidence degradation.
Protected candidate explanations and matching blocked-demand docket entries now
commit with retrieval receipts. Retraction requires an expiring caller-owned
preview and rejects new exposure after preview, including concurrent compile.
These changes use forward migrations 004 and 005. Historical v1 seal verification
is preserved; old retained text is not purged.

The initial repair covered direct uses with a 1,000-use limit. The additions
below now cover known transitive relations and retain named policy refusals;
evidence-object dependencies and full refusal traces remain open. The observation and generated-demand additions below now implement a bounded
part of that loop. See roadmap R1–R5 for completion boundaries and the
[repair verification](verification/contract-repairs-2026-09-07.md) for tests and
local installation evidence.

#### Observation, retrieval and recovery additions — 2026-09-07

Migrations 006–015 add versioned task assessments, declared run bindings, usage
coverage, task-close delegate findings, currentness pins, reviewed failure/recovery
proposals, unsigned audit checkpoints and bounded index expansion. The Unix API
establishes separate authenticated agent/observer channels. Record-specific use
reports reduce repeated observations before joining outcomes and preserve unknowns.

Semantic v3 supports declared currentness; v4 additionally seals compact indexes.
Historical recompilation rebuilds selection from retained candidate facts and
version bytes, verifies the query digest and compares the original seal. Saved v1
receipts still replay. Body pulls recheck eligibility and mandatory bootstrap on
retries and spend per-retrieval credits transactionally. The original index
exposure and later body pull remain distinct observations.

Backups now retain a separately usable expected audit-set catalog. The restore
drill validates it, recompiles both body packages and indexes, and fences restored
handles. Checkpoints cover emitted C/governance and forgetting metadata. Newer
revocation/deletion reconciliation remains unfinished.

A native Striatum coverage trial uses three accepted clauses and 12 committed
subjects in a disposable store. All 12 read sets reconstruct their seals. This
establishes coverage/reproducibility only; broad lexical matches do not establish
relevance, causal benefit, or avoided recurring failures. No live Striatum lane
or model dispatch was changed. See [verification](verification/roadmap-build-2026-09-07.md).

#### Real OpenCode repair trial — 2026-09-07

A [historical repair trial](verification/opencode-recurrence-2026-09-07.md) now
compares a real local model through OpenCode using repository-only, native-excerpt
and Cairn H0 inputs. All three arms failed to produce a repair under the fixed
budget. The corrected H0 arm received both intended B records; two actual use
rows join to rejected trial assessments, with usage unknown and testimony clearly
labelled. Earlier invalid launches and missing memory treatment are preserved.
This advances E4's evidence collection without establishing memory benefit,
Striatum integration or adaptation acceptance.

Two [follow-up calibrations](verification/opencode-calibration-2026-09-07.md)
also failed to repair the task after disabling thinking and increasing context.
A separate local fixture now verifies actual read/edit execution and model-option
forwarding through the wrapper. Local package tests use separate databases and
CI serializes packages to prevent unrelated writes exhausting bounded retries.
Two [hosted calibrations](verification/opencode-hosted-calibration-2026-09-08.md)
also timed out without a repair. The second removed an overly small relay request
limit and reached 20 completed steps. The opt-in hosted path now keeps provider
credentials outside the model sandbox and retains bounded request metadata.
The binding/task pairing still lacks a successful repair baseline; memory benefit
and real Striatum integration remain open.
The [extended-budget follow-up](verification/opencode-extended-calibration-2026-09-08.md)
completed normally after 641 seconds but still made no repair. No memory-benefit
comparison is qualified by that result.

#### Record-body deletion — 2026-09-07

Migration 016 adds [operator forgetting](deletion.md), immediate exclusion of
record/package/cached-response payloads, tombstones, dependency flags and durable
per-target database purge effects. D events join the unsigned checkpoint subset.
Disposable tests cover stale previews, citation races, interrupted worker recovery,
restored pending deletion and explicit backup/provider/metadata residuals. This
is partial L5 implementation; metadata/evidence redaction, historical file adoption,
newer-deletion restore reconciliation and retention remain open.

#### Managed context files — 2026-09-07

Migration 017 registers new run context slots with their directory identity and
ownership marker. The CLI purge worker removes those slots under the writer's
filesystem lock and retains observed completion/failure. Process-death tests
cover unlink before DB commit, retry and restored pending effects. Outcome files
survive. Existing unregistered run directories are not automatically adopted.
See [ownership and remaining limits](managed-context.md).

#### Working paths

Known context-preparation and launch-intent failures now retain a pre-launch
outcome when the database remains available. Regression tests reproduce a parent
that is a file and a pre-existing run-directory symlink: no process starts, no
write crosses the refused path, `not_attempted` appears in the use report, and
the claimed receipt cannot launch again. This fixes a known failure previously
left indistinguishable from an unfinished run; process-death and unavailable-store
ambiguity remain. See [verification](verification/prelaunch-failure-2026-09-08.md).

- A records have retained versions, stamped writers/witnesses, retry identity,
  scoped reads, and compare-and-swap edits. An explicit task/run wildcard lets
  project notes survive a run boundary.
- A one-time operator root supports bounded grants, revocation, independent B
  promotion, C issuance, B correction, and retraction. Privileged transactions
  retry serialization/deadlock failures from fresh state. Deferred constraints
  require matching event subjects/versions and B evidence references.
- Inline evidence is deliberately captured with SHA-256. Retrieval verifies
  its bytes and applies the accepted one-resolvable-reference minimum.
- Delegation reconciliation matches the exact dispatcher, delegate, task, run,
  attempt, and result reference. Failed attempts retain one service observation
  and contradicted claims reach the correction docket.
- The compiler uses a repeatable-read snapshot, direct mandatory selection,
  deterministic lexical/scope/recency ordering, atomic omission of disputed
  optional material, destination filtering, and conservative budgets.
- Canonical CBOR with nanosecond RFC3339 timestamp encoding and BLAKE3 seals
  semantic state. Request IDs, delivery nonces, and receipt timestamps are
  outside the seal. Receipts and exact record exposure commit before return.
- The wrapper supplies stdin or argv before process execution, observes exit
  and duration, hashes output streams, limits process lifetime, and cleans up
  its process group. A receipt can claim only one launch. Pending outcome
  requests survive local DB failure and have an explicit recovery command.
- Local inspection provides list/get, historical replay, reverse exposure,
  explicit usage observations, an outcome report, and a correction/recovery
  docket. None treats exit zero or citation as causal proof of benefit.

#### Evidence collected

`make test-integration` passed with PostgreSQL 17 and Go's race detector. The
baseline had 25 top-level tests; new regression coverage adds capture, legacy
replay, explanation access, blocked demand, and retraction concurrency checks. Tests include the
original schema upgrade, concurrent edits and retry collisions, parent-grant
revocation races, self-promotion, evidence degradation, mandatory overflow,
conflicts, destination canaries, semantic seals, launch failure, timeout, and
duplicate-launch refusal, and durable outcome recovery after store failure. `make check` and `make build` passed.

`make test-lifecycle` passed startup, backup, restore, exact semantic-package
replay, stop, and restart against its own disposable store. The check found and
fixed an inherited lock descriptor that otherwise kept backup/stop waiting on
the running PostgreSQL process. No shared database was used for those tests.

The installed OpenCode 1.18.21 was invoked through Cairn against a local
synthetic OpenAI-compatible endpoint. The compiled canary appeared in the first
model request and the process exited zero. See the
[probe report](verification/opencode-1.18.21.json). The probe used temporary
home/XDG directories, a synthetic provider key, and no real model or user
session. It used the documented [custom provider configuration](https://opencode.ai/docs/providers/#custom-provider)
and [configuration overrides](https://opencode.ai/docs/config/), then verified
the installed binary's behavior rather than assuming the docs proved contact.

A persistent local store was started at `~/.local/share/cairn/socket`, with no
TCP listener. The `cairn` executable was installed in `~/.local/bin`. Three
ordinary repository notes were deliberately captured from Cairn's own docs and
code. A live search selected the relevant notes. A local `cat` task received a
compiled package, and its report recorded one available delivery, one zero
process exit, and one unknown task outcome. A local database backup was created.
This is a working smoke path, not measured model usefulness.

Versioned record relations now retain declared dependencies with scope/currentness/
sensitivity restrictions. B→A preserves consumed history without an authority
transition event and refuses current C/open-conflict dependencies. Retraction
previews include known transitive uses and reject changed dependency state.

Named compiler and lifecycle policy denials now retain caller-owned refusal
observations. They store bounded partial traces and digests, never raw queries or
record bodies. A failed observation commit returns `REFUSAL_UNRECORDED` without
claiming a durable identifier.

Inline evidence refresh now persists check generations and digest observations,
invalidates dependent previews and blocks conversion of proposals with unavailable
evidence. Binary inspection is lossless. Managed artifacts and scheduled refresh
remain open.

#### Current local installation

The current installed executable is clean revision
`6538462f6a4f0c40c4fe3b3475d6dfc691c2ed21`, with migrations 001–024 in Cairn's
dedicated PostgreSQL 17 store. A private dump preceded migration. The running API
matches the installed binary; an authenticated read and unchanged record-version
digest were verified. See [installation evidence](verification/authenticated-runner-2026-09-08.md#installed-host-execution).
No explicit policy was adopted in the operational Cairn repository.

The `cairn-api.service` user service is enabled and requires
`cairn-store.service`. Both are running. Agent and observer profiles have distinct
trusted principals, are limited to the Cairn repository and local destination,
and use owner-only credentials outside the checkout. The installed client created
an ordinary currentness lesson, retrieved its v4 pointer index and expanded its
body with one credit spent. No promotion or task-acceptance claim was inferred.

#### Limits and next work

These are implementation gaps, not questions waiting on the operator:

1. Wire a real Striatum/OpenCode task through declared sealed inputs and actual
   authenticated spawn/terminal and acceptance boundaries. The local Unix API
   and fixture harness tests are prerequisites, not real-host acceptance.
2. Extend the [reviewed recurrence](verification/reviewed-recurrence-2026-09-08.md)
   to broader task/host acceptance and transfer. The H0 run finished within its
   budget and avoided the baseline's inherited-environment regression; overall
   task acceptance remains unknown. The earlier negative trials remain valid.
   Do not enable grooming from this one known-case observation.
3. Complete class-proportional lifecycle, including explicit supersession,
   governed scope/policy changes and richer conflict outcomes. Unreferenced
   ordinary A deletion now works with preserved request identity.
4. Implement evidence lifecycle, class D redaction/deletion effects, retention,
   backup residual accounting and restoration of newer revoked/deleted state.
5. Extend intent matching and qualified expansion to evidence bodies. Current
   credits are per retrieval; a host must budget the whole task and establish
   any mid-run refresh/compaction behavior.

The wrapper retains permitted context files in its private run directory and
does not isolate every native-memory facility. Destination selection is trusted
host/operator configuration, not network confinement. Production receipts report
availability; the controlled OpenCode fixture observed model-request contact.
There is no H3 claim. Legacy raw task/query copies have not been purged.

No automatic backup, pruning or grooming timers are installed. User services
provide managed startup and shutdown; the current machine already has user lingering enabled, so the enabled API
and its required store can run without an interactive login. Use the systemd units for lifecycle
operations on the managed store. The standalone lifecycle script remains the
basis of isolated verification and manual installations.

[Recovery inspection](recovery-inspection.md) exports bounded private withdrawal,
audit and context-custody evidence for comparison with an older database. The
lifecycle drill detects later revocation/forgetting and missing post-backup file
custody in a real restored dump. It also checks mutable exclusion state when
audit events are intact. This read-only report does not reapply restrictions,
prove export freshness or authorize service resumption. L9 remains partial.

Migration 018 adds [restore delivery fencing](restore-fencing.md). Old receipt
claims, binding retries, context registration and pulls refuse after an explicit
operator fence; historical inspection and delayed outcomes survive. The observer
API can reserve a launch once through `claim-run`. A real restored-database API
drill covers the transition. Migration alone does not invalidate operational
receipts, and full restore reconciliation and live Striatum wiring remain open.

Migration 019 adds [standalone task-failure review](demand-review.md); migration
020 adds [ordinary note deletion](ordinary-delete.md). The local installation
runs clean commit `e2648a4ca3019993113465cbf311bd418bca0a49` after a backup and
verified migration. CI, the disposable integration suite and the
backup/restore lifecycle drill passed. The authenticated API runs that binary;
retained operational record versions have the same digest before and after.

The run report includes no-memory baselines and unfinished launch claims as
distinct observations. The installed CLI and authenticated local API return
identical results on the operational store; the reviewed M/N/O trial stores
each return one row with their latest task assessment. No host acceptance or
memory benefit is inferred from report membership.

[Conflict inspection](conflict-inspection.md) now provides scoped open/resolved
lists and exact original positions with resolution history. Current record state
is separate, and forgotten bodies are excluded. The installed CLI and local API
return the same conflict inventory; position/history and deletion checks use
disposable databases. Richer conflict qualification remains open.

#### Instruction category limits — 2026-09-08

Explicit governed `local-loop/3` policy now bounds issued C instructions by
security, workflow and preference count/body-token limits. Mandatory overflow
refuses; optional index admission reserves full instruction bodies. Scope and
retraction preserve category metadata, while old engine seals and request retries
remain compatible. See [contract](instruction-limits.md) and
[verification](verification/instruction-limits-2026-09-08.md). This advances L3;
waivers and the full enforcement table remain open.


#### Authenticated host execution — 2026-09-08

The existing H0 process wrapper now operates through a scoped Unix observer
profile with `cairn agent ... run`. Delivery, launch claims, context registration
and outcomes retain the existing core gates without database credentials on the
client. Lost claim responses preserve receipt metadata and never start a child;
lost outcome responses retain an idempotent pending request. See
[contract](authenticated-runner.md) and [verification](verification/authenticated-runner-2026-09-08.md).
This advances host ingress but does not complete Striatum's sealed integration.

### Commit and verification chronology

The rows retain every commit from repository creation through `95ef2ed`, in commit
ancestry order. Dates and descriptions come from Git; descriptions record what was
claimed at that checkpoint, rather than independently accepting those claims.
Reports are linked at their first introduction, with companion data where present.
Later commits retain corrections and deployment checks. Follow the commit link for
the exact files and report revision at that point.

#### 2026-09-06

| Commit | Change recorded | Verification artifacts introduced |
| --- | --- | --- |
| [8cac00e](https://github.com/halbritt/cairn/commit/8cac00ee1be1f2cdac35e94aff9f898dadb03089) | Start Cairn with PostgreSQL records and delegation reconciliation | — |

#### 2026-09-07

| Commit | Change recorded | Verification artifacts introduced |
| --- | --- | --- |
| [e3b47c7](https://github.com/halbritt/cairn/commit/e3b47c7b5ac70094729d2811f12ec1b05d3bb05c) | Build Cairn's local memory and task delivery loop | [opencode-1.18.21](verification/opencode-1.18.21.json) |
| [f86aa6d](https://github.com/halbritt/cairn/commit/f86aa6dae32442b8a1bbaaeec22ca2aa9677e9e4) | Repair capture and retrieval contracts; add roadmap | [Contract repair verification — 2026-09-07](verification/contract-repairs-2026-09-07.md) |
| [bec6040](https://github.com/halbritt/cairn/commit/bec604068fb5271f441990dc804cc915af74b7e0) | Join memory use to outcomes and add scoped local API | — |
| [afb7373](https://github.com/halbritt/cairn/commit/afb7373e4801bc9095d2a6dacbaa7228e42b6633) | Gate currentness and recompile historical read sets | [Native-history coverage trial — 2026-09-07](verification/history-coverage-2026-09-07.md); [striatum-history-coverage-2026-09-07](verification/striatum-history-coverage-2026-09-07.json) |
| [0f94a09](https://github.com/halbritt/cairn/commit/0f94a09ed74ba165177631d44773ceee184bf37a) | Add evidence-attached review and expected audit restore sets | — |
| [d959763](https://github.com/halbritt/cairn/commit/d9597638ff6920e403acf000578566f772a4968f) | Add sealed pointer indexes and bounded authorized body pulls | — |
| [677cac9](https://github.com/halbritt/cairn/commit/677cac9869e8e0433aea8f1fc6b7f37c41f32369) | Respect instruction applicability and add managed local services | [Roadmap build verification — 2026-09-07](verification/roadmap-build-2026-09-07.md) |
| [0e9a728](https://github.com/halbritt/cairn/commit/0e9a7281bc3c8b352487972d01e4b488251b3613) | Preserve versioned dependencies through demotion and retraction | — |
| [df1fa61](https://github.com/halbritt/cairn/commit/df1fa619e9f13957a007774fb5848e6d33843ff0) | Retain bounded policy refusals with explicit persistence failures | — |
| [1e6917a](https://github.com/halbritt/cairn/commit/1e6917a43250a0baf20a796a1ce9cb1616344b6b) | Persist evidence refresh and guard proposal conversion | — |
| [001ca95](https://github.com/halbritt/cairn/commit/001ca955d1a670df24b7e97689cdc98c196296ea) | Add audited record forgetting and resumable database purge effects | — |
| [498c079](https://github.com/halbritt/cairn/commit/498c07942991fb4cf94139919481e2eb4aada300) | Register run context ownership and recover external purge effects | — |
| [e8620de](https://github.com/halbritt/cairn/commit/e8620de41469e4bd1de4a56e929eba63671ba82e) | Inspect older restores against external withdrawal evidence | — |
| [be012b1](https://github.com/halbritt/cairn/commit/be012b12665b3cb554d5185ef33df52d107ae912) | Fence restored launch capabilities without rewriting run history | — |
| [7001a88](https://github.com/halbritt/cairn/commit/7001a8878ca3a5f3f96c297a4d169560e4210d51) | Add real OpenCode recurrence trial and preserve failed outcomes | [OpenCode repair trial — 2026-09-07](verification/opencode-recurrence-2026-09-07.md) ([data](verification/opencode-recurrence-2026-09-07.json)) |
| [d299990](https://github.com/halbritt/cairn/commit/d2999904a3a04316659f5c54eb5b41864918ce3c) | Keep task outcome unknown when the repair evaluator cannot run | — |
| [a2bdf41](https://github.com/halbritt/cairn/commit/a2bdf41a87b4aaa4037c5d6cdc660639397c1ce6) | Calibrate OpenCode execution and isolate integration test writers | [OpenCode harness calibration — 2026-09-07](verification/opencode-calibration-2026-09-07.md) ([data](verification/opencode-calibration-2026-09-07.json)) |
| [e2f68cb](https://github.com/halbritt/cairn/commit/e2f68cbe6601aef01f0843c6e81d5a7b4639e89a) | Add bounded hosted OpenCode calibration and retain failed trials | [Hosted OpenCode calibration — 2026-09-08 UTC](verification/opencode-hosted-calibration-2026-09-08.md) ([data](verification/opencode-hosted-calibration-2026-09-08.json)) |
| [155f999](https://github.com/halbritt/cairn/commit/155f9995bc23c401b3d175d4f55eb837a4e61b8c) | Test oversized relay requests before uploading refused bodies | — |
| [dfa6308](https://github.com/halbritt/cairn/commit/dfa63082ce812f75e104b7a54e4fb18ce0be2d82) | Retain known pre-launch preparation failures without unsafe file writes | [Known pre-launch failure — 2026-09-08](verification/prelaunch-failure-2026-09-08.md) |
| [9deed65](https://github.com/halbritt/cairn/commit/9deed657573991acafc2c646faf7098bd3896aae) | Record extended hosted calibration and explicit work budgets | [Extended work-budget calibration — 2026-09-08](verification/opencode-extended-calibration-2026-09-08.md) ([data](verification/opencode-extended-calibration-2026-09-08.json)) |
| [c07f458](https://github.com/halbritt/cairn/commit/c07f4587d907485e72b3537586afeaa30c861934) | Diagnose hosted repair limits and retain the first candidate regression | [Hosted repair and diagnostic review — 2026-09-08](verification/opencode-pro-calibration-2026-09-08.md) ([data](verification/opencode-pro-calibration-2026-09-08.json)) |
| [30495e1](https://github.com/halbritt/cairn/commit/30495e1ab0ac706fba59b900345ab5b764a989fe) | Declare reviewed recurrence lesson and correct workspace criteria | — |
| [c7d06bd](https://github.com/halbritt/cairn/commit/c7d06bdbe65e4fe12f79c3703731a9371fdaed44) | Check runtime cache writes and explicit environment overrides | — |
| [94d2109](https://github.com/halbritt/cairn/commit/94d210979010e0a43cc274ee977a52b477d970b2) | Preserve the inherited count-marker regression as a separate check | — |
| [cae3410](https://github.com/halbritt/cairn/commit/cae34107ba1f84af96ec45502543853116ce7e8d) | Count latest task assessments in the operational summary | [Summary assessment correction — 2026-09-08](verification/summary-assessments-2026-09-08.md) ([data](verification/summary-assessments-2026-09-08.json)) |
| [13e8f8f](https://github.com/halbritt/cairn/commit/13e8f8f440f869727a46d86934edc6c7154abf0b) | Record bounded positive H0 recurrence and reviewed outcome joins | [Reviewed-lesson recurrence — 2026-09-08](verification/reviewed-recurrence-2026-09-08.md) ([data](verification/reviewed-recurrence-2026-09-08.json)) |
| [7bc9165](https://github.com/halbritt/cairn/commit/7bc9165b0b58dc46b0bc5021e62ea276c3c0affb) | Make standalone task failures reviewable without an accepted recovery | [Standalone task-failure review](verification/standalone-demand-2026-09-08.md) ([data](verification/standalone-demand-2026-09-08.json)) |
| [cbcad73](https://github.com/halbritt/cairn/commit/cbcad737537a0d1f9c23faafe8fc928f54511416) | Record verified standalone-review installation and current trial limits | — |
| [d61769d](https://github.com/halbritt/cairn/commit/d61769decbc69fed875a6b668b20a6dc155a9b8a) | Delete unreferenced ordinary notes without retry resurrection | — |
| [f29694b](https://github.com/halbritt/cairn/commit/f29694b5ba2d07b76b04368efc533565a1f75ea4) | Record ordinary-deletion deployment and remaining lifecycle work | — |
| [9c65d32](https://github.com/halbritt/cairn/commit/9c65d324c105d4bb4a0c4b4db53b6881300f1057) | Report run outcomes independently of memory exposure | [run-report-2026-09-08](verification/run-report-2026-09-08.json) |
| [b3aeb51](https://github.com/halbritt/cairn/commit/b3aeb51008721eb708f8e3581a044c99e5616239) | Record run-report deployment and authenticated report parity | — |
| [e2648a4](https://github.com/halbritt/cairn/commit/e2648a4ca3019993113465cbf311bd418bca0a49) | Inspect retained conflict positions and resolution history | — |
| [e85972e](https://github.com/halbritt/cairn/commit/e85972e30eb5cfb6f57abbd2b55b58d82ac1d3cf) | Record verified conflict-inspection installation | [conflict-inspection-2026-09-08](verification/conflict-inspection-2026-09-08.json) |

#### 2026-09-08

| Commit | Change recorded | Verification artifacts introduced |
| --- | --- | --- |
| [f6d64c6](https://github.com/halbritt/cairn/commit/f6d64c65a4259f8933cb6016eec8296e85c91a2f) | Allow withdrawal after a dependency is forgotten | [Withdraw an instruction after its source is forgotten](verification/withdrawal-after-forgetting-2026-09-08.md) |
| [4136844](https://github.com/halbritt/cairn/commit/413684428344ba79553b75fc194c0926ffe14885) | Record verified withdrawal repair installation | — |
| [db33296](https://github.com/halbritt/cairn/commit/db33296d6e9119901e1a3d4f4570078f51c35a1e) | Supersede obsolete claims with pinned independent replacements | [Cross-record supersession verification](verification/supersession-2026-09-08.md) |
| [676b4ae](https://github.com/halbritt/cairn/commit/676b4ae2bfa02aad300c587b55417eaa9794bfc2) | Compare supersession retry timestamps by instant | — |
| [4e04131](https://github.com/halbritt/cairn/commit/4e041317587fc8edabfddbc4556c8a9af85991b9) | Record verified supersession upgrade and usage | — |
| [a8623d6](https://github.com/halbritt/cairn/commit/a8623d6b48793463d0bb6bd7dd01d2db23917ad9) | Authorize broader scope without renewing claim qualification | [Scope authorization verification](verification/scope-authorization-2026-09-08.md) |
| [cefc4aa](https://github.com/halbritt/cairn/commit/cefc4aaadd61678a4e631bab30538599dda2422d) | Record verified scope authorization installation | — |
| [a721e94](https://github.com/halbritt/cairn/commit/a721e94b7355622d841d01e6106832f96a5c4795) | Respect private instruction applicability before hosted refusal | [Private instruction applicability verification](verification/private-policy-applicability-2026-09-08.md) |
| [74a54e1](https://github.com/halbritt/cairn/commit/74a54e17351889a84f937c7a87e68d9c762ea402) | Record installed hosted policy applicability repair | — |
| [b694d7f](https://github.com/halbritt/cairn/commit/b694d7f42a4db565e5ee0dd9f79bc85a62f51246) | Govern repository policy revisions, rollback and affected runs | [Governed policy verification](verification/governed-policy-2026-09-08.md) |
| [56b3422](https://github.com/halbritt/cairn/commit/56b34227817e65b1e5c05e66cc7e73e113843986) | Record verified governed policy installation | — |
| [35543dd](https://github.com/halbritt/cairn/commit/35543dd6136d10766d097f78677d62e31902ef45) | Bound instruction categories through governed policy and indexed delivery | [Instruction limits verification](verification/instruction-limits-2026-09-08.md) |
| [7a874d1](https://github.com/halbritt/cairn/commit/7a874d197e2dd2ed029c05180662d74059f70254) | Record installed instruction limits and existing host admission lead | — |
| [6538462](https://github.com/halbritt/cairn/commit/6538462f6a4f0c40c4fe3b3475d6dfc691c2ed21) | Run harnesses through scoped observer API without database access | [Authenticated runner verification](verification/authenticated-runner-2026-09-08.md) |
| [37da317](https://github.com/halbritt/cairn/commit/37da31754dd526c251a6a65de0ff4cd49323d788) | Record deployed observer runner and actual repository validation | — |
| [b1eeb42](https://github.com/halbritt/cairn/commit/b1eeb42444a2908f0530536f64b0d3dfa11999f6) | Inspect owned run status without protected reports or launch authority | [Owner-only run status verification — 2026-09-08](verification/run-status-2026-09-08.md) |
| [6341935](https://github.com/halbritt/cairn/commit/6341935ae66e855d54f3f912a84c65a4d6ce2148) | Record deployed owner-only recovery inspection | — |
| [b976ae7](https://github.com/halbritt/cairn/commit/b976ae7ae75d156bfb127decfba882e8babab3f7) | Bind wrapped runs to exact host-observed attempts | [Host attempt linkage verification — 2026-09-08](verification/host-attempt-link-2026-09-08.md) |
| [409df41](https://github.com/halbritt/cairn/commit/409df41e91d40b2455c0ff7b89c211780987c487) | Record deployed attempt linkage and real host build evidence | — |
| [f30082e](https://github.com/halbritt/cairn/commit/f30082e2e95430ca5318eb2c354373abb0eefd68) | Reconcile native memory admission with current Striatum evidence | [Native Striatum admission assessment — 2026-09-08](verification/native-admission-assessment-2026-09-08.md) ([data](verification/native-admission-assessment-2026-09-08.json)) |
| [25c339d](https://github.com/halbritt/cairn/commit/25c339d44871a19f199bcddd315ddc1c4d6dfd2a) | Connect OpenCode trials to authenticated host attempts | [OpenCode host observation — 2026-09-08](verification/opencode-host-observation-2026-09-08.md) ([data](verification/opencode-host-observation-2026-09-08.json)) |
| [5494e54](https://github.com/halbritt/cairn/commit/5494e54d7832d2302b65692d6b3f813e282906bf) | Record observed model failure and reviewed lesson conversion | [Observed model failure and review — 2026-09-08](verification/observed-model-failure-2026-09-08.md) ([data](verification/observed-model-failure-2026-09-08.json)) |
| [c34f2db](https://github.com/halbritt/cairn/commit/c34f2db9e0c2582223ce8238bb0d4adae66e43e4) | Group matching failure proposals for source-specific review | [Exact-signature proposal review groups](verification/proposal-groups-2026-09-08.md) |
| [4eb9d7d](https://github.com/halbritt/cairn/commit/4eb9d7dcf7de12a80964a7e96b8facabc99aca16) | Record proposal grouping release verification | — |
| [521e759](https://github.com/halbritt/cairn/commit/521e759f88ce0ab66d00594d28f180d259822f7e) | Preserve forgotten-source exclusions through later relations | [Forgotten-source dependency repair](verification/deletion-dependencies-2026-09-08.md) |
| [a0d54a8](https://github.com/halbritt/cairn/commit/a0d54a8dfdb1daf1d542d4df0bb27164515c0dea) | Record deletion dependency migration release | — |
| [0835e0c](https://github.com/halbritt/cairn/commit/0835e0c7a19ef94fbc0158dde6ab2d684443f330) | Reapply recovery withdrawals with current authority and retained provenance | [Recovery reapplication verification — 2026-09-08](verification/recovery-reapplication-2026-09-08.md) |
| [9e57ff7](https://github.com/halbritt/cairn/commit/9e57ff7e2c181b8c5f188dfa4357114fea396cc8) | Record recovery reapplication release and remaining admission gap | — |
| [c370481](https://github.com/halbritt/cairn/commit/c370481d99ec1354abaac75d45146d2e4fca3c36) | Gate restored service on explicit current-root reconciliation and resume | [Restore admission verification, 2026-09-08](verification/restore-admission-2026-09-08.md) |
| [0fb924a](https://github.com/halbritt/cairn/commit/0fb924ae6c9ee54efd8e48cf3ab6882bc5ff4bc4) | Record restore admission release and remaining recovery limits | — |
| [dd83bce](https://github.com/halbritt/cairn/commit/dd83bcee93c11c8c3bd03d7b8d836c1eefdac312) | Let indexed consumers pull exact supporting evidence within shared budgets | [Supporting-evidence expansion verification, 2026-09-08](verification/evidence-expansion-2026-09-08.md); [Demonstrated usefulness, 2026-09-08](verification/usefulness-status-2026-09-08.md) |
| [754d088](https://github.com/halbritt/cairn/commit/754d088468d89d2fad0c8d453ec8bd677a5c642f) | Record evidence retrieval release and preserve usefulness limits | — |
| [aba227d](https://github.com/halbritt/cairn/commit/aba227dddabf5f2c7c317e235c83bc6c36b33cf6) | Freeze documentation questions and add opt-in retrieval measurement | — |
| [a154257](https://github.com/halbritt/cairn/commit/a154257bfe2550064f11ab19a865c8d7ed1fc9a4) | Match identifier words and record measured retrieval limits | [Documentation retrieval quality, 2026-09-08](verification/retrieval-quality-2026-09-08.md) ([data](verification/retrieval-quality-2026-09-08.json)) |
| [be71b2c](https://github.com/halbritt/cairn/commit/be71b2c374eca73f6628422275fc3af6590d3d78) | Freeze bounded model-directed retrieval experiment and verify its tool route | — |
| [763d6e8](https://github.com/halbritt/cairn/commit/763d6e8e2ecbfdb5630c395979192211b76d343f) | Pair experimental retrieval results with their complete pull commands | — |
| [acb1ca2](https://github.com/halbritt/cairn/commit/acb1ca27586476362abca5d01f2d31c0fbef2358) | Record actual agent query refinement and paired-pull results | [Model-directed retrieval, 2026-09-08](verification/tool-retrieval-2026-09-08.md) ([data](verification/tool-retrieval-2026-09-08.json)) |
| [321b3cb](https://github.com/halbritt/cairn/commit/321b3cb6e6c7e2ae6d7237b391e4ac12cf3c4d50) | Associate dynamic retrievals with observed host run outcomes | [Dynamic retrieval to host outcome correspondence](verification/run-retrieval-2026-09-08.md) |
| [4eb498f](https://github.com/halbritt/cairn/commit/4eb498f1bfd5b51e49585c07849722fcaf0997dd) | Record deployed retrieval association verification | — |
| [46eda16](https://github.com/halbritt/cairn/commit/46eda160e240985b16c2d514e2e5e577ca859525) | Expose authenticated agent search and paired pull commands | — |
| [45e37cf](https://github.com/halbritt/cairn/commit/45e37cf9d55c0b85c93e1b5532780a05cb03b4d1) | Record native agent retrieval and retained format rejection | [Reusable agent search and pull](verification/agent-search-2026-09-08.md) ([data](verification/agent-search-2026-09-08.json)) |
| [0e7d8cc](https://github.com/halbritt/cairn/commit/0e7d8ccd326432189cb8cbf774da4064228d1ac1) | Freeze matched agent connection repair experiment | — |
| [f4c5fe3](https://github.com/halbritt/cairn/commit/f4c5fe3bf1e59ec3dc83d723de7c18bb3a694ade) | Resolve agent connection defaults only when needed | — |
| [95612c0](https://github.com/halbritt/cairn/commit/95612c0c37ab3a618b4919f81990786332c1d38c) | Retain inconclusive repair comparison and classify request limits correctly | [Explicit agent connections and the inconclusive transfer experiment](verification/agent-env-2026-09-08.md) ([data](verification/agent-env-2026-09-08.json)) |
| [6a21da9](https://github.com/halbritt/cairn/commit/6a21da9f4a2b587ecca49b4ad22077a077acea44) | Add authenticated note capture with shared remember parsing | — |
| [2a4591e](https://github.com/halbritt/cairn/commit/2a4591ec9ef6019cec8df18cb11350dac819db66) | Record operational note capture and provisioned hosted access | [Authenticated note capture in everyday use](verification/agent-remember-2026-09-08.md) ([data](verification/agent-remember-2026-09-08.json)) |
| [2d526a1](https://github.com/halbritt/cairn/commit/2d526a17ed175a194d5e80c9a0af7fb85905012a) | Describe eligibility rechecks accurately in body pulls | — |
| [26b43d1](https://github.com/halbritt/cairn/commit/26b43d1ec2e8503fb107009daa176783068b849b) | Record verified pull explanation repair and retry compatibility | [Body pulls describe their eligibility recheck](verification/pull-reason-2026-09-08.md) ([data](verification/pull-reason-2026-09-08.json)) |
| [3491974](https://github.com/halbritt/cairn/commit/349197446247bd73d2e004c5b191a688dccb98ec) | Filter question framing words from versioned lexical retrieval | [Question-word retrieval change, 2026-09-08](verification/question-words-2026-09-08.md) ([data](verification/question-words-2026-09-08.json)) |
| [76a0144](https://github.com/halbritt/cairn/commit/76a01448663af3a067efdb48ce9f049aa7ab4b32) | Record deployed retrieval filter and operational storage note use | — |
| [196ac73](https://github.com/halbritt/cairn/commit/196ac7340219091a8dc2c2711979da7ef02da0f6) | Expose authenticated memory tools through MCP and verify native OpenCode use | [Native MCP tools and operational note transfer, 2026-09-08](verification/mcp-2026-09-08.md) ([data](verification/mcp-2026-09-08.json)) |
| [af8caaf](https://github.com/halbritt/cairn/commit/af8caafa2bed3d0647d8ea546c4a7778839f6e41) | Record installed MCP facade and operational capture verification | — |
| [cda6762](https://github.com/halbritt/cairn/commit/cda676257538b9f5b6ca415ea7caacd57ae5e243) | Forward host retrieval context through MCP startup configuration | [MCP retrieval context, 2026-09-08](verification/mcp-currentness-2026-09-08.md) ([data](verification/mcp-currentness-2026-09-08.json)) |
| [f889cdf](https://github.com/halbritt/cairn/commit/f889cdff766740fbd21e11f1a9d76c1ca1f02658) | Record installed MCP context forwarding and live compatibility check | — |
| [78ce807](https://github.com/halbritt/cairn/commit/78ce80706e790dacaeea000d3821a21c19998834) | Record native MCP task outcomes and successful curated procedure reuse | [Native MCP configuration task and follow-up, 2026-09-08](verification/mcp-host-use-2026-09-08.md) ([data](verification/mcp-host-use-2026-09-08.json)) |
| [ea2324f](https://github.com/halbritt/cairn/commit/ea2324f2c8cd350c568cb7b7aede7157ff428e57) | Generate native OpenCode MCP configuration from shared startup flags | [Deterministic OpenCode configuration, 2026-09-08](verification/opencode-config-2026-09-08.md) ([data](verification/opencode-config-2026-09-08.json)) |
| [1fb50ad](https://github.com/halbritt/cairn/commit/1fb50adc4863c46ef3aad55d436151f774520405) | Record installed OpenCode configuration generator and operational verification | — |
| [b511f26](https://github.com/halbritt/cairn/commit/b511f267b02ea79f151d63f4912351e2ae0fae96) | Show matching source passages in memory search previews | [Matching index previews, 2026-09-08](verification/index-previews-2026-09-08.md) |
| [c2481d4](https://github.com/halbritt/cairn/commit/c2481d4d9e96ca216a3bb66102bebf7af394d895) | Record installed matching-preview verification | [index-previews-2026-09-08](verification/index-previews-2026-09-08.json) |
| [d4bbbf6](https://github.com/halbritt/cairn/commit/d4bbbf6c330ebb3a607c5139a06198b5e03b1784) | Expose ordinary memory revisions through MCP | [Ordinary note edits through MCP, 2026-09-08](verification/mcp-edit-2026-09-08.md) |
| [96d4137](https://github.com/halbritt/cairn/commit/96d4137673cdeba5546a2b853e82ac6d3a310092) | Record installed MCP correction and fresh-session retrieval | [mcp-edit-2026-09-08](verification/mcp-edit-2026-09-08.json) |
| [19093a7](https://github.com/halbritt/cairn/commit/19093a7260822957339a62fa83d5722705e56563) | Define native Cairn input direction and record captured Striatum intent | [native-input-proposal-2026-09-08](verification/native-input-proposal-2026-09-08.json) |
| [71abeea](https://github.com/halbritt/cairn/commit/71abeeaf66eec3e96961ff45c8920acb5f9ee8bc) | Record native contract drafts and concrete launch prerequisites | [native-amendments-2026-09-08](verification/native-amendments-2026-09-08.json) |
| [99b2d93](https://github.com/halbritt/cairn/commit/99b2d93b614ab3232c5b8c52693d1a17ada19068) | Recheck retained memory eligibility before claiming a launch | — |
| [b1846a5](https://github.com/halbritt/cairn/commit/b1846a59b96885d8be5a2971a705d26f2cbba82f) | Record checked and installed launch freshness behavior | [Retained context eligibility at launch, 2026-09-08](verification/launch-freshness-2026-09-08.md) ([data](verification/launch-freshness-2026-09-08.json)) |
| [93bc66a](https://github.com/halbritt/cairn/commit/93bc66a972222fa47dec97e87beecdb5e82b8a82) | Execute exact retained memory packages through the authenticated runner | — |
| [37ec151](https://github.com/halbritt/cairn/commit/37ec151eacb5901b80c57040b7123d6f0af14124) | Record retained execution verification and native supervisor boundary | [Retained execution verification](verification/retained-execution-2026-09-08.md) ([data](verification/retained-execution-2026-09-08.json)) |
| [40e8519](https://github.com/halbritt/cairn/commit/40e85198658eeea73a1e303e9e227cc8fc01a0f0) | Record native capture tooling and real service verification | [Native host capture verification](verification/native-capture-2026-09-08.md) ([data](verification/native-capture-2026-09-08.json)) |
| [3a86d62](https://github.com/halbritt/cairn/commit/3a86d62e0413d6b53b190815b89563788cc5ce6f) | Track tested draft Striatum observation producer | — |
| [55f6769](https://github.com/halbritt/cairn/commit/55f67690ff95495423e42b3c74235d1fcca0fe29) | Record native observation issuance and admission verification | [Native observation producer and request frontend](verification/native-observation-2026-09-08.md) ([data](verification/native-observation-2026-09-08.json)) |
| [1e4faea](https://github.com/halbritt/cairn/commit/1e4faea0b8098b4255c3a7c9a480ece062d0cc7c) | Record native build input and prompt correspondence checkpoint | [Native build input and prompt correspondence](verification/native-build-input-2026-09-08.md) ([data](verification/native-build-input-2026-09-08.json)) |
| [f466d79](https://github.com/halbritt/cairn/commit/f466d791344758e2ba0c9a3db7e925c617d45431) | Record native host launch and correspondence verification | [Native host launch and retained correspondence](verification/native-host-2026-09-08.md) ([data](verification/native-host-2026-09-08.json)) |
| [b125e08](https://github.com/halbritt/cairn/commit/b125e087ba53a3fcf5fd549332fa1ceb11e9b36e) | Record the native observation-to-build integration test | [Cairn observation through native build admission](verification/native-chain-2026-09-08.md) ([data](verification/native-chain-2026-09-08.json)) |
| [ea2cd03](https://github.com/halbritt/cairn/commit/ea2cd03dd310c09583fbcc12b5ab24d015f4078f) | Calibrate frozen source and oracle for native memory recurrence | [Native recurrence source and oracle calibration](verification/native-recurrence-preflight-2026-09-08.md) ([data](verification/native-recurrence-preflight-2026-09-08.json)) |
| [55c8ff3](https://github.com/halbritt/cairn/commit/55c8ff360fce8bf5a2e148c7e805912061551cac) | Run frozen memory recurrence through native Driver supervision | — |
| [588f6af](https://github.com/halbritt/cairn/commit/588f6aff26f8fd67931ec35c5376688821a5c7e9) | Record native executor calibration and runtime toolchain fix | [Native recurrence executor](verification/native-executor-2026-09-08.md) ([data](verification/native-executor-2026-09-08.json)) |
| [409bbbc](https://github.com/halbritt/cairn/commit/409bbbccc39996c1687c9f4ee26e4cbfe9ee7b11) | Correct native scratch permissions and require actual harness calibration | [Native model recurrence and scratch permission correction](verification/native-permissions-2026-09-08.md) ([data](verification/native-permissions-2026-09-08.json)) |
| [ff62caa](https://github.com/halbritt/cairn/commit/ff62caa781542b67911787169f728648e1a91e66) | Include native model contact in the usefulness evidence inventory | — |
| [800705d](https://github.com/halbritt/cairn/commit/800705d313d7e4c1ff34a2a73ca49d46cf47d249) | Report native trial transport failures and partial usage | — |
| [d374280](https://github.com/halbritt/cairn/commit/d3742808ff7e2f3017735f09eba6445b12879d89) | Retain and verify the operational scratch-permission lesson | — |
| [4ed3a19](https://github.com/halbritt/cairn/commit/4ed3a198a8af987f6a32c77afdadad62846332e3) | Record corrected native outcomes and verify Codex MCP retrieval | [Native Codex MCP connection](verification/codex-mcp-2026-09-08.md) ([data](verification/codex-mcp-2026-09-08.json)); [Corrected native repair comparison](verification/native-corrected-recurrence-2026-09-08.md) ([data](verification/native-corrected-recurrence-2026-09-08.json)) |
| [a798f17](https://github.com/halbritt/cairn/commit/a798f1769040147c464f1f57768ba9c90891f579) | Verify native Codex capture and revision of shared memory | [Codex maintains shared Cairn notes](verification/codex-maintenance-2026-09-08.md) ([data](verification/codex-maintenance-2026-09-08.json)) |
| [06a61bb](https://github.com/halbritt/cairn/commit/06a61bb2e9870c2f4f6f2f3c28d815e39732d9da) | Support native Codex conversation scope for MCP search | [Codex conversation scope](verification/codex-thread-2026-09-09.md) ([data](verification/codex-thread-2026-09-09.json)) |
| [be5f945](https://github.com/halbritt/cairn/commit/be5f9459868745c0975bcccbde8a3187a3a6c9b4) | Verify installed Codex scope and revised setup guidance | — |
| [86f3298](https://github.com/halbritt/cairn/commit/86f3298bf15e933cee30856906978f2c1a7bdb19) | Enable and verify Cairn in the local Codex project | [Local Codex adoption](verification/codex-project-2026-09-09.md) ([data](verification/codex-project-2026-09-09.json)) |
| [3285367](https://github.com/halbritt/cairn/commit/32853678f29b932128f9e31cf7204db123e44d61) | Add native OpenCode session tools backed by Cairn CLI | [Native OpenCode session tools](verification/opencode-tools-2026-09-09.md) ([data](verification/opencode-tools-2026-09-09.json)) |
| [fcf5948](https://github.com/halbritt/cairn/commit/fcf5948e77b8788763426dc6a2a1e5675b50900c) | Verify shared procedure revision across native OpenCode and Codex | — |
| [a5ee749](https://github.com/halbritt/cairn/commit/a5ee749d34e326d78cc6b37dd6191df6b7712ba1) | Retain and verify the native tool defaults lesson | — |

#### 2026-09-09

| Commit | Change recorded | Verification artifacts introduced |
| --- | --- | --- |
| [d457b29](https://github.com/halbritt/cairn/commit/d457b294f4dc7445128b3554f36e092dc83352db) | Validate native OpenCode tool arguments before side effects | [OpenCode argument validation repair](verification/opencode-validation-2026-09-09.md) ([data](verification/opencode-validation-2026-09-09.json)) |
| [cd4027f](https://github.com/halbritt/cairn/commit/cd4027fa7ce6bf640d4b781450428355d3e8cd26) | Expose bounded memory browsing through agent interfaces | [Explicit browsing through agent interfaces](verification/browse-2026-09-09.md) ([data](verification/browse-2026-09-09.json)) |
| [75e7595](https://github.com/halbritt/cairn/commit/75e7595e81d437cd59a2c07fd63f4b5e9f0b89c1) | Reach older memory through bounded browse continuation | [Browse continuation, 2026-09-09](verification/browse-pages-2026-09-09.md) ([data](verification/browse-pages-2026-09-09.json)) |
| [7928355](https://github.com/halbritt/cairn/commit/792835558be49737ae434f64d58a23d9516c1f01) | Revise note bodies while preserving stored metadata | [Body-only note revision, 2026-09-09](verification/body-revision-2026-09-09.md) ([data](verification/body-revision-2026-09-09.json)) |
| [8650902](https://github.com/halbritt/cairn/commit/86509025c131054a62216d2235021f48067a33cf) | Record stale-note correction results and clarify edit guidance | [Stale-note correction exercise, 2026-09-09](verification/note-correction-2026-09-09.md) ([data](verification/note-correction-2026-09-09.json)) |
| [81fc308](https://github.com/halbritt/cairn/commit/81fc30802ffb7df74739221d989ca7c0ada0356b) | Evaluate local semantic retrieval against documented lexical misses | [Local semantic ranking, 2026-09-09](verification/semantic-retrieval-2026-09-09.md) ([data](verification/semantic-retrieval-2026-09-09.json)) |
| [ecbcc54](https://github.com/halbritt/cairn/commit/ecbcc54c5ae0c177ea83bab660b083f5ba8f29ab) | Add optional local semantic discovery through scoped index and pull | [Integrated semantic discovery, 2026-09-09](verification/semantic-discovery-2026-09-09.md) ([data](verification/semantic-discovery-2026-09-09.json)) |
| [d2a0e84](https://github.com/halbritt/cairn/commit/d2a0e848f43ebe53630bb808dda13f30c137d80d) | Verify deployed semantic search and source pulls in both native clients | — |
| [87356a8](https://github.com/halbritt/cairn/commit/87356a88cd81d2cc9899a4286784830ed2457e49) | Reconcile roadmap status with installed semantic discovery | — |
| [7beaaab](https://github.com/halbritt/cairn/commit/7beaaab53980c4b5326c81e9e9e35ce432842b97) | Record source-checked maintenance of operational harness procedures | [Operational note review, 2026-09-09](verification/operational-note-review-2026-09-09.md) ([data](verification/operational-note-review-2026-09-09.json)) |
| [aedc016](https://github.com/halbritt/cairn/commit/aedc0163d03d89385f1840b48102d9d0b1b82c7f) | Install matching native OpenCode tools from the Cairn binary | [Bundled OpenCode installation, 2026-09-09](verification/opencode-install-2026-09-09.md) ([data](verification/opencode-install-2026-09-09.json)) |
| [5eb9d0f](https://github.com/halbritt/cairn/commit/5eb9d0f20a924218fa399674aecf9893843b0ed4) | Verify installed native tools from a fresh project directory | — |
| [b846d04](https://github.com/halbritt/cairn/commit/b846d04610c9950b6083933c9f0d1765bc13be84) | Retain partial candidate diagnostics for refused retrievals | [Partial diagnostics for refused retrievals](verification/refusal-details-2026-09-09.md) ([data](verification/refusal-details-2026-09-09.json)) |
| [19fbfb7](https://github.com/halbritt/cairn/commit/19fbfb7f5665e6685c3ceb411cf059c6702727f6) | Verify deployed refusal diagnostics and hosted access boundary | — |
| [4bfe53a](https://github.com/halbritt/cairn/commit/4bfe53a24b59e23f3dcb1cb69db3bddf168f0f47) | Reduce semantic worker cost with single-input batches | [Lower-cost local semantic inference](verification/semantic-batching-2026-09-09.md) ([data](verification/semantic-batching-2026-09-09.json)) |
| [3c53591](https://github.com/halbritt/cairn/commit/3c535912f5826cf1548defd033d2261e58c53c64) | Verify installed semantic batching on live hosted retrieval | — |
| [6e604ce](https://github.com/halbritt/cairn/commit/6e604ce24e1697d939dbd9f17893bc9fa8c752fe) | Expose evidence-linked versions and recorded uses for local inspection | [Evidence impact inspection](verification/evidence-impact-2026-09-09.md) ([data](verification/evidence-impact-2026-09-09.json)) |
| [9f985e6](https://github.com/halbritt/cairn/commit/9f985e6cacaea783da311309104a2c09a346838a) | Verify deployed evidence inspection and hosted boundary | — |
| [45ca33b](https://github.com/halbritt/cairn/commit/45ca33b9bd2431bffe9c83a44efa51de134d7220) | Read bounded evidence spans through agent interfaces | [Bounded evidence reads](verification/evidence-spans-2026-09-09.md) ([data](verification/evidence-spans-2026-09-09.json)) |
| [fec0f2c](https://github.com/halbritt/cairn/commit/fec0f2c2b9db478240f76c301990c8dee37cc8b7) | Record deployed evidence span verification | — |
| [19facc1](https://github.com/halbritt/cairn/commit/19facc16c4a131aaf17370e0bbf93805852dce13) | Measure semantic thread tradeoff and retain current worker | [Semantic worker thread comparison](verification/semantic-threads-2026-09-09.md) ([data](verification/semantic-threads-2026-09-09.json)) |
| [95ef2ed](https://github.com/halbritt/cairn/commit/95ef2ed640fa4c558b847e5c3fafc12bfac68126) | Refresh implementation status with current capabilities and evidence | — |

### 2026-09-09 — history restoration and requested harness interfaces

The status refresh in `95ef2ed` replaced the accumulated chronology with a current
summary. The owner clarified that implementation-status should carry the complete
history. This update restores the earlier narrative and links every intervening
commit and verification artifact. Future corrections must preserve the earlier
claim and explain what supersedes it.

The owner also requested Agy and Claude interfaces. Roadmap U7/U8 now specify
ordinary native access, repeatable setup, actual client verification and observed
cross-harness use. Advanced H1 behavior and resume/compaction interlocks remain
separate requirements. These interfaces are planned, not implemented.

### 2026-09-09 — repeatable Codex configuration

`cairn codex-config` now prints an ordinary MCP TOML entry with explicit connection
settings, either conversation or task/run scope, and optional startup by default.
It shares argv construction with the existing OpenCode generator and preserves
its output contract. The command reads no credential contents and edits no host
configuration. This supersedes the earlier "generator under investigation" status.

The actual Codex CLI 0.153.4 loaded generated files and pulled the exact saved
procedure in two conversations with distinct native scopes and one explicit task/run
scope. Quotes and Unicode in the executable path worked. Independent TOML parsing
verified literal values and control-character escaping. Go tests, 30 Python tests,
vet, formatting and build passed. No model turn or new task-benefit result was
claimed. [Verification](verification/codex-config-2026-09-09.md) and
[manifest](verification/codex-config-2026-09-09.json).

### 2026-09-09 — task value takes priority over adapter breadth

After requesting Agy and Claude interfaces, the owner clarified that the two
existing harnesses are sufficient to demonstrate cross-harness task benefit. This
supersedes the earlier placement of additional adapters in the active sequence:
U7/U8 remain requested, open and lower priority than useful task progress with
Codex/OpenCode. Further adapter work needs either that progress or a concrete task
that requires the missing harness. The verified Codex setup command is being
completed; it is setup capability, not a new task-benefit result.

### 2026-09-09 — value evidence is broader than mechanical proof

The owner clarified that the objective is convincing evidence that memory creates
task value, without restricting value to what a mechanical evaluator can prove.
Qualitative, indirect and delayed benefits remain eligible. Roadmap evaluation
guidance now calls for task context, source evidence, observed behavior/artifacts,
explicit judgment and alternative explanations; comparisons and tests are used
where they fit the claim. No single score, citation count or known task oracle is
the product objective. Unexpected benefits may be investigated as exploratory
evidence. This does not upgrade any previous trial's acceptance or causal claim.

### 2026-09-09 — Codex setup installation completed

The history correction was committed as `aa0a845`, the verified generator as
`0ffe2dd`, and the task-value priority clarification as `9e6f6d3`. A clean CLI build
of `9e6f6d3` is installed; the API remains on `45ca33b` without a restart or schema
change. Existing host configuration hashes are preserved. Native generated-file
checks passed against the installed build in both scope modes. The selected
ordinary Codex procedure now includes the command at version 6, verified by a fresh
scoped pull. [Installation evidence](verification/codex-config-2026-09-09.md#local-installation).

### 2026-09-09 — cumulative value across sustained work

The owner cited confidence in Pincite's benefit across multiple model turns despite
no single-turn value proof. The roadmap now explicitly admits longitudinal
evaluation across turns, sessions and related tasks: improved decisions, consistency,
less rediscovery and correction burden may emerge over time. Practitioner judgment
can motivate and support a qualified assessment; it is not silently converted into
a causal result or prior proof of Cairn's value. Evaluation cost also counts.

### 2026-09-09 — retain owner direction for later work

A bounded hosted-profile review found technical procedures and lessons in twelve
eligible previews, but no preview covering the latest priority, evaluation and
history corrections. Three selected ordinary source-linked notes now retain those
decisions/preferences. Fresh task-scope searches pulled the exact notes; the
priority wording needed the existing semantic route after a lexical miss. Agent
guidance now explicitly includes material owner decisions and corrections in
selective capture and task-start retrieval. This is a workflow improvement and
prospective continuity benefit, not proof of a prevented recurrence: the current
session already knew the decisions. No ranker or evaluation subsystem was added.
[Review and limits](verification/project-continuity-2026-09-09.md),
[metadata](verification/project-continuity-2026-09-09.json).

### 2026-09-09 — read bounded excerpts of long saved notes

A disposable-store reproduction accepted a 60,042-byte note but could not pull it
under the fixed 24,000-byte expansion ceiling. The existing pull operation now
accepts explicit A/B byte ranges, returns separately labelled partial text and
full/selected hashes, and preserves full-source checks and shared credits. Class C
instructions remain whole. A selected tail is now readable without increasing the
ceiling. The existing evidence-span lesson pointed to the interaction and guards;
current code was checked before applying it.

Disposable PostgreSQL/race integration, actual CLI/API, MCP and native OpenCode
checks passed, including earlier-binary whole-response retries. Range, source
corruption, stale version, caller/destination, shared-credit and forgetting checks
cover the new path. Go tests, 30 Python tests, vet and build passed. The change
adds source-reading capability; task-value and longitudinal effects remain to be
observed. [Verification](verification/note-spans-2026-09-09.md) and
[manifest](verification/note-spans-2026-09-09.json).

### 2026-09-09 — note excerpts installed and verified

Implementation `559307c` is pushed and installed as the clean CLI and running API,
with its matching native OpenCode adapter. The API restarted; existing host settings,
semantic worker and schema were preserved. A fresh native Codex conversation read
a full existing note and its exact excerpt, with identical retry and correct shared
credits. Exact-commit CI passed PostgreSQL/race, Python, vet and build checks.
[Deployment](verification/note-spans-2026-09-09.md#local-deployment) records the
versions and checks. This supersedes the earlier split CLI/API installation
snapshot, while preserving its history. The current priority summary also now
states the cumulative evaluation horizon already established in the roadmap.

### 2026-09-09 — review a qualitative task-value case

The owner clarified that absence of single-turn or mechanically provable benefit
must not exclude qualitative and cumulative value. A retrospective review of the
OpenCode argument-validation repair now follows the retrieved lesson, applicability
check, malformed-write reproduction, shipped fix and corrected guidance across
harnesses. Historical source and all 31 private artifact hashes match the original
manifest. The repair is verified; the coding agent assesses recalled guidance as
a plausible useful contribution to the investigation. Existing context and ordinary
review remain alternative explanations, and net benefit is unmeasured.

This adds a [qualified case](verification/value-case-validation-2026-09-09.md) with
[review metadata](verification/value-case-validation-2026-09-09.json), preserving
the original report and all task outcomes. It does not establish independent
replication, general task benefit or later benefit from the corrected note.
Assessment guidance now explains how to retain narrative judgments, inspect their
full history and review a sequence without assigning it a synthetic task outcome.
Existing fields suffice for this case; no evaluator, schema, runtime or permission
change was made. Documentation links, source claims, hashes and historical
preservation were checked. No code tests or model cohorts were rerun.

### 2026-09-09 — authenticated assessment history

Following the qualitative review, an actual API check found that authenticated
agents and observers could write assessments but could not retrieve their history.
The CLI rejected the operation and both API fixtures returned an unknown endpoint.
The existing history is now available through `agent assessments` with a receipt
ID. It preserves complete versions, reasons, evidence IDs and observer/witness
under owner, repository and receipt-destination checks. A linked retrieval does
not grant access to its host's assessment. No score, schema or native model tool
was added, and existing outcomes remain unchanged.

Disposable API and CLI checks, PostgreSQL/race integration, Go tests, 30 Python
tests, vet and build passed. This completes access to existing review data;
improved review decisions remain to be observed. [Verification](verification/assessment-history-2026-09-09.md)
and [manifest](verification/assessment-history-2026-09-09.json) retain the evidence.

### 2026-09-09 — assessment history installed

Clean `9c8c22b` is installed as the CLI and running API. Both services are active
following the API restart; host settings, adapters and the semantic worker are
unchanged. The hosted client read its owned empty history with home lookup and
client database access disabled. Exact-commit CI passed. A selected ordinary
procedure describing the new read and its limits was saved and pulled exactly
from a fresh task scope. [Deployment details](verification/assessment-history-2026-09-09.md#local-deployment)
include the checks and retained metadata. No operational assessment was written;
no task-value claim is added by this deployment.

### 2026-09-09 — evidence transport reaches the existing inline limit

A source-maintenance review found that core evidence capture permits 1 MiB of
decoded source while the agent CLI, client and server each limited the entire
JSON request to 128 KiB. A disposable API reproduction failed on a valid full-size
source requiring JSON escaping. Evidence capture alone now allows an 8 MiB encoded
envelope through one shared transport limit. Other requests retain 128 KiB; decoded
source and pull budgets are unchanged.

Exact full-size source and SHA-256, retry identity, decoded/envelope refusals before
mutation, direct HTTP boundaries and real CLI capture passed. The full disposable
PostgreSQL/race integration, Go tests, 30 Python tests, vet and build passed.
This completes the existing capture range, without adding managed artifacts,
source freshness or task-value evidence. [Verification](verification/evidence-limit-2026-09-09.md)
and [manifest](verification/evidence-limit-2026-09-09.json) retain the findings.

### 2026-09-09 — evidence transport repair installed

Clean `283142b` is installed as the CLI and running API. Both services are active
after the API restart, a hosted read passed, and host settings, adapters and
semantic worker are preserved. No schema change or operational large-source
capture was performed. Exact-commit CI passed PostgreSQL/race, Python, vet and
build. The existing ordinary evidence procedure now includes the capture limit
at version 2; a fresh scope pulled the exact corrected body.
[Deployment evidence](verification/evidence-limit-2026-09-09.md#local-deployment)
records the update and its limits. This supersedes the preceding runtime snapshot
without changing the historical verification or task-value judgments.

### 2026-09-09 — precise supporting citations

New promotion/correction references now retain the validated full-source digest
and optional exact byte passages. Qualified scope expansion carries them forward;
correction and historical recompilation preserve earlier versions. Existing
ID-only writes pin whole-source identity, while pre-migration links retain absent
citation metadata. Returned references can drive existing bounded evidence pulls.
Source replacement remains divergent against an earlier citation even if the
replacement object's own digest verifies. See the [contract](evidence-citations.md),
[verification](verification/evidence-citations-2026-09-09.md) and
[manifest](verification/evidence-citations-2026-09-09.json).

Full disposable PostgreSQL/race, CLI/API/MCP, Go, 30 Python tests and vet/build
passed. An actual schema-29 previous-binary fixture retained promotion retry,
cached pulls and exact historical recompilation after migration 030. The existing
oversize fixture was corrected to capture its large source before citation;
source replacement has a separate integrity test. This is a practical source
inspection capability, not a new accepted model task or measured memory benefit.
L4 remains partial for larger managed sources, broader relations, source freshness
and richer dependent qualification. Deployment is recorded separately below.

### 2026-09-09 — citation migration installation and maintained guidance

Clean `3e08c18` is installed as the CLI and running API after a verified backup
and migration 030. Both services are active, the store process is unchanged, and
Codex/OpenCode settings, native adapter and semantic runtime files retain their
previous checksums. An authenticated hosted read succeeded. The operational store
had zero qualified evidence references, so legacy preservation is demonstrated by
the disposable upgrade rather than by operational citation use.

The existing evidence-span lesson is now v3, with earlier capture guidance retained
and the precise citation contract added. A fresh task/run scope read its exact full
body. This is maintained guidance and tested retrieval, not an additional accepted
model task or demonstrated memory contribution. The [verification report](verification/evidence-citations-2026-09-09.md)
and [manifest](verification/evidence-citations-2026-09-09.json) retain deployment,
backup, runtime and note checksums.

[Exact implementation CI](https://github.com/halbritt/cairn/actions/runs/34389972263)
passed PostgreSQL race tests, Python tests, vet and build.

### 2026-09-09 — consolidate accumulated harness procedures

The existing Codex procedure v6→v7 and OpenCode procedure v8→v9 now present
current setup and maintenance instructions with source links in place of repeated
release-by-release updates. Combined bodies decreased from 13,044 to 7,820 bytes;
previous bodies remain exact in retained version history. Native/MCP differences,
profile choice, scope, permissions, source inspection, editing and retry guidance
were preserved in a source review. Documented setup commands ran successfully,
and the installer wrote its matching adapter in a temporary project.

Both existing setup queries still rank their target first. Fresh scopes pulled the
exact revisions, old handles refused as stale, and note metadata stayed unchanged.
See the [review](verification/procedure-maintenance-2026-09-09.md) and
[manifest](verification/procedure-maintenance-2026-09-09.json). Clearer organization
is the coding agent's editorial judgment; smaller bodies are measured. Task
benefit, general retrieval quality and net maintenance cost remain unestablished.
No code, installed runtime, host settings or automatic grooming changed.

### 2026-09-09 — authenticated retained record history

`history` now exposes bounded newest-first version metadata and one explicitly
requested earlier body through the ordinary API/CLI. The previous procedure review
needed operator SQL for that comparison. Current repository/destination rules,
record and per-version deletion exclusions, and restore admission govern the read.
Responses label historical intent and distinguish the current class/version from
the original version. No source copy, migration, mutation/use receipt, ranking
change or additional native tool is introduced.

The absent-route baseline now passes; disposable checks cover exact source/hash/
writer/time, cursor continuation across an append, metadata-only pages, earlier
classes, private/foreign/forgotten refusals and unchanged deletion eligibility.
Full PostgreSQL/race integration and additional CLI/exclusion/restore checks pass,
as do Go tests, 30 Python tests and vet/build. See the [contract](record-history.md),
[verification](verification/record-history-2026-09-09.md) and
[manifest](verification/record-history-2026-09-09.json). Software checks establish
access capability; installation and real maintenance use are recorded separately.

### 2026-09-09 — installed ordinary history review

Clean `9fb278b` is installed as CLI/API; both services are active with the existing
store process and migration 030. Host settings, native adapter and semantic
runtime files retain their previous checksums. The ordinary hosted profile read
metadata plus exact Codex v6/v7 and OpenCode v8/v9 bodies, matching the preceding
maintenance review's four source hashes without operator SQL or database access.
This completes that practical inspection step through ordinary access; it does
not establish an independent model-task benefit.

A short selected history-review procedure was saved as
`cd6217ad-2f69-45f1-bcfb-a01066909855` v1 and pulled exactly in a fresh task scope.
Existing harness procedures were not expanded again. The [verification report](verification/record-history-2026-09-09.md)
and [manifest](verification/record-history-2026-09-09.json) retain installed-source,
review and guidance evidence. No storage, restore, grooming or authority policy
was added by this read-only interface.

[Exact installed-source CI](https://github.com/halbritt/cairn/actions/runs/34392942583)
passed PostgreSQL race tests, Python tests, vet and build.

### 2026-09-09 — retained-version review repairs procedure omissions

Ordinary authenticated history reads of Codex v6 and OpenCode v8 exposed details
lost in the preceding consolidation: the `offset: N` continuation argument and
semantic worker/setup prerequisites. The previous preservation claim was too
broad. Codex v8 and OpenCode v10 restore these instructions, same-scope browsing
and lexical search guidance. Combined size is now 8,327 bytes; the earlier 7,820
bytes remains the historical consolidation measurement.

Current source supports the corrections. An ordinary CLI browse continued at the
returned offset; fresh task/run searches and full pulls verified the exact revised
bodies and preserved metadata. See the [follow-up review](verification/procedure-maintenance-2026-09-09.md#follow-up-restore-omitted-instructions).
Raw operational material remains in `/tmp/cairn-procedure-review/`. This is an
actual correction assisted by retained guidance, with no observed downstream
failure, independent comprehension comparison or measured net benefit. No code,
runtime, host configuration or prior outcome assessment changed.

### 2026-09-09 — preview positions connect search to bounded reads

New A/B search previews include the source byte range accepted by the existing
excerpt tool. A long-note baseline found the command but could not locate its
passage; it now reads those exact bytes from the returned position. The CLI/API,
MCP and native OpenCode checks exercise that path. Instructions remain whole,
and currentness, destination, integrity and expansion budgets stay unchanged.

New index packages use schema 8 and count the added metadata in packing. Actual
previous-binary schema-3/5/6/7 fixtures preserve historical recompilation, including
frozen semantic scores and a cached pull. A fixture's wrong-owner read was refused
and corrected without changing access policy. Full disposable integration/race,
Go, 30 Python tests and vet/format checks passed. See the
[verification](verification/preview-locations-2026-09-09.md) and
[manifest](verification/preview-locations-2026-09-09.json). Task benefit and net
context savings remain unmeasured. Deployment is recorded separately.

### 2026-09-09 — installed preview locations and exact passage retrieval

Clean `fe59de9` is installed as CLI/API with its bundled OpenCode adapter. Both
services are active, with the existing store process and migration 030. Connection
settings, Codex configuration and semantic runtime files retain their previous
checksums. An ordinary hosted search located a passage in the existing OpenCode
v10 procedure; a pull using the returned offset 3,736 and length 154 returned
those exact bytes and retained three credits. Its retry was identical.

The [deployment report](verification/preview-locations-2026-09-09.md#local-deployment)
and [manifest](verification/preview-locations-2026-09-09.json) retain checksums and
source correspondence. No operational note or prior task assessment changed.
This verifies practical source inspection; memory's task contribution and net
context benefit remain separate questions.

[Exact implementation CI](https://github.com/halbritt/cairn/actions/runs/34395712027)
passed PostgreSQL/race, Python tests, vet and build.

### 2026-09-09 — cumulative maintenance value review

The contiguous `ec76c91..e51c3c0` sequence now has a
[source-linked cumulative review](verification/cumulative-maintenance-value-2026-09-09.md)
and [metadata](verification/cumulative-maintenance-value-2026-09-09.json).
All 55 referenced artifact entries across its three original manifests matched
their retained hashes; exact old/consolidated/corrected bodies and the installed
passage response were also checked. The review retains the positive observation
of recovered guidance and the negative observation that consolidation lost it.

The coding agent judges this useful continuity within Cairn development. Net
benefit, independent reuse and broader task improvement remain uncertain; supplied
CLI task/run names are not evidence of independent model sessions. The next
priority is using existing interfaces on an independently worthwhile accepted
task, with further inspection machinery justified by concrete task needs. This
changes work selection rather than historical assessments or roadmap completion.
No code, runtime, operational memory, model trial or authority policy changed.

### 2026-09-09 — ordinary note JSON transport

Ordinary create/edit/revise now allow 512 KiB encoded envelopes for the existing
64 KiB decoded bodies across API, agent and trusted operator JSON ingress. MCP
and native OpenCode use the same paths. Evidence remains 8 MiB; other operations
retain their existing caps. The prior evidence feature's ordinary 128 KiB boundary
is intentionally superseded for these three operations only.

The [report](verification/note-transport-2026-09-09.md) and
[manifest](verification/note-transport-2026-09-09.json) preserve the failing
baseline, exact body and prior-binary retry checks, refused oversize requests
before effects, and full disposable integration/race, Go/Python/vet/build results.
An initial native debug-output JSON parse failure remains recorded; the corrected
normal-session fixture passed without inference. No schema, permission, model
trial or task assessment changed. The recalled analogous evidence procedure
helped frame verification; incremental memory value remains uncertain.

### 2026-09-09 — installed ordinary note transport and corrected guidance

Clean `6dbca8b` is installed as CLI/API, with both services healthy and the store
process unchanged. The native adapter, harness configuration and semantic worker
files retain their prior checksums. No migration or operational test fixture was
needed. The existing evidence procedure's capture paragraph now describes the
ordinary 512 KiB envelope; v4 was saved with metadata preserved and read back
exactly in fresh authenticated scope. The same revision request retries exactly.
[Deployment evidence](verification/note-transport-2026-09-09.md#local-deployment)
and [metadata](verification/note-transport-2026-09-09.json) retain source/binary
identities and the actual procedure checks. This completes deployment of the
transport repair; it adds no independent model-task benefit claim.

[Exact implementation CI](https://github.com/halbritt/cairn/actions/runs/34399146044)
passed PostgreSQL/race, Python tests, vet and build.

### 2026-09-09 — reconciled current installation snapshot

The previous deployment entry correctly recorded `6dbca8b`, but the current
installation and verification summaries still named `fe59de9` and its CI run.
Those summaries now match the installed CLI and running API. Fresh checks of
both executable hashes, clean build revision, service state, PostgreSQL version
and migrations, adapter identity and exact implementation CI confirmed the
current state. The latest ordinary-note verification and 30 Python tests now
appear in the summary; each earlier report retains its original coverage.

This corrects a documentation mismatch introduced by appending deployment history
without refreshing its current snapshot. All earlier history remains intact.
No runtime, operational note, code or task assessment changed in this correction.

### 2026-09-09 — Claude Code configuration generator

`claude-config` now emits the existing five-tool MCP interface in Claude Code's
JSON format with explicit task/run scope. It reuses shared path/context assembly,
refuses invalid literal arguments and unsupported Codex metadata scope, and
leaves credentials, host settings and permissions untouched. The
[setup guide](claude-code.md), [report](verification/claude-config-2026-09-09.md)
and [metadata](verification/claude-config-2026-09-09.json) record the boundary.

Installed Claude Code 2.1.265 accepted and connected to the generated entry in
an isolated home. The generated launch also searched and retrieved exact revised
content through an independent MCP client. Full disposable integration/race,
Go tests, 30 Python tests, vet and build passed. No model inference or operational
fixtures were used. U8 advances to partial setup support; native Claude-selected
use, session attribution and a useful Claude task remain open. This requested
lower-priority adapter work adds no new task-value evidence or outcome correction.

### 2026-09-09 — installed Claude setup generator

Clean CLI `cee290e` is installed and generates an owner-only task-specific
configuration. It did not register or change the owner's Claude settings.
The API stays on `6dbca8b`; this generator needs no service restart or schema
migration. Both services remain healthy, and Claude/Codex/OpenCode settings and
adapter identities are unchanged. The
[installation report](verification/claude-config-2026-09-09.md#local-installation)
and [manifest](verification/claude-config-2026-09-09.json) retain the separate
CLI/API identities. The current snapshot has been refreshed with this distinction.

A concise ordinary setup lesson was also saved and freshly pulled through the
provisioned hosted-agent profile. It retains explicit scope requirements and
native-use limits with source pointers. No operational test fixture, owner Claude
registration or accepted task assessment was created. Capture and exact retrieval
establish continuity support, not independent downstream benefit.

[Exact CLI source CI](https://github.com/halbritt/cairn/actions/runs/34400988285)
passed PostgreSQL/race, Python tests, vet and build.

### 2026-09-09 — native Claude tool execution and input-boundary finding

The [native tool check](verification/claude-native-tools-2026-09-09.md) and
[metadata](verification/claude-native-tools-2026-09-09.json) now cover actual
Claude execution of all five ordinary tools across two distinct native sessions.
Exact capture/edit/pull/evidence results, retries, stale/version refusals, hosted
filtering and a permission-denied edit with no stored effect passed. Responses
were scripted locally; no inference or model-selected task result is claimed.

A second run failed an assumed refusal for textual `shareable: "false"`. The
completed comparison showed direct MCP rejection, native acceptance as a local
note, and refusal of unrecognized boolean text. The test now checks the actual
storage/disclosure boundary and retains the failed assumption. No production
validation fix was warranted. The final 18-case native check and full disposable
integration/race passed; Go tests, 30 Python tests and vet/format also passed.
Installed binaries, service processes and schema are unchanged. U8 remains
partial pending useful native task evidence; historical assessments are untouched.

### 2026-09-09 — retained native Claude verification in setup guidance

The existing Claude setup lesson was revised v1→v2 with its metadata preserved.
It now records the native scripted checks and observed boolean input adjustment,
while retaining the unverified model-use/task-value boundary. Exact retry and a
fresh full pull were checked through ordinary hosted access. The
[follow-up evidence](verification/claude-native-tools-2026-09-09.md#operational-follow-up)
and [metadata](verification/claude-native-tools-2026-09-09.json) retain the change.
Installed CLI/API binaries, the API process and owner harness settings remain
unchanged. No operational test record or task assessment was introduced.

[Exact verification-source CI](https://github.com/halbritt/cairn/actions/runs/34403218213)
passed PostgreSQL/race, Python tests, vet and build. Native Claude checks ran
separately on the owner host against disposable data.

### 2026-09-09 — find project direction using existing kind labels

An ordinary direction lookup put recent procedures/lessons on the first browse
page and the desired decisions/preferences on the next. A lexical query for
`project decisions preferences` returned setup guidance and one direction note.
The [kind-filter implementation](verification/kind-filter-2026-09-09.md) and
[verification metadata](verification/kind-filter-2026-09-09.json) retain that
baseline and the new optional selection contract.

Existing compile/index, agent/trusted search and native search now accept kind
selection. Filters apply before optional ranking and paging while required
instructions, applicability, authority, privacy and budgets remain. Filtered
requests retain normalized intent in semantic v9; unfiltered v3/v8 requests and
previous-binary retries stay exact. No migration or additional tool is needed.

Fail-first store cases exposed the gap; final PostgreSQL/race, Go, 30 Python,
vet/format, CLI/API, independent MCP and scripted native OpenCode checks pass.
Two intervening failures were test cleanup order and historical-envelope
comparison mistakes, corrected without runtime changes. Doctrine receipt and
citation closure are retained, with 13 nonmaterial residuals. This verifies
retrieval capability; model-selected use and incremental task benefit remain
open. The local installation snapshot above remains the prior deployment until
the separately recorded installation check completes.

### 2026-09-09 — install and exercise kind selection on saved direction

Clean `2d67181` is installed as CLI/API, with the bundled OpenCode
adapter updated. Both services are healthy, the store process is unchanged, and
migrations remain 001–030. Host connection settings and semantic worker hashes
are preserved; the previous executable and adapter remain available locally.

One filtered browse at the ordinary 32,000-token allowance found all three
existing direction notes that had required the second unfiltered page. Three
full pulls verified their source checksums and exact retries, leaving one credit.
A fresh MCP session returned the same note versions, and an actual filtered
semantic query reported `ready`. No new notes, model tasks or task assessments
were created. This establishes the lookup behavior and a shorter preview path
for this case; broader task benefit remains open. The current installation
snapshot above now reflects this deployment, superseding the preceding source-only
entry's pending-installation statement.

Exact installed-source CI run [34405630245](https://github.com/halbritt/cairn/actions/runs/34405630245)
completed successfully. The feature's local native and operational checks remain
separate evidence from CI. The complete preceding implementation history is
preserved.

### 2026-09-09 — repair retained kind intent and clarify E2

An E2 acceptance review found that the previous kind-filter feature omitted the
runner's retained-intent comparison and matching run flags. Real child-process
checks reproduced launch under changed or omitted kinds. The
[repair and acceptance review](verification/retained-kinds-2026-09-09.md) and
[metadata](verification/retained-kinds-2026-09-09.json) retain the failure and
verified correction. Shared normalization now checks kinds before binding/launch;
fresh and retained trusted/authenticated run commands accept repeated --kind.
Equivalent sets preserve exact retained bytes and caller input.

PostgreSQL/race, CLI/API, Go, 30 Python, vet and formatting checks pass. This is
self-created rework, with no claim that memory caused discovery or delivered net
benefit. Existing retained-launch guidance was read after finding the defect.
The original kind-filter retrieval checks remain valid within their stated scope.

E2 remains partial: generic H0 runner and retained execution explicitly reject
index packages, and native tools do not establish automatic compact startup or
aggregate task budgeting. The roadmap now records those exact gaps. Source
changes are verified; the current installation snapshot remains the preceding
deployment until the separate CLI installation check.

### 2026-09-09 — install retained-kind repair and preserve the correction

Clean `3256023` is installed as the CLI; the API stays on `2d67181`.
No service restart, schema, host configuration or adapter change was needed.
The installed CLI recognizes the new flag and refuses invalid kind intent;
valid fresh/retained behavior is covered by the disposable real-process checks.

The ordinary retained-context lesson advanced v2→v3, preserving its complete old
body as a prefix and all metadata. Its appended correction names the missed
intent check, shared normalization and run flags, and explicitly says the old
lesson was read after discovery. Exact mutation retry and a fresh full pull
verified the saved version. This is useful guidance retained for future work;
it does not establish that memory discovered the defect or produced net savings.
The current installation snapshot now reflects the CLI-only deployment.

Exact installed-CLI-source CI [34406955835](https://github.com/halbritt/cairn/actions/runs/34406955835)
completed successfully. The API remains on its separately verified source. Full
prior implementation history and the earlier note versions remain preserved.

### 2026-09-09 — expose complete use-history navigation in the CLI

The trusted use-report command hardcoded the first 100 exposure rows, even though
the core/API already supported continuation and record filtering. Source review
confirmed oldest-first ordering, correcting an initial assumption that the hidden
rows were older. The [CLI history change](verification/use-history-cli-2026-09-09.md)
and [metadata](verification/use-history-cli-2026-09-09.json) retain the actual
102-exposure failure and verified continuation/filter behavior.

The command now accepts --limit, --offset and --record while preserving defaults,
response shape, core validation, permissions and unknown observations. All 102
exposure pairs and one record's 51 rows are reachable in the unchanged disposable
fixture. The real CLI check joins local integration and CI. PostgreSQL/race,
Go, 30 Python, vet and formatting pass locally. No production report, model task
or task assessment was used. Existing assessment guidance informed documentation's
interpretation limits; net memory benefit remains unestablished. The source is
verified; installation and CI are recorded separately after completion.

### 2026-09-09 — install use-history CLI continuation

Clean `ab3306c` is installed as the CLI; the API remains on `2d67181`.
Both services, host settings, native adapter and semantic worker remain unchanged.
The installed help exposes --limit, --offset and --record; actual report behavior
is verified using disposable history. No protected operational report, memory
revision or task assessment was used to validate this CLI change. The current
snapshot now records this deployment while retaining every previous entry.

Exact installed-source CI [34408184103](https://github.com/halbritt/cairn/actions/runs/34408184103)
completed successfully, including the new CLI history fixture against its service
database. The change provides access to retained exposure rows; it does not close
U4's broader history analysis or establish memory task benefit.

### 2026-09-09 — measure semantic startup after kind filtering

The [small-set startup investigation](verification/semantic-startup-2026-09-09.md)
and [metadata](verification/semantic-startup-2026-09-09.json) retain phase timings,
prototype results and an interleaved comparison. A recalled performance lesson
pointed to the earlier inference-dominated batching profile. Source review
confirmed that recent kind filtering supports a different workload: one/three
optional notes. The retired thread experiment was not rerun.

Six baseline/instrumented pairs preserved complete responses. Imports/model setup
took 0.51–0.63 seconds, over half the sampled one-note worker time. A scratch
resident-model process preserved twelve further responses. Its initial separated
timing blocks were followed by six alternating baseline/warm pairs to reduce
shared-host drift. All six matched exactly and saved 0.34–0.58 seconds per warm
request. This supports implementing optional API-owned residency with bounded
lifetime and fallback checks; the prototype is not deployed or production-qualified.
The worker, launcher, API/store processes and all installed interfaces remain as
recorded above. No new API, database, model-task or full integration check is claimed.

The existing semantic performance lesson advanced v2→v3 with the conditional
small-set results and unfinished deployment checks, preserving the complete prior
body and metadata. Exact revision retry and a fresh full pull verified it. The
lesson influenced this investigation's scope; latency savings are not established
task benefit. The roadmap records the next implementation checkpoint, and the
semantic guide now correctly distinguishes unfiltered schema8 from kind-filtered
schema9. Earlier implementation history and experimental outcomes are preserved.

### 2026-09-09 — implement bounded semantic model reuse

The [resident-worker implementation](verification/semantic-residency-2026-09-09.md)
and [metadata](verification/semantic-residency-2026-09-09.json) follow the previous
startup experiment. An optional semantic-stream-command gives the API one lazy
child with framed request IDs, a20-second request deadline, immediate busy fallback
and30-second idle release. Cancellation/failure/shutdown close pipes and reap the
process group. The existing one-shot command remains supported. Python shares
model initialization only; current notes are re-embedded and no vector cache is
retained. Receipt schemas, ranking and eligibility remain unchanged.

Go/race process tests,34Python tests, disposable PostgreSQL/API integration and
vet/format pass. The real worker stays alive across17queries, a long-source pull,
ordinary revision and supersession. It refuses the stale handle, excludes a
separate local-only note, and disappears on API shutdown. Six interleaved API
pairs preserve candidate-score digests/source versions and save0.56–0.83seconds.
The new one-shot Python route also matches retained complete responses. These
are bounded execution-cost observations, not service percentiles or task benefit.

The initial fixture's sensitivity edit correctly returned AUTHORITY_DENIED; the
fixture was corrected to use supported A supersession. Source review also
corrected a goroutine capture before final race checks. Neither observation
justifies weakening a runtime contract. The prior memory lesson guided scope and
retained the previous experimental limits. Deployment and CI are recorded after
completion; all prior implementation history is preserved.

### 2026-09-09 — install bounded semantic model reuse

Clean cf66e1c is installed as both CLI and API. The API now selects worker-stream;
the model files, Python packages, one-shot launcher, Codex/OpenCode settings and
adapter remain unchanged. PostgreSQL stayed on PID163669. The installed API/CLI
executable hash, worker and drop-in hashes are retained in the resident-worker
report and current snapshot. Earlier deployment history remains intact.

Two hosted queries for saved project direction took1.246seconds cold and0.739seconds
warm, returned identical source versions/body hashes and candidate-score digests,
and reused one child. An exact body pull verified the value-direction note. The
child disappeared after the idle deadline. These operational observations do not
establish a latency distribution or downstream task improvement.

The semantic performance lesson advanced v3→v4 with the installed command and
verified lifetime behavior, preserving its entire prior body and metadata.
Exact mutation retry and a fresh full pull verified it. The prior prototype
observations remain explicitly historical. No task assessment was changed.

Exact installed-source CI [34412010428](https://github.com/halbritt/cairn/actions/runs/34412010428)
completed successfully. The optional real CPU/API comparison remains separate
local evidence. This deployment closes the bounded worker-reuse checkpoint;
general task value, larger-scale retrieval and broader roadmap work remain open.

### 2026-09-09 — reconcile U2 against existing run evidence

Source inspection found that U2's “add command/argv digest” wording described an
already implemented path: runner JSON-hashes its supplied argument vector,
BindRun retains it through an observer-only channel, and run-report exposes it.
Rendered-input/output digests, optional error-signature digests, retry identities
and versioned evidence-linked task assessments also already exist. The roadmap
now distinguishes these capabilities from selected task-output artifact/diff
observations and remaining native-task evidence.

The [digest guide](use-outcome-loop.md#command-and-delivery-digests) documents the
pre-carrier boundary and interpretation limits. A [disposable check](verification/run-evidence-audit-2026-09-09.json)
ran four real processes: prompt/carrier changes retained the base command digest,
argument splitting changed it, delivery hashes matched combined input, all task
outcomes stayed unknown, and raw argument/prompt canaries were absent from the
database dump and context files. This confirms existing behavior, not a new
capability or memory-benefit result. No production report or database was used
for the check; the operational installation is unchanged.

The existing assessment-history lesson was read after finding the implemented
command digest. It reinforced the distinction between aggregate fields and the
narrative/evidence behind a judgment. No new evidence-collection subsystem was
added merely to satisfy a stale roadmap phrase. All prior implementation history
is preserved; U2 remains partial under its narrower, explicit remaining scope.

Doctrine packet pkt-827528bed4950d6e has one typed evidence pass, a validated
decision receipt and citation closure. Twenty generic implementation obligations
are nonmaterial to this documentation-only audit; no broader runtime or failure
qualification is claimed. The existing JSON encoding also normalizes invalid
UTF-8, so the guide does not claim byte-exact identity for arbitrary Unix argv.

### 2026-09-09 — characterize the native compact-startup boundary

E2 investigation verified the actual OpenCode 1.18.21 system hook using two
isolated sessions and synthetic input. Both made two main provider requests and
one auxiliary title request; the hook appended its marker to all three. Denying
Cairn removed the synthetic search tool from the main catalog but did not gate
hook delivery. Both native processes exited zero. No model inference or Cairn API
was used by the fixture, and no production plugin or setting was changed.

The [report](verification/opencode-startup-hook-2026-09-09.md) and
[metadata](verification/opencode-startup-hook-2026-09-09.json) distinguish the
native instance permission API from a separate v2 server API, and record the
missing active-agent, request-purpose and cancellation inputs. The roadmap now
rejects an unconditional hook fetch and identifies controlled launch or a native
permission-aware entry point as the next investigation boundary. E2 remains
partial, including aggregate context budgeting and generic index execution.

The existing OpenCode procedure v10 was read before inspecting this hook. Its
permission and session-scope guidance informed the checks; it did not contain
these new findings. This is useful engineering evidence about an unsuitable
integration route, not a demonstrated memory-benefit result. No task assessment
was added. All prior implementation history is preserved.

### 2026-09-09 — explicit compact startup through ordinary memory

Added `cairn agent start`, using a fresh scoped index through the existing
ordinary hosted-profile interface and the harness's configured pull/search tools.
It preserves mandatory context, source identity and pull arguments, reserves
initial room for the task, excludes generated connection/credential instructions
and refuses a local destination. The command replaces its process with the
harness; it adds no observer authority, retained context file, recovery state or
task-outcome claim. Existing observed/retained H0 runs still require bodies.

The first API fixture exceeded its 8 KiB expansion allowance. The final fixture
preserves that refusal and verifies an exact partial pull and retry. The initial
native argv probe then exposed OpenCode's extra quoting and timed out during
provider retries. Pinned source inspection confirmed that stdin preserves the
input. The new stdin carrier uses a Linux anonymous memory file with no named
file or disk fallback. Its x/sys dependency version was already pinned and did
not change. The fixture now records its own failures and returns HTTP 400.

Full disposable PostgreSQL/race integration, Go unit tests, 34 Python tests and
vet/format checks passed. Final focused CLI/API/native checks preserve Unicode,
quotes, literal shell-like text, newlines and closed stdin. Three OpenCode 1.18.21
sessions verified a direct full-body pull, denied tool availability and stale
source refusal; each received 2,961 bytes of initial input. They used scripted
provider responses with no inference. Literal argv, process replacement,
credential environment filtering, exit status and signal behavior also passed.
The authenticated CLI suite is now included in CI; native checks remain opt-in.

The [guide](compact-start.md), [report](verification/compact-start-2026-09-09.md)
and [metadata](verification/compact-start-2026-09-09.json) retain semantics,
failed checks and interpretation limits. The retained-context lesson v3 was read
before implementation and informed fresh retrieval and authority separation.
This closes a bounded initial-index delivery gap, not aggregate task budgeting,
observed index execution, model-selected use or durable task-value acceptance.
All earlier implementation history is preserved.

### 2026-09-09 — install compact startup and isolate its CI fixture

Installed implementation `9c349a3` and used its explicit stdin startup through
the ordinary hosted profile to retrieve and update the OpenCode procedure to v11.
The complete v10 body and all other metadata were preserved. An identical retry
and a fresh index/body pull verified the update. No task assessment was added.

The first CI run failed the new authenticated CLI suite: earlier Go tests had
already installed root authority in the shared service database. Commit `a92820a`
creates and migrates a separate job-owned database for the CLI fixture. Root
bootstrap protections remain unchanged. Exact-source CI then passed every step,
including startup. Clean `a92820a` is installed as the CLI; a fresh stdin launch
retrieved the saved v11 procedure. API cf66e1c/PID 4121278 and harness configuration
were preserved. The [installation report](verification/compact-start-2026-09-09.md#local-installation-and-ci-correction)
and metadata retain both CLI installations, CI outcomes and source hashes.
This verifies availability and transport; cumulative memory contribution remains
an open task-value question. All prior history is preserved.

### 2026-09-09 — accept a saved task specification at startup

Added `agent start --prompt-file` as an alternative to inline `--prompt`. A bounded
regular-file read preserves UTF-8 text, CRLF and trailing newlines; empty/invalid,
oversized and non-regular sources refuse before retrieval. Both input carriers
retain their existing behavior and budget. No task file is deleted or persisted
by Cairn. The [verification](verification/start-task-file-2026-09-09.md) records
the old unsupported flag, observed shell newline loss, exact real CLI/API delivery,
Go/Python and static checks. Native model use and memory task benefit remain
unclaimed. Saved owner priorities informed selection; current source prevented
duplicating already-implemented retraction previews. All prior history is retained.

### 2026-09-09 — install task-file input after verification

Clean CLI `2915571` is installed; a live hosted-profile startup delivered an exact
57-byte task file, including its final two newlines, to `/bin/cat`. The API remains
cf66e1c/PID 4121278. Exact-source CI 34418879903 passed all steps, including
PostgreSQL/race and authenticated startup. The task-file report and metadata
retain installation bytes and verification evidence. The current summary now
names this CLI separately from the running API; prior history remains intact.

### 2026-09-09 — repair missing-context precedence in retrieval

Source inspection found that the first missing context pin returned before a
later mismatch could establish inapplicability. PostgreSQL regressions reproduced
false mandatory-policy refusals for both private and shareable instructions.
The existing applicability function now defers its missing result until all
possible known mismatches have been checked. Validity and unknown-context refusal
retain their contracts; private metadata remains excluded from hosted retrieval.

Full disposable PostgreSQL/race integration, Go/Python tests and static checks
passed. An old/new binary comparison verified the repaired review request,
continued refusal for genuinely unknown applicability, identical old body/index
recompilation and `STALE_PACKAGE` on a changed fresh retry. No history/schema
migration or new pin was needed. The [report](verification/applicability-precedence-2026-09-09.md)
retains failed baselines and interpretation limits. This is a verified retrieval
repair; neither a model task nor memory's incremental contribution was assessed.
All earlier implementation history remains intact.

### 2026-09-09 — deploy applicability repair and retain its lesson

Installed clean `63f63ed` as both CLI and API after a catalog-backed backup,
retaining the preceding executables. API PID 191214 now runs the repaired check;
PostgreSQL remains PID 163669 and no schema migration was needed. Recorded harness
and semantic-worker settings were preserved. Hosted semantic retrieval returned
ready discovery and the exact prior assessment-history procedure body.

A selected ordinary lesson records the missing-pin/mismatch defect, expected
conjunction semantics and historical-retry behavior. Identical capture retry and
a fresh full pull verified its content. Exact-source CI 34419842234 passed all
steps. The repair report retains deployment and lesson identities. This adds a
usable repair and saved guidance; no model task or benefit assessment is inferred.
All earlier implementation history remains intact.

### 2026-09-09 — retain fingerprints of explicitly selected task files

Added repeatable `run --artifact LABEL=PATH` to the local and authenticated host
runner. Up to 16 regular files and 64 MiB total are hashed after the process stops
and its outcome is committed. Existing evidence capture stores a metadata-only
manifest with run IDs, labels, sizes and digests. Local sensitivity is the default;
sharing needs an explicit flag. No database migration or new server endpoint was
introduced.

File-read failures preserve the process observation and submit no partial manifest.
Ambiguous capture responses retain the exact request for explicit retry without
rerunning the task. The [verification](verification/run-artifact-evidence-2026-09-09.md)
records PostgreSQL/race, real authenticated API and privacy/retry checks. A fixture
assessment can reference the evidence while fingerprint capture itself leaves task
outcome unknown. U2 remains partial for convincing native-task value evidence;
qualitative and cumulative review retains its place. All prior history is preserved.

### 2026-09-09 — install selected-file capture and observe a real build

Installed clean CLI `c3256de`; the API stays `63f63ed`/PID 191214 and PostgreSQL
stays PID 163669. A real Cairn build through the observer runner produced the same
21,809,277-byte executable as the installed CLI. Stored metadata identified those
bytes and the observed run; replaying the exact capture request returned its
original evidence ID. The manifest remained local and no assessment was inferred.

Exact-source CI 34421756291 passed all steps. The [verification report](verification/run-artifact-evidence-2026-09-09.md#local-installation)
retains installation digests and checked metadata pointers. No model task or memory
benefit is claimed; the compiler does not consume the supplied guidance. The current
installation summary now distinguishes CLI and API versions. All prior history is
preserved.

### 2026-09-09 — add declared task-phase applicability

Added `task_phase` to record constraints and caller context, with `--task-phase`
through local/authenticated search and runs, compact startup, MCP, configuration
generators and native OpenCode settings. Matching is exact within the existing
conjunction. Unknown mandatory applicability still refuses; derived guidance,
ordinary edits, overlap and authorized expansion retain phase restrictions.
Requests declaring a phase use semantic schema 10; requests without one retain
previous schemas and retry representations.

Public store and CLI tests first reproduced missing phase selection/forwarding.
A follow-up derivation test caught a missed containment comparison in the new
implementation and verified its correction. Full disposable PostgreSQL/race,
Go/Python and static checks passed, with real CLI/API/MCP and scripted native
OpenCode phase forwarding. Old/new binaries preserve existing body/index/browse/
filtered packages and refuse old-reader execution of new sealed phase packages.
Older direct database readers ignore new phase constraints, so the deployment
requires upgrading all readers before those constraints are used.

The [verification](verification/task-phase-2026-09-09.md) retains the evidence and
limits. The saved applicability lesson informed the preserved mismatch/unknown
boundary; no model task or incremental memory benefit was assessed. E1 remains
partial for entities/files/error signatures and broader currentness intent.
All previous implementation history is preserved.

### 2026-09-09 — deploy task phases and retain usage guidance

Installed clean `8f6864a` as CLI/API after a catalog-backed backup; API PID 430775
replaces 191214 and PostgreSQL remains 163669. The native OpenCode adapter now
accepts phase settings; connection, Codex and semantic-worker settings were
preserved. No migration was needed. Both installed database readers understand
new phase constraints; old readers remain unsuitable for phase-pinned data.

Live hosted semantic retrieval with declared validation phase returned schema 10,
ready discovery and the exact applicability lesson. A selected ordinary usage
procedure records phase semantics, commands and the upgrade requirement; capture
retry and fresh retrieval matched. Exact-source CI 34423942112 passed all steps.
The [deployment report](verification/task-phase-2026-09-09.md#local-installation)
retains hashes and bounded claims. No model task or memory-value judgment is
inferred. All prior implementation history remains intact.

### 2026-09-09 — Explicit applicability through ordinary capture

Closed the gap between raw Create's applicability and ordinary CLI/MCP/OpenCode
capture. `remember --pins` and the native optional `pins` object forward selected
restrictions through the existing store. Search context is never inherited, local
sharing remains the default, and ordinary edits preserve applicability.

Red tests reproduced unknown CLI flag, MCP additional-property refusal and native
OpenCode argument refusal. Static/Go/Python and disposable PostgreSQL/race checks
passed, including native fresh-session capture/retrieval, missing/mismatched phase,
unchanged default capture, retries and revision preservation. CLI forwarding was
also checked after the full run. No answering-model task or value assessment was
performed. E1 remains partial; this is practical access to existing constraints.
[Report](verification/capture-pins-2026-09-09.md) retains evidence and decision pointers.

### 2026-09-09 — Capture installation and procedure continuity

Installed clean CLI `215ecbf` and its bundled native OpenCode adapter. Existing
Codex/OpenCode connection settings retain their exact hashes. API `8f6864a`/PID
430775 and PostgreSQL PID 163669 remain active; the API already supports these
capture fields, so no restart or migration was needed. Fresh MCP processes load
the extended schema.

Updated the existing authenticated capture procedure `e2d3c2fd-92c1-42ce-8201-4348c94dfae1`
from v1 to v2 with explicit applicability usage and preservation checks. Exact
retry and fresh hosted pull matched SHA-256
`b5340899b14dd5e14b54b82f30d01085b8338e8f70c67e778b4f4a8aef0d284f`.
It remains unpinned and retains its previous version. This is knowledge maintenance
and live interface verification; no incremental task-value conclusion is added.
Installation and procedure metadata live under `/tmp/cairn-capture-deployment/`.

Corrected stale E1 prose that still listed phase matching as open; entity/file/error
matching and broader real-history usefulness remain open.

Exact implementation CI `34425510304` passed for `215ecbf`; the subsequent update
records deployment/evidence and corrects roadmap prose only.

### 2026-09-09 — Bounded reuse of semantic note vectors

Three live repeated hosted queries took 12.736/12.745/12.911 seconds with identical
score digests. The existing worker re-embedded all eligible notes each time. Added
a cache inside Scorer for exact body-hash vectors from the last successful request;
no persistent store, new service or schema. Current candidate gates still run and
cache hits still count toward the 128-chunk limit. Existing 30-second idle release
bounds lifetime; this does not claim allocator erasure.

A failing numerical test reproduced repeated embedding. Six final numerical tests,
static/Go/40Python tests and full disposable PostgreSQL/race integration passed,
including the actual streaming model, current edits/exclusions and worker shutdown.
All thirteen real-model comparisons preserve full scoring responses. Six warm pairs
fall from 1.85–2.29 to 0.034–0.044 seconds. A cold long-note pair was slower and no
cold improvement is claimed. Retrieval latency is measured; sustained task benefit
and independent answer-quality improvement remain open.
[Report](verification/semantic-vector-cache-2026-09-09.md) retains decision and evidence.

### 2026-09-09 — Vector-cache deployment and observed live cost

Installed only the optional Python worker from `d633c68`; CLI `215ecbf`, API
`8f6864a`/PID 430775, PostgreSQL PID 163669, model/dependencies and connection
settings remain. Exact-source CI `34426620798` passed.

The same live query returned identical candidate-score digests and selected IDs:
12.736/12.745/12.911 seconds before; 13.952 seconds cold and 0.060/0.062 seconds
warm after. An initial main-OS-thread-only child check missed the Go-spawned worker
and failed its final assertion after all retrieval checks passed. Corrected the
unsupported prior-exit metadata; no service restart or process kill followed.
All-thread child enumeration verified PID 552005 across a second cold/warm pair
(14.231/0.083 seconds). That process was absent at the later release check; exact
idle-exit timing was not observed.

Updated the existing performance note from v4 to v5, retaining earlier measurements
under a historical label. Exact retry and fresh pull matched SHA-256
`5b25497ad4a62d4267db13aaa34950cc9a637747f0eb9ba0a3819285a00d262e`.
This occurred after the comparison. Operational bodies, deployment metadata and
probe-correction evidence remain under `/tmp/cairn-vector-cache-deployment/`.
Repeated retrieval cost improved; cold cost, independent answer quality and
sustained task benefit remain separate open questions.

### 2026-09-09 — Repair saved task-file handoff guidance

Revisited a question whose semantic results were weak during the retrieval-cost
investigation. Neither lexical nor semantic five-result indexes returned the
OpenCode procedure; newline/CRLF search returned no notes. Pulled OpenCode and
Codex guidance omitted the implemented `--prompt-file` option, though the OpenCode
note pointed to the correct source document.

Revised OpenCode procedure `73537cbd-0ec3-4311-98c9-23e58a685b2f` v11→v12 with
selected task-file usage, input limits and source pointers. Earlier prose and
metadata remain; exact retry and fresh pulls match SHA-256
694dd8d524e1454b3ff1988a78e92908fe2ff7537dcc691312b7d0876f0f33bb. The same question now returns it first
in both modes; an existing source-span pull reads the complete added 897-byte
passage. Retained exact-byte startup evidence and the recent integration were
rechecked; no new launcher/model trial was run. This is a selected guidance
coverage correction, not a ranking change, independent benchmark or task-value
assessment. Runtime, binaries and model configuration remain unchanged.
[Report](verification/task-handoff-guidance-2026-09-09.md) retains comparison pointers.

### 2026-09-09 — Refuse explicitly empty capture pins

Found and reproduced a CLI capture defect: `--pins ''` and `--pins=` were treated
as omission, producing an unpinned draft and reaching the Create API. The shared
operator/agent parser now distinguishes flag presence from value and refuses
invalid JSON before capture. Omitted pins and valid objects keep their previous
behavior. CLI help now lists the option.

The saved capture procedure was retrieved before the repair and checked against
current source. Targeted tests first failed on accepted empty input and observed
API contact; they now pass, along with static/Go/40 Python and final CLI package
checks. Store rules and API behavior do not change. No task-value assessment or
new note was added. [Correction and evidence](verification/capture-pins-2026-09-09.md#correction-explicitly-empty-cli-pins).

### 2026-09-09 — Empty-pins CLI installation

Installed clean `397750b` as CLI. Against an isolated absent socket and a temporary
synthetic owner-only token, both empty forms return exit2/`INVALID_REQUEST`, confirming
refusal before capture. The fixture token was removed. API `8f6864a`/PID430775,
PostgreSQL, native adapter/settings and semantic worker `d633c68` remain unchanged.
Installation and refusal metadata: `/tmp/cairn-empty-pins-deployment/installation.json`.
Exact-source CI `34427884310` remains in progress; local static/Go/Python checks passed.

CI completion: `34427884310` subsequently passed for exact implementation `397750b`.
This closes the pending CI observation above.


### 2026-09-09 — Fixed BM25 screening remains inconclusive

Compared a fixed length-normalized, term-frequency-aware candidate with lexical
v4 on the unchanged twelve public passages and seventeen questions. Top-three
counts are 13/13 versus 13/12; first-place counts are 10/10 versus 11/10. Storage
vocabulary misses and irrelevant no-answer results remain. The corpus is reused
development material and its short passages do not settle the long-note hypothesis.

All 34 complete lexical baseline rankings match retained prior results; a second
report is byte-identical, existing output refusal preserves artifacts, and
`make check` passed. Added only the optional comparison script and documentation;
no production scoring, schema, runtime, installed binary, memory record or task
assessment changed. Keep lexical v4; stop tuning these same questions and revisit
on actual representative ranking failures. Qualitative and cumulative task value
remain open and are not reduced to these rank counts.
[Report and decision](verification/bm25-screen-2026-09-09.md).


### 2026-09-09 — Ranked search continuation

Added opt-in lexical and semantic search pages through CLI, MCP and native
OpenCode. Explicit offset0 starts; page.next_offset continues with the same
query/settings and a fresh request ID. The existing allocation code keeps
required instructions and individual budgets, ranks before paging, and excludes
private, wrong-phase and filtered candidates before page positions. Unpaged
search and browse behavior remain. Schema11 retains ranked pages for replay;
there is no database migration or multi-page snapshot.

Initial core,CLI,MCP and native checks reproduced missing page behavior. Final
static,Go/40Python,full disposable PostgreSQL/race and native checks passed;
additional core phase/nonmatch/retry checks passed afterward. Full result order,
exact pulls, retry conflicts and frozen-score replay are verified. The source-
freshness investigation exposed the navigation limit but its relevant note was
already on the first page. No task rescue, better ranking or cumulative value
is claimed. Runtime deployment is recorded separately below when verified.
[Report](verification/search-pages-2026-09-09.md).


### 2026-09-09 — Ranked-page installation and live continuation

Installed clean `032b9c7` as CLI/API and the bundled native OpenCode adapter after
an operational catalog-backed backup. API PID680757 runs the installed executable;
PostgreSQL PID163669, migrations001–030, semantic worker d633c68 and connection
settings remain. No operational note was changed. Exact-source CI34430333159 passed.

The existing live query keeps its six-result unpaged response. Ranked pages reach
18 distinct lexical matches across four pages (5/5/5/3), and a later-page body pull
matches its indexed version and hash. Semantic pages reach20 distinct notes in
four pages with an unchanged complete candidate-score digest:15.558seconds cold,
then0.054/0.055/0.056seconds using the existing cache. These are navigation and
runtime observations, not new relevance or answering-task evidence. Private local
artifacts: /tmp/cairn-search-pages-deployment/. The verification report retains
installation hashes, backup identity, limitations and CI state.


### 2026-09-09 — Executable build diagnosis

Added local version inspection without configuration/database access and an
explicit authenticated agent version command returning separate client/server
identities. MCP initialization now names the loaded facade build instead of1.
A shared internal whitelist exposes Go/module/VCS metadata, with nullable unknown
modification state. Build flags, paths, dependencies and credentials stay out.
No source revision is treated as proof of capability, correctness or note freshness.

Initial CLI/API/agent/MCP checks reproduced missing diagnosis. Targeted tests,
static checks,Go/40Python and full disposable PostgreSQL/race integration passed,
including all four profile types and actual stdio tools. A candidate against the
older installed API preserved NOT_FOUND rather than inventing a server identity.
No native adapter, memory schema, retrieval seal or stored record changed from
this feature. Installation is recorded separately after verification; task value
remains unassessed. [Report](verification/build-identity-2026-09-09.md).


### 2026-09-09 — Build diagnosis installed and phase guidance corrected

Installed clean `605be1a` as CLI/API after an operational catalog-backed backup.
API PID 736082 matches the installed executable; PostgreSQL PID 163669, migrations,
semantic worker, native adapter and connection settings remain. The installed
CLI and API independently report the same stamped build. A separate client built
with -buildvcs=false reports unknown local VCS state and the actual stamped API;
fresh MCP initialization reports the facade build. Exact-source CI 34431391693
was still running at this deployment observation.

Revised the existing phase procedure fa323deb-099a-4ced-aac5-b606a51e29a9 from v1
to v2: schema 10 applies to unpaged phase requests, while ranked pages use schema 11.
Added explicit build-diagnosis commands and their limits. Exact retry and fresh
pull matched SHA-256
607c347f0a921fd0844f78243289c37c3e391c35657a1fc7b45f4a713ac53481;
metadata and earlier source context remain. This corrects guidance and verifies
diagnosis, without asserting reduced task cost or cumulative memory value.
Local deployment evidence: /tmp/cairn-build-identity-deployment/.

CI completion: `34431391693` subsequently passed for exact implementation `605be1a`.


### 2026-09-09 — Agy configuration contract verified; U7 remains partial

Agy 1.2.0 can register the existing Cairn MCP command with exact arguments. An
optional isolated check verifies registration, list, explicit task/run updates,
enable/disable and removal while preserving an unrelated entry. The first check
expected enable to retain `disabled: false`; observed native enable removes that
key. The corrected check passes, and an existing-output retry preserves 27 files.
A traced run executes only Python and nine Agy configuration commands: no Cairn
process, IP connection or operational API contact was observed. `make check` passes.

Added a setup recipe and moved U7 from open to partial for configuration only.
Settings are user-wide with explicit scope; no native conversation identity,
permission/reload behavior, tool execution or useful Agy task is established.
No owner configuration, installed runtime, database or adapter changed. Further
Agy work remains lower priority than task value in Codex/OpenCode. The broader
qualitative and cumulative evaluation standard is unchanged, and all previous
implementation history is preserved.
[Report](verification/agy-configuration-2026-09-09.md).


### 2026-09-09 — Explicit binary evidence capture and request identity

Added canonical `body_base64` input to the existing evidence capture operation.
API/CLI capture now preserves arbitrary decoded bytes, source SHA-256 and retry
identity within the existing 1 MiB limit. Invalid encodings, conflicting nonempty
source forms and oversize input refuse without reserving an identity. Existing
text request serialization remains unchanged; a capture written by installed
605be1a retries exactly with the new code in a disposable store.

The initial API test failed because the field was rejected. Review also found
that direct Go invalid UTF-8 Body input was accepted despite lossy JSON request
hashing. A failing regression precedes its refusal; binary callers must use
BodyBase64, including callers attempting legacy invalid-Body retries. Existing
retained bytes and inspection are unchanged. Tests preserve local default,
repository constraints, testimony and qualified hosted pull rules. Operator
capture retains its separate 128 KiB encoded bound; agent capture has 8 MiB.

Targeted store/API tests, static checks, Go/40 Python and final full disposable
PostgreSQL/race integration pass, including actual CLI capture/inspection/pull
with no client DB access and existing stdio MCP tools. Native adapters are
unchanged and no model task ran. The saved evidence guide was retrieved after
source discovery to preserve related constraints, not credited with finding the
gap. L4 and durable task value remain partial. Installation is recorded separately.
[Report](verification/binary-evidence-capture-2026-09-09.md).


### 2026-09-09 — Binary evidence capture installed and guidance retained

Installed clean `386eae1` as CLI/API after an operational backup.
Both report that build, and running API PID 825681 matches the installed bytes.
PostgreSQL PID 163669, schema, semantic worker, native adapter and connection
settings remain unchanged. No synthetic binary evidence was added to the operational
store; exact source capture/pull was verified in disposable integration.

Revised evidence guide 8a47da19-dd71-43b2-a4cc-9cef9d113c81 from v4 to v5, preserving
its earlier body and metadata. It now describes explicit binary input, decoded and
encoded limits, retry representation, direct Go invalid-text refusal and unchanged
capture/citation authority. Exact retry and fresh pull match SHA-256
35cfeec5023e2a6b5851205f6c71aa5b45f23826c546a174f4d7506832f707b8.
The guide records retrieval after discovery; no memory-caused discovery or task
benefit is asserted. CI 34433568208 was still running at installation observation.
Deployment evidence: /tmp/cairn-binary-capture-deployment/.


CI completion: `34433568208` passed for exact binary-capture implementation `386eae1`.


### 2026-09-09 — Selected-file evidence capture

Added `agent evidence --file PATH --source LABEL` and the equivalent operator
`capture-evidence` flags. Both read a chosen regular file, preserve 1 byte–1 MiB
exactly through canonical base64, default to local sensitivity and retain only
the chosen source label, without an implicit path. Explicit sharing and stable
request UUIDs use existing capture semantics; changed files under a committed
UUID conflict. Stdin JSON remains available when no flags are supplied. Operator
file mode reaches the full source limit without changing its JSON envelope cap.

File capture and task-file startup share bounded regular-file reading; startup
keeps its distinct UTF-8/NUL/nonblank and budget rules. The initial agent test
failed on argument refusal. The first full integration passed Go/race but exposed
an operator dispatch omission: the generic argument guard ran before file parsing.
The corrected handler runs before that guard. Final disposable CLI/API integration
passes both 1 MiB file paths, retained bytes, labels, sharing, retries and changed-
file conflicts, plus existing JSON binary capture and stdio MCP. Final static/CLI
race checks and the earlier Go/40-Python suite pass. The failed run is retained.

Saved evidence guidance was retrieved before implementation and informed encoding,
limits and retry rules; current code and conversation also supplied those facts.
This removes manual JSON preparation but does not establish net task benefit.
No API/core schema or native adapter changed. Installation is recorded separately.
[Report](verification/file-evidence-capture-2026-09-09.md).


### 2026-09-09 — Selected-file CLI installed and guidance updated

Installed clean `57164ce` as the CLI, SHA-256
c9309a9eef2fa509ef2276e09240efc76909bc40313b5016ab7f4fe9a570130a.
Running API remains 386eae1 at PID 825681; PostgreSQL PID 163669, semantic worker,
native adapter and connection settings are unchanged. The installed CLI rejects
an empty file before capture. No synthetic evidence was added to the operational
store; exact full-size capture was verified in disposable integration.

Evidence guide 8a47da19-dd71-43b2-a4cc-9cef9d113c81 was revised v5→v6 with selected-file
commands, source-label and sharing choices, exact retry rules and the distinction
between operator file and JSON limits. Earlier body and metadata remain; exact
retry and fresh pull match c2f4a0938ca00aaca5b97ccf0ec80b3a312bc2209243ef9835f8b22415b0731f.
CI 34434364680 is in_progress. Installation evidence: /tmp/cairn-file-capture-deployment/.


CI completion: `34434364680` passed for exact selected-file implementation `57164ce`.


### 2026-09-09 — Reject lossy Unicode before request decoding

A raw API reproduction captured invalid UTF-8 JSON source text with HTTP200/OK:
decoding had replaced bytes before core UTF-8 validation. Lone surrogate escapes
had the same gap. Operator decoding and agent forwarding reproduced acceptance
separately. A shared Unicode guard now rejects these serialized inputs before
typed decoding or API send, retaining standard JSON/schema validation. The API
still authenticates first and enforces the same bounded request envelopes.

Valid UTF-8, paired escaped emoji, literal escaped backslashes, NUL escapes and
explicit replacement characters remain valid. Refused API/CLI requests do not
reserve capture IDs; valid retries retain exact source digests. The check cannot
recover text already altered by an upstream serializer/SDK and does not repair
historical captures. Specialized import/configuration paths are not claimed.

Static checks, Go/40-Python tests and full disposable PostgreSQL/race integration
pass, including actual malformed/valid Unicode CLI captures, maximum-size sources,
selected files, ordinary notes and stdio MCP. A bounded fuzz run had 12,199 total
executions, including skipped invalid UTF-8 inputs; it was not that many valid
cases. Make check now includes internal package formatting. No schema, authority,
sharing rule or native adapter changed. This repairs a verified capture defect;
net task value remains unassessed. Installation is recorded separately.
[Report](verification/json-unicode-integrity-2026-09-09.md).


### 2026-09-09 — Unicode request repair installed and guide retained

Installed clean `463ccef` as CLI/API after an operational backup.
Both version responses identify that build, and API PID 897597 matches the installed
bytes. PostgreSQL PID 163669, schema, semantic worker, native adapter and connection
settings remain unchanged. Malformed/successful capture tests ran only against
disposable stores; no synthetic source was captured in the operational store.

Evidence guide 8a47da19-dd71-43b2-a4cc-9cef9d113c81 was revised v6→v7 with the
serialized Unicode rule, valid-input preservation, upstream transformation limits
and refusal of malformed legacy retries. Earlier body and metadata remain. Exact
retry and fresh pull match c3a17d08e2e6811be75b8d054c72443d7db443225042cc58d700b3a2c9648dc9.
CI 34435235516 is in_progress. Deployment evidence: /tmp/cairn-json-integrity-deployment/.


### 2026-09-09 — Ordinary note source citation implementation

Added explicit `cite` updates through CLI/API and an exclusive `evidence_citations`
input on the existing MCP/OpenCode edit tool. Updates replace references on a new
unchanged-body A version; an explicit empty array clears current references.
Existing source digest/span, repository, sensitivity, request identity and CAS
checks apply. Ordinary body/full-draft revisions retain even degraded references,
while earlier versions and frozen receipt facts remain available. A citations
confer no qualification or upstream freshness.

Full disposable PostgreSQL/race packages, static checks and Go/40-Python tests
passed. Early fixtures had an off-by-one passage length, attempted self-promotion
and shared-source impact interference; corrected the fixtures without weakening
existing assertions. CLI/stdio workflows then passed; native OpenCode startup
hit npm contact and a timeout. Isolated diagnosis reached validation with default
plugins disabled, now applied only to test harnesses. Final native verification
and installation are recorded separately below.

The old/new executable check preserved existing create/revise retry identities and
confirmed that old writers drop A citations on edit. All writers must upgrade
together. No schema, semantic payload shape, authority path or tool name changed.
This is a source-retrieval capability; task value remains unassessed. L4 stays
partial. [Report](verification/ordinary-citations-2026-09-09.md).

Correction to the preceding Unicode deployment observation: CI 34435235516 for
463ccef subsequently completed successfully, verified during this work.


### 2026-09-09 — Ordinary citation native verification

The isolated CLI/API rerun now passes with stdio MCP and OpenCode 1.18.21 native
custom-tool checks. Both edit surfaces attached sources, retained them through a
text edit, pulled exact cited passages, cleared current refs and refused changed
retry intent and stale handles. Existing host permissions, sharing, session scope
and maximum-body scripted checks pass. Default plugins are disabled only in the
isolated test harness; no answering model or owner configuration was changed.


### 2026-09-09 — Ordinary citations installed and source-linked guide verified

Installed clean e5ebcbd as CLI/API and replaced the bundled native OpenCode
adapter after an operational backup. Both version responses and API PID 976089
match the new executable; PostgreSQL, schema, worker source, connection files and
host permissions remain. Full final `make test-integration`, static checks and
40 Python tests pass; the separately verified native OpenCode path also passes.

Evidence guide 8a47da19-dd71-43b2-a4cc-9cef9d113c81 v7→v8 preserves the complete old body and adds
the procedure, then v8→v9 attaches selected source passages from the two repository
evidence documents. Exact retries and fresh installed MCP body/source pulls match.
Guide SHA-256: 0ddcc688c09f238ff3ae3f3b985f4e45bc51b0a691833f5b1f3a53ee441df7e5. These are selected project sources, not synthetic
captures or raw session content. This verifies usable source links, not downstream
model task benefit. CI 34437060456 is failure.
[Installation](verification/ordinary-citations-2026-09-09.md#local-installation).


### 2026-09-09 — CI process-disappearance check corrected

Implementation CI 34437060456 passed core/API/MCP/runner tests, then failed in an
existing semantic worker test when `/proc/PID/stat` returned ESRCH after the
process disappeared. Its test-only absence check now accepts that specific error
alongside ENOENT, with other errors and live descendants still rejected. Ten
race-enabled repetitions and static checks pass; production worker code is
unchanged. The ignored project `bin/cairn` was also refreshed to the installed
clean e5ebcbd build to avoid leaving a documented old writer in the checkout.


### 2026-09-09 — Ordinary citation follow-up CI passed

[CI 34437354625](https://github.com/halbritt/cairn/actions/runs/34437354625) completed successfully for f9e4836, including full
Go race coverage, 40 Python tests, static/build checks and authenticated CLI/stdio
workflows. Production source is identical to installed e5ebcbd; the follow-up only
changes the process-disappearance test and documentation. The earlier failed CI
and its correction remain in this history. Native OpenCode verification remains
the separately recorded successful local run.


### 2026-09-09 — Observed compact index execution

Implemented the E2 observed/retained index route. An observer can designate one
ordinary expansion reader when creating an index. The authenticated API checks
that reader's configured repository and destination. Pulls reuse current source,
mandatory-context, expiry, payload and shared-budget checks; receipt inspection,
binding, launch and outcomes remain owner-only. Migration 031 adds the nullable
reader field without changing semantic seals. New `run-index` loading preserves
handles, expiry and remaining allowance instead of recompiling.

`run --index` supplies the shared compact presentation through stdin or argv with
explicit existing pull/search tool names. Retained reader, semantic/browse/page
intent, kinds and other context must match. Initial memory input is bounded;
combined initial memory/task input also has a 131,071-byte limit. Full PostgreSQL
race integration, CLI/stdio workflows, static checks and 40 Python tests pass.
Real CLI children pull a note and cited source with ordinary credentials, retaining
separate process outcomes. Native OpenCode 1.18.21 scripted-provider checks pass
allowed, denied and stale cases for both ordinary startup and observed execution.
No answering-model benefit or task acceptance is claimed.

Retained failures: the initial authority/runner refusals and missing retained API,
an incorrectly chosen non-observing test fixture, a native startup timeout before
main/tool contact, and a test-report variable shadowing error. The role gate was
preserved, the report corrected, and clean final checks passed. The native
timeout's cause remains unknown. [Full verification](verification/observed-index-2026-09-09.md).
The existing installed build remains e5ebcbd until the installation entry below.


### 2026-09-09 — Observed index installed and CI passed

Installed clean 20b7574 as CLI/API and project `bin/cairn` after backup, applying
migration 031. Client/server build identities and executable hashes match;
PostgreSQL PID 163669, adapter bytes, configuration and semantic worker source
remain unchanged. API PID is 1096559. An installed retained-index run reads the
existing OpenCode procedure through an ordinary profile and retains its separate
process outcome. The guide is now v13, preserving the full v12 body and metadata
while adding the observed route. Exact retry and fresh retrieval pass.

CI 34439862896 succeeded for the installed source. Full local disposable
PostgreSQL/race and CLI/stdio checks also pass; native OpenCode scripted delivery
verification is recorded separately. Neither successful source reads nor process
completion establishes task benefit. Aggregate task budgeting and broader goal
requirements remain open. [Deployment and evidence](verification/observed-index-2026-09-09.md#local-installation).


### 2026-09-09 — Saved task files for observed execution

Added `--prompt-file` to operator and authenticated observer runs, reusing the
bounded regular-file reader from ordinary startup. Fresh and retained body/index
runs preserve UTF-8, CRLF, literal shell characters and trailing newlines. The
file's task text remains separate from retrieval intent: omitted query means
empty query, while existing inline defaults remain. Relative paths use CLI cwd;
child `--dir` can differ. Invalid, nonregular or oversized files refuse before
retrieval; symlinks to regular files work.

The 128 KiB boundary test exposed an existing failure: a valid maximum-size task
plus memory exceeded Linux's single-argument limit only after launch was claimed.
The runner now refuses combined argv input over 131,071 bytes before binding.
The same retained body receipt then succeeds through stdin. Index delivery keeps
its existing combined limit for both carriers. No API or schema change is needed.

Static checks, Go tests, 40 Python tests, full disposable PostgreSQL/race and
CLI/stdio checks pass. The final API rerun includes operator and symlink coverage.
Native OpenCode 1.18.21 scripted allowed/denied/stale cases preserve a saved task's
trailing line breaks. No answering-model call or task-benefit claim is involved.
[Verification](verification/run-task-file-2026-09-09.md). The installed CLI remains
20b7574 until the installation entry below; the API can remain on that build.


### 2026-09-09 — Observed task-file CLI installed

Installed clean 8dd9033 as the CLI and project binary, preserving API 20b7574
(PID 1096559), PostgreSQL (PID 163669), native adapter, semantic worker and
configuration. No API restart or schema migration was needed. An installed
retained-index child verifies the exact selected task bytes and reads the current
OpenCode procedure. The guide is now v14 with all v13 text/metadata preserved,
plus the saved-task route and distinct limits. Exact retry and fresh retrieval pass.

CI 34441584749 was in progress at this installation checkpoint. The local static,
Go/Python, full disposable integration and scripted native checks passed. Source
reads and process completion remain separate from task benefit. [Deployment](verification/run-task-file-2026-09-09.md#local-installation).


### 2026-09-09 — Observed task-file CI passed

[CI 34441584749](https://github.com/halbritt/cairn/actions/runs/34441584749)
completed successfully for installed 8dd9033, including Go race tests, 40 Python
tests, static/build checks and authenticated CLI/stdio workflows. Native OpenCode
scripted checks remain the separately recorded successful local run. The API stays
on compatible 20b7574. Aggregate task budgeting and convincing durable task-value
evidence remain open; no broader completion is claimed.


### 2026-09-09 — Supporting evidence in required retirement previews

Audited R3 against the accepted source and current code. Required caller-owned
previews, retained/transitive uses, stale version/dependency/exposure checks and
concurrent compile/retraction protection already existed. The preview omitted
supporting evidence, despite the roadmap requirement. Added version-grouped source
metadata through the existing evidence reader, including historical citations
removed from later versions, exact byte passages and current integrity state.
Source bodies and labels stay out of the response; hosted inspection remains refused.

The whole preview now has a 30-second deadline and a 1,000-evidence-reference cap
alongside existing version/use caps. Counts precede body reads; 1,001 references
refuse without returning or committing a token. Existing preview tokens keep their
original lifetime, so request a fresh preview for the new inventory. No migration
or new endpoint is required. R3 is recorded as implemented/tested for known,
versioned dependencies; unknown derivations and broader lifecycle work remain open.

Retained failures: the first new test used a Record variable for Cite's Revision
result; after that fixture correction the behavioral test exposed missing preview
evidence. The first full integration run passed Go race checks and then found a
missing payload argument in the new CLI test helper. That fixture was corrected.
Focused boundary/dependency checks, static checks and Go plus 40 Python tests pass;
the clean full rerun and installation are recorded below. No operational memory
was retired. [Audit, contract and checks](verification/retraction-evidence-2026-09-09.md).


### 2026-09-09 — Retirement preview integration passed

The clean full disposable PostgreSQL/race and CLI/stdio run passed after correcting
the CLI test helper. New and existing retraction, dependency, evidence-refresh,
concurrency and conflict checks passed. The final static check passed. Installed
builds remain CLI 8dd9033/API 20b7574 until the installation entry below.


### 2026-09-09 — Retirement evidence preview installed

Installed clean 5946c5d as CLI/API and project binary. API PID is 1219742;
PostgreSQL PID 163669, native adapter, semantic worker source and configuration
are unchanged. No schema migration occurred. An installed preview of the existing
evidence guide reports its exact two source citations and passages, plus 62 uses.
Local authenticated and operator results agree; hosted inspection refuses. The
note remains active at v9, with no operational retirement or rewrite.

CI 34442784293 was in progress at installation. Full local disposable integration,
race, static and Go/Python checks passed. [Deployment](verification/retraction-evidence-2026-09-09.md#local-installation).


### 2026-09-09 — Retirement evidence preview CI passed

[CI 34442784293](https://github.com/halbritt/cairn/actions/runs/34442784293)
completed successfully for installed 5946c5d, including Go race tests, 40 Python
tests, static/build checks and authenticated CLI/stdio workflows. R3's requirement
audit is recorded with the evidence inventory and existing transactional gates.
This closes that bounded implementation item; durable task-value evidence and
the remaining roadmap requirements stay open.


### 2026-09-09 — Current usefulness inventory reconciled with owner direction

The linked usefulness inventory still presented a prospective controlled comparison
as the next required step and omitted the later qualitative adapter-repair and
cumulative-maintenance reviews. The current section now includes those cases,
separates useful outcomes from judgments about memory's contribution, and applies
the owner's broader standard for qualitative, indirect, delayed and cumulative
value. The complete earlier inventory and trial plan remain under a historical
heading, including their failures, measurements and unknowns. No task assessment
was changed and no model cohort was rerun.

README and roadmap links now lead readers to that current account. The stored
owner decision on evaluation was retrieved and checked against the current
roadmap while reconciling the documents. It supplied relevant direction, but the
same instruction was available in conversation and source; no unique causal or
net-benefit claim is made for this documentation repair. Claims were checked
against the four case reports, and local links plus complete status/history
preservation were verified. No runtime or installed binary changed.


### 2026-09-10 — Exact quoted text in memory search

Implemented a bounded, case-sensitive literal preference in the existing query
field. A known path or error phrase now precedes broader lexical overlap among
eligible optional notes, including semantic discovery and its lexical fallback.
Previews expose the earliest matching source passage. Required instructions,
currentness, destination, kind and budget gates remain in force. Quotes are soft
preferences; unmatched lexical notes remain available. Raw query text is still
represented by its existing digest in retained receipts.

The first database reproduction failed because token overlap outranked an exact
path. A second failed because the preview hid that path; both now pass. Further
checks cover zero lexical terms, bounded and duplicate hints, case-sensitive
files/errors/digests, UTF-8 spans, stale pulls and historical replay after edits.
Full disposable PostgreSQL/race, CLI/API, independent stdio, actual native
OpenCode tools, static checks, Go tests and 40 Python tests pass. The final added
source/edit tests passed separately with the race detector. Actual clean5946c5d
receipts recompile unchanged; current retries whose package changes correctly
refuse STALE_PACKAGE. No answering model was invoked.

The implementation initially considered hard error-signature pins, then rejected
that interpretation after reading accepted section15.4: those matches belong in
optional relevance after eligibility. Explicit stored matching metadata was also
considered; existing note text and query fields support this first usable step
without retagging notes. Structured entity/file/error metadata and automatic host
intent remain open under E1. An operational path query already ranked correctly
before this change, so the fixture improvement is not presented as a production
incident repair or established task benefit. Details and evidence are retained in
the quoted-search verification report. Installation is pending at this entry.


### 2026-09-10 — Quoted search installed and exercised on current notes

Clean `7dcf3dc` is installed in the CLI, API and project binary; SHA-256
24eed8586c50067858f352b11d7a0458681b9e383cbdac48d9628a157ddb87da.
The native OpenCode tool description was refreshed. API PID 1513779 serves the
new build; PostgreSQL PID 163669, schema 031, connection settings, identities and
semantic worker source are preserved. Live hosted retrieval confirmed lexical v5
for quoted text, lexical v4 for unquoted text, semantic v2 with the actual local
worker ready, and an exact pull plus matching byte span for the existing
applicability-precedence note v1. No operational notes were rewritten. This is
installation and retrieval evidence, not a completed model task or new value
claim. CI 34451418062 remains in progress at this entry.


### 2026-09-10 — Quoted search CI passed and history formatting restored

[CI 34451418062](https://github.com/halbritt/cairn/actions/runs/34451418062)
completed successfully for installed `7dcf3dc`. PostgreSQL/race tests, 40 Python
tests, static/build checks and authenticated CLI/stdio workflows passed. Local
native OpenCode and actual previous-binary compatibility checks are recorded in
the feature report. E1's structured intent work and the broader task-value goal
remain open.

A spacing cleanup in deployment documentation also changed one older status line
in `4683a29`. The history-preservation check detected it; the original line has
been restored exactly. The check now confirms that the full history through
`7dcf3dc` remains intact, followed by these deployment and CI additions.


### 2026-09-10 — Proposal conversions retain exact lesson versions and review history

The conversion path retained only a logical lesson ID, so edits obscured which
version was linked to a failure review. Reopening also cleared the current result
link without storing it on the old review row. Migration 032 now records exact
conversion references and deferral times on individual review events. An optional
result_version rejects a changed lesson; omission preserves record-only calls and
pins the current version under the existing record lock. proposal-history exposes
retained review decisions through bounded version-cursor paging.

The initial behavioral test failed on the missing pin. A history test initially
failed to compile before its API existed; a later fixture mistook metadata-only
Revision output for a record body and was corrected. Database and actual CLI
checks now preserve conversions across edits/reopening, reject stale versions
without writes, retain exact retries, enforce repository/paging bounds and allow
audited forgetting while protecting retained references from ordinary deletion.
The actual older writer creates an explicitly unpinned new event after migration;
no earlier pin is carried forward, and its original mutation response still retries.
Full disposable PostgreSQL/race, static checks, Go tests, 40 Python tests and
ordinary CLI/stdio workflows pass. The generic previous-binary quoted-search check
was updated to handle an already-upgraded v5 baseline as well as legacy v4.

No old pin was reconstructed, no body is copied into review history, and no model
task ran. This prepares the reviewed-failure path for exact-signature retrieval;
that E1 work and the broader D1/value requirements remain open. Installation is
pending at this entry. Complete earlier implementation history is preserved.


### 2026-09-10 — Versioned proposal review installed after backup

Clean `97816e9` is installed in the CLI, API and project binary; SHA-256
`d9098cbd818ce5f41a808460b329eeb0d74eb9718a4f01291ca8a5eabfd7a5b4`.
Migration 032 was applied after the standard store backup, whose checksum and
readable restore catalog are retained in the feature manifest. API PID 1583844
serves the new build. PostgreSQL PID 163669, connection settings, native adapter
and semantic worker source are unchanged.

The operational store has no proposal/review rows, so no production conversion
was performed to stage a result. The installed history command returns NOT_FOUND
for an absent ID, and ordinary hosted quoted retrieval still works. The actual
conversion, earlier-writer and deletion behaviors were verified in disposable
stores. CI 34453317076 remains in progress at this entry; task-value and E1/D1
requirements remain open.


### 2026-09-10 — Versioned proposal review CI passed

[CI 34453317076](https://github.com/halbritt/cairn/actions/runs/34453317076)
completed successfully for installed `97816e9`, with PostgreSQL/race tests,
40 Python tests, static/build checks and authenticated CLI/stdio workflows.
The local integration run additionally exercised an actual older proposal writer
and its exact retries. Versioned conversion/history is installed and verified;
error-signature retrieval, broader demand adaptation and durable task value remain
open. No model-task result was added by this change.


### 2026-09-10 — Reviewed failure signature retrieval

Implemented optional `error_signature_sha256` in the compiler and existing
CLI/MCP/native OpenCode search, startup and runner surfaces. A current converted
review can prefer its exact current lesson version on later tasks even when query
words differ. Source assessments must still be current. Lesson scope, pins,
destination and authority remain gates; old task/harness labels do not silently
restrict reuse. Signature and quoted-text matches share one optional preference,
with no weight from repeated proposals or exposures.

Migration 033 adds explicit `signature_shareable` on each conversion review, false
by default. Sharing a note alone does not publish its private failure association.
Old writers retain their existing pins and leave sharing false. Reopening, editing
a lesson or correcting an assessment changes fresh matching without retargeting
historical reviews. New signature receipts use semantic schema 12 and ranking
profiles lexical v6/semantic v3; no-signature profiles remain unchanged. Retained
execution checks the supplied signature before binding/launch.

Behavioral regressions reproduced missing retrieval, missing explicit hosted
sharing and missing blocked demand for signature-matched ordinary testimony with
no lexical overlap. These now pass. Full disposable PostgreSQL/race, CLI/API, MCP,
actual native OpenCode tooling, startup and retained intent checks pass, along
with Go, 40 Python tests and static checks. Fixture errors and their corrections
are preserved in the [verification report](verification/failure-signature-search-2026-09-10.md)
and its artifact manifest. A final API rerun separately verifies the refined
retained signature/filter cases.

The operational preflight still showed zero proposals and reviews. No production
synthetic conversion or answering-model trial was added. This completes a bounded
structured-intent capability, not E1/D1/D2 or proof of task value. Pincite packet
`pkt-e1505300c9e82e37` has validated typed evidence and decision receipt with closed
citation trace. Eight nonmaterial asynchronous/interface obligations remain named.


### 2026-09-10 — Failure signature retrieval installed

Committed and pushed `61627b3d17f96c95515b1ce6c8e0d3377096d214`. Clean CLI, API
and project binaries now share SHA-256
`4abbca2242bfae4903c9974d8fdd4802bca13177299e3f0fac9c7468a926a20e`.
Migration 033 followed the standard backup and a readable dump-catalog check;
API PID 1687910 serves the new build. PostgreSQL PID 163669 and protected
configuration/semantic source hashes are preserved. The deployed native OpenCode
adapter matches source.

Live hosted search verifies signature schema/profile and ordinary quoted fallback
to the existing applicability lesson, followed by an exact pull. The supplied
unmatched probe digest is not a failure observation. The store still has zero
proposals and reviews; no synthetic conversion was introduced. A fresh MCP facade
identifies the clean build and exposes the new field. The first live facade probe
omitted its required socket flag; the corrected script passes. Existing facade
processes still require restart to load the upgraded binary.

[Installation evidence](verification/failure-signature-search-2026-09-10.json)
retains identities, backup, protected hashes and retrieval receipts. Local full
PostgreSQL/race, static, CLI/MCP and native-tool checks pass. Hosted CI run
`34456197141` completed successfully for the feature commit: PostgreSQL/race,
Python, static/build, report and authenticated CLI/compact startup steps all passed.
No additional task-benefit claim is made.


### 2026-09-10 — Real failure signature check and text baseline

Verified the installed `61627b3` retrieval path against an isolated restore of the
actual rejected repair trial. Original proposal `f41b3559-59db-428f-92c2-3ba77bd39345`
links local A lesson `16a90b22-ae29-436c-a73f-343c7a2dc273` version 1. Its legacy
conversion pin remains unknown. A new review version 3 in the copy pins the exact
lesson and enables signature lookup under the lesson's historical revision.
Missing revision produces an omitted candidate with `CONTEXT_MISSING`; a different
revision produces `CURRENTNESS_MISMATCH`. Hosted retrieval still excludes the
local lesson. Exact local pull and both historical recompilations pass.

All three diagnostic natural-language queries already rank the lesson first.
This check establishes no ranking advantage, new repair outcome, review-cost
saving or model-task benefit. Different task/harness labels are declared probe
context, not agent executions. No answering model ran. All eight original trial
artifact hashes, source assessment history and the lesson remain unchanged; the
verification cluster was removed after retaining a private dump. The operational
store was not used.

The initial probe hit the observer receipt-ownership boundary, then a corrected
probe exposed its own missing revision. Both observations and corrections are
retained in the [report](verification/real-signature-retrieval-2026-09-10.md) and
metadata manifest. Product code and lesson pins were left unchanged. The roadmap
now favors ordinary task use before further signature feature work, while keeping
qualitative, cumulative and indirect value admissible. E1/E4/D1/D2 remain partial.
Pincite packet `pkt-1ddf7666ccf3178f` has validated evidence and decision receipt,
closed citation traces and 15 explicitly nonmaterial outstanding obligations.


### 2026-09-10 — Per-call context for native search

MCP and native OpenCode searches now accept an optional context declaration for
one call, using the existing revision/workspace/task-class/task-phase/binding/
capability fields. Previously native capture could restrict a note to a phase,
but native search could only use host configuration. The MCP regression and
installed OpenCode adapter both rejected the additional argument before this
change. The updated interfaces retrieve restricted guidance when supplied context
matches and omit it when context is missing or different.

Per-call values fill unset fields only; conflicting host-fixed values refuse
before retrieval, without reserving the retry identity. Empty fields cannot erase
configured values. Declarations do not carry into later calls, and exact retries
retain their existing intent checks. Repository/session scope, destination,
permissions, mandatory instructions and capture pins keep their existing owners.
No store migration or ranking change is needed.

Full disposable PostgreSQL/race, CLI/API, MCP, Go/40 Python and static checks pass.
Native OpenCode tooling and scripted normal-session checks also pass after an
initial timeout during the first existing capture call; its startup cause remains
unknown. The [verification report](verification/search-context-2026-09-10.md)
preserves that failure separately from the new behavior. No answering model or
new task-benefit result is claimed. E1 and broader usefulness requirements remain
partial. CI for preceding documentation commit `52e428b` completed successfully
in run `34458204478`.


### 2026-09-10 — Native search context installed and procedure updated

Committed and pushed `9f43b00de9622793c04cb963e025cb02aab95f0c`. Clean CLI, API and
project binaries share SHA-256
`088380580d8d5317e55f26674ecab77b32b301c5eccd2d554bf461de857be44c`.
The native OpenCode adapter matches source. API PID 1801136 serves the new build;
PostgreSQL PID 163669, schema 033 and protected configuration/semantic-worker
hashes are preserved. This change required no database migration.

A fresh installed MCP facade exposes the context argument and retrieves the
existing task-phase procedure under declared validation context. A subsequent
search confirms the declaration did not carry forward. Installed native OpenCode
also retrieves the same note with per-call context and its actual session scope.
These are native capability checks without answering-model inference or production
fixture notes; existing MCP processes still need restart for the new schema.

Updated retained procedure `fa323deb-099a-4ced-aac5-b606a51e29a9` from version 2 to
3 with the new interface and current source pointers. A fresh exact pull verifies
its body while scope, sensitivity, kind, claim type and applicability are preserved.
Earlier versions remain retained. Installation and maintenance receipts are in
the [verification manifest](verification/search-context-2026-09-10.json).
No additional task-value claim is made.

[CI 34459553174](https://github.com/halbritt/cairn/actions/runs/34459553174)
completed successfully for installed `9f43b00`: PostgreSQL/race, Python,
static/build, use-report and authenticated CLI/compact startup checks all passed.
The final local native check additionally verified all six per-call fields and
OpenCode's binding/capability name mapping, with fixed-host conflicts refused.


### 2026-09-10 — Native startup timeout investigation

Investigated the 60-second timeout from the first native context check without
changing product code or increasing its deadline. A fresh isolated OpenCode 1.18.21
startup with a synthetic echo tool completed in 7.193 seconds, including system-call
tracing, and returned the exact fixture text. It made no Cairn or answering-model
call. The trace shows external TLS activity; pinned upstream source confirms
startup installation of the matching plugin SDK. Source blob identities match
the retained v1.18.21 tree.

This does not reproduce or explain the original timeout. Two later full native
checks had already passed. Left timeouts, dependencies and network settings
unchanged and documented the startup dependency boundary in the native-tool
instructions. A timed-out startup trace is the next useful diagnostic if it recurs.
The [investigation](verification/native-startup-2026-09-10.md) and metadata retain
the bounded observation, uncertainty and Pincite abstention receipt. No task-value
claim or roadmap completion follows from this diagnostic.


### 2026-09-10 — Native process-argument Unicode repair

An ordinary review of per-call context retrieved and pulled the earlier OpenCode
validation lesson, version 2. That guidance informed investigation of the actual
argument boundary and a normal-session check. Source inspection found a separate
transport gap: a lone UTF-16 surrogate in a binding declaration became U+FFFD
before reaching the API's existing JSON guard. The native disposable-API regression
returned success with the altered context, establishing the defect before repair.

The shared native adapter now rejects malformed Unicode in executable/process
arguments before CLI launch, without echoing values. Valid Unicode, JSON stdin,
fixed host context, scope, permissions and retry semantics retain their contracts.
No store migration or Go API change is needed. Both the initial and expanded full
native API suites passed. Additional checks cover malformed queries and configured
bindings, high/low/reversed surrogates, exact Japanese/emoji/literal-escape/U+FFFD
preservation and reuse of refused request UUIDs. A scripted normal OpenCode session
separately verifies refusal followed by correction. Go/40 Python and static checks
also pass; no answering model or production fixture capture was used.

The [report](verification/opencode-unicode-2026-09-10.md) and metadata preserve the
failed reproduction, verification and Pincite decision receipt. This is a verified
repair during memory-guided work; it does not establish independent model benefit,
time savings or net task value. Other upstream serializers remain outside the
claim. The full prior implementation history remains intact.


### 2026-09-10 — Native Unicode repair installed and guidance retained

Committed and pushed `6c48411991b55cd766fcb2c738b4750445c04b98`. Clean installed
CLI/API and project binaries share SHA-256
`46af962e0d036bbf0b1fd084e2f997a03939757c44854f8e42eac1edc492912d`.
API PID 1894822 serves that build. PostgreSQL PID 163669, schema 033, native
connection settings, identities and semantic-worker configuration are preserved.
The installed native adapter matches the committed source.

An actual hosted-profile native search refuses a malformed query, then retrieves
the existing validation lesson with the refused request UUID. Updated that lesson
`104519b5-82cd-44b3-ba73-1def2ad42e8c` from version 2 to 3 with the new findings and
source pointers. Exact retry and fresh pull preserve the selected body and prior
metadata. No answering model or fixture-note capture was used. The
[verification manifest](verification/opencode-unicode-2026-09-10.json) retains
installation, native-check and lesson-update receipts.

[CI 34462181728](https://github.com/halbritt/cairn/actions/runs/34462181728)
completed successfully for `6c48411`: PostgreSQL/race, Python, static/build,
use-report and authenticated CLI/compact startup steps all passed. Native
OpenCode verification is the separate completed local check recorded above.


### 2026-09-10 — Retained history through native tools

Added `cairn_history` to MCP and native OpenCode using the existing authenticated
history read. Native-only callers can list retained version metadata and inspect
one exact earlier body without constructing CLI JSON. Historical labels and original
hashes remain visible; current pull/edit and version checks retain their meanings.
The Codex generator includes the new read tool. Existing allowlists require an
explicit update; native Claude/Agy execution of history is not claimed.

The API accepts an optional repository constraint, supplied from facade configuration.
A test exposed its omission in the first implementation; the corrected store check
refuses mismatched repository configuration within the read snapshot. There is no
migration or new stored state. Destination, deletion and output limits remain.
Full PostgreSQL/race, Go/40 Python, static and final MCP checks passed. Native checks
cover old bodies/digests, stable paging through an append, local-only exclusion,
invalid requests, output limits, stale edits and permission/repository refusals.
A scripted normal OpenCode session separately reads prior wording after edits.

The first native suite failed parsing debug output for a 64 KiB capture, repeating
a limitation already recorded in the note-transport report. That source was consulted
after the failure. The one large history-budget fixture now uses operator CLI setup;
native history and normal-session large capture remain under test. The corrected
complete native suite passed. No answering model or production fixture capture ran.
The [report](verification/native-history-2026-09-10.md) retains this maintenance cost,
recalled procedure, implementation evidence and unestablished net task benefit.


### 2026-09-10 — Native history installed and actual guidance compared

Committed and pushed `9b888e11c601d07c67e4cc1a2b5e0a9fffed04a2`. Installed
CLI/API and project binaries share SHA-256
`f94524c68502c4a86f4c1297ee12e7a309db6521728362c9c4636525c4593d49`.
API PID 1965492 serves that build. PostgreSQL PID 163669 and schema 033 are unchanged.
The native adapter matches source. Added only `cairn_history` to the project's
Codex enabled-tool list; connection, identity and semantic-worker configuration
were preserved. No global OpenCode permission edit was made.

A fresh native Codex 0.153.4 client discovered all six tools from actual project
configuration and inspected validation lesson versions 2/3. Native OpenCode 1.18.21
with only history allowed returned identical responses. Exact source hashes match;
the 2,964-byte current body preserves the complete 1,935-byte earlier body and
appends the verified Unicode finding. No lost wording or new correction emerged
from this comparison, and no independent answering-model task was run.

Revised history procedure `cd6217ad-2f69-45f1-bcfb-a01066909855` from version 1 to 2,
replacing the obsolete native-access limitation and recording the repeated large
debug-input fixture failure and correct test route. Fresh search/pull verifies its
body and preserved metadata. The [manifest](verification/native-history-2026-09-10.json)
retains installation, configuration and native-review evidence. This extends
ordinary access without establishing a new model-task benefit or closing the
broader usefulness requirements.

[CI 34464102538](https://github.com/halbritt/cairn/actions/runs/34464102538)
passed for `9b888e1`: PostgreSQL/race, Python, static/build, use-report and
authenticated CLI/compact startup steps all completed successfully. Native
Codex/OpenCode checks are the separate local observations described above.


### 2026-09-10 — Setup guidance corrected after interface changes

Reviewed the retained Codex v8 and OpenCode v14 setup procedures against installed
`9b888e1`, the generators and current search/history contracts. Both omitted native
history and the task-phase flag. Their context wording also omitted per-call
fill of host-unset fields; the OpenCode procedure incorrectly required restarting
a session to pick up changed connection settings.

Corrected Codex procedure `be11369a-4ae6-4600-9345-5ff36446d5ff` to version 9 and
OpenCode procedure `73537cbd-0ec3-4311-98c9-23e58a685b2f` to version 15 through the
native MCP tools. The revised passages distinguish fixed MCP startup values from
per-call declarations and OpenCode's per-call settings read, include history and
explicit capture pins, and clarify that `opencode-config --memory-only` enables
only search/body pull. Existing browse continuation, semantic prerequisites,
source checks, capture/edit rules and the longer observed-run/task-file guidance
remain intact.

Native history verified exact old and revised bodies; fresh current pulls and
exact edit retries passed. Reversing the five/six intended text substitutions
reconstructs each original body exactly. All record fields except body, version
and write timestamp are unchanged. The current Codex generator and native installer
also verified history availability and task-phase forwarding in isolated output.
No production configuration or binary was changed.

The combined bodies grew from 13,711 to 16,894 UTF-8 bytes. This is correction and
upkeep of memory after product changes, not an observed improvement in a later
model task; the extra reading and maintenance cost remains part of the evidence.
Private before/after bodies, requests and checks are under
`/tmp/cairn-setup-guidance-review/`. Its `result.json` SHA-256 is
`1ebb721a2acddad0e0ec20b209714d63f3921edcaab4c01d495d053d4fd145fe`.
Current public contracts remain [search context](search-context.md) and
[native history](record-history.md#native-tools).

### 2026-09-10 — Long-note ranking screen and semantic cost finding

At source `5537557` and installed CLI/API `9b888e1`, `Codex setup configuration`
still returned the OpenCode procedure first and Codex second. The appropriate
note remained visible; no wrong model answer was observed. A fixed comparison
retained twenty eligible hosted notes (57,544 bytes), twelve queries and dedicated
note labels before scoring. Complete baseline orders matched the current
paginated API and exact source versions/hashes.

One first-160-byte match tie-break, after lexical score and scope, moved dedicated
notes to first place for all twelve questions: baseline three with actual recency,
ten with reversed recency. Nine questions were authored from note subjects,
favoring the candidate. No question or parameter was tuned, no retired public
cohort was rerun, and no production ranker was selected. The
[report](verification/leading-context-2026-09-10.md) retains individual ranks,
limits and the material missing evidence for broader adoption.

Existing unfiltered semantic lookup returned labelled lexical fallback. A separate
installed-worker run successfully scored the same corpus in 20.015 seconds and
put Codex first; this is consistent with pressure at the twenty-second API
worker deadline, but does not prove that live failure's cause. Existing semantic
procedure filtering reduced input to seven notes/28,630 bytes and returned Codex
first in 10.181 seconds, then 0.060 seconds, with identical score digests. The
selected current v9 body was pulled and its exact hash checked. No answering-model
run, service percentile, generalization or incremental task benefit is claimed.

Worker guidance `225ad6f4-87ab-41af-a19c-1d38b53232c3` advanced v5→v6 through native
MCP. Current hash is
`ac8e2d3da16104904841661d5491de7652333e1e877ac1fdd7f0d0838d98c8bf`;
the edit preserves prior text and metadata, adds 809 bytes, and records the
observed route plus its fallible-label and timing limits. An exact retry and fresh
current pull passed. The comparison corpus predates this edit. This is selected
continuity work with added reading cost, not a performance repair.

The initial corpus collection exceeded a receipt's expansion budget and resumed
with two whole pulls per page. Doctrine receipt validation initially required an
explicit rival for an inference; that was added and validation passed. Private
inputs, responses, scripts and receipts are retained under
`/tmp/cairn-leading-screen/`; hashes and bounded results are in the
[metadata](verification/leading-context-2026-09-10.json). No runtime binary,
configuration, schema or default changed. The next material investigation is
cold semantic cost on current notes. The full preceding implementation history
is preserved.

### 2026-09-10 — Profile cold scoring; allow the current corpus to finish

Source following `a9a4b1c` changes both semantic worker transports from a twenty-
second deadline to a shared twenty-five-second budget. OpenCode remains at thirty
seconds and the API client/server at thirty-five. Earlier cancellation, busy
fallback, output bounds, group cleanup and thirty-second idle release remain.
The installed build is still `9b888e1` at this pre-deployment checkpoint.

The current twenty-note corpus was retained through ordinary hosted retrieval:
58,353 body bytes and fifty chunks, within existing size limits. Setup took
0.433 seconds; a profiled score took 20.412 seconds, with 20.321 cumulative seconds
in ONNX inference. A fresh actual API search returned unavailable lexical fallback
after 20.056 seconds. This narrows the investigation to inference work; it does
not prove the exact cause of every earlier fallback.

A fixed candidate grouped passages by length into batches of four and restored
original order before scoring. Three alternating fresh-process pairs preserved
every source-bound integer score. Baseline wall times were
23.889/21.051/21.904 seconds; candidate times were 21.400/20.948/21.612.
Median paired speedup was 1.014×, below the pre-run 1.25× target, and peak RSS rose
from about 220 to 305 MiB. Every candidate exceeded twenty seconds. The candidate
was rejected without tuning; worker Python, model, chunking, batching, thread
count and score identity stay unchanged. This is a new current-note workload,
not a rerun of the retired documentation/thread-count cohort.

The separate availability adjustment permits up to five seconds more work and
waiting rather than improving inference speed. Both actual transport regressions
first refused a twenty-one-second response, then passed with the new budget.
The unchanged real worker completed the retained corpus through the new one-shot
transport in 20.294 seconds and the stream in 20.118 seconds, followed by a
0.033-second repeated stream request. Complete results, including every source
identity/hash/version, integer score and model identity, matched the profiled
baseline. Existing semantic race/cancellation/cleanup checks, full disposable
PostgreSQL integration and Go race suites, forty Python tests and static checks
passed. Native deployment verification remains the next step at this checkpoint.

The live fallback also exposed a maintenance cost: the previous worker-guidance
update copied the exact setup query, causing that general performance note to
rank ahead of the setup procedures. Current guidance needs correction while the
full diagnostic case remains in verification history. The initial profiling script
also shadowed Python's standard `profile` module; it was renamed before successful
measurement. Neither cost is omitted from the record.

[Verification](verification/semantic-deadline-2026-09-10.md) and
[metadata](verification/semantic-deadline-2026-09-10.json) retain the measurements,
tradeoffs and private artifact hashes under `/tmp/cairn-cold-cost/`. Doctrine
packet `pkt-ba3bab962ec3ebec`, typed evidence and a validated decision receipt
separate the rejected optimization from the availability decision. Broader
workload/environment evidence remains missing; no general speedup, service
percentile, answering-model acceptance or net memory benefit is claimed.

### 2026-09-10 — Install and verify the semantic deadline adjustment

Clean source `a848e3c0051b4dabc334613d8fcc3ce969b550dd` is installed as the ordinary
CLI, project CLI and running API. Binary SHA-256 is
`8d0f1836bca1532ea2643a43890e6fbf23778ba14c0e16ccb5cd19e8ef581e2f`;
API PID is `2093518`. Preflight found no API-owned children before restart.
PostgreSQL PID `163669`, schema 033, native adapter, semantic worker/model,
configuration and identities are unchanged. The prior intentional Codex history
allowlist change was reconciled against its separate configuration receipt.
The feature's CI run `34468167658` succeeded; all jobs and steps were inspected.

Actual native OpenCode debug execution started with no existing API worker and
completed unfiltered semantic search in 25.210 seconds including native process
startup; the pull took 0.942 seconds. A fresh ephemeral Codex app-server thread
used the actual project configuration without MCP overrides and completed its
search in 20.494 seconds. Both returned ready, ranked the Codex procedure first
and pulled exact v9 SHA-256
`92d3d1461c35b7543a188406ace2facdfa38ddadb6c152aace89f7de6fa59850`.
Their complete corpus score digest was identical:
`3b9db33eed6e6f451f3828ccfc7f1b2cb24d232e2be6aaa242ce6d7aef6a8fc1`,
also matching the unchanged worker's direct result. This verifies the observed
retrieval route inside both clients' outer limits; no answering model chose or
used the note, and no downstream task acceptance is inferred.

Worker guidance `225ad6f4-87ab-41af-a19c-1d38b53232c3` advanced v6→v7. Its current
paragraph states the twenty-five-second budget and fallible kind-filter option,
removing the incidental setup query that displaced the actual procedures.
All other text and metadata are preserved; native history returned the exact v6
body. An identical edit retry and fresh current pull passed. Current SHA-256 is
`99fce4e09c2939972b7460e013c50766ad79fa95572887c1f3eaf9a32e38e539`;
body size changed 6,319→6,296 bytes. Fresh lexical lookup again has OpenCode first
and Codex second, so only the extra maintenance-induced displacement is repaired.
The native timing and score checks predate this final note edit. The first
scratch correction script had an indentation error and performed no edit before
it was corrected.

The [report](verification/semantic-deadline-2026-09-10.md) and
[metadata](verification/semantic-deadline-2026-09-10.json) include deployment and
private evidence under `/tmp/cairn-cold-cost/deployment/`. The previous binary is
retained there. This closes the installation checkpoint above, while leaving
cold inference cost, larger-corpus availability, ranking quality and demonstrated
sustained task value open. The complete preceding implementation history is kept.

### 2026-09-10 — Curate current worker guidance without erasing its history

Ordinary worker guidance `225ad6f4-87ab-41af-a19c-1d38b53232c3` advanced v7→v8.
Its current body now contains operating instructions and source pointers rather
than multiple superseded experiment narratives. Cache contents and lifetime,
current eligibility, input limits, numerical settings, request/client deadlines,
cancellation and cleanup, update adoption, process inspection, and restrictions
on repeating rejected experiments were checked against current source before
editing. The known lookup title remains. The record's `lesson` label and all
other metadata are preserved.

The body decreased from 6,296 to 3,365 bytes, a reduction of 2,931 bytes (46.6%).
This reduces the bytes needed for a whole current pull; it does not establish
faster inference, better ranking or improved model-task quality. The old wording
and measurements remain in exact retained v7 and the existing verification
reports. Native MCP verified that complete historical body both before and after
the edit, checked an identical edit retry, and pulled the exact current v8 body.
Current body SHA-256 is
`cb554748454149321d8bda93eeced21c925fe9b0f860b7a1990665eb2f1cf8c7`;
retained v7 remains
`99fce4e09c2939972b7460e013c50766ad79fa95572887c1f3eaf9a32e38e539`.

This used the existing body-only edit and native history workflow on installed
`a848e3c`. No new runtime, automatic groomer, benchmark or answering-model trial
was introduced, and retired experiments were not reopened. Private before/after
bodies, selected current-requirement/source mapping, source hashes and receipts
are under `/tmp/cairn-guidance-curation/`. Its `result.json` SHA-256 is
`36488377fda593e7aa8c4319e3e9fe93dd32ca9c139f500f6cd64ada2db4892e`.
The complete preceding implementation history remains intact. This is selected
maintenance with a measured context-size reduction; net task value is unmeasured.

### 2026-09-10 — Reconcile native integration guidance with current compatibility

The main [native integration guide](native-striatum-context.md) still described
acquisition, build consumption and host correspondence as unfinished in several
places, although later checkpoints implemented them. It now leads with current
branch implementation, accepted-contract status, the two failed model comparisons
and their retired unchanged task/binding. Earlier design requirements and
checkpoint notes remain labelled as historical. The roadmap points to this
current view rather than implying another producer or supervisor is needed.

Source inspection confirmed Striatum main `5ea87ca65c1bd25f228c0447110d991a3f0de8c8`
and integration branch `ee8a463c9978833bc53d2726eef63eb9417fcb91`. The accepted
catalog remains observation 1/build 3; RFC 0004/0007 amendments remain proposed.
The existing watch supports schema-3 body compilation, not Cairn's later kind,
task-phase, failure-signature, semantic-discovery or index/pull options. Current
Cairn still emits schema 3 for that watch. No schema extension is needed to keep
its existing request working; a task needing a newer option would require one.

The existing opt-in `TestCairnService` ran with the race detector against installed
Cairn `a848e3c0051b4dabc334613d8fcc3ce969b550dd`, binary SHA-256
`8d0f1836bca1532ea2643a43890e6fbf23778ba14c0e16ccb5cd19e8ef581e2f`.
It owns a disposable PostgreSQL cluster, API and graph. Local/hosted capture,
exact child rendering, observer ownership refusal, request acquisition, offline
replay and observation production passed. The first run skipped native host
execution because the shell's cgroup was not delegated. A second run under
`systemd-run --user --scope --quiet -p Delegate=yes`, with
`STRIATUM_REQUIRE_CGROUP=1`, passed every subtest, including a real supervised
shell/outcome and refusal before launch after a selected note changed.

- Initial log: `/tmp/cairn-native-current-20260910.log`, SHA-256
  `b032d6461d04bf7d6d2b6cc09ee754d57f742b3a51d327ec56982cecc2bd2884`.
- Delegated log: `/tmp/cairn-native-current-delegated-20260910.log`, SHA-256
  `29495f8b4940977069fc85b0e508909064507175cf0890757963d404d80974e9`.

This verifies current component compatibility; full Driver build admission and
model-task comparisons were not rerun. It adds no task-value result or contract
acceptance. No Striatum source, live graph, installed binary or configuration was
changed. The unrelated untracked `cmd_test.go` in Striatum main was left intact.
Documentation claims and links were checked against source and retained reports;
the complete preceding implementation history is preserved.

### 2026-09-10 — Let the host retain semantic vectors across spaced follow-ups

`serve --semantic-idle-timeout DURATION` now selects a positive idle lifetime for
an explicitly configured streaming worker. The existing default remains 30 seconds;
the library exposes the same choice through `StreamCommandWithIdleTimeout` while
preserving `StreamCommand`. A nonpositive interval or an idle option without
streaming mode refuses. Request deadlines, busy fallback, eligibility, score
validation, model identity, cache contents and cleanup behavior remain unchanged.
The [semantic guide](semantic-discovery.md#local-backend) explains the memory and
latency tradeoff and a five-minute host configuration.

The actual installed baseline, `a848e3c`, served two identical current-corpus
semantic searches separated by 35 seconds after the first response. Both began
with no API-owned worker and returned identical source pins and complete score
digests. The first took 20.696 seconds and the follow-up 24.851 seconds. Loaded
worker RSS was about 220 MiB. This reproduces the repeated cold-work cost across
an idle gap; it is one pair on a shared host, not a service latency distribution.
The fixed comparison plan requires the candidate follow-up below half the baseline
wall time, identical full discovery/source pins and worker RSS below 300 MiB.

The public idle-release test initially failed to compile without the new entry
point, then passed through it. Nonpositive library intervals and misplaced CLI
options each failed before their validation was added. Disposable PostgreSQL/race
integration and static checks passed. A later CLI test additionally checks that
nonpositive durations return `INVALID_REQUEST` before opening service state;
its affected-package race rerun is recorded with the installation follow-up.
The complete previous implementation history is preserved.

Private baseline responses, the frozen plan, probe and check logs are under
`/tmp/cairn-semantic-idle-20260910/`. The selected next step is a reversible
five-minute configuration trial on this host after the final checks, preserving
worker/model files and other host settings. Candidate performance, installation
and retained task benefit are not established by this source checkpoint.

### 2026-09-10 — Install and verify the spaced semantic follow-up improvement

Clean `72e7c243ae211acc3c04b4520e5bb4d5676200e6` is installed as CLI and API;
SHA-256 is `c046c983a48c04918a8c9ed8d102143faef8d0a917291b9985aeb0c6bbc21911`.
The API restarted at PID 2934824 with `--semantic-idle-timeout 5m`. PostgreSQL
remained PID 163669/migration 033. Previous Cairn executables and the API configuration are
retained under `/tmp/cairn-semantic-idle-20260910/deployment/`. Identity, native
adapter, Codex/OpenCode settings and Python/model files retained their hashes.
Final affected-package race tests and static checks passed after the extra CLI
nonpositive-duration validation; earlier full disposable integration also passed.

Under the fixed 35-second idle-gap comparison, candidate cold scoring took
20.951 seconds and the follow-up 0.063 seconds, compared with baseline cold
20.696 seconds and follow-up 24.851 seconds. The baseline lost its child before
the follow-up; the candidate retained the same child. Every full discovery digest,
source seal and returned source version matched across all four calls. Candidate
RSS was 225,288/225,292 KiB. The predeclared follow-up latency and memory target
passed. This removes repeated cold work in the measured case; cold performance,
representative traffic, task quality and net memory benefit remain unestablished.

Ordinary worker guidance `225ad6f4-87ab-41af-a19c-1d38b53232c3` advanced v8→v9,
3,365→3,557 bytes, documenting the host setting and unchanged default. Current
SHA-256 is `0d4370853ef619383ace281aa0d7d0bf7abb3c4d2bdc28cab4c05fb03cf59abf`.
MCP checked exact retry, fresh pull, unchanged metadata and the complete retained
v8 body. The comparison precedes this note edit. It added no incidental task
query to the general guidance.

The [report](verification/semantic-idle-2026-09-10.md) and
[metadata](verification/semantic-idle-2026-09-10.json) retain the measured limits,
private evidence hashes, validated doctrine decision and source checks. No
answering-model trial, new cache store, ranking change or native contract change
was introduced. All preceding implementation history remains intact.

The final idle-release observation found the candidate child alive at 299.872
seconds and absent at 300.872 seconds after the final response report, using
one-second polling with no intervening semantic request. This verifies that the
installed five-minute setting still releases the worker. Exact feature CI run
34505340594 completed successfully; all job steps were inspected. These results
are included in the same verification metadata.

### 2026-09-10 — Connect existing filename search to the agent workflow

Repository agent instructions now explain ASCII-double-quoted filename, symbol
and exact-error lookup, with a complete native query example and the existing
matching limits. They previously directed agents toward lexical or semantic
search without explaining how to prefer a known source identifier. The change
uses the already-installed quoted matcher; no new argument, ranking or store
representation is introduced.

Three actual hosted searches on installed `72e7c24` named `semantic/stream.go`,
`core/currentness.go` and `scripts/check_mcp_currentness.py`. They returned,
respectively, current worker guidance v9, the applicability-conjunction lesson v1
and capture-applicability guidance v2 first. Each current body was pulled and its
record/version/SHA-256 checked against the returned handle metadata; the guidance
was read alongside current source. Search elapsed times were 0.034, 0.027 and
0.028 seconds. This is an explicit lookup check, not a relevance benchmark or
proof that a later agent will choose and apply the instruction.

The accepted design separately calls for entity matching and file/symbol-touched
triggers. Existing substring matching does not establish typed identity or observe
workspace activity. E1 retains those open requirements. Adding a flag which only
quotes a path would duplicate the tested interface without supplying either.
Private searches, pulls and checked metadata are under
`/tmp/cairn-file-context-20260910/`; no operational bodies enter Git. The agent
example and documentation links were checked, and all prior implementation
history is preserved. No answering-model task or new task-value result is claimed.


### 2026-09-10 — Add explicit versioned file and symbol associations

Implemented explicit `entities` on note capture/edit and retrieval, using exact
kind/name overlap within existing repository and applicability gates. File names
are canonical relative paths; symbols are opaque qualified labels. No filesystem
observation, alias/rename resolution or authority is inferred. Ordinary CLI,
MCP and native OpenCode expose capture and search hints; indexes, pulls and exact
history show saved associations. Body-only edits preserve them; complete draft
edits replace them as a new version. Migration 034 stores associations by record
and version, with ordinary deletion and payload purge accounting.

A supplied entity match can retrieve a note whose body omits the filename and
rank it ahead of an incidental body mention. Entity-only queries and ranked pages
are supported; text queries retain broader candidates. Exact entity and reviewed
failure matches share the first optional relevance tier, followed by existing
text/scope/recency behavior. Semantic discovery and lexical fallback retain the
same preference. Mandatory instructions and consequential-use gates remain.

Historical replay retains normalized query and version-association digests;
retained execution checks entity intent before binding. Verification found and
fixed missing history metadata, ignored retained hints, unpurged associations,
mixed failure/entity ordering, unrelated schema changes, and replay of old readers
that had ignored newer metadata. An early test used invalid revision labels and
an MCP assertion retained old error wording; those were fixture corrections.

Full disposable PostgreSQL/race/API/MCP integration, static checks and forty
Python tests pass. Authenticated CLI and actual OpenCode 1.18.21 custom-tool
checks cover capture/search/pull/body edit/full-draft change/history. An actual
prior 72e7c24 binary's receipts and cached responses reproduce under the new
reader. Native checks precede only the final consequential-demand accounting
change; final full integration covers that change.

The source capability is verified; installation is recorded separately below.
This closes the explicit typed-association portion of E1, not automatic host
intent collection, rename continuity or durable task-value acceptance. The
fixture demonstrates the intended distinction, not representative retrieval
quality or incremental model benefit. No answering-model task was run.
See [contract](entity-search.md) and [checks, failures and limits](verification/entity-search-2026-09-10.md).


### 2026-09-10 — Install entity retrieval and use it through both native harnesses

Installed clean `3ff1fcd435121d5873f8890a2a5396ac5ed275d9` in the local CLI,
project binary and running API. Migration 034 followed a catalogued database
backup. The API was stopped for the upgrade and restarted as PID 3062046;
PostgreSQL PID 163669 stayed unchanged. The updated native OpenCode adapter is
installed. Existing Codex/OpenCode connection settings, identity configuration,
semantic worker and five-minute host idle setting retain their prior hashes.
Binary SHA-256: `695b3fe7826a649dce3340544a6e8ec926369fe21c823faf690b17be6c3f9cdb`.

Used the ordinary hosted profile to associate the existing applicability lesson
with `core/currentness.go` and `core.applicabilityReason`. This created v2 while
preserving the complete body and all other supplied draft metadata. Exact edit
retry returned the same version, and the v1 body remains readable without the new
associations. The earlier quoted lookup returned five previews; explicit
entity-only lookup returned the one associated note. These different queries
illustrate the association contract, not comparative general retrieval quality.
An initial live probe used flag syntax for JSON-only history; it was corrected
and resumed using the retained edit request, without another note revision.

Fresh Codex 0.153.4 app-server/MCP access using the project configuration searched
by file; actual OpenCode 1.18.21 custom-tool access searched by symbol. Both pulled
the same v2 source and unchanged body hash. Neither involved an answering-model
turn. These establish installed access through two harnesses, not incremental
model-task value or full E1 completion.

Installation and selected operational notes remain private under
`/tmp/cairn-entities-20260910/deployment/`. The repository retains the compact
[verification manifest](verification/entity-search-2026-09-10.json) and report.

GitHub Actions run `34510287787` completed successfully for `3ff1fcd`, including
Go race tests, Python tests, static/build checks and authenticated CLI/startup
verification. Every job step was inspected.

### 2026-09-10 — optional native recent-file intent

Implemented an opt-in OpenCode plugin for successful native text-file reads.
It keeps at most 16 relative file names per session and 128 sessions, with
15-minute per-file expiration checked on hook activity. It adds hints only to
fresh first searches without explicit entities or request IDs. Explicit empty
hints disable collection, retries/pages preserve caller intent, and capture
never inherits the observed names. Ordinary tools retain permission checks;
no system hook fetch, file body cache, symbol inference or store migration was
added. `opencode-install --recent-files` installs the bundled plugin and retains
the existing default installation and custom-file preflight rules.

Native OpenCode 1.18.21 with a scripted loopback provider and disposable Cairn
API passed five cases: no-plugin baseline, successful retrieval/pull with the
plugin, global search denial, repository-specific permission denial, and denied
read. The enabled case preserved an original receipt after another read and
captured an unassociated note. Contract and installer checks, full disposable
PostgreSQL/race integration, static checks and 40 Python tests passed. No
answering-model inference or durable task-value claim was added.

The initial missing-capability tests failed before implementation. Two denial
assertions were corrected to actual host error semantics. One subsequent native
startup timed out before tool output; its cause remains unknown and its evidence
is retained. A complete rerun passed without raising the deadline. See
[the verification report](verification/recent-file-hints-2026-09-10.md).

The current installation summary was also refreshed: its older September 9
build and migration claims had become stale while later installation evidence
accumulated elsewhere in this document. All prior implementation history is
preserved verbatim. The plugin is not yet installed in this source snapshot.

### 2026-09-10 — recent-file intent installed and existing guidance retrieved

Installed clean CLI `670cf19`, its matching native adapter, and the opt-in
recent-file plugin in this project. API `3ff1fcd` and PostgreSQL retained their
processes and executable identities; schema034 and all recorded connection,
credential, host permission and semantic configuration hashes were preserved.
No service restart or database migration was required.

An actual native OpenCode1.18.21 session in the real repository read
`core/currentness.go`, called `cairn_search` with empty arguments, then pulled the
existing applicability guide v2 through the ordinary hosted profile. Its body
hash matched the previously stored guide. Four main scripted provider requests
were used, with no answering-model inference. The first installation probe had
used an absolute read allow-pattern; OpenCode checks a worktree-relative path,
so that read was correctly refused. Correcting the probe to permit only
`core/currentness.go` exercised the intended allowed path. Product and global
host permissions were unchanged.

The OpenCode procedure advanced from v15 to v16 with the new installation,
permissions, override and continuation guidance. It gained deliberate file
associations to the native adapter, plugin and installer; exact edit retry,
ordinary file retrieval/pull and retained v15 body were checked. Its other draft
metadata remains unchanged. This is maintained guidance and working retrieval,
not evidence of a completed model task or net durable benefit. See
[the installed report](verification/recent-file-hints-2026-09-10.md#local-installation).

Feature CI **34513401855** passed at `670cf19`; both jobs and every step were
inspected. The Node plugin contract job and existing PostgreSQL/race, Python,
static/build and authenticated CLI/startup checks succeeded.

### 2026-09-10 — separate retained OpenCode guidance by workflow

The ordinary OpenCode procedure had accumulated 14,087 bytes at v16. Its
5,384-byte section about explicit preload, observed compact execution and
saved-task input was moved verbatim into a separate procedure. The original
note advanced to v17 (9,270 bytes), retaining its other text, metadata and file
associations plus a pointer to the launcher note. The new launcher procedure is
v1 (6,229 bytes), associated with `cmd/cairn/agent_start.go` and `runner/index.go`.
No original passage was dropped or rewritten, and v16 remains in ordinary history.

Ordinary file-entity searches and full pulls returned both new current bodies.
Exact capture/revision retries were idempotent. Comparison of returned current
and historical bodies verified every original segment, including the browse
`offset=N` and semantic-worker prerequisite previously lost in an earlier
consolidation. Current launcher code was inspected for the preserved input and
reader contracts. Software, services and configuration were unchanged.

Setup-only body delivery is 4,817 bytes (34.2%) smaller. Reading both notes costs
1,412 bytes more, and historical storage grows. These are byte measurements and
an editorial workflow choice, not proof of better model decisions, an avoided
failure, net savings or durable task benefit. No new model calls or automated
grooming were introduced. See [the maintained procedure report](verification/procedure-maintenance-2026-09-09.md#2026-09-10--separate-ordinary-tools-from-harness-launch-instructions)
and its exact partition evidence. All prior implementation history is preserved.


### 2026-09-10 — qualified competing advisory positions

Implemented explicit `advisory_conflicts` context retrieval through CLI, MCP,
OpenCode and host start/run. Optional disputed A/B positions previously disappeared
from retrieval. The opt-in now allocates complete marked previews or bodies,
and pulling either preview returns the requested `selection` plus all `competing`
positions under one shared credit/byte budget. Overlapping groups stay together;
independently recorded equal-text positions are retained.

Every counterpart must qualify for destination, scope, applicability, currentness,
evidence and authority at its originally disputed version. Incomplete/private
components are omitted without hidden IDs, bodies, reasons or counts. Binding C
conflicts still refuse. Connected delivery components are bounded to 16 positions
and 16 groups. Fresh pulls and retained launch recheck the full component before
cached delivery; frozen historical recompilation preserves the original positions.
Companion bodies participate in ordinary usage and deletion accounting. Schema 14
marks explicit intent; the database remains at migration 034.

The initial regression confirmed absent delivery. A draft assertion was refined
from inline index bodies to compact previews with whole-group pulls. Review later
reproduced an excluded failure-signature reference breaking historical replay;
clearing it alongside the excluded candidate facts fixed the defect. Complete
PostgreSQL/race integration, CLI/API/MCP, native OpenCode 1.18.21 protocol checks,
previous-binary compatibility, static checks and 40 Python tests passed. No model
calls or operational conflict mutations were made. See the
[verification and limitations](verification/advisory-conflicts-2026-09-10.md).

This is implemented retrieval behavior, not evidence of incremental task value.
Richer interested-party resolution and acted-under-conflict outcomes remain L2
work. The source checkpoint precedes installation; the previous installed CLI
670cf19/API3ff1fcd remain the baseline until a subsequent installation entry.


### 2026-09-10 — install advisory retrieval without changing stored notes

Installed clean `4df17afa97d2c3d457932ebb2bc7373f2d0fe83a` in the local CLI,
project binary and API, and updated the installed OpenCode adapter. A current
backup preceded replacement. The API restarted as PID 3338966; PostgreSQL remained
PID 163669 and migration 034. Record-version count and content/metadata digest
matched before and after. Credentials, semantic worker/configuration, Codex and
OpenCode configuration and the recent-file plugin hashes were preserved.

The installed authenticated hosted profile accepted advisory intent and returned
semantic schema 14 while retrieving the existing applicability guide
81751203-8768-49ee-bfce-895efb69889d v2. Its full pulled body still had SHA-256
617555818bf48ce98cdfdfb3204c4cbf821685e6d718c1006c4ab96dd26374ca.
This verifies the installed ordinary route, not a real disputed task benefit;
no operational conflict or memory body was created, edited or resolved. Exact
build, service and preservation evidence is in the
[verification record](verification/advisory-conflicts-2026-09-10.json).

[CI 34518961970](https://github.com/halbritt/cairn/actions/runs/34518961970)
subsequently completed successfully for the installed source. Both jobs and every
recorded step passed.


### 2026-09-10 — Historical replay requirements and recorded-run check

The E3 audit separates implemented retained-read-set reconstruction from the
remaining historical evaluation and access requirements. Documentation now makes
original query/entity intent, receipt ownership and missing-history limitations
explicit. It also corrects stale wording about semantic schema 3, historical
ranker v3 and per-call MCP context. Runtime code, migration 034, operational notes
and the installed `4df17af` CLI/API remain unchanged.

`TestHistoricalRecompileExcludesLaterNotesAndInstructions` verifies that a fresh
compile receives a later note and mandatory instruction while an earlier receipt
keeps its original selection and seal. The focused disposable PostgreSQL run also
passed correction/evidence, policy revision/revocation, semantic-score, task-phase
and advisory-conflict freshness checks. `make check` passed. These are regression
and reconstruction results; no new failure repair or task-value gain is claimed.

A separate disposable copy of the reviewed September 8 failed OpenCode trial
verified all eight original artifact hashes before and after inspection. Its
original operator preflight recompiled to the actual launched host package seal.
Read-only inspection found identical frozen candidate sets, with the selected
note and evidence captured before that recorded run. The lesson written after
the failure stayed excluded. Direct CLI reconstruction of the host receipt
returned `AUTHORITY_DENIED`, as required by receipt ownership; the authenticated
API has no historical recompile route. No owner was changed or impersonated,
model rerun or workspace recreated, and the disposable cluster was removed.

This establishes reconstruction relative to that recorded recurrence, whose
advice still postdates the older Striatum incident. E3/E4 remain partial. The
roadmap now names the authenticated historical-reader gap alongside the missing
original-incident evidence. A universal temporal store was not justified by this
case. The ordinary hosted profile supplied the existing applicability lesson;
its guidance agrees with current source, but that reuse adds no independent
memory-benefit claim.

The [audit report](verification/replay-requirements-2026-09-10.md) and
[metadata](verification/replay-requirements-2026-09-10.json) retain source hashes,
cutoff timestamps, selection identities, checks and private scratch locators.
Pincite packet `pkt-7d3944f3e1e70a3d` includes typed evidence, a validated decision
receipt and closed citation traces; 26 generic obligations remain explicitly
nonmaterial to this documentation/test decision. The prior implementation history
is preserved byte-for-byte.


[CI 34521354667](https://github.com/halbritt/cairn/actions/runs/34521354667)
completed successfully for audit source `e8bcf65`. Both jobs and every recorded
step passed, including the PostgreSQL race suite, Python tests, static/build
checks, authenticated CLI/compact startup and OpenCode recent-file plugin checks.
This corroborates the test/documentation change; the installed runtime is still
`4df17af` and the historical evidence gaps above remain open.


### 2026-09-10 — Authenticated historical receipt reconstruction

Added `POST /v1/recompile` and `cairn agent ... recompile` for authenticated agents
and observers to inspect their own retained receipts. The operation uses the
original query/entity intent and returns `historical: true` with the exact
package. It preserves caller/repository ownership and the original destination,
checks current privacy for every returned body/index preview, and refuses forgotten
content. The existing trusted core/local CLI interface, semantic schemas, six
ordinary MCP tools and database migration 034 are unchanged.

This addresses the preceding audit's concrete host-access gap. A disposable
restore of the reviewed September 8 trial used its original observer token and
identity configuration to reconstruct the actual host receipt, not just its
matching preflight. The original seal, revision and selected record version/body
digest reproduced; the later failure lesson stayed excluded. An identical retry
was exact, while the collector and local operator were refused. All eight source
artifact hashes, credential/configuration hashes and inspected receipt/candidate/
use/delivery/outcome/assessment tables remained unchanged. The temporary API and
database stopped. No model task ran or operational memory was edited.

The first API regression failed because the endpoint was absent. It then passed
with exact old selection after revision/new advice while current execution still
refused stale context. Further core/API checks passed for agent/observer access,
foreign owners, changed query, injected identity/destination, body/index privacy,
destination mismatch and forgetting. Full disposable PostgreSQL race/API/MCP
integration and `make check` passed; an additional focused check preserved
historical reconstruction after policy revocation while execution remained
refused. No new Go interface, service or recovery framework was added.

E3/E4 remain partial: this is reconstruction of a recorded recurrence whose
advice postdates the older Striatum incident. The rejected outcome and recurrence
classification remain unchanged, and incremental task benefit is unmeasured.
The [verification report](verification/authenticated-recompile-2026-09-10.md) and
[metadata](verification/authenticated-recompile-2026-09-10.json) retain the evidence
and limitations. Pincite packet `pkt-d95c8e9d819ddc26` has typed evidence, a
validated decision receipt and closed citation traces; four Go-interface design
obligations are recorded as nonmaterial. Installation is recorded separately
below when complete. All prior implementation history is preserved byte-for-byte.


### 2026-09-10 — Authenticated reconstruction installation

Installed clean source `3f17cf2959054666bd01e9112c8c2563497d96f9` as both the local
CLI and API after backing up the operational store. The API restarted at PID
3475969; the PostgreSQL service remained active and migration stayed at 034.
All 77 retained record versions and their combined digest were unchanged, as were
the identities, harness configuration, OpenCode adapter/recent-file plugin and
semantic-worker settings. Exact hashes and backup provenance are retained in the
[installation metadata](verification/authenticated-recompile-2026-09-10.json).

Using the existing ordinary hosted profile, the installed command reconstructed
its earlier receipt `9ae9d867-67b0-4bb9-8dac-3838117c7909`, preserving the original
five-entry semantic-schema-13 index and seal. It created no new operational note
or authority. This verifies the installed historical access path; it does not
change the older recurrence outcome or establish incremental task benefit.


### 2026-09-10 — UTC historical-package assertion correction

[CI 34523060478](https://github.com/halbritt/cairn/actions/runs/34523060478)
failed in two new core assertions while its OpenCode plugin job passed. A local
UTC reproduction established equal timestamp instants and identical serialized
packages, but different internal `time.Time` location pointers. The new tests
had used `reflect.DeepEqual` on process-local representations. They now compare
the complete serialized historical package, preserving checks of all returned
fields and the seal. Targeted checks pass in UTC and America/Los_Angeles.
Production code and the installed `3f17cf2` build remain unchanged. The failed CI
log and reproduction are retained in the verification metadata; a corrected-source
CI result is recorded separately when complete.


[CI 34523528107](https://github.com/halbritt/cairn/actions/runs/34523528107)
completed successfully on corrected source `8732b91`. Both jobs and every recorded
step passed, including the UTC PostgreSQL race suite, Python tests, build/static
checks, authenticated CLI/compact startup and OpenCode plugin checks. Production
source hashes match the installed `3f17cf2` build; the correction changed tests and
documentation only. The initial failed run remains recorded above.


### 2026-09-10 — Selected additive note updates

Added `Store.Append`, authenticated `/v1/append`, CLI `append` and the exclusive
`append` argument on the existing MCP/OpenCode `cairn_edit` tool. The operation
adds a caller-supplied suffix inside the existing serializable revision
transaction; prior body bytes, draft metadata and citations remain. Exact retries
return the original identifiers without adding twice, stale versions conflict,
and the combined 65,536-byte limit refuses overflow without committing a version.
This keeps six ordinary tools and schema 034. Replacement remains necessary for
corrections and consolidation; additive updates still require source checking.

The first API check failed for the absent endpoint, then passed. Targeted core
checks and the full disposable PostgreSQL race/API/MCP suite pass, as do static
checks and all 40 Python tests. Native OpenCode 1.18.21 passed the final direct
and normal-session checks with scripted loopback responses and no inference.
The checks cover exact suffixes, metadata/history, degraded citations, concurrent
append/full-edit races, request conflicts, maximum escaped input, authority
refusals and native argument validation. Prior-binary mutation retries remain
unchanged. Early fixture failures and the native run using a previously loaded
expected-error assertion remain recorded in the [verification report](verification/note-append-2026-09-10.md).

This is an additive maintenance capability, prompted by observed rewrite omissions
and a selected update to the existing history procedure. It does not solve
consolidation or establish independent downstream task value or net savings.
Installation and operational use are recorded separately when complete. Pincite
packet `pkt-60a6733bfe653db3` has a validated decision receipt and closed citation
traces; four interface-design obligations remain explicitly nonmaterial. All
prior implementation history is preserved byte-for-byte.


### 2026-09-10 — Append installation and procedure maintenance

Installed clean `664472c4288f937bdd2819d0c36ca334c36eb2fe` as the CLI/API and
matching native OpenCode adapter after a store backup. The API restarted at PID
3598568; PostgreSQL stayed active at migration 034. All 77 retained versions and
their combined digest were preserved, along with credentials, configuration,
recent-file plugin and semantic-worker settings.

A native OpenCode edit then appended selected, source-checked receipt-recompile
guidance to the existing history procedure, advancing v2 to v3. Its earlier
2,657-byte body is an exact prefix of the new 3,413-byte body; metadata and the
retained v2 body are unchanged. An identical retry returned the same result
without a second append. All 77 earlier versions still match the pre-installation
digest; one selected update brings the retained count to 78. The call used the
ordinary hosted profile and exact installed adapter bytes in an isolated client
workspace, without model inference.

This completes an additive maintenance task without rewriting the old text in the
mutation input. It is self-maintenance, with no independently observed downstream
outcome or net savings claim. The [report and metadata](verification/note-append-2026-09-10.md)
retain exact hashes and route details. Earlier implementation history is unchanged.


[CI 34526655526](https://github.com/halbritt/cairn/actions/runs/34526655526)
passed on installed append source `664472c`. Both jobs and all recorded steps
passed, including the UTC PostgreSQL race suite and OpenCode plugin verification.
Production hashes still match the installed build. This follow-up records results
and installation only; the earlier failed fixtures and native check remain in
the history and verification metadata.


### 2026-09-10 — Current tool inventory documentation correction

Corrected stale five-tool claims in the roadmap's current Codex/OpenCode and
Claude-generator descriptions. The installed `664472c` facade advertises six
ordinary tools, including `cairn_history`; generated Codex, Claude and OpenCode
configurations launch that same command, and Codex's explicit allowlist contains
all six. The installed edit schema also advertises the append argument. A
read-only stdio initialization and tool-list check verified those claims without
calling a memory tool, invoking a model or changing host settings. Verification
script and result: `/tmp/cairn-tool-guide-20260910/check.py` and `result.json`.

The individual setup guides already described history correctly. The roadmap now
also distinguishes current availability from the historical Claude native check,
which exercised the original five tools. Native Claude history remains unverified;
this correction adds no harness execution or task-benefit evidence. Dated prior
claims and all committed implementation history remain unchanged.


### 2026-09-10 — Preserve known instruction refusal gates

Corrected a retrieval-diagnostic mismatch: a required instruction could refuse
compilation while its candidate trace reported `mandatory: false` and
`EVALUATION_INCOMPLETE`. The compiler returned before copying already-known gate
results. Mandatory category overflow could instead appear `SELECTED` before
admission passed. Known mandatory flags and reasons now reach the protected
trace, with explanation version 2 for new compiler refusals. Admission and error
behavior, the early stopping point, privacy restrictions and the 1,000-candidate
cap are unchanged. Traces remain partial; R5 is not closed by this repair.

The initial body/index regressions reproduced runtime, missing-context and dispute
misclassification, plus the category label error. A two-policy test incorrectly
expected both positions to be evaluated; normal issuance already creates a
dispute, so compilation correctly stops at the first. Its expectation was corrected.
An API fixture needed exact bootstrap request reuse across destinations, and a
CLI assertion needed its expected explanation version changed to 2. Those failed
runs remain in the [report and verification metadata](verification/refusal-gates-2026-09-10.md).

The final targeted checks, disposable PostgreSQL race/API/MCP suite and static
checks pass. Local authenticated readers receive corrected diagnostics; hosted
owners remain denied access. Existing version-1 observations and exact retries
remain unchanged. The retrieved phase procedure was read fully after the first
reproduction and corroborated expected context gating; it did not establish
memory-led discovery, independent downstream task value or net savings.

Pincite packet `pkt-0bf5356afef1acdc` has a validated decision receipt and closed
citation loops. Seven interface/nil-contract obligations remain nonmaterial.
Installation is recorded separately when complete. All prior implementation
history remains byte-for-byte intact.


### 2026-09-10 — Refusal diagnostic installation

Installed clean `c5fa55ad4b0b9c85eb6cb39c83acdac2412a8040` in the CLI and API after
a store backup. The API restarted at PID 3655879 and its reported build matches
the client. PostgreSQL stayed active at migration 034. All 78 retained record
versions and their combined digest are unchanged, along with credentials,
configuration, the OpenCode adapter/recent-file plugin and semantic-worker settings.
No operational note or policy fixture was created for this fix. Source, backup,
service and preservation hashes are retained in the [verification metadata](verification/refusal-gates-2026-09-10.json).
The repaired diagnostic behavior was verified against disposable stores; no
independent downstream task benefit or full R5 acceptance is claimed.


### 2026-09-10 — Refusal API fixture in the CI database

[CI 34528936872](https://github.com/halbritt/cairn/actions/runs/34528936872)
failed in the new API test: CI shares one database across package suites, so the
core suite's root was already installed. Local integration isolates each package
and passed. The test now reuses the core suite's exact synthetic operator and
bootstrap identity. A local UTC, race-enabled core→API sequence against the same
disposable database passes, as do static checks. The production sources and
installed `c5fa55a` build are unchanged. The failed CI and targeted correction
remain recorded in the verification metadata; corrected-source CI follows.


### 2026-09-10 — Refusal diagnostic CI completion


[Corrected CI 34529414559](https://github.com/halbritt/cairn/actions/runs/34529414559)
passed on `3ad6e134320c882e6c5cc86e1f0c598d56aa27c0`. Both jobs and every
recorded step passed, including the shared-database PostgreSQL race suite,
Python checks, static checks, authenticated CLI checks and OpenCode plugin check.
The tested production source matches installed `c5fa55a`; the intervening commit
changes only the API fixture and documentation. The CLI and API still report the
clean installed build. The initial CI failure remains in the history and metadata.


### 2026-09-10 — Exact passage corrections for ordinary notes

Added `replace` to the existing native edit tool and CLI/API so an agent can
correct one uniquely matching passage while preserving all other text and stored
metadata. The maintenance history contains actual lost instructions from full-body
rewrites; this operation supports targeted corrections alongside full-body edits
and append. Missing, repeated and overlapping matches refuse, as do stale versions
and invalid final bodies. Explicit empty replacement can remove one passage from
a nonblank note. Previous versions and citations remain intact; exact retries
return the original identifiers after later edits. No migration or new tool is
required.

The disposable PostgreSQL race/API/MCP suite, static checks and 40 Python checks
pass. Checks cover concurrency, ambiguity, UTF-8 bytes, maximum escaped requests,
metadata/history, current authority and request identity. The first API check
reproduced the missing endpoint; an initial core test also failed compilation
because it used incorrect evidence/history fields. Those failed runs remain in
the [report and metadata](verification/note-replace-2026-09-10.md).

This is a usable correction operation, with independent downstream task benefit,
reduced omission rate and net savings unmeasured. Owner-priority notes informed
work selection alongside the roadmap. Installation and native verification are
recorded separately when complete. All preceding implementation history is retained.


Native OpenCode 1.18.21 checks passed with scripted completions and no inference,
including exact replacement/retry and malformed-argument refusal. Previous CLI
mutation responses still retry through the new binary. Pincite packet
`pkt-973a8ef54d989d3b` has a validated receipt and closed citation loops; 21
nonmaterial domain-model/interface obligations remain recorded. Installation is
pending below; the operational store has not been used for fixtures.


### 2026-09-10 — Exact passage correction installation and CI


[CI 34531223764](https://github.com/halbritt/cairn/actions/runs/34531223764)
passed both jobs and every recorded step on clean `96e8eae875a85650b2547f680455c0ae24a93233`.
Installed that source in the CLI and API after a store backup, together with the
matching native OpenCode adapter. The API restarted at PID 3716773; PostgreSQL
remained at PID 163669 and migration 034. All 78 retained record versions and
their combined digest are unchanged. Credentials, host configuration, recent-file
plugin and semantic-worker settings retain their prior hashes.

A fresh installed MCP initialization advertises `replace` on the existing edit
tool, with six ordinary tools still available. The facade, CLI and API report the
installed source. No operational note was changed for verification. This records
installation and the tested correction capability; downstream task benefit and
net savings remain unmeasured.


### 2026-09-10 — Development changelog and version navigation

Added a root [changelog](../CHANGELOG.md) with source-dated development changes,
links to the feature contracts, the latest recorded installation and upgrade
requirements. README and build-identity documentation now link to it. Cairn
remains an untagged local alpha; local and remote tag inspection found no release
tags. The running CLI and API still report clean `96e8eae`, while later commits
record documentation changes.

Verified 95 local links/anchors across the changed navigation surfaces, all 16
explicit source commit references, the first source commit and current build
identity. Check output is retained at `/tmp/cairn-changelog-20260910/checks.json`.
No executable, store, host setting or operational note changed. This closes the
missing concise changelog surface alongside the earlier version/status request;
it adds no new task-benefit result. All previous implementation history, including
negative results and superseded work, is preserved.


### 2026-09-10 — Existing retirement workflow for obsolete ordinary advice

Verified that an already-used A note can stop appearing in fresh retrieval through
existing grant-backed retraction, while retaining its earlier history. Ordinary
hard deletion refuses protected references with `FORGET_REQUIRED`; that error does
not mean that erasing history is needed to retire advice. The deletion guide now
explains correction, supersession, retraction and forgetting as different choices,
with the exact retraction request and access requirements. README, changelog and
roadmap link to the workflow.

The existing disposable PostgreSQL tests passed for referenced-note deletion
refusal, preview ownership/expiry/new-exposure invalidation, exact retraction
retry, fresh retrieval exclusion and concurrent compile/retract ordering.
Source inspection confirms retained-version access and the CLI/API authority
boundaries. Ordinary hosted tools cannot perform the protected retirement path;
this limitation remains explicit. The existing path satisfies operator retirement
without a new archive flag or changed lifecycle contract.

Verification output is retained at `/tmp/cairn-obsolete-guidance-20260910/checks.log`.
No runtime code, installation, operational note or authority state changed. The
prior implementation history is preserved. This adds verified operating guidance,
not a new task-benefit finding or completion of the remaining lifecycle roadmap.


### 2026-09-10 — Bounded passages from retained history

Added optional byte spans to exact-version history through CLI/API, MCP and native
OpenCode. The standing 64 KiB fixture exposed a concrete gap: storing the note was
valid but its whole historical body exceeded the tool's configured room. A selected
passage now fits without increasing that room. Results preserve the original full
hash, byte count and version metadata, omit the full body, and return exact selected
bytes with a separate hash. Split UTF-8 uses base64; EOF alone clips the range.

The API and MCP regressions first failed on the unsupported argument, then passed.
Disposable PostgreSQL tests cover earlier/current distinction, bounds, hashes,
Unicode fragments, private/foreign/forgotten/excluded refusal and no new durable
references. Full race integration, static checks, 40 Python tests and native
OpenCode checks passed. Both native interfaces preserve whole-body budget refusal
while successfully returning the selected passage. No migration, extra tool,
operational-note mutation or model inference was needed.

[Verification](verification/history-spans-2026-09-10.md) records the scope and limits.
This improves historical inspection capability; independent downstream task value
and net cost remain unmeasured. Current instruction delivery still requires full
bodies. All prior implementation history is preserved. The installation remains
at its last recorded build until the separate deployment entry is appended.


### 2026-09-10 — Historical excerpts installed and procedure corrected

Installed clean `4999caa` after both jobs and all steps in
[CI run 34533931794](https://github.com/halbritt/cairn/actions/runs/34533931794)
passed. The CLI, running API and native OpenCode adapter match the tested source.
PostgreSQL stayed running at migration 034; deployment preserved all 78 retained
versions and the existing profiles, host settings, semantic configuration and
recent-file plugin. The API alone restarted after a backup. The installation
manifest is `/tmp/cairn-history-spans-20260910/deployment/installation.json`.

Used the installed ordinary hosted profile to inspect the precise v3 passage in
the saved history procedure that described oversized-output handling. An exact
replacement revised it to v4 with the new excerpt option and source pointers,
preserving all unrelated wording. Reading the same v3 span after the edit returned
the identical earlier bytes and source hash. This selected maintenance action
adds one retained revision (79 total after maintenance); it is separate from the
unchanged deployment comparison. The note remains fallible guidance. No independent
downstream model use or net task benefit is claimed. The prior implementation
history, including the source-only state above, remains intact.

A fresh installed MCP session also exposed and read the historical span. The
first probe omitted the required socket flag; the corrected probe passed.
Fresh ordinary search and pull returned the corrected v4 procedure and expected
body hash. This verifies current guidance retrieval after maintenance.


### 2026-09-10 — Offline help for MCP and harness configuration

The prior installation probe exposed a CLI usability defect: `cairn mcp --help`
returned only an error rather than the required connection flags. The same silent
flag handling affected the three configuration generators. All four commands now
print their registered usage/options on `--help` or `-h` and exit zero before
connection or configuration generation. Other CLI parsing remains as implemented.

The configuration tests first failed on empty help output, and a public invocation
reproduced the installed MCP failure. Command package tests, static checks and
offline public CLI checks then passed. Invalid inputs still fail without stdout,
help-like flag values remain data, output failures propagate, and valid generated
configuration matches the previous binary after normalizing the executable path.
The standing offline check now runs in the existing CLI integration workflow.

[Verification](verification/harness-help-2026-09-10.md) records source and evidence.
This resolves an encountered setup obstacle. Recalled project priorities helped
select work in the existing interfaces; incremental memory benefit and setup-time
savings remain unmeasured. No model inference, operational-note mutation or storage
change occurred. Installation remains at the separately recorded build until the
CLI deployment entry is appended. All earlier history is preserved.


### 2026-09-10 — Harness help installed without restarting services

Installed clean CLI `af43ac7` after [CI run 34535253230](https://github.com/halbritt/cairn/actions/runs/34535253230)
passed both jobs and every step. This includes PostgreSQL/race, Python, static,
configuration and authenticated CLI/MCP integration checks. The installed CLI
passes the offline help check for all four commands and both help spellings.
The project binary matches it, and the previous binary is retained for reversal.

The API stays on clean `4999caa`: help is handled by the CLI before API access,
so no server upgrade or restart was needed. Both API/PostgreSQL processes,
configuration, native adapter/plugin and all 79 retained versions stayed unchanged.
The database remains at migration 034. Installation evidence is retained in
`/tmp/cairn-harness-help-20260910/deployment/installation.json`. No operational
memory was revised and no model inference ran. This completes the observed help
repair; setup-time savings and incremental memory benefit remain unmeasured.
All prior implementation history is preserved.


### 2026-09-10 — Retained intent and native integration review

Applied the saved native-capture and prior retained-kind lessons to current source
and live Striatum state. Main remains at `5ea87ca`, native integration at `ee8a463`,
and the installed Driver at clean `a6b1ae7`. The original native request 408328 is
satisfied at `captured`; main's catalog remains `observation@1`/`build@3`. This is
opening-capture evidence, not acceptance of the proposed native contracts. The
producer, consumer and supervisor already exist; the review adds none of them.

Current Cairn source explicitly checks the newer retrieval fields before retained
launch, including failure signatures, entities, semantic mode, page/browse
positions and advisory-conflict intent. Expansion-reader equality and expiry are
checked separately before index rendering reaches binding. Existing disposable
PostgreSQL tests passed for exact retained execution, changed-context refusal,
currentness rechecking, normalized kinds and observed index delivery. These focused
checks did not enable the race detector; static field mapping is not an exhaustive
behavioral matrix or full execution-acceptance proof. No missing current intent
guard was found and no product/test repair was warranted.

[The review](verification/retained-intent-review-2026-09-10.md) records scope,
source hashes and limits. Initial inspection assumed the wrong position for
Striatum's global JSON flag and tried unsupported ledger help; the corrected
status read used `striatum --json status`. The unnecessary ledger reader completed
normally. Receipt validation also caught a missing verification dependency, which
was corrected. No graph mutation, deployment, operational-note revision or model
inference occurred. Recalled guidance oriented this review; avoided rework and net
task benefit remain unestablished. Earlier implementation history is preserved.


### 2026-09-10 — Integrate existing native Striatum implementation

Merged and pushed Striatum's six existing native commits plus compatibility
repair `5848b23f1800e7051e59f4f70303244c711e0575` into main. The implementation
contains acquisition/request handling, deterministic observation production and
admission, packet-selected build inputs, exact prompt correspondence, and host
operations in the existing supervisor. The merged branch and worktree were
removed; the unrelated untracked main file was preserved byte for byte.

Review found two regressions in the preparatory branch: ordinary requests with
`cairn-context/` subjects were rejected under observation contract 1, and old
build preparation treated prefixed file paths as native identities. The repaired
version guards preserve historical behavior while new native contracts retain
their acquisition and evidence checks. Both final regression fixtures fail on
the old branch code through a Go overlay and pass after repair. Full `make check`
passes, including the 615.422-second Driver suite; focused Driver/native race
checks and strict supervision checks also pass. The real disposable Cairn service
and complete Driver chain ran; the model experiment remained skipped.

The accepted catalog, generated Decisions, policy and backend declarations are
unchanged. New schemas remain proposed. No production deployment or compiler
acceptance occurred, and no operational graph mutation or new opening request
was made. RQ-408328 remains the existing captured subject; the wider native
contract/adoption work and its eventual closing observation remain open. The
retired model comparison was not repeated. Source integration is concrete
implementation progress, not evidence of incremental memory benefit or full U1
completion. [Verification and limits](verification/native-integration-2026-09-10.md).

The selected native capture procedure `82fcfec8-474b-48fe-9a00-a36f59d34ac1`
was updated v2→v3 by exact passage replacement to point at merged main and remove
the obsolete worktree direction. All other text and the retained v2 body were
preserved. Fresh ordinary search/pull verified v3; downstream benefit remains
unmeasured. No credentials or raw operational note bodies were committed.


### 2026-09-10 — Accept structured pull arguments through the agent CLI

`agent pull` and `agent pull-evidence` now accept JSON on stdin with no trailing
arguments. This resolves a repeated interaction mismatch from native integration:
structured pull arguments previously required the differently named `agent expand`
operation, while `agent pull` accepted only shell arguments. The old documented
interface was working as specified; this is a usability extension. Shell forms
remain available, and the existing authenticated API owns expansion validation.

The change is confined to CLI dispatch. Focused race tests reproduce the former
refusal and verify argument preservation, invalid input and partial-shell refusal.
The real disposable API checks equivalent body/evidence pulls, byte excerpts,
shared retry credit, foreign-owner and unknown-field refusal, and stale handles
after a note edit. The full API/MCP script and `make check` pass. No store schema,
API endpoint, adapter, authority or ranking change is introduced. Installed-build
verification will be recorded after the checked commit is deployed. Removing this
particular interaction failure does not establish net task benefit.

[Verification and decision record](verification/pull-json-2026-09-10.md) also retain
the ordinary live API comparison: new JSON pull and old expand returned identical
responses. Operational note content was unchanged.


### 2026-09-10 — Install structured CLI pulls after CI

Clean CLI `7ce59a40a472803ff1b1af9b2f31fee3405293e3` is installed in the user's CLI location and the project binary.
[CI run 34540128174](https://github.com/halbritt/cairn/actions/runs/34540128174) passed both jobs and all steps,
including the full Go race suite, Python tests and authenticated API/MCP checks.
The API remains clean `4999caa`; API and PostgreSQL PIDs and executable hashes
were unchanged. Migration remains 034. Host settings, native adapter/plugin
hashes and all 80 retained note-version payloads matched the pre-installation snapshot.
The previous CLI and project binary were backed up before replacement.

The installed CLI retrieved the current evaluation decision through JSON `pull`.
The old CLI's `expand`, the current returned shell command and the JSON retry all
returned the identical response with three credits remaining. This was an ordinary
read, with no note-content mutation or model inference. The command mismatch is
resolved; broader task benefit remains open. [Verification](verification/pull-json-2026-09-10.md).


### 2026-09-10 — Expose smaller per-search room in native tools

MCP and native OpenCode `cairn_search` accept optional `available_tokens`, from
256 through the configured host ceiling. The effective room reaches the existing
compiler and final presentation check; omitted arguments retain current defaults.
Changed room is changed retry intent, and later calls do not inherit a prior
call's allowance. The existing receipt still owns expansion credits and bytes.
[Usage](search-room.md) records the failure and retry behavior.

This began as an investigation of E2's aggregate-context gap. Existing receipts
already bound their own spending, while the native search interface exposed only
fixed startup/configuration room. The saved startup procedure v1 and current
source distinguish initial startup/runner allowances from aggregate task context.
This interface extension improves caller control without asserting observed
remaining context. Whole-task accounting across retained conversation, generation,
tool schemas and compaction remains open; no global budget ledger was added.

Both old native interfaces rejected the parameter. The changed MCP/store race
checks and full disposable API/MCP/native OpenCode suite pass, including smaller
room, exact pulls, retries, mandatory refusal and default preservation. Actual
Codex 0.153.4 retrieved the saved procedure's 154-byte passage with 8,000-byte
room. Its initial probe incorrectly expected regenerated pull UUIDs to match;
comparing the retained receipt, handles and budget corrected that test assumption.
The production retrieval implementation did not need a retry repair.

`make check` and CLI race tests pass. The OpenCode output-budget error drops advice
to retry with a changed room under the same UUID; the changed search needs a new
UUID. This is a CLI/MCP and adapter addition with no core, API or schema change.
[Verification](verification/search-room-2026-09-10.md) retains the failed probe,
corrected evidence and limits. Installation will be recorded after CI. Native
checks performed no model inference or operational note-content mutation;
incremental task benefit remains unmeasured.


### 2026-09-10 — Install native search room after CI

Clean CLI/MCP `f405aeb9c388ceb40e4d95d994b9130dd04e906d` and its native OpenCode adapter are installed.
[CI run 34541726630](https://github.com/halbritt/cairn/actions/runs/34541726630) passed both jobs and all steps,
including the full Go race suite, Python tests and authenticated CLI/API/MCP checks.
API `4999caa`, PostgreSQL, migration 034, host configuration and the recent-file
plugin remained unchanged. The installation snapshot preserved all
80 retained note-version payloads. Previous binaries and the adapter were backed up.

Installed Codex 0.153.4 and OpenCode 1.18.21 each retrieved
exactly the same 154-byte passage from startup procedure
`8ab6f87a-ff0d-46f2-9ae3-4f4c0cfa033c` v1 with 8,000-byte room and
an 800-byte optional index allowance. Both preserved pull retries, rejected values
above the host ceiling and returned to the 32,000-byte default on the next search.
Codex also verified changed-room search retry refusal through its real MCP client.
These were ordinary reads with no model inference or operational note-content
mutation. [Verification](verification/search-room-2026-09-10.md) records the
installation and matching source/span hashes. Whole-task accounting and durable
model-task benefit remain open.


### 2026-09-10 — Reconcile native adoption with the shared RFC graph

A completed live status read and selected retained ledger records distinguish
Cairn's captured opening request 408328 from RFC 0004 staging request 408565.
The latter carries the separate `review-conclusions-are-evidence-bound` intent
from request 408483. Its nine horizon lapses and escalations 409639/409640 are
not Cairn trials. An initial subject-name inference was corrected after reading
the exact retained intent; no request, resolution or retry was issued.

[Adoption guidance](native-striatum-context.md#shared-rfc-adoption-check--2026-09-10)
now requires reconciliation of the shared RFC sequence before Cairn stage
production and personal acceptance. No relevant acceptance candidate was queued;
the live projection listed RFC 0007's Decision as stale. Source and installed
catalogs retain observation@1/build@3, and installed Striatum remains clean
a6b1ae7. Striatum main is 5848b23; its unrelated untracked file was preserved.
The [check metadata](verification/native-adoption-2026-09-10.json) retains
selected references and hashes; private request bodies remain outside Git.

This is a correction to adoption planning, not a code change, diagnosis of the
other feature's failures, native enablement or new usefulness evidence. Full
implementation history is preserved. Ordinary task-value work remains available;
the overall Cairn goal is active.

The native-capture procedure was extended from v3 to v4 with the shared-RFC
finding and source pointers. Retained-history reads verified that all earlier
body bytes remain exact. The first append omitted the API's required repository
and refused; the corrected request succeeded. This selected maintenance does
not establish downstream benefit. No global Codex memory file was changed.


### 2026-09-10 — Select one task or run in outcome reports

Added exact `--task`/`--run` selection to `use-report`, `run-report` and `runs`.
The authenticated requests accept optional `task_id`/`run_id`. Existing report
queries apply the filters before pagination, preserving record/policy filters,
linked retrievals, corrected assessments, zero-memory executions and unknowns.
This supports cumulative task review without repository-wide history scans;
no value score, report permission or database migration was added.

Both new scope tests failed against the previous behavior. Full disposable
`make test-integration` and `make check` passed, including an actual CLI selection
among two child executions and 102 earlier exposure rows. The API checks preserve
hosted and foreign-repository denial. An initial fixture omitted its required
observed exit code and was corrected. [Verification](verification/report-scope-2026-09-10.md)
retains the scope and limitations. No operational note was edited in this turn;
no model-task or measured review-cost improvement is claimed. Installation will
follow CI. U4's broader recurrence and task-value requirements remain open.


### 2026-09-10 — Isolate report CLI fixture artifacts

The report CLI check initially used a disposable database but left two generated
context directories in the default Cairn run directory. They were identified,
hash-checked and moved intact to the private verification directory. The fixture
now gives its child runs a temporary `CAIRN_HOME` and verifies their artifact paths.
A fresh disposable CLI check passed without writing its sentinel run directory.
Its first standalone invocation omitted migration and was corrected. No operational
database was used for these tests. [Verification](verification/report-scope-2026-09-10.md#fixture-isolation-correction)
retains the correction and evidence; report behavior is unchanged.


### 2026-09-10 — Install task/run report filters

Installed clean CLI/API `b171a8b78b9b0a889a7718974cb0b4e60b71bfba` after
[CI 34544439753](https://github.com/halbritt/cairn/actions/runs/34544439753)
passed both jobs and all steps. The preceding feature CI also passed. A store
backup and previous binary copies were retained before restarting the API.
PostgreSQL, migration 034, configuration, native adapter and all 81 retained
note-version payloads stayed unchanged.

The installed executable passed the isolated CLI history/task/run check. The
running API reports the same clean build and retains hosted denial for both
protected reports. [Verification](verification/report-scope-2026-09-10.md#installation)
records the installation and its limits. There was no operational note-content
mutation or model-task trial; this completes the report-filter capability while
leaving U4's broader task-value requirements open.


### 2026-09-10 — reuse unchanged semantic passages after localized edits

Implemented exact decoded-passage vector reuse in the existing worker. Three
appends to a public long note scored in 0.24–0.28 seconds versus 4.15–4.53 seconds
with whole-note caching; eight paired complete responses matched. A separate
profile counted ten inference inputs before and two after. Cache ownership,
128-window occurrence limit, current eligibility and idle expiry remain;
retention includes only current passage hashes and raw vectors, with no disk
cache. Unchanged notes now pay tokenization each time. Cold performance and
sustained task benefit remain open.

The append test first failed, then passed; thirteen numerical/framing tests,
full disposable PostgreSQL/race integration with the real worker, Python checks
and static checks passed. Initial make test hit ETXTBSY launching a temporary
cold-deadline fixture. Its unchanged isolated rerun and full integration passed;
root cause is unknown and the failure is retained. Worker installation is pending
at this checkpoint; installed CLI/API remain b171a8b. See
[comparison and limitations](verification/semantic-chunk-cache-2026-09-10.md).


### 2026-09-10 — install passage reuse and refresh worker guidance

Clean feature d3edaa29bf82fce3572aebf786b69439b37869ca passed CI 34545885103;
both jobs and their steps completed successfully. Installed worker SHA-256 is
2d85715d25ea9dbc5deb86d37e3ad9e640ba11dfe08f4e920a52453e2aab240f. API restarted
as PID 4138687; its executable and both CLI copies remain clean b171a8b. Existing
configuration, credentials, native adapter/plugin and launchers were preserved.
PostgreSQL stayed PID 163669, and the installation preserved all 81 note versions
with payload inventory hash 9dd36e27886d461cdee9d78789c9bb08c2e7ec52a82167e5ca1bcd3031adbb25.
The previous worker file and installation manifest are retained under
/tmp/cairn-semantic-chunks-20260910/deployment/.

Actual hosted-profile semantic calls completed ready in 8.153 and 0.073 seconds,
using the same child with matching score digest, context seal and selected source
versions. These are installed-path checks, not an old/new cache comparison.
The v9 semantic worker guidance was then revised to v10, preserving unrelated
text, metadata and retained v9 wording. This deliberate update adds one version
after the unchanged installation inventory; v10 body SHA-256 is
f3de9e6ab9428ec10219e6611956f984ac85af3f2aca7d4af31ae33e8dd5dede.

The comparison report now spells out shifted-window limits: earlier insertions
or deletions can invalidate later windows, and short-note edits may require full
passage inference. Cold speedup, larger-corpus behavior and net task benefit
remain unestablished. [Full evidence](verification/semantic-chunk-cache-2026-09-10.md).


### 2026-09-10 — keep native OpenCode searches on a declared task

Implemented optional opencode-install --task and --run settings for native search.
A task alone persists across sessions while each session retains its native run
ID; an explicit run requires the task and fixes both labels. Default native
scope remains unchanged. This addresses the documented compact-start scope switch
without pretending launcher labels are native session IDs or propagating them
automatically. Scope is host-configured; tool arguments cannot override it.

The new installer test failed before the flag existed and then passed. The older
invalid-argument test was updated because --task is now deliberately supported;
it still rejects an unknown flag. Full disposable PostgreSQL/race and actual
OpenCode 1.18.21 checks passed, including exact task-note pulls from two distinct
native sessions, run-restricted selection, private/unrelated exclusion, unchanged
request retry identity, invalid settings and restored defaults. Python and static
checks passed. No answering-model task or production test mutation occurred.

Installation is pending at this checkpoint. CLI/API remain b171a8b and the earlier
passage-reuse worker remains installed. Fixed task settings apply across sessions
using one connection file until changed; this is an explicit applicability choice,
not observed task/attempt attribution. [Usage](opencode-tools.md#continue-a-task-across-sessions)
and [verification](verification/opencode-task-scope-2026-09-10.md). Whole-task
budgeting, automatic scope propagation and broader task value remain open.


### 2026-09-10 — install declared native task scope

Clean feature 50e4935567aef0d9b267f288422ca71d267a82ea passed CI 34547465329,
including both jobs and every step. CLI and project CLI now identify that commit;
the installed bundled adapter SHA-256 is
e18aec232eb34af4d8e4d2c4109cf942d84c95c2e834d6bed54dd35aa6ec39df.
The running API remains clean b171a8b, PID 4138687, because the existing API
already accepts explicit search scope. Neither API nor PostgreSQL restarted.
Store PID 163669 and migration 034 remain. Semantic worker, launchers, identities,
project/native configuration and recent-file plugin were preserved.

Installation preserved all 82 note versions with payload inventory hash
0f991a545927ffa340bb7859847f530696e81518172801bd3fd5aa12c79c5af5.
The actual new CLI installed a temporary native project configured for the current
task/run; OpenCode 1.18.21 searched through the operational ordinary hosted profile
and pulled the exact existing semantic guidance v10. This was read-only use with
no answering-model inference. The canonical project's default session scope
remained unchanged. [Installation and evidence](verification/opencode-task-scope-2026-09-10.md).

Ordinary OpenCode guidance was then revised v17→v18 by replacing one unique scope
passage. Fresh retrieval, unchanged unrelated text and metadata, and the retained
v17 body were verified. The new body SHA-256 is
5e8cb608b4dde3029b917515492efead2522ff1f30387759767fbf92fae8ddd5.
This deliberate guidance update adds one version after the installation check.
Earlier history remains intact. Broader task value, automatic propagation and
observed native execution requirements remain open.


### 2026-09-10 — review memory contribution during retrieval optimization

Added a retrospective performance-memory case to the task-value inventory. The
review verified eleven original artifact hashes, eight frozen request/response
identity sets, the three append measurements, the profile's ten-to-two inference
input reduction, and exact historical semantic guidance v9. It records one
qualified positive continuity case: earlier experimental findings were available
during useful engineering work. Source inspection found the optimization, and
the measured speedup is not attributed causally to memory.

The review explicitly retains overlapping handoff/conversation and source
context, investigator self-review, unknown total cost and the conditional edit
workload. The original comparison checked both responses in memory but retained
one response per pair; the review records that audit limitation. No new model
trial, benchmark rerun, task acceptance, authority/ranking change or note
revision was performed. Existing results and all earlier history remain.
[Review and evidence](verification/performance-memory-value-2026-09-10.md).


### 2026-09-10 — explicit task/run capture in native memory tools

Native MCP and OpenCode `cairn_remember` now accept `scope: "repository"`,
`"task"` or `"run"`. Repository-wide capture remains the default. Task/run
selection uses the same host labels as search, allowing a task-specific finding
to retain its applicability when captured through a native tool. Existing A
attribution, local sensitivity, explicit pins, ordinary edit restrictions and
create retry semantics remain. No API or schema change is required.

The new MCP and actual native OpenCode tests first rejected the unsupported
scope argument. Targeted and full disposable PostgreSQL/race checks then passed,
including stored scope, fresh-host search/pull, private and unrelated-scope
exclusion, invalid choices/metadata, changed-scope retry refusal and a task retry
across a run change. All 43 Python tests and make check passed.

Three expanded fixture mistakes were corrected: new repository-wide notes
changed an older browse fixture's inventory; a shared query word exhausted pull
credits by selecting other fixtures; identical task/run bodies were deduplicated.
The corrections separated test execution and used unique queries/distinct bodies.
No production matching, deduplication or budget behavior changed for these tests.
[Verification and retained evidence](verification/native-capture-scope-2026-09-10.md).

This is an implemented capture/retrieval capability, without an answering-model
trial or an incremental memory-value claim. Existing host labels still describe
configured tasks or conversations/sessions, not independent executions. Source
integration precedes installation; the preceding installation summary remains
accurate until the next installation checkpoint. All earlier history remains.


### 2026-09-10 — install native capture scope and preserve task findings

Installed clean feature commit `797aafedd70cfea2fb56f6f10087ac5519f04e14` after both jobs and all
steps of CI run 34550429040 passed. The CLI/project binary and bundled native
adapter are current; a fresh MCP process advertises the same revision and scope
argument. Existing MCP processes need restart to use it. The API remains clean
b171a8b at PID 4138687, and the store remains PID 163669 with migration 034.
No API restart was needed. The installed CLI SHA-256 is
6a49f841b7f940d0e53aaec100828a24b6fc3b48ca439c8fd6acf7d85dbb7c3b; native adapter SHA-256 is
92de49181e3a7ff0117364ec6d2556ad593e82a0b2caae183f3a009f2a6bd8b0.

Installation preserved all 83 note versions with inventory SHA-256
cded6114bc0372275f3ec885545e42e8dd697b3058d062733689444e396f404f, plus canonical native session
defaults, recent-file plugin, credentials and the prepared semantic worker.
The ordinary hosted profile then captured one selected task finding through the
installed native adapter under task `capture-scope`, wildcard run. It retains
scope-test fixture mistakes and source pointers for task follow-up. Fresh native
retrieval returned the exact body, identical capture retry reused the record,
and another task omitted it. This is operational capture/retrieval without an
answering-model task or a new task-value claim.

The selected note is 56cd4054-9425-428a-832c-3e29243e5b62 v1, body SHA-256
2eb7fa4c90f4d4dc154e52dd9cc8f021e031d101c88dc656bb942541b960afbd. OpenCode procedure v18→v19 and Codex procedure v9→v10
then gained the new capture instructions through ordinary append updates.
Fresh retrieval verified complete updated bodies, unchanged prior text and
metadata, and retained earlier versions. There are now 86 note versions; all
83 preceding versions still match their original inventory when the three
deliberate additions are excluded. Services and preserved files remain unchanged.
[Installed evidence and limits](verification/native-capture-scope-2026-09-10.md#installed-verification).
All earlier implementation history remains intact.


### Harness configuration text repair — 2026-09-10

MCP startup and all three configuration generators now reject scope identifiers
above the store's 256-byte limit. OpenCode configuration and native installation
refuse malformed UTF-8 before JSON encoding can change it to U+FFFD. Configuration
arguments and paths also reject NUL. Valid Unicode, intentional U+FFFD, literal
escapes and exact 256-byte labels remain usable. Existing startup and command
construction own the checks; the API retains context-pin semantics.

Installed baseline `797aafe` reproduced both defects with synthetic arguments.
A raw-output scan initially missed JSON-escaped replacement characters; decoding
the arguments exposed the changed values. Focused red/green tests, actual offline
CLI round trips, standard disposable integration, static checks and 43 Python
tests passed. No production database was used for fixtures.

Three full runs with optional OpenCode checks timed out at 60 seconds: first in
the large-note session, then twice in recent-file sessions. Both unchanged session
checks passed separately; the recent-file run passed all five cases. Retained
failure logs show only a title request and no main tool calls, with snapshot
tracking as the last log entry. A later process check found no Git process;
attaching a syscall tracer was refused. The cause remains unknown. No timeout or
automatic retry policy changed, and a full optional native-suite pass is not
claimed. The adapter source is unchanged.

The [repair report](verification/harness-configuration-text-2026-09-10.md) and
[manifest](verification/harness-configuration-text-2026-09-10.json) retain the
checks and limits. Source comparison found the defect; recalled Unicode guidance
subsequently informed the validation boundary. This is a verified setup repair,
with no independent memory-value attribution. Installation and CI are recorded
in the subsequent checkpoint when completed.


### Harness configuration installation checkpoint — 2026-09-10

Clean `208c1c81a861555ba64808c2b194e55ecbb63cb0` is installed in the local and
project CLI locations. Both jobs and their required steps passed in
[CI run 34554012895](https://github.com/halbritt/cairn/actions/runs/34554012895).
The installed executable passed the offline configuration check. API `b171a8b`
and store processes stayed unchanged, as did native adapter bytes from `797aafe`,
semantic worker `d3edaa2`, configuration, credentials and database migration 034.
The installation preserved all existing note versions. A deliberate append then
extended Unicode lesson `104519b5-82cd-44b3-ba73-1def2ad42e8c` from version 3 to 4;
its previous body was verified unchanged. The [manifest](verification/harness-configuration-text-2026-09-10.json)
records those identities and checks.

The unresolved optional full native-suite timeouts remain recorded above. Passing
CI and isolated sessions do not erase them or establish an incremental memory
benefit. This closes the setup repair; broader task value and roadmap requirements
remain open.


### Semantic idle-restart experiment — 2026-09-10

An actual hosted-profile semantic search began without an API child and took
22.629 seconds; its immediate repeat took 0.125 seconds with the same complete
score digest. Current worker guidance recovered the earlier inference profile
and rejected thread/batch experiments. Source review identified the remaining
idle-release loss of cached vectors.

A scratch candidate restored exact passage vectors into a fresh scoring process.
Three alternating pairs on two fixed public repository documents took
11.41–11.65 seconds cold and 0.58–0.66 seconds restored, with identical complete
source-bound results and lower process peak RSS. Restoring 51,014 serialized bytes
reduced actual model inputs from 25 to 1. An appended note recomputed one passage;
removed passages were dropped; a different snapshot model identity forced cold
recomputation. All pairs met the prospective latency, state-size and RSS bounds.

The [report](verification/semantic-idle-snapshot-2026-09-10.md) and
[manifest](verification/semantic-idle-snapshot-2026-09-10.json) preserve both sides
of the comparison and its fixed-workload limits. No API/worker change was installed
and no production test suite was rerun for the experimental documentation.
The selected next implementation retains a bounded snapshot in API memory across
idle release only, with lifecycle, compatibility, output-bound and actual API
eligibility checks required before installation. It adds no disk cache or new
service. The installed CLI/API/worker and note versions remain unchanged.
The comparison establishes a promising retrieval-cost improvement; broader task
value and the roadmap remain open.


### Semantic idle cache implementation — 2026-09-10

The API streaming transport now retains one bounded opaque snapshot across idle
worker release. A fresh prepared worker restores matching-model passage vectors;
current source eligibility and scoring stay with their existing owners. Failed
or cancelled exchanges, omitted snapshots and owner shutdown discard saved state.
Calls that never reach scoring do not change it. This extends in-memory lifetime
without introducing a disk cache, new service, database schema or longer deadline.

The host advertises support through its stripped worker environment. Old workers
retain their original behavior; a new worker under an old API returns its original
response shape. Scores remain bounded to 64 KiB independently of the 600 KiB cache
allowance. The full reply frame cap is 664 KiB. Python validates model identity,
128 copied float32 vectors and complete state before restore; absent passages are
replaced after successful scoring. No query/note text or record IDs enter the
bundled snapshot, and cache state stays outside core results and receipts.

Red/green Go and numerical tests verify actual idle child replacement and stream
restoration. Race checks cover state replacement, omission, cancellation, failure,
owner isolation and independent state/score/frame bounds. Eighteen numerical and
framing tests, 48 total Python tests and static checks passed. Full disposable
integration with the real prepared model passed: an API request fell from 11.838
seconds cold to 0.605 seconds after a verified idle child exit with the same score
digest and ordered source identities. A later edited source was pulled at version
2, its old handle refused and private fixture content excluded.

A separate full authenticated model check ran the new worker with installed CLI
`208c1c8` serving a fixture API. Compatibility passed without cache restoration;
its after-idle request remained cold. That run used a later documentation body,
so its timings are not another controlled performance pair. Both source/result
sets and their limits are retained in the [report](verification/semantic-idle-cache-2026-09-10.md)
and [manifest](verification/semantic-idle-cache-2026-09-10.json). Installation follows
verified CI; no independent downstream task-value claim is added.


### Semantic idle cache installation checkpoint — 2026-09-10

Clean `ed29cbcb70907dbc0e275ebe90e198e450accfcc` is installed for the CLI, API and prepared semantic
worker. [CI run 34556271313](https://github.com/halbritt/cairn/actions/runs/34556271313)
passed both jobs and required steps. API PID 235929 replaced the earlier
API process; PostgreSQL, schema 034, native adapter, configuration, model/packages
and launcher bytes stayed unchanged. All 87 existing note versions were preserved
by installation. A selected revision advanced semantic guidance from v10 to v11;
all previous versions remain byte-for-byte unchanged, and 88 versions are retained.

An ordinary hosted-profile semantic search completed cold in 24.228 seconds.
The worker actually exited after the unchanged five-minute idle interval; a
different child then completed the same search in 0.612 seconds with the same
complete score digest and ordered source identities. The API remained running
throughout. This verifies installed reuse after a real idle release, while the
initial cold budget remains close and broader task value remains open. The
[report and manifest](verification/semantic-idle-cache-2026-09-10.md) retain the
installation identities, source hashes, both responses and that limitation.


### Failed integration artifacts and native startup investigation — 2026-09-10

The integration runner now retains its temporary artifacts and prints their
location when a check fails, while stopping its owned PostgreSQL cluster and
preserving the failure exit code. Successful runs still remove their directory.
An intentional Go-job failure reproduced the former artifact loss, then verified
retention after the repair; a successful stub-job probe verified shutdown and
removal. These are real PostgreSQL lifecycle checks with controlled jobs, not a
replacement for application tests. No binary installation or production restart
is required. [Repair and verification](verification/failed-integration-artifacts-2026-09-10.md).

The native investigation preserved another 60-second startup timeout in the first
OpenCode debug-agent search, after all five recent-file cases passed. Both this
failure and the earlier inspectable baseline retained an unfinished global plugin
installation while their project installation completed. Launch-time tracing was
available despite the earlier refused attach. A complete authenticated API/native
tool check then passed under tracing, including 226 debug invocations, recent-file
permissions and the maximum-note session. Tracing can change timing; untraced
reliability and the underlying startup cause remain unresolved. No deadline,
retry, snapshot or permission change was selected, and no answering model ran.
Earlier failed trials remain part of this history; no task-value acceptance is
added by the cleanup or diagnostic checks.


### Actionable client setup and connection errors — 2026-09-10

A diagnostic launch with an absent token file blamed the local store and task
runtime. Token-file I/O now reports `CLIENT_SETUP_FAILED`; Unix dial failure
reports `API_CONNECTION_FAILED`. CLI and MCP messages point to the relevant
configuration without printing private paths or credentials. The original OS
or cancellation error remains inspectable by trusted Go callers, and unknown
CLI errors retain the existing privacy mask.

The initial pending regression test had a missing brace; after correcting its
syntax, missing-token and missing-socket checks reproduced the intended failures.
The executable CLI check then caught the separate message-masking defect. Focused
Go race checks, actual CLI/MCP failure checks, `make check` and full disposable
PostgreSQL integration now pass. Successful authenticated requests and the existing
committed-but-lost-response runner tests remain covered.

This changes diagnostics, including exit 7 for a later run operation's dial
failure; earlier run effects remain possible and retained result fields still
matter. No retry, API execution, token-validation or database behavior changes.
Only the CLI/MCP executable requires adoption. The [report and manifest](verification/client-diagnostics-2026-09-10.md)
separate checkout verification from installation and record source-review-only
branches. No incremental task-value or native startup reliability claim is added.


### Client diagnostics installed — 2026-09-10

Exact-source CI passed for `d0f04d8`. Its clean CLI is installed, and actual
CLI/fresh-MCP failure checks pass. Normal version diagnosis reports the new
client alongside the unchanged clean `ed29cbc` API at PID 235929. No service
restart or database migration occurred. The inventory of 88 ordinary record
versions is unchanged. Already-running MCP processes retain their loaded build.

An installed hosted-profile search and JSON pull recovered the expected current
connection lesson v1 and body hash, verifying ordinary retrieval across these
client/API builds. Existing guidance about explicit connection paths and default
identity remains consistent with source. The [installation report](verification/client-diagnostics-2026-09-10.md)
records these observations separately from the repair and from task-value claims.


### Observed expansion survives usage summarization — 2026-09-10

Review of U4/U5 found that a later reported expansion could replace the displayed
witness for an actual service expansion, while citation took precedence over
expansion entirely. The existing classifications remained accurate for the one
selected event, but the report hid the separate instrumented fact. A disposable
PostgreSQL sequence reproduced that gap.

`use-report` now returns `expansion_observed` for each exact record/version/receipt.
The field retains instrumented expansion alongside the existing usage label,
witness and method; manual testimony alone leaves it false. Body and supporting
evidence pulls, including excerpts, qualify. A service-side operation does not
establish response delivery, comprehension or task benefit. Absence of a retained
event does not prove absence of use, and older servers omit the new field.

Focused PostgreSQL/race checks, the complete disposable integration suite and
`make check` passed. The actual CLI/API reports observed expansion alongside
citation testimony with task outcome and coverage still unknown. Other records
and retrievals do not inherit the flag; repeated pulls do not multiply rows.
The [report and manifest](verification/expansion-observation-2026-09-10.md) retain
verification and the bounded compatibility decision. Update the API or direct-store
CLI for this additive projection; no migration, event rewrite, ranking change or
new utility score is involved. U4/U5 and broader task-value evidence remain open.


### Expansion observation installed — 2026-09-10

Exact-source CI passed for `4e75ed1`, and its clean CLI/API are installed with
matching executable hashes. The API restarted at PID 386925; the store process
remained at PID 163669. All 88 ordinary record versions retain their prior
inventory digest. No migration occurred, and the semantic worker and idle
configuration remain unchanged.

Installed hosted search/pull recovered the expected value-evaluation decision v1.
The protected use-report endpoint still refused the hosted profile. Field output
was checked on the disposable authenticated fixture, while production verification
establishes binary identity, normal retrieval and preserved access boundaries.
The [installation report](verification/expansion-observation-2026-09-10.md) retains
those distinctions. No new note, task assessment or memory-value claim was added.


### Qualitative review example and failed native authoring — 2026-09-10

The assessment guide previously explained qualitative and cumulative value but
provided only a history-read command. It now includes a complete ordinary-profile
write/read-back example: inspect existing history, choose a real narrative, keep
unknown acceptance when appropriate, retain a stable request for retry, and
inspect conflicts before issuing a new assessment. The coding agent authored
this addition and executed its literal shell blocks against a disposable
PostgreSQL/API instance. Unicode narrative read-back, ordinary testimony, empty
and nonempty history, exact retry after later assessments, and stale-request
refusal passed. No production code, schema, installed binary or profile changed.

The earlier native OpenCode 1.18.21 authoring attempt reached its 300-second
limit without producing a candidate. Five source reads were denied; six memory
searches and seven full pulls completed. Its ten relay requests include nine
completed requests reporting $0.007250116 and one interrupted request with no
usage report. This excludes review and local-compute cost. Interim text was not
a final candidate. The six retrievals are now linked to the observed run, whose
assessment remains unknown with a binding/adapter obstacle. No memory benefit
or model capability failure is inferred.

A local scripted native check reproduced the permission failure, including a
failed relative-pattern attempt without a Git project. Initializing the private
snapshot as a Git project and allowing only its four exact relative source paths
made those reads succeed while an unselected file stayed denied. This is a
scratch-controller correction; no global permissions changed and no second
answering-model run was launched after local completion. The original run and
failed checks remain in the [review and manifest](verification/qualitative-review-example-2026-09-10.md).
The task-value inventory includes the negative observation; U1/U4/U5 and E4/D2
remain open. All previous implementation history is preserved.


### Native assessment-guide maintenance — 2026-09-10

OpenCode updated the existing assessment-history procedure from the checked-in
qualitative write example at `22fb971`. The task used the corrected private Git
snapshot and exact relative read permission from the preceding investigation.
Its earlier failed documentation-authoring attempt remains a separate result.

The native run completed in 100.549 seconds with two searches, a successful
source read, two full pulls and one append. It pulled procedure v1 before editing
and v2 afterward. Seven provider requests completed without relay refusal,
reporting $0.004850411; local setup and review effort are not priced. The task's
source and target note were supplied explicitly, so this does not measure an
optional-memory advantage.

Independent review found the 137-word addition accurate and confirmed all prior
text, metadata and retained versions survived. Only the intended note changed.
A missing paragraph separator required a root correction: v3 inserts two newline
bytes and preserves every native word, while v2 retains the original defect.
The ordinary-version inventory moved from 89 to 90 during native work and 91
following that correction, with all prior fingerprints unchanged. A fresh root
search/pull verified current v3.

Both native retrievals are linked to the observed run. Its evidence-attached
assessment accepts substantive maintenance with the formatting finding and root
correction explicitly stated, under delegated coding-agent review. It establishes
no new human acceptance, downstream task benefit or net savings. The [review and
selected manifest](verification/native-guide-maintenance-2026-09-10.md) retain
counts, source and artifact hashes, record versions, costs and acceptance limits.
The existing maintenance case now includes native updating of a Codex-maintained
guide. No production code, installed build, profile, schema or roadmap requirement
changed; the full earlier implementation history is preserved.


### Assessment write binding repair — 2026-09-10

Preparing native assessment-history and write tools exposed an API inconsistency.
History reads checked the original receipt destination, but the write route used
trusted `AssessRun` without a destination guard. Exact cached retries also
preceded its repository check. A disposable API reproduction returned an earlier
local assessment to the same principal after its destination changed to hosted.
The test supplied the complete earlier payload; this is no claim of UUID-only
disclosure or an observed production incident.

The API now calls `AssessRunForDestination`, checking current receipt ownership,
repository and original destination inside the mutation transaction before
replay or application. Mismatches return `AUTHORITY_DENIED`; matching retries
retain their original response, witness and history. The trusted direct-store
method, request identity/digest, version checks and stored formats remain. No
migration is needed. Formerly accepted inconsistent API requests now refuse,
enforcing the existing receipt policy.

Focused PostgreSQL/race checks cover both destination-change directions for
agents and observers, repository reassignment, unchanged earlier history and
exact matching-profile retries. The full disposable integration suite and
`make check` passed. The [report and manifest](verification/assessment-binding-2026-09-10.md)
retain the baseline failure and checks. The initial native history tool passed
its first API check; the native write tool remains unimplemented. Their unfinished
changes are saved under `/tmp/cairn-native-assessments-20260910` and excluded from
this checkpoint. No answering-model run or task-value gain is claimed. All earlier
implementation history is preserved.

### Assessment repair CI comparison correction — 2026-09-10

CI for repair commit `14c61eb` failed the new retained-history assertion after
its authorization checks passed. A local `TZ=UTC` reproduction isolated the
comparison: pgx and JSON history used different Go location representations for
the same timestamp. The test now normalizes both to UTC while comparing every
assessment field. Focused disposable checks passed in UTC and the host timezone;
`make check` passed. Production code is unchanged by this correction.
The [repair report](verification/assessment-binding-2026-09-10.md) retains the
failed CI run and reproduction. Installation remains pending successful CI.

### Assessment binding repair installed — 2026-09-10

Clean CLI/API `bb600c5` is installed after
[successful CI](https://github.com/halbritt/cairn/actions/runs/34564679136).
The API process executable matches the tested CLI hash. A fresh hosted search
and pull verified the current assessment guide v3; the agent's protected report
access still refused. The API restarted without restarting PostgreSQL or running
a migration. The exact inventory of 91 ordinary record versions, semantic worker
and semantic service settings remained unchanged. The
[installation manifest](verification/assessment-binding-2026-09-10.json) retains
source identities, executable hashes and before/after checks.

This completes adoption of the write-binding repair. Native assessment tools
remain in the saved work-in-progress patch for subsequent implementation. No new
task-benefit result or roadmap completion is inferred from installing the fix.

### Native assessment tools implemented — 2026-09-10

MCP and the custom OpenCode adapter now expose `cairn_assessments` and
`cairn_assess`. Agents can read owned review history, append a qualitative
account with unknown acceptance, and verify or revise it through the same native
interface they use for memory. The existing API owns receipt binding, version
checks, request idempotency, evidence validation and testimony attribution. The
write response contains identifiers and attribution without echoing the reason.
No new evaluation standard, automatic outcome inference or ranking change is
introduced. Explicit OpenCode permissions still govern both operations.

The initial MCP write check failed because the tool was absent. Its completed
workflow passes against a disposable API, covering unknown review text, exact
retry, conflicts, retained corrections and foreign-owner refusal. The old
OpenCode build similarly refused the missing history tool. With the new adapter,
13 native calls passed, including permission-denied reads/writes and unchanged
history after refusal. The fixture supplied pinned plugin dependencies and no
answering model. Its initial bootstrap error treated a successful migration
response as if it required a data field; that check-script error was corrected
before the native baseline comparison.

The full disposable integration suite and `make check` passed.
[Verification details](verification/native-assessments-2026-09-10.md) preserve
the checks and limits. Installation remains separate from this source checkpoint.
These tools remove a manual CLI step, but native review quality and additional
task value still need observations from actual work. The full earlier history
is retained, including the API repair that this work exposed.

### Native assessment tools installed and used — 2026-09-10

Clean CLI/MCP `2ef6e04` and its matching native OpenCode adapter are installed
after [successful CI](https://github.com/halbritt/cairn/actions/runs/34565632417).
The running API remains on `bb600c5`, which already contains the required binding
repair. The API and PostgreSQL processes, exact 91-version ordinary-note
inventory, connection files, Codex/OpenCode configuration and semantic service
settings remained unchanged. No migration was run. The
[installation manifest](verification/native-assessments-2026-09-10.json) retains
the before/after identities and hashes. Fresh harness sessions load the new tools.

Through a fresh installed MCP process, the implementing agent read, appended and
read back an actual review for retrieval `f9110acb-dda5-45bf-8bb7-b9fd8af4b78b`.
It records the assessment guide v3 pull and acknowledges that current source and
handoff already supplied the same rules. Version 1 remains `unknown` and
`testimony`, attributed to `agent:cairn-hosted`; no incremental memory benefit,
net savings or human acceptance is inferred. The full narrative stays in owned
assessment history; its selected identifiers and hash are in the manifest.
This completes installed adoption of the interfaces, while review quality and
durable task benefit remain open.

### Codex review-tool allowlist corrected — 2026-09-10

The previous installation preserved an explicit Codex allowlist with only six
tools. Although direct MCP and native OpenCode exposed the review tools, Codex
could not. The generator had the same omission. The earlier statement that fresh
harness sessions would load the tools was incomplete; this entry records the
miss and correction without removing that history.

The generator now includes `cairn_assessments` and `cairn_assess`. A new disposable
integration check compares its parsed allowlist with actual MCP discovery, so
independently consistent but mismatched lists cannot pass. The parser check
failed before the fix; its four cases, `make check` and the full integration
suite passed after it. Actual Codex 0.153.4 exposed six tools with the old
generator and eight with the candidate. The latter completed the review workflow,
including retained reasons/evidence, unknown testimony, retries and conflict
refusals, without an answering-model turn.

This project's ignored configuration now adds only the two names; other parsed
settings are unchanged. Documentation explains upgrading existing allowlists and
removing all three write tools for retrieval-only access. The
[verification report](verification/codex-review-allowlist-2026-09-10.md) retains
checks and limits. Clean-build installation remains pending at this checkpoint.
The stored Codex setup guide v10 also has the six-tool list; its selected
correction follows installation. These are usability corrections, with additional
memory benefit and review quality still unestablished.

### Semantic output-cap test corrected during Codex release — 2026-09-10

CI for `087be85` failed an existing semantic test: output above the cap produced
worker exit 141 instead of the copying error text the test required. Local
repetition did not reproduce it, but Go's process-wait source establishes that
unsuccessful exit takes precedence over copying errors. The test now checks
complete valid JSON at the 64 KiB boundary and refusal above it, accepting either
underlying error through the existing worker-failure classification.

Thirty focused race repetitions, the semantic race suite and `make check` passed.
A temporary cap-removal mutation failed both oversized cases. No production
worker code changed. The [Codex correction report](verification/codex-review-allowlist-2026-09-10.md)
preserves the failed CI and repair evidence. The clean installed CLI remains
`2ef6e04` pending successful CI for this correction.

### Codex correction installed and setup guide maintained — 2026-09-10

[CI for `3acf719`](https://github.com/halbritt/cairn/actions/runs/34567566389)
passed, and clean CLI/MCP `3acf719` is installed. The installed generator and
resolved project configuration expose the same eight tools. The API remains
`bb600c5` and the native adapter `2ef6e04`; the API/PostgreSQL processes,
configuration hashes and exact 91-version ordinary-note inventory were unchanged
by installation. No migration was run.

The selected Codex setup guide then advanced from v10 to v11. Its obsolete
setup paragraph now names both review tools and explains existing-allowlist and
retrieval-only configuration. Fresh search/pull verified the exact intended body,
with other metadata preserved; history returned v10 unchanged. This adds one
note version after the installation check. The [manifest](verification/codex-review-allowlist-2026-09-10.json)
retains installation and selected correction evidence without committing note
bodies. The native Codex interface gap is closed. Broader task value and the
remaining roadmap requirements are still open.

### Ordinary agent request help implemented — 2026-09-10

The preceding guide-maintenance task encountered an actual CLI gap: `agent edit
--help` returned a JSON-request error, requiring source lookup to choose the
replacement request. `agent --help` now introduces ordinary operations, and nine
JSON operations provide readable request examples through `--help` or `-h`.
Help works before home lookup, credential access, connection or stdin; normal
execution retains its JSON envelopes and API contracts.

The offline-help test failed before implementation. Actual subprocess checks pass
with no HOME and stdin left open. Copied examples complete real hosted API
corrections, citations, history, unknown qualitative reviews and reconstruction,
including original retries and stale-version refusal. CLI race tests, `make check`
and the full disposable integration suite passed. The first full run revealed
shared test evidence affecting a later count assertion; giving the example its
own evidence fixed that fixture collision. The [verification report](verification/agent-help-2026-09-10.md)
preserves both runs. Installation is pending at this checkpoint.

This is a practical CLI usability improvement, with no new evaluator, authority,
API or schema. The retrieved connection lesson and current source both supplied
the execution-default constraints; their overlap prevents attributing the result
to memory alone. Broader task value remains open.

### Ordinary agent help installed — 2026-09-10

[CI for `03ff22c`](https://github.com/halbritt/cairn/actions/runs/34568798147)
passed, and clean CLI/MCP `03ff22c` is installed. The installed binary passes the
offline overview and all nine operation-help checks, including absent HOME,
open stdin and invalid invocations. `cairn agent replace --help` now provides the
request shape that previously required source lookup.

The API remains `bb600c5` and the native OpenCode adapter `2ef6e04`. API and
PostgreSQL processes, configuration hashes and the exact 92-version ordinary-note
inventory were unchanged. No migration or restart was required. The
[installation manifest](verification/agent-help-2026-09-10.json) retains the build
and before/after checks. This completes the CLI usability change, while durable
memory benefit and the remaining roadmap requirements stay open.

### Everyday agent flag help implemented — 2026-09-11

The four ordinary flag-based agent commands now expose readable `--help` and
`-h`: `search`, `remember`, `pull`, and `pull-evidence`. Each help path runs
before home lookup, credential access, API connection, or stdin reads. Search,
remember, and pull execution share their flag constructors with help, avoiding a
second option list. Positional use remains described separately. Normal requests,
explicit and default connection handling, JSON operation help, response envelopes,
and malformed-call refusal are unchanged.

The public baseline returned `INVALID_REQUEST` for all four help commands. A
bounded OpenCode implementation trial then exited 1 after 461.6 seconds and left
only an untracked print-based scratch probe, not an implementation or asserted
test. It made one Cairn search and no pull. Seven denied shell command forms added
trial friction but are not treated as the sole cause. The retained run is assessed
`rejected` with `failure_domain=capability`; root implementation and the model-run
result remain separate.

Four test-first slices reproduced the missing operation help before each path was
added. The consolidated Go test covers both spellings and current option/usage
signals without HOME, connection, or stdin. The public subprocess check leaves
stdin open, verifies the actual current flags, and keeps malformed and literal-help
forms out of the help path. `make test-integration`, `make check`, and the independent
baseline-derived check pass. [Verification and limits](verification/agent-flag-help-2026-09-11.md)
preserve the source and trial identities.

Pincite packet `pkt-1fda759a1a719776` has a validated `verified` decision
receipt and closed citation observations. Its remaining obligations concern
unaffected ingestion, ranking, UI, configuration-reference and data-identity
surfaces, or the already-resolved no-change question; they are nonmaterial to
this CLI-only change.

This is implemented CLI usability, not installation acceptance or evidence that
memory improved the failed model run. The trial's one search did not lead to a
pull or usable patch. Durable memory benefit and the remaining roadmap work stay
open.

### Everyday agent flag help installed — 2026-09-11

[Exact-source CI for `75eb01e`](https://github.com/halbritt/cairn/actions/runs/34585053210)
passed both the OpenCode-plugin and disposable-PostgreSQL jobs, including the race
suite, Python checks, static checks, build, and authenticated CLI checks. Clean
CLI/MCP `75eb01e` is installed in the user's CLI location. Its public offline
check passes for the overview, nine JSON operations, and the four flag operations
under both help spellings, with absent HOME and open stdin; malformed calls remain
`INVALID_REQUEST`.

The API remains clean `bb600c5` and the native OpenCode adapter remains unchanged
from `2ef6e04`. API PID 555696, PostgreSQL PID 163669, service executable hash,
adapter hash, configuration hashes, and the exact 93-version ordinary-note
inventory were identical before and after installation. No service restarted and
no migration ran. The [installation manifest](verification/agent-flag-help-2026-09-11-installation.json)
retains those checks and the rollback-binary location. This accepts the local CLI
installation only; setup-time savings, model-task improvement, durable memory
benefit, and the remaining roadmap requirements stay open.

### Shared memory installed in four harnesses — 2026-09-11

The owner's current goal supersedes the earlier deferral of Agy and Claude Code.
Both Codex profiles, OpenCode, Agy, and Claude Code now load Cairn from user-level
configuration across projects. Existing notes remain in the same shared collection;
no database or API change was needed. Agy and Claude receive session labels from
a small installed launcher, removing manual task/run setup.

Codex created a useful setup note from Pincite. OpenCode read and extended it from
Striatum Next; Claude read and extended it from Pincite; Agy read and extended it
from Striatum Next. Fresh Codex and OpenCode sessions retrieved the resulting
version 4 from the other projects. Agy also corrected the existing priority note
from the updated roadmap and read the new version. These were native installed
tool calls: Codex app-server and OpenCode's native tool executor, plus ordinary
model sessions in Claude and Agy. The [usage guide](shared-memory.md) records the
configuration locations. This completes the near-term access goal; broader
memory usefulness and full design acceptance remain separate work.
