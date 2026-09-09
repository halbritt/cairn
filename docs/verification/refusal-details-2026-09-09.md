# Partial diagnostics for refused retrievals

A refused compilation previously retained considered record/version IDs but
lost the candidate reasons and allocation features already computed in memory.
The change preserves those features in the existing refusal JSONB observation.
It supports local inspection of a budget refusal without repeating the query
against potentially changed notes. It does not establish downstream task benefit.

## Behavior and preservation

New compiler refusals use explanation version 1. Budget and ranking context are
retained when initialized. Candidate observations exclude bodies, raw queries and
frozen evidence/grant facts. Missing reasons become `EVALUATION_INCOMPLETE`.
Considered references and candidates use the same sorted prefix of at most 1,000;
the observed count remains separate. Selection may have stopped early, so every
refusal remains explicitly partial. An aborted `SELECTED` entry is not delivery.

Successful compilation and package seals, ranking, visibility gates, caller/repo
ownership and request grouping are unchanged. Hosted profiles receive only the
opaque refusal ID and cannot inspect protected traces. Old and non-compiler
observations decode as explanation version 0 without fabricated detail. No SQL
migration or new service is needed. Refusal observations have no automatic expiry
or purge command; the field cap does not bound lifetime database growth.

## Verification

`make test-integration` passed against disposable PostgreSQL with the race detector
and actual CLI/Unix API checks. The new core fixture forces a mandatory budget
refusal, verifies optional omission reasons and allocation context, excludes a
hidden local note and raw query/body canaries, checks retry identity and caller
ownership, and reads a simulated legacy observation. A separate retainer test
passes 1,001 synthetic candidate observations and verifies the retained 1,000-entry
prefix; it is not a full 1,001-note compiler workload.

The Unix API fixture inspects a real CLI-created budget refusal through a local
synthetic profile. It checks version, allocation context, reason and absence of
body/facts. Existing hosted filtering and MCP checks also passed. `make test`
passed all Go packages and 26 Python tests; `make check` passed vet and formatting.
No native harness or model inference was needed for this core/API change.
Source and log hashes are retained in the adjacent JSON report.

## Remaining requirements

R5 remains partial: early failure can precede complete candidate evaluation, and
this change cannot reconstruct a complete historical decision trace. R3 also
remains partial: impact traversal follows explicit versioned record relations;
`EvidenceRequest.Source` is an opaque label, not a typed source-record dependency.
Inferring that graph from strings would not satisfy the requirement.

The selected alternative retains computed diagnostic fields at the existing
failure boundary. Leaving the code unchanged loses useful failure context;
reconstructing traces later uses changed state; retaining full source snapshots
would add unnecessary sensitive content. No performance or memory-usage gain is
claimed. Deployment evidence will be recorded after installing a clean build.
