# Maintain and recover a Cairn store

Keep database backups and the external records needed to assess a restore.
A PostgreSQL dump alone cannot tell you which restrictions or external effects
occurred after it was taken. Cairn's restore workflow checks known audit history
and keeps ordinary consumers fenced until explicit admission.

## Upgrade the schema

Back up the store and stop every running API, scheduler, wake supervisor,
dispatch, and consumer process before a planned migration. Build the matching
Cairn executable, configure its intended database, and run:

```sh
cairn migrate
```

Migrations live in `core/schema/`. They use a migration lock and transactional
application. Binaries that implement schema compatibility checks run them at store
opening and transaction entry; direct-pool helpers can bypass transaction checks.
Older binaries can lack these checks: schema-074 `d2cccca` still accepted an ordinary
write after a disposable upgrade to 084, although its `migrate` command refused.
The `cairn.restore_admission` tuple-update barrier in migrations 080 and 084 covers
only transactions that enter `restore_admission`. Neither mechanism replaces stopping
and upgrading every old direct-store process; see
[event admission rollout](event-admission-rollout.md#1-upgrade-the-store).
A failure must be investigated before restarting dependent services;
do not remove migration checks or alter recorded checksums to force acceptance.
Validate changes locally using disposable databases, as described in
[developer testing](../developer/testing.md).

## Back up a managed local store

For a cluster managed by `scripts/local-store.sh`, use Python 3.11 or newer and
run from the matching checkout. The catalog writer uses `hashlib.file_digest`:

```sh
bash scripts/local-store.sh backup
```

The script writes a PostgreSQL custom-format dump, its checksum, and a backup
catalog under `CAIRN_HOME/backups`. The catalog retains the expectation for an
audit checkpoint committed before the dump. Keep the catalog and checksums with
the backup, and retain a trusted copy outside the database being restored.
Verify the dump checksum before using it.

A checkpoint covers selected authority and deletion audit metadata. It does not
verify all note bodies, prove their truth, or establish that no later history is
missing. See [audit checkpoints](../audit-checkpoints.md) for its exact coverage.
For an independently managed PostgreSQL database, use its backup procedure and
retain the corresponding Cairn checkpoint expectation separately.

## Restore into isolation

Use the complete [restore admission procedure](../restore-admission.md), including
its request formats. The sequence is:

1. Stop dispatch and consumers. Restore the database and coordinated artifacts
   into an isolated environment with external execution disabled.
2. Apply current migrations and call `begin-restore` before connecting ordinary
   consumers. A raw database restore does not automatically establish this fence.
3. Inspect trusted external recovery records and reapply known newer
   restrictions using [recovery reapplication](../recovery-reapplication.md).
4. Rebuild required projections, reconcile deletion effects, and run
   `verify-restore` with the external checkpoint, recovery expectations, and
   retained compiler fixtures.
5. Resolve failures, then call `resume-restore` for explicit admission. If any
   collection uses enforced event admission, complete `event-closure-reconcile`
   for the new restore generation before expecting new non-closure publications
   or waits to be admitted; `begin-restore` zeroes existing rate buckets at
   fence time and leaves the new generation unreconciled (see
   [event admission rollout](event-admission-rollout.md#restore-and-generations)).
   Restart consumers only after resume (and any required reconciliation)
   succeeds.

Do not skip verification between reapplication and resume. A successful
verification is a snapshot; it does not itself authorize serving. An old backup
cannot establish whether work ran after it was taken. Reconcile uncertain
external effects before publishing replacement requests.

For larger stores, [recovery bundles](../recovery-bundles.md) provide streamed
exports and inspection. Preserve their manifest digests and every required part
outside the restored database. These records supplement database backups.

## Manage retained diagnostic data

Search evaluations can retain candidate details for replay and diagnosis.
`candidate-retention` can preview eligibility or expire old candidate detail.
`candidate-reclaim` rewrites the table to return space to the filesystem under
the [retention contract](../candidate-retention.md).
They do not provide general note deletion or database cleanup. Review the
request and effect before reclaiming data needed by a restore fixture.

For note removal, distinguish [ordinary deletion](../ordinary-delete.md) from
[audited forgetting](../deletion.md). Editing a note or deleting a current row is
not a substitute for the required effects on retained history and references.

Use normal PostgreSQL monitoring and maintenance for the database itself.
Cairn's tests and recovery reports establish specific checked properties;
[historical verification records](../verification/README.md) are evidence for
their recorded runs, not a live health report for your installation.
