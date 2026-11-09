# Pinned full-occurrence matrix-vector stage

This crate owns the selected NumPy-wheel OpenBLAS runtime and implements the
float32 operation `vectors[1:] @ vectors[0]` for a contiguous matrix of width
384. The query is row zero; every passage occurrence, including duplicate and
cache-hit rows, follows in source order. The input must already have passed
both required normalizations. The output is unrounded float32 similarity in
that same occurrence order.

The crate supplies one real native computation stage. It does not implement a
complete rank `BackendSession`/`BackendFactory`, either normalization, a worker
handshake or derivation identity. No native execution or numerical acceptance
is established by source authoring.

## Exact ABI and source selection

The ABI headers are the unchanged `cblas.h` and `openblas_config.h` from the
canonical PyPI **scipy-openblas64 0.3.31.188.0** GNU/Linux x86_64 wheel, whose
SHA-256 is `2d02f588ba6817a222be8d7053c0dc8bea7f1083de78fdeb2bf9b3e53dcaacb9`.
The [release metadata](https://pypi.org/pypi/scipy-openblas64/0.3.31.188.0/json)
selects this 8,738,257-byte artifact. Its package metadata and installed
pkg-config file identify that exact version, ILP64, prefix `scipy_`, and suffix
`64_`. No equivalence to bare OpenBLAS 0.3.31 or an unobserved upstream commit
is asserted. The build/CPU macros in a header or `numpy.__config__` are build
metadata; runtime CPU/threads must come from actual observation.

| Vendored file | SHA-256 |
| --- | --- |
| `cblas.h` | `fdb5d0e41493b577388dc283c40dc374a9551694ba6d7d401810136010f466d8` |
| `openblas_config.h` | `9e433ce15a78ff8c2de5ae26ac322c26c42546d05e94c4bf51cf9ee08cadb347` |
| `LICENSE.txt` | `51b0d449fdce3b1fabfead1af1cc6eff1df46a70b378c27dc0f5663afc6cc66a` |

The headers declare `blasint` through the ILP64 configuration; the bridge
statically requires eight-byte integers. It obtains all six function-pointer
types with GNU `__typeof__` on the actual header declarations. The CBLAS enums
come from that header and agree with NumPy's selected enums. Rust sees only the
small bridge ABI, not a hand-written CBLAS table or guessed signature.

Read-only ELF inspection of the exact retained NumPy libraries establishes
these exported names: `scipy_cblas_sgemv64_`, `scipy_cblas_sdot64_`, and
`scipy_openblas_get_config64_`, `scipy_openblas_get_corename64_`, `scipy_openblas_get_num_threads64_`, and
`scipy_openblas_get_parallel64_`. The pinned NumPy `_multiarray_umath` extension imports the
same sgemv/sdot names. No library, getter, Python module or package setup was
executed by that inspection.

## NumPy dispatch and arithmetic boundary

The source basis is NumPy **v2.4.6**, acquired from its canonical tagged tree:

- [`matmul.c.src`](https://github.com/numpy/numpy/blob/v2.4.6/numpy/_core/src/umath/matmul.c.src),
  32,380 bytes, SHA-256 `400ca5498718d9b6bc4ccaca015b441e409a0d69dc683038bd0f65c790f65843`.
- [`arraytypes.c.src`](https://github.com/numpy/numpy/blob/v2.4.6/numpy/_core/src/multiarray/arraytypes.c.src),
  132,777 bytes, SHA-256 `b866d807d45835771e72b2104e1c5e836b79f49c5dd975fae8a9bd2eab5c2aa5`.
- [`npy_cblas.h`](https://github.com/numpy/numpy/blob/v2.4.6/numpy/_core/src/common/npy_cblas.h),
  3,499 bytes, SHA-256 `57abc3fd0b4e64dd871af3339707138316f5989c23ef05f6d7846438bccff737`.

With `d_n=384`, `d_p=1`, and contiguous strides, `matmul.c.src:462-550`
selects the following operations:

| Passage rows | Selected path |
| --- | --- |
| 0 | Empty output; no BLAS call |
| 1 | `FLOAT_dot` on one passage and the query |
| 2 or more | One `sgemv` over the entire passage matrix |

The GEMV call preserves NumPy's exact convention at `matmul.c.src:127-160`:
column-major, transposed `[384, passage_rows]`, leading dimension 384,
increments one, alpha one and beta zero. Replacing it with general per-row dot
products would change the selected operation and is not implemented. No
unique-row reduction, deduplication, sorting, passage padding or output
reordering occurs here.

`arraytypes.c.src:3453-3475` implements FLOAT_dot by accumulating CBLAS chunk
returns in a double variable before casting back to float. For this fixed
width and ILP64 configuration there is exactly **one** chunk of 384 elements.
The production bridge calls the real float32 `sdot` once and then adds positive
zero in float32. It performs no f64 product/element/block accumulation. For a
finite float32 result, the Source's exact f32-to-f64 promotion, addition of zero
and f64-to-f32 conversion introduce no precision difference; the explicit zero
addition also preserves its signed-zero operation. This is a source-derived
restriction to width 384, not a generic reimplementation of NumPy dot.
NaN payload/quieting, floating-environment flags, rounding/FTZ/DAZ and exact
host/compiler correspondence still require executable P3 comparison. They are
not claimed to pass. No NaN/finite/zero/norm refusal is added to the input or
output numeric policy. The bridge is authored with `-fno-fast-math` and
`-ffp-contract=off`; the actual selected build settings must still be retained
by the component runner.

## Runtime and dependency ownership

`Runtime::load` accepts three explicitly absolute paths. It hashes their actual
bytes against these fixed NumPy-wheel identities, with no caller-supplied
version/digest or alternate backend:

| Library | Bytes | SHA-256 |
| --- | ---: | --- |
| `libscipy_openblas64_-32a4b2a6.so` | 25,409,073 | `05c9f9eb89ee68a4b9d673184fa91c99587e736392c0c2d49180a8aa5303d080` |
| `libgfortran-040039e1-0352e75f.so.5.0.0` | 2,833,617 | `c6090048eccc763522c12ef016f81da6b627cb3a044f55cf0479a839c41c0980` |
| `libquadmath-96973f99-934c22de.so.0.0.0` | 250,985 | `6ed5137f412781ad7863439fb543613f620b43c32b63292a0029246162f5bbc6` |

The root and gfortran ELF objects have `$ORIGIN` RPATHs and hashed SONAME/NEEDED
entries. Gfortran also needs `libz.so.1`; it is not ignored. The observed static
NEEDED closure includes libm, pthread, z, gcc_s, libc and ld-linux. Those host
libraries have no supplied content pins and remain explicit Release inputs.

The loader copies all three verified payloads into owned memfds and seals
writes, growth, shrinkage and seal changes. It loads quadmath, then gfortran,
then OpenBLAS in one fresh glibc `dlmopen` namespace with `RTLD_NOW|RTLD_LOCAL`.
The exact SONAMEs allow selected prerequisites to satisfy their dependent
objects without relying on the missing `$ORIGIN` directory of an anonymous
file. Runtime viability of this mechanism is still unqualified.

The construction-time namespace inventory uses `dlinfo` and kernel
`/proc/self/maps` device/inode identities. All three pinned files must map
exactly once. An unaccounted object outside the selected standard-library
names refuses. Each resolved CBLAS/getter symbol must actually map from the
sealed OpenBLAS file; a declared pathname alone is insufficient. Rust reopens
and hashes every inventoried mapped file and checks its device/inode against
the kernel record. The three sealed mappings must still match their fixed
byte pins. Paths remain provenance, not complete derivation identities.

Standard dependency names are a bounded supported-host list, not a claim that
any file with such a name is qualified. Their actual paths, mapped identities
and hashes are retained for Release. Their immutability, full workload mapping
closure and comparison to the reference remain open. A mismatch in the pinned
Fortran/quadmath path or contents never silently substitutes a host library.
There is no system BLAS lookup, numeric fallback, retry or alternate version.

The bridge rejects present `LD_LIBRARY_PATH`, `LD_PRELOAD` and `LD_AUDIT` without
mutating global environment. Checking current environment cannot prove what
the dynamic linker consumed at process initialization. Private-namespace
availability/behavior, executable sealed memfds, procfs, loader/ABI support,
standard dependencies and complete launch environment remain Release host
obligations. This source mechanism is not a no-write/no-connect proof.

Actual OpenBLAS configuration, core name, configured thread count and threading
backend are copied from real getters into provenance. No thread count or CPU
name is inferred from build metadata or historical reference observations, and
no runtime setting is silently changed. These getters do not establish a
complete ISA/OS-vector-state/kernel class, per-call thread schedule, floating
environment or parity.

The owned handle is `!Send`/`!Sync`; synchronous computation retains the entire
input and distinct output storage. Bounds cover shape, byte multiplication,
pointer offsets and NumPy's ILP64 BLAS_MAXSIZE. Drop closes the root, then its
Fortran and quadmath handles, then all sealed fds, retaining every cleanup
failure in the caller's `CleanupErrors` observer. Keep that observer after
runtime drop and drain it. Actual namespace unload, background-thread/resource
cleanup and repeated lifecycle behavior still need host qualification.

## Remaining normalization and composition work

The pinned FastEmbed `onnx_embedding.py:311-322` selects CLS for rank-three
output and calls `common/utils.py:18-23`: row L2 norm, `maximum(norm,1e-12)`,
then division. The rank Source subsequently reconstructs the full occurrence
matrix (`semantic_rank.py:101-105`), computes NumPy
`linalg.norm(axis=1,keepdims=True)`, checks its existing zero-norm condition and
divides again before the matmul (`:106-110`). NumPy `_linalg.py:2802-2805` uses
float32 elementwise squares, add reduction and square root for that row norm.
Neither normalization/reduction/division stage is implemented in this crate.

The composing rank backend must supply its future `Normalized` associated
type only after those exact operations, then pass all rows through
`FullOccurrenceMatrix::from_normalized` and `Runtime::matrix_vector`. The latter
supplies only the matrix-vector result; existing rank/score owners retain note
maximum, float64 clamp/scale and Python ties-to-even integer rounding. This
crate supplies no fake norms, BackendSession/Factory implementation, ready
boolean, worker identity or substitute score output. Full P3 remains open.

## Build and verification boundary

Direct dependencies are only sha2 0.10.9 with defaults disabled/force-soft and
build dependency cc 1.11.1. Native edges are the two C units, versioned ABI
headers, GNU/Linux headers, libdl and the explicit runtime dependency graph.
The selected source target is x86_64-unknown-linux-gnu. No bindgen, libclang,
Rust downloader or build-time native payload acquisition is included.
A real Cargo lock must be provided by the designated source-only intake step;
none is fabricated here. Actual build scripts/compiler probes, linking and
tests need separately selected component validation.

Seven authored pure-data tests exercise production layout/dispatch, zero/one/
many-row boundaries, duplicate/cache-row preservation, storage/ILP64 bounds,
nonfinite bit preservation and actual payload hash refusal. They do not load
native math or compute synthetic BLAS answers through a fake backend. Source
format/hash/ELF inspection is not compile/link/test/runtime qualification.

All 294 tokenizer IDs/lengths must pass before actual model/session
construction. P2 raw ORT/output order/model proof, both normalization stages,
P3 bit-identical native math and strict integers, P4 actual host/library/CPU/
egress/write/resource boundaries, P5 real worker/CLI/Go and existing C2/D1-A
IO_NOT_ESTABLISHED/D2 HOLD/RTERM/P/W/uv/failed-runtime/production holds remain.
Actual-worker FIRST is unconsumed. No author build/test/compiler, native
load/getter/BLAS/probe, Python/NumPy/FastEmbed import, model run, Go reference,
service, database or deployment is established by this source unit.
