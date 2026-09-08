# Implementation status — 2026-09-07

Cairn is a usable local alpha. It can retain project notes, retrieve them on a
later run, supply a bounded context package to a process, and show the retained
package and observed outcome afterward. This advances the initial record-only
slice; it does not complete every Stage 1–2 design contract.

The [roadmap](roadmap.md) is the complete requirements and acceptance tracker.

## Contract repairs — 2026-09-07

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

## Observation, retrieval and recovery additions — 2026-09-07

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

## Real OpenCode repair trial — 2026-09-07

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

## Record-body deletion — 2026-09-07

Migration 016 adds [operator forgetting](deletion.md), immediate exclusion of
record/package/cached-response payloads, tombstones, dependency flags and durable
per-target database purge effects. D events join the unsigned checkpoint subset.
Disposable tests cover stale previews, citation races, interrupted worker recovery,
restored pending deletion and explicit backup/provider/metadata residuals. This
is partial L5 implementation; metadata/evidence redaction, historical file adoption,
newer-deletion restore reconciliation and retention remain open.

## Managed context files — 2026-09-07

Migration 017 registers new run context slots with their directory identity and
ownership marker. The CLI purge worker removes those slots under the writer's
filesystem lock and retains observed completion/failure. Process-death tests
cover unlink before DB commit, retry and restored pending effects. Outcome files
survive. Existing unregistered run directories are not automatically adopted.
See [ownership and remaining limits](managed-context.md).

## Working paths

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

## Evidence collected

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

## Current local installation

This build includes migrations 001–018 for Cairn's dedicated PostgreSQL 17
store. The upgrade was preceded by a private dump. A subsequent dump includes the
new expected audit-set catalog and verifies against the installed store.

The `cairn-api.service` user service is enabled and requires
`cairn-store.service`. Both are running. Agent and observer profiles have distinct
trusted principals, are limited to the Cairn repository and local destination,
and use owner-only credentials outside the checkout. The installed client created
an ordinary currentness lesson, retrieved its v4 pointer index and expanded its
body with one credit spent. No promotion or task-acceptance claim was inferred.

## Limits and next work

These are implementation gaps, not questions waiting on the operator:

1. Wire a real Striatum/OpenCode task through declared sealed inputs and actual
   authenticated spawn/terminal and acceptance boundaries. The local Unix API
   and fixture harness tests are prerequisites, not real-host acceptance.
2. Calibrate a harness and response budget that can finish a historical repair,
   then extend recurrence evaluation against native/search and model baselines.
   The first real OpenCode comparison produced no repair in any arm. The
   12-subject coverage trial still has no relevance labels or avoided-failure
   evidence. Do not enable grooming from coverage or delivery alone.
3. Complete class-proportional lifecycle, including ordinary deletion, explicit
   supersession, governed scope/policy changes and richer conflict outcomes.
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
