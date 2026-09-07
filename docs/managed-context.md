# Managed context files

New wrapped runs register their `context.txt` file before writing its bytes.
Record forgetting includes each registered file as a `managed_context` purge
effect. `cairn purge-deletion DELETION_UUID` now performs both database and owned
context-file effects. It preserves `outcome.json`, the recovery request, and the
run directory's ownership marker.

## Ownership and ordering

A run reserves a new private directory named by its receipt UUID. Existing
directories are not reused. The filesystem owner records the canonical directory
path, device/inode identity, and a fresh ownership UUID stored in a private
`.cairn-context-owner` file. Registration also stores the SHA-256 of the rendered
canonical package. That digest describes expected complete bytes; it does not
claim that a crashed writer finished writing them.

The writer holds an exclusive directory `flock` from before registration until
after the write. The registry transaction requires the authenticated receipt
owner, an instrumented channel, a claimed run and an available sealed package.
It increments source impact generations so registration invalidates earlier
deletion previews. Registration retries recheck exclusion before returning any
cached result.

The purge worker takes the same directory lock and rechecks effect state after
acquiring it. It verifies the directory identity, private ownership and marker,
then unlinks only the reserved `context.txt` slot. It refuses recursive deletion.
A substituted final symlink is unlinked itself; its target is untouched. A
substituted directory or ownership marker causes a retained failure instead of
removing the replacement's data. The marker supplements device/inode identity,
which alone can be reused after directory deletion.

The reserved slot belongs to Cairn. Partial writes and changed bytes in that slot
can still contain the selected content, so purge removes them without requiring
a complete-file digest match. Keep user files outside this reserved slot. The
worker retains the directory and ownership marker as its stable identity.

The context file, run directory and its immediate parent are synced before
success observations.
The database and filesystem do not share an atomic commit. A crash before the
write leaves registered intent; a crash during it can leave partial bytes; a
crash after unlink can leave a pending database effect. Retrying under the lock
removes partial bytes or confirms the slot is absent and commits the observation.
If the database cannot retain the observation, `PURGE_UNRECORDED` leaves the
request inspectable and retryable. No worker timer is enabled.

## Decision and alternatives

Keep context files as the existing inspectable run artifact and add custody at
its actual writer. Disabling future files would reduce duplication but leave
existing supported inspection and deletion obligations unresolved. Guessing
paths during purge would not prove ownership. A database-only status update
cannot prevent a late writer from recreating a removed file, so the filesystem
owner supplies the shared lock; external I/O does not occur inside a transaction
that claims atomic commitment across both stores.

## Authority and limits

Registration is an embedded host operation; it is absent from the agent API.
The CLI purge worker uses an instrumented operator channel for observed file
effects. Payload JSON cannot confer that channel identity. File paths come from
the retained registry, never from a purge command's arbitrary path argument.
This follows Cairn's trusted local-user model; it does not protect against a
malicious process with the operator's OS account and database credentials.

Runs created before migration 017 remain unregistered residuals. Their paths
are not guessed or silently adopted. Missing/replaced ownership directories
remain failed effects until their location/ownership is resolved. Restoring a
pending effect can finish against the same registered directory or an already
absent slot. Relocating an artifact tree needs explicit reconciliation; copied
paths do not establish the original directory identity.

The worker cannot recall in-process or provider copies. Filesystem snapshots,
backups, hard-link aliases, underlying storage and metadata remain outside this
slot-unlink observation. Overall deletion therefore still reports `limited`
after available effects finish. Metadata/evidence redaction, historical-file
adoption, backup inventory, retention and newer-deletion restore reconciliation
remain roadmap work.

## Verification

The PostgreSQL race suite runs the real wrapper and confirms its context is a
purge target while its outcome survives. Filesystem tests exercise identity and
marker replacement, symlink isolation, a live writer lock, partial writes, stale
previews and late registration. The lifecycle test kills an observed CLI worker
after unlink and before DB completion, retries it, and restores a pending-effect
backup. These are process-crash and exact-slot deletion checks, not physical
media erasure or a complete power-loss campaign.
