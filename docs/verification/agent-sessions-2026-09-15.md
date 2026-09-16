# Agent session verification — 2026-09-15

## Session foundation

Implemented contract: [agent sessions](../agent-sessions.md). This is progress
within the [coordination plan](../plans/agent-coordination-v1.md), not acceptance
of the full plan or measured cross-agent usefulness.

Build `df6d1c3a3f9d1bb5c353fa3852a982fb84696cf6` was installed on the owner's host.
The CLI and running API reported the same clean VCS revision. Migration 037
applied after an operator checkpoint and backup, whose dump digest matched its
catalog. All seven worker profiles had no unfinished attempts before shutdown.
The API retained its semantic worker configuration. All seven wake supervisors
and the API were active after restart. No synthetic messages entered production.
The hosted directory read succeeded and returned no sessions; native registration
was not yet connected.

Required checks passed: `make test-integration` (disposable databases, race-enabled
Go tests and real CLI/API/systemd probes), `make check`, `make test-lifecycle`
(real backup/restore including session rows), and 101 Python tests. Coverage
includes separate conversations under one account, resume identity, execution
fencing, context revision conflicts, expiry, destination restrictions, restore
invalidation, base/session inbox separation and API restart.

The wake worker also receives a versioned JSON context with exact completion
arguments. Its process probe checked the file mode, identifiers and agreement
with the prompt. Existing crash/outage/cgroup and explicit-completion checks passed.

Cairn skill guidance was updated in skillpack `621cf8d`, pushed and deployed.
Both files matched across all eight existing installations, including Hermes.
The earlier Hermes CLI/gateway restart remains recorded in the wakeup report;
this foundation changes the shared API/CLI, not the Hermes provider integration.

## Recipient resolution

Current implementation adds exact project aliases, structured unique/ambiguous/
no-match/stale results and transactionally checked publication. The focused real
CLI/API probe verified ambiguous matches, a unique alias match, persisted
resolution, original-recipient retry after resume and rejection of new stale sends.
Store tests additionally check project changes, expiry, restore, visibility and
collection mismatch. `make test-integration`, `make check`, `make test-lifecycle` and 101 Python tests
passed for this follow-up. The fresh lifecycle database caught an initial bug:
resolution validation rejected database generation zero. Validation now accepts
the schema's initial zero, and both the lifecycle and complete integration checks
passed afterward. Deployment is recorded separately from the foundation above.
