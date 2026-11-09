# Raw ORT inference component

This crate owns the selected ORT 1.29.0 C-API runtime, a real environment and
session, and synchronous batch-one inference. Its result is the **raw float32
bits** of metadata-ordered output zero (rank-three token zero or rank-two
direct), before FastEmbed's first L2
normalization. It does not implement `semantic-rank`'s `BackendSession` or
`BackendFactory`, supply an initialized-worker identity, normalize vectors,
compute scores, or qualify the full worker.

## Source and build boundary

The bridge compiles against the exact official
[v1.29.0 C API header](https://github.com/microsoft/onnxruntime/blob/v1.29.0/include/onnxruntime/core/session/onnxruntime_c_api.h).
`vendor/onnxruntime-1.29.0/` contains that header, its two directly included
companion headers, and the upstream MIT license, unchanged. Their SHA-256s are:

| File | SHA-256 |
| --- | --- |
| `onnxruntime_c_api.h` | `acc0cf4b3f28d39339c76770d76164bb7a0637dc89f5fde764b4017b632f6743` |
| `onnxruntime_error_code.h` | `5ce3b054e798eced8d14f5b86e98692fd33470463f96194ce0700a2d53dd8721` |
| `onnxruntime_ep_c_api.h` | `e6c986c9e98583f8113b2c6bc3864814883b806d501cf24da4d239c45753e235` |
| `LICENSE` | `2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c` |

The C compiler obtains the actual `OrtApi` layout from these headers. Rust sees
only the small owned bridge ABI in `native/bridge.h`; it has no hand-written
ORT function table offsets, generated bindings, downloader, or `ort` dependency.
The selected target is `x86_64-unknown-linux-gnu`, matching the retained wheel.
`build.rs` uses `cc = 1.11.1`, C11, system GNU/Linux headers and `libdl`.
`cc` performs compiler discovery and flag checks when a later build is admitted.
The other direct dependencies are the published local tokenizer and
`sha2 = 0.10.9`, with defaults disabled and `force-soft` enabled. This adds a
native bridge/build-script edge to the existing tokenizer closure.

The accepted Cargo-produced `Cargo.lock` is provided unchanged. It preserves all
116 existing dependency records and adds only this crate (117 packages:
111 registry, 6 local). Existing manifests and locks remain unchanged.
The selected isolated offline, locked component run compiled the C bridge and
passed all seven pure-data tests. Primary source review accepted the candidate;
landing preserved all 47 source/input pins and passed one `make check`. These
checks did not load the ORT library, construct a model session or run inference.
Actual runtime execution and qualification remain outstanding.

## Runtime ownership and binding

`Runtime::load` requires an explicitly absolute library path. It reads at most
28,501,849 bytes and accepts exactly 28,501,848 bytes with SHA-256
`8902e52a04bf6bf26f1613746811d6194f9c97ce7a6a7a28452b48522d3bd8d0`.
A caller-provided digest or version is never accepted as evidence. The bridge
copies those bytes into an owned Linux memfd, seals writes, growth, shrinkage
and further seal changes, and calls `dlopen` on that explicit descriptor path
with `RTLD_NOW | RTLD_LOCAL`. It resolves `OrtGetApiBase` with the documented
clear-`dlerror`/`dlsym`/check-`dlerror` sequence. It matches that actual symbol's
kernel `/proc/self/maps` device/inode to the sealed descriptor. A pathname-only
`dladdr` result would not establish this binding when an fd spelling is reused.
The Rust owner re-opens and hashes the sealed descriptor and checks its
size/device/inode again. It retains both original-file and mapped-file
provenance. The runtime must report exactly `1.29.0` and supply API 29.

This mechanism still requires Release's supported-host qualification: executable
memfd/seal and procfs availability, dynamic dependencies and their actual maps,
ABI/CPU dispatch, and the external mapped-library observation required by
[semantic/4](../../docs/plans/hybrid-runtime-contracts/semantic.md).
A sealed payload does not bind every dependent library. The reference executes
a different static ORT inside its Python extension; same version is not raw-bit
equivalence. There is no alternate/default/system runtime fallback.

The launcher must set `ORT_DISABLE_TELEMETRY=1` before process/runtime
initialization and remove loader search/preload/audit overrides. Before loading,
the bridge requires that telemetry value and rejects any present
`LD_LIBRARY_PATH`, `LD_PRELOAD`, or `LD_AUDIT`; it never mutates the global
environment. It then calls `DisableTelemetryEvents` on the actual environment.
Checking the current environment does not prove the launch environment or
absence of earlier preloads/initialization. The official `CreateEnv` contract
also permits reuse of a prior process environment. Fresh-process proof and the
actual no-write/no-connect observation remain qualification obligations.

Sessions hold an `Rc` reference to the runtime, making the owners `!Send` and
`!Sync`. Native session destruction precedes model-buffer destruction. All
borrowed tensor storage outlives synchronous `Run`; all native values, shape
objects, metadata type objects, options and allocated names are released on
success and failure paths. OrtStatus messages/codes are copied before release;
message bytes are retained without UTF-8 replacement. Cleanup failures are
appended to the original error. For errors encountered by runtime `Drop`, keep
the supplied `CleanupErrors` observer after all owners are dropped and drain
it. Such errors must remain visible to the composing worker; `Drop` cannot
return a `Result`.

## Session and raw-output behavior

`Runtime::session` constructs a real session from caller-owned model bytes,
retained immutably for the full native session lifetime. It computes their
actual hash and retains a provenance record. The supplied prepared tokenizer
contributes its actual consumed config/tokenizer/special-token bytes, whose
hashes are checked. These are inputs to a later complete derivation manifest,
not a replacement worker identity or proof of asset qualification. The selected
model must be self-contained, or every external initializer/other consumed
asset must be separately bound before model construction is admitted; this
component does not discover or stage external weights. That check remains an
explicit qualification obligation rather than an assertion inferred from a
caller-supplied model hash.

Retained FastEmbed 0.8 `common/onnx_model.py:103-112` sets graph optimization
`ORT_ENABLE_ALL` and intra/inter-op settings to 2. The bridge applies those
settings with checked statuses. It appends no providers: the API29 header's
`CreateSessionOptions` contract specifies the internal CPU provider in that
case. It explicitly sets deterministic compute to false, the documented
native default, and disables profiling. It leaves execution mode and memory
pattern at the native defaults that the Python wrapper leaves untouched, then
reads them through API29's `GetSessionExecutionMode` and
`GetMemPatternEnabled`. Their observed values are retained in `SessionSettings`.
The header documents that sequential execution ignores the inter-op setting;
neither configured setting is a whole-process thread cap.

The selected C API does not expose all native defaults as getters. CPU-arena
and remaining unobserved defaults retain an explicit `NativeDefault` obligation
instead of invented effective values. Their correspondence with the frozen
Python build and the complete effective-settings manifest remain open. Raw-bit
qualification must resolve this along with all other P2 settings; the settings
record is not a full derivation manifest.

Session metadata comes from actual owning-session input/output count, name,
type and shape APIs. There must be exactly one `input_ids`, with optional
`attention_mask` and `token_type_ids` only when advertised. Unknown or duplicate
inputs and incompatible dtype/rank/fixed dimensions refuse. The bridge uses
that session's input order and names, supplies owned int64 tensors of shape
`[1, sequence]`, and requests ALL outputs in that metadata order, selecting returned index zero.
The owned type-ID feed is zero, without rewriting original tokenizer Encoding
type IDs. Each sequence is 1..512 tokens and each text runs separately; there
is no padding across passages, deduplication or ordering change.

After synchronous inference the bridge checks tensor status, float32 dtype,
CPU-addressable memory, exact `[1, sequence, 384]` or `[1, 384]` shape and element count before
copying output storage. Rust checks the returned storage and uses the same
`extract_cls` function exercised by the pure-data tests to select token zero.
It copies bits without arithmetic, finiteness rewriting, epsilon, mean pooling,
zero-vector substitutes or normalization. `raw_cls_in_order` preserves the
iterator's query/passage order. Any native error stops that sequence; there is
no provider fallback, retry or substitute result.

## Verification boundaries

The authored pure-data tests cover actual metadata planning, optional/reordered
inputs, missing/duplicate/unknown inputs, dtype/rank/fixed-shape errors, owned
tensor lengths and zero type IDs, ordered first-output selection, CLS bounds and raw
signed-zero/NaN/subnormal/infinity bits. They use synthetic data and do not
mock or claim an ORT runtime/session/model. Native ownership, C/Rust ABI,
compiler/linker disposition and actual runtime errors need separate component
selection or protected runtime qualification as applicable.

All 294 exact tokenizer IDs/lengths must pass before any actual model/session
construction. This source supplies that construction code; it does not grant
permission to execute it. P2 must establish raw CLS bit equality against the
frozen reference with exact model/settings provenance. P3 must then implement
FastEmbed float32 L2 epsilon1e-12 and NumPy/OpenBLAS rank arithmetic, preserving
operation order and exact scores. P4 actual host/library/egress/resources and
P5 actual CLI/Go supervision remain open, together with the campaign's existing
C2/D1-A/D2/RTERM/P/W/uv/runtime/production holds. The actual-worker FIRST gate
is unconsumed. The component checks above do not establish native library load,
model execution, protected Python import, service behavior, database behavior or
deployment.

## S-P2-OBSERVE-1 output-order continuation

Selected FastEmbed `text/onnx_text_model.py:18,96-98` supplies output names None,
then consumes result[0]. Selected ORT Python wrapper
`onnxruntime_inference_collection.py:323-326` expands None to `_outputs_meta`
names in order. FastEmbed `text/onnx_embedding.py:311-322` selects rank-three
`[:,0]` and leaves rank-two output direct before first normalization. Accordingly
the bridge retains ordered names, requests all outputs, validates the first
float32 tensor, and releases every returned OrtValue on both success and error.
Auxiliary non-tensor metadata retains its ONNX type without a tensor cast.
Missing/incompatible first metadata fails before returning the session; a later
named output does not alter selection. Duplicate names retain their ordinals.
Batch/width/sequence bounds remain explicit, with sequence checked only for
rank-three output. The existing C/Rust output ABI adds a rank field and both
sides assert 48 bytes on the selected x86_64 target. Vendor API29 is unchanged.

Five new backend `p2_observe1_` metadata tests exercise the public pure-data
validation/extraction functions, including different first/later sentinel bits,
duplicates, missing outputs, invalid first dtype, rank-two/rank-three shape and
signed-zero/NaN bit preservation. They are authored, not executed here. Existing
component receipts precede this changed source and do not validate its ABI.
No settings/default/provider configuration was changed. The backend's new
model-closure preflight is conditional; direct ORT callers still require the
external model-asset admission described above.
