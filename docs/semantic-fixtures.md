# Semantic reference fixtures

[semantic/4](plans/hybrid-runtime-contracts/semantic.md) defines acceptance.
The input module and capture/import CLI implement the S-01a preparation
surface. They do not accept S-01, Rust parity, packaging or deployment.
Keep raw outputs, arrays, native binaries and runtime observations outside
every repository worktree.

## Input seam

[`semantic_fixture_cases.py`](../scripts/semantic_fixture_cases.py) imports only
the Python standard library. Each call returns fresh dictionaries.

| Export | Input inventory |
| --- | --- |
| `ranking_cases()` | 17 retrieval-quality queries, long-tail and 64-short: 19 requests, 269 note scores |
| `embedding_cases()` | Nine named positive queries and six named positive documents |
| `embedding_failure_cases()` | 63 typed-text negatives, including all 29 Python whitespace characters, empty text and byte overflow |
| `consumed_punctuation_case()` | Import-only maximum punctuation input; never a new capture request |
| `wordpiece_boundary_case(tokenizer)` | Extra input with `##` at chunk start 320, built using an explicitly supplied prepared tokenizer |

Rank inputs contain `id`, `query` and `notes`. Each note contains `record_id`,
`version`, `body`, and its UTF-8 `body_sha256`. Note UUIDs match the frozen
comparator's UUIDv5 construction. The canonical sorted-key, compact UTF-8 JSON
digest of all 19 cases is
`ebeecaa4a508ec4cd735ae6f6844f3c96dd30f4d48ef85e7caadbc4cdc9420ba`.
The workload file itself is hash pinned; changed input refuses.

Positive and typed-negative embedding inputs contain `id`, `operation`,
`text` and `expected`. Positives use exactly `"accepted"`; typed negatives
use exactly `"refused"`. Form worker requests using only `id`, `operation`
and `text`. No permissive expectation alias is supported.
Query IDs are `query-ascii`, `query-cjk`, `query-emoji`, `query-combining`,
`query-special-tokens`, `query-exact-512`, `query-over-512`,
`query-4096-bytes` and `query-token-empty`. Document IDs are `document-ascii`,
`document-cjk`, `document-mixed-unicode`, `document-trailing-decision`,
`document-maximum-latin` and `document-maximum-cjk`.

The canonical sorted-key, compact UTF-8 JSON digest of the 15 positive embedding
cases, including expectation metadata, is
`feb24e50fd6b37b7405afd8f972ec6bf57c086820db22d8ee96381c4f26e44a2`.
The corresponding three-field worker-request projection has digest
`6e1bafddc5a00b061ca56281845a09b58766cf4f97e53bcc0da15a2f350062cf`;
the metadata correction leaves those worker inputs unchanged.
The separate import-only punctuation observation records
`observed_direct_supervisor_disposition: "timeout"` and
`real_go_disposition: "unmeasured"`. These are observations, not expectations.
It is absent from both positive and typed-negative embedding inventories.

The WordPiece builder never loads a tokenizer. Supply the prepared tokenizer
inside the reviewed isolation boundary, freeze the returned input and tokenizer
digest, and retain its actual token/decode observation. A manufactured tokenizer
in a unit test verifies builder mechanics only. This extra case does not alter
the 294-occurrence ranking baseline.

Print the input-only inventory without model dependencies:

```sh
python3 -B -S scripts/capture_semantic_fixtures.py inputs
```

## Import retained evidence

The importer requires Python 3.11 or later and Git, but no model packages.
Supply the independently retained SHA-256 of each bundle's `SHA256SUMS`;
do not derive a new expected seal from changed contents.

```sh
python3 -B -S scripts/capture_semantic_fixtures.py import \
  --bundle "$S01_CHARACTERIZATION" afbbc828e964ea9d446a3d12a2b80c48e823d09547600f9688bd45fe7c6774d5 \
  --bundle "$S01_ARITHMETIC" d7287ca702bc017d3c42ee40280bc7b3164665565c4483064fad90d1edb23cb2 \
  --bundle "$S01_CONSUMED_ORACLE" ef04e09d5bb7d906740da0f6406b14b4de3d14a6dfe664d313ce8ebb87815a4d \
  --output "$S01_NEW_CATALOG"
```

The output directory must be fresh and outside all Git worktrees. Import checks
every sealed file, opens relative paths without following symlinks, and rechecks
bytes when reading structured evidence. Unsealed paths, changed hashes, duplicate
JSON keys, nonfinite values and missing provenance refuse. No archive is modified.
New tensor archives are parsed without NumPy or pickle; dtype, shape and bytes
must match their descriptions.

`catalog.json` enumerates 77 preparation cells: 19 rank, 38 stream cold/warm,
one idle restore, 15 positive embeddings, one raw-294 arithmetic gate,
one prepared-23 test population, one punctuation numerical oracle and one
S-01b failure-inventory handoff. The last cell refers to S-B2's detailed inventory;
it is not a single replacement test for that inventory.

Importing the three retained bundles yields 24 missing cells and 53
unqualified cells. Missing observations include 16 unobserved cold stream cases,
long-tail/64-short stream cold/warm,
idle restore, maximum Latin document, raw-294 tensor qualification and S-01b
failure evidence. The 34 legacy stream frames are observations of 17 first/repeat
pairs in one shared-notes process; only frame zero was cold. All subsequent
frames fill warm cells, including both frames of each later pair. Legacy arithmetic contains float32 stages
but lacks actual int64 snapshots and native library inode proof.
The retained 23-test result is historical and is not a fresh isolated run.

The original maximum-punctuation 90-second timeout remains direct-supervisor
characterization. Its real-Go disposition is unmeasured. The single
longer 180-second numerical attempt is consumed and **UNQUALIFIED**. Its 256
vectors and source spans can be imported, but changed launcher/environment,
unproved Go-generated bytes, the supervisor sampler error and absent wrapper
invariance prevent numerical coverage. Do not rerun it or increase its deadline.

## Ordinary trace mechanics and capture hold

Actual `capture` and non-preflight `_collect` currently refuse before wrapper
import or session construction. The accepted pretoken gate and separate
semantic/4 B1 proof vehicle remain pending. There is no caller flag, receipt
pointer or hash that bypasses this hold. A dictionary matching the input
digests is a mechanical check, not observed tokenization or execution authority.

The internal tracer/orchestration is for ordinary default-wrapper references.
It cannot produce or import a conditional B1 proof: manifests require
`capture_scope: "ordinary-default-reference"`, `conditional_b1_proof: false`
and the shared wrapper's `default` profile. A `derived-W` proof refuses here.
The separate B1 vehicle must use the exact permitted derivation, check IDs and
lengths before inference, and meet every semantic/4 requirement. No W backing
or limited model attempt is created by these preparation tools.

The expected receipt is `cairn.semantic-isolation-review/1`, with `status` equal
to `accepted`, the wrapper `source_commit` and `module_sha256`, and a
`review_record` containing `record_id`, positive `version` and `body_sha256`.
These are provenance pointers, not authentication. The external pre-run installed
reference selection is not an isolation-review receipt or a qualification.

The wrapper interface is `SemanticIsolation(read_only_paths=..., output_dir=...)`,
a context manager exposing `command_contract()`, `run(argv, timeout=...,
stdout_limit=..., role=..., mode=...)`, `invocation_runtime(invocation, pins)`,
`proof`, and `runtime_evidence(require_ort=True)`. The shared wrapper owns child
reaping and disposable-home cleanup. The collector calls `command_contract()`
without overrides, saves the declaration as its own `collector-command.json`,
and passes it to the child with `--command-contract`. The returned
`result.invocation.contract` must equal that input. Its declaration file and
digest, role, mode, launcher identity and command must agree. The wrapper's
profile-default `proof.environment_contract` does not replace a command's
authoritative contract.

The preflight-only collector compares its own environment and cwd exactly with
`cpython_environment` and `cwd`. The separate exec `environment` includes `PWD`
at that cwd. CPython's `LC_CTYPE=C.UTF-8` addition must be explicitly declared
and justified in `runtime_additions` when locale variables are absent or empty;
there is no optional-variable allowance. HOME must be absent, with actual `getpwuid`,
`expanduser` and probe inode matching the declared home. Network checks occur
in the actual collector namespace. Preflight is not model admission.

Each successful model command must pass `invocation_runtime` with all three
pinned native-library paths and hashes. Its own runtime observation is joined
to that invocation's launcher PID/start, role/mode, child process start and PID
namespace, executable and native-library identities. The child's in-process
environment and the observer's proc exec block remain separate observations.
A worker observed for one command cannot supply another command's evidence.

Ordinary outputs are created privately with mode `0700`. Cleanup uses the
shared module's `disposable-home.json` declared backing and initial inode/device/
owner/mode and payload exclusion, its final `cleanup.json` joined-process/removal/
errors receipt, the exact `isolation-proof.json` digest and invocation bindings,
and observed backing absence. A guessed path's absence never proves cleanup.
Successful cleanup does not establish successful capture.

The sole wrapper timeout is `IsolationError` with `code: "timeout"`; output caps
use `stdout_limit` and `stderr_limit`. A `cleanup` error can retain the command's
`failure_code` plus both process and context cleanup errors. The collector
retains the bounded byte-valued stdout/stderr as binary artifacts and retains
the structured invocation, exit and failure evidence. It does not interpret
`TimeoutExpired` as a second public wrapper contract. Unexpected interface,
collector and cleanup exceptions are retained and sealed, then exit nonzero.
Failure sealing includes only explicit public artifacts and never traverses a
private backing or copies telemetry payloads. FIFOs and sockets refuse.
Wrapper-owned `command-*.json`, `isolation-proof.json`, `cleanup.json`,
`disposable-home.json`, `runtime-observations.json` and `read-only-probe` are
retained under their original names; collector artifacts use separate names.

This consumer preparation adds no W backing constructor or compatibility
adapter. The separate conditional B1 vehicle still needs the corrected,
reviewed factory that validates an owned S-B1 task root and frozen/protected
overlap before creating a fresh child. Its exact B1/B2 source pair and synthetic
seam require independent review before any actual execution admission.

Frozen worker Python is compiled from the exact verified bytes; adjacent pyc
is not executed or altered. The manufactured tests exercise that behavior.
Trace hooks observe frozen `Scorer.score` and FastEmbed normalization without
replacing their arithmetic. Typed artifacts retain IDs/masks/token types, shapes,
raw float32 CLS output, first normalization, rank's second normalization,
matrix-vector operands/results, unrounded similarities, note ownership and
occurrence-to-execution maps. Replay must match bits. Each trace and manifest
separates observed executions, logical rows and mapped duplicates.

The runtime reader records executable and loaded-library device/inode/hash,
package/source hashes, Python compiler/ABI, NumPy build configuration, ORT
provider/session settings, OpenBLAS runtime getters and resource measurements.
The CPU/kernel class hashes canonical architecture, byte order, pointer width,
sorted effective ISA/OS vector-state capabilities, and observed BLAS core/config.
Marketing name, feature order and incidental paths do not select the class.
Unknown capabilities or library core refuse. Import requires the pinned ORT,
NumPy and OpenBLAS images, corresponding actual-child observations, declared
environment/home facts, array mappings and final cleanup receipts. Even complete
ordinary observations remain unqualified.

The old per-case rank path has 242 execution observations and 52 mapped duplicate
rows: 294 logical rows, not 294 executions. A post-hoc expanded digest match
cannot establish the separate B1 proof, pre-inference checks, or repeat
determinism. The independent baseline identities remain pinned:

| Bytes | SHA-256 |
| --- | --- |
| IDs, int64 little endian | `1762a94e5b785ffcfa300f404252994026b18a71f763086f297f89a9809394a9` |
| Row lengths, int64 little endian | `a81f2c9f819d10049549d14dcdb38983febf0a41cbfe314421e4f70bba8ded33` |
| Raw CLS, float32 little endian | `1f7ba8c969c2053385ce6e89528d2decea78b627e9fb6ef40cfc322fd760b623` |

These are `b31016f5` v1 digest observations; its scratch arrays are not retained
artifacts. Do not create another unisolated baseline. Matching these logical
rows under a default wrapper does not qualify the oracle's W derivation or
establish invariance for every query/document input.

The ordinary path uses traced Python calls and compact UTF-8 request encoding.
It does not execute the real Go supervisor or claim Go-generated request bytes.
S-B2 owns those runs, stream/cache/idle cases and process/wire failures. Failed
attempts retain commands and available artifacts; output discarded by a failed
wrapper is labelled unavailable rather than reported as empty.
No inference execution of this new collector has yet been qualified.

## Results and validation

| Exit | Meaning |
| --- | --- |
| 0, `inputs` | Synthetic input inventory printed; no model run |
| 0, `_collect --preflight-only` | Default-profile non-model diagnostic; no capture admission |
| 2, `capture` | Actual capture unavailable pending accepted pretoken gate/proof vehicle |
| 2 | Refused/failed, with a JSON diagnostic; missing dependencies cannot pass |
| 3, `import` | Catalog written, preparation incomplete; explicit missing/unqualified cells |

This CLI never upgrades an integrity seal, manufactured fixture, skipped test,
or partial phase to model acceptance. Import always reports an incomplete
S-01 preparation catalog; the independent acceptance runner consumes reviewed
references and supplies its own required phase evidence.
Exact integer scores/input order, pairwise cosine error at most `1e-6`, and
bit-exact arithmetic are separate gates. Isolated ties are arithmetic controls,
not claimed model observations.

Run the model-free tests and repository checks:

```sh
env -u CAIRN_EMBEDDING_WORKER -u CAIRN_SEMANTIC_WORKER -u CAIRN_SEMANTIC_STREAM_WORKER \
  -u CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED -u CAIRN_TEST_DATABASE_URL -u CAIRN_DATABASE_URL \
  PYTHONPATH=scripts "$S01_PREPARED_PYTHON" -B -S -m unittest \
  scripts/test_semantic_fixture_cases.py scripts/test_capture_semantic_fixtures.py
make check
python3 -B scripts/verify_docs.py --tier 1,3
```

The tests cover comparator-derived golden inputs, dependency-free imports,
real bwrap with blocked model imports, declared environment refusal, source-pyc
resistance, canonical timeout/cap partial bytes, merged cleanup failures,
per-invocation binding refusals, proof-digest cleanup, typed arrays, finite float32 values, exact
wire objects, UTF-8 boundaries and manufactured python-reference import gates.
Prepared NumPy runs the frozen scorer, normalization and ONNX input preparation
with a mocked session and synthetic tokenizer data. ORT, FastEmbed package and
tokenizer-model imports are blocked in those children; no inference occurs.
These manufactured observations establish mechanics only.
The existing real-tokenizer/model tests require the reviewed
isolation boundary and remain a separately counted 23-test obligation.
