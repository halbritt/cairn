# Complete owned native unit driver

The [driver](../../scripts/check_r06_native_unit.py) and private
[Go helper](../../cmd/cairn-lifecycle/native_unit_fixture_test.go) have two
separate operations. `--prepare-unit` stages the declared retained Python `/2`
and Go `/3` installations without starting a native client. `--native-unit`
drives the same native conversation through Python, Go and restored Python;
it requires Root's explicit admission and exact composed input pins.
The original `--auth-setup` prerequisite remains available.

This driver is a child of frozen `346e08c1` and its diagnostic parent
`fffc55d5` on production input `31a554ae`. Its native path depends on the
separately owned identity reader, retained `/2` hook producer and Go native
hook consumer. Their named frozen commits must be composed before preparing
the unit. The original worktree and evidence packets remain frozen.
A successful preparation or auth setup does not qualify the native path.

The composed preparation selects the retained producer with the documented
`legacy: true` hook-plan field. It uses Foundation's exact identity readers,
the selected retained consumer/collector and Root's recovery/concurrency
component. The producer bytes remain exactly those of `6249f82c` after the
join; no partial `/3` receipt is constructed for the retained generation.

Preparation exercises emitted memory and coordination callbacks from both
generations against one actual setup session in its disposable API. It uses
the context-issued read and completion/reply operations, retains the real
product journal, and matches the API-returned current and historical IDs.
The selected Python watcher heartbeats that setup execution and is stopped
and joined with its observed argv and lock owner recorded. Go candidate
callbacks run without publishing that candidate config directory.
The actual close must fence the old reference with `STALE_SESSION`. A
source-driven reopen of the same setup ID must return a new execution of the
same API agent; that current reference reads the actual earlier attempt and
must match its product-issued claim and completed journal. These are setup
authentication checks and do not establish a real native conversation.

The pinned Go observer reads the same actual setup files without a fabricated
native readback. The expected result is `FixtureReady: false` with
`account hook/config coverage is unknown: selected native settings/readback seam is unavailable`.
That refusal and the unchanged filesystem snapshot are retained. Preparation
does not turn a setup row into a native row or prove a Go watcher activation,
same-conversation refresh, native D2, or complete-unit qualification.

## Declared installation and operations

One disposable PostgreSQL cluster and actual authenticated Cairn API issue the
fixture profiles. Both implementations select the same API socket, credential
path, account, workspace, coordination state and memory state. Their genuine
immutable bindings are disabled. The retained generation has its actual `/2`
engine and bridge bundles and literal retained argv; it has no `/3` receipt.
The ordinary hook stager produces each implementation's selected settings.
The account's unrelated Unicode and large-integer settings remain pinned.
Before either configuration is staged, the fixture installs the maintained
private local null-selector and sets `memory.claude` to its pinned path. Its
actual inputs, command, result and executable hash are retained. It selects
no durable notes and never starts a model account. Preparation exercises the
actual emitted capture callback for each generation; the Source missing-provider
negative behavior remains unchanged.

The native path uses the existing Claude stream-control transport with one
local scripted Messages provider. `HOME`, account and workspace stay the same
across processes. It observes the actual client result, effective settings,
hook listing and executed prompt hooks. Callback-written session records are
read unchanged and matched to the actual API directory. The fixture executes
only the context-issued read and completion/reply commands for that same
native row; it retains the product-written journal and actual returned IDs.
The local provider remains alive across both refreshes; no model account is
invoked by this fixture.

Each transition starts the selected watcher against the complete declared
config directory and observes its heartbeat of the actual native execution.
The owner stops and joins that watcher. The combined `unit-control/2` path
then inspects the real catalog and authenticated drain before switching the
complete directory. The driver checks the actual returned preparation and
publication, config membership, hashes and shared lock identity. It retains
store and filesystem snapshots around each operation; protected state must
remain unchanged. Every declared callback reference and native layer file
is included in the catalog.

Reverse selects every pinned member of the last retained watch directory.
The original installation generation is not a retained watch selection: it
contains additional runtime files and does not share the watch lock. Its
membership refusal remains unchanged.

After publication, orderly native stop/join and `--resume` select the next
settings. The same conversation must retain its prior prompts and API agent
identity, with one native executor at a time. The final phase runs and drains
the restored Python watcher and performs another combined inspection.
These are implemented operations awaiting the composed-tree checks and native
admission; this document does not report them as measured successes.

The Go owner helper pins its actual child program, enforces a positive deadline
of at most ten minutes, and retains the child's streams, exit and observed PID
and group. Driver cleanup joins owned children and stops its own PostgreSQL
cluster. It never signals a process group.

## Input pins and admission

The image file has schema `cairn.r06-unit-images/1`, a `source_tree`, a
`source_files` map of repository-relative paths to SHA-256, and `images` entries
`api`, `control`, `helper`, `stager`, each with an absolute `path` and `sha256`.
The complete-unit modes require the dedicated driver/helper and all named
predecessor files in that source map and verify their bytes before use.
Images must be built from the final composed tree or have explicit evidence
that every compiled and embedded input, module and build flag is unchanged.
A carried image retains its original build identity; it is not a fresh build.
Build/module and compiler pins belong in the accompanying packet. Use absolute
host image paths; old namespace paths and admission bytes do not select a
changed candidate.

The additional native admission file has schema
`cairn.r06-native-unit-admission/1` and these fields:

- `purpose`: `one-complete-unit-native-run`.
- `source_tree`, `source_files`, and `images`: the exact composed inputs.
- `client`: the explicitly selected Claude executable's `path` and `sha256`.
- `provider`: `local-scripted-messages`.

Its bytes must match `--admission-sha256`. This is a mechanical input check;
a caller-written file does not grant permission. Root must separately admit
the one complete-unit native run. Every native, client and model invocation
remains held until that happens. Existing native allowances are unchanged.

## Validation commands

Run directly on the normal host with the maintained mise task. The complete
unit already owns its disposable application state and PostgreSQL cluster.
The controller, API, watcher and hooks must share the same files, lock inode
and process view. Keep normal host tools, HOME and authentication. Do not wrap
these modes in `test_hybrid_runtime_operations.py` or add namespace plumbing.
With pinned host images, preparation uses:

```sh
mise run host:exec -- --timeout 10m --weight 2 -- \
  python3 -B scripts/check_r06_native_unit.py --prepare-unit \
  --images "$R06_IMAGES/images-host.json" \
  --evidence "$R06_EVIDENCE/preparation" --archive-stdout
```

For a separately admitted native run, replace `--prepare-unit` with
`--native-unit --admission` and its selected absolute host path, plus
`--admission-sha256` and the expected hash. The client must be the frozen host
file named `claude`. Record full argv and pins before use; this example does
not admit a run. Preserve every material failure and cleanup result.

The driver captures selected stdio from actual hook processes using the
maintained trace decoder. Other file-read payloads are not dumped. Exact
selected memory configurations, relevant memory state and actual synthetic
transcripts are copied before and after orderly native close, with original
inode/device/mode/hash metadata. Copies are labeled as copies. Each snapshot
allows at most 64 files, 2 MiB per file and 16 MiB total; overflow refuses rather
than truncates. Credential files and their inode aliases are excluded. The
local Messages fixture retains actual request bodies without authentication
headers. These future captures do not reconstruct previously lost evidence.

Focused driver checks, which start no native client, use:

```sh
mise run test:unit -- test_r06_native_unit_driver -v
```

The retained-watch regression requires an explicit pinned HOST image manifest
and a fresh evidence leaf under an existing private `0700` parent:

```sh
CAIRN_R06_UNIT_IMAGES=/absolute/path/images-host.json \
CAIRN_R06_CONTROL_EVIDENCE=/absolute/private/parent/new-control-proof \
mise run host:exec -- --timeout 5m --weight 2 -- \
  python3 -B -m unittest discover -s scripts \
  -p test_r06_native_unit_driver.py -v
```

It calls the actual `NativeUnit.control()` plan construction and real
`cairn-lifecycle unit-control switch`, `reverse` and final `inspect` against
the maintained disposable API/PostgreSQL fixtures. Both disabled installations
use an empty account and workspace, with no native session, callbacks or
fabricated native readback. The test checks `retained-to-go3` and
`retained-to-python2`, complete pinned membership, target bytes and the shared
`.watch.lock` inode. The original installation-directory reverse still must
refuse. Store and protected-file checks remain active. This is a non-native
configuration proof; it does not qualify native conversation or refresh.
Without the explicit image manifest, unittest reports this prerequisite check
as skipped. The four pure driver checks remain available without images.

Focused helper checks use:

```sh
mise run test:unit -- ./cmd/cairn-lifecycle \
  -run '^TestOwnedUnitFixture' -race -count=1 -v
```

Run the applicable real API/PG preparation/helper check, `mise run check`, and
bounded documentation validation once after the final freeze. A real final
gate failure stops that gate; a capacity exit 75 that never started it is not
a test result. Preparation, helper checks and native qualification have
separate receipts and claims.

## Remaining qualification

The inherited real-API setup establishes routing and actual product journals;
it does not establish native conversation continuity. The three original
missing-selection refusal controls remain unchanged. Source-backed native
settings and retained `/2` interpretation must pass the final product consumer;
unknowns stay refusals. In particular, the existing census checks the selected
system shell and Python paths; a namespace ownership refusal cannot be bypassed
by changing state, substituting another session or exporting a partial fixture.
Record the exact operation and refusal if no supported placement exists.

The declared fixture is finite. The product census continues to report
external coverage as `unknown` and native refresh as `unobserved`; separate
native readbacks describe only the observed stop/join/resume transition.
Per-fact reads and final rename are not atomic. Legacy writers, opaque
attribution and hard-kill intervals retain their existing limits. Production
technical, backup, drain, rollback and phase admission gates, the all-client/R04
matrix, common-image, CAIRN-305 AC4, HC05 and full R06 acceptance remain open. Root owns review, composition and integration.
