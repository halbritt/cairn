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
If the goal is to stop future retrieval while retaining the record, use the
retirement workflow below. `FORGET_REQUIRED` describes the refused hard deletion;
it does not mean that removing the retained history is necessary to retire advice.
An open conflict returns a retained `OPEN_CONFLICT` refusal. Use
`cairn conflicts --record RECORD_UUID REPO` and `cairn conflict CONFLICT_UUID`
to [inspect its retained positions](conflict-inspection.md).

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

## Retire obsolete advice while retaining history

Choose the operation that matches the intended change:

| Intended change | Existing operation | Access |
| --- | --- | --- |
| Correct an active A note | [Ordinary text edit](local-api.md#body-only-revisions), including exact passage replacement | Ordinary CLI/API and native edit tools |
| Replace an obsolete record with a different current record | [Supersede](supersession.md), with both versions and an impact preview | Local agent API for A; operator authority for B |
| Stop retrieving a record without naming a replacement | `retract`, with a live grant and an impact preview | Operator CLI, including when the record is Class A |
| Remove retained body content | [Audited forgetting](deletion.md), with its deletion guards and effect tracking | Operator authority |

For retraction, obtain and review the current impact with
`cairn preview-retract RECORD_UUID`. Then pass a request to `cairn retract`:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 3,
  "grant_id": "AUTHORIZED_RETRACT_GRANT_UUID",
  "preview_id": "RETURNED_PREVIEW_UUID",
  "reason": "This workaround is obsolete and should not enter new task context"
}
```

Replace the example IDs and version with those for the inspected record, preview
and live grant. The caller must own the preview and hold `retract` authority for
the repository. New exposures or changed dependency state invalidate the preview;
`STALE_PREVIEW` requires another impact review. Exact accepted-request retries
return their original result. An open conflict blocks retraction.

Retraction creates an inactive version and an audit event. Fresh retrieval omits
the record, while earlier versions, recorded uses and existing receipt history
remain retained. [Historical reads](record-history.md) still apply current
destination and forgetting restrictions. Retraction does not erase already
delivered context or remove content from a running agent's session.

Ordinary hosted profiles cannot inspect the protected impact preview or invoke
`retract`; the native tool interface has no retirement operation. Use the
operator workflow when an already-used note needs retirement. Editing its body
to say “obsolete” changes the text but leaves the record active in retrieval.
