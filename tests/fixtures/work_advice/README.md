# Advice restore and upgrade fixtures

These explicit, locally run checks use owned disposable PostgreSQL clusters.
They do not install executables or use a production database. The advice provider
is a registered synthetic subprocess, not a model client. Its capability response
is fixture data; it does not establish native Codex or subscription behavior.

`restore_provider.py` is a standalone copy of the browser boundary provider at
commit `12810e2` (original SHA-256
`9e18b738eb6de373fc19ed4ab095d82b2bb4ee76538d6d5b0afbe25a754ac026`) plus the
reviewed before-input and restore-action modes. The original browser helper is
unchanged. The restore executable's reviewed SHA-256 is
`d9cf513045ddc2e493b1e3e678eea234b8bbd977e13e129e4972c29fb0ee6984`.
It receives selected synthetic source text, has no network client, and holds a
real process family only in the explicit `hold_family` mode. The case registers
the copied executable's exact hash through the operator CLI.

## Whole-database restore

Run `scripts/check_work_advice_restore.py` with the explicit Cairn binary,
`--source`, `--capture-manifest`, `--binary-report`, `--native-manifest`, and a
new `--report-directory` outside the source/fixture directories. It retains the
existing wait and native-notice assertions in
`scripts/check_work_coordination_restore.py` and adds real advice jobs,
proposals, applications and a held process to the same backup and restore.
The complete pinned native package is required; native provider traffic goes to
the fixture's local scripted Responses endpoint.

The restored database must enforce generation admission, retained deletion
witnesses, copied-text removal, explicit process reconciliation and fresh
controls. The advice case runs no late-completion race: its old CLI controller
was deliberately stopped before restore. The process supervisor owns only its
created children, pins their identities with pidfds, and retains failure evidence
when cleanup cannot be confirmed. The default first-four restore entrypoint
still runs without an advice extension.

## Schema078 to schema079 upgrade

Run `scripts/check_work_advice_upgrade.py` with `--old-source`,
`--old-capture-manifest`, `--old-binary`, `--old-binary-report`, the corresponding
four `--new-*` arguments, and a new `--output` directory. The old source must be
landed commit `42de49de8345754a7e9da72018c1ee661ff0b9e4`. The new source must
contain exactly migrations001–079 with the first78 unchanged. Real old CLI
state, migration receipts and prior feature states must survive migration;
advice starts disabled, a repeated migrate is idempotent, and the old binary
must refuse schema079 even while advice is disabled. This check invokes no
advice or native provider.

## Source and binary receipts

Both entrypoints consume the existing `cairn.work-ui-source/1` capture format.
Its `source_path` is the absolute captured directory; `source` contains the full
commit as `revision`, `dirty: []`, every relative file path and byte SHA-256 in
`inputs`, and `input_sha256`, computed as SHA-256 of
`json.dumps(inputs, sort_keys=True).encode()`. Captures contain no symlinks.
Keep the manifest outside the source to avoid including itself.

The accompanying `cairn.physical-restore-build/1` receipt binds `source_commit`,
`source_input_sha256`, `binary_sha256`, `build_returncode: 0`, and
`source_unchanged: true`. The same normal079 and old078 builds can supply the
lower-support compatibility, restore and upgrade checks. Capture and fixture
identities are rechecked after each scenario. Reports are operational evidence
and remain outside the repository. A prepared fixture or parsed source is not
an executed acceptance result.
