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
evidence pointers. Operational adoption is verified separately when the clean
committed binary is installed. No reduction in model task errors or independent
setup time has been measured.

The implementation decision used validated doctrine packet
`pkt-7cf60a527d7b142a`. Its schema-validated receipt retains four nonmaterial
obligations concerning a policy/mechanism split that was not introduced and
generic named procedures. File effects, alternatives and consumer checks are
recorded directly. This supports the bounded installer decision, not full
roadmap acceptance.
