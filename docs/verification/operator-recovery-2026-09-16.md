# Operator recovery verification — 2026-09-16

## Implemented slice

Migration 044 adds a single-successor trace from a failed delivery to a new
request. `cairn event-reissue` uses the existing local operator channel, requires
explicit acknowledgment of uncertain effects, and retains original attribution
and history. `cairn coordination-review` combines bounded delivery, queued-pool,
session and worker observations. The [contract](../event-recovery.md) describes
paging and the distinction between execution, handling and reported acceptance.

Finishing a prepared wake now preserves `prelaunch_failed` before replacing its
state with `finished`. Older finished attempts without that report stay uncertain.

## Checks

`make test-integration`, `make check` and `make test-lifecycle` passed on disposable
PostgreSQL. The backup/restore comparison includes populated reissue trace rows.
Final focused tests and a real CLI/API probe cover the added queued-pool view and
combined operator command after the broader suite's initial Go-package pass.

Store checks cover source-version pinning after a source edit, unavailable source,
causal privacy, direct and topic targeting, pool requirements, pending/leased/
handled refusal, unfinished wake/native holds, ordinary-profile refusal,
transaction rollback, exact retry, changed-intent conflict, and racing operators
creating one successor. The failed original remains intact.

Review tests cover a handled delivery that still has a hold; prelaunch history
and older uncertain attempts; native failure; an explicit accepted assessment
later corrected to rejected; paging and exact delivery selection; bidirectional
reissue traces; and visibility of a queued pool successor before assignment.
The final runtime probe checks explicit acknowledgment, operator attribution,
no topic refanout, retained failure, exact retry and independent claiming of the
fresh delivery. No production work was reissued during verification.

Local logs: `/tmp/cairn-recovery-integration.log`,
`/tmp/cairn-recovery-check-final.log`, `/tmp/cairn-recovery-lifecycle.log`,
`/tmp/cairn-recovery-focused.log`, and `/tmp/cairn-recovery-runtime-final.log`.
The final runtime fixture is `/tmp/cairn-recovery-runtime-final`.

## Review and limits

The owner-supplied agent implemented the core reissue operation and tests. Parent
review corrected topic metadata to an explicit single recipient and added source
revision, concurrency and privacy regressions. The agent separately reviewed the
parent's combined review view, prelaunch reporting, assessment corrections and
trace links; it reported no concrete correctness defect. The projection includes selected metadata and reasons; it omits source bodies
and lease tokens.

The [decision receipt](../plans/operator-recovery-review.json) records the atomicity
choice, evidence and scope. Review pages are current observations and do not hold
a database snapshot across calls. A retained assessment is testimony, and prior
external effects can remain uncertain. Scheduling, deadlines, cancellation and
bounded response groups remain open in the v1 plan.

## Deployment

Clean release `7471688ae7b4a0aae9169b8009953a009616948e` is pushed and deployed
through migration 044. Before upgrade there were zero unfinished wake and native
inbox attempts. Backup `cairn-20260916T080216-95926.dump` passed catalog, checksum
and archive checks; `cairn-before-operator-recovery` retains the previous binary.

The API, presence process, seven workers and Hermes gateway were upgraded
coherently. The clean API version responded before workers were started. All ten
services reported active/running with zero restarts; all seven slots were online
with available admission health. The native coordination plugin was unchanged.

A production read-only `coordination-review` returned a bounded delivery page,
three retained sessions, seven workers and no queued pool requests. The new trace
table remained empty: deployment did not reissue production work. Shared Cairn
skill instructions were updated with the explicit recovery contract.
