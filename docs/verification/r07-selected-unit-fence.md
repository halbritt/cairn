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
pins, optional complete command-line hashes, optional database name/system identifier,
and an observation window of at most 30 seconds. Capture copies and normalizes
the selection. It binds actual machine, boot, process generation, tool, unit,
cgroup, process and any selected database observations to that selection.
An empty explicit unit list is valid, as on the audited talos installation;
at least one executable pin is still required. Manager/transient-unit and
process observations run even when the explicit unit list is empty.

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

Joint follow-on `773027de-a857-4cc8-b330-36998a7a4d63` version 1 keeps both
receipt APIs above unchanged. In the composed bootstrap contract, the caller
performs its final same-scope `Recheck` after callbacks, descriptor opens and
operand/member identity checks, directly before `Renameat2(RENAME_EXCHANGE)`.
The caller implementation and forward/reverse late-activation tests live in
`shared_unit_bootstrap.go` and its tests, under agent-1467's disjoint lease.
The fence never substitutes a cached successful observation for this read.

A refused final recheck means no namespace exchange and the original directory
remains active. It does not imply transaction-wide absence of earlier effects:
the forward legacy path may already have disabled `/2` manifests and written
its recovery journal. As corrected by `64fb86c3-ffd0-4417-aec7-daf2abbe787c`
version 1, preserve those effects and the reconciliation evidence. Re-establish
the stopped boundary before a fresh documented rollback restores raw originals;
do not automatically re-enable hooks after a fence failure.

## Observations and refusals

The production path uses the installed root-owned `/usr/bin/systemctl`, pins
its bytes/identity and checks it around each read-only query. Queries select the
current UID's local user-manager bus. It enumerates `cairn-wake-*` units as
well as the declared units and their reported activation triggers. New
transient membership, missing properties and unreadable manager state refuse.

Selected units must be loaded or masked, inactive/dead, with
`NeedDaemonReload=no` and an enabled, disabled, static, masked, masked-runtime
or indirect unit-file state. Services require `MainPID=0`. An administratively
stopped service can retain `Restart=on-failure`; the reader does not require a
mask or `Restart=no`. Every recursively reachable `Triggers`/`TriggeredBy` unit
must meet the stopped contract. The operator procedure still stops and disables
activation triggers, saving their earlier enablement for reverse.

All returned properties, including restart policy, unit-file state,
`InvocationID` and `StateChangeTimestampMonotonic`, remain exactly pinned across
capture's first/last observations and both observations of every recheck.
Missing required properties, reactivation or generation/property drift refuse.
The retained refusal substring "unit or activation trigger is not stopped and
masked" is a driver compatibility string, not an additional masking requirement.

Effective fragment/drop-in identities remain pinned. A masked definition can be
the manager's literal `/dev/null`, or an absolute symlink whose two `lstat` and
`readlink` observations agree on device, inode, owner, group, mode and exact
`/dev/null` target. The device is not opened as a regular unit file. Replacing
the symlink with another `/dev/null` symlink changes the pinned identity and
refuses. Neither form attests to an earlier unmasked definition.

For an inactive/dead service with `MainPID=0`, an explicitly present empty
`ControlGroup` is recorded as `manager-reported-empty`: systemd normally removes
the stopped service's cgroup. A nonempty group still requires an actual readable,
canonical cgroup-v2 directory. Every observed subtree must have `populated 0`
and an empty `cgroup.procs`; directory identities and contents remain pinned.
Missing properties, paths, files, permissions or population fields are unknown.

The local `/proc` reader considers the selected UID's executable path/inode
identities and optional whole-command hashes. It rereads generation, UID and command
metadata around each selected process observation. Only the caller PID with a
start generation verified against actual `/proc` is excluded as self; matching
the numeric PID alone is insufficient. Other selected processes still refuse.

An executable permission denial permits exclusion only if readable command
bytes, `comm` and cgroup all remain identical across two reads and none could
match a selected command digest, executable/argv0 name, 15-byte Linux comm
prefix, selected unit or `cairn-wake-*` cgroup. UID, generation, state and the
executable's permission-denied status are rechecked too. Any unreadability,
change or possible match refuses. An unrelated Z/X process can be excluded
only with stable nonmatching comm/cgroup, UID and generation. A selected or
ambiguous zombie refuses. ENOENT/ESRCH means absent only when `lstat` confirms
that the PID directory no longer exists; permission and other errors never do.
Private command bytes are compared in process memory and are not
returned as evidence. Other executable identities and other hosts remain outside
the declared coverage. This does not qualify mapped shared-library bytes or
resolve the separate ABI/runtime-adoption holds.

Database coverage is optional only when both `scope.Database` and
`scope.DatabaseSystemID` are empty and `db == nil`. That lifecycle selection
returns `database_coverage=not-selected`, performs no PostgreSQL dial or query,
and supplies no database identity, activity or writer-closure evidence. Partial
database selections and a connection supplied without selection refuse.

When explicitly selected, database input must already be an authenticated local
Unix `*pgx.Conn`, and facts report `database_coverage=selected`.
It must be idle and exclusively borrowed for observation; an existing caller
transaction refuses without being rolled back. The caller must not use this
connection concurrently. Observation cleanup has its own bounded rollback
context, including when the read context expires.
The reader starts a read-only transaction and verifies actual cluster,
database, role and `cairn.semantic_job` identities. It requires superuser or
`pg_read_all_stats` visibility and refuses every other backend in that database,
including idle connections. It does not identify authenticated writers from
`application_name`, query text, a quiet queue or a caller principal flag.
Missing relation/identity/visibility and a failed observation rollback refuse.
No database credentials or connection strings are retained in `Facts()`.

## Consumer prerequisite and limits

The lifecycle-only selection does not require a database constructor. Under
Release follow-on `38c06372-4562-4fd5-be0c-f2810622db61` version 1, the semantic
`pg_stat_activity`/`semantic_job` writer observation moves to an explicit operator
step at the `cairn-api` STOP boundary in the R07 packet. This component does not
execute or attest to that step; database coverage remains outside the selection.
Consumer integration and operational admission still require their own contracts.
The current unit-bootstrap frontend has no authenticated process-local PostgreSQL
connection for the optional selected-database branch. A selected database with a
nil connection refuses before service/process queries. JSON, a DSN presented as
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
exercise a closed selected-set fixture and negative active/changed-policy/trigger,
new transient, definition, cgroup, UID, generation, permission, replay and final
reread cases. Lifecycle-only cases prove database readers are never called,
unselected coverage is explicit, partial selections refuse, and scope/lifecycle
drift still invalidates. Systemd responses and the fixture cgroup tree are unit inputs;
they do not qualify the installed host's manager or cgroups.

Audit-derived cases from `c9cb91d8-dea9-4ba0-bf35-0635a7b41067` version 1
cover the proximal/archon stopped-property shapes and talos's empty unit list.
Additional cases cover mask inode replacement, recursive trigger activation,
InvocationID/generation changes on each pass, two-read permission exclusions,
unrelated/selected zombies and confirmed PID disappearance. These are bounded
fixtures, not fresh remote-host qualification.

The owned-child test reads an actual child PID, then closes its input, waits
for its exit and checks `/proc` absence. The PostgreSQL test uses an existing
maintained disposable-cluster fixture, an actual authenticated Unix connection,
a live uncommitted writer, restricted stats visibility and read-only checks.
It creates `cairn.semantic_job` using the maintained schema's table definition
and proves that a decoy `public.semantic_job` does not supply Cairn identity.
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

The proximal read-only evidence step is explicitly gated and otherwise reports
NOT RUN. It selects actual own-generation, available audited-class non-dumpable
and unrelated zombie processes, and already-inactive installed service
properties/definitions. Each unavailable class reports NOT RUN separately.
It issues no service/process mutation and does not claim full Capture success
while other wake workers remain active:

```sh
CAIRN_SELECTED_FENCE_LIVE_READONLY=1 mise run test:unit -- \
  ./internal/lifecycle/coordination -run '^TestSelectedUnitFenceProximalReadOnly$' -count=1 -v
```

Standing suites do not depend on proximal's live state. Remote candidate upload
or execution on archon/talos remains a separately admitted R07 preflight.
