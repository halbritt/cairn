# Foundation contracts: foundation/v1

Status: acceptance criteria for the authorized hybrid runtime campaign, prepared
against `d3c255a4bb75438e93527eaa1d83da780fd11eea` on 2026-11-06.
These contracts specify work to implement and verify. They do not claim that
`internal/statefile`, `internal/procpeer`, `internal/lifecycle`, binary binding
`/3`, or the proposed lifecycle commands already exist.

The [implementation plan](../hybrid-runtime-implementation.md) controls the
sequence. Foundation owns this file. The overall lead owns the shared contract
index, cross-team decisions and X-01 admission. Dependent implementation waits
for the overall lead's explicit X-01 completion notice. Source inventory,
synthetic fixture preparation and existing baseline tests may proceed now.

## Work and dependencies

| Plan key | Plane item | Deliverable | Builder | Required predecessors |
| --- | --- | --- | --- | --- |
| F-01C | CAIRN-314 | Existing admission, binding, peer and state-file characterization | F-B1 | None for read-only characterization |
| F-04C | CAIRN-315 | Existing session and grant-ledger characterization | F-B3 | None for read-only characterization |
| F-01 | CAIRN-316 | Callable admission/binding path and shared primitives with exercised consumers | F-B1 | X-01 / CAIRN-302; accepted F-01C fixtures |
| F-04 | CAIRN-317 | Compatible session and ledger operations through a lifecycle consumer | F-B3 | F-01; accepted F-04C fixtures |
| F-02 | CAIRN-318 | Direct local API recall | F-B2 | F-01 and F-04 |
| F-03 | CAIRN-319 | Direct local API capture | F-B3 after F-04 | F-01 and F-04 |
| HC-03 | CAIRN-320 | Reviewed, integrated foundation and supported hook matrix | Foundation lead | F-01 through F-04 |

All seven items have CAIRN-301 as their native parent, verified by readback.
Native blocking relations remain unverified: the installed Plane CLI exposes no
relation command and discovery found no callable legacy relation tool. The
predecessors above and the item bodies are mandatory regardless of tracker
rendering. Session/profile assignments and selected execution evidence remain
outside the repository in the campaign cache and Plane.

F-04 does not wait for J-05. Foundation supplies compatible storage operations;
Coordination owns journal execution and turn/custody policy. J-02 consumes F-01
and F-04; J-04 consumes the peer contract after F-01; J-05 later integrates the
state operations. Release consumes binding/admission behavior in R-02, then the
memory and coordination hook implementations in its adapter work.

## Ownership and change control

F-B1 owns the proposed shared state, peer, admission and binding packages and
changes to their existing Go helper consumers. F-B3 owns session/ledger packages,
then capture. F-B2 owns recall. The overall lead allocates shared CLI registration
and Makefile edits before any writer touches them. Foundation does not edit
Coordination's journal/watcher implementation or Release's installers/adapters.
There is one writer per file; extraction and consumer migrations are sequenced.

Each shared extraction lands with an exercised consumer and its regressions.
No helper-only package is accepted from an unused API or a green compilation.
The F-R1 Opus 5.5 Max reviewer reviews criteria before implementation and the
exact candidate before landing. Findings require author fixes and re-review.
An interface revision change lists affected consumers and notifies their leads
before dependent changes merge. The overall lead resolves ownership, persisted
schema, authority and compatibility disputes.

## Admission and binding contract

The current reference is
[`hook_admission.py`](../../../integrations/lifecycle/hook_admission.py),
[`inbox_binding.py`](../../../integrations/lifecycle/inbox_binding.py),
[`lifecycle_bundle.py`](../../../integrations/lifecycle/lifecycle_bundle.py) and
[`recall_dispatch.py`](../../../integrations/lifecycle/recall_dispatch.py).
The future Go admission operation returns an explicit run/skip/invalid decision,
reason and normalized observation; callers retain their existing refusal envelope.
The decision itself grants no store authority.

Preserve these distinctions:

- Environment/child/wake guards, account-home selection, event vocabulary and
  privacy markers precede effects. `.cairn-no-coordination` does not silence the
  independent memory hook. Memory includes the explicit project ancestry in its
  privacy walk; coordination's walk uses the working-directory ancestry.
- Native coordination identity and memory identity have different validation
  rules. Claude ownership uses the channel prompt identity; Codex, OpenCode and
  Hermes use their native turn identities. Model and project values are metadata.
- Disabled binding staging removes the opt-in from the effective configuration
  without inspecting pins that are being replaced. Reserved wake activation has
  a persisted, shared per-turn decision and does not allocate or refund grants.
- Existing `/2` binding validation remains available for its complete pinned
  Python bundle. It checks owner-controlled absolute paths, configuration hashes,
  compatible profiles and distinct state/configuration homes where required.
  Current bound inbox recall is limited to the supported Codex/Claude profiles;
  other harnesses do not acquire that capability by being listed in the campaign.
- A `/2` pin authenticates Python bytes only. It cannot authorize a Go binary.
  During coexistence use the preserved pinned Python executor, or visibly refuse
  until an explicit drain and `/3` repin has been performed.

The proposed `cairn.inbox-recall-binding/3` replaces Python code descriptors with
one `binary` descriptor containing an absolute `path` and SHA-256. Validate both
the pinned binary and the running executable before effects. Preserve `enabled`
as a boolean, pinned `memory_config`, one to eight explicit
`coordination_configs`, profile agreement and the bound `1000..9500` byte budget.
A token-file path chooses a provisioned profile; no token value is a fixture or
model input. A payload principal, model label or session ID is not authentication.
The store continues to own principal, identity and observation-channel attribution.

Release owns immutable installation, drain, restage, repin, restart and rollback.
F-01 validates a callable path; it does not silently replace any live installation.
Legacy dispatch keeps the original event bytes, streams, signals and exit code.

## Shared state-file contract

The existing reference is
[`LockedStateFile`](../../../cmd/cairn/locked_state_file.go) and its
[tests](../../../cmd/cairn/locked_state_file_test.go). The proposed extraction
retains the existing generic configuration and `Update` callback semantics for
current callers, including the intermediate `save` callback, default state,
validation, size limits, caller-specific error text and blocking/nonblocking
selection. Existing Go consumers remain exercised after extraction.

The shared persistence mechanism owns lock acquisition and atomic replacement;
callers own schemas, transition policy and caller-specific refusal text. F-04
must be able to select the ledger's bounded, deadline-capped lock wait without
changing existing callers' blocking or nonblocking behavior. The default ledger
wait is 1.5 seconds in `grant_ledger.acquire_session_lock`.

For each operation, retain the real lock pathname and hold it across load,
decision and all required persistence. Do not replace a session's established
lock with a new adjacent lock merely because the generic helper defaults to
`Path + ".lock"`. Preserve lock order and intermediate durability boundaries.
Write in the same directory, set the required private mode, write all bytes,
sync the file, close, rename and sync the directory before reporting a durable
save or releasing delivery bytes. Surface failures at every required boundary.
Concurrent writers and a killed writer must leave the previous or next complete
state; initialized accounting must never reset to an empty budget.

Characterize every caller's actual mode/owner/symlink, parsing and missing-file
policy. Current Go `StrictJSON` checks trailing data; its name does not establish
unknown-field, duplicate-key or Unicode rejection. A new uniform rejection is a
behavior change if the reference accepted the input. Resolve such differences
explicitly rather than rewriting the oracle.

There is a source-level discrepancy requiring a separate repair decision:
`LockedStateFile.writeAtomic` declares a new `err` inside the `tmp.Write` `if`
block, while later checks use the outer `err`. Write/sync failures can therefore
be lost by that control flow. This is a static finding, not an executed fault
injection result. F-01C must characterize the path and the independent reviewer
must assess it; any correction is a separately classified repair with a failing
regression, followed by re-review. Compatibility work must not silently preserve
or silently fix this discrepancy.

## Shared process and peer contract

The existing sources are
[`internal/claudechannel`](../../../internal/claudechannel/claudechannel.go) and
[`unix_peer_transport.py`](../../../integrations/lifecycle/unix_peer_transport.py).
The proposed `internal/procpeer` owns Linux identity measurement and verified
socket transport. Harness-specific JSON-RPC, wake/cancel meaning and journal
transitions remain with Coordination.

Keep the `ProcessRef` wire identity `pid`, `start`, `boot`, where `start` is the
`/proc/<pid>/stat` start tick value and `boot` is the kernel boot ID. Ancillary
state and parent metadata in Python records must not disappear during roundtrip.
A numeric PID alone never certifies identity. Preserve the reference distinction
between a process-owned endpoint and a same-user endpoint: the latter can have a
peer PID different from the measured target process. Both require their actual
credential/identity policy before any payload write. Tests cover the individual
harness ownership mode, not an assumed universal peer-PID equality rule.

The consumer-facing operations measure a reference, check liveness/ancestry,
connect and validate a peer, and read/write bounded frames under one enclosing
deadline. Preserve each consumer's error classification and message. New Go
export names are fixed in F-01 and reviewed with the first consumers; the wire
identity and behavioral obligations in this revision are already binding.

Require synthetic PID-reuse/start/boot mismatch cases and real child-process
socket tests, UID mismatch, zombie/dead process, stale/unknown listener behavior,
malformed and oversized frames, EOF/short writes and cancellation. Mock process
control must intercept effects and reject PID or process-group values <= 1;
process-death tests target only children created by that test.

## Session and grant-ledger contract

The current state owners are
[`native_session.py`](../../../integrations/lifecycle/native_session.py) and
[`grant_ledger.py`](../../../integrations/lifecycle/grant_ledger.py). F-04 supplies
locked load/update/persist operations to a real lifecycle consumer. It does not
port the Coordination journal or independently select retries, claims or custody.

| Durable surface | Current schema / identity | Required compatibility |
| --- | --- | --- |
| Native conversation state | `cairn.native-session/1` | Preserve agent/execution, pending intents, exact request IDs, attempt/result references and all unrelated fields |
| Claude bound grants | `cairn.inbox-recall-grants/1` with `session` | Preserve grant/activation/accounting state and initialized sentinel |
| Claude initialized marker | `cairn.inbox-recall-initialized/1` with `session` | Missing ledger after initialization refuses; never refund by recreating it |
| Codex budget and marker | `cairn.codex-memory-grants/1` with `session_id` | Preserve active/pending turn budgets, reservations, emitted-byte counts and initialized marker |

`GrantLedger` uses `<session>.lock`, `<session>.inbox-recall.json`,
`<session>.memory-budget.json` and the corresponding `.initialized` paths.
`open_session` obtains its location and lock from the host. The fixture inventory
must include those host-supplied lock and persistence consumers before the port.
Preserve unknown/extension fields when updating only a portion of a record.

Both Python-to-Go and Go-to-Python state roundtrips are required. Preserve the
64-entry recent-grant and decision policies, 4096 folded-token bound, fold domain,
exact wake parsing and session/turn/task matching. Persist grants and activation
choices before delivery. Wrong identity, malformed state or a conflicting
initialized marker must not become an empty successful ledger. F-04 owns these
operations; J-05 later integrates their use with turn/custody policy.

## Memory hook and local API contract

The reference is [`memory.py`](../../../integrations/lifecycle/memory.py),
[`inbox_recall.py`](../../../integrations/lifecycle/inbox_recall.py) and
[`localapi.Client`](../../../localapi/client.go). `NewClient(socket, tokenFile)`,
`Call`, `WithDiagnostics` and `ForAgentSession` exist today. F-02/F-03 replace CLI
subprocess hops with those authenticated local client operations. They preserve
request IDs, typed refusals, scope, session binding and store-attributed fields.

Compare the exact hook stdout, stderr, exit status, outbound request shape and
persisted state, including these control signals:

| Trigger | Required result |
| --- | --- |
| Required-context refusal on `UserPromptSubmit` | Existing refusal diagnostic on stderr; exit 2 |
| Required-context refusal on Codex `SessionStart` | Existing `continue: false` / `stopReason` JSON on stdout; exit 0 |
| Optional recall unavailable or timed out without a required gate | Bounded degraded diagnostics; exit 0 |
| Unsupported operation or invalid admission | Existing per-entrypoint behavior; no new successful capability |
| Capture selection/save failure | Existing failure evidence and retryability; no false saved status or advanced capture marker |

For each harness enumerate the actual supported operations from its current
entrypoints and tests. Listing Claude, Codex, Agy, OpenCode and Hermes does not
make every event valid on every harness. Release's R-01 matrix independently
checks these cells and installed stream behavior. The abbreviated command sketch
in the architecture proposal does not define additional current grammar.

Preserve selected-content boundaries: owner dialogue excludes injected context,
tools, reasoning and automatic wakes. Keep transcript snapshots, duplicate and
CAS identity, compaction/stop coalescing, retry markers, privacy exclusions and
shutdown persistence. Private Council sessions remain excluded.

Recall preserves required whole groups/currentness/conflicts, source hashes and
span extent, byte budgets, receipt credits, pull limits and the enclosing hook
deadline; optional selection cannot restart that deadline. Preserve the schemas
and meanings of `cairn-lifecycle/recall-meter/1`,
`cairn-lifecycle/inbox-recall-meter/1` and `cairn-lifecycle/capture-meter/2`.
Implementation identity necessarily changes for the Go binary: assert the exact
expected binary identity separately rather than erasing the field from a
comparison. No meter comparison may replace store authentication.

## Fixture review and verification

F-01C and F-04C capture synthetic fixtures against the source revision above.
Every fixture records input, configuration/profile path metadata without token
contents, observed streams/exit status, calls, persisted states and ordering.
Declare each nondeterministic field and why normalization is necessary; never
normalize refusal text, request IDs, grant accounting, mutation order or state.
A source discrepancy is retained as a discrepancy before any repair is selected.

| Fixture family | Existing source/tests to inspect | Required failure/success coverage |
| --- | --- | --- |
| F-A: admission/binding/dispatch | `test_hook_admission.py`, `test_lifecycle_bundle.py`, `scripts/test_recall_dispatch.py`, `scripts/test_installed_lifecycle_bundle.py` | Guards, wrong account, ancestor privacy markers, invalid identity/event, disabled staging, missing/changed/incomplete pins, /2 legacy execution and /3 mismatch refusal |
| F-P: process/transport/statefile | `test_unix_peer_transport.py`, `internal/claudechannel/claudechannel_test.go`, `cmd/cairn/locked_state_file_test.go` | Real peer credentials, process/user ownership modes, start/boot mismatch, process death, stale listeners, bounded framing, lock contention and persistence failure |
| F-S: session/ledger | `test_native_session.py`, `test_grant_ledger.py`, production host consumers | Schema/session/turn mismatch, sentinel anti-reset, concurrent writers, deadline contention, folding/accounting boundaries, failed write/sync/rename and killed writers |
| F-R: recall | `scripts/test_claude_lifecycle.py`, `scripts/test_codex_lifecycle.py`, relevant installed and adapter tests | Required and optional responses, direct API attribution/diagnostics, source and budget integrity, bounded failures/deadlines, supported harness cells |
| F-C: capture | The same memory tests and `scripts/test_hermes_lifecycle.py` | Dialogue exclusions, duplicate/CAS identity, failed save/retry, concurrent/appended/truncated transcripts, compact/stop and shutdown boundaries |

Run existing focused suites before the port and retain their exact outcomes.
New state/store implementation also requires `make check` and
`make test-integration`, using disposable PostgreSQL only. Retain independent
process/crash/install checks even where Go in-process fixtures reduce overhead.
No GitHub Actions workflow is introduced. A skipped test is missing evidence.

Reviewers bind each verdict to the specification revision or exact candidate
commit. Item Done requires passing criteria, closed independent findings,
current docs, reviewed integration/push and author worktree/branch cleanup.
HC-03 establishes the integrated software slice and supported fixture matrix.
Installed cutover, burn-in, rollback exercise and measured benefits remain
separate Release/campaign claims.

## Open decisions and handoff

1. Root must allocate the `lifecycle` command registration and any shared
   Makefile edit before F-01 implementation; Foundation supplies the callable
   handler and command/stream fixtures.
2. Review the state-file write/sync error discrepancy before choosing a separate
   repair or changing the preservation baseline. Parser/mode/symlink differences
   discovered by characterization follow the same explicit decision path.
3. Coordination must confirm process-owned versus same-user peer requirements
   for each wake adapter and the host lock paths needed by native-session state.
   This determines consumers of one shared implementation, not parallel helpers.
4. Release must reconcile its independent supported-operation matrix and /2-/3
   staging tests with these fixtures. No production replacement is admitted by
   this setup document.

The selected doctrine packet `pkt-7964ae6ae9c97aef` from validated Pincite release
`6deb1af41d41075b28577bcf2126c9db90d0e7c0` informed the explicit preservation
boundary and separation of characterization from repair. Repository contracts
control these criteria. Implementation parity, crash recovery and installed
compatibility are still proof obligations for their respective items, not
completed evidence from this setup.
