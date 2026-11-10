# Hybrid runtime campaign: code and remaining work

The CAIRN-301 campaign remains incomplete. Its destination is a Go lifecycle
runtime, a Rust semantic worker and thin host adapters, as described in the
[architecture proposal](hybrid-runtime-consolidation.md) and
[implementation plan](hybrid-runtime-implementation.md).

Published source includes the Go lifecycle components, Rust semantic libraries
and operations tooling below. Component checks and source review establish only
their selected boundaries. Installation, runtime integration and campaign
acceptance remain open.

## Inspect the implementation

The table records published source and extensions through the Rust original-text
document-passage projection
[`474b756f`](https://github.com/halbritt/cairn/commit/474b756fd4f47e7c20ce23b4ca1887db1fdb893e)
on 2026-11-09. The Go libraries are callable source. Rust has an explicit
rank/stream entry target; model initialization and actual-worker qualification
remain open. Publication does not activate the migration on supported hosts.

| Component | Source and publication | Implemented behavior | Remaining acceptance and area owner |
| --- | --- | --- | --- |
| Go journal execution, recovery and inspection | [journal](../../internal/lifecycle/journal/journal.go), [execute](../../internal/lifecycle/journal/execute.go), [recover](../../internal/lifecycle/journal/recover.go), [inspect](../../internal/lifecycle/journal/inspect.go), [json constants](../../internal/lifecycle/journal/json_constants.go); [`4eb9c41c`](https://github.com/halbritt/cairn/commit/4eb9c41c017086e5012420747dcbb48158ea76fa), Stage 2C [`249f1eda`](https://github.com/halbritt/cairn/commit/249f1edabb9453dc664e3e0a9e12cbcbde42a080) | Durable inbox intents, command/reply recovery, distinct reader policies and default-JSON non-finite literals with CPython `_CONSTANTS['NaN']` container-identity retry semantics | Coordination: other-producer `NaN` identity, C2 child-process parity and installed runtime qualification; the caller still supplies validated context |
| Go coordination locks and process observations | [locks](../../internal/lifecycle/coordination/locks.go), [owner lookup](../../internal/lifecycle/coordination/owner.go), [process scan](../../internal/lifecycle/coordination/process_scan.go); [`5a6364a9`](https://github.com/halbritt/cairn/commit/5a6364a9a2b2d9ed49c4c330cfafa96c44ba94f2) | Callable session/ledger/intent/watcher/cue locks, owner ancestry lookup and process-marker scans with unknown-coverage reporting | Coordination: native caller orchestration, cross-runtime locking and state-writer compatibility; full J-05/J-06 and installed behavior remain open |
| Go native sessions and recovery orchestration | [sessions](../../internal/lifecycle/coordination/session.go), [file host](../../internal/lifecycle/coordination/file_host.go), [journal adapter](../../internal/lifecycle/coordination/journal_adapter.go), [process bindings](../../internal/lifecycle/coordination/process_bindings.go), [recovery](../../internal/lifecycle/coordination/recovery.go); Stage 2B [`fa831236`](https://github.com/halbritt/cairn/commit/fa831236302ef4e18c68d71e9544c3236fe40ff8), corrections [`3c5fa30a`](https://github.com/halbritt/cairn/commit/3c5fa30a9746decc381b17e8aebb7056b36c5fb4) | Native session state, concrete filesystem persistence and locks, journal projection, process hooks and recovery using transcript evidence | Coordination: binary entrypoint and installed service integration, C2 child-process parity and installed runtime qualification; full J-05/J-06 acceptance remains open |
| Go coordination service bindings and selected CLI surfaces | [CLI](../../internal/lifecycle/coordination/cli.go), [compact CLI](../../internal/lifecycle/coordination/compact_cli.go), [inbox CLI](../../internal/lifecycle/coordination/inbox_cli.go), [grant bindings](../../internal/lifecycle/coordination/grant_bindings.go), [Codex health](../../internal/lifecycle/coordination/codex_health.go), [service bindings](../../internal/lifecycle/coordination/service_bindings.go); Stage 2C [`249f1eda`](https://github.com/halbritt/cairn/commit/249f1edabb9453dc664e3e0a9e12cbcbde42a080) | Time, grant and Codex health bindings; selected inbox, compact, drain and recovery commands | Coordination: `hook` and `watch` return `ProfileUnavailable`; binary entrypoint wiring after Foundation F-01/F-04, C2 child-process parity and installed runtime qualification remain open |
| Go shared values and JSON profiles | [values](../../internal/lifecycle/value/value.go), [profiles](../../internal/lifecycle/value/profile.go), [writer](../../internal/lifecycle/value/encode.go), [operations](../../internal/lifecycle/value/operations.go); [`be67f6e5`](https://github.com/halbritt/cairn/commit/be67f6e58cc2d64b1fb1e45af5a3143418880bca) | Lossless decoded values, explicit reader/writer profiles and selectable duplicate-key handling, shared with the transcript consumer | Foundation: actual interpreter and per-route profile qualification, exact writer behavior and caller integration; source publication does not qualify a runtime profile |
| Go native transcript readers and proofs | [readers](../../internal/lifecycle/transcript/transcript.go), [Claude](../../internal/lifecycle/transcript/claude.go), [Codex](../../internal/lifecycle/transcript/codex.go), [proofs](../../internal/lifecycle/transcript/proofs.go); [`2ba1b330`](https://github.com/halbritt/cairn/commit/2ba1b33032e7f10af10c606e6424db2b4d684587), fixture correction [`f9ecf5fe`](https://github.com/halbritt/cairn/commit/f9ecf5fe7d52cc215d4060f6ac5625ccac68fbe6) | Pinned record reads, ownership and locator checks, harness-specific transcript evidence and shared profile-based JSON decoding | Coordination: native caller integration, remaining timestamp/path/read parity and Stage 2 serialization; full J-06 and installed behavior remain open |
| Shared Unix peer client and wake consumers | [client](../../internal/procpeer/client.go), [wake envelope](../../internal/lifecycle/wake/wake.go), [Claude channel](../../internal/lifecycle/wake/claude.go), [Codex queue](../../internal/lifecycle/wake/codex.go), [OpenCode](../../internal/lifecycle/wake/opencode.go), [Hermes](../../internal/lifecycle/wake/hermes.go), [Herdr](../../internal/lifecycle/wake/herdr.go); [`0ad5b419`](https://github.com/halbritt/cairn/commit/0ad5b419edd12a6dd757a185fbd7c83fa6468c03), extended by [`f6f0e427`](https://github.com/halbritt/cairn/commit/f6f0e42784d0e1eefc0c211d8a4b80b054f2ddab) | Peer verification before payload, bounded framing, wake binding and callable transport consumers | Foundation: shared primitive integration; Coordination: native turn/custody integration and installed behavior |
| Rust blank-text predicate | [library](../../rust/semantic-text/src/lib.rs), [boundary](../../rust/semantic-text/README.md); [`0c5cd5e3`](https://github.com/halbritt/cairn/commit/0c5cd5e3e0da570da369aa8a01f610021d5a88a0) | Fixed Python 3.11 whitespace classification over supplied strings | Semantic: caller binding and full rank/embed worker acceptance |
| Rust single-note score reduction | [library](../../rust/semantic-score/src/lib.rs), [boundary](../../rust/semantic-score/README.md); [`94cf6685`](https://github.com/halbritt/cairn/commit/94cf66857e7c96602f9dc3bc4b3a1d0984091c1c) | Ordered maximum over supplied chunk similarities, final clamp, scaling and ties-to-even rounding | Semantic: tokenizer/model execution, matrix arithmetic and actual worker/caller qualification |
| Rust decoded protocol, hash admission and cache payloads | [protocol](../../rust/semantic-protocol/src/lib.rs), [hash](../../rust/semantic-protocol/src/hash.rs), [cache](../../rust/semantic-protocol/src/cache.rs), [boundary](../../rust/semantic-protocol/README.md); [`e4e8e80d`](https://github.com/halbritt/cairn/commit/e4e8e80d93cf5bd2ca43bec817006198d7c05632), extended by [`66dd1540`](https://github.com/halbritt/cairn/commit/66dd15409359d1f28b32fab7896dec3e2c425053) | Lossless decoded values and ordered prefix guards, body hashes, atomic cache restore and payload codecs with shared JSON rendering and byte measurement | Semantic: real constructor/model identity, tokenizer and worker/runtime qualification; unsupported depth remains explicitly pending |
| Rust JSON input and reply codecs | [codec](../../rust/semantic-protocol/src/json.rs), [boundary](../../rust/semantic-protocol/README.md); [`b244e26b`](https://github.com/halbritt/cairn/commit/b244e26b85673d15308f7fbac9f36c311beaad23), corrected in [`114cf677`](https://github.com/halbritt/cairn/commit/114cf677082af2729e0e744a3c83fee6eb40bceb) | Byte framing, Unicode/JSON decoding, reply serialization and shared cache measurement over lossless values | Semantic: inputs deeper than 64 containers remain pending; actual worker, model, runtime policy and transport qualification remain open |
| Rust rank and stream engine | [request engine](../../rust/semantic-rank/src/rank.rs), [bounded I/O](../../rust/semantic-rank/src/stream.rs), [backend interface](../../rust/semantic-rank/src/backend.rs), [boundary](../../rust/semantic-rank/README.md); [`d1c78f8c`](https://github.com/halbritt/cairn/commit/d1c78f8cbffc01a19bbc5469f4fca0e65210e9f6) | Joins protocol, hash admission, cache and score components; orders backend calls and flushes each stream reply before reading the next request | Semantic: actual tokenizer/model/numerical behavior and Go supervisor remain unqualified; the separate entry target below has only bounded component evidence |
| Rust prepared tokenizer and input tensors | [preparation](../../rust/semantic-tokenizer/src/prepared.rs), [tensors](../../rust/semantic-tokenizer/src/tensors.rs), [boundary](../../rust/semantic-tokenizer/README.md); [`d732f5fe`](https://github.com/halbritt/cairn/commit/d732f5fe292aff17cfc54b580c1ada433ac795e2), fixture correction [`37a50424`](https://github.com/halbritt/cairn/commit/37a50424872d8b3229ad97b80dd3ed8aa0e4888a) | Uses Tokenizers 0.23.2 with supplied model configuration; maintains separate model/rank policies and produces batch-one int64 IDs, attention masks and zero model token-type rows | Semantic: prepared-asset comparison of all 294 token IDs/lengths, actual model/ORT and numerical backend, executable and Go supervisor remain unqualified |
| Rust ORT runtime and raw CLS inference | [runtime/session owners](../../rust/semantic-ort/src/lib.rs), [native bridge](../../rust/semantic-ort/native/bridge.c), [boundary](../../rust/semantic-ort/README.md); [`2cdedfbf`](https://github.com/halbritt/cairn/commit/2cdedfbf56d67848f1921cd5ed81253b2539eaf2) | ORT 1.29 C-API runtime/session ownership, batch-one int64 feeds and raw 384-component CLS bits from the named output | Semantic: actual model/session execution and raw-bit parity, numerical backend, worker and Go supervisor; component tests do not load ORT or run a model |
| Rust full-occurrence matrix-vector stage | [layout](../../rust/semantic-math/src/layout.rs), [runtime owner](../../rust/semantic-math/src/lib.rs), [native computation](../../rust/semantic-math/native/compute.c), [boundary](../../rust/semantic-math/README.md); [`4b54d04e`](https://github.com/halbritt/cairn/commit/4b54d04ef03dd8ffbbf9b09b57abb307d7857741), accepted lock [`dc6a066d`](https://github.com/halbritt/cairn/commit/dc6a066d76e58d0222cb8e8234c0ba1a29c70651) | Preserves all passage occurrences in source order at width 384; selects empty output, one native dot product, or one full-matrix GEMV using the pinned OpenBLAS bridge | Semantic: actual native loading and numerical parity, complete rank backend, worker and Go supervisor; no BLAS computation was exercised by the component tests |
| Rust first-pass and full-occurrence normalization | [stage owners](../../rust/semantic-math/src/normalization.rs), [native arithmetic](../../rust/semantic-math/native/normalization.c), [boundary](../../rust/semantic-math/README.md); [`11f1395e`](https://github.com/halbritt/cairn/commit/11f1395ebae685a72a05ee4541c7f2d91790d6eb) | Normalizes raw 384-component CLS rows with epsilon, preserves immutable first-pass cache rows, reconstructs all occurrences, then checks every second-pass norm before division | Semantic: rank/cache composition is published below; actual NumPy, CPU and floating-environment correspondence, exact numerical parity and initialized backend/worker acceptance remain open |
| Rust owning native rank backend | [composition](../../rust/semantic-backend/src/lib.rs), [cache/arithmetic binding](../../rust/semantic-backend/src/arithmetic.rs), [initialization boundary](../../rust/semantic-backend/README.md); [`5b6f1f31`](https://github.com/halbritt/cairn/commit/5b6f1f310635b9384d199f9869f22cbab39561b1) | Connects the prepared tokenizer, ORT session, first-pass cache rows and full-occurrence normalization/matrix computation under one session owner; restored rows retain their bits without repeating first normalization | Semantic and Release: missing initialization observations still prevent a usable factory; actual assets/model/math parity, worker, Go supervisor and runtime qualification remain open |
| Rust typed session-observation serializer fixtures | [serializer](../../rust/semantic-backend/src/identity.rs); tested source `d0ad8f82` | Private read-only traits let owner-created ORT records and private typed fixtures use the same serializers; four selected fixture checks passed with independent expected values | Semantic and Release: concrete ORT getter forwarding and the original actual-session T1 comparison remain owed; required observations, NULL globals, numerical/runtime and FIRST holds remain unchanged |
| Rust rank/stream entry and launch plan | [entry](../../rust/semantic-backend/src/bin/rank.rs), [parser](../../rust/semantic-backend/src/bin/rank_entry/plan.rs); tested source [`cb6dbc90`](https://github.com/halbritt/cairn/commit/cb6dbc906661e771431a22238d0fd75f5afedd18) | Explicit `cairn-semantic-rank` target and pure parsing of six absolute asset/library/source selections; rank-once or stream dispatch with exact stream-cache opt-in; 11 bounded parser, private-driver and synthetic-cleanup checks passed | Semantic and Release: production entry/native wiring, real owner drop/cleanup, exit/stderr behavior, model initialization, actual-session T1, worker and Go supervisor remain unqualified; installer and Go routing are unchanged |
| Rust original-text embedding-query projection | [projection](../../rust/semantic-tokenizer/src/query.rs), [character slicing](../../rust/semantic-tokenizer/src/offsets.rs); tested source [`d58aed45`](https://github.com/halbritt/cairn/commit/d58aed45a45901b0de155b832a5697bda7161f24) | Untruncated/unpadded rank-tokenizer counts, original-text prefix selection with at most eight re-encodings, character-to-UTF-8 slicing and prefix-only hash/count metadata; 20 bounded owned-fixture checks passed, with 11 prior tests filtered out | Semantic and Release: Python binding/version correspondence, actual prepared-token parity, actual-session T1 and model/worker/runtime qualification remain open; component input attestation has the header limit below |
| Rust original-text document-passage projection | [projection](../../rust/semantic-tokenizer/src/document.rs), [shared character slicing](../../rust/semantic-tokenizer/src/offsets.rs); tested source [`474b756f`](https://github.com/halbritt/cairn/commit/474b756fd4f47e7c20ce23b4ca1887db1fdb893e) | Typed original passage text and UTF-8 spans, adaptive 128/96 windows including the final tail, complete span validation before ordered default-special re-encoding; 22 bounded owned-fixture checks passed with 31 prior tests filtered out | Semantic and Release: Python binding/version correspondence, actual prepared-token parity, actual-session T1 and model/worker/runtime qualification remain open; current selected header coverage does not close item2's historical gap |
| Go memory client and checkpoint reader | [client](../../internal/lifecycle/memoryclient/client.go), [checkpoint](../../internal/lifecycle/memoryclient/checkpoint.go), [contract](hybrid-runtime-contracts/f02-memory-client.md); [`2989f834`](https://github.com/halbritt/cairn/commit/2989f83442ba880dd586b807b9cdece5282b67e5) | Direct authenticated SDK search/pull calls, receipt-credit accounting and concrete raw-record checkpoint traversal | Foundation: recall/capture frontends, hook streams/exits/meters, grants and executing-hook identity; full F-02 and HC-03 remain open |
| Go unfinished checkpoint refresh | [refresh](../../internal/lifecycle/memoryclient/checkpoint_refresh.go), [contract](hybrid-runtime-contracts/f02-checkpoint-refresh.md); [`965c48d6`](https://github.com/halbritt/cairn/commit/965c48d66a397023377110d8be6f27f383d3534a) | Restores at most one current whole unfinished note through bounded authenticated search/pull, with identity/hash/extent checks and shared deadline accounting | Foundation: frontend state, grants and metering integration; runtime and full F-02 acceptance remain open |
| Go capture selection and ordered writes | [capture](../../internal/lifecycle/memoryclient/capture.go), [contract](hybrid-runtime-contracts/f03-capture-commit.md); [`1497c7dc`](https://github.com/halbritt/cairn/commit/1497c7dc8ac14c776ca4672e28d038976cb21265) | Validates the complete selection before record mutation, performs ordered create/revise calls and retains confirmed writes when a later write fails | Foundation: frontend state commits, hooks, streams/exits/meters, grants and executing-hook identity; full F-03 remains open |

The journal and first wake/client slices passed focused Go fixture checks and
independent source review before publication. The Rust slices have component
check and independent review evidence; the hash/cache package pass covers its
28 selected unit tests. Memory-client integration passed its focused four-name
selector; the retained output records a package PASS, without measured per-test
or subcase execution counts. The checkpoint-refresh extension passed all 40
frozen test names, required static checks and independent source review; its
unchanged candidate and inputs allowed those checks to be reused for landing.
The coordination primitives passed the three selected race-test roots and
`make check` on the landing base, following source review and review of the
required corrections. The JSON-codec extension has 51 retained passing tests
and one corrected test that passed a focused run; the original 51-pass/1-fail
run remains recorded. This is combined coverage of the 52 cases, not a fresh
full-suite or doctest pass. Independent source review accepted the codec, and
one landing `make check` passed. The rank/stream engine passed its 27 component
tests in one run, independent source review and one landing `make check`; the
tests use a scripted backend and do not run a model or native worker.
The prepared-tokenizer component has ten retained passing tests and one corrected
test that passed a focused run with the other ten filtered out. The original
ten-pass/one-fail run remains recorded; this is not a fresh full-suite pass.
Independent source review accepted the candidate. Landing preserved all 33
reviewed source/input pins and both reviewed diffs, and ran `make check` once
without repeating the Cargo component tests. The tests use a synthetic tokenizer
through the real Tokenizers API; they do not establish prepared-model parity or
run an ORT session.
The ORT component passed seven pure-data tests in one isolated offline, locked
Cargo run and its primary source review. The run compiled the C bridge but did
not load an ORT library, construct a model session or perform inference. Landing
preserved all 47 source/input pins and passed one `make check`; actual runtime
qualification remains open.
The matrix-vector component passed seven pure-data tests in one isolated offline,
locked Cargo run and its existing primary source review. The run compiled the
two C units but did not load the math runtime, call its getters or execute BLAS.
Full numerical/runtime qualification remains open.
The normalization extension passed nine new synthetic tests in one isolated,
offline, locked component run, with the seven previously accepted tests filtered
out. The run exercised the new native normalization arithmetic and compiled all
three C units; it did not load the math runtime, execute BLAS or compare against
an actual NumPy/model reference. Independent source review accepted the exact
candidate and completed component receipt. Landing preserved all 67 source/input
pins and the reviewed unit diff, and passed one `make check` without repeating
the component run. The later backend slice connects rank/cache ownership; full
numerical/runtime acceptance remains open.
The native-backend component passed its twelve selected tests in one isolated
component run and met the existing primary source review condition. The run
validated the combined build graph and compiled the native units; its synthetic
inputs and test-only collaborators did not load an actual ORT/model or execute
OpenBLAS. Landing preserved all 76 source/input pins and the reviewed diff, and
passed one `make check` without repeating the component tests. Missing
initialization observations still prevent the factory from returning a
usable session; publication does not establish an executable worker or runtime
qualification.
The typed session-observation serializer extension (`d0ad8f82`) met its source
review condition after the two-literal zero-padding correction. One isolated,
offline, locked backend-only `p2_t1_source_` invocation passed four selected
tests with 31 prior tests filtered out. The checks exercised the shared
`session_observations_json` and `graph_assignment_json` bodies through private
typed fixtures. Independent expected values cover exact bytes and zero-padding,
optional versus empty values, ordering, grouping, duplicates, API/setting/stage
provenance and unchanged readiness refusal. No public observation constructor,
fixture feature/dependency or native bridge/ABI change was added.

That run compiled the inherited native units but did not execute concrete ORT
forwarding implementations, construct an actual session or call native getters,
ORT, BLAS, a model or a worker. The original actual-session T1 comparison remains
owed. `REQUIRED` declares 11 observation rows; this is a declaration count, not
a measured count of missing observations. Those rows and the existing missing
observations remain unchanged. `effective_provider`, `cpu_arena` and
`remaining_defaults` remain NULL. Full backend initialization, readiness,
numerical parity and campaign acceptance remain open, with the current strict
score policy and all294-before-model, numerical/runtime/resource/production and
actual-worker FIRST holds unchanged.

The Rust rank/stream entry (`cb6dbc90`) was accepted in independent source
review round 1 with no defect. One isolated, offline, locked
`--bin cairn-semantic-rank rank_entry1_` invocation passed all 11 selected checks,
with zero filtered tests and zero retries. The checks cover pure launch-plan
path/cache/refusal/non-UTF-8 behavior, private `ProbeDriver` dispatch and synthetic
cleanup/drop/error sequencing. The normal backend library and binary-test
harness were built separately. Inherited native compilation and the new harness's
linkage/startup were observed; these component effects do not establish production
entry or native runtime behavior. Landing preserves the reviewed and tested Rust
source without repeating the component command.

The parser requires separate absolute values for `--model-dir`, `--ort-runtime`,
`--openblas`, `--gfortran`, `--quadmath` and `--source-root`; it performs no asset
reads or discovery. `--stream` selects streaming, and only
`CAIRN_SEMANTIC_STREAM_CACHE=1` advertises its cache. Rank-once ignores that
setting. The parser is intentionally stricter than argparse: equals forms,
abbreviations, help, duplicates and relative paths are refused. A following flag
consumed as a path value is also refused deterministically.

Four nonblocking review limits remain. The binary and its library dependency
share a crate name, leaving a documentation-output collision if dependency docs
are built; no rename or documentation build was required. Free-form
`BackendError` details mean source inspection cannot guarantee request-free
stderr. Production `main`, `NativeDriver`, `invoke_native`, real factory/driver
wiring and owner drop/native cleanup, exit mapping and stderr remain supported
only by source review. The synthetic cleanup tests establish sequencing within
the helper, not the real shared-observer wiring; a driver panic would exit
nonzero without draining cleanup. The stricter parser behavior above is the
fourth limit.

The active installer and Go routing are unchanged. Actual-session T1, required
observations and NULL globals remain as described above. `REQUIRED` still
declares 11 rows, not a measured count of missing observations. The current
strict score policy permits no +/-1 tolerance. All 294 token IDs/lengths must
precede model execution; actual-worker FIRST remains unconsumed. Full P1-P5,
numerical/runtime/resource/production and old-failure holds persist. This entry
publication establishes neither readiness, parity, initialization nor campaign
acceptance. The query-prefix and document-span projections are published below.

The original-text embedding-query projection (`d58aed45`) was accepted in
independent source review round 1 with no candidate defect. One isolated,
offline, locked `--lib query_prefix2_` invocation passed all 20 exact selected
checks with 11 prior tests filtered out and zero retries. The owned synthetic
`PreparedInputs` fixtures exercise actual Bert/WordPiece tokenization and
independent count, Unicode-offset and SHA-256 expectations. Private scripted
observations exercise the same production projection algorithm for retreat and
error bounds. No actual prepared294 inputs, protected Python reference, model,
session or worker were executed. The component build excludes the backend, ORT
and math crates. It compiled the inherited Onig/ESAXX units and exercised the new
test harness's linkage/startup; those effects do not qualify production runtime.

`PreparedTokenizer::query_prefix` refuses Python-blank text and inputs above
4096 strict UTF-8 bytes. It counts default-special tokens on the separate
untruncated/unpadded rank clone. At 512 tokens or fewer it preserves the original
text. Above that bound it re-encodes at most eight original-text prefixes,
retreating through the original encoding's offsets with Python's signed slice
semantics. Character offsets map to valid original UTF-8 cuts, without decoding
tokens, normalizing the selected text or expanding over ignored bytes. The
question includes the exact instruction; `original-prefix/1` metadata retains
the full original count, embedded count, truncation flag, and prefix-only UTF-8
byte length and SHA-256. Model tokenizer truncation/padding remains unchanged.

The source review retains several limits. Python binding use of character
offsets and Python/Rust tokenizer-version correspondence remain unproved. A
valid UTF-8 cut can split a base character from a combining mark, as the source
algorithm permits. Four current identical instruction-prefix copies lack a
cross-crate guard. The simple retreat test discriminates the decrement weakly;
the negative-end and large-count cases cover the arithmetic. The owned-fixture
expectations have now passed the component run; broader parity remains held.

Component header evidence has a specific limit: all 198 selected system-header
preflight hashes matched. Twenty additional headers have same-run post-run
identities/hashes and retained historical path evidence, but no current-run
preflight hashes. The prior line parser missed split unfinished/resumed opens;
the coverage assertion was repaired only from that same trace, without a rerun.
The plan explicitly limited its selected header set. The bounded component
result supports source landing; it does not establish before/after stability of
those 20 headers or complete include/input attestation.

Landing preserves the tested code, manifests, locks and native inputs without
repeating the component command. The active installer, Go routing, rank/stream
entry and existing model/rank preparation bodies are unchanged. Actual-session
T1, required observations/NULL globals, exact current integer scores with no
+/-1 tolerance, all294-before-model, FIRST UNCONSUMED and full P1-P5,
numerical/runtime/resource/production and old-failure holds persist. `REQUIRED`
declares 11 rows, not a measured missing count. No readiness, usable
initialization, actual prepared-token parity or campaign acceptance is claimed.
The document-passage projection is published below.

The original-text document-passage projection (`474b756f`) was accepted in
independent source review round 1 with no candidate defect. One isolated,
offline, locked `--lib document_spans3_` invocation passed all 22 exact selected
checks with 31 prior tests filtered out and zero retries. Owned Bert/WordPiece
fixtures and private observations exercise the same production algorithm with
independent literal expectations. They cover byte/scalar geometry, blank and
token-empty distinctions, bounds, tail windows, adaptive density, failure order
and changed token counts after a cut. No actual prepared294 inputs, protected
Python reference, model, session or worker were executed. The build excludes
backend/ORT/math; inherited Onig/ESAXX compilation and new test-harness
linkage/startup are component effects only.

`PreparedTokenizer::document_passages` returns owned original text plus UTF-8
byte spans. Python-blank refusal precedes strict UTF-8 conversion and the
inclusive 65536-byte bound. The untruncated/unpadded rank clone encodes the body
without specials; the offset population determines the windows. Stride is
`max(96, ceil(tokens/256))` and window width is `max(128, stride+32)`, with
overflow-safe arithmetic. Every start emits, including a final smaller tail
even when the preceding window already reaches the end. Each span uses the
first and last endpoint, not a maximum over the window; invalid ordering
refuses before Python-style clipping. Shared character slicing preserves the
exact original UTF-8 text and byte geometry without decoding, normalizing or
expanding ignored bytes. All spans and the 1..256 passage guard complete before
ordered default-special re-encodings of the actual passages against the
inclusive 512-token limit. There is no document instruction prefix or silent
truncation to fit.

Source-review limits remain. Python binding/version correspondence is unproved.
The passage-count refusal is mathematically unreachable because the adaptive
stride bounds positive offset populations to 1..256 windows; the source guard
is retained. Out-of-range offsets can clip to an empty passage, matching Python
rather than adding a new refusal. A trailing combining mark can stay outside a
span, as in the query projection. The owned real-tokenizer expectations have
now passed the component run; they do not establish Python or runtime parity.

For this component, all 218 selected system-header preflight, observed and
postflight paths matched, with zero additional observed system headers, as
rederived from the complete joined trace. This is selected-path coverage, not
complete future, transitive, include or runtime input attestation. It does not
retroactively close item2's historical 198-preflight/20-post-run-only gap or
establish before/after stability for those historical 20 headers.

Landing preserves the reviewed/tested Rust source and its code, manifest, lock
and native inputs without repeating the component command. Query behavior,
shared helpers, model/rank preparation, rank/stream entry, identity, numerical
code, installer and Go routing are unchanged. Actual-session T1 remains owed.
Required observations/NULL globals, exact integer scores with no +/-1 tolerance,
all294-before-model, FIRST UNCONSUMED and full P1-P5,
numerical/runtime/resource/production and old-failure holds persist. `REQUIRED`
declares 11 rows, not 11 measured missing observations. This source publication
establishes no readiness, usable initialization, actual prepared-token parity or
campaign acceptance.

The paired value/transcript component passed formatting, build, vet and scoped
race checks for those two packages. Foundation cleared the exact four-file value
dependency, and both transcript reviewers accepted the source and fixture delta.
Landing retained all 12 reviewed file blobs; intervening changes affected only
documentation and Rust, so the passing Go checks were reused without rerunning.
The later native-session/recovery slice passed both reviewers' Stage 2A correction
reviews and its scoped checks. Coordination race coverage selected only
`^TestStage2`; the existing child-process fixtures remained held. Landing preserved
the reviewed coordination and transcript subtrees. Follow-up commits
[`c38ff5bb`](https://github.com/halbritt/cairn/commit/c38ff5bba28bb07b9eabc8c3dd95930e98ae0738)
and
[`05c3c83c`](https://github.com/halbritt/cairn/commit/05c3c83c123b8f3d37af12439a17e37efd0e90b5)
correct daemon liveness-call order, scalar delivery error classification,
session mutation order, and selected recovery guards and default-object behavior.
Both passed the existing reviewer's focused source review; their formatting,
build, vet and scoped race checks passed. The reviewer reused those logs without
rerunning tests, and the transcript source remained unchanged.
Stage 2B adds concrete filesystem persistence, locks, journal projection and
process bindings. It also corrects health-check harness ordering and lazy field
access during queue-wake reconciliation. Its formatting, build, vet and scoped
`^TestStage2` race checks passed; the existing reviewer checked the ten changed
file pins and retained evidence without rerunning tests.
The follow-up in
[`3c5fa30a`](https://github.com/halbritt/cairn/commit/3c5fa30a9746decc381b17e8aebb7056b36c5fb4)
corrects command/decode error classification, float-zero truthiness through
journal projection, and process-field evaluation order for malformed short stat
records. All four affected coordination checks passed, and the existing reviewer
accepted the five-file delta with no new findings, using retained evidence without
rerunning tests. The published coordination and transcript source matches the
reviewed candidate. At that stage, CLI integration, external service bindings and
installed runtime qualification remained open; the source review did not establish
full J-05/J-06 acceptance.

Stage 2C was published in
[`e29e4a9e`](https://github.com/halbritt/cairn/commit/e29e4a9e3a2691d03de211df2ff9d8fa3a54e779)
through
[`249f1eda`](https://github.com/halbritt/cairn/commit/249f1edabb9453dc664e3e0a9e12cbcbde42a080),
after J-R1's `PASS_STAGE2C` review
(`e73d0cbb-31f5-46a2-aaa5-e575812bcff4@1`). Landing preserved the reviewed
coordination and journal subtrees. The Go landing base was unchanged, so retained
check evidence was reused with zero duplicate Go tests. Formatting, build and vet
passed, as did the selected journal and `^TestStage2` coordination race suites.
Those tests used fake runners and owned temporary directories; C2 child-process
fixtures remained held. General B1/other-producer `NaN` constant identity remains
unclaimed, and `hook` and `watch` still return `ProfileUnavailable`. Binary
entrypoint wiring after Foundation F-01/F-04 remains open. These results do not
establish child-process parity, installed hook behavior, full J-05/J-06 acceptance
or measured usefulness.

These checks do not establish actual model/native
execution, installed hook behavior, full design acceptance or measured usefulness.

The [lifecycle source map](../reference/lifecycle-slices.md),
[bundle documentation](../lifecycle-bundles.md) and
[testing guide](../developer/testing.md) describe the shared Go mechanisms and
their current limits, updated in
[`b0ff1617`](https://github.com/halbritt/cairn/commit/b0ff16170aa1515e4cdd65b2dae50204d20fa2fd).

The existing [implementation plan](hybrid-runtime-implementation.md) assigns
cross-component and installation qualification to Integration and release, with
Foundation, Coordination and Semantic retaining the source work above.

The [inactive runtime operations](../../integrations/lifecycle/operations/README.md)
support that qualification:

| Component | Source | Implemented behavior |
| --- | --- | --- |
| F1 topology collector | [collector.py](../../integrations/lifecycle/operations/f1/collector-code/collector.py) | Fixed authenticated reads, route and process metadata checks, bounded child intake and exclusive observation records |
| F1 metadata helper | [metadata_helper.py](../../integrations/lifecycle/operations/f1/helper-code/metadata_helper.py) | Selected socket/process/image checks, exact code closures and the fixed privilege-tool alias chain |
| F1 executor | [executor.py](../../integrations/lifecycle/operations/f1/executor.py) | Split-owner installation custody, independently bound producer completion, bounded child intake and disabled entry; all operational selections remain unbound |
| Effect custody | [effects.py](../../integrations/lifecycle/operations/f3/effects.py) | Exclusive durable intent, fixed actions, causal outcomes and stop uncertainty |
| Local transport | [transport.py](../../integrations/lifecycle/operations/f3/transport.py) | Bounded request/output handling, validated clock samples and retained child custody |
| Fixed SSH launcher | [root_launcher.py](../../integrations/lifecycle/operations/f3/root_launcher.py) | Intent and pin checks, SIGNAL-only admission and one consumed transport session per request; operational inputs remain unbound |
| Fixed remote executor | [remote_executor.py](../../integrations/lifecycle/operations/f3/remote_executor.py) | Reserved actions, fixed commands, guarded exit observation and causal outcomes; the entry remains disabled |
| Handoff sequencing | [sequence.py](../../integrations/lifecycle/operations/f3/sequence.py) | Separate physical join and claim qualification, fresh proof before subsequent effects |
| Journal projection | [journal.py](../../integrations/lifecycle/operations/f3/journal.py) | Bounded structural validation; complete journal collection remains unimplemented |

The F1 collector/helper entries retain `ENACTED=False`; the executor entry
refuses `REFUSED_UNBOUND`. Both F3 entries refuse `REFUSED_UNBOUND`;
the concrete adapters' operational configuration and evidence inputs remain unbound. Adding this source
does not wire it into installed services or activate the migration.

## Run the checks

From the repository root, on Linux with Python and Bubblewrap installed:

```sh
make test-hybrid-runtime-operations
```

The target runs 168 F1 collector/helper tests, 97 F1 executor tests and 87 F3
tests in isolated namespaces. They use synthetic observations, fake effects and
fixed disposable children. Source is
mounted read-only, with private temporary storage and no production home,
profiles, sockets, network or host process filesystem exposed. These tests
verify the implemented boundaries; live handoff qualification remains separate.

## Remaining work

- Complete the Foundation, semantic IO/native and cross-component integration
  acceptance work tracked by the implementation plan.
- Review exact runtime bindings and source-to-artifact assurance. Machine-bound
  activation drafts and operational evidence are outside this source bundle.
- Review and qualify exact enabled installation-executor bindings, then assemble
  the disabled remote adapters with complete owner census and controls, host
  recipe, current clock evidence and complete journal collection. The repository layout is not the
  future installed bootstrap closure.
- Qualify installation and live behavior, then finish the supported-host rollout,
  full verification, 24-hour burn-in and retirement of replaced components.

The journal projection still has open cursor-revisit/end-inclusion limits, and
a failed stop-receipt write can propagate `OSError` while the runner remains
stopped. The local operations README records these limits. Publication of the
reviewed source does not close those findings or campaign deployment items.
