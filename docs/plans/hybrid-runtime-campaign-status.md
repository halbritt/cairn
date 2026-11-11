# Hybrid runtime campaign: code and remaining work

The CAIRN-301 campaign remains incomplete. Its destination is a Go lifecycle
runtime, a Rust semantic worker and thin host adapters, as described in the
[architecture proposal](hybrid-runtime-consolidation.md) and
[implementation plan](hybrid-runtime-implementation.md).

Published source includes the Go lifecycle components, Rust semantic libraries
and operations tooling below. Component checks and source review establish only
their selected boundaries. Installation, runtime integration and campaign
acceptance remain open.

## Current owner policy

Owner decision `98d46758-c61c-42b4-9912-76cf9dfb0dc0@1` of 2026-11-10
selects rule C for Semantic acceptance: per-vector cosine within `1e-6`, a
bounded score shift of 500 units, rank order preserved outside near-ties, and
the retrieval-quality fixture passing. Exact integer scores (rule A) and
within-one-unit scores (rule B) are diagnostics. Truly discrete outputs remain
exact: spans, token counts, prefix bytes/hash, chunk caps and tie rounding in
isolated arithmetic tests. This supersedes earlier strict-score clauses below
and in the Semantic contract; amending that contract and reviewing the amendment
is a separate follow-up.

The owner replaced C2, FIRST and source-only execution holds with an isolation
rule: tests run in the shared isolation wrapper's namespace, which hides production home and
sockets, network and host processes. Tests may run any number of times. Authors
compile their code and run its tests there while writing and before review.
No admitted command, separate test worker or receipt is required. One reviewer
is required before landing. Coordinator correction
`3b3b5c42-26c2-40c9-a2f5-83708af3d453@1` supersedes the earlier dispatch limits;
runs already in flight keep their current operation.
Lifecycle and Semantic have separate release trains; retirement waits for both.
The embedded CPython host, C value graph and RTERM1 observer are paused, with all
artifacts retained. Foundation's [contract](hybrid-runtime-contracts/foundation.md)
records the owner's three-class lifecycle parity rule. No production change is
granted by these source publications.

Owner decision item 7 retains the Semantic tester's empty-input model/ORT load
scope after the `cairn-semantic-embed` binary lands: a read-only, no-network
sandbox and no inference. It grants no inference, runtime-kit preparation or
production change. Command-admission conditions from earlier dispatches no
longer apply. Other runtime acceptance, production, preserved-failure and
retry-policy holds remain.

## Inspect the implementation

The S-02 stream follow-through adds an external, test-only observer around the
landed Go driver. A selected isolated run completed all 38 frozen inventory
cells on one Rust child, with one initial cold process and 37 subsequent
requests on that same child. All 538 repeated score entries matched the retained
19-case scalar oracle exactly. A separately counted idle case made three
exchanges: the old process entry disappeared after 30.049786554 seconds, a new
PID appeared, and its first wire request contained the prior reply's exact JSON
cache state (25546 canonical bytes). Both Go owners were reaped with no live
Rust children. Ten observer mechanics tests passed. Production supervision,
the 25-second request deadline and 30-second idle lifetime are unchanged.

The retained Python stream transcript supplies 34 frames for 17 workload
queries. Its original note aliases are mapped by the committed fixture
producer's UUID5/URL rule; source bytes, versions, body hashes and numeric scores
are preserved and the mapping is recorded. All 34 frames and 408 scores matched
the frozen Python rank reference exactly, with the same model identity. Under
coordinator decision `d8b70f55-48fb-4c27-bfc5-f8405c56fe97/v1`, the four
`long-tail/cold`, `long-tail/warm`, `64-short/cold` and `64-short/warm` cells use
the corresponding frozen rank values plus the Rust stream observations. This
satisfies the decision's reference prerequisite: accepted coverage is 34
captured cells plus four rank-backed cells. The raw historical capture still
has 34 frames and `full_38_reference_available=false`; no replacement Python
capture was made. Combined review and landing remain pending. The observer
does not claim an ORT-call count, a cache performance benefit or full S-02
acceptance.

The Go supervisors now accept explicit Rust worker configuration through
`serve --semantic-rust-config`; Rust remains disabled when the flag is absent.
Rank, stream and embed use fixed arguments for the six absolute asset/library/
source selections and a sanitized offline environment with
`ORT_DISABLE_TELEMETRY=1`. Legacy executable-only APIs retain their signatures.
The same Go owners enforce framing, identity, cancellation, busy refusal and
child cleanup, with the existing 25-second rank/query, 90-second document and
30-second stream-idle limits. The
[operator configuration and Go comparison driver](../semantic-worker-comparison.md#rust-workers-through-go)
describe this source slice; installation and running services are unchanged.

For request `981f254e-5b9d-497d-8231-075b320534ee@1`, the author built both
native binaries with locked, offline Cargo in the optimized release profile
and exercised them through the real Go supervisors in the unchanged isolation
wrapper. Both one-shot and stream completed all 19 frozen ranking cases:
269 of 269 integer scores were identical to the reference, with all 3138
pairwise ordering relations unchanged in each mode. Maximum observed request
times were 5.514390 seconds for one-shot and 3.847115 seconds for stream.
Separate real-worker checks passed cold/warm rank equality, a restore request
after the actual 30-second idle expiry, embedding identity, normalized
384-component vectors, UTF-8 passage coverage and distinct query/document
processes. The ordinary semantic and CLI race suites plus `make check` also
passed in the wrapper.

Independent source review accepted exact candidate `fea6475b`
(`ea22a262-29f5-4935-9aee-2d24f4afe9ad@1`); its four advisories require no
correction for this landing. The tester's separate Go-driven comparison on the
same candidate completed all 19 rank and 19 stream cases, with all 269 scores
exact and all 3138 ordering relations unchanged in each mode
(`69b57ab0-e20b-43b4-874a-4526e8c0b5bd@1`). Embed completed 37 cases and paired
all 549 vectors, including all 44 vectors from the 22 two-passage documents.
All vectors were float32 bit-exact against the frozen references and within
the `1e-6` cosine criterion; the maximum observed absolute cosine difference
was `4.440892098500626e-16`. Counts, original UTF-8 spans, substrings and query
projection metadata matched. The whole measurement exited 0 in 309.105591539
seconds; the longest document took 67.295834 seconds within the unchanged
90-second limit. No closed model, vector or idle measurement is repeated for
landing. The prior 97-row/Latin-reference gap strings in author artifacts are
historical and superseded by the tester's current paired result.

The review identifies the author idle test's limit: it establishes equal
results after a 31-second wait without independently observing the restarted
PID or cache restoration. The tester retains a supplemental cleanup status 98
from its immediate process-group check; subsequent reconciliation found the
owned group absent and the private namespace ended. That preserved cleanup
observation is separate from the successful worker comparisons. Final landing
validation runs the ordinary race suites and `make check` on the committed,
rebased source to bind their evidence to that exact tree.

A separate Go-driven sample in each mode matched the four mapped, sealed
memory-file images to the selected ORT/math library hashes and inspected the
actual sanitized child environments. The samples had no observed `connect`
calls or changes to the disposable home/cache files; a loopback control
produced a captured `connect` call. This is bounded sample evidence, not complete
runtime or input-closure qualification.

An earlier unoptimized development build hit the unchanged 25-second deadline
on its first real stream request after one-shot had completed. That failed run
is retained separately; the release-profile pass does not erase it. No timeout
was extended and no protocol fixture replaced a model failure. The new
`TestRustReal*` cases skip when their explicit asset/config selectors are absent.
These measurements establish their selected comparison and process boundaries.
Full S-01, S-02, S-03, near-tie/retrieval evaluation, T1/CPU-FENV, numerical and
runtime qualification, and production acceptance remain open. Earlier rows
below retain their component evidence boundaries.

The rank initialization follow-through applies the same ten-field rule-C policy
to `ConstructionReport::rank_identity`, which the shipped
`LoadedBackend::initialize` admission already calls. The shared observation
owner retains the missing/null leaves and their sorted names. Rank keeps its
complete tag, two normalizations, matvec, score reduction and cache manifest;
it never uses embedding's mode projection. Hard validation, model-asset closure
and unrelated owner gaps still refuse. No scoring, tokenization, thread/FENV,
protocol, runtime-loader or dependency change is included. Independent source
review accepted candidate `2b29837b` in round 1 with no source delta
(`2f9822d6-9a37-4cd8-bae8-857357d0ccd2@1`). One shared-wrapper component command
passed 12 selected fixtures: the seven rank-policy checks and five existing
checks whose assertions changed. It exited 0 in 58.550153336 seconds, with
89 older checks filtered out and zero retries
(`b9cef357-0ebc-41b7-9c46-540b7e6ac8e0@1`). The exact 12 names and summary
matched the admitted selection, and all 105 source pins remained unchanged.
There was no separate seven-only run or full closed-20, all-294 or embedding
stream replay. The reviewer-selected five checks address the changed legacy
assertions; no optional advisory code fix is included.

These synthetic fixtures did not execute actual ORT-owned rank initialization,
produce the 269 integer scores or establish numerical, near-tie, retrieval,
runtime or production acceptance. The component's retained artifact evidence
covers its selected compiler graph and surviving objects, not complete
header/link-input closure or CPU/FENV behavior. Landing preserves the reviewed
and tested Rust source and consumed inputs; only this status evidence is
updated. Actual native initialization and ranking measurements remain separate.
Earlier strict-rank initialization wording below describes the prior source and
is superseded only for those ten unavailable observations.

The item 7 source change applies the owner's rule-C initialization exception to
embedding identity. Its canonical manifest names exactly ten unavailable
build/session/native observations in sorted `unobserved_observations`, retaining
the absent/null values without defaults. Valid observed empty values remain
observed; malformed observations, other owner gaps, model-asset closure and
actual construction/file-mapping errors still refuse. The strict rank gate and
the three-field embedding wire identity are unchanged. Private fixtures cover
the named gaps, observed/unobserved distinction, digest sensitivity, invalid
observations and initialization before EOF/identity input. Independent source
review accepted candidate `0513d6b8` in round 1 without conditions
(`71f9aac6-e06d-4fc7-b88d-d3f9b3d75d21@1`). One command in the shared isolation
wrapper passed the lead-selected 20 fixtures: ten item 7 checks and ten
compatibility, refusal and wire checks, with 75 older checks filtered out. The
run exited 0 in 88.094604474 seconds with zero retries
(`8719a0ef-3e5f-4fe4-a87b-148918d55685@1`). The reviewer's proposed 60-fixture
selector was not run. The raw selected names and summary matched the prepared
manifest, and all 105 candidate source pins remained unchanged.

These synthetic fixtures did not call the real factory load/construct path,
open model/runtime payloads or run a production worker, ORT session or inference.
The retained component evidence covers its selected compiler/artifact graph and
surviving objects; it does not establish complete temporary-object/link-input or
header closure, actual-session T1, CPU/FENV behavior, rule-C numerical acceptance
or runtime/production qualification. Landing updates only this status wording;
reviewed/tested Rust and consumed inputs are preserved. Historical missing
observation refusals below describe their earlier source; they are superseded
only for these ten unavailable embedding observations. Reviewer notes about a
currently unreachable reserved input key and an additional empty-flags fixture
remain nonblocking; no optional code fix is included.

The table records published source and extensions through the native embedding
entry, tested as `d41be9e7`, on 2026-11-10. The Go libraries
are callable source. Rust has explicit rank/stream and embedding entry targets;
model initialization and actual-worker qualification
remain open. Publication does not activate the migration on supported hosts.

| Component | Source and publication | Implemented behavior | Remaining acceptance and area owner |
| --- | --- | --- | --- |
| Go journal execution, recovery and inspection | [journal](../../internal/lifecycle/journal/journal.go), [execute](../../internal/lifecycle/journal/execute.go), [recover](../../internal/lifecycle/journal/recover.go), [inspect](../../internal/lifecycle/journal/inspect.go), [json constants](../../internal/lifecycle/journal/json_constants.go); [`4eb9c41c`](https://github.com/halbritt/cairn/commit/4eb9c41c017086e5012420747dcbb48158ea76fa), Stage 2C [`249f1eda`](https://github.com/halbritt/cairn/commit/249f1edabb9453dc664e3e0a9e12cbcbde42a080) | Durable inbox intents, command/reply recovery, distinct reader policies, default-JSON non-finite literals with CPython `_CONSTANTS['NaN']` container-identity retry semantics, `NaN` scalar formatting (`"nan"`), and selected Class A Python/Go differential cases | Coordination: C2 child-process parity and installed runtime qualification; the caller still supplies validated context |
| Go coordination locks and process observations | [locks](../../internal/lifecycle/coordination/locks.go), [owner lookup](../../internal/lifecycle/coordination/owner.go), [process scan](../../internal/lifecycle/coordination/process_scan.go); [`5a6364a9`](https://github.com/halbritt/cairn/commit/5a6364a9a2b2d9ed49c4c330cfafa96c44ba94f2) | Callable session/ledger/intent/watcher/cue locks, owner ancestry lookup and process-marker scans with unknown-coverage reporting | Coordination: native caller orchestration, cross-runtime locking and state-writer compatibility; full J-05/J-06 and installed behavior remain open |
| Go native sessions and recovery orchestration | [sessions](../../internal/lifecycle/coordination/session.go), [settlement](../../internal/lifecycle/coordination/settlement.go), [file host](../../internal/lifecycle/coordination/file_host.go), [journal adapter](../../internal/lifecycle/coordination/journal_adapter.go), [process bindings](../../internal/lifecycle/coordination/process_bindings.go), [recovery](../../internal/lifecycle/coordination/recovery.go); Stage 2B [`fa831236`](https://github.com/halbritt/cairn/commit/fa831236302ef4e18c68d71e9544c3236fe40ff8), corrections [`3c5fa30a`](https://github.com/halbritt/cairn/commit/3c5fa30a9746decc381b17e8aebb7056b36c5fb4) | Native session state, concrete filesystem persistence and locks, lossless `NaN` journal projection, callable completion scope and turn-end reminder/settlement (`hook` wiring open), process hooks and recovery using transcript evidence | Coordination: binary entrypoint and installed service integration, C2 child-process parity and installed runtime qualification; full J-05/J-06 acceptance remains open |
| Go coordination service bindings and selected CLI surfaces | [CLI](../../internal/lifecycle/coordination/cli.go), [compact CLI](../../internal/lifecycle/coordination/compact_cli.go), [inbox CLI](../../internal/lifecycle/coordination/inbox_cli.go), [grant bindings](../../internal/lifecycle/coordination/grant_bindings.go), [Codex health](../../internal/lifecycle/coordination/codex_health.go), [service bindings](../../internal/lifecycle/coordination/service_bindings.go); Stage 2C [`249f1eda`](https://github.com/halbritt/cairn/commit/249f1edabb9453dc664e3e0a9e12cbcbde42a080) | Time, grant and Codex health bindings; selected inbox, compact, drain and recovery commands, plus selected Class A Python/Go differential cases | Coordination: `hook` and `watch` return `ProfileUnavailable`; binary entrypoint wiring after Foundation F-01/F-04, C2 child-process parity and installed runtime qualification remain open |
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
| Rust first-pass and full-occurrence normalization | [stage owners](../../rust/semantic-math/src/normalization.rs), [native arithmetic](../../rust/semantic-math/native/normalization.c), [boundary](../../rust/semantic-math/README.md); [`11f1395e`](https://github.com/halbritt/cairn/commit/11f1395ebae685a72a05ee4541c7f2d91790d6eb) | Normalizes raw 384-component CLS rows with epsilon, preserves immutable first-pass cache rows, reconstructs all occurrences, then checks every second-pass norm before division | Semantic: rank/cache composition is published below; actual NumPy, CPU and floating-environment correspondence, rule C numerical acceptance and initialized backend/worker acceptance remain open |
| Rust owning native rank backend | [composition](../../rust/semantic-backend/src/lib.rs), [cache/arithmetic binding](../../rust/semantic-backend/src/arithmetic.rs), [initialization boundary](../../rust/semantic-backend/README.md); [`5b6f1f31`](https://github.com/halbritt/cairn/commit/5b6f1f310635b9384d199f9869f22cbab39561b1) | Connects the prepared tokenizer, ORT session, first-pass cache rows and full-occurrence normalization/matrix computation under one session owner; restored rows retain their bits without repeating first normalization | Semantic and Release: missing initialization observations still prevent a usable factory; actual assets/model/math parity, worker, Go supervisor and runtime qualification remain open |
| Rust typed session-observation serializer fixtures | [serializer](../../rust/semantic-backend/src/identity.rs); tested source `d0ad8f82` | Private read-only traits let owner-created ORT records and private typed fixtures use the same serializers; four selected fixture checks passed with independent expected values | Semantic and Release: concrete ORT getter forwarding and the original actual-session T1 comparison remain owed; required observations, NULL globals, numerical/runtime qualification remain open; current owner policy above applies |
| Rust rank/stream entry and launch plan | [entry](../../rust/semantic-backend/src/bin/rank.rs), [parser](../../rust/semantic-backend/src/bin/rank_entry/plan.rs); tested source [`cb6dbc90`](https://github.com/halbritt/cairn/commit/cb6dbc906661e771431a22238d0fd75f5afedd18) | Explicit `cairn-semantic-rank` target and pure parsing of six absolute asset/library/source selections; rank-once or stream dispatch with exact stream-cache opt-in; 11 bounded parser, private-driver and synthetic-cleanup checks passed | Semantic and Release: production entry/native wiring, real owner drop/cleanup, exit/stderr behavior, model initialization, actual-session T1, worker and Go supervisor remain unqualified; installer and Go routing are unchanged |
| Rust original-text embedding-query projection | [projection](../../rust/semantic-tokenizer/src/query.rs), [character slicing](../../rust/semantic-tokenizer/src/offsets.rs); tested source [`d58aed45`](https://github.com/halbritt/cairn/commit/d58aed45a45901b0de155b832a5697bda7161f24) | Untruncated/unpadded rank-tokenizer counts, original-text prefix selection with at most eight re-encodings, character-to-UTF-8 slicing and prefix-only hash/count metadata; 20 bounded owned-fixture checks passed, with 11 prior tests filtered out | Semantic and Release: Python binding/version correspondence, actual prepared-token parity, actual-session T1 and model/worker/runtime qualification remain open; component input attestation has the header limit below |
| Rust original-text document-passage projection | [projection](../../rust/semantic-tokenizer/src/document.rs), [shared character slicing](../../rust/semantic-tokenizer/src/offsets.rs); tested source [`474b756f`](https://github.com/halbritt/cairn/commit/474b756fd4f47e7c20ce23b4ca1887db1fdb893e) | Typed original passage text and UTF-8 spans, adaptive 128/96 windows including the final tail, complete span validation before ordered default-special re-encoding; 22 bounded owned-fixture checks passed with 31 prior tests filtered out | Semantic and Release: Python binding/version correspondence, actual prepared-token parity, actual-session T1 and model/worker/runtime qualification remain open; current selected header coverage does not close item2's historical gap |
| Rust embedding derivation projection | [projection](../../rust/semantic-backend/src/identity/embedding.rs), [observed-input owner](../../rust/semantic-backend/src/identity.rs); tested source [`58b3a299`](https://github.com/halbritt/cairn/commit/58b3a299cf3e323bdd54fb38fe20be7ee2d8e2c1) | Gated owned `ConstructionReport` embedding manifest and digest with distinct mode tag/constants and retained common inputs; 17 pure owned-fixture checks passed with 35 prior tests filtered out | Semantic and Release: actual observation rows/NULL globals, real construction/session, actual-session T1 and native embedding execution/binary qualification remain owed; pure digest sensitivity is not observed completeness or usable identity |
| Rust embedding response loop | [composition](../../rust/semantic-backend/src/embedding_response.rs), [private fixtures](../../rust/semantic-backend/src/identity/embedding/tests/response_tests.rs); tested source `008efd1b` | One owning collaborator and protected identity before any read, landed query/document projections, whole-batch vector guards, bounded complete serialization and per-reply write/flush; 32 selected checks passed with 52 prior library tests filtered | Semantic and Release: real native initialization/session cleanup, actual-session T1, numerical/runtime and Go-supervisor qualification remain open |
| Rust native embedding adapter and entry | [adapter](../../rust/semantic-backend/src/embedding_native.rs), [entry](../../rust/semantic-backend/src/bin/embed.rs), [shared cleanup](../../rust/semantic-backend/src/bin/entry/cleanup.rs); tested source `d41be9e7` | Owning native adapter and explicit six-path embed target, protected observation refusal, first-pass-only rows and shared failure/cleanup handling; 17 selected checks passed (7 library/10 embed), 84 older library tests filtered | Semantic and Release: production main, concrete load/construct/getters and model/session behavior remain unexecuted; actual initialization, T1, numerical/runtime, Go supervisor and owner decision item 7 EOF load remain open |
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
numerical and campaign acceptance remain open under the current owner policy
above. The all294-before-model and numerical/runtime/resource/production
requirements remain; no actual worker initialization is established.

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
owner rule C and isolation policy above apply. All 294 token IDs/lengths must
precede model execution. Full P1-P5 acceptance, numerical/runtime/resource/production
and preserved-failure requirements remain open. This entry
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
T1, required observations/NULL globals, all294-before-model and full P1-P5,
numerical/runtime/resource/production and preserved-failure requirements remain
open under the current rule C and isolation policy above. `REQUIRED`
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
Required observations/NULL globals, all294-before-model and full P1-P5,
numerical/runtime/resource/production and preserved-failure requirements remain
open under the current rule C and isolation policy above. `REQUIRED`
declares 11 rows, not 11 measured missing observations. This source publication
establishes no readiness, usable initialization, actual prepared-token parity or
campaign acceptance.

The embedding derivation projection (`58b3a299`) was accepted in independent
source review round 1 with no condition. One isolated, offline, locked
`--lib embedding_identity4_` invocation passed all 17 exact selected checks with
35 prior tests filtered out and zero retries. Private owned report fixtures
exercise exact literal rank/embedding canonical manifests and digests, required
missing/null/invalid refusals, common and mode sensitivity, provenance exclusion,
set/sequence rules and repeated projections without mutation. They are not
actual native observation rows. Inherited native units were compiled and the
new library-test harness linked and started; project ORT/BLAS/getter/model/session
runtime paths were not selected.

`ConstructionReport::embedding_manifest` and `embedding_identity` first reuse
the existing cached-missing, recomputed-required and canonical-validation
guards. The rank digest is discarded, not used as embedding evidence. The owned
projection retains common implementation, compilation, native, session, asset
and prepared-tokenizer inputs, including unknown future common fields. It
binds `cairn.rust-embedding-worker/1`, `bge-original-passages/1`, dimensions 384,
the exact query/document policies and the first-normalization epsilon/order.
Rank-only second normalization, matvec, reduction and cache policy declarations
are removed from embedding mode without mutating the report or changing rank
behavior. Object keys remain sorted, sequences retain order, floating constants
remain decimal strings, and absolute installation paths and image device/inode
metadata remain provenance. Existing observer, source-snapshot and actual
initialization behavior is unchanged.

Three source-review qualifications remain. Common algorithm keys beyond the
first normalization rely on the private production owner supplying them and on
the exact literal fixture. Guard-before-projection order is direct in the code
but has no dedicated combined test. Mode-constant sensitivity establishes hash
sensitivity; correctness of those constants rests on the exact literal-byte
test. Passing these fixtures supplies neither actual observations nor usable
initialization, Python parity or runtime acceptance. Actual build/link/CPU and
session-global observations, real construction/session, actual-session T1 and
actual native embedding execution and binary qualification remain owed.

For this component, all 268 selected system-header preflight, observed and
postflight paths matched, with zero additional system headers. This is
selected-path coverage, not complete future, transitive, include or runtime
attestation. Item2's historical 198-preflight/20-post-run-only gap remains
unclosed. A whitespace-tolerant receipt-parser correction reconciled six joined
exit records from the same retained trace, without repeating the component
invocation or changing source/receipt inputs.

Landing preserves the reviewed/tested source, manifests, locks and native
inputs without repeating the component command. Required observations and NULL
globals, full P1-P5, actual-session T1, all294-before-model and
runtime/resource/production and preserved-failure requirements remain open under
the current rule C and isolation policy above. `REQUIRED` declares 11 rows,
not 11 measured missing
observations. No readiness, usable identity, initialization, parity or campaign
acceptance is claimed. The conditional post-item6 EOF grant above has not been
executed; runtime-kit preparation remains outside this landing. The response
composition follows below; the actual native adapter/entry awaits item 6.

The embedding response loop (`008efd1b`) was accepted in independent source
review round 1 with no condition (`18ff302d-1d4f-4d2b-ad2d-c4ba1d237e7e@1`).
One offline, locked `--lib embedding_response5_` component invocation passed all
32 exact selected names with 52 prior library tests filtered, zero retries and
an elapsed time of 58.836583017 seconds (`141dacdd-6366-40f1-8a12-c51ad087cd25@1`).
The rank binary's 11 tests, doctests and dependency test harnesses were excluded.
The selected bodies use an owned synthetic report, WordPiece tokenizer and
scripted factory/session/vector/I/O/drop collaborators. Compilation/linkage and
harness startup occurred; actual project ORT/BLAS/model/session/native-getter
paths were not exercised.

`run_embedding` consumes one owning factory before any read, including immediate
EOF and identity-only input. It derives embedding identity through the owner's
protected report guard. Trait success alone establishes no native readiness.
Query responses embed the landed projection's exact prefixed question and
retain its metadata. Document projection completes all span/count/token guards
before the first ordered original-text batch call. The complete batch count
precedes all 384-shape/storage, finite and nonzero row guards; only then are
FIRST-normalized float32 values widened for JSON, retaining signed zero and
without a rank second normalization. Existing codecs enforce the 512 KiB input
line and 4 MiB output line bounds, including LF. Complete serialization precedes
write-all/flush for each reply. Codec, projection, backend and I/O failures stop
before the next logical frame; the session drops on EOF or failure. Synthetic
ownership/drop evidence does not qualify an actual native lifecycle. The actual
`NativeFactory` embedding adapter and `cairn-semantic-embed` entry are supplied
below; their concrete runtime paths remain unexecuted.

Four nonblocking review limits remain: inherited read/write loops do not prove
Python EINTR retry parity; no loop-level `QueryPrefixError` fixture exists;
late-projection trace labels are synthetic and establish only injected-barrier
cutoff; and widening/finite/nonzero checks assume default floating settings,
with DAZ/CPU/FENV qualification still open. Direct nonfinite/oversize/depth
serializer fixtures prove the shared helper, not loop-level reachability.

The accepted component graph has 100 noncustom units (25 host/75 target),
23 scripts, nine host macros and 123 artifacts, with 53 production native units,
five archives and 102 correlated native/support/link statuses. Lead intake
verified 241 sealed receipts, 24 plans, 2008 ordered chunks, the full artifact
multiset, all 100 source pins and cleanup. All 268 current selected system-header
preflight/observed/postflight paths agree with zero extras. This does not prove
complete transitive/include/runtime attestation or repair item2's historical
198-preflight/20-post-run-only gap. B2's retained materialization and its separate
cleanup belong to the lead.

Landing preserves the tested Rust tree, source patch, manifests, locks and
native inputs while retaining foreign trunk documentation. Only the campaign
status changes intentionally among the 100 pinned paths. Full P1-P5, required
observations/NULL globals, actual-session T1, all294-before-model and other
numerical/runtime/resource/production/preserved-failure obligations remain open
under the owner policy above. `REQUIRED` declares 11 rows, not 11 measured
missing observations. This component pass and source publication establish no
usable initialization, Python parity, actual worker, Go-supervisor or campaign
acceptance. The conditional post-item6 no-inference EOF load remains unexecuted.

The item 6 native embedding adapter and explicit `cairn-semantic-embed` target
(`d41be9e7`) were accepted in source review round 1 without conditions
(`20486459-809f-4d25-b519-d673256c38e4@1`). One command inside the shared isolation
wrapper passed all 17 exact fixtures: seven library checks with 84 older tests
filtered, and ten embed-bin harness checks with zero older tests filtered. It
ran for 60.304456579 seconds, exited 0 and had no retries
(`0bd601df-b802-4e85-bf79-3b990cd17bcc@1`). Rank's 11 tests, doctests, dependency
harnesses and closed selectors were excluded. The wrapper's ordinary built-in
checks ran; no extra wrapper self-test or special wrapper receipt was added.

The adapter retains the real loaded owner and gates readiness on protected
embedding observations. Its concrete path uses existing batch-one FIRST
normalization, with no rank second normalization or matvec. The entry reuses
the six absolute-path parser, refuses rank stream mode, and shares cleanup
collection with rank. The moved cleanup block and rank dispatch/main/test bodies
preserve behavior, with only visibility and equivalent generic error typing
changed. The selected fixtures exercised structural row bits/layout, synthetic
report gates and panic-on-I/O failure, parser/dispatch, synthetic owner-drop and
dual-error collection, diagnostics, and configuration-only `NativeFactory::new`
plus empty cleanup drains. They did not exercise production `main`, `run_native`,
concrete load/construct/observe/getters, the three native-session methods,
PyString conversion in that adapter, ORT/BLAS/model/session or actual cleanup.
The new test harnesses are not production-worker execution evidence.

The observed graph matched 102 noncustom units (25 host/77 target), 23 custom
artifacts, nine host macros and 125 total artifacts, with 53 production native
units and five archives. Lead intake independently verified all 105 source pins,
34 sealed receipts, exact raw test names/output, the full artifact multiset and
lossless trace digest. Link evidence explicitly lacks hashes for 85 embedding
and 216 library temporary objects removed during teardown; five stable object
hashes per harness remain. Do not infer complete linker-input hashing, archive
member extraction, function behavior or CPU/FENV coverage. All 268 selected
system headers match prospective pre/post pins with zero extras; this is not
complete include/transitive/runtime attestation and does not close item2's
historical 198-preflight/20-post-only gap. B2's retained materialization and its
separate cleanup belong to the lead.

Review qualifications remain: duplicate `--stream` returns the closed parser's
Duplicate error while still exiting 2; embedding does not read the shared
stream-cache field; backend/native/tokenizer detail strings are not sanitized,
although the raw request buffer is not formatted; and no signal-handler,
MXCSR/denormal or actual ORT/OpenBLAS floating-mode behavior was observed.
Inherited EINTR, DAZ/FTZ, N1-N4 and helper-only limits remain. All truly discrete
outputs stay exact; numerical acceptance uses the owner rule C above.

One existing contract-precedence sentence cites owner decision
`98d46758-c61c-42b4-9912-76cf9dfb0dc0@1` in this code change, without a separate
review round. Landing preserves tested Rust code, manifests, locks and native
inputs; campaign status and the backend README's test-status paragraph are the
intentional documentation updates. Required observations/NULL globals, full
P1-P5, actual-session T1, all294 IDs/lengths before model, numerical/runtime
acceptance, production, preserved failures, retry policy and the original R1
draft remain open under current policy. `REQUIRED` declares 11 rows, not 11
measured missing observations. Only B2 may perform the separately granted ONE
no-inference EOF-only model load after landing and exact command/sandbox/all294
admission. No model load, inference, runtime-kit preparation or production
change occurred in this component or author landing.

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
make test-isolated CMD="go test ./..." RECEIPT="/tmp/isolation-receipt.json"
```

The operations target runs 168 F1 collector/helper tests, 97 F1 executor tests
and 87 F3 tests in isolated namespaces, and `make test-isolated` (or
`scripts/test_hybrid_runtime_operations.py [--ro-bind <dir>] -- <command>`) runs arbitrary Go,
Python or Rust test commands in the same sandbox. Every run masks real-user
account homes (`/root`, `/home/*`) and service state directories while keeping
standard OS directories (`/`, `/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`, `/dev`,
`/usr/games`, `/nonexistent`) intact (`2692f81b-bf25-46b1-9bcc-98e46df5796c` v1,
`52ddb307`, `0fc910a9`), mounts a fresh child-PID `procfs`, binds explicit
read-only toolchains and `/work` with writes on `tmpfs`, and starts with an
in-sandbox self-test (`positive_system_checks`: runs `/bin/true`, `/bin/sleep 0`
and `/bin/bash -c 'exit 0'`, and requires `/usr/sbin`, `/dev`, `/bin` and `/sbin`
to list non-empty) that fails closed if any masked home is
readable, any Unix socket is visible under a bound path, any network route
exists or `/proc` shows a process outside the sandbox
(`98d46758-c61c-42b4-9912-76cf9dfb0dc0` v1).
These tests verify the implemented boundaries; live handoff qualification
remains separate.

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
