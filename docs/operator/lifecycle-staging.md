# Stage lifecycle selections

The Linux `cairn-stage-lifecycle` tool implements the binary-staging part of
R-02. It copies an independently pinned executable into an existing private
release directory and prints its `{path, sha256}` descriptor as JSON.
It does not install a binding, configure a hook, validate the `/3` lifecycle
interface or execute the staged program.

Build the tool from the selected source with
`go build -o cairn-stage-lifecycle ./cmd/cairn-stage-lifecycle`. Invoke it with
`--root ABSOLUTE_RELEASE_DIRECTORY --binary ABSOLUTE_EXECUTABLE --sha256 SHA256`,
using the expected lowercase SHA-256 from the accepted build selection.
The root must be an absolute UTF-8 path, already exist, have mode `0700`, and
belong to the invoking UID.
Use a persistent directory for an installed release; tests use disposable homes
inside the campaign isolation wrapper.

Publication creates `ROOT/SHA256/cairn`, with directory mode `0700` and file mode
`0500`. The tool checks ownership and safe ancestors without following symlinks,
copies and hashes through the opened source descriptor, syncs the completed
artifact, and publishes without replacing an existing directory.
An existing selection is reused only after its mode, ownership, contents and
digest pass verification; tampered or incomplete selections refuse unchanged.
Other files in the release root, including retained legacy selections, stay put.

A failure prints a diagnostic and emits no success descriptor. Verification or
root-sync failure after publication identifies the path that already exists.
Retain that state for reconciliation rather than assuming no write occurred.
Staging alone does not authorize activation, live replacement or removal of a legacy runtime.
The compatibility assembler below joins this descriptor to Foundation's
F-01/F-04 interfaces. Build its `/3` executable from **`./cmd/cairn-lifecycle`**.
The ordinary `./cmd/cairn` image remains the API/store CLI and its public
`cairn lifecycle` command remains disabled. Configuration field `cairn` continues
to select that ordinary API CLI; it is not replaced by the private image.

## Versioned configuration generations

`cairn-stage-lifecycle generation --root ABSOLUTE_RELEASE_DIRECTORY` reads a
plan from stdin and publishes `ROOT/selection-ID/manifest.json` with every
selected file in that private directory. Select a canonical nonzero UUID `ID`
before rendering versioned paths into configurations. The JSON plan contains
exactly `id` and `files`; each file has `name`, `path`, `sha256` and an optional
boolean `executable`. Use an independently selected lowercase SHA-256 for each
source file. Names are single UTF-8 basenames; `manifest.json` is reserved.
The input plan and generated manifest each have a 1 MiB limit.

Configuration and retained launch-argv files are copied byte for byte. This
command does not rewrite their contents, discover credentials or interpret a
binding. Render configurations and their paths using the Foundation contract,
and include the exact retained launch argv as an installation member when it
is needed for reverse selection. Selectors remain paths to existing credentials;
credential contents are not installation inputs. Files become mode `0400`, or
`0500` when explicitly selected as executable; generation directories use `0700`.
Old generations and unrelated files remain in place. Reusing an ID with different
contents, modes or membership refuses without replacing it.

The command prints only the generation manifest's `{path, sha256}` pin. Retain
that external pin. `cairn-stage-lifecycle inspect-generation --path MANIFEST
--sha256 SHA256` verifies it and every installed member before returning the
exact immutable file selections. To prepare reverse selection, inspect the
retained previous pin and use its original configurations and launch argv;
do not rewrite a `/2` binding into `/3` or point an old issued command at new
code. This readback is not a global active-config switch or proof that cached
hooks refreshed.

The install lock is an exclusive, nonblocking `flock` on the opened release-root
directory. Every generation publisher using this root shares that lock; a
conflicting installer refuses. It protects publication only. Legacy hooks,
claims and watchers do not acquire it, so it cannot establish quiescence.
The durable order is: copy and pin each member, seal its mode and sync it;
write and sync the manifest; sync the private generation directory; publish
with a no-replace rename; verify every published member; sync the release-root
directory and check its pathname identity. A failure can leave a published
generation: reconcile the intended ID rather than inferring no write from an
error, including a deferred-close error. A killed publisher may leave an unselected
`.stage-*` directory, which is retained for reconciliation, never selected or
automatically pruned. Exact published generations are verified before reuse.

Foundation's validator and activation run in the selected private image, with
the checked frontend continuation and shared activation ledger. File readback
does not replace that consumer or validate an arbitrary binary on its behalf.
Supported refresh, drain and the full watcher unit remain required by the
release contract before a live transition.

## Hash-named component directories

`cairn-stage-lifecycle directory --root ROOT` reads exactly `identity` and
`files` from stdin. `identity` is a selected lowercase SHA-256 directory name;
the consumer defines what it hashes. The file descriptors have the generation
plan's shape. The result is `{path, identity}` for `ROOT/identity`, containing
only those files. There is no added manifest, so a compatibility alias named
`manifest.json` remains legal. The files are sealed as `0400` or `0500` and
the directory as `0700`. No-replace publication, root locking, durable syncing,
readback and exact reuse have the same rules as configuration generations.

`inspect-directory --root ROOT` reads the same plan with each member's path
pointing into `ROOT/identity`. It verifies exact membership, modes and bytes
without publishing or repairing anything. It does not infer an expected digest
from the installed bytes; the independent generation selection supplies it.

## Assemble a compatibility installation offline

Use the selected `scripts/stage_lifecycle_compatibility.py` and built stager
with an offline package containing the pinned inputs. The package needs no
Git checkout or Go toolchain for installation. Invoke the assembler through
the approved isolated interpreter:

```sh
/usr/bin/python3 -I -S -B /package/stage_lifecycle_compatibility.py \
  --stager /package/cairn-stage-lifecycle stage --root /private/release < plan.json
```

The plan has exactly these fields:

| Field | Selection |
| --- | --- |
| `id` | A canonical nonzero UUID chosen before rendering the new configuration paths. |
| `enabled` | The explicit Boolean for this immutable binding. Publishing either value performs no activation or hook switch. |
| `sources` | Map of all 18 canonical F-04 source basenames to independent `{path, sha256}` input pins, including `lifecycle_compatibility.py`. |
| `source_revision` | Source provenance text; it grants no authority. |
| `binary` | `{path, sha256}` for the independently selected `cmd/cairn-lifecycle` build. |
| `memory_config` | `{path, sha256}` for already-rendered memory configuration bytes. |
| `coordination_configs` | Ordered list of one to eight already-rendered `{path, sha256}` selections. |
| `aliases` | Public alias basename to `memory.py` or `coordination.py`; aliases cannot shadow canonical members. |
| `python`, `shell` | Approved absolute interpreter and noninteractive shell paths, external to receipt authentication. |

Each input is an owned, singly linked regular file, without group/other write
permission or symlink ancestors. Source files are bounded at 2 MiB and each
configuration at 1 MiB. Every configuration must already name
`ROOT/generations/selection-ID/binding.json` in `inbox_recall_binding`.
The assembler copies those exact bytes; it does not discover or edit a live
profile. Credential selectors, collection/account fields, custom state paths
and unknown configuration fields stay intact. It never opens the credential
selected by `token_file`.
Configuration parsing keeps the selected Python source's duplicate-last and
opaque-value behavior; the stricter unique-key rule applies to installation
plans and receipts. The copied configuration bytes are not reserialized.

The assembler uses the pinned Foundation material writer to guard the complete
18-member snapshot, duplicate the declared aliases, construct the exact
receipt and render `L` from `V`. It publishes:

- `bin/B/cairn`, where `B` hashes the selected private executable;
- `snapshots/H/*`, where `H` hashes Foundation's sorted name-to-digest map;
- `launch/S/inbox`, where `S` hashes the exact launcher bytes;
- `generations/selection-ID/manifest.json` with the configuration snapshots,
  `/3` binding, compatibility receipt, installation selection and retained argv.

Memory has distinct 13- and 150-second launch selections; each coordination
profile has distinct 2- and 15-second selections. Choose the applicable host
hook row when configuring a qualified unit. Issued inbox commands retain their
own command deadlines. The emitted JSON records the generation pin, receipt pin
and launch argv, and says `activation: not-performed`.

The selected shell is probed with controlled `ENV` and `BASH_ENV` sentinels;
reading either startup file refuses publication. The probe records the actual
isolated, no-site interpreter profile. Python, its stdlib/dependency closure,
the shell, stager and owner-controlled launch selection remain external trust
anchors. Retain those approved dependencies with every still-referenced
selection. Recording a version or `sys.path` is not whole-image authentication
or permission to retire or replace them.

Inspect with the independent manifest pin:

```sh
/usr/bin/python3 -I -S -B /package/stage_lifecycle_compatibility.py \
  --stager /package/cairn-stage-lifecycle inspect --path MANIFEST --sha256 SHA256
```

Inspection verifies the generation and every referenced binary/snapshot/launcher
directory, then reads back each exact launcher byte count, digest and parsed
argv, verifier digest and retained argv. It compares the selected receipt with
the verified generation member, its snapshot/binary with the verified components,
and each launcher's receipt arguments with that same pin. The original input package is no
longer needed. `inspect --legacy` instead verifies an explicitly selected old
generation and returns its exact retained argv. It refuses compatibility
generations, which require full inspection. It provides no automatic
fallback and never converts a `/2` descriptor into `/3`.

### Durability and transition boundary

The assembler holds an exclusive nonblocking lock on `ROOT` across composition.
Each component publisher also locks its own namespace. These are installer
locks; hooks and watchers do not participate in them.

| Order | Durable selection |
| --- | --- |
| 1 | Capture independently pinned source and configuration bytes; qualify the selected launch anchors. |
| 2 | Copy, seal, sync, publish and verify the private binary and complete snapshot. |
| 3 | Finalize binding and receipt bytes, render each exact `V`/`L`, then sync, publish and verify each `L`. |
| 4 | Copy and sync configuration, binding, receipt and retained selection files; sync their generation, publish without replacement, verify and sync its root. |
| 5 | Verify all referenced components and actual launcher byte/argv readback before emitting the independent generation pin. |

A failure emits no successful selection. It can leave published, unselected
immutable components or a complete generation, so reconcile the original UUID
and pins. An exact retry verifies before reuse; differing bytes never replace
the old identity. SIGTERM is deferred during the child's exec handshake; once
ownership is established, interruption kills and joins the owned child.
SIGKILL can leave unselected `.prepare-*` or `.stage-*` files. Keep them for
reconciliation; there is no automatic pruning. Existing selections remain.

Publishing a generation does not publish an active host configuration, prove
that cached callbacks refreshed, or establish a drain barrier. An old enabled
launcher remains enabled when a new disabled generation is staged. Live
watcher/config-directory/shared-user cutover and reverse rollout still require
R-06/R-07's supported drain, refresh and whole-unit verification. R-02's
disposable process fixtures prove file selection and actual installed consumers,
not those live transition claims.

Run the installed process tests through the campaign wrapper, and run
`python3 -I -B scripts/check_r02_offline_install.py` for the standalone-package
check. That host orchestration entrypoint builds inside the existing wrapper,
then starts a second wrapper namespace with only the package bound at `/work`.
The second namespace contains no checkout or production home. It installs and
invokes the selected real frontend again after removing the producer inputs.

## Stage Codex and Claude hook settings

The selected `scripts/stage_lifecycle_hooks.py` renders supported shell hook
rows from a verified compatibility installation. Package it together with
`stage_lifecycle_compatibility.py` and the built stager. It requires an explicit
plan on stdin and an existing owned `0700` output directory:

```sh
/usr/bin/python3 -I -S -B /package/stage_lifecycle_hooks.py \
  --stager /package/cairn-stage-lifecycle --root /private/hook-generations < hooks-plan.json
```

The plan has exactly these fields:

| Field | Selection |
| --- | --- |
| `id` | A canonical nonzero UUID for this settings generation. |
| `selection` | Independent `{path, sha256}` pin for the compatibility installation's generation manifest. |
| `harness` | `codex` or `claude`; both selected role configurations must have this harness. |
| `account_home` | Explicit canonical account-home path; an explicit `config_home` in either selected configuration must match it. |
| `settings` | Independent `{path, sha256}` input pin for the complete settings object to render. Use an empty object for a new profile. |
| `coordination_index` | Zero-based index into the installation's ordered coordination configurations. |
| `replace_commands` | Exactly `memory` and `coordination`, each an array of the exact owned prior command strings to replace; use empty arrays for a fresh profile. Commands must be distinct across the two lists. |

The input settings can be a separately staged snapshot; the renderer does not
discover or open a conventional live account settings path. The selected home
records the intended account. Absent `config_home` fields retain the original
consumer's runtime account-admission behavior. Staging does not verify a live
client's environment, native trust or loaded settings.

Each supported row selects the installation's exact role, configuration and
trusted timeout vector before rendering its single immutable `L` path as shell
text. Codex memory keeps its 12,000-byte context limits on SessionStart and
UserPromptSubmit and its asynchronous Stop row. The remaining supported rows
and timeouts match the existing Codex, Claude and coordination installers.
No OpenCode or Agy bound-recall support is inferred from this shell format.

Within each touched event, one matching owned command is replaced in place.
Its group matcher, group metadata and unrelated hooks remain. If none matches,
a new group is appended. Multiple matches refuse, rather than deleting a
possibly distinct conditional hook. The exact newly selected command is also
recognized, so rendering already-rendered settings adds no duplicate hooks.
Naming a newly selected command in the other role's previous-command list refuses.
Unknown events and other settings remain, including commands the operator did
not select for replacement. Supply the complete owned command lists; this
tool does not identify stale lifecycle hooks by basename or remove them for you.

The stager publishes `hook-settings.json`, `hook-selection.json` and the exact
original bytes as `previous-settings.json` in a new immutable generation. Its
output contains the generation pin and the current/previous settings pins.
Read the generation back with `inspect-generation` and the independent pin
before selecting its files. The input settings file and earlier generations
remain unchanged. Interrupted publication uses the generation publisher's
same no-replace, synchronization and reconciliation rules.

Both the selection metadata and CLI result state `native_trust`, `refresh` and
`activation` as `not-performed`. This command does not invoke Codex's trust
operation, apply settings, interrupt a native turn, refresh a cached callback,
or switch a watcher. Those require the supported native operation and the
separately authorized R-06/R-07 transition. Reversing a file selection alone
does not prove that a running client returned to its previous configuration.

The installed renderer tests are `test_r04_hook_settings.py`; the standalone
package check above also executes every rendered Codex and Claude row without
a checkout. `test_r02_installed_contracts.py` applies the existing F-04 ledger,
failure and deadline oracles to actual installer output, including separately
pinned test child images. Those disposable fault images are not release builds.
