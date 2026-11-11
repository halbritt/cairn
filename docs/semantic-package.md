# Offline Rust semantic package

`scripts/install-semantic.sh --stage-rust` stages an explicit offline package in
an owned disposable directory. It does not select a service command, edit
configuration, restart a service or modify the existing Python environment.
Invoking the installer without this option retains the Python installer.

Run package builds and fixtures inside the campaign's unchanged
`scripts/test_hybrid_runtime_operations.py` wrapper. The namespace must expose
only selected read-only inputs and tools, with private HOME/cache, no external
network, no production sockets and no host process view. The ordinary Go
`make check`, `make test` and `make test-integration` gates are separate.

## Build inputs

The caller supplies an installed Rust/C toolchain, an offline Cargo source and
all five model files and four native libraries. The build never downloads a
model or library. For example, inside the wrapper:

```sh
make semantic-rust-build SEMANTIC_BUILD_DIR=/tmp/semantic-build
```

The output directory must not exist. The helper uses Cargo `--locked --offline
--release --bins --target x86_64-unknown-linux-gnu`, retains verbose per-unit
output, the release artifact profiles, binary hashes, Cargo.lock hash, local
source hashes, rustc output, target cfg and selected tool hashes. Set
`CAIRN_CARGO_DIRECTORY_SOURCE` to the already configured read-only directory
source to retain its complete file-hash inventory. This variable records inputs;
Cargo's source configuration must separately select that directory.

Build receipts distinguish observed compiler commands and source snapshots from
a complete linked-input attestation. Unknown linkage, CPU and native-dispatch
observations remain explicitly unobserved. Neither a Cargo artifact nor a
runtime source snapshot certifies every linked input. These dynamically linked
workers are not described as static executables.

## Staging

Create a fresh owned `0700` directory outside the checkout, selected build/model
inputs and installed Python semantic tree. Supply a JSON provenance object with
exactly these keys: `model_optimized.onnx`, `config.json`, `tokenizer.json`,
`special_tokens_map.json`, `tokenizer_config.json`, `ort`, `openblas`, `gfortran`
and `quadmath`. Each value contains a nonempty `source` description and the
selected file's `sha256`. Provenance records where bytes came from; the helper
independently checks their hashes. It does not infer trusted provenance from a
filename or version label.

```sh
mkdir -m 700 /tmp/semantic-stage
bash scripts/install-semantic.sh --stage-rust \
  --destination /tmp/semantic-stage --source-root /work \
  --build /tmp/semantic-build --model-dir /opt/model \
  --ort /opt/runtime/ort/libonnxruntime.so.1.29.0 \
  --openblas /opt/runtime/numpy.libs/libscipy_openblas64_-32a4b2a6.so \
  --gfortran /opt/runtime/numpy.libs/libgfortran-040039e1-0352e75f.so.5.0.0 \
  --quadmath /opt/runtime/numpy.libs/libquadmath-96973f99-934c22de.so.0.0.0 \
  --provenance /opt/inputs/provenance.json \
  --abi-prerequisites /opt/inputs/abi-prerequisites.json
```

ABI prerequisites are an array of absolute resolved `path` and `sha256` pairs.
They describe required host loader/system-library bytes, rather than claiming
the native runtime is self-contained. The local real-package fixture derives
its initial closure from ELF dependencies and separately records actual worker
mappings. It does not prove that list is the complete future dependency closure.
An omitted prerequisite list is null and named unobserved.

Staging refuses symlinked selected paths, mismatched source/build/binary/log
inputs, incomplete provenance, an untagged nonempty destination and the existing
Python semantic tree. The stage marker identifies only this disposable package
root. It grants no production activation authority.

Three independent addresses avoid a circular hash:

- `payloads/<sha256>` contains binary, model, native runtime, source-identity
  data, build receipt and logs. Its address hashes the canonical payload record;
  `payload.json` contains that record and is excluded from its own file list.
- `launchers/<sha256>/{rank,stream,embed}` hashes the rendered launcher bytes,
  which contain the selected absolute payload path. A different installation
  root retains the payload address and changes the launcher address.
- `manifests/<sha256>/manifest.json` contains
  `cairn.semantic-worker-manifest/1`. Its address hashes its canonical content,
  excluding its own address. Later observations produce a new manifest path.

Addresses use compact sorted-key JSON without a final newline. Stored
`payload.json` and `manifest.json` append a newline, so their whole-file hashes
differ from the generation addresses. The stage result's `sha256` is the
whole-manifest-file hash. A launcher-set address hashes the canonical mapping
from mode names to rendered launcher byte hashes.

Every generation is published by a same-parent rename. Generation files and
directories are read-only. Staging verifies an existing generation's exact
bytes, modes and tree; a discrepancy refuses without repairing it. This is an
owned-filesystem immutability convention, not protection against the owner or
root changing the filesystem. No `current` symlink or mutable selection is
created. Release must select one exact absolute launcher through its separately
authorized configuration transition.

Updating a listed host library makes the old launcher refuse on its next start.
Re-stage the new bytes and use Release's controlled re-selection; the old
immutable generation is retained rather than repaired.

Each launcher takes zero arguments, checks the selected payload and ABI hashes,
refuses inherited loader overrides, then executes one fixed mode with an empty
environment plus explicit paths, HOME/cache locations, thread caps and
`ORT_DISABLE_TELEMETRY=1`. It requires the host's `/bin/sh`, `/usr/bin/env`,
`/usr/bin/find` and `/usr/bin/sha256sum`; it invokes no Python, compiler or
download client. The worker uses its existing pinned native loader and runtime
telemetry-disable facility. A launcher does not add a production network sandbox.

## Package fixtures and evidence

`make test-semantic-package` exercises actual filesystem publication and a small
C process fixture. That process is not the model. The tests cover stable reuse,
changed immutable bytes, missing/mismatched inputs, fixed argv/environment,
source/build binding, root relocation and preservation of the Python installer
boundary.

`scripts/check_semantic_offline_package.py` is a host orchestration entrypoint,
following `scripts/check_r02_offline_install.py`. It enters the unchanged wrapper
twice, sequentially. The first namespace builds or verifies retained inputs and
exports a standalone archive. The host extracts it as data with
`tarfile`'s `filter='data'`. The second namespace exposes that package at `/work`,
with no development checkout. All builds, generated executables, model activity
and project assertions remain inside the wrapper. The host only orchestrates
and transports data.

With an existing offline directory source and the selected inputs above:

```sh
python3 -I -B scripts/check_semantic_offline_package.py \
  --artifact-dir /absolute/new-artifact-directory \
  --directory-source /absolute/offline-cargo-directory \
  --model-dir /absolute/model-files \
  --ort /absolute/libonnxruntime.so.1.29.0 \
  --openblas /absolute/libscipy_openblas64_-32a4b2a6.so \
  --gfortran /absolute/libgfortran-040039e1-0352e75f.so.5.0.0 \
  --quadmath /absolute/libquadmath-96973f99-934c22de.so.0.0.0 \
  --provenance /absolute/provenance.json \
  --rank-request /absolute/frozen-rank-request.json
```

`make test-semantic-package-real SEMANTIC_PACKAGE_ARGS='...'` invokes the same
entrypoint. The artifact directory must not exist. To reuse exact retained
builds, replace `--directory-source` with `--reuse-build`; `--reuse-tools` also
requires `--reuse-tool-binding`, containing the fixture source and binary
hashes. Source, binary and log bindings are checked inside the wrapper before
use. A different source refuses reuse. No replacement credentials or downloads
are part of this workflow.

The first namespace prepares a test-only Go driver using the production
`Command`, `StreamCommandWithIdleTimeout` and `EmbeddingCommand` supervisors,
plus a public `NativeFactory` observation probe. Neither helper becomes part
of the installed worker. The second entry runs
`scripts/test_semantic_package_real.py` against those prebuilt inputs, without
building them again.

The probe's running-image digest belongs to the probe. The fixture derives each
worker's input manifest by replacing only that digest, then requires the
canonical manifest hash to match the actual worker protocol identity on both
direct and Go routes. It retains all other observed fields and named unknowns.
This explicit derivation is not represented as an observation made inside the
worker. Rank and stream share the rank binary identity; embedding has its own.

The standalone fixture verifies its complete input-file inventory and refuses
a development checkout at `/work`. It probes the canonical host checkout path;
the wrapper's independent self-checks mask all production homes, including the
author worktree. Python and bootstrap tools
remain mounted by the existing wrapper. Literal no-Python qualification is an
explicit gap. The fixture creates no nested namespace, changes no wrapper
restriction and provides no unconfined model fallback.

Three deliberately wrong copies replace existing system BLAS/Fortran/quadmath
objects only in the second namespace. Every successful worker must still map
the exact four selected native images. Missing/wrong library, missing/wrong
asset and binary-digest cases refuse all three launchers. Inherited shadow
loader settings also refuse. A disposable Go stream service retains its selected
launcher inode, timestamp and hash across repeated staging, idle release and a
forced worker failure followed by respawn.

For actual loaded native bytes, the fixture correlates `/proc/PID/maps` with
the worker's sealed memfd descriptors and hashes those descriptors. It also
checks system-library file identities before hashing their mapped files.
`ldd`, ELF declarations and selected paths alone do not establish loaded bytes.
Syscall traces cover descendants, including failed `connect` calls and telemetry
store mutation. Positive controls demonstrate both detections, then remove only
their own generated store before the workers start. Each worker run checks an
initially empty fixed HOME/cache and records whether the mapped OS-home ORT store
is absent or inaccessible under the wrapper's mask. It checks that state again
afterward; inaccessible is not represented as inspected absence. Store names in protocol output are not counted as
filesystem writes. Raw outputs,
binary packages, manifests and private paths stay outside Git.

The initial manifest labels mode derivations and qualification unobserved.
Local observation creates a new manifest containing verified mode identities
and complete derivation inputs with their existing named unknown fields.
Local positives alone never set fleet qualification to complete. Release's
nonempty inventory must cover every managed active/restartable semantic host;
the acceptance matrix requires `6*N` positive runs for `N >= 1` supported CPU
classes, plus named negative cases and a live-staging/respawn fixture per class.
Unknown effective CPU/library classes and unreachable hosts remain gaps.

## Campaign status on 2026-11-11

Semantic lead accepted S-02 / CAIRN-325 and S-03 / CAIRN-326 under the current
owner-amended implementation criteria. S-03 landed through
`f2c7253060c004c1e21d6601d49b95b607180166`; author result
`952308bd-a9dd-480e-964f-8dc86af5a98e/1` and SAME1275 review
`67404226-5cfc-4f3a-87c8-5347de33da6a/1` include the fulfilled documentation
condition. This closes those implementation slices; it does not accept S-04,
all-host qualification, runtime/production activation, controller transitions,
retirement, R1-draft work or the outstanding CPU/FENV obligations. Historical
failed attempts remain preserved. The retained R-01 characterization is partial
Proximal evidence, not an authoritative complete host/ABI/CPU roster.
