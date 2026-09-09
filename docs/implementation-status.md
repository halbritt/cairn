# Implementation status — 2026-09-09

Cairn is a usable local alpha for explicitly selected agent memory. It stores
versioned notes and supporting evidence, makes them available through CLI,
Codex and OpenCode interfaces, and records supplied context and observed outcomes.
The installed system supports ordinary cross-session use today.

The strongest usefulness evidence is one successful knowledge transfer between
harnesses and one accepted configuration task after a stored procedure was
corrected. Durable improvement across coding tasks and harnesses remains
unestablished. The [roadmap](roadmap.md) retains the complete requirements and
acceptance boundaries; full Stage 1–2 completion is not claimed.

This snapshot assesses source through `19facc1` and the local installation on
2026-09-09. Feature reports below preserve their own verification dates and limits.

## Working capabilities

| Area | Implemented behavior | Details |
| --- | --- | --- |
| Ordinary memory | Authenticated capture of selected notes, retained versions and attribution, repository/task/run scope, compare-and-swap edits, and body-only corrections that preserve metadata. | [Capture and use](../README.md), [ordinary revision](mcp.md#tools) |
| Retrieval | Lexical search, matching source previews, bounded browsing with continuation, currentness and destination filtering, mandatory context, and exact body pulls with live checks on retries. | [Index and pull](index-and-pull.md) |
| Semantic discovery | Optional local CPU scoring for vocabulary mismatches, after eligibility checks; bounded work and labelled lexical fallback. Historical recompilation uses retained scores. | [Semantic discovery](semantic-discovery.md) |
| Supporting evidence | Explicit capture of up to 1 MiB, SHA-256 verification, lossless binary inspection, persisted check generations, and whole-object or byte-span pulls through existing indexed handles and budgets. | [Evidence refresh](evidence-refresh.md), [span verification](verification/evidence-spans-2026-09-09.md) |
| Harness access | Five ordinary tools for search, body/evidence pulls, capture and edit through MCP; Codex conversation scope and native OpenCode session scope. OpenCode configuration generation and a bundled native-tool installer are shipped. | [MCP](mcp.md), [OpenCode tools](opencode-tools.md) |
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
  Codex currently uses the documented project TOML configuration; a Codex
  configuration generator is being investigated and is not implemented.
  [Codex adoption](verification/codex-project-2026-09-09.md),
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

Live checks on 2026-09-09 found:

- Installed CLI and running API: clean implementation commit
  `45ca33b9bd2431bffe9c83a44efa51de134d7220`. Both executable hashes match
  `50c2f23d43e2be6a04ce51f379b927f63f084b4440986dad1ed7b210304c6b31`.
  Later source commits through `19facc1` contain verification and benchmark
  metadata changes; the one-thread candidate was not deployed.
- Dedicated PostgreSQL **17.10**, with migrations **001–029** applied. Data and
  socket remain under `~/.local/share/cairn`; captured evidence and canonical
  packages are in PostgreSQL, while run-directory context copies need separate
  retention accounting.
- `cairn-api.service` is enabled and active. Its required `cairn-store.service`
  is active and starts as a dependency; the store unit itself is not enabled.
  The API executable was verified through its running process.
- A scoped ordinary hosted-agent profile supports routine repository use.
  Agent and observer roles remain separate. The trusted project's Codex MCP
  configuration exposes all five ordinary tools with conversation scope and
  optional startup. The installed native OpenCode adapter matches the current
  span-capable source and retains its existing connection settings.
- Optional semantic discovery uses the prepared local CPU worker, batch size
  one and two ONNX threads. The installed script matches the retained baseline
  SHA-256 `d042326bc0f1bea838434b354fa21484222277092e6db1a7c1f475212e410954`.
  It adds no persistent embedding cache or model service.

The [span deployment report](verification/evidence-spans-2026-09-09.md#local-deployment)
records the backup, executable/adapter checks and hosted smoke path. Installation
paths and credentials stay outside Git. No automatic backup rotation, pruning
or grooming timer is installed. Use the user services for the managed store's
lifecycle; standalone scripts also support isolated/manual installations.

## Verification coverage

The latest implementation change passed disposable PostgreSQL integration with
Go's race detector, all Go package tests, 26 Python tests, vet and formatting.
Its optional checks exercised the actual CLI/Unix API, independent MCP stdio
client, native OpenCode tools and cached-response compatibility with the previous
binary. The linked feature reports identify their fixtures and limits.

[CI for assessed source `19facc1`](https://github.com/halbritt/cairn/actions/runs/34378500108)
passed. The thread experiment ran twenty-one complete paired worker cases
and retained raw measurements outside the repository; it did not change store or
runtime behavior. No production database was used for those tests. Today's
installation checks read service/build information and database schema metadata.

## Remaining work and priorities

1. **Demonstrate durable task value.** Complete real tasks with observed use of
   relevant memory and independent outcome checks. Broader native Striatum
   ingress, task/host acceptance and cross-harness transfer remain partial
   (U1, U4/U5, E4, D2). Do not rerun retired cohorts unchanged or infer benefit
   from delivery alone.
2. **Finish practical retrieval and evidence lifecycle.** Managed artifacts above
   the inline limit, persisted as-cited span/version relations, richer dependent
   invalidation and source freshness remain open (L4). Byte-span reads do not
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
