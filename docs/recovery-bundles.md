# Streamed recovery bundles

Directory mode is the operator path for a recovery union that exceeds a single
record or the bounded compatibility collector:

```sh
cairn recovery-export --directory /private/recovery/after-withdrawals-001
cairn recovery-inspect --directory /private/recovery/after-withdrawals-001
```

Set `CAIRN_DATABASE_URL` to the isolated restore for inspection/reapplication.
The unscoped operator and current-root rules are unchanged. No ordinary agent API
exposes these operations. Checksums detect changed input; they do not authenticate
a source or establish its freshness. Retain the trusted bundle and the returned
`manifest_sha256` independently of the database and its backup.

## Publication and format

The parent directory must exist. Export writes a new mode-0700 private staging
directory named `.cairn-recovery-staging-*`, streams mode-0600 part files, syncs
them and the manifest, then publishes the destination without replacement. An
existing destination, even an empty directory, refuses. Ordinary failure removes
only the current staging directory and reports cleanup failure. A process crash
can leave its distinct unpublished staging directory. A new invocation uses a
new staging directory; it does not delete prior exports or abandoned directories.
If a directory-sync error follows publication, a complete destination can exist
without confirmed crash durability. Inspect it and choose a new destination for
retry; do not overwrite the retained copy.

Each `part-000000001.json` file is an independently valid
`cairn.recovery-record/1`. No existing record digest/schema semantics changed.
`manifest.jsonl` uses the separate `cairn.recovery-bundle/1` transport contract:

1. One header line with schema, root grant, optional set identity and part count.
2. Exactly that many descriptors, in position order, with `index`, the exact
   generated `file` basename, the SHA-256 of that file's bytes (`sha256`), and its
   canonical recovery-record checksum (`record_sha256`).
3. A final `sha256` line hashing the exact preceding lines including newlines.

Readers limit each manifest line to 4,096 bytes, reject extra/missing descriptors,
unsafe paths, symlinks, public/wrong-owner files, changed file/record checksums,
wrong root/positions and incomplete sets. They read one record at a time and
stage the complete union in PostgreSQL before reporting consistency. A directory
listing or a subset of files is not a complete manifest. Directory inspection
returns its validated manifest digest even when the database has known state
mismatches; malformed input returns an error before a consistency result.

## Reapply a validated bundle

First inspect the bundle against the isolated restore and review its mismatches.
Missing original audit events can remain after legitimate reapplication; see
[reapplication coverage](recovery-reapplication.md). Do not use an invalid,
incomplete or untrusted bundle. Choose a new application namespace UUID for this
restore and retain it for retries. The example below pins the independently
retained manifest digest, snapshots only its small descriptor stream to temporary
disk, then runs the existing operator command once per part. It does not load all
records or all descriptors into memory. Each command still checks the exact
record checksum and current root; a failure stops the loop.

```sh
python3 - /private/recovery/after-withdrawals-001 PINNED_MANIFEST_SHA256 RESTORE_NAMESPACE_UUID <<'PY'
import hashlib, json, pathlib, subprocess, sys, tempfile, uuid
bundle, pinned, namespace = pathlib.Path(sys.argv[1]), sys.argv[2], uuid.UUID(sys.argv[3])
def line(stream):
    value = stream.readline(4097)
    if not value or len(value) > 4096:
        raise ValueError('missing or oversized manifest line')
    return value
with tempfile.TemporaryFile() as snapshot, bundle.joinpath('manifest.jsonl').open('rb') as manifest:
    first = line(manifest)
    header = json.loads(first)
    count = header['count']
    if header['schema'] != 'cairn.recovery-bundle/1' or type(count) is not int or not 1 <= count <= 100000:
        raise ValueError('unsupported bundle header')
    digest = hashlib.sha256(first)
    for index in range(1, count + 1):
        raw = line(manifest)
        part = json.loads(raw)
        if part['index'] != index or part['file'] != f'part-{index:09d}.json':
            raise ValueError('unexpected part path or position')
        digest.update(raw)
        snapshot.write(raw)
    trailer = json.loads(line(manifest))
    if manifest.read(1) or trailer['sha256'] != digest.hexdigest() or digest.hexdigest() != pinned:
        raise ValueError('manifest differs from retained expectation')
    snapshot.seek(0)
    for raw in snapshot:
        part = json.loads(raw)
        request = str(uuid.uuid5(namespace, pinned + '/' + part['file']))
        subprocess.run(['cairn', 'recovery-reapply', '--request-id', request,
                        '--expected-sha256', part['record_sha256'],
                        '--reason', 'Reapply trusted recovery bundle',
                        str(bundle / part['file'])], check=True)
PY
```

Reusing the same namespace and pinned bundle after interruption gives each part
the same request ID and intent. Every original part must be retained, including
the part later supplied to verification. Recapture refuses a retained incomplete
set; importing a different digest at an existing position refuses before mutation.

Then run the documented rebuild pages, verify using one original part plus the
backup checkpoint expectation, and explicitly resume. An interrupted verification
restarts its snapshot read, while completed reapplication and rebuild work stays
committed. Ordinary work remains paused until current verification passes under
the resume lock. [Restore admission](restore-admission.md#stores-beyond-one-recovery-record)
sets out the exact bounds and remaining limits.
