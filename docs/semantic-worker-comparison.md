# Semantic worker comparison

This tooling follows the `semantic/4` evidence boundaries. Its own code review,
reference qualification and full S-01 acceptance are separate gates.

`scripts/compare_semantic_workers.py` compares fixed executable workers without
passing a Python interpreter or arbitrary arguments to the candidate. It runs
one-shot rank, streaming rank and embedding protocols. Each worker's reported
identity must match its own manifest; reference and candidate identities may
differ.

The harness is test tooling. A passing comparison does not activate a worker,
change the installer or establish full design acceptance. Results, raw replies,
runtime observations and qualification records belong outside the repository.

## Process mechanics

On Linux with `bwrap`, `gcc` and Python installed, run from the repository root.
Keep ambient worker/database selectors absent for each mechanics subprocess:

```sh
env -u CAIRN_EMBEDDING_WORKER -u CAIRN_SEMANTIC_WORKER -u CAIRN_SEMANTIC_STREAM_WORKER \
  -u CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED -u CAIRN_TEST_DATABASE_URL -u CAIRN_DATABASE_URL \
  python3 -B scripts/test_semantic_acceptance_isolation.py
env -u CAIRN_EMBEDDING_WORKER -u CAIRN_SEMANTIC_WORKER -u CAIRN_SEMANTIC_STREAM_WORKER \
  -u CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED -u CAIRN_TEST_DATABASE_URL -u CAIRN_DATABASE_URL \
  python3 -B scripts/test_compare_semantic_workers.py
env -u CAIRN_EMBEDDING_WORKER -u CAIRN_SEMANTIC_WORKER -u CAIRN_SEMANTIC_STREAM_WORKER \
  -u CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED -u CAIRN_TEST_DATABASE_URL -u CAIRN_DATABASE_URL \
  go test -race -count=1 -v ./semantic
make check
python3 -B scripts/verify_docs.py --tier 1,3
```

The comparator tests compile the checked-in
`semantic/testdata/executable-harness/protocol-worker.c` in a temporary directory,
create three fixed executable paths per side, and exercise the CLI's
`--phase mechanics` path. These ELF executables implement synthetic replies;
they contain no model or Rust implementation. The tests exercise direct native
invocation, distinct manifest identities, repeated stream replies, fresh cache
restore, refusal cleanup, exact score failure and an independent pairwise-cosine
failure. They also reject missing assets/cells, skipped tests and empty or
duplicate named test inventories.

Ten shared `stream_limit_cases` drive both the Python framed reader and the
unchanged Go `StreamCommand` through the compiled model-free fixture. Go bounds
the compact typed `{id,result}` plus newline at 65536 bytes; the raw line has
that bound only when cache is absent or null. Cache-bearing frames may contain
extra whitespace. The cache and outer-frame limits remain 600 KiB and
600 KiB + 65536 bytes. Cases also cover typed zero values, Go's HTML escaping
and UTF-8 encoding. These checks establish protocol mechanics only.

Normal Go tests skip the three opt-in `TestWorkerAcceptance*` model tests. Those
skips are visible and do not count as model coverage. The strict comparator runs
the named tests and rejects every skip, failure, missing test and zero-test run.
The three Go entry points are smoke checks: rank/stream use case 0, and embedding
uses `query-ascii` and `document-ascii`. They do not compare the two roles through
Go or execute the full input inventory. Direct-worker comparisons and later
Go-path acceptance remain distinct.

## Rust workers through Go

`cairn serve --semantic-rust-config /absolute/path/worker.json` selects an
operator-prepared Rust worker. With no flag, Rust is disabled. The flag is
mutually exclusive with `--semantic-command`, `--semantic-stream-command` and
`--embedding-command`; ambient environment variables do not select Rust.
The installer and running services are unchanged.

The JSON file contains one object, at most 32768 bytes, with no unknown fields.
This example is a path template, not a supplied runtime installation:

```json
{
  "mode": "stream",
  "executable": "/prepared/bin/cairn-semantic-rank",
  "model_dir": "/prepared/model",
  "ort_runtime": "/prepared/runtime/libonnxruntime.so.1.29.0",
  "openblas": "/prepared/runtime/libscipy_openblas64_-32a4b2a6.so",
  "gfortran": "/prepared/runtime/libgfortran-040039e1-0352e75f.so.5.0.0",
  "quadmath": "/prepared/runtime/libquadmath-96973f99-934c22de.so.0.0.0",
  "source_root": "/prepared/source"
}
```

All seven selections must be absolute existing paths. `model_dir` and
`source_root` must be directories; the libraries must be regular files and
`executable` must be an executable regular file. The Rust worker still validates
asset contents and native library selection. Go does not infer missing paths,
download assets or treat path validation as runtime qualification. Invalid
configuration returns an error before the API opens its identity file or store.

Mode `rank` launches `cairn-semantic-rank` for each request. Mode `stream` adds
`--stream` and owns a reusable rank process; its default idle lifetime is 30
seconds, adjustable with `--semantic-idle-timeout`. Mode `embed` selects
`cairn-semantic-embed` with separate query and document processes and the existing
asynchronous index owner. Rank/query requests retain their 25-second deadline;
document exchanges retain 90 seconds. Earlier caller cancellation still wins.
Cold model initialization is inside those deadlines. Qualify the chosen native
binary and build profile through Go; valid paths alone do not establish that a
cold request completes in time.

Go supplies the six explicit `--model-dir`, `--ort-runtime`, `--openblas`,
`--gfortran`, `--quadmath` and `--source-root` arguments without a shell. Children
receive the existing fixed offline environment plus `ORT_DISABLE_TELEMETRY=1`.
The three `LD_LIBRARY_PATH`, `LD_PRELOAD` and `LD_AUDIT` variables and the
database DSN are absent. Streaming rank retains the existing cache opt-in,
bounded frames, idle restore, busy refusal and child cleanup. This environment
policy does not place production workers in an OS sandbox.

The library entry points are `semantic.RustCommand`,
`semantic.RustStreamCommand`, `semantic.RustEmbeddingCommand` and
`semantic.StartRustIndex`. Their legacy executable-only counterparts retain
their signatures. Rust paths and mode are operator configuration, never request
fields or an arbitrary argument/environment list.

The standalone
[`go-supervisor-driver`](../semantic/testdata/go-supervisor-driver/main.go) builds
with `go build -buildvcs=false -o /tmp/go-supervisor-driver
./semantic/testdata/go-supervisor-driver`. It reads bounded NDJSON from stdin
and uses these production supervisors without opening a database. Rank/stream
inputs are `{"id":"case-id","request":{...}}`; outputs are
`{"id":"case-id","result":{...}}`, suitable for comparison against the frozen
19-case ranking reference. Embed inputs select `operation` (`identity`, `query`
or `document`) and `text`. A worker failure exits nonzero without substituting
scores or vectors. Case timings go to stderr.

Run builds and comparisons inside the unchanged campaign isolation wrapper.
For a prepared read-only input directory whose `stream.json` selects paths
under `/opt/runtime-inputs` (and `source_root: "/work"`), the Go-driven repeat is:

```sh
python3 -I -B scripts/test_hybrid_runtime_operations.py \
  --ro-bind "${RUST_RUNTIME_INPUTS:?}:/opt/runtime-inputs" -- sh -c '
    mkdir -p /tmp/bin && ln -sf /usr/bin/gcc /tmp/bin/cc &&
    export PATH=/tmp/bin:$PATH CC=gcc TMPDIR=/tmp &&
    go build -buildvcs=false -o /tmp/go-supervisor-driver ./semantic/testdata/go-supervisor-driver &&
    /tmp/go-supervisor-driver --config /opt/runtime-inputs/stream.json \
      < /opt/runtime-inputs/rank-requests.jsonl'
```

The directory must contain the already prepared executable, model, libraries,
configuration and frozen requests. Do not expose a production home to the
wrapper. Select `rank.json` to repeat through the one-shot supervisor.
The opt-in `TestRustReal*` tests use `CAIRN_RUST_RANK_TEST_CONFIG`,
`CAIRN_RUST_RANK_TEST_REQUESTS` and `CAIRN_RUST_EMBED_TEST_CONFIG` inside the
wrapper. Ordinary test runs skip them when the config selectors are absent;
protocol fixtures do not count as real Rust/model evidence. These Go checks
and measurements do not by themselves complete S-01, S-02, S-03, numerical
qualification or production acceptance.

## Observed continuous stream and idle handoff

`scripts/semantic_stream_evidence.py` drives the existing Go comparison binary
in stream mode inside `scripts/semantic_acceptance_isolation.py`. It requires
`strace` with `--trace-fds`, a stream configuration, the committed inventory,
frozen request/scalar-reference files, a retained capture manifest and a new
output directory. Its required options are `--driver`, `--config`, `--inventory`,
`--requests`, `--reference`, `--retained-stream-manifest` and `--output`.

The 38 inventory cells keep their case order and `cold`/`warm` labels. The
harness separately records one initial cold process and 37 requests on that
same child. It validates the complete frozen input digest and observes the
actual Rust input/output pipe frames; it does not launch 19 independent workers.
A separate idle case makes three exchanges: initial, warm and post-idle. It
requires the old `/proc` entry to disappear after the unchanged 30-second idle
lifetime, observes a distinct new PID, and compares the old reply's cache state
with the new child's first input. This establishes the cache handoff without
claiming an ORT inference-call count or a performance benefit.

The retained Python capture uses historical note aliases. The harness applies
the committed fixture producer's UUID5/URL ID adapter, records that mapping and
its source digest, and preserves every body, version, body hash and numeric
score. Missing retained stream cells remain named in the report. Reusing a
frozen scalar reference for repeated scores does not manufacture a missing
stream transcript. Raw traces and caches stay in the external output directory.
The observer's mechanics tests are in `scripts/test_semantic_stream_evidence.py`;
protocol fixtures and selected observations remain separate from full acceptance.

## Fixed workers and evidence gates

The CLI requires `--output` to name a new directory outside this checkout.
Fixed mode requires `--phase`, `--baseline-manifest` and `--candidate-manifest`.
Each manifest has this shape; paths and hashes must name existing prepared files:

```json
{
  "schema": "cairn.semantic-comparison-worker/1",
  "kind": "model",
  "workers": {
    "rank": {"path": "/prepared/rank", "sha256": "...", "model_sha256": "...", "runtime_libraries": [{"path": "/prepared/runtime/libonnxruntime.so", "sha256": "..."}]},
    "stream": {"path": "/prepared/stream", "sha256": "...", "model_sha256": "...", "runtime_libraries": [{"path": "/prepared/runtime/libonnxruntime.so", "sha256": "..."}]},
    "embed": {"path": "/prepared/embed", "sha256": "...", "model_sha256": "...", "runtime_libraries": [{"path": "/prepared/runtime/libonnxruntime.so", "sha256": "..."}]}
  },
  "assets": [{"path": "/prepared/model/tokenizer.json", "sha256": "..."}],
  "read_only_paths": ["/prepared/runtime"]
}
```

The JSON illustrates the schema, not a runnable asset set. Hashes are SHA-256;
`model_sha256` is the worker's derivation identity, separate from its executable
digest. A mechanics manifest uses `kind: "protocol-fixture"`, compiled ELF
executables and an empty asset list. Model manifests require an asset inventory;
include all source/model/runtime files that the selected workers depend on.
Each model worker entry also requires `runtime_libraries`, a nonempty list of
`{path, sha256}` entries for its actual loaded images. Pin ORT per mode; a Python
ORT extension also requires the NumPy `_multiarray_umath` and OpenBLAS images.
Every comparison invocation is checked against its own manifest's pins.
Declare only narrow runtime roots that the isolation wrapper can mount safely.
Additional roots must be selected subtrees of the OS home, `/usr` or `/tmp`;
the wrapper supplies its own restricted system mounts.

The `reference`, `rank` and `embed` phases currently enforce the same complete
three-mode comparison and test inventory. Their names label campaign stages;
none bypasses a gate. Model phases additionally require:

- `--reference-catalog`: schema `cairn.semantic-reference-catalog/1`, with every
  required cell in the committed inventory present once and `status: "qualified"`.
- `--raw-qualification`: reviewed `cairn.semantic-raw-qualification/1` evidence,
  `state: "qualified"`, `review_record`, `wrapper_profile: "default"` and the
  current isolation-module SHA-256. Each `roles.baseline`/`roles.candidate` binds
  `manifest_sha256`, all three executable digests in `workers`, and retained
  `arrays.ids`, `arrays.lengths`, `arrays.cls` paths/digests matching the frozen
  ordinary 294-text baseline. This is corpus-bounded evidence.
  A default capture with 242 executed rows expanded to 294 logical occurrences
  is not the conditional semantic/4 B1 proof. That proof requires its separately
  reviewed derived-W vehicle, pre-inference ID/length checks and the specified
  exact executions. This comparator does not supply that vehicle or promote an
  ordinary capture into it.
- `--reference-catalog-sha256` and `--raw-qualification-sha256`: caller-pinned
  digests of those exact artifacts. Missing or changed bytes refuse.
- `--review-receipt` and `--review-receipt-sha256`: a pinned
  `cairn.semantic-comparison-review/1` receipt binding `status: "accepted"`,
  `source_commit`, `module_sha256`, `catalog_sha256`,
  `raw_qualification_sha256` and `review_record {record_id, version, body_sha256}`.
  The parent runner reads the exact Cairn source using the existing hosted
  token-file selector without reading the token. The source must be published
  by the campaign's independent S-R1 reviewer and contain a JSON
  `cairn.semantic-comparison-verdict/1` with the same accepted bindings. The
  source commit's module bytes must also match. A local JSON `qualified` or
  `accepted` field cannot replace this record. No such accepted evidence receipt
  is created by the harness; missing qualification stays a failure.

- `--python`: the prepared Python interpreter; `--go-test-binary`: a semantic
  test binary compiled with `go test -c`; `--go-test2json`: the installed Go
  `test2json` executable. The prepared tokenizer must be a uniquely named
  `tokenizer.json` asset in the reference manifest.
- The reviewed `scripts/semantic_fixture_cases.py` producer. Its
  `ranking_cases()`/`embedding_cases()` IDs, order and explicit
  `expected: "accepted"` fields must match the committed inventory. No fallback
  generator or alternate disposition field is accepted. Canonical UTF-8 input
  hashes must also match the inventory.

The current review binding pins the isolation module against `source_commit`;
it does not bind the running comparator or inventory bytes and names the
campaign's S-R1 UUID directly. Future actual evidence review must cover those
facts. A fabricated JSON verdict cannot establish that review or actual
acceptance. The Go and prepared-Python test lanes also lack per-invocation
runtime binding; their smoke/skip results do not qualify an actual worker.

The checked-in
[inventory](../semantic/testdata/executable-harness/reference-inventory.json)
names 19 ranking cases (269 score entries), 38 cold/warm stream replies per
worker plus a separate fresh restore, nine positive queries, six positive
documents, 23 prepared Python tests, 21 Go process tests and three real-worker
Go entry points. A fresh direct-process restore is reported separately from
actual Go idle expiry. `TestWorkerAcceptanceStream` keeps the production
30-second idle timer and waits 31 seconds before its restore request.

Unqualified cells remain failures. In particular, this harness does not turn the
old direct-supervisor punctuation observation into real-Go evidence, qualify a
numerical oracle, reconstruct missing raw arrays or automatically run the
maximum-punctuation document. That input is excluded from automatic inventories.
Its separately authorized, reviewed capture needs its own frozen execution plan.

## Comparisons and records

Ranking requires exact integer scores, input order and source metadata. Stream
replies must preserve those values across repeated requests and cache restore.
Embedding checks require exact original UTF-8 spans and query projections.
Vectors must have 384 finite components and a finite nonzero norm. The harness
does not impose a unit-norm interval. Each corresponding vector must satisfy
`1 - cosine <= 1e-6`; independently, every query/passage cosine difference must
be at most `1e-6`. Norms and maximum component differences are diagnostics.
Worker normalization/unit-norm correctness is an explicit S-03-owned gate in
the inventory; it is not a new rejection interval in Go.

`report.json` records passed modes and failures. Raw bytes are retained in
`raw-*.bin`, with length, hash and a bounded base64 prefix in the report. Refusals
retain exit status, request/reply frames, stderr and pending stdout when present.
The isolation subdirectory records preflight, actual process/library observations
and cleanup. A missing executable/asset, malformed response, mismatch, incomplete
evidence or failed cleanup returns nonzero. `model_acceptance` remains false even
when `model_comparison_passed` is true; independent acceptance is separate.

The Python direct-worker permissiveness characterized in the semantic contract
is not changed. This comparator exercises reviewed positive JSON requests and
does not replace the negative-input capture corpus or Go's typed-input checks.

## Isolation API

The shared module exposes a small test boundary:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from semantic_acceptance_isolation import SemanticIsolation

with TemporaryDirectory() as scratch:
    with SemanticIsolation(read_only_paths=[], output_dir=Path(scratch) / "proof") as boundary:
        result = boundary.run(["/usr/bin/python3", "-I", "-S", "-c", "print('probe')"], timeout=5)
        assert result.returncode == 0, result.stderr
```

Run this from `scripts/` or place that directory on the import path. The sample
is a nonmodel probe. `output_dir` must be new; existing directories are refused.
`popen()` is available
for framed exchanges; the context owns all processes it starts. `run()` bounds
input/output progress, elapsed time and retained stdout/stderr. There is no
unisolated fallback.

`command_contract(env=None)` returns the exact declaration for the same `env`
argument used by `run()`/`popen()`: schema, profile, cwd, `environment`,
`cpython_environment` and justified `runtime_additions`. Default execution has
the four sanitized worker values plus bwrap's `PWD` equal to the declared cwd.
CPython's C-locale coercion adds exactly `LC_CTYPE=C.UTF-8`; its complete
in-process environment must equal `cpython_environment`. Custom `LANG`,
`LC_ALL`, `LC_CTYPE`, `PYTHONCOERCE_C_LOCALE`, `PYTHONUTF8`, `PYTHONHOME` and
`PYTHONPATH` are refused. Other supplied variables remain part of the exact
declaration. Derived-W retains its literal
HOME/XDG/TMPDIR/cache/bytecode settings and cwd, with no overrides. Stream and
direct embedding lanes declare `CAIRN_SEMANTIC_STREAM_CACHE=1`.

Each command owns a copied declaration in `command-*.json`, with a recorded
digest. `proof.environment_contract` describes the profile default;
`CompletedProcess.invocation` (or the Popen `_semantic_invocation` record) holds
the command's authoritative contract, role/mode, launcher PID/start and
declaration digest. The ambiguous `proof.model_environment` field is absent.
The proof accessor returns a copy. Proof/declaration files are read-only and
their digests are checked before another launch and during cleanup.

The bounded worker-deadline form is `IsolationError` with `code == "timeout"`.
Its `evidence` contains `failure_code`, bounded partial
`stdout`/`stderr` bytes, actual argv/exit code, `timeout_seconds`, invocation
metadata and `processes_joined`. Output bounds use `stdout_limit` or
`stderr_limit`. Context exit separately records cleanup; it must not be inferred
from a missing path or a caught exception.

Before worker launch, a nonmodel probe verifies a different network namespace,
no external network interface/reachability, a read-only root/canary, and a new
mapped OS home. With `HOME` absent, `getpwuid` and `expanduser` must resolve that
mapping, and its probe inode must match the host-owned disposable directory.
The owner token and telemetry-store paths must be absent. Runtime mounts are
read-only, with `ST_RDONLY` checked for every declared destination; broad owner
state, host `/proc`, writable shadows and escaping symlinks are refused.
Runtime dependencies and declared mounts still require review.

Opt-in Go smoke tests execute inside this outer boundary, then launch the unchanged production commands with
their normal sanitized environments and 25/90-second request deadlines. Go's
supervision source and the two-second index retrieval timeout are unchanged.

The observer follows actual descendants and records executable and mapped
library inodes/device IDs/hashes, correlated by role/mode and launcher PID/start.
`invocation_runtime(invocation, runtime_libraries)` requires exactly one actual
ORT-mapped descendant for that invocation and matches its loaded digests to the
pins. One worker cannot qualify a missing role/mode. The observer also checks
the actual child's proc exec-environment block against the declaration; that
block is distinct from CPython's later in-process locale coercion. Cwd is
declared and set by bwrap; `/proc/PID/cwd` is not observed. A bwrap
process alone cannot satisfy `runtime_evidence(require_ort=True)`.
Short-lived processes may evade sampling; missing evidence fails.
Numerical qualification also requires
the actual Python worker's self-maps and the frozen runtime identities.
PID descriptors tie cleanup to observed processes after launcher exit. The
context kills, waits for and reaps launchers, verifies observed descendants have
exited, joins observers, and removes only its exclusively created backing.
Namespace teardown also terminates unobserved namespace descendants. Telemetry
payloads are never read. Failed preflight removes its disposable home; existing
output or backing directories are preserved.
Read `disposable-home.json` for the actual backing path and initial
inode/device/owner/mode. `cleanup.json` records `all_observed_processes_exited`,
`backing_removed`, `errors`, invocations and `isolation_proof_sha256`. Require
both booleans and an empty error list; process joining is separate from overall
success or a seal/proof failure. Never guess a `mapped-home` path.

`DisposableBacking.create(sealed_root=..., task_root=..., name=...)` validates
the sealed profile, an explicit owned `0700` allowlisted task root, protected
overlaps and inode aliases before allocating one fresh child. `name` is one
basename; paths and existing children refuse. `DisposableBacking(path)` has no
allocation authority. The factory also requires the prefix's `--setenv` pairs
to equal the sealed environment.

`SemanticIsolation.sealed_oracle(...)` is a separate, pinned construction path
for the one sealed W oracle. It checks the authorized 31-file seal and all three
original prefix records, then constructs
the authorized `derived-W` profile by replacing only host-source operand 34 with
a fresh external private backing. The `--bind` flag, guest `/sandbox`, remaining
prefix/environment and sealed files stay pinned; declared additional tool/input
binds are read-only. Receipts include the original/effective prefix, exact
replacement/insertion, backing ownership/mode and before/after seal metadata.
This declared derivation needs its own raw/runtime qualification and independent
review. It never borrows the default wrapper's qualification. The
caller passes `task_scratch_roots` explicitly. Each root must be owned `0700`,
resolved without symlinks/aliases, and below the campaign's dedicated
`semantic-S-B1`/`semantic-S-B2` task roots. Named `/tmp/cairn-semantic-*`
disposable task directories support synthetic tests; actual B1 uses its own
external `semantic-S-B1` task directory. The whole owner cache or
`~/.local/share/cairn` is not an allowlist. Backing must be a fresh child of a
declared root and cannot overlap/alias sealed or live state. Root identity is
checked again at launch/cleanup, along with backing identity. The caller uses
the validated factory; after entering, the context owns removal. Identity
changes refuse launch/removal and are recorded as failed cleanup. New evidence
belongs outside the frozen oracle root and bounded
pipe output is captured there.
An end-to-end test uses manufactured 31-file evidence and a 97-argument prefix;
only the seal pin is patched inside the test. It exercises unchanged and
tampered seal outcomes without executing the real W preflight or any model.

The module also has a nonmodel CLI: `--read-only` (repeatable), `--output-dir`,
`--timeout`, optional `--require-ort`, then `-- COMMAND ...`. The CLI is a test
boundary, not a new production worker argv configuration interface.

## Legacy Python comparison

Omitting `--phase` retains the earlier `--python`, `--model-dir`, `--baseline`
and `--candidate` script mode, with two repeated storage cases after the shared
19-case producer. It still alternates launch order and reports wall time, CPU
time and RSS under its exploratory 20-second inner deadline. Wall time now
includes the isolation launch overhead; it is not a production-latency claim.

Legacy measurements are written beneath the actual writable isolation output,
so `/usr/bin/time` files remain host-visible after home cleanup. The measurement
plumbing has a compiled-fixture test that requires no fake model qualification.

Legacy model execution also requires `--raw-qualification`, its pinned digest
and the bound review receipt (with a null catalog digest for this exploratory
mode). Besides the common
schema/state/review/wrapper fields, `legacy_roles.baseline`/`candidate` bind the
exact `argv`, `script_sha256`, `python_sha256`, nonempty `model_assets` paths and
digests, and the three retained frozen-baseline arrays. Missing qualification
prints `semantic comparison refused:` and exits nonzero before starting a worker.
This mode explicitly runs Python scripts; it is distinct from fixed executable
mode.

Open tooling notes remain: legacy runtime pins do not have the fixed manifest's
nonempty ORT/NumPy/OpenBLAS validation, and rare setup or direct cleanup waits
can still expose `subprocess.TimeoutExpired`. Neither note is an actual-evidence
acceptance claim or permission to execute an unqualified lane.

Revert the harness files and opt-in test file to remove this tooling. No installed
worker, model asset, environment profile, service or production database is
modified by the change.
