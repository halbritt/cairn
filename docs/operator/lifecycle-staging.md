# Stage a lifecycle executable

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
The remaining R-02 work joins this descriptor to Foundation's actual F-01/F-04
admission and compatibility interfaces and exercises installation/reverse
selection in disposable homes.

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

Foundation F-01's read-only `binding.Check` is callable only in the selected
running image; it does not validate a different installed binary on behalf of
an installer. The public `cairn lifecycle` command remains disabled. R-02's
available file publication and reverse-selection primitives therefore do not
provide an activation bypass. F-04 must supply its real activation consumer,
checked frontend continuation and shared grant/ledger decisions before installed
end-to-end activation or rollback can be qualified. Supported refresh, drain
and the full watcher unit remain required by the release contract.
