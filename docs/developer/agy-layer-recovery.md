# Checked Agy layer crash recovery

The explicit Go `recover-agy-layer` operation restores a pinned publication's
old settings inode and exact bytes/mode, or its prior absence. It does not run
Agy, refresh a running unit, or grant runtime or production authority.

Agy's account and project hook layers remain cumulative. Publication checks
both explicit roots; recovery rechecks those same rooted identities and the
unchanged other layer. The selected roots are `ACCOUNT_HOME` and `WORKSPACE`,
with `account` selecting `.gemini/config/hooks.json` and `project` selecting
`.agents/hooks.json`.

## Retain and select correspondence

Before the selected rename, checked `agy-layer` publication syncs the new
settings and a private `0400` record, then syncs their parent directory. The
record pins both root/ancestor observations, old presence and backup identity,
old content digest/mode, and the expected new inode, metadata and digest.
Successful publication retains the record and old inode and returns the
record's exact `path` and `sha256` in `recovery`. An identical publication is a
no-op and does not create another correspondence.

Choose a known record path before publication when interruption could prevent
the result from returning. Its basename must be
`.cairn-recovery-agy-` followed by 32 lowercase hex characters and `.json`, in
the selected settings directory. The optional publication flag is forwarded by
the existing pinned `publish-agy` bridge:

```sh
/usr/bin/python3 -I -S -B /package/stage_lifecycle_hooks.py publish-agy \
  --stager /package/cairn-stage-lifecycle --stager-sha256 "$STAGER_SHA256" \
  --account-home /accounts/selected --workspace /work/selected --layer project \
  --recovery-record /work/selected/.agents/.cairn-recovery-agy-0123456789abcdef0123456789abcdef.json \
  < agy-publication.json
```

Select that exact record and its independently retained expected digest. Do
not discover a record by newest-file order or a glob. A record or digest alone
does not authorize a host transition; existing owner authorization and trusted
custody still govern its use. Do not repin modified or unexplained evidence.

Use the already verified Go image for explicit recovery:

```sh
/package/cairn-stage-lifecycle recover-agy-layer \
  --account-home /accounts/selected --workspace /work/selected --layer project \
  --record /work/selected/.agents/.cairn-recovery-agy-0123456789abcdef0123456789abcdef.json \
  --record-sha256 "$RECOVERY_SHA256"
```

Recovery validates record schema, pin, mode and rooted file identity, both
layers and the exact old/new inode correspondence before any rename. Unknown,
missing, stale, mutated, aliased or foreign observations refuse. It never
repairs file permissions or opens a credential selector. It preserves all
unrelated settings bytes, including Unicode and integers outside float64's
exact range. The retained `/2` installer/rollback and default generation
staging remain unchanged.

For prior presence, recovery exchanges the exact old backup with the owned
new selection. For prior absence, it moves the owned new inode to the explicit
recovery member without replacing another member. It syncs the directory and
rechecks observations. A post-rename refusal attempts compensation only while
the observed inode pair still matches; an intervening writer is not overwritten.
The record and displaced new inode remain as evidence. Repeating recovery is
a no-op only while the exact restored old/absent state and displaced new inode
still match. Stale evidence is never silently removed.

Renames change inode ctime, so correspondence across a rename compares device,
inode, ownership, link count, mode, size, mtime and content digest. Ordinary
file observations and the record itself retain the full ctime recheck.

## Executed fixture scope and remaining gap

The owned-file process fixture builds and pins the actual stager and private
publisher observer before use. It kills only its own child PID greater than
one, at both sides of settings sync, recovery-record sync, publication,
readback and directory sync, for both layers and initial old/absent settings.
It records state immediately after SIGKILL and after the actual recovery CLI,
then checks repeat/no-op behavior. Its separate refusal cases retain raw
before/after snapshots of both layers and recovery evidence.

A kill after publication can leave **NEW active** until explicit recovery.
The fixture records that interval; eventual restoration does not establish
strict old-or-disabled behavior during interruption or full install-5/R06
qualification. Kills before the durable record exists already leave the old
selection or absence; a missing record refuses without guessing. The tests
exercise SIGKILL, not power loss or a reboot.

There is no new lock or common-writer protocol. Point observations cannot
exclude a legacy or other writer after the last check. Full qualification
still needs supported unit quiescence/exclusion and the separate installed
transition decision. Native Agy, whole-unit refresh, common-image, store/auth,
CAIRN-305 AC4, HC05 and production acceptance remain separate.
