# Source normalization and pinned full-occurrence matrix-vector stages

This crate owns the selected NumPy-wheel OpenBLAS runtime and implements the
float32 operation `vectors[1:] @ vectors[0]` for a contiguous matrix of width
384. The query is row zero; every passage occurrence, including duplicate and
cache-hit rows, follows in source order. The input must already have passed
both required normalizations. The output is unrounded float32 similarity in
that same occurrence order.

The crate also implements both real Source normalization stages through
`normalization`: raw CLS -> first-pass cache row -> owned full occurrences ->
second norms -> zero decision -> second-pass matrix. It does not implement a
complete rank `BackendSession`/`BackendFactory`, worker handshake or derivation
identity. Source authoring establishes no native execution or numerical acceptance.

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

## Both normalization stages and composition

`normalization::RawClsRow::from_values` accepts exactly 384 raw float32 CLS
components. Consuming `normalize` actually computes squares, NumPy's pairwise
FLOAT reduction and sqrt, maximum with the weakly cast Python epsilon 1e-12,
and true float32 division. `FirstNormalizedRow` has no public constructor that
asserts prior normalization; its private storage is immutable and cloneable for
first-pass cache ownership. Its observed norm/denominator can be inspected.
It has no second first-pass normalization method.

`FirstPassOccurrences::reconstruct` copies a query and all passage references
in supplied order into distinct checked owned storage. Every duplicate/cache
hit gets its own row. `norms_keepdims` consumes that matrix and computes an
independent norm for every row with NO epsilon. `SecondPassNorms` owns both
norms and that exact matrix, exposing immutable pre-division data and
`any_zero_norm`. Consuming `divide_in_place` checks ALL norms first. A zero
returns `ZeroNorm` with the intact owner and the existing "empty model
embedding" diagnostic; it never partly mutates earlier rows. Successful
division produces `FullOccurrenceMatrix` for the published matrix-vector path.
No caller-supplied norms, numeric fallback, clipping or extra finite policy
enters this performed path.

The existing lower-level `FullOccurrenceMatrix::from_normalized` retains its
already-normalized input contract for compatibility. It is not evidence that
either stage ran. The new production construction path uses actual performed
operations; future backend composition must use those stage owners, preserve
the unchanged rank finite check and cache FIRST-pass vectors. Cache restore
needs its own authenticated snapshot boundary; this slice does not introduce
a caller assertion that raw or arbitrary vectors are first-normalized.

The selected algorithms, intermediate rounding, broadcasting, real conjugate,
epsilon dtype promotion, signed-zero/NaN behavior, generated-template/CPU
dispatch relation and full source/license custody are documented in
[vendor/numpy-2.4.6/README.md](vendor/numpy-2.4.6/README.md). Explicit x86 float32
lane operations implement the source-derived arithmetic; actual selected
wheel/CPU/kernel/FENV/NaN-bit correspondence remains unproved. Both stage norm
outputs, unrounded similarities and strict integers still need exact runtime
reference comparison. No tolerance or narrowed input domain substitutes for it.

Existing rank/score owners retain note maximum, float64 clamp/scale and Python
ties-to-even rounding. The accepted rank ordering and other semantic crates
are unchanged. No full backend, ready boolean, worker identity or qualified
derivation is supplied. Full P3 remains open.

## Build and verification boundary

Direct dependencies are only sha2 0.10.9 with defaults disabled/force-soft and
build dependency cc 1.11.1. The existing native edges are the two math C units,
versioned ABI headers, GNU/Linux headers, libdl and explicit runtime dependencies.
S-NORM-1 adds one standalone C11 normalization unit and compiler SSE headers,
with -fno-fast-math, -ffp-contract=off and -frounding-math, and no new library.
Existing loader/compute files and their build flags are unchanged.
The selected source target is x86_64-unknown-linux-gnu. No bindgen, libclang,
Rust downloader or build-time native payload acquisition is included.
The accepted source-only intake provides the real Cargo-produced lock unchanged:
14 packages (13 registry plus this crate), preserving selected versions/features.
The existing selected math component was separately validated; its retained
result is not replayed here. The new normalization unit requires a separately
selected SAME B2 changed-component disposition and SAME R1 primary review;
its compiler/build/test/runtime execution is unexecuted in this author slice.

Seven authored pure-data tests exercise production layout/dispatch, zero/one/
many-row boundaries, duplicate/cache-row preservation, storage/ILP64 bounds,
nonfinite bit preservation and actual payload hash refusal. They do not load
native math or compute synthetic BLAS answers through a fake backend. Nine new
normalization tests exercise performed arithmetic, exact raw shape, epsilon
versus second-pass behavior, analytic pairwise grouping, all-row zero decision
before mutation, duplicates/cache ownership, empty matvec dispatch, nonfinite
flow and subnormal-square underflow. The new tests are synthetic and authored
but UNEXECUTED; no selected NumPy/reference golden or runtime parity is claimed.
Source formatting/hash inspection is not compilation or test qualification.

All 294 tokenizer IDs/lengths must pass before actual model/session
construction. P2 raw ORT/output order/model proof, actual execution and exact
reference proof for both normalization stages, P3 native math and strict integers,
P4 actual host/library/CPU/egress/write/resource boundaries, P5 real worker/CLI/Go and existing C2/D1-A
IO_NOT_ESTABLISHED/D2 HOLD/RTERM/P/W/uv/failed-runtime/production holds remain.
Actual-worker FIRST is unconsumed. No author build/test/compiler, native
load/getter/BLAS/probe, Python/NumPy/FastEmbed import, model run, Go reference,
service, database or deployment is established by this source unit.
