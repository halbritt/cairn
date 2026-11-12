# S-01 reference evidence

The committed [inventory](../scripts/testdata/semantic-reference-inventory.json)
and [runner](../scripts/check_semantic_reference.py) separate retained reference
evidence, fresh model-free tests and unresolved acceptance criteria. Raw model
replies, native binaries, arrays and generated reports stay outside Git.

## Campaign closure and limits

The owner-directed CAIRN-301 reset on 2026-11-12, recorded in
`cb71136b-cd84-4fc2-b84f-eb247b975548/v1`, accepts the S-01 reference and
failure-characterization scope for CAIRN-322, CAIRN-323 and CAIRN-324. The
retained audit covers 77 of 78 parents and 236 of 238 subcases; accepted S-02
and S-03 parity evidence remains separate. This decision changes the campaign
closure boundary, not the recorded test outcomes.

Two synthetic maximum-byte subcases remain limitations:
`F-EMBED/65536-byte-document/Latin65536` lacks the complete per-worker terminal,
stdout and restart tuple; `F-EMBED/65536-byte-document/CJK65535` also lacks that
tuple and the retained CJK input is 65,536 bytes rather than the named 65,535.
These are not reported as passing observations. No maximum-size rerun,
deadline change, new reference or retry-until-success is required for closure.
Historical raw/FENV, punctuation and timing observations retain their original
limits; the immutable audit can still report incomplete catalogue coverage.

The same reset selects the existing retrieval-quality fixture at
[`core/testdata/retrieval-quality.json`](../core/testdata/retrieval-quality.json)
for HC-01. Its current executable entry is
[`TestDocumentationRetrievalQuality`](../core/retrieval_quality_trial_test.go),
run with an owned disposable database and `CAIRN_RETRIEVAL_REPORT`. It reports
lexical retrieval and relevance observations; it does not introduce an absolute
relevance threshold or establish model quality by itself. Accepted real-worker
parity and indexed-comparison evidence remains part of HC-01. This removes the
open-ended request for a new quality contract; it grants no production cutover.

The fixture passed on 2026-11-12 with the race detector: one test, no skips,
12 notes, 17 queries and 68 query/order/mode observations. At least one authored
relevant note was returned in 55 of those observations; the other 13 remain
visible in the report. Test success establishes the fixture's integrity and
execution checks, not perfect relevance. The generated report is retained
outside Git with SHA-256
`01d1ab496339ca2cf43ed32721bbbe56614799616d7228b8ea9ab9e456705854`.

The inventory fixes test names, counts, source hashes, five model asset hashes
and retained artifact hashes before execution. The runner rejects missing or
changed inputs, skipped or zero-test suites, command failures and absent Go
package completion. It parses actual unittest and Go JSON outcomes. A summary
with a passing count cannot replace the pinned raw outcomes. Modifying a score,
span, order or projection breaks the reviewed retained artifact binding.

The 23 prepared Python selectors include the actual tokenizer test; that test
is one of the 23, not a twenty-fourth test. The six native harness tests use
compiled native process fixtures and invoke candidates directly. Their synthetic
values establish process and comparison mechanics. They do not establish native
model quality. The 21 Go selectors exercise the existing process owners without
selecting model workers or a database. S-01 does not require an unimplemented
Rust worker.

The unchanged earlier 12-test comparator suite starts its own isolation wrapper.
A retained attempt to run it inside another wrapper failed because the inner
`bwrap` could not read `/proc/sys/kernel/overflowuid`. The six new native fixture
tests run directly inside the existing boundary. The earlier suite's accepted
tooling results remain separate; the failed attempt is retained.

## Evidence and remaining criteria

| Criterion | Selected evidence and limit |
| --- | --- |
| Rank | Frozen 19 requests and 269 note scores; accepted packaged comparison also retains exact input/score/order bindings. |
| Selected stream replies | 34 actual Python replies / 408 scores plus four rank-backed cells / 130 scores. Coordinator `d8b70f55-48fb-4c27-bfc5-f8405c56fe97/v1` selected the latter under the frozen source. These are 38 selected references, not 38 historical captures. |
| Stream phases | One initially cold child followed by 37 warm exchanges. The inventory's historical cold/warm names do not relabel the actual phases. |
| Idle restore | Separate accepted Go observation: one case, three exchanges, observed old-child exit and replacement, retained cache bytes. It is not a thirty-ninth selected stream reply or a fresh Python reference. |
| Embeddings | Nine positive query and six positive document cases; retained vector closure supplies maximum Latin and overlapping tails. Exact input, projection, UTF-8 span and vector counts remain bound to their accepted artifacts. |
| Prepared tests | A fresh isolated 23-test run is required; an old unisolated `OK` does not supply it. |
| Failure evidence | The checked S-01b ledger names all 78 required IDs and expands compound cases. Its audit reports observed scopes and exact residual subcase IDs. A complete catalogue or passing test count does not close missing tuples. |
| Raw/FENV | Historical first-normalized vectors do not establish original raw CLS, actual historical mappings or floating-point environment. Unproved evidence remains named. |
| Maximum punctuation | The direct-supervisor 90-second observation and consumed, unqualified 180-second attempt remain historical. The protected raw/A-B/real-Go route is held; this runner never invokes it. |
| Retrieval quality | The 2026-11-12 reset selects the existing retrieval-quality fixture for HC-01; report its result and limits alongside accepted real-worker parity. |

The old CAIRN-322/323/324 review findings describe predecessor candidates.
The producer tooling and combined model-free seam were accepted and landed as
`d0d61205` and `157b207e`; the real-module both-profile N1 test landed as
`58b48092` (CAIRN-409). These closures do not qualify model captures or the
conditional punctuation proof. This slice preserves those distinctions rather
than reopening the accepted tooling findings.

The selected report reuses the isolated 23-test prepared pass from the earlier
named phase, with its report SHA and original inventory SHA. It records six
native fixture tests, 21 Go process tests and 63 typed-text refusal cases from
the later run. The typed cases use the
actual prepared tokenizer and the frozen worker's response method with inference
forbidden. Their tuple is exception type/message, absent result and zero
inference calls; they do not claim a model worker's process-exit tuple.
The report verifies 248 files under the retained characterization seal and
61 vector-closure, 61 Go-comparison and 65 packaged-result artifacts, including
28 separately listed stream traces. Nineteen historical direct-Python refusal
tuples remain labelled observations. Unbound failure case IDs require exact
source/input/test reconciliation before deciding that a new execution is needed.
They are not an automatic capture list. F-PACKAGE and F-REBUILD retain their
separate S-04/S-05 owners.

## Per-case refusal ledger

[The ledger](../scripts/testdata/semantic-refusal-ledger.json) binds the existing
19 F-RANK, 25 F-STREAM, 19 F-EMBED, ten F-PROCESS and five F-IDENTITY IDs to
source hashes/anchors, fixture inputs and six expected disposition fields:
Go error/result, discovery/coverage, process exit/kill, stdout prefix, cache
custody and restarts. The separate
[input catalogue](../scripts/testdata/semantic-refusal-inputs.json) expands 238
named boundaries. These authored recipes are expectations, not execution
records. Accepted boundaries such as 128 occurrences, ignored keys, echoed
version types, wrong-model cache and omitted legacy projection remain successes.

Each audit preserves the 19 historical direct-Python refusals, the 63 typed
function refusals, three historical positive controls and selected accepted
package/idle observations with their original scope. Where an accepted report
does not record exit status or stdout bytes for an individual case, those fields
remain unknown. Existing Go test outcomes locate protection but do not supply
unrecorded per-case tuples. Source inspection cannot fill an observed field.

Three fixed collectors add missing observations without changing production
workers or owners:

- `failures --python-home PATH --output NEW` captures 32 source-worker refusals
  before model construction. The model path is deliberately nonexistent.
- `processes --driver NATIVE_ELF --output NEW` exercises the unchanged Go owners
  against disposable process doubles. Build
  [the native driver](../scripts/testdata/semantic-refusal-driver/main.go) inside
  the unchanged isolation wrapper. Raw Go results survive alongside errors.
  Per-process traces retain actual exit/kill status, group signals, direct-child
  waits and stdout bytes received by Go. These fixtures do not use a real model.
  The decoder handles `strace -xx` descriptor escapes and unfinished writes;
  absent retained reads after a write remain unknown. `processes --reuse-report
  PATH --reuse-report-sha256 SHA --output NEW` requalifies retained raw traces
  without another worker execution. It preserves the prior report and its
  original tool pins, verifies unchanged actors/binary/raw files, and records
  the withdrawn projection separately from the new decoder result.
- `source-failures --evidence-root PATH --python-home PATH --site PATH
  --runtime-binding PATH --assets PATH --output NEW` runs fixed missing source
  controls through the frozen Python runtime and five assets. Each case has a
  fresh mapped disposable home and records its own correlated ORT process,
  seven native library bindings, exact input/output/error bytes and cleanup.
  These are protocol controls, not a new numerical reference or quality corpus.

All collectors use the existing isolation wrapper, offline inputs and new
output directories. The source-model controls use the original worker settings;
no thread-cap or telemetry suppression experiment is added. Selected telemetry
metadata records only whether the disposable mapped-store path exists; it never
reads database contents or owner identifiers and does not claim zero effects.
A cache field in these captures describes wire snapshots; an internal atomic
restore assertion still needs its own function observation.

Bind these reports to `audit` with `--refusals PATH --refusals-sha256 SHA`,
`--processes PATH --processes-sha256 SHA` and `--source-refusals PATH
--source-refusals-sha256 SHA`. The audit reuses their sealed bytes and names
their report pins. It checks exact membership, current source/input bindings,
raw dispositions and cleanup before attaching an observation. The early and Go
collectors also bind the current collector/test files. Source-model reuse
independently rechecks the unchanged worker, wrapper, runtime, assets, input
bytes and actual runtime mappings; an unrelated checker/test edit does not
require another model execution. An interrupted capture prefix can be supplied
with `--reuse-capture-manifest PATH --reuse-capture-manifest-sha256 SHA`.
That sealed manifest retains every selected raw file. Qualification rechecks
each case before copying completed proof intact; unqualified prior cases remain
named. Missing original collector provenance remains unknown, separately from
the recorded tool pins of the later sealing/continuation phase. The audit
preserves each case's original or reused capture origin.

The `failure_ledger.remaining_ids`, each row's `residual_subcase_ids` and
`missing_evidence` name incomplete proof and its prerequisite. Function-only
tokenizer/model doubles remain separate from actual model processes. Transport
decoding remains separate from Index span/count/vector validation. The retained
idle proof does not invent a missing exit/kill tuple. Each additional function,
process or store observation must retain its own scope and raw evidence.
`failures/s01b` stays partial
while any required subcase is missing; the other S-01 holds and separate S-04/S-05
ownership are unchanged.

## Residual observations and exact report reuse

`audit --reuse-accepted` selects only the four report hashes in
[the accepted baseline](../scripts/testdata/semantic-refusal-baseline.json),
from revision `91a97fdb862e190ea196704b3c446dab06be2b2b` and review
`97fee45b-2a15-4a79-9387-9884d8bad48d/v1`. The baseline metadata has a fixed
digest in the reader. Its 23 actor/source hashes must still match, and each old
reader rechecks raw inputs, outcomes, process identities, traces and cleanup.
An arbitrary old report cannot use the accepted producer bindings. This retains
the original 59 parents / 190 subcases without executing their captures again.

Three additional report selectors attach the residual evidence:

- `--functions PATH --functions-sha256 SHA` checks 24 function observations.
  Five rank guards use actual frozen NumPy with explicit tokenizer/model
  doubles; query retreat uses a tokenizer double and forbids inference.
  Eighteen identity controls call the actual Source constructors on disposable
  source/asset copies with external model/tokenizer constructor stubs. Their
  baseline identities match retained real-worker identities. One asset, source,
  package metadata value or consumed setting changes at a time; path moves and
  seven separate timestamp changes preserve bytes and identities. Unsupported
  FastEmbed actually refuses. None of these identity controls proves real model
  initialization or inference. The child records its executed fixture and actor
  hashes; the literal bootstrap and exact input-catalogue projection are bound
  separately from the retained full checker/test snapshot.
- `--native-residuals PATH --native-residuals-sha256 SHA` checks 14 actual
  `Index.Step`/`vectorText` controls with an in-process model double and one
  fresh private PostgreSQL cluster, using a separate database per case. Invalid
  spans, count, dimensions, nonfinite and zero vectors publish no semantic rows
  and retain the retry job. The positive 256-passage and 384-dimension controls
  publish and produce actual compiler discovery coverage 1/1. A separate idle
  process double records three calls, two child identities, actual old-child
  absence before replacement, termination/join traces, nonempty stdout prefixes
  and restoration of the last successful bounded cache. The native build binds
  Go 1.25, dependency metadata, all compiled local packages and embedded schema
  files, tests and binary bytes. A byte-identical rebuild can add complete build
  source attestation without replaying store or idle observations.
- `--source-residuals PATH --source-residuals-sha256 SHA` checks five short real
  Source controls: missing rank record ID/version, plus embedding identity,
  query and document. Each has its own actual interpreter/ORT/library binding,
  input/output/error, exit and cleanup. Two additional serialization guards run
  the actual `serve_stream` with a labelled Scorer double: exact 64 KiB ordinary
  and combined-cache output boundaries succeed, and the next byte refuses with
  no output. The artificial payloads establish those guards; they do not claim
  that the real Scorer can produce these sizes.

Collect with `function-refusals`, `native-residuals` and `source-residuals`;
each command's `--help` lists its explicit inputs. The native collector accepts
an isolated native build report, including the retained `go list -deps -json`,
test/build commands, source pins and binary hash. The selected build recipe and
all generated reports stay in the external evidence directory. None selects an
installed worker or production database. Capture paths remain provenance when
the checker runs from another checkout with identical actor bytes.

The private-store runtime report is deliberately partial. It records actual Go
and PostgreSQL executable hashes and selected loaded pgvector mappings. One
runtime-observer environment parse error remains retained; PostgreSQL server
environment blocks and the native driver's exec environment are unobserved.
The observed Python launch environment, private cluster settings, SQL outcomes
and clean server stop are separate evidence. The report does not claim a complete
process inventory, a server environment attestation or zero effects.

The combined residual audit verifies 77 of 78 parents and 236 of 238 subcases.
Only `F-EMBED/65536-byte-document/Latin65536` and `/CJK65535` remain open. Retained
packaged results have successful Go replies, but no per-embedding-worker terminal
status, wire stdout prefix or complete restart tuple. The packaged CJK input is
65,536 bytes (`漢` repeated plus `!`), so it also differs from the catalogue's
65,535-byte subcase. Historical direct Python CJK also has 65,536 input bytes and
exit 0 with raw stdout, but lacks actual runtime/process identity and restart
observations. Neither selected CJK input matches the exact 65,535-byte name.
The historical dense ASCII maximum timed out. No maximum-size or
latency experiment is added here. Closing these observation gaps would require
a separately authorized route that records the missing observations. Under the
2026-11-12 reset, they are documented limits rather than S-01 campaign-closure
blockers. Raw/FENV, punctuation and runtime claims are not expanded; production
remains held.

## Retrieval-quality evidence

Owner `98d46758-c61c-42b4-9912-76cf9dfb0dc0/v1` selects Rule C. Its four
clauses include vector error at most `1e-6`, score delta at most 500, unchanged
rank order outside near-ties, and the retrieval-quality fixture passing. The
accepted packaged comparison evaluates the first three locally; it does not
evaluate the fourth. No new near-tie threshold is selected here.

`TestDocumentationRetrievalQuality` is an opt-in lexical, no-model observation.
It records misses and explicitly does not define scores as acceptance criteria.
`TestIndexedPythonRustComparison` already exercises the real `Index.Search`
and compiler for 17 frozen workload queries plus nine boundaries. Its accepted
S-03 result proves discrete compiler/SQL equality and receipt replay; it does
not declare an absolute relevance threshold. The 17 labelled worker observations
and 15 unchanged relevant-rank intervals also remain observations.

The 2026-11-12 owner-directed reset resolves the open-ended fixture-selection
question: use the existing retrieval-quality fixture, reporting relevant-label
ranks separately. Preserve the accepted Python-relative real compiler
comparison as model evidence. A passing lexical measurement is not a claim
that every relevance label was retrieved or that an absolute relevance/no-answer
threshold was met.

## Running the bounded tests

Use `check_semantic_reference.py tests --help` for the required explicit paths.
Supply the retained prepared Python home, owned reference site and its pinned
runtime binding, model assets, a native Go semantic test executable and native
`test2json`. Build the Go test executable inside the unchanged isolation wrapper.
The test subcommand uses that same wrapper for each named suite, with networking
disabled, inputs read-only and a disposable mapped OS home/cache. It verifies
481 retained module hashes, seven numeric native hashes and the interpreter
digest before invoking Python. It never initializes the model or selects an
installed worker. No host fallback is available.

An already-passed prepared phase can be supplied with `--prepared-report` and
`--prepared-report-sha256`. Reuse requires the exact current source bindings and
all 23 actual names in the pinned raw outcome. The new report records the source
receipt and original inventory hash rather than claiming a fresh execution.
Reuse does not compare the earlier invocation's interpreter, site, runtime
binding or asset paths with the new invocation. It retains that original command
as provenance; the current supplied runtime and assets pass their own preflight.

`plan --output PATH` writes the required criteria without running tests or
workers. `audit --evidence-root PATH --output PATH` verifies the selected retained
artifacts and their manifests. Add `--tests PATH --tests-sha256 SHA256` to bind
a selected test report and its actual raw outcomes. Run audits inside the
unchanged wrapper as well. Output paths must be new.

Exit 0 from `tests` means only the named tests passed. Exit 3 from `plan` or
`audit` records incomplete criteria. Exit 2 records a refusal. Held, missing,
unproved and decision-required entries remain in `remaining`; they cannot become
a full catalogue PASS through a skipped phase or a passing unrelated test.
The campaign closure decision above accepts the two named residuals as limits;
it does not rewrite this audit's result. S-04R, runtime and production evidence
remain separately reported.
