# Audit checkpoints and the backup catalog

`bash scripts/local-store.sh backup` now writes a PostgreSQL dump, its SHA-256
sidecar, and a `.catalog.json` expectation beside it. Retain the catalog separately
from a restored database. A restore can be internally consistent yet omit newer
commits; the catalog supplies the expected checkpoint identity and membership
digest needed to detect a missing expected set.

Before the dump starts, Cairn commits an unsigned checkpoint under a consistent
snapshot. It lists every currently emitted governance/C/D audit event UUID and
its metadata digest, up to 10,000 members. It refuses larger sets rather than
silently truncating. New commits between checkpoint and dump can be included in
the dump; verification reports them as `uncovered_count`. This is an explicit
minimum expected audit set, not a claim that every dump transaction is covered.

The initial subset includes root installation, grants/revocations, instruction
issuance/retraction, conflict resolution, and emitted redaction/forgetting events.
Explicit scope authorizations are C events even when their subject stays A or B.
Governed policy revisions and rollback decisions also enter this C subset.
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

To verify a restored database, set `CAIRN_DATABASE_URL` to that dedicated restore
and supply the catalog's `checkpoint_expectation` to `cairn verify-checkpoint`:

```sh
python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["checkpoint_expectation"]))' \
  /path/to/backup.dump.catalog.json | cairn verify-checkpoint
```

Missing checkpoints or mismatched expectations fail. Missing/altered members
are reported individually and the CLI exits unsuccessfully. An old checkpoint
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
ordinary-memory exclusion and operator authorization. Projection rebuild,
post-backup revocation/deletion reconciliation, and external-effects recovery
remain separate roadmap work.
