# Implementation status — 2026-09-09

Cairn is a usable local alpha for explicitly selected agent memory. It stores
versioned notes and supporting evidence, makes them available through CLI,
Codex and OpenCode interfaces, and records supplied context and observed outcomes.
The installed system supports ordinary cross-session use today.

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

This snapshot assesses the source changes recorded below and the local installation
on 2026-09-09. Feature reports below preserve their own verification dates and limits.
The [implementation history](#implementation-history) retains the earlier narrative
and every committed change through `95ef2ed`, including failed trials and superseded
work. Future updates should append dated history and corrections while refreshing
the current summary; do not replace the historical record.

## Working capabilities

| Area | Implemented behavior | Details |
| --- | --- | --- |
| Ordinary memory | Authenticated capture of selected notes up to 64 KiB with room for JSON escaping, retained versions and attribution, repository/task/run scope, compare-and-swap edits, body-only corrections that preserve metadata, and authenticated retained-version inspection. | [Capture and use](../README.md), [ordinary revision](mcp.md#tools), [record history](record-history.md) |
| Retrieval | Lexical search, matching previews with source byte positions, bounded browsing with continuation, currentness and destination filtering, mandatory context, and whole or explicit partial A/B body pulls with live checks on retries. | [Index and pull](index-and-pull.md) |
| Semantic discovery | Optional local CPU scoring for vocabulary mismatches, after eligibility checks; bounded work and labelled lexical fallback. Historical recompilation uses retained scores. | [Semantic discovery](semantic-discovery.md) |
| Supporting evidence | Explicit capture of up to 1 MiB, SHA-256 verification, lossless binary inspection, persisted check generations, citation-time digests and optional byte passages on qualified claim versions, and whole-object or byte-span pulls through existing indexed handles and budgets. | [Evidence refresh](evidence-refresh.md), [precise citations](evidence-citations.md), [span verification](verification/evidence-spans-2026-09-09.md) |
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

Live checks on 2026-09-09 found:

- Installed CLI and running API: clean `6dbca8b6a2a2b57c2197a9d00dc8f8a9362da7c7`,
  with SHA-256 `b77490a86e70b64af63e21edec3986b3d301f334a14f3ffbb679e76320078174`.
  Ordinary capture and edits accept 512 KiB JSON envelopes for the existing
  64 KiB note bodies. Preview positions and their OpenCode tool guidance remain
  installed. The API restarted without a migration; host settings and the
  two-thread semantic worker are preserved. Earlier capabilities remain installed.
- Dedicated PostgreSQL **17.10**, with migrations **001–030** applied. Data and
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

The [note-transport deployment report](verification/note-transport-2026-09-09.md#local-deployment)
records the current executable, preserved adapter/configuration identities and
fresh retrieval of the corrected capture procedure. Installation
paths and credentials stay outside Git. No automatic backup rotation, pruning
or grooming timer is installed. Use the user services for the managed store's
lifecycle; standalone scripts also support isolated/manual installations.

## Verification coverage

The installed note-transport implementation passed disposable PostgreSQL
integration with Go's race detector, all Go package tests, 30 Python tests, vet,
formatting and build. Its optional checks exercised the CLI/Unix API, trusted
operator commands, independent MCP stdio client and a normal native OpenCode
session with scripted completions. Mutations committed by the prior binary
retained exact retry responses. The [feature report](verification/note-transport-2026-09-09.md)
preserves the failing baseline, initial native debug-output failure and corrected
integration result. No model inference was used for these checks.

[CI for installed source `6dbca8b`](https://github.com/halbritt/cairn/actions/runs/34399146044)
passed PostgreSQL integration with the race detector, Python tests, vet and build.
The earlier thread experiment ran twenty-one complete paired worker cases
and retained raw measurements outside the repository; it did not change store or
runtime behavior. No production database was used for those tests. Today's
installation checks read service/build information and database schema metadata.

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
