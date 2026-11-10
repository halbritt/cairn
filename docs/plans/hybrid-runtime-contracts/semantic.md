# Semantic worker contract

Owner decision `98d46758-c61c-42b4-9912-76cf9dfb0dc0@1` takes precedence over conflicting score, isolation, release-train and conditional model-load clauses in this contract.

Revision: `semantic/4`, proposed maximum-document evidence amendment to the
accepted `semantic/3`. Characterization baseline:
`d3c255a4bb75438e93527eaa1d83da780fd11eea` (2026-11-06).
The semantic team owns this file. Overall owns the shared contract index;
Release consumes the packaging, service-switch and rebuild requirements.
This document records acceptance criteria, not implementation or deployment
acceptance. X-01 specification setup is closed under `shared/2`; implementation
still follows each item's predecessor, review and evidence gates. The bounded
amendment below requires exact S-R1 and R-R1 review and Release's corresponding
criteria before numerical coverage is counted.

The [shared contract index](README.md) records cross-team decisions and the
X-01 gate. The [implementation plan](../hybrid-runtime-implementation.md) controls where
the earlier [proposal](../hybrid-runtime-consolidation.md) differs from source.
Keep characterization, a behavioral repair, Rust replacement, and production
cutover as separate reviewed checkpoints.

## Owners and delivery boundaries

| Plan key | Plane | Deliverable | Builder role | Dependencies |
| --- | --- | --- | --- | --- |
| S-01a | CAIRN-323 | Freeze the Python numerical/tokenization oracle and synthetic fixture inputs | S-B1 | Existing source; may characterize before X-01 |
| S-01b | CAIRN-324 | Characterize wire/process failures and add executable-worker comparison | S-B2 | Characterization may start now; the comparison extension waits for X-01 and criteria review |
| S-01 | CAIRN-322 | All three reference modes and the differential acceptance harness | S-B1, S-B2 | X-01 / CAIRN-302, S-01a, S-01b |
| S-02 | CAIRN-325 | Rust rank and stream modes behind Go supervision | S-B1 | S-01 |
| S-03 | CAIRN-326 | Rust embed mode, original-text spans and derivation identity | S-B1 | S-01; serialize shared Rust edits with S-02 |
| S-04 | CAIRN-327 | Offline worker/model staging, fixed launchers and native-library evidence | S-B2 | S-02, S-03, R-01 / CAIRN-303 for the supported-host inventory |
| S-04R | CAIRN-355 | Separately qualify a telemetry-disabled retained Python launcher/closure | S-B2; S-R1 and Release review | X-01, frozen S-01 reference, R-01 / CAIRN-303 |
| S-05 | CAIRN-328 | Switch/rebuild/reverse-rebuild tests and runbook | S-B2 | S-04, S-04R, R-01 / CAIRN-303 |
| HC-01 | CAIRN-329 | Accepted S-01 through S-04 and independent parity evidence | Semantic lead | S-01, S-02, S-03, S-04 |
| HC-02 | CAIRN-330 | Accepted semantic transition and observed installed re-index | Semantic and Release leads | HC-01, S-05, R-07 / CAIRN-309 |

S-R1 independently reviews criteria before implementation and exact candidate
commits before landing. No two builders write the same file concurrently.
The semantic lead owns this contract. The shared index assigns
`scripts/install-semantic.sh` and the first dedicated Cargo/build/actual-worker
Makefile targets to S-B2 after X-01. Overall transfers Makefile ownership to
Release only after those targets land. Release supplies host/architecture
requirements, reviews installer compatibility, and owns final activation and
service configuration. Later Rust ownership must name individual files before
work starts. Authors retain responsibility for review fixes, integration,
pushing main, and branch/worktree cleanup.

All eight semantic delivery/milestone items are direct children of CAIRN-301;
S-01a/S-01b are children of S-01. Native parent/external-ID readbacks are separate
from blocking edges. No relation tool is exposed to this session, so native
blocking edges remain unverified and dependencies are enforced in Plane bodies
and the campaign map.

## Existing process and wire behavior

These are current-source observations. Failure fixtures must preserve the
observable disposition; Python traceback wording is not a wire contract.
Any discrepancy found while recording an oracle remains visible and blocks a
dependent replacement until separately resolved.

| Surface | Current contract and owner | Source |
| --- | --- | --- |
| Worker selection | One absolute executable path, no configured argv. Fixed launchers select rank, stream, embed and a pinned local model directory. | [command.go](../../../semantic/command.go), [installer](../../../scripts/install-semantic.sh) |
| One-shot rank | One JSON request, 1–64 notes, query nonblank and at most 4096 UTF-8 bytes, total bodies at most 1 MiB, exact body hashes. Python's stdin cap is 8 MiB; Go caps stdout at 65536 bytes. | [semantic_rank.py](../../../scripts/semantic_rank.py), [command.go](../../../semantic/command.go) |
| Stream rank | Newline-delimited `id`/`request`, optional `cache` only when advertised. IDs contain 1–64 Python characters. Go sends at most 8 MiB before newline. Ordinary reply including newline is at most 65536 bytes; cache is separately at most 600 KiB. Matching IDs and a stable model/algorithm within each child are required; a fresh child after idle release is not pinned to the prior child's identity. | [semantic_rank.py](../../../scripts/semantic_rank.py), [stream.go](../../../semantic/stream.go) |
| Stream cache | `cairn.passage-vectors/1`, at most 128 passage hashes and raw 384-component little-endian float32 base64 vectors. Only a fresh worker may restore. Format/envelope bounds are checked before model identity; an otherwise bounded envelope from another model is ignored before vector decoding. Malformed same-model state refuses. Cache survives idle release only in the Go owner's memory; failure or close discards it. | [semantic_rank.py](../../../scripts/semantic_rank.py), [stream.go](../../../semantic/stream.go) |
| Embed request | Exactly `id`, `operation`, `text`; ID length 1–128 Python characters. A complete JSON line is at most 512 KiB. `identity` requires empty text, `query` at most 4096 UTF-8 bytes, and nonblank `document` at most 65536 UTF-8 bytes. Unknown keys and operations refuse. | [embedding_worker.py](../../../scripts/embedding_worker.py) |
| Embed reply | Complete line at most 4 MiB, matching ID, `identity`, and operation-specific projection/vector/passages. Identity is stable across query/document lanes and worker restarts. Go rejects unknown reply fields and trailing JSON. | [embedding_command.go](../../../semantic/embedding_command.go), [command.go](../../../semantic/command.go) |
| Model handshake | Python constructs its scorer/model/tokenizer before reading embedding requests. Missing or unusable assets/backend prevent the identity reply; a digest-only identity must not be treated as proof of an initialized embedder. | [embedding_worker.py](../../../scripts/embedding_worker.py), [semantic_rank.py](../../../scripts/semantic_rank.py), [index_service.go](../../../semantic/index_service.go) |
| Supervision | Go owns process groups, cancellation, bounded pipes and reaping. Rank/query worker timeout is 25 seconds, document timeout 90 seconds, stream idle release 30 seconds. StartIndex bounds retrieval calls to 2 seconds and document leases are 2 minutes. Busy calls refuse without queueing. | [command.go](../../../semantic/command.go), [stream.go](../../../semantic/stream.go), [embedding_command.go](../../../semantic/embedding_command.go), [index_service.go](../../../semantic/index_service.go), [index.go](../../../semantic/index.go) |
| Trust boundary | No database, API, credentials or orchestration in Rust. Go supplies a sanitized offline environment and discards worker stderr. This does not establish OS-enforced network isolation. | [command.go](../../../semantic/command.go), [stream.go](../../../semantic/stream.go) |

The oracle must record boundary inclusivity, newline handling, Unicode ID
length, duplicate/unknown JSON keys, truncated JSON, EOF and extra output.
Do not replace characterized permissiveness with an undocumented Rust rule.
Serialization whitespace and object-key order may differ. Python and Rust
derivation identities intentionally differ and each is checked against its
own frozen manifest. Other IDs/metadata, source hashes, spans, score values,
ordering and failure dispositions remain the preservation surface.
Rank/stream results retain `bge-max-chunk/1` and return score entries in note
input order. Embed retains `bge-original-passages/1`, dimensions 384, and
query-projection method `original-prefix/1`.

## Numerical, tokenization and identity criteria

Until S-04R is accepted, every Python reference, oracle, comparison and
transition execution requires actual network isolation and a disposable mapped
home/cache that also protects OS home-directory lookup. This includes existing
real-model tests, sequential rebuilds, API probes and execution through Go's
real sanitized supervisor, which removes HOME. A disposable HOME variable alone
does not establish that protection. Failed isolation stops the run. Apply the
same home-lookup protection to every Rust acceptance run through the real
supervisor. Prove the isolation wrapper changes no vector-affecting setting by
matching raw model outputs against the frozen reference. Preserve the actual
supervisor and its deadlines inside the external isolation boundary.

Freeze both Python worker source files before recording reference results.
Record source commit and hashes, fixture-input hashes, every model/config
digest, Python and package versions, CPU architecture, backend/provider,
thread settings, invocation and exit status. Store raw model outputs and
resource measurements outside the repository. Retain synthetic fixture inputs,
capture/comparison code and selected manifests in source; never replace the
reference implementation while measuring the candidate.

The baseline uses `fastembed==0.8.0`, CPUExecutionProvider, ORT intra-op and
inter-op settings of 2, and batch size 1 for `BAAI/bge-small-en-v1.5`.
These settings do not cap the whole process at two threads. Rank and embed queries
prepend the exact string `"Represent this sentence for searching relevant passages: "`,
including its trailing ASCII space. The scorer hashes `model_optimized.onnx`,
`config.json`, `tokenizer.json`, `special_tokens_map.json`, and
`tokenizer_config.json`, plus package versions and scoring constants.
Embedding identity additionally hashes both worker source files. The captured
reference environment uses Python 3.11.15, ORT 1.29.0, NumPy 2.4.6 and
tokenizers 0.23.2. Its installed FastEmbed postprocessor selects the first/CLS
token and applies L2 normalization with epsilon `1e-12`; the ranker then
normalizes its float32 query/passage matrix again before the dot product.
Preserve the prepared tokenizer's special tokens and WordPiece decoding,
including cleanup, rather than substituting mean pooling or another text
representation. S-01 retains the exact package/native-source hashes with its
reference manifest.

| Fixture family | Required assertion | Source |
| --- | --- | --- |
| Rank chunks | Token-decode chunks use window 384 and stride 320. The cap is 128 chunk occurrences across the entire request, including duplicates/cache hits; unique inference reuse does not enlarge it. | [semantic_rank.py](../../../scripts/semantic_rank.py), [cache tests](../../../scripts/test_semantic_cache.py) |
| Query treatment | Rank refuses a prefixed query above 512 tokens. Embed instead preserves an original-text prefix, re-tokenizes it and retreats at most eight times. Prefix bytes/hash and original/embedded token counts must match exactly. | [semantic_rank.py](../../../scripts/semantic_rank.py), [embedding_worker.py](../../../scripts/embedding_worker.py), [embedding tests](../../../scripts/test_embedding_worker.py) |
| Document spans | Original text uses window 128, stride 96, adaptive stride `max(96, (tokens + 255) // 256)` and window `max(128, stride + 32)`. Emit the final smaller window, 1–256 passages, each re-encoded within 512 tokens. UTF-8 offsets/lengths must identify exactly the original substring. Rank's decoded text is a different representation. | [embedding_worker.py](../../../scripts/embedding_worker.py) |
| Unicode coverage | Exercise ASCII, CJK, emoji, combining marks, whitespace-only and token-empty text, token-dense maximum bodies, and a short trailing decision. Compare every span and query projection; verify tokenizer-offset coverage without inflating spans to cover ignored bytes. For example, a trailing combining acute can lie outside every token offset. Nonblank tokenizer-empty queries can accept prefix-only inference while documents refuse. | [embedding_worker.py](../../../scripts/embedding_worker.py), [embedding tests](../../../scripts/test_embedding_worker.py) |
| Continuous values | For every reference/candidate embedding pair, `1 - cosine(reference, candidate) <= 1e-6`; separately compare query/passage cosine differences at `<= 1e-6`. Record finite/nonzero checks and normalization. Do not substitute this tolerance for exact rank-score parity. | [proposal](../hybrid-runtime-consolidation.md), [implementation corrections](../hybrid-runtime-implementation.md) |
| Discrete values | Preserve exact clamped, scaled integer scores and ranking assertions on `core/testdata/retrieval-quality.json`, repeated requests, shared passages and long-tail cases. Test positive/negative half-integer scaling with Python's ties-to-even rounding. An off-by-one score fails even when vector tolerance passes. | [semantic_rank.py](../../../scripts/semantic_rank.py), [existing comparator](../../../scripts/compare_semantic_workers.py) |
| Go validation | 384 finite, nonzero components; valid source spans and bounded passage count. Go currently has no `[0.99, 1.01]` norm rejection rule. Verify the worker's normalization separately. | [index.go](../../../semantic/index.go) |

The Python rounding requirement applies to rank and streaming-rank output.
The stored index computes scores with Go's existing `math.Round` after
PostgreSQL cosine distance; preserve and test that path separately rather than
silently changing its half-integer behavior to match Python.
Worker-order parity compares against the frozen Python worker; existing
compiler-level ranking assertions remain separate. Characterize reference
workload relevance misses without changing their scores or inventing a new
worker-only top-rank requirement to obtain acceptance.

Rust derivation identity uses the complete manifest below. Every consumed
model/config asset, implementation/compiler/runtime input and effective
vector-affecting setting is bound. Exclude timestamps and checkout paths;
the worker must not claim Python's derivation identity.

### Strict score arithmetic and indexed parity

The selected policy is exact discrete parity. There is no rounding-boundary
margin or permission for a one-unit score difference. Vector tolerance does
not discharge this requirement. Capture the unrounded reference similarities
through a harness around the frozen scorer, without editing its code.

The rank reference performs these operations in order:

1. ORT float32 CLS output passes through FastEmbed's float32 L2 normalization
   with epsilon `1e-12`.
2. Reconstruct the contiguous float32 matrix with the query first, then every
   chunk occurrence in source order, including cache hits and duplicate chunks.
3. Apply NumPy 2.4.6's float32 row norm and division again. Multiply the
   `(chunk occurrences, 384)` slice excluding the query row by the normalized
   query row, using its matrix-vector call. A per-row dot, float64 accumulation,
   single normalization or multiplication over only unique chunks is a
   different reference path.
4. Promote chunk similarities to float64 and take each note's maximum,
   starting from `-1.0`. Clamp each note maximum to `[-1, 1]`, multiply by
   `1000000` in float64 and apply Python ties-to-even rounding.

S-01 freezes the NumPy code/native-library hashes, loaded OpenBLAS/other math
library version and digest, selected CPU/kernel class, dispatch settings,
matrix shape/layout and operation trace with its arithmetic fixtures. For
identical raw float32 arithmetic inputs, the candidate's normalization and
unrounded similarities must match that executable reference bit for bit.
Actual model runs additionally pass the vector/pairwise-cosine targets and
exact final integer scores/order. Test the known alternative arithmetic paths
as negative controls. Test ties in isolated arithmetic fixtures, including
representable float32 similarities `±1/128`, `±3/128` and `±5/128`; do not
claim that a fabricated tie was observed in model output.

Run reference and candidate on the same host for each supported CPU class.
Cross-CPU bit stability is not assumed. A new CPU/kernel class requires its
own frozen reference and identity inputs before packaging acceptance. If
strict parity cannot be achieved, stop the affected migration and obtain an
explicit Overall decision; do not tune fixtures or relax the rule.

The initial Rust runtime prerequisite is ORT **1.29.0**, matching the frozen
reference build. The reference executes ORT statically inside
`onnxruntime_pybind11_state.cpython-311-x86_64-linux-gnu.so`, SHA-256
`a434baf53b7eba95c0c819eb67505099d9c8027e1fce4f3e25a0a368bc01045b`.
It does not execute the wheel's separate C-API library. The initial native
C-API candidate is `libonnxruntime.so.1.29.0`, SHA-256
`8902e52a04bf6bf26f1613746811d6194f9c97ce7a6a7a28452b48522d3bd8d0`;
that candidate is unqualified until the raw-output gate below passes. No
crate-bundled/default/newer runtime is an implicit substitute. Another release
or build is a recorded backend change under the stop rule, even if its
reported version is also 1.29.0.

Before accepting score comparisons, S-02/S-03 must demonstrate bit-identical
raw `last_hidden_state[:,0]` output against the reference for every S-01 model
input. Freeze the int64 input tensors, shapes, provider/session settings,
model digest and per-input output bit patterns. The initial 19 ranking cases
have 294 query/chunk occurrences, including duplicates; retain the unique
inference mapping as well. Extend the gate to S-01's query/document cases.
Use the actual loaded C-API/runtime, not fabricated raw vectors. A non-shipped
model probe may observe the same inference module/build inputs used by the
release worker. Preserve the observed 1.30.0-versus-1.29.0 drift as a negative
control. Report per-vector angle/component diagnostics, but the pairwise
`<=1e-6` requirement and exact scores/order remain binding in addition to this
raw-output prerequisite; no per-vector pass alone establishes parity.

S-03 also compares the indexed path on the same disposable PostgreSQL/pgvector
instance, with fixed source records and sequential Python and Rust rebuilds.
Through [Index.Search](../../../semantic/index.go) and
[compiler integration](../../../core/indexed_retrieval.go), assert every
hit's record/version/body hash/span/integer score, SQL hit order, compiler
returned order, query projection, discovery state and `Indexed/Eligible`
counts. Include near-ties, equal-distance record-ID ties, long queries, partial
coverage and the retained ranking fixtures. Go's `math.Round` and pgvector
distance calculation remain the indexed score owner; Rust emits vectors,
not indexed scores. These discrete assertions are exact as well.

### Derivation manifest

Under the owner's 2026-11-10 rule-C initialization policy, unavailable
bit-identity observations do not prevent the embedding worker from initializing.
The embedding manifest retains each missing or null value and adds a sorted
`unobserved_observations` array naming the unavailable fields:
`compilation.rustc`, `compilation.target`, `compilation.enabled_features`,
`compilation.effective_flags`, `compilation.linked_input_attestation`,
`session.cpu_arena`, `session.remaining_defaults`, `session.effective_provider`,
`native.ort_dependency_inventory` and `native.cpu_os_complete_class`.
No default, partial observation or fabricated value fills these gaps. A valid
observed empty value remains distinct from an unobserved value. The names and
retained input values participate in the canonical embedding digest; the
existing three-field wire identity remains unchanged because the Go decoder
refuses unknown fields.

This exception applies only to unavailable observations in those ten fields.
Invalid observed values, malformed parent objects, other unresolved owner gaps,
invalid canonical values and missing or invalid model-asset closure still
refuse. Actual asset/runtime mapping and construction errors remain terminal.
The rank identity gate is unchanged. Initialization does not establish rule-C
numerical acceptance, actual-session coverage or production qualification.

Rank and embed use separate versioned tags, respectively
`cairn.rust-rank-worker/1` and `cairn.rust-embedding-worker/1`.
Each SHA-256 binds a canonical manifest containing the following inputs.
The common input digest may be shared, but the final identities differ by tag
and mode-specific constants. Stream caches carry the Rust rank identity, so
bounded Python-cache envelopes from another model are ignored under the
existing restore ordering.

| Input | Required identity material |
| --- | --- |
| Implementation | Sorted relative paths and content digests for every crate Rust source file, build script, Cargo manifest/lockfile and toolchain declaration, plus non-secret effective build settings; do not select an undefined subset of relevant source files or read credential configuration. |
| Compilation | Exact rustc identity, target triple, enabled features, target-cpu/CPU features and effective vector-affecting codegen flags. Normalize path-remapping inputs; incidental build paths/timestamps do not enter the digest. |
| Native execution | ORT version reported at runtime, digest of the actually loaded ORT library and any native math library, CPU/kernel dispatch class and effective provider. A static link needs retained archive/linkage provenance and digests instead of a dynamic-path assertion. |
| Session | Effective optimization/execution/determinism options, intra/inter-op caps, batch size and input dtypes/layout. Record actual runtime values, including defaults that affect vectors. |
| Assets | Content hashes for every consumed model/config/tokenizer/special-token asset and the prepared model provenance. Pin Rust tokenizers to the reference wheel's 0.23.2 release; another tokenizer release is a recorded backend change requiring the same parity gates. |
| Algorithms | Prefix bytes; token/truncation/retreat/window/stride/passage limits; special-token/normalizer/WordPiece behavior; CLS pooling; normalization epsilon/dtype/order; rank arithmetic/kernel policy; dimensions and algorithm labels. |

Hash the compact UTF-8 JSON manifest with lexically sorted object keys and
relative-path entries, exact strings, decimal integers, and vector-affecting
floating constants represented as canonical decimal strings. Sort set-valued
feature lists; sequence-valued algorithm inputs retain order. The tag is an
explicit field. Absolute installation/library paths are provenance fields in
the release handoff, not identity inputs; their selected content digests and
effective settings are identity inputs.

F-IDENTITY swaps a same-version native library, changes a thread cap and
target-cpu/features, mutates each source/config/asset input, and moves an
otherwise identical build/model tree. Each vector-affecting change invalidates
the corresponding derivation; moves and timestamps alone do not. A missing,
unreadable or mismatched runtime/asset fails initialization rather than
falling back to another loader-path copy. Identity remains stable throughout
each selected worker lifetime and across the embed owner's lanes/restarts.

Compute the CPU/kernel class as SHA-256 of canonical JSON containing the target
architecture, endianness, pointer width, sorted effective process-visible ISA
features/OS vector-state capabilities, and the loaded OpenBLAS core name and
configuration when BLAS is used. Obtain enabled features through the platform's
CPU/OS capability interfaces and actual library getters, not a marketing label.
Retain the observed inputs with the class digest; feature-list ordering does
not change it, but a changed capability/core does. An unknown capability or
unobserved library core cannot silently become a shared default class.

The package handoff is a versioned `cairn.semantic-worker-manifest/1` document
with source/build identity, binary digest, rank/embed identities, the complete
derivation inputs, asset/library provenance and digests, target/ABI/CPU
requirements, and per-mode absolute launcher path plus launcher digest.
Release consumes this manifest; no credential or operational note belongs in it.

S-01 must provide a comparison path that executes a native candidate directly;
the current comparator always prefixes candidates with Python, covers only
rank, and records mismatches without making them a failing exit status. The
new acceptance entry point must fail for a mismatch while keeping the existing
exploratory behavior separately characterized. Acceptance runs the actual
Python and Rust models for rank, stream and embed. Missing
assets, launchers or required model tests fail the acceptance run; skips do
not count as parity. Cargo build/test/acceptance commands are dedicated targets
allocated through Overall, so ordinary Go checks continue without Cargo.

### Conditional maximum-document reference

The exact `"!" * 65536` Python capture reached its external 90-second budget
without a reply. This is **direct-supervisor characterization**, not an
observed real-Go or production timeout. Its full inherited environment was not
recorded. The one separately authorized, isolated, at-most-180-second oracle
attempt is consumed: it returned 256 normalized vectors in 100.394107 seconds.
Neither run used the actual Go supervisor. Preserve both immutable observations;
token geometry, file integrity and a matching identity do not qualify vectors.

S-R1's artifact verdict `378ded83-462a-47f1-82d8-f5243bef82db` version 1
verified the 31-file oracle seal
`ef04e09d5bb7d906740da0f6406b14b4de3d14a6dfe664d313ce8ebb87815a4d`
and report digest
`24c2b513af3cdd4076d8a1a1a5ed4b354d30bbe2b9c307fe96758c4ebb0e701f`,
while leaving numerical qualification open. The original bytes, environment
and remapped direct-Python command do not literally match Go's frame,
sanitized environment and fixed launcher. Normalized vectors do not recover
historical raw CLS or loaded-library mappings: normalization is non-injective.

Overall decision `0ba29009-f422-4a11-a94b-018f1d3139b0` version 1 selects the
following conditional route for **only this sealed case**. It grants bounded
experiments; it establishes no completed evidence or general equivalence rule.

1. Derive the execution prefix from sealed wrapper W under Overall's
   construction amendment `b8bde736-20e4-4957-88f3-2acc7109e4fb` version 1.
   W is the bwrap argv before `--` in the sealed
   `oracle-attempt.json`; it must match `sandbox_prefix` in
   `inventory/experiment-manifest.json` and the corresponding prefix in
   `preflight/full-isolation-command.json` under the seal above.
   Replace only the existing RW bind's host-source operand with a freshly
   created empty owned `0700` scratch directory outside the frozen artifact
   root. The original argv positions are `33: --bind`, `34: host source`,
   `35: /sandbox`; keep the flag and guest destination unchanged. Apart from
   that operand and declared read-only tool/input additions, preserve all
   original arguments, guest paths, namespaces/settings, environment and mount
   flags. Call the result a **declared derivation**, never byte-identical W.
   Record original/derived argv and their exact diff. Resolve and verify the
   backing's owner/mode/emptiness without symlink substitution or overlap/alias
   with the seal or protected live model/home/cache/telemetry trees. Do not
   recreate the old disposable directory beneath the frozen root. Keep every
   old file/directory unchanged, including unsealed delivery material; all new
   logs/results/receipts stay outside it and old evidence is mounted read-only.
   Record before/after seal and ownership/mode checks and final cleanup; remove
   only the newly owned scratch after workers/descendants are joined.
   This host-path change establishes no numerical equivalence. Preflight and
   the 294-text raw proof apply to this **derived construction**. It grants no
   new attempt, budget reset or retry.
   Before inference, prove real network isolation
   and disposable mapped OS-home/cache protection, including `getpwuid` and
   `expanduser` with HOME absent in the relevant namespace. Keep frozen sources,
   assets, interpreter/provider, batch 1, ORT settings and tensor
   shapes/masks/type IDs. A failed isolation, digest or runtime check stops the
   experiment and retains the gap. Every actual experiment attempt, including
   its preflight, is recorded. A failed proof or a failed, uncertain or
   interrupted attempt ends that experiment; further execution requires a new
   Overall decision. Avoiding an automatic retry does not grant a manual one.
2. Pin the complete pre-decision 294-text baseline digests before executing
   the already-scoped baseline capture. Require exact matches for int64 input
   IDs, lengths and raw float32 CLS. The recipe is the first 19 cases of
   `scripts/compare_semantic_workers.py` at `d3c255a`: the 17 queries of
   `core/testdata/retrieval-quality.json` in file order, then its long-tail and
   64-short cases, excluding the two repeat-storage cases. Pin workload SHA-256
   `30b9b9852c91bac38836c8d121244ee3e762445ac505d5845dc454bae90eaec0`.
   For each case, use the exact query prefix specified above plus the query,
   followed by every frozen `semantic_rank.chunks` occurrence in note/chunk
   order, including duplicates. Use FastEmbed's own tokenizer `encode_batch`,
   one text at a time, masks of ones, type IDs of zeros and its own session.
   CLS is output 0, `[:,0]`; concatenate arrays little-endian in text order.
   Match the input-ID and length digests from tokenization **before any model
   inference**, so a recipe error cannot consume a model attempt.
   The accepted digest basis from S-R1's
   `b31016f5` version 1 is:

   | Baseline bytes | SHA-256 |
   | --- | --- |
   | 33395 token IDs, little-endian int64 | `1762a94e5b785ffcfa300f404252994026b18a71f763086f297f89a9809394a9` |
   | 294 lengths, little-endian int64 | `a81f2c9f819d10049549d14dcdb38983febf0a41cbfe314421e4f70bba8ded33` |
   | Raw CLS, little-endian float32, 294 by 384 | `1f7ba8c969c2053385ce6e89528d2decea78b627e9fb6ef40cfc322fd760b623` |

   The reported 33395-token count comes from S-R1's `378ded83` version 1.
   Those earlier arrays were unsealed volatile scratch, not retained artifacts.
   A match is corpus-bounded wrapper evidence, not proof for every input.
3. After that match, perform exactly two additional passage-level executions:
   A is `"!" * 288`, B is `"!" * 256`. Capture raw CLS and FastEmbed's normalized
   output from **each same execution**; calling `embed` and then a second raw
   `session.run` is forbidden. Retain per-input tensors and the mapping
   A to rows `0..254`, B to row `255`. Require normalized row hashes
   `18f797c69cbe3aa177a9ea7c93accca95ef30124adc8fbedfd5ce807f164c907`
   and `ebaa34e553e0e36d1b60b31f485f48b831353d1ab1091042d2ee0e173e06b632`,
   respectively. Each row hash covers 384 little-endian float32 components,
   exactly 1536 bytes. Read `/proc/self/maps` in the actual Python worker and
   hash its loaded images against these pinned reference digests:

   | Loaded image | SHA-256 |
   | --- | --- |
   | ORT Python extension | `a434baf53b7eba95c0c819eb67505099d9c8027e1fce4f3e25a0a368bc01045b` |
   | NumPy `_multiarray_umath` | `1535fbed1e9ac6b717e72ad9bd5c20b9f0536a21084fcadc40c00255a8f35d1d` |
   | OpenBLAS | `05c9f9eb89ee68a4b9d673184fa91c99587e736392c0c2d49180a8aa5303d080` |

   These raw values and mappings are **new references and observations**;
   never describe them as reconstructed historical evidence.
4. Reusing A's row for 255 passages requires the exact retained row mapping
   and frozen batch-1/stateless-source analysis. It is not empirical proof of
   255-repeat determinism. Any stateful, batch-dependent or vector-affecting
   contradiction defeats the disposition. Source-equivalence arguments for
   the old request bytes, environment and launcher remain explicit and
   input-specific. They do not become literal compliance or apply generally.
   Fixture provenance enumerates and reviews each concrete O-4 through O-6
   difference with source commit and file:line proof before counting coverage:
   Go's compact UUID-ID/newline frame versus the oracle's Python serialization
   (`semantic/embedding_command.go:111-128`); the oracle's omitted
   `CAIRN_SEMANTIC_STREAM_CACHE=1` and added HOME/XDG/TMPDIR/cache/bytecode/PWD
   settings versus Go's launch environment (`semantic/stream.go:174`); and its
   remapped direct `python -B` command versus the fixed installed launcher.
   The old 90-second run, specifically, lacks a full inherited-environment
   record; the oracle's environment is recorded. Unknowns remain explicit.
5. Once steps 1-3 all pass and the exact S-01b isolation/recording harness
   candidate is independently accepted, execute **one** request whose operation
   is `document` and text is exactly `"!" * 65536` (65536 UTF-8 bytes), through
   the unchanged Go `CommandEmbedder`, selected installed pre-repair embed
   launcher, actual Go-generated frame and sanitized environment, with
   the existing 90-second deadline. Normal startup/identity exchange is allowed;
   no extra model/document probe or repeated request is implied. Keep the
   external isolation boundary and report its overhead separately; it cannot
   replace or extend Go's timer. The selected launcher is the installed
   `semantic/worker-embeddings`, SHA-256
   `f63e0a943e1607e0b24e960206e67d9161290115c9999447bd133305b56b5aa8`;
   its exact absolute host path and argv are pinned in the external S-01
   `semantic-oracle-installed-reference-selection.json`, SHA-256
   `0531831177d52dcd11fe86a3790cbbd0825a0e35b14db04d35fc0a391e6e5f41`.
   The execution plan binds this selection plus the built Go artifact.
   It is never S-04R's staged telemetry-disabled selection.
   Mount the selected runtime read-only and verify launcher/source/interpreter
   digests before and after. Operational paths/manifests remain outside git.
   Before this request, repeat network and HOME-absent OS-home/cache preflight
   in the actual Go child's namespace. Record namespace/UID correspondence
   with the observed model child; identical prefixes alone are not that proof.
   Failure ends the experiment with the gap retained.
   Record Go source/binary identity, interpreter digest, launcher argv/digest
   and externally sampled actual-child ORT/NumPy/OpenBLAS mappings and hashes.
   Also record the exact Go frame hash, UUID, decoded operation/text matching
   the sealed input, and Go's four environment variables plus
   `CAIRN_SEMANTIC_STREAM_CACHE=1`; distinguish runtime-added entries. An
   inference-free recording stub may establish the frame/environment facts.
   Record cleanup,
   cancellation and whichever result occurs. A timeout is bounded isolated
   real-Go evidence; a successful reply is a separate immutable artifact.
   Compare a reply with the sealed oracle and escalate differences rather
   than replacing either reference. The old direct-supervisor failure never
   becomes an observed real-Go timeout by relabelling it.
   Seal both the new B1 reference sets and B2 record with digest manifests.
6. Obtain exact S-R1 and R-R1 review of this criteria amendment and Release's
   corresponding delta. Send accepted revisions and proposed shared-index
   wording to Overall before counting numerical coverage. Review the actual
   experiment evidence separately. No new 180-second oracle, deadline
   inflation, production action, source-oracle repair, backend/tolerance/role
   change or automatic retry is authorized by this route. Escalations go to
   Overall, which owns population dispositions with Semantic/Release review.

Keep all 256 Rust vectors and spans, the raw-output gate, pairwise `<=1e-6`,
exact score/order and unchanged real-Go 90-second execution mandatory. The
oracle vectors alone do not make the source embeddable or covered for Python.
Experiment outputs, including a B2 reply, are reference artifacts and never
index coverage. Until the authorized Python real-Go run returns a valid
successful document reply, treat a Rust reply within 90 seconds as a population
difference requiring Overall's disposition with Semantic/Release review.
Blocked, failed or unmeasured Python runs never establish equal populations.
Record and obtain a disposition for any other differing population. A faster
Rust reply need not mimic the old direct capture's slowness. Retain every
unresolved cell until its specified evidence exists; this authorization does
not pass a coverage, runtime or deployment gate.

For Rust comparisons, the retained spans are `raw/oracle-spans.json`, SHA-256
`485076a424b842c3634c1f6bd0ce70968b91aa5ff428ce0d05e8b462df37dd4b`;
normalized rows come from `raw/oracle-vectors-f32le.bin`, SHA-256
`e29bf3eb56c889c7fcd9a4592179a7fd783e2a587c785152ab2704136e86e64f`,
through the A/B mapping. Raw values come from the new sealed B1 references,
subject to the conditional evidence gates above.

### Required acceptance inventory

S-B2 supplies a phase-selecting acceptance entry point and dedicated Makefile
targets after X-01. These are requirements for new tooling. They are not
claims that a Cargo target or strict runner already exists.

The runner reads a committed, reviewed fixture/test inventory with names,
expected run counts, applicable phase, required assets/environment and
reference input hashes. It never derives success expectations from whichever
tests happened to run. A missing expected case, failed command, mismatch,
required `skip`/`ignored`, or zero-ran suite fails the selected phase. Parse
Go `-json` run/skip/pass events and Python unittest class/method run and skipped
counts. Enforce equivalent nonzero named coverage for required Cargo suites.
The tested inventory changes only with the owning item and independent review.

| Phase / owner | Required inventory and expected positive counts |
| --- | --- |
| Reference / S-01 | Prepared-Python `PassageContractTest` 5, `SemanticStreamTest` 4, `SemanticCacheTest` 9 and `SemanticResumeTest` 5: 23 tests, no skips. Run the actual tokenizer test. Capture all three real Python worker modes. The 17 retrieval-quality queries plus long-tail and 64-short cases give 19 rank requests / 269 note scores; stream cold/warm repeats give 38 successful replies plus a separately counted idle-restore case. Every failure-family case has its own inventory ID and expected refusal tuple. |
| Reference embeddings / S-01 | Nine named positive query cases: ASCII, CJK, emoji, combining marks, special tokens, exact-512 tokens, over-512 tokens, exact-4096 UTF-8 bytes and tokenizer-empty/nonblank. Six named positive documents: ASCII, CJK, mixed Unicode/combining, trailing decision, maximum Latin and maximum CJK. Exact projection/span/vector counts are frozen per input. The maximum-punctuation direct-supervisor timeout is retained separately; its conditional raw-reference and one real-Go experiment gates above remain open until evidenced. |
| Rank / S-02 | Reference/candidate arithmetic fixtures, all 19 exact-score cases, all 38 stream replies and the idle/cache-restart case; real `Command` and `StreamCommand` supervisor runs, not only script doubles. Add those native-model process tests; no real-model rank/stream Go test currently exists. The 21 current non-DB/non-real-model semantic tests remain separately required process/unit coverage. |
| Embed and index / S-03 | All nine query and six document comparisons; the two configured real tests `TestRealMaximumDocumentEmbeddingCompletes` and `TestRealProjectedLongQuery`; `TestRealPersistentPassageDiscovery` and the three remaining index/store tests with a disposable DSN. Add the sequential Python/Rust indexed-comparison matrix above, with all 17 workload queries and the nine query-boundary cases. |
| Package / S-04 | For `N >= 1` supported CPU classes, all three modes run both as clean offline artifacts and through real Go supervision: `6*N` positive runs. Separately count live-staging rerun, wrong/missing library, wrong/missing asset, digest mismatch and shadow-system-library cases per class. Inventory must cover every managed host running a semantic service; an empty list cannot pass. |
| Transition / S-05 | Ten named cases: full forward/reverse rebuild; mid-rebuild restart; worker death/deadline; edit during embedding; delete/forget during embedding; expired lease/retry backlog; partial versus unavailable coverage; competing collection writers/inactive units; unembeddable-population refusal; default-selection/whole-API-restart/rollback handoff. Each runs real workers and disposable PostgreSQL, with declared subcase counts. |

S-01 accepts the frozen Python reference and tested harness mechanics, not an
unimplemented Rust model. Its native-executable-path self-tests may use an
explicit process fixture, labelled as such. S-02/S-03 and HC-01 require the
actual Rust models; a process fixture never satisfies those phases. This
separates the gates without making S-01 depend circularly on S-02/S-03.

Preflight checks the prepared Python/NumPy, tokenizer, immutable baseline and
candidate launchers, all model/runtime assets, and the owned disposable DSN
when required. Configure `CAIRN_EMBEDDING_WORKER`,
`CAIRN_EMBEDDING_TOKENIZER`, `CAIRN_TEST_DATABASE_URL`, and rank/stream worker
selectors for their applicable tests; default `make test` or `make
test-integration` does not establish model coverage. The strict runner
explicitly runs the real rank/stream API probes currently conditional in
`scripts/test-postgres.sh`. It fails before counting a missing selector as a skip.

`TestDocumentationRetrievalQuality` is an opt-in lexical/no-model measurement,
as its source states. If selected, set `CAIRN_RETRIEVAL_REPORT` and a disposable
DSN and require its one test to run; it does not count as a model test or turn
authored relevance observations into new acceptance thresholds. Preserve all
existing compiler ranking assertions separately.

Negative runner self-tests remove each required selector/asset, inject a Go
skip event, a skipped Python class, a zero-ran suite and a missing named case,
and introduce score/span/order mismatches. Every control must produce a
failing acceptance exit. Dependency removal is simulated in isolated fixtures,
not by uninstalling packages from the live reference environment.

### Decoder and tokenizer compatibility

Preserve Python 3.11's whitespace predicate for rank query, embed query,
document and retreat-prefix blank checks. Freeze its full character set,
including U+001C–U+001F, and test U+0085, U+00A0 and U+3000 as well. Rust's
default trim predicate does not establish parity. Freeze BertNormalizer
lowercase/clean_text/handle_chinese_chars/strip_accents, WordPiece's 100-character
word bound, special tokens and decoder cleanup. Include chunks starting with
`##` subwords and punctuation affected by cleanup; decode and re-encode the
actual passage text, not the original token-ID slice.

Go-generated typed UTF-8 frames are the operational consumer path. Record the
Python worker's additional direct-input behavior separately: duplicate keys,
ignored rank/note keys, unvalidated echoed version types, NaN/Infinity tokens,
lone-surrogate escapes, BOM and UTF-16/32 bytes. The selected compatibility
policy preserves each observed accepted/refused disposition in worker-boundary
fixtures; do not infer that all such frames succeed in every mode or survive
Go's downstream validation. Any proposed narrowing requires an explicit
Overall contract decision before the affected implementation. Go reply field
matching is case-insensitive, duplicate fields keep the last value, and BOM
replies refuse; Rust's canonical replies do not alter those Go semantics.

## Required failure evidence

The assigned review checks these fixture families against source before code:

- **F-RANK:** zero/65 notes, empty/oversize query, wrong body hash, body-byte
  overflow, 128 versus 129 chunk occurrences, decoded chunk above token
  bounds, malformed JSON, and nonfinite/zero inference vectors. Include
  token-empty notes that refuse the whole request, a query above 512 model
  tokens but within 4096 bytes, one-shot input above 8 MiB, structural errors,
  ignored extra request/note keys and unvalidated echoed version types.
- **F-STREAM:** incomplete/oversize lines, wrong/absent response ID, missing
  result, unexpected/trailing output, second-frame failure, identity change,
  cache omission, corrupt cache, wrong-model cache, warm/cold score equality,
  actual idle exit/restore, and cache discard after failure/close. Include
  missing/extra envelope keys, wrong/empty/over-64-character IDs, unadvertised
  cache, late restore, entry/hash/base64/finite/nonzero/size checks, and the
  format/size-before-model restore ordering. Check identity within a child;
  a new idle-replacement child may report another identity.
- **F-EMBED:** all three operations, wrong request fields/types, Unicode ID
  boundaries, bad query projection, long-query retreat refusal, 65536-byte
  documents, invalid UTF-8 span boundaries, too many passages, bad dimensions,
  NaN/infinity/zero vectors, and model change after worker restart. Include
  blank queries, blank/token-empty documents, identity with nonempty text,
  missing newline and above-512-KiB input. Preserve Go's acceptance of an
  omitted query projection for legacy replies; the replacement worker itself
  emits the current complete projection.
- **F-PROCESS:** blocked input/output, output caps, early exit, query/document
  concurrency, busy refusal, earlier caller deadline, owner shutdown and
  descendants holding pipes. Observe process-group termination and joining;
  a fake in-process worker alone cannot establish these claims.
- **F-IDENTITY:** mutate each asset, each relevant implementation source and
  vector-affecting setting/version; derivation identity changes. Moving an
  otherwise identical build or model tree and changing timestamps does not.
- **F-PACKAGE:** offline startup without a checkout or Python runtime, all
  fixed launcher modes, absent/wrong native library and assets, digest
  mismatch, installation refusal paths and reverse configuration.
- **F-REBUILD:** Python→Rust→Python, service restart during rebuild, worker
  death/deadline, edits/deletes while embedding, expired leases, retry backlog,
  and partial versus complete dense coverage in disposable PostgreSQL. Include
  competing writer/service configurations for different collections sharing
  the same database queue, plus inactive definitions that could restart an old
  generation; observe exclusion before replacement effects. Current OpenIndex
  itself does not reject a second model generation, so this fixture must prove
  the switching controller's stop/join behavior rather than invent that store
  guarantee.

For the pinned tokenizer, no real input is yet shown to make a decoded rank
chunk or re-encoded document passage exceed 512 tokens. Exercise those guards
with an explicitly labelled tokenizer test double; do not count them as
actual-model cases or invent a reachable production input.

Each failure fixture records a disposition tuple: Go error/result plus
discovery state/coverage where exercised; process exit/kill and stdout prefix;
cache retained/discarded state; and restart count. Python refusal exits are
not structured error replies. A second unsolicited stream line can fail the
next exchange rather than the first. Sparse one-shot replies accepted by the
transport are distinguished from downstream semantic validation failures.

Real-worker faults use harness-owned SIGSTOP/SIGKILL, earlier caller deadlines,
pipe pressure and private wrapper launchers that execute the actual worker.
Record the real binary/identity and observed process tree. No fault command,
environment switch or launcher-selectable fault hook is shipped in a release
worker. Test-double protocol checks remain separately labelled.

Existing anchors include [command tests](../../../semantic/command_test.go),
[stream tests](../../../semantic/stream_test.go),
[cache process tests](../../../semantic/stream_cache_test.go),
[embedding process tests](../../../semantic/embedding_command_test.go), and
[index tests](../../../semantic/index_test.go), plus
[busy-start tests](../../../semantic/busy_test.go) and
[deadline tests](../../../semantic/deadline_test.go). Record which assertions run
against real assets/processes and which isolate a protocol boundary with a
test double. Characterization does not silently repair the oracle.

## Packaging, index transition and rollback

The selected backend is ORT. S-04 records locked crate/runtime versions,
model/config provenance and checksums, target architecture/ABI, CPU features,
native-library paths and dependencies, and clean-environment execution on
every host architecture declared supported by Release. A packaging failure
reopens the backend decision with Overall; it does not authorize a silent
Candle substitution or tolerance relaxation. Do not describe the worker as
static until actual linkage establishes it.

The Release inventory is nonempty and names every managed host with an active
or restartable semantic service, its architecture/ABI and CPU/kernel class.
An unreachable/uninspected host remains a gap. S-04 depends on R-01's inventory
and cannot pass by declaring no supported targets. Compare reference and
candidate numerics on each supported CPU class.

Stage worker/model/native-runtime payloads and mode launchers into versioned,
content-addressed immutable paths. A live service configuration selects one
specific absolute launcher, never a mutable `current` symlink or stable path
whose target/content is rewritten. Each launcher selects an immutable payload
and explicitly supplies mode, model directory, thread caps and native-library
location; it cannot depend on inherited HOME, LD_LIBRARY_PATH or thread
variables, which Go removes. Keep payload and rendered-launcher addressing
separate so hashing an absolute launcher does not create a self-referential
generation digest.

Staging never rewrites/re-targets any live-selected file/symlink and never
updates the existing Python venv, source workers or model cache. An existing
content-addressed path with different bytes refuses; it is not repaired in
place. Activation selects a new path through Release's controlled configuration
change/restart after the writer fence. Re-running staging against a disposable
running service must leave its executed launcher digest and identity unchanged,
including after idle/failure respawn.

Execute every mode through the real Go supervisor environment as well as clean
offline packaging fixtures. Observe the loaded ORT/math-library path in
`/proc/PID/maps` and hash the actual file; a declared path or ldd output alone
is insufficient. Demonstrate that a shadow system library is not selected and
that absent/mismatched selected libraries fail. Retain provenance/digests for
any ORT binary fetched at build time. Static linking requires actual linkage
evidence. Runtime builds exclude download features not needed for local assets;
network-isolated acceptance uses a namespace with no external interfaces/routes.
That test confinement does not claim production Go workers already have an
OS-enforced network sandbox.

Explicitly disable ORT telemetry in the Rust launcher before process/runtime
initialization (`ORT_DISABLE_TELEMETRY=1`) and through the runtime's
`DisableTelemetryEvents` facility as applicable. Acceptance must observe zero
worker `connect()` attempts, including failed attempts, and no telemetry-store
creation or writes with an initially empty disposable HOME/cache. Use syscall
or equivalent egress/write observation scoped to the actual worker process
tree; an interface-free namespace or absence of one sampled socket is not
this proof. Negative controls establish that the observer detects a connect
attempt and a store write. Go's own API/database traffic is outside that scope.

S-01b separately records the unmodified Python reference's telemetry behavior:
the observed `~/.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db`
store with `events`/`settings` tables, the OneCollector endpoint
`https://mobile.events.data.microsoft.com/OneCollector/1.0/`, and the
`ORT_DISABLE_TELEMETRY` switch in the pinned runtime. Current observations establish
persistent store writes, not a verified upload. Inspect only selected metadata
and disposable captures; do not copy the owner's event payloads/device IDs.
Changing the reference or Python rollback launcher is a separately reviewed
behavior repair and requires a recorded Release/Overall disposition. The Rust
no-write/no-connect gate does not silently claim the legacy closure already
satisfies it.

Overall selected S-04R / CAIRN-355 as that bounded repair. It stages a new
immutable Python launcher/closure with the verified telemetry-disable setting;
it never changes the frozen oracle, existing installed source/venv/model cache
or current service selection during characterization. Independent S-R1 criteria
and exact-candidate review and Release rollback review precede acceptance.
Use actual network isolation and a disposable mapped HOME/cache/telemetry
location, also protecting OS home-directory lookup when HOME is absent. Failed
isolation stops an experiment; it never falls back to the owner's store.
Apply the same zero-store/zero-connect process-tree observation and observer
controls above, including every mode through the real sanitized Go supervisor.
Compare numerical/discrete outputs, spans/projection and both identities against
the frozen reference. A launcher-only setting must prove the expected identities
remain exact. If source changes are needed, record the resulting derivation
identity and explicitly revise and review rollback expectations.
Any added wrapper or sitecustomize code is included in the closure manifest,
because the existing Python derivation identity does not hash that code.
S-05 rollback acceptance and R-06 / CAIRN-308 qualification require accepted
S-04R evidence.
Release retains final staging/activation control; X-01 names this downstream
gate without claiming its implementation is complete.

The current installer stages `semantic/venv`, source workers and fixed
`worker`, `worker-stream` and `worker-embeddings` launchers. Inventory the
actual installed paths and active configuration before changing them. Preserve
Python rollback artifacts until the release retirement gates pass. Release's
retained rollback target remains the qualified Python closure after retirement;
Semantic supplies its interpreter/venv/source/model/native-library/launcher
facts. Do not silently substitute a previous Rust generation as that target.

[Schema 056](../../../core/schema/056_semantic_passages.sql) keys documents
and jobs by record and passages by record/ordinal.
[OpenIndex and Step](../../../semantic/index.go) use the shared
`cairn.semantic_job` table without a collection predicate. Publishing a new
derivation deletes the old document. Therefore:

1. Release inventories every writer sharing the database/queue, stops it and
   joins indexing and worker processes before starting a different model
   generation. Stopping only one collection's named service is insufficient
   if another process shares the queue. Include inactive/restartable alternate
   service definitions and prevent old generations restarting during the switch
   and rebuild. Apply the same exclusion to reverse rebuilds.
2. Switch through fixed executable launchers and a controlled service restart.
   Keep the existing schema. Measure whole-API outage as well as semantic
   availability and rebuild duration: restarting the current combined API
   service affects more than its semantic lane. Characterize a cold query-lane
   restart inside StartIndex's two-second retrieval budget. Report lease/backoff
   state and prove that no old-identity document is published after the fence;
   an expired/empty lease set alone does not prove writers were joined.
   Uninterrupted dense coverage and instant rollback are not acceptance claims.
3. Exercise both directions on a disposable store. Observe labelled
   unavailability when the backend cannot serve, and separately report the
   indexed/eligible counts for partial coverage. A ready index with some
   embeddings does not prove per-record lexical fallback for missing vectors.
4. Prove completion by comparing every eligible active current source's
   record/version/body hash against the selected model derivation and passage
   rows. An empty job queue, successful query or ready status is insufficient.
5. Roll back by stopping/joining Rust writers, restarting the pinned Python
   configuration and completing the reverse rebuild. Preserve operational
   source data; no production database is a test fixture.

The completion population is exactly OpenIndex's database-wide seeding set:
every active record's current version with no payload-deletion marker, across
all collections. For each source require its current version/body hash,
selected model identity, and `1..256` valid passage rows with contiguous
ordinals `0..n-1`, valid UTF-8 spans and finite/nonzero 384-component vectors.
Document-only rows or empty/missing passage sets fail completion.

List unembeddable records and retained failures/backoff with reasons. Compare
the Python and Rust failure populations; a difference requires a recorded
disposition. Never silently remove these records from the denominator or
delete/re-scope them to obtain a complete rebuild. A nonempty unresolved list
blocks a full-coverage claim, even if both implementations refuse the same
record. Negative disposable fixtures prove that the completion gate refuses
Python-blank/token-empty records instead of reporting success forever.

The pinned Python rollback configuration is its preserved executable
interpreter/venv closure, exact worker sources, prepared model/config snapshot,
native libraries and launcher bytes, not a new installation of
`fastembed==0.8.0` with freshly resolved dependencies. Record exact pre-cutover
rank and embed identities, qualified by S-04R, in the release manifest and assert both on reverse
selection before rebuilding the full population. Release owns retention and
offline restore qualification; Semantic supplies these identities and assets.

S-05 supplies the reviewed default-selection/configuration delta and reverse
selection for the fixed launchers, retaining the service's selected rank,
stream or embed mode. Release owns the actual shared service configuration
write and final activation. Exercise that handoff in disposable services;
there is no concurrent Semantic/Release writer for production configuration.

S-05 requires `make test-integration` and `make check` for store changes,
focused process/model checks, and lifecycle checks when service lifecycle or
backup/restore behavior changes. HC-02 additionally needs Release's exact
installed-source/binary/model manifest, active-service evidence and completed
re-index evidence. Setup acceptance cannot close S-01–S-05 or either milestone.

## Cross-team agreement and remaining acceptance

- The shared index adopts database/queue-wide exclusion and assigns S-B2 the
  semantic installer and first Cargo targets. Release's `release/2` draft
  agrees on the binary/model/native-library identity manifest, architecture/ABI
  requirements, fixed launchers, activation ownership and reverse rebuild.
- Release's R-01 / CAIRN-303 supplies supported hosts/architectures; its
  R-07 / CAIRN-309 supplies installed cutover and completed re-index evidence.
  Those evidence gates remain open.
- Review records acceptance or concrete missing fixtures against this revision;
  any contract change is sent to all consumers before dependent work merges.
