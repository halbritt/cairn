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
One failed context effect does not block attempts on other registered context
files in the same deletion. The worker reports failures after those attempts.

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

## Adopting a historical context file

`cairn adopt-context RECEIPT_UUID RUN_DIRECTORY` brings one historical `context.txt`
into the same custody a new run registers, so `preview-delete` inventories it as
a `managed_context` effect and `purge-deletion` removes it. It is an operator
command that uses the same instrumented operator channel as `purge-deletion`
and takes only the two values you name. It does not scan, list or guess runs.

```sh
cairn adopt-context 8f0c2d5e-6c60-4b0e-9d7c-1f8f7a1d2b34 \
  "$CAIRN_ARTIFACTS/8f0c2d5e-6c60-4b0e-9d7c-1f8f7a1d2b34"
```

It accepts only a case where every one of these holds, and otherwise refuses
with a code and changes nothing:

- **The run is yours and finished.** The receipt belongs to the invoking
  channel and repository, was launched, and has a recorded outcome. A receipt
  that predates a restore fence (`STALE_PACKAGE`), is paused by a restore
  (`RESTORE_PAUSED`) or whose payload was forgotten (`PAYLOAD_UNAVAILABLE`) is
  refused. A policy change since the run is not a reason to refuse: adoption
  authorizes no delivery.
- **The directory is a private local run directory.** It is an absolute
  canonical path (no symlink components) named by the receipt UUID, owned by
  the invoking user with no group or other permissions, on a local filesystem
  (NFS, SMB/CIFS, Ceph, AFS, Coda and 9P are refused). It is locked with the
  same `flock` that writers and purge workers use, and the lock is held until
  the adoption is recorded.
- **The file is what Cairn retained.** `context.txt` is a regular, private,
  single-link file of this user (`ARTIFACT_UNSAFE` otherwise, including a
  symlink, a directory, a hard-linked name and anything over 8 MiB). Its bytes must
  equal the canonical rendering of the receipt's sealed package, which the
  store reads and re-verifies against its seal. An edited, truncated, empty or
  foreign file is `ARTIFACT_MISMATCH`; a format change since the run makes an
  old file unverifiable for the same reason.
- **No other custody conflicts.** A registered run, a different recorded
  directory, retained recovery custody for the receipt, or a marker already
  naming another receipt is `CUSTODY_CONFLICT`. Adoption never replaces custody.

On success it creates the private `.cairn-context-owner` marker (a fresh UUID,
mode 0600) and records a `managed_context` row with `custody_origin: adopted`
and what it observed now: the file's size, device/inode and modification time,
the directory identity and the SHA-256. Those are filesystem observations, not a
launch, delivery or completion time. It writes no delivery, usage, outcome or
assessment record, so `use-report` and `run-report` do not change, and it
bumps the source records' use generation, so an earlier deletion preview goes
stale. `context.txt`, `outcome.json` and every other file are untouched.

Order and failure handling follow the existing writer's. The directory is
locked and the file read and hashed first. The store then checks the adoption
read-only, so a predictable refusal leaves the directory exactly as it was. Only
then is the marker created and fsynced, and the registration committed
afterwards; the command reports success after that commit.

- *Retry.* An identical completed adoption returns the recorded custody with
  `already_adopted: true` and changes nothing. It re-verifies the bytes first, so
  a file changed since is refused (`ARTIFACT_MISMATCH`) while its custody stays.
  Recorded custody whose marker is gone is refused (`ARTIFACT_CHANGED`) and is
  never repaired by creating a new marker.
- *Interruption.* If the process dies after the marker and before the commit,
  rerun the same command: a well-formed marker that no custody record references
  is reused. A marker that is malformed, not private, a symlink or already
  referenced elsewhere is refused. The marker that remains is harmless: purge
  never removes it, and nothing is claimed until the row commits. A commit
  whose reply was lost shows as `already_adopted` on retry.
- *Concurrent adopters* serialize on the directory lock, and exactly one
  creates custody. *Concurrent forgetting* either commits first (the payload is
  gone: `PAYLOAD_UNAVAILABLE`, nothing adopted) or after (its preview or
  inventory then includes the file; an older preview is `STALE_PREVIEW`).
  A refusal after the marker was made leaves the marker and the unchanged file.

Adopted custody then behaves exactly as registered custody does: purge checks
the directory identity and marker, unlinks only the reserved `context.txt`
slot, refuses a replaced directory, and keeps `outcome.json` and the marker.

Limits. The file is verified when it is read. The directory lock is advisory, so a
process that ignores it can still change the file afterwards; as for registered
custody, purge removes the reserved slot whatever it then holds. This adopts one explicitly named file for a launch of this operator's
channel on this host; it accepts no remote or other-user custody, no moved copy
whose directory name or identity differs, and no scan or bulk mode. It cannot
verify a file the current renderer would not reproduce byte for byte. Other copies
(backups, snapshots, hard links elsewhere, in-process and provider copies) remain
the residuals listed above. Adoption records custody from now on: a backup taken
before it does not know the file, so deletion effects restored from that backup
do not include it. Adoption is not general retention or redaction.

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

Runs created before migration 017 remain unregistered residuals until an
operator [adopts](#adopting-a-historical-context-file) one explicitly. Their
paths are not guessed or silently adopted. Missing/replaced ownership directories
remain failed effects until their location/ownership is resolved. Restoring a
pending effect can finish against the same registered directory or an already
absent slot. Relocating an artifact tree needs explicit reconciliation; copied
paths do not establish the original directory identity.

The worker cannot recall in-process or provider copies. Filesystem snapshots,
backups, hard-link aliases, underlying storage and metadata remain outside this
slot-unlink observation. Overall deletion therefore still reports `limited`
after available effects finish. Metadata/evidence redaction, adoption beyond the
narrow case below, backup inventory, retention and newer-deletion restore
reconciliation remain roadmap work.

## Verification

The PostgreSQL race suite runs the real wrapper and confirms its context is a
purge target while its outcome survives. Filesystem tests exercise identity and
marker replacement, symlink isolation, a live writer lock, partial writes, stale
previews and late registration. The lifecycle test kills an observed CLI worker
after unlink and before DB completion, retries it, and restores a pending-effect
backup. These are process-crash and exact-slot deletion checks, not physical
media erasure or a complete power-loss campaign.
