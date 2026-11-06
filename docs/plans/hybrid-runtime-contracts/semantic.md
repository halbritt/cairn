# Semantic worker contract

Revision: `semantic/1`, proposed for X-01 acceptance. Characterization baseline:
`d3c255a4bb75438e93527eaa1d83da780fd11eea` (2026-11-06).
The semantic team owns this file. Overall owns the shared contract index;
Release consumes the packaging, service-switch and rebuild requirements.
This document records acceptance criteria, not implementation or deployment
acceptance. Shared-contract implementation waits for Overall's X-01 completion
notice. Read-only inventory and characterization of the existing Python worker
may proceed before that notice.

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
| S-04 | CAIRN-327 | Offline worker/model staging, fixed launchers and native-library evidence | S-B2 | S-02, S-03; Release's supported-host inventory |
| S-05 | CAIRN-328 | Switch/rebuild/reverse-rebuild tests and runbook | S-B2 | S-04, R-01 / CAIRN-303 |
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

All seven semantic delivery/milestone items are direct children of CAIRN-301;
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

Rust derivation identity must change when any consumed model/config asset,
backend/tokenizer version, native-runtime library digest/build identity,
vector-affecting build feature, preprocessing,
prefix, truncation, passage slicing, inference, pooling, normalization or
implementation source changes. Include a versioned derivation tag and a
deterministic digest of the relevant source and locked build inputs. Include
all effective vector-affecting settings. Exclude timestamps and checkout
paths. The worker must not claim Python's derivation identity.

S-01 must provide a comparison path that executes a native candidate directly;
the current comparator always prefixes candidates with Python, covers only
rank, and records mismatches without making them a failing exit status. The
new acceptance entry point must fail for a mismatch while keeping the existing
exploratory behavior separately characterized. Acceptance runs the actual
Python and Rust models for rank, stream and embed. Missing
assets, launchers or required model tests fail the acceptance run; skips do
not count as parity. Cargo build/test/acceptance commands are dedicated targets
allocated through Overall, so ordinary Go checks continue without Cargo.

The initial capture retains one maximum-document model gap: exactly 65536
punctuation bytes reached the 90-second document budget without a reply.
Tokenizer-only evidence for its 256 spans is not vector evidence. S-01 must
record an explicit disposition of that case with Overall and independent
review before claiming complete required-model coverage. Do not drop the
case, relax the deadline, or substitute its source-only geometry for a model
reply. Other successful maximum-document cases do not close this gap.

## Required failure evidence

The assigned review checks these fixture families against source before code:

- **F-RANK:** zero/65 notes, empty/oversize query, wrong body hash, body-byte
  overflow, 128 versus 129 chunk occurrences, decoded chunk above token
  bounds, malformed JSON, and nonfinite/zero inference vectors.
- **F-STREAM:** incomplete/oversize lines, wrong/absent response ID, missing
  result, unexpected/trailing output, second-frame failure, identity change,
  cache omission, corrupt cache, wrong-model cache, warm/cold score equality,
  actual idle exit/restore, and cache discard after failure/close.
- **F-EMBED:** all three operations, wrong request fields/types, Unicode ID
  boundaries, bad query projection, long-query retreat refusal, 65536-byte
  documents, invalid UTF-8 span boundaries, too many passages, bad dimensions,
  NaN/infinity/zero vectors, and model change after worker restart.
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

Existing anchors include [command tests](../../../semantic/command_test.go),
[stream tests](../../../semantic/stream_test.go),
[cache process tests](../../../semantic/stream_cache_test.go),
[embedding process tests](../../../semantic/embedding_command_test.go), and
[index tests](../../../semantic/index_test.go). Record which assertions run
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

The current installer stages `semantic/venv`, source workers and fixed
`worker`, `worker-stream` and `worker-embeddings` launchers. Inventory the
actual installed paths and active configuration before changing them. Preserve
Python rollback artifacts until the release retirement gates pass.

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
   Keep the existing schema. Measure availability and rebuild duration;
   uninterrupted dense coverage and instant rollback are not acceptance claims.
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

S-05 requires `make test-integration` and `make check` for store changes,
focused process/model checks, and lifecycle checks when service lifecycle or
backup/restore behavior changes. HC-02 additionally needs Release's exact
installed-source/binary/model manifest, active-service evidence and completed
re-index evidence. Setup acceptance cannot close S-01–S-05 or either milestone.

## Cross-team agreement and remaining acceptance

- The shared index adopts database/queue-wide exclusion and assigns S-B2 the
  semantic installer and first Cargo targets. Release's `release/1` draft
  agrees on the binary/model/native-library identity manifest, architecture/ABI
  requirements, fixed launchers, activation ownership and reverse rebuild.
- Release's R-01 / CAIRN-303 supplies supported hosts/architectures; its
  R-07 / CAIRN-309 supplies installed cutover and completed re-index evidence.
  Those evidence gates remain open.
- Review records acceptance or concrete missing fixtures against this revision;
  any contract change is sent to all consumers before dependent work merges.
