# Native rank backend composition source

This local crate connects the published rank, tokenizer, ORT and math owners.
It ships no CLI, installer, worker handshake, Go routing or deployment change.
The generic `run_rank_once` and `run_rank_stream` entry points are re-exported;
the rank engine calls `NativeFactory::construct` only after its existing
prefix/hash admission. Ordinary validation, tokenization laziness, note/chunk
ordering, reply sizing, snapshot ordering and I/O/flush behavior stay in rank.
Both advertised-cache and absent-cache profiles use those same entry points;
existing pending codec depth profiles remain pending.

The production computation path is written, but usable initialization is
currently withheld by precise missing observations below. Source construction
is not a completed worker, numerical qualification or campaign acceptance.
No caller-supplied digest, identity, ready flag, synthetic model or fallback
can make the production factory publish an initialized session.

## Actual ownership and data path

`AssetPaths` names explicit absolute local model/config/tokenizer paths, the
selected ORT library, the three selected math libraries and a source root.
`NativeFactory::load` opens each asset into bounded owned bytes, retaining
I/O stages and checking read-time metadata stability. Those exact bytes feed
`PreparedTokenizer::from_bytes` and the real ORT session constructor. There
is no asset discovery, download, resolver, alternate path or retry. Runtime
owners perform their existing selected-file and actual-mapping checks.

`LoadedBackend` owns the real ORT session, selected math runtime and prepared
tokenizer. ORT retains its model bytes and runtime Rc. Field/drop order releases
the session before the other component owners; existing component cleanup
errors remain in `CleanupErrors`. Keep a clone outside the factory/session,
drop all owners, then drain both its `ort` and `math` observers. A failed
identity initialization returns `PendingInitialization` with the loaded owner
and observations intact; the factory's terminal failure drops that owner and
retains cleanup diagnostics in the supplied observers. It does not retry.

Production `encode` and `decode` call the actual prepared rank tokenizer with
the existing policies. The lone-surrogate UTF-8 bridge retains its structured
`BackendError::TextUtf8` and UnicodeEncodeError family; other native errors
retain their stage/detail and are not broadly converted into Python errors.

`embed_batch_one` iterates exactly the query followed by supplied unique
misses. For EACH text it calls model encoding, constructs batch-one int64
IDs/mask/zero-type-ID tensors, calls real `OrtSession::raw_cls`, then consumes
those raw bits through `RawClsRow::normalize` exactly once. Only resulting
FIRST-pass float32 bits are returned. No request text is stored in the session,
no cross-text padding/batch coalescing/deduplication occurs, and no mean pooling
or precomputed result is substituted. Rank's shape/finite check remains after
this first normalization at its original Source position.

## First-pass cache boundary and second-pass binding

Rank's existing codec admits model identity, shapes, finite/nonzero cache rows,
restore ordering and source hash keys. Rank stores copied first-pass Vec<u32>
rows and reconstructs query plus EVERY passage occurrence before calling norms.

`FirstPassOccurrences::from_rank_first_pass_bits` is the explicit data boundary
for that already admitted payload. It checks shape/storage and copies bits
without arithmetic. It creates no `FirstNormalizedRow`, claims no historical
execution and fabricates no first norm/denominator for cache or restored rows.
Fresh actual model rows take the performed first-pass path; warm/restored rows
are reused bit-for-bit without a repeated first normalization.

`Norms` owns the exact RawMatrix and the second-pass matrix/norm owner, plus a
private session Rc. Division requires exact bit/shape equality with that input
and the same session, so changing even a zero sign or supplying another
session's norms fails before division. This is structural binding, not a new
numeric policy. Second norms have NO epsilon and cover ALL occurrences.
The all-row zero decision precedes ANY mutation. Successful division occurs
once and returns a session-bound `Normalized` owner, then real math matvec
consumes its full contiguous matrix: M0 empty, M1 one SDOT plus positive zero,
M>=2 one full SGEMV. No cache row is mutated. Existing numerical C files,
normalization grouping, dtype, score reduction and exact integer policy stay
unchanged. NaN/Inf/subnormal/overflow values are not sanitized or newly refused.

## Derivation observations and initialization gaps

`ConstructionReport` gathers actually consumed tokenizer/model bytes, prepared
tokenizer serialization, actual selected/mapped runtime identity, actual math
namespace modules/getters, available ORT session settings/metadata, current
binary bytes and a complete snapshot of the eight local crate source trees.
The local snapshot includes Rust/C/header files, Cargo manifests/locks and
present toolchain declarations. It is explicitly NOT an attestation that those
files produced the current executable. Registry/build linkage remains missing.
No credential configuration is read. Explicit paths are retained separately
as provenance; absolute installation paths and timestamps do not enter inputs.

Canonicalization recursively sorts object keys and relative source paths,
emits compact UTF-8 JSON and integer decimals, rejects floating JSON numbers,
and represents the epsilon as canonical decimal string `0.000000000001`.
Feature observations are sorted sets; algorithm sequences retain order.
The separate tag is `cairn.rust-rank-worker/1`, never Python's model identity.
The final SHA-256 path is implemented but refuses missing observations; a
canonical partial report is not a complete identity or qualification receipt.

The existing component interfaces cannot yet establish these required inputs:

| Required observation | Exact interface/owner action |
| --- | --- |
| Complete source/build/toolchain/feature/native-link binding | Bind all local and registry source, rustc/target, per-unit Rust/C flags and actual linked inputs to the observed executable. Runtime source reads and its binary hash alone cannot establish that binding. |
| CPU arena and remaining effective ORT defaults | `semantic-ort::SessionSettings::{cpu_arena,remaining_defaults}` currently return `NativeDefault::NotExposedBySelectedCapi`. Extend the API29 owner observation without inventing defaults. |
| Effective session provider | Existing `SessionSettings` has no effective-provider observation; the ORT owner must expose actual selected-session evidence. |
| ORT native dependency closure | `RuntimeProvenance` binds the actual OrtGetApiBase file, but exposes no complete mapped dependency inventory. Extend that owner before treating its closure as observed. |
| Complete CPU/OS/kernel class | The written platform observer collects real Rust CPU/OS-enabled feature detections when separately executed, but labels its subset partial. Complete ISA/vector-state/kernel observations and library getter binding are required before hashing a complete CPU class. |
| Actually consumed model tensor assets | New bounded schema observer proves self-contained ONNX tensor closure before native load and binds the exact retained model bytes. External tensors, ORT format, future/unknown schema and observation bounds remain explicit pending cases; owned external-file mapping is not implemented. |

Missing observations are derived from the actual report's missing fields and
rechecked internally before identity hashing; no public API can replace them
with asserted observations. The currently written observer leaves those exact
fields unresolved, so `NativeFactory::construct` does not yet return a usable
initialized session. Independent actual tokenizer/inference/normalization/math
operations are crate-private on the retained loaded owner; the public production
composed route remains identity-gated NativeSession. This separation lets
the owner extend precise observation interfaces without replacing a dummy
backend or declaring an unknown default qualified. It is not permission to
execute the loaded owner outside the separately admitted host boundary.

The next observation/code checkpoint is the API29/build/asset observer plan
after this exact source component is reviewed: enumerate those field getters
or retained build evidence against their owners, then wire only actual
observations. Current bounds prohibit changing the accepted ORT ABI/settings
or executing probes here. Runtime qualification remains independently required
once complete identity inputs exist; identity hashing itself proves no parity.

## Source graph and test selection

The new package uses only selected local crates plus existing sha2 0.10.9
(defaults disabled, force-soft) and serde_json 1.0.145 (preserve_order,
unbounded_depth). Cargo.lock is a SOURCE-ASSEMBLED union of the accepted ORT
and math lock entries, NOT a Cargo-produced or resolved lock. It has119 packages:
111 unchanged registry packages and eight local packages. Same registry IDs,
checksums and dependency edges match byte-derived accepted records. No registry
version, dependency acquisition or feature request changes. B2 validated
the combined lock/feature graph before the selected component run, as recorded
in the [campaign inventory](../../docs/plans/hybrid-runtime-campaign-status.md).

The projected active graph reuses the selected ORT graph (98 contexts,
22 scripts, nine host proc macros), adding target math/backend contexts and
one math build script: 100 contexts/23 scripts/nine macros. The completed
component run matched that projection and reported 123 artifacts. Existing
Onig C, ESAXX C++11, support probes, ORT C11 bridge
and three math C11 units retain their source/flags/system-link requirements.
There is no new build script, native unit, ABI or system library in this crate.
Actual compiler/link/process effects require B2's exact component admission;
old component tests are not rerun by this source assignment.

All twelve selected component tests passed in one run. They cover restored bit
storage without first renormalization, exact norms/session binding, all-row
zero-before-mutation/cache preservation, shape refusal, numeric-bit policy,
relative asset refusal, structured UTF-8 errors, canonical identity refusal,
input mutation and set/sequence rules. Two cfg(test) literal collaborators
exercise real adapter normalization through generic rank streams: cold unique
misses versus full warm duplicate rows, and late metadata failure with no new
reply/cache snapshot or next read. Their literal token/inference/score values
are explicitly synthetic; they do not exercise actual ORT or OpenBLAS.
The `--lib backend1_` selection covered these twelve cases only. No
production test collaborator, fake ready identity or broad replacement backend
is available. Independent source review accepted the candidate with the
completed component receipt. Landing preserved all 76 source/input pins and passed one `make check`;
the component run was not repeated. These checks do not establish actual model
or runtime qualification.

All294 token IDs/lengths precede actual model/session admission. P2 raw ORT,
assets/output order, full P3 both norm outputs/unrounded similarities/strict
integers/CPU-ISA-OSstate-FENV-threads, NumPy full384 iterator relation,
production flags/objects, P4 loader/egress/writes/resources, P5 workerCLI/Go,
C2/D1-A IO_NOT_ESTABLISHED/D2 HOLD/RTERM/P-W-uv/old failed/runtime/production
remain open and actual-worker FIRST remains unconsumed. No tolerance or
one-unit allowance substitutes for exact proof. Source-only authoring ran no
Cargo resolver/build/check/test, native load/getter/CPU probe, model/session,
Python Source/protected import, real worker, service, database or deployment.

## S-P2-OBSERVE-1 source continuation

Output metadata retains actual session order. The ORT bridge requests every
output in that order, matching Python `run(None)`, and selects returned index
zero. Rank-three float32 `[1, sequence, 384]` selects token zero; rank-two
`[1, 384]` passes through. A later output named `last_hidden_state` never
rescues an incompatible first output. Duplicate output names remain ordered
metadata; native graph validity stays with ORT. Every returned value is released.

The new private model observer runs on the actual owned bytes before any
`Runtime::load` or `CreateSessionFromArray`. Its schema-derived traversal covers
all eight ONNX v1.22.0 message types transitively containing TensorProto,
including sparse tensors, attributes, nested graphs, functions and training
graphs. Singular message fragments merge; repeated messages stay separate and
last scalar wins. The proof binds length and SHA-256 again to ORT's retained
bytes. See [schema provenance](vendor/onnx-1.22.0/README.md). This proves only
absence of tensor external-data consumption for accepted self-contained bytes,
not model validity or runtime I/O safety. No actual selected model was inspected
or executed. External files require a future owned-byte mapping; unknown fields,
IR versions, formats, wire errors and explicit depth/field/size bounds produce
pending observations. These cases remain in campaign scope.

Running-image bytes now come from an owned `/proc/self/exe` descriptor, with
before/after device, inode, length, mtime and ctime stability checks. The
installation path is provenance only. The report exposes descriptor identity
separately and hashes those exact bytes. The identity gate additionally requires
real compilation rustc, target, per-unit sorted feature sets and ordered flags.
All report inputs remain private; no generic ready blob supplies observations.
For a proved self-contained model, ten required observations still remain:
the four compilation fields, linked-input attestation, CPU arena, remaining
defaults, effective provider, ORT dependency inventory and CPU/kernel class.
Release owns binary/build/link, native dependency and CPU/OS evidence; the P2
owner retains arena/default/provider source work. Initialization stays withheld.

Sixteen new `p2_observe1_` tests are authored and UNEXECUTED: eight synthetic
wire/schema cases, five ordered-metadata/raw-bit cases, one opened-inode versus
replaced-path fixture, and two identity-shape cases. Future bounded selection is
`--lib p2_observe1_ -- --test-threads=1`. The descriptor fixture creates, writes,
renames, reads and removes owned temporary files; it does not call the actual
proc observer. None constructs a native/model session or CPU observer. Old
`backend1_` tests and accepted arithmetic are unchanged and not selected.
The dependency/feature graph stays 100 contexts, 23 scripts, nine proc macros
(123 prior artifacts), with no manifest/lock/version changes. Existing ORT C
bridge changes include the API29 type query and a 48-byte output ABI carrying
rank. New compile/link/ABI and selected-test disposition belong to SAME B2,
followed by SAME R1. Only rustfmt, source inspection and whitespace checks were
authorized for author verification. No component run or runtime acceptance is
claimed. All existing P1-P5 and actual-worker FIRST holds above remain.

## S-P2-SESSION-1 partial owning-session evidence

`Session::observations()` exposes immutable, privately constructed evidence
from its actual C cache. The backend includes options-entry byte pairs and
ordinal input/output memory/optional EP records under `session.partial_observations`.
Bytes use exact hex; config pairs have a canonical byte-pair ordering with
duplicates retained, and I/O arrays keep metadata order. Configured caps and
settings are distinguished from actual option getters and initialized-session
I/O evidence. An options entry list, including an observed empty list, is not a
complete effective-default manifest. No public fixture/ready constructor was
added, and LoadedBackend computation operations stay crate-private.

All ten missing observations remain. B1's three session obligations now state
the unsupported relation precisely: I/O allocator type does not read global
CPU-arena enablement; config entries and two option getters do not enumerate
remaining effective defaults; optional I/O EP devices do not prove every node's
provider/dispatch. The public API29 functions used here expose these partial
records only. Retained internal C++ getters are not public C ABI observations.
The next B1 source action must establish actual public, binary-bound readback
for each missing global field without changing settings. Release retains the
existing binary/build/link, mapped-dependency and CPU/OS obligations. External
model assets/unknown schema or ORT format remain pending where applicable.

Two new private `p2_session1_` backend tests call the actual session-input
consumer with synthetic partial JSON and metadata. They reject readiness
inference even from CPU/arena-looking I/O or asserted global fields nested in
partial evidence, retain the identity gate, separate stages, preserve metadata
order and distinguish changed byte/order inputs. Eight ORT decoder tests cover
the owning-data boundary. These ten tests are authored, not executed by B1.
Future selections are `--lib p2_session1_ -- --test-threads=1` in their respective
backend and ORT roots, subject to SAME B2 admission. Old selections are not
replayed. No new dependency, graph feature, native unit or build-system edge is
introduced; existing bridge code and readback ABI gain the observations.

Author checks are source inspection, rustfmt and whitespace only. Source
construction does not establish actual getter behavior, default/provider
completeness, model execution or numerical/runtime acceptance. Full P1-P5,
all294-before-model, both norms/cache occurrences/NumPy iterator, raw-bit
similarity/strict integer, production and FIRST holds remain unchanged.
