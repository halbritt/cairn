# Delete an ordinary note

Use `cairn delete` to remove an active Class A note that has no retained
references and has never held a privileged class. It takes a JSON request:

```json
{
  "request_id": "NEW_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 2
}
```

The authenticated agent operation is `cairn agent delete`, with the same request.
The caller's repository scope applies. No operator grant is required, matching
ordinary create/edit authority. The result contains `record_id` and
`deleted_version`; it does not contain the deleted body.

Deletion removes all ordinary revisions and their own applicability/evidence
links and outgoing relations in one transaction. Captured evidence objects are
separate and remain. Cached create/edit response bodies are removed, while their
request IDs and digests remain: retrying an old write returns
`PAYLOAD_UNAVAILABLE` and cannot recreate the note. Retrying the deletion request
returns its original result. A new `get` returns `NOT_FOUND`.

Retained uses, compiler read sets, incoming relations, proposal results,
conflicts, audit history and other protected references prevent hard deletion.
This includes notes considered but not selected by a retained compilation.
Former B/C records and service-generated failure-recovery observations also
require the [audited forgetting workflow](deletion.md). The ordinary command
returns `FORGET_REQUIRED`; it does not silently discard those references.
An open conflict returns a retained `OPEN_CONFLICT` refusal.

Migration 020 adds the response-exclusion marker used by ordinary deletion.
Deploy the updated binary before using the command. No Class D event, tombstone,
purge job or automatic retention schedule is created for this unreferenced A
path. PostgreSQL dead tuples, WAL, backups and previously returned copies are
outside its SQL deletion claim. Restoring an older backup can restore the note;
use explicit security/forgetting procedures for sensitive content.

Database checks cover revision removal, scope/version refusal, write and delete
retries, preserved referenced history, former B state, open conflicts and
concurrent citation versus deletion. An authenticated API check exercises the
same store operation. These checks establish ordinary lifecycle behavior, not
physical erasure.

The local installation runs clean commit
`d61769decbc69fed875a6b668b20a6dc155a9b8a`, with
[passing CI](https://github.com/halbritt/cairn/actions/runs/34188470083), disposable
integration/static checks and a backup/restore lifecycle drill. Installation
followed a private backup; migration 020, the API binary and authenticated
retrieval were verified. Operational record versions retained their previous
digest. No operational note was deleted for verification.
