# Checked Agy layer crash recovery

The selected `hooks.json` rename commits checked publication. The explicit Go
`recover-agy-layer` operation validates a pinned publication and finishes a
committed selection forward, preserving its new inode, bytes and mode. It does
not run Agy, refresh a running unit, or grant runtime or production authority.

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
After making its own record durable, the publisher checks the other layer for
Cairn dispatch and canonical recovery records. An old selection plus the exact
staged new inode is in progress and blocks publication; an exact new selection
plus its recorded old backup (or prior absence) identifies a completed commit.
Malformed, missing or changed correspondence refuses. A refusing precommit
publisher withdraws only its own unchanged record and staged inode and syncs
the directory. This ordering uses no new lock or record fields.

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
layers and the exact old/new inode correspondence. Recovery performs no rename. Unknown,
missing, stale, mutated, aliased or foreign observations refuse. It never
repairs file permissions or opens a credential selector. It preserves all
unrelated settings bytes, including Unicode and integers outside float64's
exact range. The retained `/2` installer/rollback and default generation
staging remain unchanged.

Before commit, recovery leaves the old selection or absence and staged new
inode unchanged. The retained record remains an in-progress intent; this
read does not silently settle or delete it. After commit, recovery syncs the
selected parent and rechecks all observations. It never restores the old
backup over a committed activation. Both the record and old backup remain as
evidence under the same explicit record path/pin contract. `changed` is false
because recovery does not activate another selection. Repeated recovery is a
no-op only while the same exact file correspondence still matches. Foreign,
stale or ambiguous evidence refuses without overwriting or removing it.

Renames change inode ctime, so correspondence across a rename compares device,
inode, ownership, link count, mode, size, mtime and content digest. Ordinary
file observations and the record itself retain the full ctime recheck.

## Fixture scope and limits

The owned-file process fixture builds and pins the actual stager and private
publisher observer before use. It kills only its own child PID greater than
one, at both sides of settings sync, recovery-record sync, publication,
readback and directory sync, for both layers and initial old/absent settings.
It records state immediately after SIGKILL and after the actual recovery CLI,
then checks repeat/no-op behavior. Its separate refusal cases retain raw
before/after snapshots of both layers and recovery evidence.

A kill before rename leaves the old selection or absence. A kill after rename
leaves a complete committed NEW selection with unfinished bookkeeping; the
next explicit checked recovery validates that new inode and finishes forward.
The fixture records the NEW-active snapshots and verifies record/backup and
selected bytes remain exact. It does not relabel the earlier 20 NEW-active
observations or their old rollback results as a passing new run. Missing
records refuse without guessing. These are owned SIGKILL tests, not power-loss
or reboot tests. Exact candidate commands, image pins, counts and outcomes are
recorded with the frozen candidate packet; independent acceptance is separate.

The durable-intent ordering coordinates checked publishers only. Point
observations cannot exclude an uncooperating legacy writer after the last check. Full qualification
still needs supported unit quiescence/exclusion and the separate installed
transition decision. Native Agy, whole-unit refresh, common-image, store/auth,
CAIRN-305 AC4, HC05 and production acceptance remain separate.
