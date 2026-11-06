# Release installation and acceptance contract

Revision: `release/2`, proposed for campaign X-01 on 2026-11-06. Review packets
and Plane items identify the exact candidate commit; root's shared index
records the accepted revision and commit. This is a
specification and source characterization, not evidence of an implemented or
deployed hybrid runtime. Independent criteria review and the overall lead's
X-01 closure are required before new code consumes this revision.

The authoritative dependency tables and migration corrections are in the
[implementation plan](../hybrid-runtime-implementation.md). They supersede
the architecture proposal's concurrent-model, instant-rollback and existing
vector-norm claims. The initial source baseline is
`d3c255a4bb75438e93527eaa1d83da780fd11eea`; inventory observations must record
their own current source and artifact identities.

## Ownership and interfaces

The release team owns installation, host adapters, independent acceptance,
documentation, deployment evidence and retirement. The overall lead owns
cross-team decisions, the shared contract index and landing queue. This file
has one release-team writer at a time. R-B2's independent baseline evidence is
separate from the installers it will evaluate; R-R1 reviews criteria before
implementation and exact candidates before landing.

| Interface | Provider | Consumers | Required boundary |
| --- | --- | --- | --- |
| Lifecycle command grammar, stdin/stdout/stderr, exit/refusal semantics | Foundation and Coordination | Installers and host adapters | Preserve the characterized full command surface, including already issued inbox commands; the proposal's abbreviated grammar is insufficient. |
| Binding admission, state files, ledgers and native sessions | Foundation | Coordination, installers, adapters | Preserve authenticated identity, compatible schemas, lock and persistence order; release does not implement a competing validator. |
| Journal, process identity, wake and custody | Coordination with Foundation primitives | Installers, adapters and installed acceptance | No replacement conversation, new credentials, alternate inbox claim or uncertain-action replay to refresh a client. |
| Worker binaries, model assets, derivation and rebuild runbook | Semantic | Release staging and deployment | Offline artifact identity, fixed executable launchers, one indexing generation across the shared database/job queue and tested reverse rebuild. |
| Install, pin, drain, restart and rollback | Release | Every team and supported client | Immutable selected code, explicit disabled staging, checked activation and observed live connections. |

Foundation supplies `/3` validation and any necessary shared state primitives;
release supplies installation and its independent black-box proof. The exact
`/3` descriptor and lifecycle command spelling must agree with Foundation's
reviewed contract before R-02 code starts. The overall lead's X-01 ownership
decision assigns `scripts/install-semantic.sh` to Semantic S-B2 for S-04
packaging. Semantic supplies the binary/model/native-library identity manifest,
architecture/ABI requirements and fixed rank/stream/embed launchers; release
supplies the host inventory, reviews installer compatibility and owns final
service configuration and staging activation. Release does not edit that
installer without transfer.

Semantic S-B2 also owns the first dedicated Cargo/build/actual-worker acceptance
Makefile targets. Root transfers Makefile ownership to release for R-05/final
suite wiring only after those targets land. Foundation F-B1 owns initial
lifecycle registration in `cmd/cairn/main.go` and `cmd/cairn/commands.go`;
Coordination uses separate implementation files and a callable dispatch seam.
Subsequent shared CLI/service edits require an explicit root slot.

## Current source facts and preservation boundary

These observations describe source, not current installations:

- `integrations/lifecycle/inbox_binding.py` declares
  `cairn.inbox-recall-binding/2`. It validates owner-only binding metadata,
  owner-controlled absolute configuration paths and hashes, complete pinned
  Python code, boolean activation and compatible memory/coordination profiles.
  Bound recall currently permits Codex and Claude, one to eight coordination
  configurations, distinct explicit homes when sharing memory, and integer
  context budgets from 1000 through 9500. Other harness support does not imply
  support for this binding.
- `scripts/install-claude-hooks.py` preserves selected existing memory
  configuration and custom state. It refuses replacement under an enabled
  recall binding. `scripts/install-agent-coordination.py` likewise preserves
  custom state and checks enabled bindings, including other accounts sharing
  the installed runtime. The tested legacy behavior does not establish that
  concurrent installers or a future `/3` transition are safe.
- `integrations/lifecycle/lifecycle_bundle.py` owns the legacy complete module
  inventory. [Installed lifecycle bundles](../../lifecycle-bundles.md) requires
  staged entrypoints to run without a checkout, execute validated bytes, keep
  bundle import caches isolated and refuse missing or changed helpers.
- `scripts/test_installed_lifecycle_bundle.py` runs installed Python entries in
  a network-isolated `bwrap` home without the checkout. It covers memory
  entries for Claude, Codex, OpenCode and Hermes and coordination entries for
  those harnesses plus Agy. Its Codex memory installer fixture replaces native
  hook trust setup; that fixture alone does not verify real Codex trust.
- The installed coordination surface includes the `inbox` family and the
  watcher's `--config-dir` option in
  `integrations/lifecycle/coordination.py`. Coordination owns its full grammar
  and issued-context compatibility fixtures; release must not invent an
  incompatible shorter recovery interface.
  Its `flush_journal` also has a specific `finish_reply` recovery exception:
  when a committed completion lacks the exact result reference and no response
  exists, it retries that same completion to recover the reference before
  creating the reply. Preserve this exception alongside ordinary pending-slot
  automatic replay; it does not permit replaying arbitrary settled/refused
  commands. `flush_inbox_intents(scope='attempt')` still flushes all
  conversation-owned journals; scope changes the settlement Boolean, not the
  replay set. Installed state/recovery acceptance follows Coordination's
  characterization of these cases and Foundation's session boundary.
  `NativeSession.settle(strict=False)` retains its prior diagnostic on a
  nonexception uncertain result, as covered by
  `integrations/lifecycle/test_native_session.py`; release must not accidentally
  strengthen this behavior when moving installation and adapters.
- `scripts/install-semantic.sh` currently uses `semantic/venv`.
  `semantic/embedding_command.go` supervises an executable command; service
  flags do not carry arbitrary launcher arguments. Semantic provides fixed
  mode/model launchers and the selected derivation. The record-keyed semantic
  storage does not retain complete Python and Rust generations concurrently.
  `semantic/index.go` seeds and leases `cairn.semantic_job` without a collection
  predicate and replaces documents per record. Writer exclusion therefore
  covers the entire shared database/job queue, including writers configured
  for other collections, not just the collection being released.

R-01 records the complete operation matrix from actual source and installed
observations. Each row names harness, account selector, operation/event,
configuration source, code/pin identity, supported or unsupported result,
source fixture, installed observation and live-client observation. Each live
session also records whether hooks are cached, the supported hook/MCP refresh
method and its actual scope, and evidence that the selected hook identity took
effect. A current MCP connection does not prove that cached hooks refreshed.
Inventory `/1`, two-file and `/2` selections separately; the existing
`scripts/check_effective_recall_deployment.py` recognizes historical `/1`.
An unreviewed live legacy selection blocks its migration; frozen trials retain
their original executors and pins. Unknown or
unobserved entries remain explicit. Agy memory and each harness's idle wake,
cancellation and recovery are checked individually; neither a shared installer
nor a successful MCP connection proves the whole row.

The [dated prior deployment](../../verification/event-admission-deployment-2026-11-06.md)
names Proximal, Archon and Talos and documents account and repository pin drift,
live MCP refresh and running-image verification. It is discovery context, not
a current inventory or proof of the hybrid release. Re-inventory those hosts
and any additional installations discovered through existing configuration.
Unreachable or uninspected hosts are gaps, not evidence of absence.

## Required install and transition behavior

The cases below are requirements for new code. They have not passed merely
because a corresponding legacy test exists.

1. Stage each lifecycle executable in an owner-controlled immutable,
   hash-addressed location. The receipt identifies source/build and executable
   SHA-256. Its binary, binding and configuration descriptors select the same
   bytes used at execution. Validate absolute paths, ownership, write modes,
   hashes and supported schemas before effects. The proposal specifies an
   executable mode of 0755 within a private 0700 staging directory; existing
   unrelated owners and modes must survive installation.
2. `/2` pins authenticate only the complete retained Python bundle. They
   never authenticate a Go executable. Continue through the selected legacy
   executor or require explicit drain and `/3` repin. Keep the previous
   immutable release for explicit rollback; no automatic pruning occurs.
   Use versioned paths for `/3` bindings and their configuration snapshots;
   never rewrite an old `/2` binding into `/3` under cached legacy callbacks.
   An old entry given a `/3` descriptor, or a new binary presented only a `/2`
   descriptor, fails closed before effects with the characterized streams and
   exit status. Deliberate `/2` routing invokes the preserved legacy executor,
   rather than treating its pin as binary authentication.
3. Disabled staging permits preparing files without enabling callbacks.
   Activation follows successful contract validation and verified drain.
   An enabled binding, unsettled callback/attempt, conflicting installer,
   incompatible state or changed selected code must prevent unsafe replacement.
   Do not treat changing an `enabled` flag alone as proof of drain.
4. Preserve unrelated configuration, account and collection bindings,
   credential selectors, custom state paths and state bytes. Do not provision
   credentials or inspect their contents. Test preservation with synthetic
   configuration and credentials in disposable homes.
5. Complete or safely fail atomic staging, pinning and activation under
   concurrent install and interruption. Inject failures before and after
   each durable boundary; the result is a valid old selection or an explicit
   disabled, recoverable selection, with no partial active configuration.
   Failed or uncertain external effects are reconciled rather than replayed.
6. Drain old hooks and native attempts before switching their executor. Refresh
   watchers and clients through supported mechanisms. Verify actual running
   images, account and repository-local pins, active MCP/channel connections
   and conversation continuity. A staged binary or version string alone is
   insufficient. Unsupported refresh remains an explicit release blocker for
   an installed client; do not replace its conversation to bypass it.
   Process parentage alone does not identify a thread's active MCP connection.
   Check connection identity separately from retained process accounting, and
   verify the native refresh method's actual scope before using it. An
   app-server-wide reload is not a thread-specific force-reconnect operation;
   an accepted reload does not prove replacement or reaping.
   Keep every interpreter, entrypoint, configuration snapshot and context path
   named by a live hook or issued completion/reply argv executable and compatible
   until all its unsettled references finish and its session refresh is proved.
   Existing `coordination.py` embeds `sys.executable` and its own absolute path
   in issued argv. No reference may be silently redirected to an incompatible
   descriptor. An explicitly unsupported cross-version invocation has a tested
   fail-closed diagnostic; it is not a substitute for preserving supported
   unchanged explicit retries. Missing refresh/reference evidence blocks removal.
7. Reverse lifecycle rollout through the preserved immutable `/2` release only
   after the same drain, pin validation and supported client refresh. Compatible
   persistent state remains readable and writable by the selected executor;
   any discovered incompatible state requires an independently reviewed
   transition before dependent implementation.

## Activation units, drain and coexistence

The production transition unit is a host's presence watcher plus **every**
binding served by its selected `--config-dir`, all associated memory bindings,
callback entrypoints and account/repository/session selections. Any shared
runtime root, ledger or native-session state expands the unit to include its
other users. One release owner controls it. `/2` and `/3` may coexist across
independent units; this contract does not permit two production watchers or
executors concurrently owning the same unit. A mixed unit stays on its retained
legacy selection until every member can transition safely. No Go watcher is
assumed to serve `/2` sessions without an explicit separately tested contract.

R-02 proves immutable staging, pins and activation controls using the implemented
F-01/F-04 admission/state operations. It cannot certify Go recall/capture or a Go
watcher that its dependencies do not yet provide. Complete watcher drain,
switch, restart and rollback qualification belongs to **R-06**, after R-03,
R-04, R-05 and S-05; its J-06 dependency includes J-05 and F-02/F-03. R-07 repeats
that qualified procedure on actual units. This allocation preserves the plan's
dependency edges and HC-05 gate; root must record it in X-01's reconciliation.

A drain receipt names the unit, affected session/agent/execution IDs, source and
pin identities, start/end observations and the held quiescence barrier. Every
predicate below must be true, with no unknowns. Historical terminal fields need
not be deleted; inspect their correspondence and status, not key absence.

| Predicate and source | Required negative fixture / safe outcome |
| --- | --- |
| Disabled binding and no effective positive activation grant for an owning turn; inspect `inbox_binding.py` and `grant_ledger.py` decisions, not just the manifest Boolean. | REL-DRAIN-D1: disabled manifest with a retained positive ledger grant still blocks transition. |
| Every native-session intent/attempt is terminal with matching authoritative close, completion/ack and requested reply facts; `native_session.py:ATTEMPT_KEYS` defines the retained fields. No active cancellation/custody hold remains. | REL-DRAIN-D2: retained active intent, attempt or mismatched close blocks; a correctly terminal retained record is not falsely classified as active. Stale process identity alone does not prove settlement. |
| Owning sessions/watcher have completed their authorized journal settlement with the equivalent of `flush_inbox_intents(scope='all')`; no pending/running slot or live unresolved reply obligation remains. `reply_possible` is checked separately because the existing settled Boolean excludes it. | REL-DRAIN-D3: pending slot, lock owner, missing exact completion reference or unresolved `reply_possible` blocks. Recover the narrow `finish_reply` case through its existing owner; never replay arbitrary settled/refused or held/failed work. |
| No active wake attempt and no live delivery lease/hold for affected sessions. Sources are authenticated `agent wake-attempts` with `active:true`, and the existing store-owned `cairn.agent_delivery`, `cairn.agent_wake_attempt` and session-inbox state, using reviewed read-only status inspection. | REL-DRAIN-D4: an active wake, nonterminal hold or live lease blocks even when no hook process exists. Missing visibility is unknown, never zero. |
| Watcher and wake launchers for the unit are stopped/joined; no old lifecycle callback is running. Ordinary capture already admitted finishes against the retained immutable release. | REL-DRAIN-D5: a running callback, delayed child or restarting unit blocks; do not kill a conversation or fake retirement to make the predicate pass. |
| A demonstrated quiescence barrier prevents new callback admissions, inbox claims and wakes throughout the check/stage/refresh/activate interval. It covers cached hooks, direct issued entrypoints and every unit member, not merely competing installers. | REL-DRAIN-D6: inject an admission/claim/wake between inspection and activation; it is prevented or invalidates the receipt and activation refuses. A quiet sleep or a new lock ignored by legacy code is insufficient. |
| Selected configuration/pins and effective session hooks are read back under that barrier; rollback or failure leaves a complete old selection or disabled recoverable unit. | REL-DRAIN-D7: interrupt each durable boundary and race a configuration/reference change. No partial selection becomes active. |

Journal flushing sends commands and is **not** a read-only inspection method.
Sessions finish their own supplied contexts; the watcher uses only its existing
authorized recovery path. The release inspector observes settled facts and
never completes another agent's delivery or claims its inbox. Foundation and
Coordination must provide a side-effect-free fact seam for any missing
observable before dependent acceptance. Fixtures map status inspection to the
actual schema and access checks; this specification invents no new CLI API.
The orchestrating invocation must not itself retain an affected unresolved
native attempt. Arrange a supported safe boundary/control invocation without
re-registration, a replacement conversation or an exemption from the predicate.

Compatibility qualification also runs real `/2` Python and `/3` Go processes
against the same fixture lock paths, journal, native-session record and ledger.
Exercise contention, timeout and crash at every persistence boundary; after
each handoff the other executor reads the surviving state, including pending
and `reply_possible` journals. Verify one mutation owner, preserved lock order
and exact retry/reply references. This is a disposable compatibility test,
not permission for an unqualified mixed production unit.

Before R-02 implementation review, publish the install lock's path, unit scope,
wait/refusal behavior and ordered durability map. The map includes staging and
syncing immutable bytes/receipt, publishing the immutable path and directory,
writing/syncing versioned configuration and pins, disabled selection publication,
supported refresh/readback, final activation publication and its parent sync,
and recovery-receipt persistence. Inject faults on both sides of every actual
durable boundary. Only the final validated selection becomes active; a
multi-file copy is not an atomic activation protocol.

## Rollback, observation and retirement

Before R-08 the rollback target is the retained `/2` executor and Python semantic
worker, including their interpreter, websocket package, venv/native libraries,
model/config assets and fixed launchers. R-08 may remove their **active**
requirements only after an offline, hash-pinned restoration closure is retained
and tested for each supported target architecture. A directory of Python source
alone is insufficient. Post-R-08 rollback restores that declared closure from
offline artifacts into a clean disposable home, restores the selected bindings
and launchers, and executes lifecycle behavior plus the semantic reverse rebuild
against a disposable database with the retired dependencies initially absent.
No network download or assumed global package satisfies this fixture.

The post-retirement target therefore remains the executable retained Python
release, not an implicitly chosen older Go/Rust build. Changing that target
requires a root decision and its own accepted rollback fixture. Keep all paths
still needed by live issued argv/hooks until reference and session-refresh proof
permits retirement. Unknown references or unavailable restoration artifacts
block R-08; preserving them is preferable to an unsupported removal claim.

An observation lane is host × harness × account/binding, plus one semantic lane
per shared database/job queue. R-01 maps each lane to supported operation rows
and expected identities. Each installed supported memory event and each
supported request/reply/recovery path needs an observed cycle; include idle,
busy, restart, refusal and cancellation only where supported. Unsupported rows
have explicit refusal/no-op expectations and are never counted as successful
supported operations. Each lane needs at least 24 continuous hours after its
last relevant runtime change, and the semantic lane needs a full rebuild.

A relevant change affects the lane's executed binary, active configuration,
launcher, model or supervised service. Reset every affected lane, including
dependent lanes when the semantic service changes. Rehearse destructive rollback
in disposable homes/databases using the exact release artifacts. If a separately
coordinated production rehearsal is performed, its rollback and roll-forward
restart the affected observation window. R-09 must account for the final removal
release's affected lanes; pre-removal observation cannot silently certify it.
Record the minimum cycles, timestamps, unexplained anomalies and unobserved rows.

R-07 also has a transition/backup ledger. Normal semantic derivations are
regenerated from unchanged authoritative source by the tested S-05 reverse
rebuild; this is not a database-schema rollback. Lifecycle state stays
schema-compatible and its rollback snapshot is retained and exercised. Any
additional authoritative data/schema or incompatible state transition requires
a separate root decision, current backup/restore evidence and an exact-version
restore rehearsal in a disposable environment before the transition. Never use
an existing production database for tests or cleanup; missing evidence blocks
the affected transition.

## Independent fixture and evidence matrix

Fixture IDs below are acceptance cases to implement or map to retained tests,
not new commands or claims of existing coverage. R-01 records the exact test
entrypoints, inputs, expected bytes/state and evidence for each harness row.
Each successful case includes a concrete negative or interruption case.

| ID | Required observable cases | Current source/reference and evidence needed |
| --- | --- | --- |
| REL-BASE | Every supported harness/operation and explicit unsupported case; source expectation separated from installed and live state. | `scripts/test_installed_lifecycle_bundle.py`; affected harness guides and read-only host discovery. Record exact source SHA and missing observations. |
| REL-STREAM | Exact stdin bounds, stdout/stderr, exit status, required-context/prompt refusal and optional degradation. Preserve diagnostics and attribution. | Foundation and Coordination differential fixtures plus independent installed hooks. Declare normalization only for nondeterministic fields; never erase refusal, identity, ordering or state differences. |
| REL-IDENTITY | Wrong owner/mode/hash, stale process identity, wrong account/home and another session's context refuse before effects. Store owns principal and observation channel. | `inbox_binding.py`; `IdleWakeupPreflight` and `CompactInboxInstaller` in `scripts/test_install_agent_coordination.py`; Foundation/Coordination process fixtures. |
| REL-FRESH | Run at least one effectful installed event for every supported R-01 row, offline without checkout or retired dependencies, using a disposable API/store and real selected executables/host adapters. Assert the resulting write/retrieval/journal effect and exact streams/status, plus missing/changed-file negatives. | The cited legacy installed-bundle tests prove closure smoke behavior only: `--help`, hint/no-op memory and excluded coordination are not effectful acceptance. R-02 covers implemented F-01/F-04 paths; R-03/R-04/R-06 complete actual supported memory, hook/watch/inbox/recovery rows after their dependencies. Claude Stop is coordination, not memory capture; Agy has no current ordinary lifecycle memory hook. Do not invent support to fill the matrix. |
| REL-PIN | Pin and execute the same immutable bytes; reject mixed installations, stale sidecars and unsafe ownership/modes. Check both account and repository-local selection. | Legacy binding/bundle validation; future Foundation `/3` admission and R-02 installed tests. Include a replacement race rather than checking only a hash before execution. |
| REL-LEGACY | `/2` stays with Python; `/3` binds the Go binary. Test both incompatible cross-version directions fail closed, versioned binding/config paths, retained issued argv and cached hooks, and the declared activation-unit topology. | Legacy validation and new real-executor cross-read/contention/crash fixtures; no in-place `/2` to `/3` binding rewrite and no unqualified mixed production watcher. Frozen two-file and bundle trials stay unchanged. |
| REL-DRAIN | Every predicate D1–D7 above has positive and negative evidence under a held quiescence barrier; stop, stage, repin, refresh, restart and reverse selection preserve the whole unit. | Legacy enabled/shared-account refusal tests are partial smoke coverage. R-02 proves available F-01/F-04 staging/activation controls. R-06 qualifies the full watcher/hook unit after J-05/F-02/F-03 and adapter dependencies; R-07 repeats it for production. |
| REL-ROLLBACK | Reverse `/3` to retained `/2` with compatible state, pins and supported refresh. Before selecting the semantic rollback generation, stop and join every index writer sharing the database/job queue and prevent inactive definitions from restarting. | Custom-state/ledger preservation tests in `scripts/test_installed_lifecycle_bundle.py`; R-02 disposable homes and S-05 disposable-store switch/reverse-rebuild evidence. |
| REL-PROCESS | Real `flock` contention, cross-executor state reads, crash, pipes, supported cancellation, shutdown, process-tree cleanup, restart and state persistence. | `scripts/test_install_agent_coordination_timeout.py`, `scripts/test_install_agent_coordination_active_turn.py`, `scripts/test_install_wake_service.py`, and Foundation/Coordination process tests. Synthetic app-server cancellation does not establish public Store cancellation support. In-process mocks do not replace process assertions. |
| REL-HERMES | Actual `MemoryProvider`, registration and hook objects preserve host returns, custody/cancel and shutdown while delegating policy/state. | `integrations/hermes/`, `scripts/check_hermes_lifecycle.py` and `scripts/check_hermes_coordination.py`. Preserve required Python and test the installed adapter with missing/refusing selected binary. |
| REL-HOOKS | Required OpenCode plugin objects and each supported Claude/Codex/Agy hook retain their host contract and native trust requirements. | `integrations/opencode/`, `scripts/install-opencode-hooks.py`, `scripts/install-codex-hooks.py`, `scripts/install-claude-hooks.py`, `scripts/install-agent-coordination.py`; real native trust/refresh evidence separate from mocked installer fixtures. |
| REL-LIVE | Verify actual executable and semantic worker image/closure, fixed launcher hashes, active connection identity, and effective hook identity for each live session. Detect cached old hooks, old issued argv, repository overrides and retained processes separately. | Caller-origin `implementation_id` in `docs/client-observations.md` is reported, not attested or per-session evidence. Pair it with a session-correlated native hook/host observation. Coordination requires an equivalent supported signal or an explicit blocking gap. Repeat before release, after rollout and on the final removal commit; process parentage and a current MCP connection do not prove hook refresh. |
| REL-SEMANTIC | Inventory every index writer sharing the database/job queue, including other collections; stop/join all running writers and prevent inactive definitions from restarting before switch or reverse rebuild. Disclose partial coverage, fallback and backoff. | S-05 tests competing writers configured for different collections in one disposable database. Selected-model current version/body hashes and present, valid passage rows must cover every eligible active nonforgotten record. Check parent correspondence, ordinals, byte spans and vector validity against the accepted worker contract; document/queue counts alone are insufficient. |
| REL-EQUIVALENCE | Map each old/new assertion and seed observable faults that both checks must reject before old coverage is removed. Required model tests execute rather than skip. | R-05 records applicable changed refusal/exit, reordered operations, request-ID reuse, missing persisted field, wrong span and corrupted pin faults, their expected failures and actual results on both executors/checks. An insensitive old assertion is a disclosed gap, not permission to weaken or silently repair the reference. Retain frozen trial/SDK surfaces. |
| REL-PERF | Compare the same workload and machine conditions; distinguish build/setup time, subprocess latency, worker footprint and suite duration. | Reproducible R-01 baseline and R-05 comparison with exact commands, environment, sample count and durations. Proposal targets are not observed results. |
| REL-DOCS | Every affected current-facing CLI/config/example and operational claim agrees with implemented source and checked invocation. | Architecture/status, CLI/lifecycle references, harness guides, semantic install/rebuild, testing, deployment and recovery. Apply docs-guard; proposed commands stay labelled proposed. |
| REL-MANIFEST | One exact candidate binds source, Go/Rust/model/config and fixed-launcher hashes, running worker/interpreter/library closure, derivation, schema, host/session matrix, checks, findings, limitations and executable offline rollback closure. | R-06 candidate evidence, R-07 installed/live observations, transition/backup ledger and R-09 final reconciliation. No credentials, raw sessions, generated binaries or operational memory committed. |
| REL-OBSERVE | Each defined lane completes its minimum supported cycles and 24 continuous hours after its last relevant change; the shared-queue semantic lane completes a full rebuild. | Use the lane/reset/rehearsal rules above, disclose unobserved rows and failed/pending work, and reset after affected removal/runtime changes. Time alone is insufficient. |
| REL-RETIRE | No active configuration, process, cached session hook or unsettled issued context needs a removed file/dependency. Every session has new hook-identity evidence; no retired identity is observed during the qualified window. | R-08 exact candidate, reference/session/process inventory, initially dependency-free offline rollback restore plus lifecycle execution and semantic reverse rebuild in disposable environments. Keep required Hermes Python, frozen trials/SDK and still-referenced compatible paths. Unknown identity/reference or missing rollback closure blocks removal. |
| REL-FINAL | Recheck and redeploy the exact removal commit everywhere, including every long-lived client, then reconcile findings and clean pushed trunk. | R-09 final manifest and independent review. Pre-removal deployment evidence does not verify the removal release. |

Tests use synthetic fixtures, disposable homes and disposable PostgreSQL.
Existing operational stores are never used for tests or cleanup. A test skipped
because `bwrap`, a native binary or model assets are absent remains missing
coverage. Existing installer mocks are recorded as such and cannot establish
native handshake, process or live-client acceptance.

## Work items and release gates

Stable external IDs use source `hybrid-runtime-consolidation` and
`hybrid-runtime-v1/<key>`. Every release item is a direct child of CAIRN-301.
The dependency tables remain authoritative; dependencies are enforced even
when the tracker cannot display native blocking edges.

| Key | Plane | Builder role | Blocked by |
| --- | --- | --- | --- |
| R-01 | CAIRN-303 | R-B2 | X-01; read-only discovery and existing-fixture work may start immediately |
| R-02 | CAIRN-304 | R-B1 | F-01, F-04, R-01 |
| R-03 | CAIRN-305 | R-B1 | F-02, F-03, J-05, R-02 |
| R-04 | CAIRN-306 | R-B1 | J-06, R-02 |
| R-05 | CAIRN-307 | R-B2 | F-02, F-03, J-06, S-05, R-01 |
| R-06 | CAIRN-308 | R-B2 | R-03, R-04, R-05, S-05 |
| R-07 | CAIRN-309 | R-B1; release lead serializes production | R-06 |
| R-08 | CAIRN-310 | R-B1 | R-07 and observation exit criteria |
| R-09 | CAIRN-311 | R-B2; release lead controls deployment | R-08 |
| HC-05 | CAIRN-312 | Release team | R-02 through R-06 and the full installed harness matrix |
| HC-06 | CAIRN-313 | Release team | R-08, R-09, HC-02, HC-05 |

The installed Plane CLI lacks relation operations and no legacy relation tool
is exposed to this session. Native blocking edges are **unverified**; body
dependencies and the campaign map enforce them. Native parent IDs and stable
external IDs are read back separately. Team/model/session assignments are
metadata, not invented Plane memberships. Exact live session assignments and
operational inventory are retained outside Git.

Before building, Opus 5.5 Max reviews these criteria and the current source.
Before landing, it reviews the exact candidate commit independently, and every
blocking finding receives a fix and re-review. Green tests do not close a
finding on their own. Each author owns local validation, queued integration,
pushing main and removing the authored branch/worktree.

Runtime/store changes require `make check` and `make test-integration` locally;
lifecycle, backup and restore changes additionally run the relevant
`make test-lifecycle` coverage. Actual worker acceptance must execute the
selected Python reference and Rust artifact with prepared model assets.
Documentation-only setup does not establish any of these runtime gates.

R-06 produces a reviewed release candidate; only coordinated R-07 execution
changes production. R-08 cannot retire a runtime before observation exit and
independent acceptance. R-09 revalidates and redeploys the final removal commit.
HC-05/HC-06 remain open until their actual criteria are met. Report implemented,
tested, deployed and measured claims separately; none alone establishes full
design acceptance or usefulness.
