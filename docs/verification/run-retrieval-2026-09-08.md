# Dynamic retrieval to host outcome correspondence

The actual [OpenCode tool experiment](tool-retrieval-2026-09-08.md) used an
ordinary agent identity to retrieve sources. Those receipts were independent of
host execution outcomes. Equality of declared task/run strings did not establish
an authenticated association. This change adds an explicit host observation so
future evaluations can inspect which retrieved record versions accompanied a run
without counting every retrieval as another execution.

## Implemented behavior

`LinkRunRetrieval` associates one source receipt with one host-owned run whose
binding names the host's independently observed attempt. It verifies the source
reader, exact scope, destination and creation interval. Ordinary agents and
foreign observers cannot make the association. Original retrieval data and use
observations remain on the source receipt; host outcome and latest assessment
are joined only in the reports. No content is copied into the association.

One new table in migration 029 stores the receipt IDs, authenticated reader and
observer, method and observation time. Execution binding, claim and outcome
paths serialize against association writes to prevent a source receipt from
also becoming an execution. Existing run-report population rules stay intact.
No automatic scope join, new permission grant, model call, ranking rule or
recovery subsystem is introduced. Association correction/unlinking is open.

## Verification

Public-store tests in `core/run_retrieval_test.go` cover:

- Preserved record/version/source receipt and expansion, host process outcome,
  unknown task outcome after exit zero, and subsequent assessment corrections.
- Source-local assessment testimony cannot override the joined host assessment.
- Ordinary/foreign observer refusal, expected reader, exact scope, destination,
  missing attempt, binding-only run, retrieval interval and retry intent.
- Delayed observation after completion, including external outcomes without a
  Cairn launch claim; live repository access before cached retry.
- Linked receipts cannot become executions. Concurrent association versus
  binding/claim/outcome has one winner; competing host runs cannot share a source.

`scripts/check_run_retrieval.py`, invoked by `make test-integration`, uses the
real CLI and Unix API with distinct ordinary-agent and observer identities.
The client has an unusable database address; only the API owns database access.
An observed Python child receives an empty bootstrap, issues an empty index
query, refines it, pulls the exact note and records two citations. The host then
links both receipts after actual process completion. Reports retain one source
exposure for the selected note, citation testimony, two retrievals and one
execution. Exit zero remains unknown until a separate evidence-backed fixture
assessment. The ordinary token is refused the same linking operation.

This is a deterministic synthetic integration fixture. It does not measure
model judgment, benefit over direct context, transfer, or hostile-process
confinement. The earlier model experiment is not retroactively assigned host
observations. Its failures and unknowns remain as originally reported.

Validation logs for this development checkout are retained locally under
`/tmp/cairn-run-retrieval-{boundaries,integration,check}.log`. The tests use only
owned disposable PostgreSQL clusters. No operational memory is imported by them.
