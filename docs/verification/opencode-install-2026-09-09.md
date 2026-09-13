# Bundled OpenCode installation, 2026-09-09

Native setup previously required a source checkout to copy the TypeScript adapter
and manually construct connection JSON. Both body-only revision and semantic
discovery upgrades required coordinating that adapter copy with the CLI. The
new `cairn opencode-install` command installs the adapter embedded in its own
binary and generates the existing connection format from explicit arguments.
The [operator guide](../opencode-tools.md#install-in-a-project) covers setup,
replacement and declared context.

The command writes only `.opencode/cairn.json` and `.opencode/tools/cairn.ts`.
It preserves host permissions and requires no service, database, credentials or
source checkout during installation. The token path is configuration; the token
bytes are not read. Ordinary API authentication still happens when tools run.
This adds a repeatable installation path, not automatic credential provisioning,
permission policy, service upgrading or agent adoption.

## Checks

- The actual CLI installed into a new directory with spaces, shell syntax and
  Japanese characters in its name, with HOME absent, an invalid database address
  and nonexistent credential paths. Literal paths survived, and a second
  invocation reported both files unchanged.
- Unit checks preserve an existing host permission/model configuration, retain
  declared context fields, compare the installed adapter with the embedded
  source, and verify owner-only modes and unchanged modification times on repeat.
- A differing custom adapter refuses before creating the connection file;
  explicit replacement then installs the bundled version. Symlink directories
  and destinations are refused even with replacement requested.
- Invalid arguments have no filesystem effects. An actual missing-project
  invocation returns `INSTALL_FAILED`, names the affected path and exits 7.
- The native OpenCode integration now installs its files through this command,
  then exercises all five tools against a disposable API. Session scope,
  capture/edit, exact body/evidence pulls, retries, semantic fallback and
  destination/permission refusals pass without model inference.
- `make test`, `make check` and the PostgreSQL/race integration suite passed.

The two files are replaced individually with atomic renames; installation is not
a transaction across both files. After an I/O failure, the operator can rerun the
same command. Concurrent installers or edits are outside this trusted-host
check's coverage. The command does not promise that an independently upgraded
API supports every feature of its bundled client.

[Metadata](opencode-install-2026-09-09.json) retains artifact hashes and local
evidence pointers. No reduction in model task errors or independent setup time
has been measured.

The implementation decision used validated doctrine packet
`pkt-7cf60a527d7b142a`. Its schema-validated receipt retains four nonmaterial
obligations concerning a policy/mechanism split that was not introduced and
generic named procedures. File effects, alternatives and consumer checks are
recorded directly. This supports the bounded installer decision, not full
roadmap acceptance.

## Installed path

The clean `aedc0163d03d89385f1840b48102d9d0b1b82c7f` binary was installed locally.
Running its installer in the existing project preserved connection contents and
tightened the unchanged adapter's file mode. It also installed both files in a
fresh directory with no Cairn source checkout. The API and PostgreSQL processes
remained unchanged; the API still runs the compatible `ecbcc54` implementation.

The ordinary OpenCode procedure was revised from version 7 to 8 to describe the
new setup command, explicit replacement and preserved host permissions. A normal
OpenCode session launched from the fresh directory used the installed tools to
find that procedure first and pull its exact version 8 body. The configured
canonical repository and actual session scope were retained. Completions came
from the scripted loopback fixture, with no model inference. This verifies
functional installation and operational note access from the new directory;
it does not establish autonomous adoption or model task improvement.
