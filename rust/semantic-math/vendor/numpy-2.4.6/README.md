# NumPy 2.4.6 normalization source and license

`native/normalization.c` adapts the real-float branch of NumPy's pairwise sum
and x86 float32 arithmetic. NumPy's BSD-3-Clause notice is retained unchanged in
[LICENSE.txt](LICENSE.txt). Only the selected algorithms are adapted; NumPy is
neither built nor dynamically loaded by this component.

## Source path and operation order

The selected FastEmbed `onnx_embedding.py:311-322` takes rank-three CLS
`[:,0]` (rank-two output directly), then `common/utils.py:18-23` performs
`norm(ord=2, axis=1, keepdims=True)`, `maximum(norm, 1e-12)` and division.
Each real batch-size-one raw 384-float row enters this first stage separately.
The unchanged rank Source checks the first-pass model.embed output's shape and
finiteness, reconstructs query plus EVERY passage occurrence, and then performs
its independent second norm, all-row zero check, in-place division and matvec
(`scripts/semantic_rank.py:98-122`). Cached passage vectors are first-pass
values; their duplicates and warm/cold occurrence order survive reconstruction.

The retained `_linalg.py:2787-2805` chooses exactly the same real vector branch
for ord=2 and ord=None: `(x.conj()*x).real`, `add.reduce(axis=1,keepdims=True)`,
then sqrt. `calculation.c:829-861` returns a legacy real numeric array itself
for conjugate with no out argument; no conjugation arithmetic is needed.
Both stages use contiguous `[rows,384]` binary32 storage, stride4 along axis1;
keepdims norms are `[rows,1]`, broadcast within their matching row only.

- `loops_arithm_fp.dispatch.c.src:27-69,92-177` selects FLOAT multiplication,
  pairwise add reduction and true division. Squares are materialized into a
  separate 384-float array before reduction. `loops_utils.h.src:63-145` fixes
  PW_BLOCKSIZE=128, eight float32 accumulators and the exact parenthesized tree.
  Width 384 splits into 192+192, each into 96+96; each 96-element leaf uses eight
  lanes, twelve values per lane, then the explicit four-pair tree. No sequential
  all-element sum, double accumulation, reassociation or multiply-add is used.
- `generate_umath.py:12,351-365` gives add the integer-zero identity.
  `legacy_array_method.c:274-324,429-457` packs/caches that identity into the
  FLOAT descriptor; `reduction.c:299-379` initializes each result from it.
  The arithmetic loop updates positive zero with its pairwise float32 result.
  `ufunc_object.c:2287-2346` only widens integer/bool add reductions, not FLOAT.
- `loops_unary_fp.dispatch.c.src:1-7,105-179,199-239` explicitly forces 128-bit
  SIMD on x86, including scalar tails, and `simd/sse/math.h:11` maps float32
  sqrt to `_mm_sqrt_ps`. The adaptation uses that same instruction on one
  meaningful lane and zero-filled unused lanes. Rows are independent; the exact
  reference vector batching/FP-status boundaries remain a qualification item.
- `ufunc_object.c:680-709` marks the Python epsilon as a weak scalar.
  `dtypemeta.c:964-969` keeps an existing real floating dtype when promoted
  with PyFloatDType; maximum thus remains FLOAT. The already-retained
  `arraytypes.c.src:123-136,317-326,389-410` converts Python binary64 to FLOAT.
  The epsilon is the exact binary64 value `0x1.19799812dea11p-40`, converted at
  runtime with `_mm_cvtsd_ss` from a volatile double. It is not pre-rounded into
  a binary32 constant, so the conversion is not silently pinned to a different
  rounding mode. No epsilon is used in the second stage.
- `loops_minmax.dispatch.c.src:19,49-63,224-255,317-359` supplies FLOAT maximum,
  first-NaN propagation and scalar/vector stride handling. The first pass has
  one norm: its scalar tail uses x86 max plus the ordered-first-operand mask.
  The adaptation retains that mask. It does not use C fmaxf, ignore NaNs, or
  add a zero/finite refusal. With this positive epsilon, either zero sign
  selects epsilon; a first NaN stays NaN, and infinity stays infinity.
- `loops_utils.h.src:21-54` treats exactly matching input/output strided ranges
  as allowed for SIMD. FLOAT division's scalar-broadcast branch at
  `loops_arithm_fp.dispatch.c.src:145-164` therefore supports both the separate
  first-pass output and the exact in-place second-pass row. x86 SSE, AVX2 and
  AVX512 arithmetic headers map multiplication/division to real packed
  MULPS/DIVPS families, with no reciprocal approximation. The implementation
  uses explicit SSE lanes, valid for the crate's fixed x86_64 GNU/Linux target.

## Dispatcher, generated source and native edges

The tagged `numpy/_core/meson.build:929-937,992-1000,1037-1045,1088-1089`
expands the three `.src` templates and emits `loops_*.dispatch.h`: FLOAT
arithmetic has baseline/X86_V2/X86_V3 variants; minmax additionally X86_V4;
unary-fp has baseline/X86_V2 and forces 128-bit SIMD. `generate_umath.py`
registers FLOAT add/multiply/divide/sqrt/maximum with those loop families.
This establishes source selectors, not the selected wheel's generated header,
loaded dispatcher choice or executed kernel. No generated NumPy header or
NumPy generator is needed to compile this fully expanded adaptation. There is
no NumPy build, template execution, package setup, bindgen or new dependency.

The added C11 unit includes compiler `<emmintrin.h>`, `<float.h>` and
`<stddef.h>`; a static assertion requires IEEE binary32. Its build requests
`-fno-fast-math`, `-ffp-contract=off`, `-frounding-math`; it adds no system
library, dynamic loader, libm call, subprocess or runtime file access. The two
published math C units and their flags are unchanged. Cargo manifest, real
Cargo.lock, all dependency versions/features, the 3 pinned math payloads and
the vendored BLAS ABI headers are unchanged. Later B2 must retain actual
compiler/system-header/flags/object/link receipts for this ONE changed native
edge; source text does not prove that compilation or those flags succeeded.

## Remaining exact-reference obligations

This code implements the full input arithmetic domain. It neither filters
nonfinite/subnormal/overflow values nor clips/replaces results. Its Rust zero
check is the existing Source check after independent second norms and before
ANY division, and returns the intact matrix owner. Existing rank shape/finite
checks remain with their original owner.

Actual selected NumPy-wheel dispatch, compiler-generated reduction code,
per-row versus multi-row SIMD batching, NaN payload/quieting and exception
flags, runtime epsilon-cast correspondence, rounding/FTZ/DAZ, OS vector state,
CPU, library core/threads and exact host build relation still require the held
P3 reference comparison. Lane-level source correspondence is not bit-identical
runtime evidence. No alternative CPU/backend is declared qualified, no ready
boolean is accepted, no numeric domain is narrowed to obtain agreement, and
no +/-1/tolerance acceptance is permitted. Both norm outputs, unrounded
similarities and strict integers remain exact-reference obligations.

Synthetic tests include an analytic pairwise-rounding case; its expected value
is derived from the selected source tree and IEEE rounding, not from running
this implementation or from an actual NumPy golden trace. These tests assume
round-to-nearest/even with gradual underflow and cannot qualify other floating
environments. They are authored and UNEXECUTED in this source candidate.

## Selected official source custody

All entries below were acquired as text from the exact official `v2.4.6` tag, with
one bounded 20-second request per selected member. Final URLs were the requested
raw GitHub URLs and every listed status was 200. No upstream commit or checksum
was assumed. Receipts and full selected source bodies remain outside git;
only this provenance and the unchanged license are vendored. One speculative
`numpy/_core/src/umath/meson.build` path returned 404 once; the actual required
`numpy/_core/meson.build` returned 200. No retry loop or repository mirror ran.

| Exact member | Bytes | SHA-256 |
| --- | ---: | --- |
| [`numpy/_core/src/umath/loops_arithm_fp.dispatch.c.src`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/loops_arithm_fp.dispatch.c.src) | 25061 | `75352da4b6a7bd3140d3893b969e3d17038446c9e25c15130f78c340238c95de` |
| [`numpy/_core/src/umath/loops_utils.h.src`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/loops_utils.h.src) | 8001 | `ed85d5b99f0b457cb7a316bf8c4acb884034bbbb81a89c5cedb62be80bf7c4f8` |
| [`numpy/_core/src/umath/loops_unary_fp.dispatch.c.src`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/loops_unary_fp.dispatch.c.src) | 7620 | `504df9a278921ea01a42b4c7690fd9c399217ded47589fd4968fdd9c96fa1929` |
| [`numpy/_core/src/umath/loops_minmax.dispatch.c.src`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/loops_minmax.dispatch.c.src) | 16344 | `8fc5e26c2072f6e5248e758f0582ad283988aee4e768dce54b9619fac591484f` |
| [`numpy/_core/src/umath/ufunc_object.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/ufunc_object.c) | 227206 | `5094f8f91c746b5070291bc41d4c1ab4c246e9ae77c3fb18516702a5918f1249` |
| [`numpy/_core/src/umath/scalarmath.c.src`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/scalarmath.c.src) | 60211 | `b1e443f49876c55232c193742713ac80175e5991a9b8d70893a915c51a4a50ce` |
| [`numpy/_core/src/common/simd/sse/math.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/sse/math.h) | 21644 | `89592fe0ec258f80b0a04c10db09cc876732b2aa3f34043287cbddcc8dc50764` |
| [`LICENSE.txt`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/LICENSE.txt) | 1543 | `1be1df33863f97a7bc1c4d67980bd6c69c9a6fef0a5ee76e6ad6cb91e56e8491` |
| [`numpy/_core/src/umath/reduction.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/reduction.c) | 16059 | `7885393f751abdeac7c04c16c861ed5e2bad4a3d7bfa70c8782fef062f6960f4` |
| [`numpy/_core/src/multiarray/abstractdtypes.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/multiarray/abstractdtypes.c) | 16355 | `488554ecadf77e56c9103a13bf39875fe622a4530506cbae44461f559dee27b4` |
| [`numpy/_core/src/common/simd/sse/arithmetic.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/sse/arithmetic.h) | 16514 | `13959d5e8427fb2d67da29797a1bb0ec5f1dd344001d37435562406567c4cdaf` |
| [`numpy/_core/src/common/simd/avx2/arithmetic.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/avx2/arithmetic.h) | 14914 | `d456bbbc37b24c1f16d6da593105fce41d4de70594de423d44c74be467fbcdfc` |
| [`numpy/_core/src/common/simd/avx512/arithmetic.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/avx512/arithmetic.h) | 19259 | `a3e3be7e9768a5d54da9c760b497f02c7b126d8e0d39c2bbbc82a63e0599b977` |
| [`numpy/_core/src/common/simd/avx2/math.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/avx2/math.h) | 13903 | `d9de71c1c8d6bad366cfc7ca9494820dad26a7efb5b7338b1167647dbbfa5c18` |
| [`numpy/_core/src/common/simd/avx512/math.h`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/common/simd/avx512/math.h) | 16837 | `4c94edd7a55982719b0e04db5591bf6be684f92f818dda87c9ed954364cdc021` |
| [`numpy/_core/meson.build`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/meson.build) | 43030 | `15130e151a73f7685f7dcc5818418c3ef36e0f5b8921340e1f40ca3efa43e5bb` |
| [`numpy/_core/src/umath/legacy_array_method.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/umath/legacy_array_method.c) | 14849 | `15701dc94e5c800ee5215c1a47c824b800e905888c43653c910224c9fb83bb9e` |
| [`numpy/_core/src/multiarray/dtypemeta.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/multiarray/dtypemeta.c) | 51474 | `1e2927de13b94be9ebe6c70c81a327495de122d07e6f58c22af1b90aa18b7483` |
| [`numpy/_core/code_generators/generate_umath.py`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/code_generators/generate_umath.py) | 53516 | `f1770ba92f43fea045f8637c3c7d7000daf94f6e4d2deab5402859f2f20be3ef` |
| [`numpy/_core/src/multiarray/calculation.c`](https://raw.githubusercontent.com/numpy/numpy/v2.4.6/numpy/_core/src/multiarray/calculation.c) | 22916 | `2838e6d0a1b4983dbb3161ed0c3065bf44e6eec4acd861a6344b7b13accc9a6b` |
