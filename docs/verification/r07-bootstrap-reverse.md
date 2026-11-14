# R07 bootstrap and retained Python reverse: source gap and fixture

The original installed layout has **no supported operator bootstrap or bootstrap
reverse entrypoint** on source `8631c6f12b8b484d608d959683ed5a6767a2db66`, tree
`771aa3be71d205ab3db7f3b330f2e7c9183328c2`. The three-file engineering slice
provides a metadata planner and an owned regression fixture. It deliberately
does not add an apply command, accept a serialized drain receipt, or turn an
inventory into admission. Native qualification, production mutation and remote
commands are outside this fixture.

The starting observations are the accepted read-only inventory
`6c560c91-c5c1-4af7-9313-12d93599c1af/v1`, report SHA-256
`4db796b61102022514f2a6824c959e02ace52fb889fa094f8f109f94b65d3217`, index
`9735a920a150facb15b2f2af79c668b79e5e4e1afd566aae543977f724832d41`.
Those are historical observations, not current owner or writer coverage. The
fixture does not contact those hosts or reuse any installed data.

## Why a Python swap cannot currently be admitted

| Boundary | Current source and consequence |
| --- | --- |
| Original mixed directory | [readWatchBindings](../../internal/lifecycle/coordination/shared_unit.go) accepts pinned JSON basenames and `.watch.lock` only. The 17 non-JSON archives prevent even the local part of drain capture. Renaming them away first would mutate the original before the required drain. |
| Private drain receipt | [RunUnitControl](../../internal/lifecycle/coordination/shared_unit_control.go) validates current/next selections before capturing authenticated D1–D5. Its `inspect` output contains evidence, not the process-local `WatchDrainReceipt`. |
| D6 | [recheckPrepared](../../internal/lifecycle/coordination/shared_unit_drain.go) is private and compares freshly observed facts. Python cannot reuse a JSON `inspected` flag or earlier fingerprints as that receipt. The read/rename interval is not atomic even for the existing checked operation. |
| Genuine unbound retained /2 | [unboundReceipt and unboundMemory](../../internal/lifecycle/coordination/shared_unit_census_unbound.go) require a verified disabled /3 association. [loadUnitTarget](../../internal/lifecycle/coordination/shared_unit_control_target.go) explicitly separates retained /2 binding and literal argv from a /3 target receipt. |
| Universe and account hooks | [Census](../../internal/lifecycle/coordination/shared_unit_census.go) retains the disposable-fixture scope boundary. The catalog root must be owned `0700`; nonselected account/workspace hook layers still need actual closure. A new subtree cannot cover unchanged issued paths outside it. |
| Identity | `unitControlPairIdentity` fixes binding, state directory, harness, repository, socket, token selector, config home and affected memory state. Copying profiles into a new subtree does not authorize moving those identities. |
| Complete writer fence | A joined fixture child, a quiet snapshot and a selection bitmap do not identify and fence all installed service/restart/native writers. No currently selected source contract supplies that complete stopped boundary for this original layout. |
| Semantic reverse | [S04R contracts](../semantic-python-closure.md) bind exact immutable payload/control generations and a separate private home. Their accepted runtime selection is not established by lifecycle /2 binding bytes, inventory or the inert file fixture. |

These gaps were reported before any shared helper, Go, schema, SDK or Store
edit. The existing Go checks remain unchanged. A successful bound `/2` profile
data roundtrip through the Go control command is narrower than native retained
Python qualification or a full Go `/3` target roundtrip.

## Proposed separate operator boundary

Root must arbitrate a separate source contract before implementation outside the
three-file lease. The smallest useful boundary would keep these facts in one
control process:

1. Read the exhaustive original JSON selection and approved opaque archive
   metadata, including aliases, owner, mode and the actual lock inode. Validate
   genuine bound/unbound retained `/2` associations without normalizing them to
   `/3`, inventing receipts or altering the current Go loader.
2. Capture actual authenticated D1–D5 over that same original selection. Bind
   state, sessions, authoritative terminal correspondence, journals, lock owners,
   callback joins and complete pagination. Unknown or missing coverage refuses.
3. Establish an independently supported service/restart fence and complete
   observable stopped/joined writer boundary. An input Boolean or caller-selected
   scope cannot establish it. If no such contract exists, remain unsupported.
4. Reread applicable D6 and exact original layout/lock/issued references at the
   swap. Refuse new/live/racing owners, lock changes, unknown members or changed
   receipts before modifying originals. Preserve the documented final-read race
   limit rather than claiming a cross-process transaction.
5. At a separately admitted stopped boundary, reverse to the exact original
   retained `/2` binding, literal argv, complete raw layout and exact accepted
   S04R payload/control/home selection. An automatic failure rollback is not
   this operation. Missing factual closure remains explicitly unsupported.

This is a proposal, not a new API or permission. No common RC image rebuild or
production remediation is performed by this slice.

## Layout and preservation rules

The metadata planner never opens config, credential, alias, lock or historical
archive contents. It reports the three fixture topologies independently:

| Topology | Observed shape represented by new synthetic files |
| --- | --- |
| Proximal | Eight binding JSON files, one existing `.watch.lock`, all 17 named `.before-*` archives. Original JSON files are `0600`; proposed/checked retained data copies are `0400`. |
| Archon | `codex-archon.json` and its existing lock. |
| Talos | `opencode-one.json`, `0755` initialization directories and no observed lock. Absence is not proof of no watcher; lock initialization requires a separate admitted identity. |

Fixture copies of `0750` home and `0775` `.local`/`.local/share` produce a
remediation **plan**, with no chmod of those observed paths. Unknown/missing
members, symlinks and known credential inode aliases stay visible in the report.
Their contents are neither read nor hashed. Historical 17-versus-19 count
disagreement is not resolved by constructing a 17-member fixture.

The raw filesystem primitive allocates its own new home, joins a child it
created, parks the complete original directory, publishes separate `0400`
synthetic profile copies using a hardlink to the **same** lock, then separately
restores the original directory. It checks all eight issued-reference byte
strings and original member inodes, modes, owners and mtimes. It does not copy
or recreate an equivalent lock. Same-filesystem rename/link do **not** preserve
original ctime or parent timestamps. The two ordinary renames have an observable
gap; this primitive is not an atomic publication implementation. Faults retain
the original at the last observed path and do not invoke automatic rollback.
Adding the retained hardlink increases the lock's link count and changes its
ctime; retaining its inode does not preserve every stat field.

The primitive does not capture an authenticated drain or establish a complete
writer fence. Consequently its positive preservation result is never reported
as a supported operator bootstrap. Checked generation reverse, raw bootstrap
reverse and S04R runtime reverse are separate evidence obligations.

## Run and interpret validation

Use the normal host HOME, tools, authentication and services. Only application
fixture state is disposable. Source worktrees belong under the maintained
`git-wt` cache, never `/tmp`.

```sh
mise run test:unit -- test_r07_bootstrap_reverse -v
mise run host:exec --timeout 12m --weight 1 -- python3 -B scripts/check_r07_bootstrap_reverse.py --evidence "$HOME/.cache/r07-bootstrap-new-evidence"
mise run check
mise run host:exec --timeout 5m --weight 1 -- python3 -B scripts/verify_docs.py --tier 1,2,3,4
```

The evidence destination must be new. The explicit integration command builds
two private fixture images, starts an `OwnedCluster` and uses the maintained
`DisposableHost` through the existing status/control fixture. It is not a final
five-role common RC build. The actual control consumer uses authenticated
read-only endpoints. Separately recorded fixture actors create and settle
synthetic obligations; the control consumer never settles them itself.

The integration cases cover read-only inspection, original archival/unknown
membership refusal, replayed facts refusal, unbound missing-receipt refusal,
live callback refusal, joined callback acceptance, checked bound `/2` data
switch/reverse and changed-lock refusal. A second instance introduces a real
owning hold after capture: D6 refuses before exchange and retains the complete
unpublished preparation with the same lock inode. Protected database/state
snapshots distinguish the actor's mutation from the consumer's reads.

The S04R file test uses inert synthetic interpreter/model/native files with the
existing `seal_inputs`, `publish_payload`, `render_python_controls`,
`verify_generation` and `private_home` contracts. It never executes those files,
imports ORT or runs rank/stream/embed. Exact accepted S04R selection, runtime,
destination ABI and host home closure remain unknown.

Raw command exits, source/tool pins, selected read responses, process wait
receipts, PostgreSQL stop and scratch-removal receipts are private evidence.
Fixture token files and credential selectors are not retained or content
hashed. Parent environment equality is checked without printing its values.
Failed runs remain failed evidence; a corrected fixture uses a fresh evidence
directory. Passing tests confirm the stated source gap and bounded mechanics;
they do not grant R07 admission, release old holds or start an observation clock.
