# Reapplying withdrawals after a restore

An older backup can revive a grant, instruction or forgotten record. An
independently retained [recovery export](recovery-inspection.md) identifies
known restrictions that the backup lost. `recovery-reapply` applies them under
current operator authority and retains the external expectations for subsequent
inspection and export.

Keep the restored store isolated. Choose the source export and its content
digest from trusted retained evidence, then use a stable request UUID:

```sh
cairn recovery-inspect /private/recovery.json
cairn recovery-reapply --request-id UUID --expected-sha256 DIGEST \
  --reason 'Reapply known withdrawals to the isolated restore' /private/recovery.json
cairn recovery-inspect /private/recovery.json
cairn purge-deletion DELETION_UUID
```

The reapplication result includes each affected subject, original event and
digest, outcome, and any current event or deletion ID. Purge each reported
deletion through the ordinary worker and inspect its residuals. An `absent`
subject remains absent; the operation does not create a replacement record or
grant. An external context whose source record is absent refuses the application
because no real deletion request can own its effect.

The command requires the existing unscoped operator channel and live root
owned by that caller. The file must be an owner-only regular file owned by the
operator. Its root must match the restored store and its embedded content digest
must match `--expected-sha256`. The checksum detects changed input; it establishes
neither source authentication nor freshness. No agent API exposes this operation.

## Atomic restrictions, retained history

One serializable transaction applies the bounded withdrawal set and retains the
application audit, external source record and deletion effects. Any authority,
scope, conflict, inventory, source contradiction or database failure rolls back
the application. Existing bounded serialization retries apply. The request key
uses the existing caller/operation/UUID contract: retry identical intent after a
lost response; changed intent with the same key refuses. Current root authority
is checked even for a cached result.

Revocations precede retractions, which precede forgetting. They use the ordinary
transaction code and guards, including caller-owned impact previews, open-conflict
refusals, version checks, payload exclusions and dependency propagation. Recovery
obtains previews within its own transaction; as with ordinary previews, their
presence proves inventory acquisition, not human review. Root revocation remains
unavailable. Already-restricted subjects do not receive redundant transitions.
Existing tombstones with incomplete exclusions refuse rather than acquire a
second deletion request.

An application records a new `reapply_recovery` audit event. Any newly necessary
revoke, retract or forget transition also receives its own new local event.
Original external events remain missing if the backup lost them: hashes cannot
reconstruct their actors, timestamps or transaction IDs. Inspection therefore
continues to return `AUDIT_MISSING` and an unsuccessful consistency result even
when reapplication has repaired all known restrictive state.

For forgotten sources, inspection accepts a current deletion only through an
explicit retained mapping of original event, digest, subject and repository to
that deletion event. It still verifies the deletion's payload and dependency
exclusions. Later exports merge retained external expectations with local audit
and withdrawals; they do not silently discard known missing history. Conflicting
expectations refuse. The existing 10,000-member/count limits remain, with overflow
refusing the transaction rather than retaining a partial expectation set.

## File custody and remaining recovery work

A post-backup context can have no receipt in the restored database. Imported
custody is retained separately in `recovery_context`, linked to the new recovery
application and an actual deletion request. It does not fabricate a receipt,
exposure, launch or host observation. Conflicting descriptors for a receipt
refuse the transaction. Recovery adds a pending managed-context deletion effect
when no such effect exists; it preserves an existing effect's status.

The ordinary instrumented worker rechecks the directory lock, device/inode and
ownership marker before removing the reserved context slot. Its attempt/result
observations remain separate from the imported descriptor. Process exit after
reapplication leaves durable work to retry. Unmanaged copies, retained metadata,
provider state, WAL and backups retain the existing deletion residuals.

This operation does not establish restore admission. Source freshness, unknown
newer events, lost dependency relations, broader projection reconstruction and
physical backup restoration remain separate requirements. Restore fencing still
invalidates old handles without stopping processes or proving restored state is
safe for fresh compilation. L9 remains incomplete.
