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
