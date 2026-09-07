# Roadmap build verification — 2026-09-07

This work advances the observation, replay, demand and restoration dependencies
in the accepted roadmap. It does not establish full Stage 1–2 acceptance or
measured agent benefit.

| Implemented behavior | Evidence |
| --- | --- |
| Per-record/version/run join, coverage-aware usage and corrected task assessments | `core/use_report_test.go`; repeated observations do not multiply the unit; binding failures cannot become capability failure |
| Closed-task delegates with missing terminal observations | `core/task_test.go`; late terminal recovery clears the current finding |
| Authenticated local API with server-owned writer, role, repository and destination | `localapi/server_test.go`, `scripts/check-local-api.py`; real Unix socket, client, listener collision and graceful shutdown |
| Declared revision/workspace/task/binding/capability/validity gates | `core/currentness_test.go`; ordinary edits cannot remove pins, disjoint instructions do not create automatic conflicts |
| Historical recompilation under retained gate facts and versions | `core/recompile_test.go`; later corrections/evidence state do not change historical selection; altered bytes fail |
| Native-history coverage without a usefulness claim | [Coverage artifact](striatum-history-coverage-2026-09-07.json); 3 accepted Striatum clauses, 12 native subjects, 12 seal matches; later-recurrence scenario |
| Evidence-attached failure/recovery proposals and review decisions | `core/proposals_test.go`; duplicate grouping, source corrections, stale review, deferral and repo boundary |
| Expected audit restore set | `core/checkpoint_test.go`; altered/missing members, later uncovered events, A exclusion, catalog mismatch and operator access |
| Sealed index, expiring body handles and transactional credits | `core/index_test.go`; current version/authority/bootstrap checks on retry, concurrent credit limit, oversized body refusal and preview invalidation |
| Restore recomputation and handle fencing | `scripts/test-local-lifecycle.sh`; independent dump, audit catalog verification, exact body/index recompile and rejection of restored handles |

`make check`, `make test-integration` and `make test-lifecycle` pass against
disposable PostgreSQL 17. Integration tests use Go's race detector. Red/green
probes also reproduced common-word lexical noise and automatic conflicts between
instructions with disjoint revision/validity pins before their fixes.

Preserved boundaries: no raw query/task retention by default; no payload principal
as authentication; no automatic promotion or learned ranking; no model benefit
inferred from process exit, exposure, citation, pairing or coverage; no changes to
Striatum's sealed dispatch input contract. Applied migrations are unchanged.

Remaining material work is tracked in the roadmap: real host/model acceptance,
causal recurrence evaluation, transitive dependency impact, class D deletion and
retention, governed policy and richer conflicts, evidence lifecycle, and recovery
of newer revocations/deletion effects. The endpoint credits are per retrieval,
not a global host task budget. Checkpoints are unsigned metadata integrity checks,
not proof that newer commits never existed.
