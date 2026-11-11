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

## Stage Codex, Claude and Agy hook settings

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
| `harness` | `codex`, `claude`, `opencode` or `agy`; configurations for emitted rows must have this harness. |
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
trusted timeout vector. Python rows keep the installation's immutable `L`.
For Go rows the renderer publishes another immutable launcher under the same
launch root. It executes the pinned private image with `memory` or
`coordination`, the eight `/3` receipt-selection flags, and `-- "$@"`.
Coordination rows pass `hook --config PATH` to that launcher. Every row records
its executable launcher as `checked_launcher` and the original Python `L` as
`retained_launcher`; retain both and their complete selection. The Go memory
entry verifies its derived launcher before receipt admission. The Go
coordination entry reconstructs and verifies the original 21-element Python
vector for its issued inbox commands. Codex memory keeps its
12,000-byte context limits on SessionStart and
UserPromptSubmit and its asynchronous Stop row. The remaining supported rows
and timeouts match the existing Codex, Claude and coordination installers.
No OpenCode or Agy bound-recall support is inferred from this shell format.

Agy stages only its existing coordination `PreInvocation` and `Stop` hooks,
each with a 15-second timeout and an explicit `--event` argument. It uses the
`cairn-coordination` object, whose event values are lists of command hooks.
Only the selected previous command is replaced; unrelated entries, events and
settings remain. Duplicate matching commands or malformed event lists refuse.
All profiles in the receipt must be unbound, and the plan's previous memory
command list must be empty. The output records `ordinary memory hook` in
`unsupported_operations`; it does not install an Agy memory callback or add
bound recall support.

Within each touched event, one matching owned command is replaced in place.
Its group matcher, group metadata and unrelated hooks remain. If none matches,
a new group is appended. Multiple matches refuse, rather than deleting a
possibly distinct conditional hook. The exact newly selected command is also
recognized, so rendering already-rendered settings adds no duplicate hooks.
Naming a newly selected command, checked launcher or retained `L` in the other role's
previous-command list refuses.
Unknown events and other settings remain, including commands the operator did
not select for replacement. Supply the complete owned command lists; this
tool does not identify stale lifecycle hooks by basename or remove them for you.

The stager publishes `hook-settings.json`, `hook-selection.json` and the exact
original bytes as `previous-settings.json` in a new immutable generation. Its
output contains the generation pin and the current/previous settings pins.
The declared layout targets the selected account configuration directory:
`hooks.json` for Codex and Agy, and `settings.json` for Claude. The Agy layout
names its account-wide hooks file; it does not select a workspace
`.agents/hooks.json` file.
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

### Staged OpenCode adapters

The OpenCode selection wires unbound `agent_tools` start, prompt and tool
observation commands to the receipt-checked ordinary Go consumer, and coordination
callbacks to the receipt-checked Go hook entry. Capture, bound and provider-selected
memory rows retain their checked R-02 Python launcher. The bare
`[IMAGE, memory, --config, PATH]` entry is not selected:
it lacks the runtime receipt, image, closure and configuration checks and the
original H/V/L frame. The public `cairn lifecycle` command stays disabled. Shell
transport uses the same literal argv through the selected row's shell-quoted
`hook.command`; shell is not a harness.

For an ordinary installation, add `"profile_mode": "unbound"` to the R-02
compatibility plan and set `"enabled": false`. Every supplied memory and
coordination profile must already have no truthy `inbox_recall_binding`.
The assembler copies their exact bytes, including opaque fields and whitespace;
it never removes a binding. The default mode remains `bound`, with the existing
versioned binding-path requirement. Disabling a bound generation does not turn
it into an unbound staging input. The retained Python caller for an explicitly
unbound generation checks the disabled manifest and every selected profile
hash before running Source, without calling activation.

An OpenCode hook plan uses the fields above with these differences:

| Field | OpenCode selection |
| --- | --- |
| `settings` | Independent pin for the existing `cairn-lifecycle.json` object, or a separately staged empty object. |
| `coordination_settings` | Independent pin for the existing `cairn-coordination.json` object, or a separately staged empty object. |
| `plugins` | Exactly `memory` and `coordination`, each an independent `{path, sha256}` pin for the selected `lifecycle.ts` and `coordination.ts` adapter source. Use controlled files in the selected installer package. |
| `replace_commands` | Exactly `{"memory": [], "coordination": []}`; plugin configurations do not contain shell hook groups. |

For example, the additions to an otherwise complete hook plan are:

```json
{
  "harness": "opencode",
  "coordination_settings": {"path": "/selected/coordination.json", "sha256": "SELECTED_SHA256"},
  "plugins": {
    "memory": {"path": "/selected/lifecycle.ts", "sha256": "SELECTED_SHA256"},
    "coordination": {"path": "/selected/coordination.ts", "sha256": "SELECTED_SHA256"}
  },
  "replace_commands": {"memory": [], "coordination": []}
}
```

The immutable output contains both plugin sources and configurations, their
exact prior configuration bytes, and `hook-selection.json`. The result's
`files` pins and `layout` map identify `cairn-lifecycle.json`,
`cairn-coordination.json`, `plugins/cairn-lifecycle.ts` and
`plugins/cairn-coordination.ts` relative to the selected account. The generation
is flat; `layout` describes a later authorized installation. It does not copy
files into that account. Configuration fields unrelated to `commands` remain.
The plugins use each callback's explicit `commands` argv; a missing or malformed
selected command refuses instead of falling back. Existing installations without
`commands` keep the original Python/script configuration behavior.

Every row records its `implementation` and `reason`. Current routing is:

| Cell | Route |
| --- | --- |
| OpenCode unbound `agent_tools` SessionStart/UserPromptSubmit | Receipt-checked ordinary Go consumer; all selected profiles must be unbound. |
| OpenCode unbound `agent_tools` PreToolUse/PostToolUse/PostToolUseFailure commands | Receipt-checked ordinary Go tool observation with the same H=13 and R-02 receipt/image/closure/profile/launcher checks. |
| Bound callbacks or provider-selected recall | Existing receipt-selected Python caller. |
| Codex/Claude unbound ordinary recall | Existing Python caller. |
| PreCompact/SessionEnd capture and other harnesses' tool observation | Existing receipt-selected Python caller. |
| Coordination hooks for all supported harnesses | Receipt-checked Go `/3` hook entry, with the exact retained R-02 launcher for its selection and issued commands. |
| Agy coordination PreInvocation/Stop | Checked Go `/3` hook with the selected event flag; all profiles in the receipt unbound. |
| Agy ordinary memory hook | Not installed; this tool adds none. |

The selected OpenCode plugin emits PostToolUse for its existing read/edit/write
after-tool callback, and PostToolUseFailure for tool error updates. It has no
before-tool callback. The PreToolUse command is available in the eligible staged
configuration and shell row; this does not add a native plugin callback. Bound
and provider-selected configurations gain no PreToolUse row. The installed
Source/Go pairs use the host's absolute workspace directory.

Every selected command retains the R-02 runtime receipt checks. No native-client trust operation, activation,
cached-client refresh, production apply or full R-04 acceptance is claimed by
this output.

### HC-05 coordination cutover constraint

The direct Go entry refuses a bound `/2` wake with exit 2
(`NATIVE_PROMPT_REFUSED`) during activation, before a session lock or inbox
claim. A session reached only by those wakes receives no wake deliveries until
its selection is repinned to `/3` and it uses the receipt-checked `/3` entry.
Ordinary turn boundaries such as Stop and SessionStart continue to deliver.
Keep this distinction in the later authorized HC-05 cutover and readback; staged
row generation does not repin or change a live session.

`test_r04_host_wiring.py` runs the staged OpenCode plugins and shell argv in
disposable homes against the checked R-02 path and retained Source, including
reverse bytes, excluded cells and changed-input refusal.
