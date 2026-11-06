# Foundation contracts: foundation/v3

Status: acceptance criteria for the authorized hybrid runtime campaign, prepared
against `d3c255a4bb75438e93527eaa1d83da780fd11eea` on 2026-11-06.
Delivery allocation incorporates the overall lead's
[F-04 consumer decision](f04-consumer-decision.md), committed as `32ca633`.
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
| F-01R | CAIRN-331 | Propagate current helper write/sync failures before extraction | F-B1 | F-01C; independent repair criteria review (can precede X-01) |
| F-04C | CAIRN-315 | Existing session and grant-ledger characterization | F-B3 | None for read-only characterization |
| F-01 | CAIRN-316 | Callable admission/binding path and shared primitives with exercised consumers | F-B1 | X-01 / CAIRN-302; accepted F-01C fixtures; integrated F-01R |
| F-04 | CAIRN-317 | Compatible shared state and binding activation through both real hook frontends | F-B3 | F-01; accepted F-04C fixtures |
| F-02 | CAIRN-318 | Direct local API recall | F-B2 | F-01 and F-04 |
| F-03 | CAIRN-319 | Direct local API capture | F-B3 after F-04 | F-01 and F-04 |
| HC-03 | CAIRN-320 | Reviewed, integrated foundation and supported hook matrix | Foundation lead | F-01 through F-04 |

All eight items have CAIRN-301 as their native parent, verified by readback.
Native blocking relations remain unverified: the installed Plane CLI exposes no
relation command and discovery found no callable legacy relation tool. The
predecessors above and the item bodies are mandatory regardless of tracker
rendering. Session/profile assignments and selected execution evidence remain
outside the repository in the campaign cache and Plane.

F-04 does not wait for J-05. It delivers the exercised shared-state/activation
subset through the real memory and coordination frontends. Remaining grant
operations land with their F-02/F-03 consumers and remain HC-03 requirements.
Native-specific exports land with their first real consumer: J-05 delivers
native orchestration and the compiled journal adapter together; HC-04 accepts
native-session transition policy. F-04 and HC-03 do not claim that policy has
migrated. J-02 consumes F-01/F-04 and J-04 consumes the peer contract after F-01.
Release consumes available binding/admission/staging controls in R-02, then the
complete memory and coordination implementations in R-06's cutover qualification.

## Ownership and change control

F-B1 owns the proposed shared state, peer, admission and binding packages and
changes to their existing Go helper consumers. F-B3 owns session/ledger
packages, the F-04 compatibility caller and its narrowly scoped
event/configuration plumbing in `inbox_binding.py`, `memory.py` and
`coordination.py`, then capture. The caller edits require a sequenced
shared-file slot agreed with Coordination; they do not transfer native-session
or journal policy ownership. F-B2 owns recall. The overall lead assigned F-B1
the initial `lifecycle` registration in `cmd/cairn/main.go` and
`cmd/cairn/commands.go` for F-01. Subsequent shared registration edits require
a new slot after Foundation integration. Makefile changes remain outside
Foundation ownership; send target requirements to root. Foundation does not
edit Coordination's journal/watcher implementation or Release's
installers/adapters. There is one writer per file; extraction and consumer
migrations are sequenced.

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
Its production cutover unit includes the watcher and all config-dir bindings and
shared state users; mixed-language fixtures do not authorize concurrent mixed
production executors. `/3` configurations use versioned paths instead of
rewriting `/2` files under cached hooks. R-02 qualifies available F-01/F-04
staging controls; complete hook/watcher drain and rollback qualification waits
for R-06 and its coordination/memory prerequisites.
F-01 validates a callable path; it does not silently replace any live installation.
Legacy dispatch keeps the original event bytes, streams, signals and exit code.

### Binary `/3` validation policy

These are new `/3` rules; `/2` retains its existing Python validation rules.
The manifest and configuration pins retain their current UID/mode checks. The
binary must be a regular file owned by the invoking real UID, executable, and
not group/other writable; set-ID/sticky file bits are refused. A root-owned
source executable must be copied into user-owned staging before it can be a
`/3` pin in a non-root invocation. The normative rule is binary owner UID
equals the invoking real UID. Symlinks in the binary path are refused. Its
containing snapshot directory must be owned by that UID and mode `0700`.

Inspect path components without following symlinks. Ancestors must be owned by
root or the invoking UID and not group/other writable. A root-owned sticky
system temporary ancestor is allowed above the private snapshot directory as a
general validator rule; the private directory and everything below it remain
owner-controlled. Other writable ancestors, including group-writable ones,
refuse. This policy is specific to binary staging and does not silently change
legacy state-lock policy.

Open the pinned file without following a symlink and separately open
`/proc/self/exe`. Require both SHA-256 digests to equal the manifest digest
and require the same device/inode identity. Verify descriptor metadata did not
change while hashing. An identical copy at a different inode is refused;
hard-link aliases of the same validated inode are allowed. Missing/deleted
pins, a replaced-after-start path, a deleted running image, a changed file or
any digest/identity mismatch refuses before token/API/state effects. Fixtures
cover each case, including a root-owned pin, symlink, writable parent,
run-from-another-copy and successful immutable staging. Production pins must
reside in canonical, symlink-free persistent paths, never under
tmpfiles-cleaned temporary directories. Release inventories the managed paths
and consumes these rules in R-02; any policy change requires consumer review
before merge.

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

Root selected a separate correctness repair, F-01R / CAIRN-331, for this
source-level discrepancy:
`LockedStateFile.writeAtomic` declares a new `err` inside the `tmp.Write` `if`
block, while later checks use the outer `err`. Write/sync failures can therefore
be lost by that control flow. The initial finding was static. F-01C characterizes
the path; F-R1 reviews repair criteria
before meaningful injected-I/O regression tests and the narrow correction. The
original failure must propagate and the prior durable state must remain unchanged;
rename must not publish the failed write. Review binds the exact repaired
candidate. This current-helper repair can proceed before X-01 once its own
criteria are accepted; helper extraction remains gated on X-01 and the integrated
repair. Characterization and repair evidence stay separate.

### Legacy state equivalence and serialization

Roundtrip acceptance compares the values accepted by each original reader,
including exact scalar types, rather than requiring one universal byte layout.
The legacy native-session writer uses non-ASCII JSON and a trailing newline;
compact grant saves use non-ASCII compact JSON; activation's local writer uses
ASCII escaping. Whitespace, separators, trailing newline and equivalent Unicode/HTML escapes
are declared file-serialization differences. Object insertion order is preserved
at every level, as are array order and case-sensitive key spelling. Grant and
activation retention consume that order; sorting a Go map is not equivalent.
They are not normalizations of hook streams, request IDs, string contents,
accounting, transition order or persisted references.

Compare JSON objects with case-sensitive keys; preserve unknown/extension
fields except the exact replacements/pruning performed by the original
operation. Preserve every integer accepted by the source reader exactly
(including values above `2^53`), distinguish integer/float/bool values, and
preserve floating values including signed zero. Existing Python duplicate keys
use the last value; case-variant keys remain distinct; replacing a duplicate
retains its original insertion position, and nested objects are replaced
rather than merged. Legacy `NaN`, `Infinity` and `-Infinity` must retain their
source reader/writer behavior: invalid counter fields still refuse, while
accepted opaque values roundtrip with their non-finite class preserved. Go's
standard JSON/float64/struct decoding is insufficient evidence of this
contract; use a source-compatible lossless representation for accepted legacy
values. Source parser/encoder limits remain part of the operation profile: the
observed Python 3.14 default integer digit limit is 4300, environment
configuration can change it, and excessive nesting raises an escaping
`RecursionError`. Capture the active reference profile and test both sides of
its integer/depth limits so Go neither widens nor narrows the accepted domain.
No strict-parser change or silent replacement character is authorized by this
port. A narrower accepted domain requires a separate explicit compatibility
decision before F-04 can be accepted.

Required cross-language fixtures include each writer profile, unknown nested
values, large integers, floats/signed zero, bool versus int, duplicate/case keys,
non-finite values, non-ASCII text, `<>&`, U+2028 and escaped surrogate cases.
The reference writer's own encoding failures remain failures in its profile.
Initialization/sentinel ordering is per consumer: marker-first general saves
and activation differ from the bridge's pristine-ledger-first bootstrap. Keep
those partial states and failure order; atomicity is per replaced file, not an
invented transaction across marker, inbox ledger and Codex budget.

### Mixed-language lock coexistence

Use the same existing inode and pathname in both directions during `/2`/`/3`
coexistence. Ledger locks use `<session>.lock`; native state uses
`sha256(binding + NUL + native_id).json` with suffix replaced by `.lock`.
Native-session locking is immediate/nonblocking and preserves `SESSION_BUSY`;
ledger waiting is capped at 1.5 seconds and its supplied deadline. The ledger
primitive makes its initial nonblocking attempt before testing a contended
wait deadline, as Python does. Existing generic Go callers keep their original
blocking/nonblocking and `Path + ".lock"` behavior.

Existing Python-created `0644`/`0664` locks must not be chmodded, replaced or
rejected merely because the send-journal consumer enables private-file checks
in the generic Go helper. Legacy lifecycle lock opens retain Python's
symlink-following behavior and lock the resolved inode; their original
directory/file checks still apply where the source caller performed them.
Existing Go journal `O_NOFOLLOW` and private-file checks remain unchanged. New
state temporary files retain their source `0600` mode. New lock creation
follows its consumer: append-style Python ledger/memory locks use `0666`
masked by the inherited umask; native-session and existing Go journal locks
use `0600`. A uniform hardening policy needs a separate decision, not an
undocumented side effect of extraction.

Fixtures run a Python process and Go process on the same lock, in both holder/
waiter directions, assert identical inode identity, contention outcome, wait cap,
deadline boundary and later successful update, and kill only test-owned holders.
Include `0644`, `0664`, symlinked locks and the private-file Go journal profile.
Preserve each caller's exact busy/refusal text; statefile extraction must retain
current send-journal strings while lifecycle callers use their own mapped text.

## Shared process and peer contract

The existing sources are
[`internal/claudechannel`](../../../internal/claudechannel/claudechannel.go) and
[`unix_peer_transport.py`](../../../integrations/lifecycle/unix_peer_transport.py).
The proposed `internal/procpeer` owns shared Linux measurement and framing.
F-01 extracts only operations with real first consumers: `claudechannel` uses
identity measurement, its existing liveness policy, bounded server framing and
deadlines; current CLI consumers exercise state-file extraction. Client
connect/credential verification currently exists in Python, not the Go bridge.
It lands in the same shared package only with J-04's first real Go client
consumer, in a Foundation-owned shared-file slot coordinated with J-B2. F-01
must not pre-land unused client APIs. Harness JSON-RPC, wake/cancel meaning and
journal transitions remain with Coordination.

Keep the `ProcessRef` wire identity `pid`, `start`, `boot`, where `start` is the
`/proc/<pid>/stat` start tick value and `boot` is the kernel boot ID. Ancillary
state and parent metadata in Python records must not disappear during roundtrip.
The existing Go bridge server checks parent references, `getppid` and liveness;
it does not currently call `SO_PEERCRED`. Adding server-side peer credentials
would be a separate hardening decision. Python process-owned verification
requires matching peer PID/start/boot and checks UID only when `expected_uid`
was explicitly supplied; the current Claude client supplies none. Same-user
mode requires its selected/effective UID while allowing a different peer PID.
Preserve these individual policies and their refusal messages.

A numeric PID alone never certifies identity. Existing Go `liveProcess` compares
PID/start/boot, while Python `process_alive` also excludes `Z`/`X` states. Keep
both observations in fixtures and preserve each consumer's policy until an
explicitly reviewed change selects otherwise. Preserve the reference distinction
between a process-owned endpoint and a same-user endpoint: the latter can have a
peer PID different from the measured target process. Both require their actual
credential/identity policy before any payload write. Tests cover the individual
harness ownership mode, not an assumed universal peer-PID equality rule.

The consumer-facing operations, as their first consumers land, measure a
reference, check liveness/ancestry, connect/validate a client peer and
read/write bounded frames under the consumer's deadline. Preserve each
consumer's error classification and message. New Go export names are fixed
when each operation lands and are reviewed with its first consumers; the wire
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
The current native host uses `sha256(binding + NUL + native_id).json` and the
sibling `.lock`, with immediate `SESSION_BUSY`. `open_session` itself does not
validate schema/type or impose file size, mode or symlink rules; keep its caller
checks and operation-specific failure category rather than inheriting new generic
Go defaults.
Preserve unknown/extension fields when updating only a portion of a record.

Both Python-to-Go and Go-to-Python state roundtrips are required. Preserve the
64-entry recent-grant and decision policies, 4096 folded-token bound, fold domain,
exact wake parsing and session/turn/task matching. Persist grants and activation
choices before delivery. Wrong identity, malformed state or a conflicting
initialized marker must not become an empty successful ledger. F-04 implements
the shared-state/activation subset; remaining grant operations land with their
F-02/F-03 consumers. All remain required for HC-03. Native-specific operations
land with their real readers/orchestration, with transition-policy acceptance
in J-05 / HC-04. Do not pre-land unused exports to satisfy the table.

### F-04 first production consumer

Root selected the actual `InboxBinding.effective_config` activation prefix in
both `memory._main` and `coordination.main`. F-04 delivers that caller with
`binding.ResolveActivation(ctx, admission.HookInvocation)` and the real shared
ledger/state operations it uses. A command exercised only by tests, a native
mark-only operation without a caller, or a no-op adapter is insufficient.

The private command is `cairn lifecycle binding activation --entrypoint
memory|coordination --config ABS --config-sha256 SHA256`. The parent retains
its checked configuration bytes and their digest, and passes the original
bounded hook-event bytes unchanged on stdin (at most 1 MiB). It does not
serialize a parsed event again. Go checks the selected `/3` binding and the
expected current config digest before mutation; changed configuration refuses.
Full downstream config-pin validation follows the effective decision as
specified below, retaining the disabled-staging exception.
`--binding-sha256 SHA256` similarly binds the checked receipt's exact binding
snapshot, so the child cannot silently observe a different enabled decision.
This digest binds the parent's observation, not authority or a new config
selection. The parent's continued handler uses that same checked snapshot.

This operation ports admission, one enabled snapshot and one activation
decision, including the source marker-first persistence order. It neither
allocates/refunds grants nor claims a turn, calls the store or transitions a
journal. It runs before the parent's ordinary memory/coordination session lock;
the child alone holds the existing activation lock through durable persistence.
No parent-held-lock handoff or duplicate Python activation is accepted.

Success is exactly one compact `{"enabled":true}\n` or `{"enabled":false}\n`
on stdout, empty stderr and exit 0. A private failure has empty stdout, exit 1
and one JSON error frame of at most 4096 bytes on stderr: fixed `class`, fixed
`reason` and optional numeric `errno`. The reviewed protocol enumerates those
values and their source exception counterparts; no paths, events, credentials
or free-form exception text cross this frame. The parent maps them through its
existing entrypoint-specific public exception handling, preserving streams,
phase/class diagnostics and exits. Launch, timeout, malformed/oversized output,
unknown class/reason, panic and persistence failures must never become
`enabled:false`. Once the child starts, there is no Python activation fallback.
Unexpected failures retain their source escaping/caught distinction; a child
panic never accidentally returns the host-blocking exit 2.

### Independently pinned compatibility caller

The temporary `/3`-aware Python caller has a separate versioned
`cairn.lifecycle-compatibility/1` receipt. `/3` authenticates Go only; `/2`
authenticates its unchanged legacy Python bundle only. Neither substitutes for
the compatibility receipt. Foundation owns receipt validation, the loader and
callable protocol; Release owns immutable staging and hook activation. Their
concrete receipt/loader agreement and independent delta review precede use.

The concrete receipt fields agreed with Release are:

| Field | Value / validation |
| --- | --- |
| `schema` | Exactly `cairn.lifecycle-compatibility/1` |
| `source_revision` | Source provenance; never authority or proof of executed bytes |
| `snapshot_root` | Canonical absolute private persistent versioned directory |
| `members` | Exact complete basename-to-`{sha256, bytes}` map, including aliases; lowercase 64-hex SHA-256 and exact raw byte count |
| `bootstrap`, `loader` | Checked member names: proposed `lifecycle_compatibility.py` and retained/adapted `lifecycle_bundle.py` |
| `roles` | Exactly memory, coordination, inbox_recall and inbox_binding, selecting their corresponding `.py` members; memory also supplies the retained engine |
| `aliases` | Public basename to canonical member name; alias bytes equal that member |
| `binary` | Exact absolute Go `path` and `sha256`, equal to `/3` |
| `binding` | Exact absolute manifest `path`, `sha256` and schema `cairn.inbox-recall-binding/3` |
| `memory_config`, `coordination_configs` | The `/3` config descriptors, with identical ordered coordinator membership; selected frontend path must be one of them |

Retain the complete local closure, including all 17 current lifecycle members
plus the new bootstrap and declared aliases; do not authenticate only edited
files. Reject missing/extra members or roles, duplicate/noncanonical names,
escaped aliases and unknown schemas. The new receipt is at most 1 MiB and each
member at most 2 MiB, matching the existing bundle's member limit. Hash exact
bytes, not reserialized JSON. Descriptor agreement is always checked; opening
and hashing final downstream configs follows the disabled-staging exception
below. Unknown, missing, mixed, changed or incompatible enabled selections
refuse before effects.

The receipt's exact SHA-256 and canonical path are pinned **outside the
receipt**, in the owner-controlled hook launch configuration. The proposed
trust entry is a fixed, independently reviewed stdlib-only verifier under an
approved absolute Python interpreter in isolated mode (`-I -S`). The hook's
actual argv contains its reviewed `-c` literal and independently recorded
verifier digest, receipt path/digest, selected role/alias and config path.
The verifier itself is not the receipt's bootstrap. It verifies the
external receipt digest, checks bootstrap/loader bytes, then executes those
checked byte snapshots. The checked loader validates the entire closure before
executing any member and uses only its process-local checked source cache.
Aliases must match their selected role's bytes. No unchecked adjacent import,
self-described trust root, hash-then-reopen execution or user import path is
accepted. Release agrees the precise immutable launch text/selection before
consumption; it is a new installation artifact, not a current installer feature.
The approved interpreter and standard library belong to Release's offline
restoration closure. Owner-controlled launch configuration is the explicit
trust root; its selection changes require install/readback, not self-authentication.

The hash graph is acyclic: configs name only their versioned binding path;
the `/3` manifest hashes Go and config bytes; the receipt hashes that manifest,
closure and the same Go/config selections; the external hook pins the receipt.
Neither configs nor the receipt contain a receipt digest. Changing enabled or
descriptors publishes a new immutable binding/receipt/launch selection.

Use bounded regular-file reads with owner/mode, symlink-free path and stable
descriptor checks. Snapshot paths are canonical, persistent, owned by the real
UID, private and versioned. Reuse the `/3` ancestor policy for these new
artifacts; do not impose it on existing `/2` state paths. No live snapshot or
`/2` descriptor is edited in place. Source staging fixtures use disposable
homes; R-02 later exercises the real installer against this same contract.

Enabled callbacks must continue through the complete retained Python handler.
Current `load_bridge` / `validate` require `/2` and bridge/engine descriptors;
the compatibility loader explicitly loads those retained roles from its
independently authenticated closure. It must not synthesize a fake `/2`
descriptor to authorize Go, reuse an incompatible downstream `/2` validator,
or fall back after Go has started. Disabled callbacks preserve the source
staging/skip behavior without inspecting disabled pins being replaced.

The disabled exception applies only to final downstream configuration byte
hash/profile checks. Trusted launch, receipt, every closure/alias/role member,
Go image and the `/3` binding bytes always authenticate. Read the current
frontend config once with its source owner/file policy, verify its selected
path and pass its actual digest to Go even if its final staged descriptor hash
does not yet match. When the effective activation is false, do not open/hash
other final configs merely to stage them. Reserved-wake activation may read
only the source-required owner-controlled `memory_config.path` to locate the
existing ledger without checking its final SHA; preserve its source state-dir
type checks and failure categories. When effective activation is true,
including a cached true decision under a now-disabled snapshot, all final
config hashes and profile checks apply before retained-handler effects.

Inject one authenticated invocation context through the checked bundle,
bridge and engine roles. Downstream `binding`, `validate`, `coordinates_wake`
and engine selection reuse checked objects/bytes, not pathname reloads or
another activation. This is an explicit compatibility-loader path, not a
second Release implementation of Foundation validation.

F-04 acceptance runs both actual public frontends, enabled and disabled, in
process fixtures using the admitted compatibility caller. Prove one Go
activation, no duplicate Python mutation, retained-handler continuation,
unchanged outer streams/exits, the same config snapshot and raw event,
config-race refusal, tampered/missing receipt/bootstrap/member/alias refusal,
mixed Go/config refusal and unchanged `/2` coexistence. Include a failure after
activation persistence to prove no fallback/replay. Isolated Go unit tests or
activation-only success cannot satisfy this gate. R-02 need not precede these
source fixtures, and F-04 does not acquire a reverse dependency on J-05.

## Native-session journal adapter

Root's X-01 boundary decision requires one named adapter for the callbacks in
`native_session.Host`. The proposed Foundation-owned consumer interface is
`session.JournalBoundary` in `internal/lifecycle/session`. Coordination supplies
`journal.SessionJournalAdapter`; the session package does not import journal.
The following is the binding specification. The compiled interface and adapter
land together with J-05's real native orchestration consumer, using J-02/J-03's
single journal implementation; they are not unused F-04/J-03 deliverables.
The proposed callable boundary is:

```go
// Proposed API, to implement after X-01; State preserves the complete /1 object.
type FlushScope string // exactly "attempt" or "all"
type JournalBoundary interface {
    Flush(ctx context.Context, state *State, scope FlushScope) (settled bool, err error)
    Committed(ctx context.Context, state *State) (bool, error)
    TurnEndDiagnostic(ctx context.Context, state *State) error
}
```

`State` is the session-owned lossless native-session object. The real consumer
holds the existing session lock. `Flush` delegates to the single journal owner;
it does not recreate retry, completion or reply execution in Foundation.
It may seed/prune `inbox_journals` as the current `session_journals` /
`flush_inbox_intents` path does. The enclosing session transition persists those
changes at its existing boundaries. It must not drop other state fields.

Both flush scopes replay **all** conversation-owned journals, including
reconciled attempts. Scope changes the returned settlement gate, not which
journals are visited. `Flush("attempt")` is false while the active attempt has
an uncertain/running command; `Flush("all")` is false while any owned journal
does. An unused journal or a committed request still awaiting a possible reply
does not alone make a flush uncertain. Preserve completion-before-response
execution and the exact reply reference. Retain the existing `finish_reply`
recovery exception: when `finish_reply` exists, response is absent, completion
is committed and `finish_reply_ref` has no usable result, the journal owner
retries that exact completion to recover its result before materializing the
response. This does not permit arbitrary settled/refused replay; ordinary
automatic replay remains pending-only. A refusal is a journal fact, not
permission to send a new command. `Committed` reads only whether the active
completion/ack slot committed: missing state is false; malformed/unreadable
state preserves the current error policy. It sends no command and does not
stand in for reply completion. `TurnEndDiagnostic` retains the existing
bounded diagnostic/state update and never completes or acknowledges a
delivery.

The Go error result has a source-level taxonomy. Within the existing
`Flush`/`end_turn` try region only, settlement catches the counterparts of the
configured coordination refusal, `OSError`, `ValueError`, `KeyError` and
`TypeError`; represent these as classified settlement errors. Unexpected
errors corresponding to an escaping `AttributeError` or other unhandled source
error propagate without marking pending or making the second settlement save.
Errors before or after that try region also retain their source phase and
escaping behavior, even if their class is caught inside the region. A
non-object journal is one explicit escaping-error fixture. Malformed JSON and
unreadable journal files retain their respective caught value/I/O
classification. Do not collapse every Go error into a caught error.

Deadline/transport uncertainty that `send_intent` classifies as a pending slot
returns `(false, nil)` from `Flush`, retaining that slot's diagnostic. A context
deadline must not become a new caught exception solely because Go represents it
as an error. Check both `strict` values: nonexception unsettled `strict=false`
preserves the prior diagnostic, including an initial `pending` value; caught
errors mark `turn_ended_pending` for either value; escaping errors leave only the
first evidence save. External cancellation follows J-05's separate cancellation
contract. Fixtures cover every source exception class, non-object journals,
malformed/unreadable journals, API timeout, context deadline and both strict
settings. Flush/turn-end stderr remains owned by the journal/session caller and
is compared, not discarded by the adapter.

Failure-order fixtures must exercise the current `NativeSession.settle`,
`end_turn` and `retire` boundaries: persist ended-turn evidence before flush;
flush before reconciliation; mark settlement pending after classified caught
errors inside the `Flush`/`end_turn` try region and, for nonexception
uncertainty, only when `strict` is true. A non-strict unsettled call preserves
the prior diagnostic. Persist logical ending before API work; refuse
retirement while an all-journal flush is uncertain; leave/reconcile before
marking retired. F-04 supplies the storage/API seam and an exercised storage
consumer without waiting for J-05. Coordination later owns the full
orchestration. Scripted adapters are test-only; a missing production adapter
must use the retained legacy path or explicit unavailability, never a no-op
successful flush.

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
| Successful empty/degraded recall, including a handled optional-body failure or labelled lexical fallback | Existing success payload/diagnostics; exit 0 |
| Search timeout, missing socket or another caught optional failure escaping to `_main` | Empty stdout, existing fixed phase/class diagnostic plus the existing lifecycle failure line on stderr; exit 1 |
| Required-context refusal on `UserPromptSubmit` | Existing fixed phase/class diagnostic and refusal line on stderr; exit 2 |
| Required-context refusal on Codex `SessionStart` | Existing diagnostic on stderr and exact `continue: false` / `stopReason` JSON on stdout; exit 0 |
| Required-context refusal on Claude `SessionStart` | Characterized ordinary caught-error output on stderr; exit 1, without the Codex stop envelope |
| Unsupported operation or invalid admission | Existing per-entrypoint behavior; no new successful capability |
| Capture selection/save failure | Existing failure evidence and retryability; no false saved status or advanced capture marker |
| Unexpected unhandled failure/panic at a supported Go memory hook boundary | Redacted fixed unexpected-error diagnostic, empty stdout and exit 1; never accidentally exit 2 |

Caught-error fixtures preserve both stderr lines, phase and fixed error-class
labels as actually emitted, including classification before any
required-context conversion. The current Python unexpected-error result is
traceback plus exit 1. For that case alone the declared cross-runtime
diagnostic difference replaces the interpreter traceback with exactly `Cairn
lifecycle diagnostic: phase=<current fixed phase> error_class=UnexpectedError`
followed by a newline. There are no payloads, paths or stack dumps. Every
supported Go memory hook entrypoint must recover an unexpected panic to this
exit-1 result. Coordination and recall dispatch retain their own fixed
error-family mappings and intentional refusal codes; the memory-specific line
is not imposed on them. Their owned contracts specify the per-family
unexpected error line, while accidental panic exit 2 is excluded at each
frontend. Intentional required refusal still exits 2. Fixtures cover
unexpected failures on prompt, start, stop and tool boundaries so an
unrecovered Go panic cannot acquire host-blocking meaning. This does not
normalize any caught refusal or ordinary diagnostic.

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

The following existing script modules form a minimum oracle inventory:
`test_hook_error_diagnostics`, `test_recall_error_diagnostics`,
`test_claude_inbox_recall`, `test_inbox_recall_bridge`, `test_claude_channel`,
`test_codex_queue`, `test_hermes_queue`, `test_opencode_queue`,
`test_capture_concurrency`, `test_capture_observation`, `test_codex_turn_budget`,
`test_inbox_ledger_compaction`, `test_recall_task_grant`, `test_startup_recall`,
`test_hook_invocation_trace`, `test_recall_failure_observation`,
`test_selector_process_observation`, `test_recall_observation` and
`test_inbox_recall_observation`. Frozen `test_trial_*` modules are not edited.
The inclusion rule additionally covers every non-trial script test that loads
or wraps `memory.py`, `inbox_recall.py`, `grant_ledger.py`, `native_session.py`,
`hook_admission.py` or `unix_peer_transport.py`, including transitively loaded
fixtures. A feature author enumerates those source-derived modules and records
an explicit reason for any exclusion or Release-owned installed check; the
minimum list does not establish completeness. The per-module baseline command is
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p '<module>.py'`;
record its actual `Ran`, skip and exit results before its behavior is ported.
Existing Foundation baseline captures cover subsets, not an assertion that this
entire inventory already passed. Add Go/differential counterparts to the relevant
item without deleting these independent reference assertions prematurely.

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

1. F-B1 has the root-assigned first CLI registration slot; the proposed
   `session.JournalBoundary` needs Coordination agreement and both independent
   reviews. Foundation owns no Makefile edit in this wave.
2. F-01R / CAIRN-331 is selected; its criteria/regressions and exact corrected
   candidate still need independent review. Parser/mode/symlink and process-state
   differences discovered by characterization require their own explicit decision
   before changing the preservation baseline.
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
