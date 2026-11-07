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
Keep ambient worker/database selectors absent for Go mechanics:

```sh
python3 -B scripts/test_semantic_acceptance_isolation.py
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

Normal Go tests skip the three opt-in `TestWorkerAcceptance*` model tests. Those
skips are visible and do not count as model coverage. The strict comparator runs
the named tests and rejects every skip, failure, missing test and zero-test run.
The three Go entry points are smoke checks: rank/stream use case 0, and embedding
uses `query-ascii` and `document-ascii`. They do not compare the two roles through
Go or execute the full input inventory. Direct-worker comparisons and later
Go-path acceptance remain distinct.

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
in-process environment must equal `cpython_environment`. Custom locale or
interpreter-control variables are refused. Derived-W retains its literal
HOME/XDG/TMPDIR/cache/bytecode settings and cwd, with no overrides. Stream and
direct embedding lanes declare `CAIRN_SEMANTIC_STREAM_CACHE=1`.

Each command owns a copied declaration in `command-*.json`, with a recorded
digest. `proof.environment_contract` describes the profile default;
`CompletedProcess.invocation` (or the Popen `_semantic_invocation` record) holds
the command's authoritative contract, role/mode, launcher PID/start and
declaration digest. The ambiguous `proof.model_environment` field is absent.
The proof accessor returns a copy. Proof/declaration files are read-only and
their digests are checked before another launch and during cleanup.

The public timeout form is `IsolationError` with `code == "timeout"`, not
`TimeoutExpired`. Its `evidence` contains `failure_code`, bounded partial
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
block is distinct from CPython's later in-process locale coercion. A bwrap
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
disposable task directories support synthetic tests. The whole owner cache or
`~/.local/share/cairn` is not an allowlist. Backing must be a fresh child of a
declared root and cannot overlap/alias sealed or live state. Root identity is
checked again at launch/cleanup. The `DisposableBacking` caller owns exclusive creation; after entering, the context
owns removal. New evidence belongs outside the frozen oracle root and bounded
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

Revert the harness files and opt-in test file to remove this tooling. No installed
worker, model asset, environment profile, service or production database is
modified by the change.
