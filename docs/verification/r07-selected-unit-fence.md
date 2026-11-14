# Selected-unit stopped observations

This reader captures a process-local receipt for an explicitly selected local
unit set. It issues no stop, mask, start, restart, cutover or data write.
Its coverage is `selected-units-only`; everything outside the declared
selection remains `unknown`. It is not a host-wide writer fence.

The engineering baseline is `8631c6f12b8b484d608d959683ed5a6767a2db66`.
Owner answer `63dffa76-5252-4e56-8c39-9259f75d0046` version 1 identifies the
previous gaps: semantic S-05 covers eight closed fixture definitions, store
restore fencing does not stop processes, and the existing watch census covers
disposable fixtures. This reader does not reinterpret those earlier contracts.

## Typed boundary

The implementation is in
[shared_unit_fence.go](../../internal/lifecycle/coordination/shared_unit_fence.go).
The agreed fence-side interface is:

```go
func CaptureSelectedUnitFence(ctx context.Context, scope SelectedUnitFenceScope,
    db *pgx.Conn) (*SelectedUnitFenceReceipt, error)

func (r *SelectedUnitFenceReceipt) Recheck(ctx context.Context,
    expected SelectedUnitFenceScope) error

func (r *SelectedUnitFenceReceipt) Facts() map[string]string
```

The scope contains the local UID, exact declared units, executable path/hash
pins, optional complete command-line hashes, database name/system identifier,
and an observation window of at most 30 seconds. Capture copies and normalizes
the selection. It binds actual machine, boot, process generation, tool, unit,
cgroup, process and database observations to that selection.

The receipt has private origin and scope. A zero value, copied handle,
serialized status, `stopped=true`, `complete`, `Inspected` or returned facts
cannot construct one. JSON marshal/unmarshal refuse a receipt handoff.
Construction binds the concrete reader; it accepts no generic fact-map adapter.
`Facts()` returns a copy for selected evidence, without command lines,
credentials, DSNs or unit source bodies. It grants no operational authority.

Every consumer must supply its expected scope to a fresh `Recheck` immediately
before each swap or reverse. Recheck reads the producer twice, compares both
observations with the captured facts, verifies the original process generation,
and enforces the deadline. A failed recheck permanently invalidates that
receipt. Returning to an earlier state cannot revive it; a new capture is
required for a new stopped window.

## Observations and refusals

The production path uses the installed root-owned `/usr/bin/systemctl`, pins
its bytes/identity and checks it around each read-only query. Queries select the
current UID's local user-manager bus. It enumerates `cairn-wake-*` units as
well as the declared units and their reported activation triggers. New
transient membership, missing properties and unreadable manager state refuse.

Selected services must be inactive/dead with `MainPID=0`, `Restart=no`, a
masked or runtime-masked unit file, and no pending daemon reload. Reported
activation triggers must also be inactive/dead and masked. Effective unit
properties, fragment/drop-in identities, invocation and state-generation
metadata remain pinned. A manager-reported `/dev/null` masked definition is
recorded as that mask; it does not attest to an earlier unmasked definition.

Cgroup coverage requires an actual readable, canonical cgroup-v2 directory.
Every observed subtree must have `populated 0` and an empty `cgroup.procs`;
directory identities and contents are retained as fingerprints. Missing paths,
files, permissions or population fields are unknown, never empty. In particular,
an inactive unit whose manager has removed its cgroup is currently unsupported.
Supporting that case requires a separate explicit manager absence contract;
this implementation does not infer one.

The local `/proc` reader considers the selected UID's executable path/inode
identities and optional whole-command hashes. It rereads generation, UID and command
metadata around each selected process observation. A matching process, zombie,
permission error, disappearing process during inspection or changed generation
refuses. Private command bytes are compared in process memory and are not
returned as evidence. Other executable identities and other hosts remain outside
the declared coverage. This does not qualify mapped shared-library bytes or
resolve the separate ABI/runtime-adoption holds.

The database input must already be an authenticated local Unix `*pgx.Conn`.
It must be idle and exclusively borrowed for observation; an existing caller
transaction refuses without being rolled back. The caller must not use this
connection concurrently. Observation cleanup has its own bounded rollback
context, including when the read context expires.
The reader starts a read-only transaction and verifies actual cluster,
database, role and `public.semantic_job` identities. It requires superuser or
`pg_read_all_stats` visibility and refuses every other backend in that database,
including idle connections. It does not identify authenticated writers from
`application_name`, query text, a quiet queue or a caller principal flag.
Missing relation/identity/visibility and a failed observation rollback refuse.
No database credentials or connection strings are retained in `Facts()`.

## Consumer prerequisite and limits

The current unit-bootstrap frontend has an authenticated local Cairn API
profile but no authenticated process-local PostgreSQL connection. Its
integration therefore remains unsupported until Release supplies an explicit
operator-side connection-selection and ownership contract under an exact lease.
A nil connection fails before service/process queries. JSON, a DSN presented as
authority, borrowed credentials and a fake database adapter are not substitutes.

The observations are not a transaction across systemd, files, `/proc` and
PostgreSQL. Each fact has its own read window. Both final reads can miss a
change after the last observation or activity that leaves no retained fact.
The reader observes current masks and restart policy; it does not hold an
unmask-proof lease, prevent a privileged actor from changing them, stop a new
manual writer or atomically fence a later filesystem swap. Those intervals and
all activity outside the selected scope remain qualification limits.

There is no installed-service or production-database qualification in this
engineering task. No R07 phase admission, full writer closure, native/client
qualification or observation-clock start follows from a successful receipt.

## Focused verification

[The tests](../../internal/lifecycle/coordination/shared_unit_fence_test.go)
exercise a closed selected-set fixture and negative active/restartable/trigger,
new transient, definition, cgroup, UID, generation, permission, replay and final
reread cases. Systemd responses and the fixture cgroup tree are unit inputs;
they do not qualify the installed host's manager or cgroups.

The owned-child test reads an actual child PID, then closes its input, waits
for its exit and checks `/proc` absence. The PostgreSQL test uses an existing
maintained disposable-cluster fixture, an actual authenticated Unix connection,
a live uncommitted writer, restricted stats visibility and read-only checks.
It skips explicitly without the disposable test DSN; that skip is not database
coverage. The test never selects an operational database.

Run focused tests through the existing task:

```sh
mise run test:unit -- ./internal/lifecycle/coordination \
  -run '^TestSelectedUnitFence' -count=1 -v
```

Run the same selection with `-race` under the maintained disposable PostgreSQL
fixture for actual database coverage. The selected run's raw evidence records
counts, skips, child cleanup and database outcomes separately from synthetic
systemd proof. `mise run check` and documentation tiers 1–4 remain separate
engineering gates. Final common Go images belong to Release after the last
composed Go change lands.
