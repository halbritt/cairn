# Audit checkpoints and the backup catalog

`bash scripts/local-store.sh backup` now writes a PostgreSQL dump, its SHA-256
sidecar, and a `.catalog.json` expectation beside it. Retain the catalog separately
from a restored database. A restore can be internally consistent yet omit newer
commits; the catalog supplies the expected checkpoint identity and membership
digest needed to detect a missing expected set.

Before the dump starts, Cairn commits an unsigned checkpoint under a consistent
snapshot. It lists every currently emitted governance/C/D audit event UUID and
its metadata digest. PostgreSQL retains immutable member rows separately from a
bounded checkpoint header. Hashing and comparison stream those rows; application
memory does not grow with the total membership. The legacy `checkpoint` response is a complete `cairn.audit-checkpoint/1`
manifest and refuses above 10,000 members. `checkpoint --header` returns the
explicit `cairn.audit-checkpoint-header/1` summary with count, digest and
`digest_schema: cairn.audit-checkpoint/1`; it contains no member-list field.
The backup script uses this bounded header mode. No members are omitted from
retained storage. New commits between checkpoint
and dump can be included in the dump; verification reports them as
`uncovered_count`. This is an explicit minimum expected audit set, not a claim
that every dump transaction is covered.

The initial subset includes root installation, grants/revocations, instruction
issuance/retraction, conflict resolution, and emitted redaction/forgetting events.
Explicit scope authorizations are C events even when their subject stays A or B.
Governed policy revisions and rollback decisions also enter this C subset.
Recovery reapplication and explicit restore resume enter the subset as new
operator decisions. Their member digests also commit the retained source/action
mapping or session/policy/verification metadata, respectively. Earlier event
hash encodings are unchanged.
Ordinary A history and B promotion/correction/retraction/supersession are excluded.
Record-body forgetting emits a D event regardless of the prior record class.
Other D transitions remain unfinished.
An explicit membership list avoids sequence gaps and late-committing transactions
being silently skipped by a sequence watermark.

Each member commits to audit event type, identity, subject, previous/resulting
version, actor, authority basis, transaction identity and timestamp. SHA-256
hashes PostgreSQL's JSONB text representation of that metadata; manifest version
`cairn.audit-checkpoint/1` hashes the ordered member list as Go JSON. Tested restore
compatibility is PostgreSQL 17. Reasons and payload bytes are excluded. These
checks establish audit metadata integrity; they do not validate record bodies,
current projections, truth, or resistance to a malicious database administrator.
They are not signatures, security commitments, or a universal memory ledger.

## Storage compatibility

Migration 057 adds the explicit storage representations
`cairn.audit-checkpoint-inline/1` and `cairn.audit-checkpoint-members/1`.
Existing inline manifests are unchanged and remain readable. New checkpoints
store an empty member array, the complete count/digest, `members_omitted: true`
and the member-storage schema in their stored header; membership lives in immutable
`audit_checkpoint_member` rows. Header and members commit in one transaction.
The complete legacy response and canonical digest schema remain `cairn.audit-checkpoint/1`: the new
reader reconstructs the exact old ordered Go JSON bytes incrementally, including
legacy array order, before comparing the retained and operator-pinned digests.

Use a migration-057-aware binary to verify or restore new checkpoints.
Pre-057 readers do not support member-backed storage: their existing schema
check refuses the new stored header, including an empty checkpoint, and their migration/restore
schema checks refuse a newer migration ledger. This is a fail-closed version
boundary, not backward read compatibility. An old binary can still read an old
inline checkpoint; new binaries read both representations. Do not remove the
storage marker, replace the expected digest, or fill an empty header from current
state to force an older reader to accept it. Updating or deleting a retained
header/member is refused; missing, added or altered members cannot match the
original externally retained digest.

To verify a restored database, set `CAIRN_DATABASE_URL` to that dedicated restore
and supply the catalog's `checkpoint_expectation` to `cairn verify-checkpoint`:

```sh
python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["checkpoint_expectation"]))' \
  /path/to/backup.dump.catalog.json | cairn verify-checkpoint
```

Missing checkpoints or mismatched expectations fail. Missing/altered members
are reported individually, up to the first 100 of each, with `missing_total` and
`altered_total` counting every divergence, and the CLI exits unsuccessfully. An old checkpoint
with exactly its old audit set can still verify; absent a newer external
expectation, that says nothing about later lost commits. Verify the dump checksum
before restoring it as a separate check of the dump file itself.

For a checkpoint without a dump, `cairn checkpoint` accepts JSON `request_id`
and `export_id`. Retain its `checkpoint_id`, `sha256`, and `export_id` outside the
restore as the expectation. Whole-store checkpoint operations require an
unscoped operator channel; they are absent from the agent API.

`make test-lifecycle` restores a disposable dump, checks the catalog, recompiles
from restored candidate inputs and compares the original seal. It also rejects
a newer checkpoint expectation absent from that older restore. Core tests cover
altered and missing audit members, later commits outside the closed set,
ordinary-memory exclusion and operator authorization. The
[restore-session workflow](restore-admission.md) combines this checkpoint
expectation with known withdrawal reapplication, projection rebuilding, compiler
fixtures and recoverable effects before explicit resume. It does not establish
unknown newer history or independently verify external-source freshness.
