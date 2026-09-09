# Authenticated assessment history

An authenticated agent could write an assessment but could not read its history
later. The agent CLI rejected `assessments` as unknown, and disposable API checks
for both agent and observer profiles returned `NOT_FOUND: unknown endpoint`.
Aggregate reports omit the reasons and evidence IDs, so they could not supply
the narrative needed for the newly documented qualitative review workflow.

`POST /v1/assessments` and `cairn agent assessments` now accept one JSON object
containing `receipt_id`. They return the existing assessment versions in ascending
order, including reasons, evidence IDs, method, observer and witness. An owned
receipt with no assessments returns an empty array. The request does not append
an observation or consume expansion credits.

## Access and preservation

The new `Store.AssessmentHistory` checks the authenticated destination and shares
the existing history query with `Store.Assessments`. The original direct-store
method keeps its behavior. Receipt owner and repository access, destination
matching and history reads occur in one core transaction. A hosted profile cannot
read a local receipt owned by the same principal, including after reconfiguration.
The client cannot supply a destination override. Agent and observer profiles retain
their original testimony/instrumented witness; neither gains another caller's
history or protected aggregate reports.

Only assessment data is returned. Referenced evidence bodies and receipt package
bodies are not included. Missing and malformed receipt IDs remain explicit errors.
The existing 1,000-version history bound, client response-size limit and restore
fence remain in place; pagination is not added. No schema, assessment mutation,
historical task outcome, native model tool or score changed.

## Verification

- A disposable PostgreSQL test failed before implementation at the first owned
  history read for both agent and observer profiles.
- The completed API check reads an empty history, writes two narrative judgments
  with unknown task acceptance, and compares the entire returned history with
  the original write responses. It verifies ordering, repeat reads, exact reasons,
  evidence references and role-dependent witness without returning evidence bodies.
- Other caller, other repository and same-owner local-destination receipts are
  refused. Missing/invalid IDs and a caller-supplied destination are rejected.
  Protected run reports remain unavailable to the hosted profiles.
- The existing observed-child CLI fixture reads its host assessment exactly
  through the authenticated socket. The linked retrieval agent cannot read that
  host's assessment history. This uses the actual CLI/API and a disposable store.
- The full disposable PostgreSQL integration suite with Go race detection, all
  Go tests, 30 Python tests, vet and build passed. No model cohort was rerun.

This makes already-retained judgments accessible to their authenticated owner
without database access. It does not establish improved review decisions or
longitudinal memory value. The [manifest](assessment-history-2026-09-09.json)
records source, checks and decision provenance; local deployment is recorded below
when completed.
