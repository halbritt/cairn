# Response-group verification — 2026-09-16

## Implemented contract

Migration 047 adds fixed response groups for direct and topic requests. Publication
atomically snapshots 1–100 deliveries. The first matching explicit response from
each recipient counts; delivery acknowledgment alone does not. Collection uses
an absolute deadline and explicit `all` or `partial` policy. Duplicate, late and
unmatched replies remain observations. Status keeps historical collection
separate from current payload availability and always reports task outcome as
unknown. See [the contract](../response-groups.md).

The existing scheduler closes overdue groups in bounded batches. Restore fences
open groups as incomplete. Fresh-worker context supplies stable slot-owned reply
arguments; native inbox context already supplies session-owned reply arguments.
No new session credentials or harness identity rules are introduced.

## Verification

All database tests used disposable databases and generated fixtures. Production
memory and production backups were not restored for testing.

| Check | Observed result |
| --- | --- |
| `make test-integration` | Passed, including Go race tests and real CLI/API/systemd probes. |
| `make check` | Passed after the independent review tests were added. |
| `make test-lifecycle` | Passed; full populated group, member and observation rows survive backup/restore. |
| Focused `TestResponseGroup*` under `-race` | Passed: explicit reply versus acknowledgment, snapshot stability, duplicate/late replies, all/partial deadlines, forgotten payloads, restore, and admission rollback. |
| Independent `TestReview*` under `-race` | Parent rerun passed after full integration: concurrent same/distinct-recipient replies, reply/sweep ordering, injected SQL failure, publication retry after subscription changes, owner/destination isolation and publication/restore ordering. |
| CLI/API probe | Passed through API restart between recipients' replies; exact retries, observation paging and owner-only reads remain correct. |
| Systemd worker probe | Passed with a synthetic worker following the actual context's completion and response commands; collection does not imply task acceptance. |

The independent agent added only `core/agent_response_group_review_test.go` for
the store review. Its result was read and acknowledged. These tests were added
after the full integration run's core package had compiled, so their separate
successful race run is the evidence for those additions. Production code did
not change after that integration run.

Initial fixture failures were corrected before the successful runs: the
101-recipient fixture exhausted test connections by retaining every store pool;
the first CLI probe shared a database with tests that fence the whole store.
Stores now close promptly, and store tests and runtime probes use separate
disposable databases. The review fixture also corrected an invalid destination
name. Those initial runs are not counted as successful verification.

## Decision and limits

Selected doctrine is transaction-wide atomicity and explicit failure policy.
The [decision receipt](../plans/response-groups-review.json) records evidence,
alternatives and remaining nonmaterial obligations. The final packet is
`pkt-b770dd7732e29639`, content SHA-256
`b770dd7732e29639b2fe24a1969724cc0fccf382ae804f135fd42929a0f7f2b7`, from
validated Pincite release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`.

These checks establish the bounded response-collection contract. They do not
establish provider task success, live Codex/Rhumb routing, every possible race,
throughput targets or full v1 acceptance. Pools lack a publication-time recipient
snapshot and reject response groups. A collection deadline does not stop work;
managed task deadlines use the separate request-control contract.

At verification time the installed runtime remains migration 046. Deployment
must retain existing profile scope, inspect unfinished holds, back up the store,
stop consumers and API, migrate, then verify the clean installed revision before
restarting consumers and the Hermes gateway.
