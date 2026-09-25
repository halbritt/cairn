# Inspecting an older restore

A backup catalog describes the audit set present when the backup was taken.
A later recovery export lets an operator detect withdrawals missing from that
backup. It carries the root grant identity, governance/C/D audit metadata hashes,
revoked grants, forgotten records, retracted C instructions, and the registered
context-file custody associated with forgetting. It contains no record bodies,
queries, task prompts or audit reasons. Paths and identifiers remain private.

After a withdrawal, retain a new export outside the database and its backup set:

```sh
cairn recovery-export /private/recovery/after-withdrawals-001.json
```

The parent directory must already exist. The command creates a new mode-0600
file, syncs it and its directory, and refuses to overwrite an existing path.
If writing or syncing fails after creation, it removes the incomplete file and
syncs the directory; any cleanup failure is reported with the original error.
Exports have a content checksum, not a signature. The operator must retain the
latest trusted file independently of the database being restored. Creating an
export from the restored older database does not provide that expectation.
Nothing currently refreshes exports automatically or guarantees their freshness.

Keep the restored service isolated, then inspect it with that external file:

```sh
CAIRN_DATABASE_URL='host=/private/restore/socket dbname=cairn sslmode=disable' \
  cairn recovery-inspect /private/recovery/after-withdrawals-001.json
```

Inspection requires an unscoped operator channel and an owner-only regular file.
It rejects symlinks, malformed or oversized JSON, unknown fields, checksum damage,
and unrelated root identities. Exports are bounded to 10,000 audit members,
withdrawals and context references each, and 16 MiB of JSON; overflow refuses
instead of silently exporting a partial expectation.

A mismatch exits 7 with `INTEGRITY_FAILURE` and a report in `data`. Reasons include
`AUDIT_MISSING`, `AUDIT_CHANGED`, `GRANT_REVIVED`, `RECORD_REVIVED`,
`INSTRUCTION_REVIVED`, `PAYLOAD_EXCLUSION_MISSING`, and
`CONTEXT_CUSTODY_MISSING`. `DEPENDENCY_EXCLUSION_MISSING` identifies a forgotten
source whose retained relation descendants lack the exact deletion's exclusion.
This check traverses at most 1,000 retained versions per source and refuses
overflow instead of certifying a partial set. A grant's revocation flag is checked even when its
original audit event is still present. Forgotten records must retain the
logical exclusions on versions, selected receipts and cached mutation responses.
Missing records and scopes that differ from the expectation also produce gaps.
Known file custody must match its retained descriptor and purge effect, with either an original exposure or explicit imported recovery custody.

`consistent: true` means only that these checks match the supplied snapshot.
The report separately counts outstanding effects and explicit residuals; pending
purges can coexist with consistent logical exclusion. It does not inspect files,
prove media erasure, verify all projections or establish the latest security state.
The command changes neither the database nor external files.

[Recovery reapplication](recovery-reapplication.md) applies missing restrictions
and retains newer file custody with auditable provenance. Original missing audit
events remain gaps. [Restore sessions](restore-admission.md) pause ordinary core
and API transactions, verify the known retained structures and compiler fixtures,
and require an explicit current-root resume. They cover a missing original
withdrawal only through exact retained reapplication provenance and restricted
state; inspection continues to report the original gap.

Inspection success alone does not authorize resume. The ordinary API has no
recovery endpoints. A restore outside the explicit isolated begin/verify/resume
procedure is not automatically detected; external-source freshness remains an
operator responsibility.

`make test-lifecycle` takes an actual dump before revocation and forgetting, runs
a process afterward to create new context custody, exports the later evidence,
and inspects the restored old dump. It detects all four missing-state categories
and confirms inspection itself leaves the revived payload and file unchanged.
It then reapplies revocation, instruction retraction and forgetting, retains the
original audit gaps across a new export, and purges the post-backup file through
imported custody without fabricating a receipt.
Unit/integration tests also cover a revived mutable flag with an intact audit,
missing cached-payload and transitive-dependency exclusions, wrong root, damaged
checksum and caller scope. [Migration 026](verification/deletion-dependencies-2026-09-08.md)
backfills known dependency exclusions from retained relations. It does not
reconstruct missing original withdrawal events or newer context custody.
