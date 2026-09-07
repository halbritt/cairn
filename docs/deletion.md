# Forgetting retained record bodies

`preview-delete`, `forget`, `deletion-status` and `purge-deletion` implement an
operator-only Class D workflow. Forgetting applies to all retained body versions
of one record and the canonical packages and mutation responses that copy it.
It preserves identity, audit, correction links and use history. It does not
claim complete physical erasure.

## Operator workflow

Run `cairn preview-delete RECORD_UUID` to inspect retained versions, known
versioned dependents, their uses, and the deletion target inventory. The preview
expires after one hour. New versions, citations, exposures, evidence refresh or
changes in known copy targets invalidate it. Inventories above 1,000 targets or
the existing impact bounds refuse; no partial inventory authorizes forgetting.
An ordinary retraction preview cannot authorize deletion.

Submit the returned token with a live grant containing `redact`:

```json
{
  "request_id": "NEW_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "grant_id": "GRANT_UUID",
  "preview_id": "DELETION_PREVIEW_UUID"
}
```

Pass this JSON to `cairn forget`. The transaction creates a new tombstoned
version, a matching `forget` audit event, deletion request, dependency flags and
per-target effects, including registered context files. A cited A record follows this D path too. Open conflicts on
the record or known dependent versions refuse with a retained refusal ID.
Emergency redaction of conflicted content remains a separate unfinished path.
The audit uses a fixed action description and does not copy a user-supplied
secret-bearing reason.

Once the transaction commits, new retrieval omits the record. `get`, saved
package replay, affected recompilation, body expansion and cached mutation
responses return `PAYLOAD_UNAVAILABLE`. Record listing omits tombstones.
Unselected historical candidate bodies can also become unavailable to
recompilation. Receipt seals remain historical metadata; Cairn does not alter
sealed bytes and pretend the old seal still verifies. Already returned content
and in-flight reads that precede deletion cannot be recalled.

Known dependent versions remain stored but are excluded from future compilation
until reviewed and revised. A mandatory instruction with a forgotten dependency
blocks compilation. A relation does not identify copied spans, so the workflow
does not erase related records or separately captured evidence automatically.
New citations to the tombstoned record refuse.

Use `cairn deletion-status DELETION_UUID` for current effects, then
`cairn purge-deletion DELETION_UUID` to execute pending or failed database and [managed context-file purges](managed-context.md).
Each effect and its completion commit together. A worker crash rolls back its
current effect, leaving earlier completed effects intact. A retry resumes
remaining work. SQL failures retain a bounded error code and increment the
attempt count; failure to retain that observation returns `PURGE_UNRECORDED`.
Filesystem effects hold their registered directory lock across unlink and completion observation. Effect state changes append actor-stamped history. A lost response never
requires repeating the forgetting transition: retry its original request UUID
and use `deletion-status` for current progress.

States mean:

| State | Meaning |
|---|---|
| `partial` | At least one required effect is pending, running or failed. |
| `limited` | Available effects finished, but explicitly recorded residuals remain. |
| `completed` | Every inventoried effect is confirmed completed. The current implementation always retains residual effects, so it cannot reach this state. |

## Remaining copies and limits

Database purge removes the record-version body values, full canonical packages
(including compact index summaries and any legacy query text in those packages),
and matching retained response JSON. Other records in a shared package also
lose that package's replay, while their own record versions remain intact.

SQL deletion does not certify erasure of PostgreSQL dead tuples, WAL, snapshots,
backups or storage media. Those remain explicit residuals. Previous API callers,
user files, provider deliveries and unregistered run directories can also retain
copies. Supporting evidence, related records, scope/attribution metadata,
observation details and audit reasons need separate review. Digests and identity
metadata are not anonymization. No backup rotation or automatic purge timer is
enabled. Historical run-file adoption, evidence and metadata redaction,
access-policy changes and retention scheduling remain roadmap work.

The restore drill proves that a backup containing a pending deletion preserves
logical exclusion and can resume its effects. A backup from before forgetting
can resurrect content. Reconciling newer deletion/revocation requests before
serving such a restore remains unfinished; a self-consistent old dump is not
proof that it contains all subsequent security transitions.

## Verification

`make test-integration` covers copy exclusion before purge, SQL payload removal,
retained use history, effect retry/failure, stale previews, authorization,
open-conflict refusal, index retry, mandatory dependency refusal, citation/delete
races and D checkpoint membership. `make test-lifecycle` kills an observed CLI
purge worker at a controlled SQL boundary, verifies rollback of only its active
effect, resumes it, and restores a pending-deletion backup into another
disposable database. These checks do not establish physical or provider erasure.
