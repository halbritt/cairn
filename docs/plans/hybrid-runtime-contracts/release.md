# Release installation and acceptance contract

Revision: `release/1`, proposed for campaign X-01 on 2026-11-06. This is a
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
source fixture, installed observation and live-client observation. Unknown or
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
7. Reverse lifecycle rollout through the preserved immutable `/2` release only
   after the same drain, pin validation and supported client refresh. Compatible
   persistent state remains readable and writable by the selected executor;
   any discovered incompatible state requires an independently reviewed
   transition before dependent implementation.

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
| REL-FRESH | Fresh installed entrypoints run offline in disposable homes without source checkout or retired runtime dependencies. Missing/changed selected files fail before state writes. | `test_memory_installations_are_complete_for_every_supported_harness`, `test_coordination_installations_run_outside_checkout_for_every_harness`, and `test_installed_memory_refuses_changed_or_missing_helpers_before_state_write`. Repeat against the Go artifact with required native adapters intact. |
| REL-PIN | Pin and execute the same immutable bytes; reject mixed installations, stale sidecars and unsafe ownership/modes. Check both account and repository-local selection. | Legacy binding/bundle validation; future Foundation `/3` admission and R-02 installed tests. Include a replacement race rather than checking only a hash before execution. |
| REL-LEGACY | `/2` stays with its Python executor; mixed `/2` and `/3` clients can coexist without treating either receipt as the other's authentication. | Current `/2` validation plus explicit legacy-route and drain/repin fixtures under the reviewed `/3` contract. Frozen two-file and bundle trials stay unchanged. |
| REL-DRAIN | Enabled/shared-account binding replacement refuses without mutation. Active callbacks/attempts block transition. Drain, restage, validate, repin and restart succeed after settlement. Concurrent installers and crashes preserve selection. | Legacy `test_explicit_memory_reinstall_preserves_config_and_refuses_enabled_binding`, `test_shared_coordination_reinstall_refuses_another_enabled_account`; new active-attempt, concurrency and interruption fixtures. |
| REL-ROLLBACK | Reverse `/3` to retained `/2` with compatible state, pins and supported refresh. Semantic reverse rebuild excludes every different-generation writer sharing the database/job queue. | Custom-state/ledger preservation tests in `scripts/test_installed_lifecycle_bundle.py`; R-02 disposable homes and S-05 disposable-store switch/reverse-rebuild evidence. |
| REL-PROCESS | Real `flock` contention, crash, pipes, cancellation, shutdown, process-tree cleanup, restart and state persistence. | `scripts/test_install_agent_coordination_timeout.py`, active-turn integration tests, `scripts/test_install_wake_service.py`, and Foundation/Coordination process tests. In-process mocks do not replace process-boundary assertions. |
| REL-HERMES | Actual `MemoryProvider`, registration and hook objects preserve host returns, custody/cancel and shutdown while delegating policy/state. | `integrations/hermes/`, `scripts/check_hermes_lifecycle.py` and `scripts/check_hermes_coordination.py`. Preserve required Python and test the installed adapter with missing/refusing selected binary. |
| REL-HOOKS | Required OpenCode plugin objects and each supported Claude/Codex/Agy hook retain their host contract and native trust requirements. | `integrations/opencode/`, `scripts/install-opencode-hooks.py`, `scripts/install-codex-hooks.py`, `scripts/install-claude-hooks.py`, `scripts/install-agent-coordination.py`; real native trust/refresh evidence separate from mocked installer fixtures. |
| REL-LIVE | Installed source/artifact/config identities match actual running executables and active connections; old process and repository override are detected. | Read-only current inventory and dated prior deployment as a discovery checklist. Repeat before release, after rollout and against the final removal commit. |
| REL-SEMANTIC | Inventory, stop and join every different-generation writer sharing the database/job queue, including other collections and inactive units that could restart; switch only after exclusion, then restart with the fixed launcher, rebuild and disclose partial/fallback/backoff. Apply the same exclusion for reverse rebuild. | `semantic/index.go` has no collection predicate on queue seeding/claiming. S-05 must exercise competing writers configured for different collections in the same disposable database. Selected-model document versions/body hashes must cover all eligible active nonforgotten records; an empty queue or ready query is insufficient. |
| REL-EQUIVALENCE | Replacement checks preserve reference assertions and fail for meaningful behavioral differences against both executors. Required model tests execute rather than skip. | R-05 mapping from old to new checks, exact fixture identities and before/after commands. Retain frozen trial/SDK surfaces. |
| REL-PERF | Compare the same workload and machine conditions; distinguish build/setup time, subprocess latency, worker footprint and suite duration. | Reproducible R-01 baseline and R-05 comparison with exact commands, environment, sample count and durations. Proposal targets are not observed results. |
| REL-DOCS | Every affected current-facing CLI/config/example and operational claim agrees with implemented source and checked invocation. | Architecture/status, CLI/lifecycle references, harness guides, semantic install/rebuild, testing, deployment and recovery. Apply docs-guard; proposed commands stay labelled proposed. |
| REL-MANIFEST | One exact candidate binds source, Go/Rust/model/config hashes, derivation, schema, host/client matrix, checks, findings, limitations and rollback material. | R-06 candidate evidence, R-07 installed/live observations and R-09 final reconciliation. No credentials, raw sessions, generated binaries or operational memory committed. |
| REL-OBSERVE | At least 24 continuous hours after the last relevant correction, full rebuild and representative supported memory/coordination/recovery cycles on every installed harness. | R-08 timestamped selected evidence: idle/busy, restart/cancel where supported, rollback rehearsal, anomalies, failures and stuck work. Reset the affected lane after a fix; time alone is insufficient. |
| REL-RETIRE | Each removed module/dependency has no active configuration/process references; fresh install runs without it while required Hermes Python and rollback artifacts remain. | R-08 exact removal candidate, static inventory plus installed process/config evidence, retained trial/SDK identity and local checks. |
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
