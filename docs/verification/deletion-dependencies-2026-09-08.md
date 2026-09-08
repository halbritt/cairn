# Forgotten-source dependency repair

Verified 2026-09-08 with PostgreSQL 17. Restore inspection exposed a broader
ordinary-write defect: after forgetting a source, a new note could cite one of
its retained dependents without inheriting the forgotten-source exclusion.
That new descendant was selected by fresh compilation.

## Change

Relation writes now inherit every deletion exclusion attached to the exact
referenced version. The note and its history remain reviewable. A later
independent revision without the withdrawn dependency stays distinct from the
historical version that retains it.

Forgetting advances the affected dependent records' use generations before
committing exclusions. This orders relation writers against forgetting: an
in-flight writer makes forgetting wait and invalidates its old impact preview;
an older relation snapshot cannot cross a committed forget. The existing
serializable retry and explicit stale-preview behavior remain.

Migration 026 reconstructs exclusions from retained deletion requests and the
transitive versioned relation graph. It adds missing rows and advances the
affected records' use generations, preserving record bodies, versions and
original authority events. Stop old writers during upgrade: an older binary
can reintroduce the missing projection after the backfill.

Recovery inspection now reports `DEPENDENCY_EXCLUSION_MISSING` when a known
forgotten source's descendants lack that exact deletion's exclusion. Traversal
uses the same 1,000-version bound as deletion impact checks and refuses overflow.
Inspection remains read-only and uses one snapshot. It does not reconstruct
missing relations or withdrawal events from hashes.

## Evidence

- The original public-write regression selected the new descendant after its
  ancestor was forgotten. The original restore regression reported
  `consistent: true` after an isolated transitive exclusion was removed.
- Focused tests cover inherited exclusion, independent revised versions,
  missing transitive projection, existing payload/authority checks, and both
  sides of the relation-writer/forget ordering boundary. Both ordering tests
  also fail against the preceding committed source.
- `make test-integration check` passed across all five Go packages with race
  checks, disposable PostgreSQL, existing CLI/API checks, vet and formatting.
  All 12 Python tests passed separately.
- `make test-lifecycle` passed the actual backup/restore, custody, purge,
  restore-fencing and restart checks in its disposable store.

An upgrade drill used the previous clean released binary
`c34f2db9e0c2582223ce8238bb0d4adae66e43e4` to create the defect in schema 025.
It selected the descendant and its recovery inspection reported consistency.
An actual dump was restored into another database. Before migration, the new
inspector identified the missing exclusion. After migration 026, fresh
compilation omitted the descendant and inspection passed. Record-version
digests and reviewable bodies remained unchanged. Repeating migration did not
change the projection. The original source database remained unchanged, and
the disposable cluster was stopped and removed.

A population check confirmed one exclusion before upgrade and exactly two
afterward: the original direct dependent and the later descendant. An unrelated
control record received no exclusion. This checks backfill membership rather
than relying only on a changed compiler result.

Private evidence: `/tmp/cairn-recovery-dependencies-live-reproduction.log`,
`/tmp/cairn-recovery-dependencies-final-focused.log`,
`/tmp/cairn-recovery-dependencies-integration.log`,
`/tmp/cairn-recovery-dependencies-lifecycle.log`,
`/tmp/cairn-recovery-dependencies-python.log`, and
`/tmp/cairn-recovery-dependencies-upgrade.json`. Exact backfill membership is in
`/tmp/cairn-recovery-dependencies-upgrade-population.json`; the ordering baseline
is `/tmp/cairn-recovery-dependencies-ordering-red.log`.

The schema-validated decision receipt is
`/tmp/cairn-recovery-dependencies-decision-receipt.json`, using doctrine packet
`pkt-6a818e1bea3dba02` after two evidence passes. Material propagation, ordering,
population and preservation obligations are covered. Generic UI/configuration,
ranking, serving-parity and named-procedure obligations remain explicitly
nonmaterial to the pre-release repair claim. Deployment parity is separate.

## Remaining recovery work

The [baseline audit](../../CAIRN_FAILURE_MODE_AUDIT_GPT6_2026-09-08.md) traces this
defect separately from missing withdrawal replay and restore admission. This
repair closes the demonstrated propagation/backfill/inspection gap. It does not
complete L9, reconstruct lost audit events, establish export freshness, recover
post-backup file custody or authorize a restored service to resume.

Already delivered bytes and historical receipts are not recalled by this change.
The existing operational requirement to isolate restored stores and fence old
delivery capabilities remains. Full projection verification and recovery
admission need their own evidence.
