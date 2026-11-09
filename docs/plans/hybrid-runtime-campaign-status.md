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

The following slices are published through
[`fa831236`](https://github.com/halbritt/cairn/commit/fa831236302ef4e18c68d71e9544c3236fe40ff8)
on 2026-11-09. The Go libraries are callable source; the Rust libraries have no
executable worker or initialized model backend. Their publication does not
activate the migration on supported hosts.

| Component | Source and publication | Implemented behavior | Remaining acceptance and area owner |
| --- | --- | --- | --- |
| Go journal execution, recovery and inspection | [journal](../../internal/lifecycle/journal/journal.go), [execute](../../internal/lifecycle/journal/execute.go), [recover](../../internal/lifecycle/journal/recover.go), [inspect](../../internal/lifecycle/journal/inspect.go); [`4eb9c41c`](https://github.com/halbritt/cairn/commit/4eb9c41c017086e5012420747dcbb48158ea76fa) | Durable inbox intents, command/reply recovery and distinct journal-reader policies | Coordination: native caller/session integration, watcher and recovery command surfaces; the caller still supplies validated context |
| Go coordination locks and process observations | [locks](../../internal/lifecycle/coordination/locks.go), [owner lookup](../../internal/lifecycle/coordination/owner.go), [process scan](../../internal/lifecycle/coordination/process_scan.go); [`5a6364a9`](https://github.com/halbritt/cairn/commit/5a6364a9a2b2d9ed49c4c330cfafa96c44ba94f2) | Callable session/ledger/intent/watcher/cue locks, owner ancestry lookup and process-marker scans with unknown-coverage reporting | Coordination: native caller orchestration, cross-runtime locking and state-writer compatibility; full J-05/J-06 and installed behavior remain open |
| Go native sessions and recovery orchestration | [sessions](../../internal/lifecycle/coordination/session.go), [file host](../../internal/lifecycle/coordination/file_host.go), [journal adapter](../../internal/lifecycle/coordination/journal_adapter.go), [process bindings](../../internal/lifecycle/coordination/process_bindings.go), [recovery](../../internal/lifecycle/coordination/recovery.go); Stage 2B [`fa831236`](https://github.com/halbritt/cairn/commit/fa831236302ef4e18c68d71e9544c3236fe40ff8) | Native session state, concrete filesystem persistence and locks, journal projection, process hooks and recovery using transcript evidence | Coordination: remaining error classification, float-zero projection and process-field evaluation-order corrections, CLI wiring and runtime qualification; source publication does not complete J-05/J-06 |
| Go shared values and JSON profiles | [values](../../internal/lifecycle/value/value.go), [profiles](../../internal/lifecycle/value/profile.go), [writer](../../internal/lifecycle/value/encode.go), [operations](../../internal/lifecycle/value/operations.go); [`be67f6e5`](https://github.com/halbritt/cairn/commit/be67f6e58cc2d64b1fb1e45af5a3143418880bca) | Lossless decoded values, explicit reader/writer profiles and selectable duplicate-key handling, shared with the transcript consumer | Foundation: actual interpreter and per-route profile qualification, exact writer behavior and caller integration; source publication does not qualify a runtime profile |
| Go native transcript readers and proofs | [readers](../../internal/lifecycle/transcript/transcript.go), [Claude](../../internal/lifecycle/transcript/claude.go), [Codex](../../internal/lifecycle/transcript/codex.go), [proofs](../../internal/lifecycle/transcript/proofs.go); [`2ba1b330`](https://github.com/halbritt/cairn/commit/2ba1b33032e7f10af10c606e6424db2b4d684587), fixture correction [`f9ecf5fe`](https://github.com/halbritt/cairn/commit/f9ecf5fe7d52cc215d4060f6ac5625ccac68fbe6) | Pinned record reads, ownership and locator checks, harness-specific transcript evidence and shared profile-based JSON decoding | Coordination: native caller integration, remaining timestamp/path/read parity and Stage 2 serialization; full J-06 and installed behavior remain open |
| Shared Unix peer client and wake consumers | [client](../../internal/procpeer/client.go), [wake envelope](../../internal/lifecycle/wake/wake.go), [Claude channel](../../internal/lifecycle/wake/claude.go), [Codex queue](../../internal/lifecycle/wake/codex.go), [OpenCode](../../internal/lifecycle/wake/opencode.go), [Hermes](../../internal/lifecycle/wake/hermes.go), [Herdr](../../internal/lifecycle/wake/herdr.go); [`0ad5b419`](https://github.com/halbritt/cairn/commit/0ad5b419edd12a6dd757a185fbd7c83fa6468c03), extended by [`f6f0e427`](https://github.com/halbritt/cairn/commit/f6f0e42784d0e1eefc0c211d8a4b80b054f2ddab) | Peer verification before payload, bounded framing, wake binding and callable transport consumers | Foundation: shared primitive integration; Coordination: native turn/custody integration and installed behavior |
| Rust blank-text predicate | [library](../../rust/semantic-text/src/lib.rs), [boundary](../../rust/semantic-text/README.md); [`0c5cd5e3`](https://github.com/halbritt/cairn/commit/0c5cd5e3e0da570da369aa8a01f610021d5a88a0) | Fixed Python 3.11 whitespace classification over supplied strings | Semantic: caller binding and full rank/embed worker acceptance |
| Rust single-note score reduction | [library](../../rust/semantic-score/src/lib.rs), [boundary](../../rust/semantic-score/README.md); [`94cf6685`](https://github.com/halbritt/cairn/commit/94cf66857e7c96602f9dc3bc4b3a1d0984091c1c) | Ordered maximum over supplied chunk similarities, final clamp, scaling and ties-to-even rounding | Semantic: tokenizer/model execution, matrix arithmetic and actual worker/caller qualification |
| Rust decoded protocol, hash admission and cache payloads | [protocol](../../rust/semantic-protocol/src/lib.rs), [hash](../../rust/semantic-protocol/src/hash.rs), [cache](../../rust/semantic-protocol/src/cache.rs), [boundary](../../rust/semantic-protocol/README.md); [`e4e8e80d`](https://github.com/halbritt/cairn/commit/e4e8e80d93cf5bd2ca43bec817006198d7c05632), extended by [`66dd1540`](https://github.com/halbritt/cairn/commit/66dd15409359d1f28b32fab7896dec3e2c425053) | Lossless decoded values and ordered prefix guards, body hashes, atomic cache restore and payload codecs with shared JSON rendering and byte measurement | Semantic: real constructor/model identity, tokenizer and worker/runtime qualification; unsupported depth remains explicitly pending |
| Rust JSON input and reply codecs | [codec](../../rust/semantic-protocol/src/json.rs), [boundary](../../rust/semantic-protocol/README.md); [`b244e26b`](https://github.com/halbritt/cairn/commit/b244e26b85673d15308f7fbac9f36c311beaad23), corrected in [`114cf677`](https://github.com/halbritt/cairn/commit/114cf677082af2729e0e744a3c83fee6eb40bceb) | Byte framing, Unicode/JSON decoding, reply serialization and shared cache measurement over lossless values | Semantic: inputs deeper than 64 containers remain pending; actual worker, model, runtime policy and transport qualification remain open |
| Rust rank and stream engine | [request engine](../../rust/semantic-rank/src/rank.rs), [bounded I/O](../../rust/semantic-rank/src/stream.rs), [backend interface](../../rust/semantic-rank/src/backend.rs), [boundary](../../rust/semantic-rank/README.md); [`d1c78f8c`](https://github.com/halbritt/cairn/commit/d1c78f8cbffc01a19bbc5469f4fca0e65210e9f6) | Joins protocol, hash admission, cache and score components; orders backend calls and flushes each stream reply before reading the next request | Semantic: actual tokenizer/model/numerical backend, executable and Go supervisor; component control-flow evidence does not qualify those implementations |
| Rust prepared tokenizer and input tensors | [preparation](../../rust/semantic-tokenizer/src/prepared.rs), [tensors](../../rust/semantic-tokenizer/src/tensors.rs), [boundary](../../rust/semantic-tokenizer/README.md); [`d732f5fe`](https://github.com/halbritt/cairn/commit/d732f5fe292aff17cfc54b580c1ada433ac795e2), fixture correction [`37a50424`](https://github.com/halbritt/cairn/commit/37a50424872d8b3229ad97b80dd3ed8aa0e4888a) | Uses Tokenizers 0.23.2 with supplied model configuration; maintains separate model/rank policies and produces batch-one int64 IDs, attention masks and zero model token-type rows | Semantic: prepared-asset comparison of all 294 token IDs/lengths, actual model/ORT and numerical backend, executable and Go supervisor remain unqualified |
| Rust ORT runtime and raw CLS inference | [runtime/session owners](../../rust/semantic-ort/src/lib.rs), [native bridge](../../rust/semantic-ort/native/bridge.c), [boundary](../../rust/semantic-ort/README.md); [`2cdedfbf`](https://github.com/halbritt/cairn/commit/2cdedfbf56d67848f1921cd5ed81253b2539eaf2) | ORT 1.29 C-API runtime/session ownership, batch-one int64 feeds and raw 384-component CLS bits from the named output | Semantic: actual model/session execution and raw-bit parity, numerical backend, worker and Go supervisor; component tests do not load ORT or run a model |
| Rust full-occurrence matrix-vector stage | [layout](../../rust/semantic-math/src/layout.rs), [runtime owner](../../rust/semantic-math/src/lib.rs), [native computation](../../rust/semantic-math/native/compute.c), [boundary](../../rust/semantic-math/README.md); [`4b54d04e`](https://github.com/halbritt/cairn/commit/4b54d04ef03dd8ffbbf9b09b57abb307d7857741), accepted lock [`dc6a066d`](https://github.com/halbritt/cairn/commit/dc6a066d76e58d0222cb8e8234c0ba1a29c70651) | Preserves all passage occurrences in source order at width 384; selects empty output, one native dot product, or one full-matrix GEMV using the pinned OpenBLAS bridge | Semantic: both normalization stages, actual native loading and numerical parity, complete rank backend, worker and Go supervisor; no native computation was exercised by the component tests |
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
Both normalization stages and full numerical/runtime qualification remain open.
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
The review identified remaining differences in command/decode error
classification, float-zero truthiness through journal projection, and process
field evaluation order for malformed short stat records. These are assigned to
the existing author for a focused correction batch. The accepted component
review does not establish complete behavior equivalence, CLI integration or
installed runtime qualification.
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
