# Finite watcher-unit census preflight

`CLI.InspectWatchUnitCensus` reads an explicitly pinned observation catalog and
a proposed set of existing `WatchBindings`, session paths and callback paths.
It returns selected membership, relationships, fingerprints and blockers.
It does not construct a drain receipt, call the API, acquire an application
lock, settle work, change grants, signal processes or publish configuration.

The initial dependency is landed
`62416e99bda1e7c6e5559a9968f7766708974983`. This cut adds only census code,
tests, a process driver and this document. Control adoption and qualification
of the composed image remain separate.

## Observation universe and closure

The pinned `cairn.watch-unit-catalog/1` catalog names a private fixture root,
its exhaustive selected config-directory root, its callback-reference directory,
all `WatchBindings`, native-session pins, callback pins, binding and client-image
pins, and credential selectors. Actual directory entries must agree with the
catalog. The inspector reads the selected config copies and immutable binding,
coordination and memory descriptors, validates their existing profile agreement,
and checks actual referenced bytes. Credential selectors receive metadata
inspection only. Unknown roots, unlisted sessions/callbacks, changed pins,
unsafe path aliases and missing coverage refuse.

Shared config directories, state/ledger directories, account paths and selected
runtime roots form edges between known users. Closure includes every connected
selected user. A proposed half switch refuses. Independent units remain
separate. Retained callback configurations also contribute their pinned old
binding, memory, runtime and associated users. Mixed runtime identities refuse.
Directory membership and selected bytes are read back; the composite observation
is non-atomic and has a per-fact read-to-use window.
Enabled/granted/running users in a known disjoint unit do not become selected
members. Their resources and references are still inspected to establish that
they are separate. Selected enabled bindings and positive grants block readiness.

The supported successful scope is **`disposable-fixture`**. `FixtureReady`
means the explicitly declared finite inventory passed. The result always says
`ExternalCoverage: unknown` and `NativeRefresh: unobserved`; it is not whole-unit
production readiness. `external-unknown` scope refuses. A catalog is selected
test input, not proof that no live user exists elsewhere. Global installed
account/session discovery, authoritative native join/refresh readback, store
drain and closure of unknown external users remain unavailable here.
The finite fixture explicitly names account directories and each session's
workspace. Their actual hook/config layers must be empty unless the selected Claude
fixture input described below supplies exact emitted settings and native
readback. Other nonempty layers, missing account/workspace identity or
unobserved native settings refuse. No input establishes global absence or an
authenticated native connection.

## Processes and retained obligations

Generic fixture callback records bind executable/entrypoint/config/binding pins
to actual PID, boot ID, start time, parent and process group. PID, parent and
group must exceed 1. Live callbacks must match `/proc` identity, executable and
argv. A dead PID alone is unknown. The finite fixture's owner wait receipt must
match the exact callback and still-observable reaper identity; each known delayed
child needs its own reference. This is process accounting, not proof of an active
MCP/channel connection or a supported native refresh.

The fixture callback captures actual selection A bytes and can remain running
after a new immutable disabled B is published. Census retains A's paths and
identity, the old issued reference and any positive ledger grant, then refuses.
Publishing B does not refresh A or clear the grant. Only the fixture owner joins
its children; the inspector performs no cleanup or settlement. Old fixture
references and grants remain on disk under retained scratch custody.

Non-null retained `AttemptKeys` fields produce named `DrainRequirements`, each
with its field name and the exact observed session path, native/session identity
and raw state SHA-256 pin. The raw session fields remain untouched. These
requirements belong only to selected members of the connected unit; unrelated
units do not acquire a selected drain obligation. They are not permanent
blockers merely because the keys remain present.

Standalone inspection cannot authenticate this correspondence. It returns
`FixtureReady: false` and an error naming each unresolved field and session,
while preserving every other blocker. A census report does not authorize a
switch. The combined private `unit-control/2` path can satisfy a requirement
only with the existing authenticated D2 check for the same session in its own
drain receipt. Missing journals, unknown attempts, mismatched claims or session
identities still refuse. No caller Boolean or serialized report clears a
requirement; the public `/2` input accepts no authenticated-result field.

The receipt joins by exact path, native identity, agent/execution IDs, state pin
and retained field name. It repeats that join against the final D2 read before
publication. A changed or unobservable fact invalidates the receipt. This adds
no API authority, settlement, grant/journal mutation, owner exception or lock;
the existing final-read-to-rename interval remains non-atomic.

## Focused validation

Use a clean committed checkout and a new evidence directory:

```sh
mise run test:unit -- ./internal/lifecycle/coordination \
  -run '^Test(WatchCensus|SharedWatch)' -race -count=1 -v
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/check_r06_unit_census.py \
  --evidence "$HOME/.cache/r06-census-run"
mise run check
mise run host:exec -- python3 -B scripts/verify_docs.py \
  --tier 1,2,3,4 --json
```

Unit cases use real files: two-binding/disjoint units, shared ledger/runtime
union, omission, stale pins, aliases, unknown coverage and retained grants.
The process driver builds a frozen race-enabled helper and starts only owned
generic callbacks. It checks running/delayed, joined/unjoined, omitted callback,
invalid group/start identity and cached-A/disabled-B cases. `strace` records actual
inspection opens and checks that no credential is opened, protected file is
opened for writing, or protected path is renamed, removed or locked.
`flock`, `kill` and `creat` syscalls are rejected anywhere in the inspector
trace before path filtering; descriptor-only lock calls are covered. Receipt
counts come from those checked lines. Protected
file bytes, membership and modes are compared before and after inspection.
Inactive private process entry points skip outside their explicit driver;
required unit/process selectors run without skips.
The process driver also observes a real callback in a disjoint enabled unit
remaining alive while the selected two-binding inventory passes. Retained native
obligation fields defer to the existing authenticated drain correspondence; the
census does not classify an unverified historical field as active or delete it.

`scripts/check_r06_census_d2.py` uses the existing disposable API/PostgreSQL
fixtures to complete and close real matching deliveries while retaining their
policy and journal fields. Its seven cases cover combined success, mismatched
policy claim, absent journal, unknown attempt, wrong session and final policy
or actual claim invalidation. Standalone inspection remains not ready in every
case. Source's existing `completion_scope` is observed for the valid retained
policy and used for the actual completion; Source has no census/drain-result
counterpart, so the fixture does not invent one. Unit tests separately check
named read-only requirements, identity/pin/field refusal, unrelated blockers and
rejection of caller-supplied authentication fields.

Evidence records source/tree, dependencies, toolchain/native executable and
helper image hashes, argv, actual output/exits, reached assertions and owned
process joins. It excludes synthetic token contents and retains failed attempts.
Normal host `HOME`, tools and subscription state stay unchanged. No native
client, model, live API/store, production transition or full suite is invoked.
Full R06/HC-05, supported refresh, whole-unit D6, final dependency/control adoption
and production R07 remain held.


## Selected Claude hook observations

An optional `HookSelections` catalog entry pins a
`cairn.watch-claude-hooks/1` descriptor. It selects one catalogued configuration
and native-session record, account and workspace, an actual immutable R-04 hook
generation, its settings file, a pinned `cairn.watch-claude-readback/1` observation,
and an exhaustive list of account/workspace files. The inspector enumerates
both actual trees and rejects omissions, extra pins, aliases and changed bytes.
Every affected config/session needs its own readback; a caller completeness
Boolean is not accepted. Other account/project settings containing hooks refuse
as an unsupported layer, including extra Cairn hooks. Ancestor `.claude`
layers inside the finite universe also refuse; the workspace is not assumed to
be the repository root. This selected-input seam is unavailable for
`external-unknown` scope.

This bounded cell supports Claude `flagSettings` and the eleven emitted hook
rows. It verifies the real generation manifest and lifecycle selection, selected
role configurations and preserved unrelated settings. A selected `/3` uses
canonical content-addressed launchers, compatibility receipts and their retained
source closure. A genuine retained `/2` uses its complete installed engine and
bridge bundles and `retained-argv.json`; it does not acquire a `/3` receipt.
The native
observation must match the exact effective hooks and listing. Both prompt roles
must identify successful actual launcher/program executions with the exact
literal argv. Stale cached A against selected B, missing readback, changed
receipt/config/program/shell, mixed units and unsupported harnesses/layers refuse.
The inspector does not execute hooks or change settings.

The retained hook producer's `retained_python` record must agree with the
selected binding, bundle manifest, engine and bridge descriptors, and retained
argv pin. Every bundle member must belong to that same verified generation.
Both role rows retain the original direct Python command, including its exact
interpreter flags, entrypoint, role configuration and coordination `hook` tail.
The recorded command must equal the shell rendering of that argv. Prompt
execution observations use the direct interpreter frame, without adding a
compatibility launcher's `--` separator. Missing executions, changed program
hashes or argv, nonzero exits, and PID or role conflicts still refuse.

`TestWatchCensusRetainedPythonRow` checks literal-frame acceptance and refusals
with selected retained source files. `test_r06_retained_hook_census` checks the
collector's distinct direct-Python and checked-launcher frames. Its material
case stages the complete real `/2` bundle and hook producer output, creates
state through the Go producer with synthetic registration, and runs the actual
Go inspector with synthetic readback input. It checks producer-pin, unknown
proof-field and observed-argv refusals, complete unchanged snapshots and the
existing syscall guards. With `CAIRN_RETAINED_CENSUS_EVIDENCE` selected, it
retains the actual compiled census, control and stager images, build metadata,
source/module pins and interpreter/shell/observer pins before staging or
inspection begins. These are unit checks; they do not establish a real
native `/2` observation, authenticated
drain, or a complete transition from Python to Go. The complete consumed setup
must provide its own matching session, real API history and issued journals.

`HookObservations` reports only the selected fixture observations that agree.
`ExternalCoverage` remains `unknown` and `NativeRefresh` remains `unobserved`.
The records are supplied selected facts, not authenticated native ownership.
A parent PID, manifest or successful prompt does not identify an active MCP
connection. Codex, Agy, OpenCode and Hermes are not qualified by this cell.

The new `scripts/check_r06_native_hook_census.py` cell uses the existing R-02
installer/R-04 renderer, synthetic local provider/API, the frozen selected Claude
executable, and real Go census inspection. It observes cached A after B is
published and a process restart with the same conversation and selected B,
then injects bounded file/readback refusals. It does not replace the existing
eleven-case R-04 collector or qualify a complete unit transition. Run it only
through the existing hybrid wrapper, with fresh private Go images and selected
hashes; no native executable invocation occurs on the host:

The selected Go memory hooks use the existing R-04 disposable Unix HTTP recall
fixture and its readable synthetic token. The token retains the `.token` suffix
used by the census snapshot and syscall guards: the actual hook may read it,
while inspection records its metadata without opening credential content. SDK
request evidence records operation names and authorization success only. Startup,
prompt and shutdown hook failures still fail the cell.

Native session identity uses the shared observed-state reader. All present
`registration.native_session_id`, `agent.native_session_id` and retained legacy
outer identities must agree. Missing, malformed or conflicting identities
refuse; a legacy outer identity cannot override a current producer identity.
These fields establish correspondence only, and do not authenticate a caller.
The collector preserves the exact newly produced state and its
key sets before checking the selected input; prior failures that preceded this
capture do not supply that evidence. Unit fixtures use the existing coordination
state producer rather than fabricate an outer identity.

The disposable collector creates its config root and selected leaf separately
at `0700`, and verifies their owner, directory type, canonical path and exact
mode on both catalog constructions. It records directory UID, mode, device and
inode before inspection. Existing unsafe directories are refused without chmod
or alias normalization; the Go path guards and refusal reasons remain unchanged.

Later catalog construction reuses the selected config only when its canonical
owned regular file remains `0400` and byte-identical to the emitted config.
Creation uses exclusive no-follow opening; existing files and dangling symlinks
are never overwritten or chmodded for writing. Local setup proofs replace only
the native client boundary and remain labelled `SETUP/STUB ONLY`; their results
do not qualify native behavior.

The disposable fixture also provisions its two known per-session capture locks as
owned regular `0600` files before shutdown capture, and verifies it on resume.
The callback's known `0664` lock creation is narrowed through an inode-checked
descriptor to `0600`; unexpected type, owner, alias or mode is refused. This keeps
the fixture's selected state private; it does not change capture or census
product behavior or qualify ordinary installations.

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/test_hybrid_runtime_operations.py --timeout 480 \
  --receipt "$R06_EVIDENCE/isolation.json" \
  --ro-bind "$R06_CLAUDE:/opt/r04-native/claude" \
  --ro-bind "$R06_CENSUS_IMAGE:/opt/r06-hook-census/census-tests" \
  --ro-bind "$R06_LIFECYCLE_IMAGE:/opt/r06-hook-census/lifecycle" \
  --ro-bind "$R06_STAGER_IMAGE:/opt/r06-hook-census/stager" \
  -- python3 -B scripts/check_r06_native_hook_census.py \
  --claude /opt/r04-native/claude --sha256 "$R06_CLAUDE_SHA256" \
  --census-image /opt/r06-hook-census/census-tests \
  --lifecycle /opt/r06-hook-census/lifecycle \
  --stager /opt/r06-hook-census/stager --archive-stdout
```

Retain the decoded synthetic evidence archive outside Git, including failed
attempts. The final integrated image, full-unit barrier, supported connection
refresh, grants, HC-05 and production acceptance remain separate.
