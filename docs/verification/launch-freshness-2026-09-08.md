# Retained context eligibility at launch, 2026-09-08

A retained package could authorize launch after a selected note was edited or a
new mandatory instruction was issued. Disposable PostgreSQL reproductions showed
all four cases succeeding incorrectly: edit and new mandatory context, each in
body and index mode.

Commit `99b2d93b614ab3232c5b8c52693d1a17ada19068` adds the
[launch freshness check](../launch-freshness.md). It reuses compiler eligibility
and compares the retained selection and mandatory context without reranking or
rewriting the package. Historical replay and newly optional additions remain
permitted. A fresh compilation can launch once the relevant conditions allow it.

The first implementation used RepeatableRead. Existing race tests exposed two
regressions: duplicate host execution reservations and simultaneous execution
and retrieval roles. The final change uses Serializable for claim, binding,
outcome and retrieval-association transactions, with bounded retries only for
known database aborts. The original race expectations remain intact. Two new
test fixtures were corrected to obey independent promotion and audited retraction
rules; those rules were not weakened.

The final `make test-integration` passed against disposable databases, including
Go race tests and the authenticated CLI/MCP checks. `make check` and all 17 Python
unit tests passed. New core tests cover edited/retracted/disputed selections,
revoked authority, divergent consequential evidence, expired applicability,
new mandatory instructions and unsupported required runtime enforcement. Both
body and index packages are covered. Runner tests change a selected note after
binding and establish that neither stdin nor argv delivery starts the child.
[Code CI 34289644337](https://github.com/halbritt/cairn/actions/runs/34289644337)
also passed.

The clean-commit build is installed as both CLI/MCP executable and running API;
their executable SHA-256 values match. The API and store are active, with schema
and profiles unchanged. Authenticated hosted-agent retrieval succeeded after
the API restart. The selected concurrency lesson was saved as ordinary shareable
A memory and retrieved exactly by a fresh CLI run, ranked first of two results.
Its body, operational IDs and raw responses remain outside the repository.

This proves the repaired pre-launch refusal behavior and installed code parity.
It does not prove continuous enforcement after launch, physical workspace
verification, native Striatum delivery or better model-task outcomes. The native
bridge still needs staged consumption of its admitted receipt and actual host
correspondence under accepted contracts.

[Metadata](launch-freshness-2026-09-08.json) records build and check identities.
Pincite packet `pkt-f06355964cd40a17` informed repository precedence, evidence
before intervention, authority boundaries and behavior preservation. Its 31 unmatched
obligations concern unchanged schemas, ingestion, UI, ranking, configuration and
monitoring, or broader incident/runtime evidence not claimed by this repair.
