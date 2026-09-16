# Provider failure verification — 2026-09-16

## Implemented slice

Migration 042 adds selected provider observations to wake attempts. The owning
slot becomes unavailable in the same transaction. A local observation survives
a failed API report and is available during confirmed supervisor cleanup.
Supported native envelopes and gaps are documented in
[provider failures](../provider-failures.md). This does not complete coordination
v1 or establish provider capacity.

## Checks

`make test-integration`, `make check`, the Python suite (109 tests), and
`make test-lifecycle` passed. Integration uses disposable PostgreSQL and the
repository's race-enabled Go test command. Lifecycle verification populated
provider failure fields through the real API/systemd pool probe, backed up the
database, restored it, and compared the complete wake/worker/event rows.

Store regressions cover independent slot health, injected transaction failure,
exact retry, explicit recovery before finish, mismatched harness refusal,
conflicting observations, and cleanup with a retained failure after restore.
Parser regressions cover exact diagnostic matching, tool-output isolation,
successful retry, later HTTP 500, oversized lines, chunk boundaries, trailing
lines and failed persistence callbacks. Hermes hook tests exclude child sessions
and raw request/diagnostic fields.

The installed Hermes binary ran against a loopback HTTP 429 provider with an
isolated home, real coordination plugin, disposable API and systemd worker. It
registered its native session, retained a `native-hook` rate-limit observation,
finished the failed delivery and marked its slot unavailable. The same probe
verified synthetic Codex terminal JSON through the supervisor, then restarted
the supervisor and observed that later work remained queued.

Parent independently ran the installed OpenCode binary against a loopback HTTP
402 provider. Stdout contained a terminal `error` with `APIError.data.statusCode`
402 and no later completion event. The isolated output is retained at
`/tmp/cairn-opencode-quota-x59kvzpy/stdout.jsonl`.

Local logs: `/tmp/cairn-provider-integration.log`,
`/tmp/cairn-provider-check.log`, `/tmp/cairn-provider-python2.log`,
`/tmp/cairn-provider-lifecycle.log`, and `/tmp/cairn-provider-runtime-01.log`.
The retained native fixture is `/tmp/cairn-provider-runtime-01`.
These probes used no production provider account and no production test data.

One existing Python process-cleanup test exposed a check/read race when a killed
child was reaped between `/proc` reads. It now accepts a missing process as
confirmed cleanup while continuing to reject a live child.

## Review and limits

The owner-supplied agent investigated native Codex/Claude envelopes, implemented
the bounded stdout parser and its tests, and received a separate integration
review request, whose response was read and acknowledged. Parent review restricted Claude classification to the observed
status/code pair, classified HTTP 402 as billing, and removed an unsupported
OpenCode session-completion heuristic. No generic stderr matching is used.
The reviewer suggested bypassing a stale supervisor during finish. That change
was rejected: registration already refuses replacement while a wake remains
unfinished, and the restore regression proves cleanup preserves the retained
incarnation. Hermes callbacks already require a registered nonempty owner
session and clear only that session's candidate.

Codex has two supported exact diagnostic strings. Other subscription-limit
wording remains unclassified. Agy automatic quota detection remains unverified.
No failure observation proves final task acceptance. Broader wake operations,
scheduling, cancellation and response groups remain in the v1 plan.

The [decision receipt](../plans/provider-failure-review.json) records the bounded
atomicity/failure-policy review, evidence and remaining generic obligations.

## Deployment

Clean release `026a6052ed0305b3f7669b9e6628c6e904ecc454` is pushed and deployed.
Its repeat runtime probe passed native OpenCode completion, synthetic Codex quota
retention/restart and installed Hermes loopback 429 ingestion. Log:
`/tmp/cairn-provider-release-runtime.log`.

Production had zero active wake/native inbox attempts before upgrade. Backup
`cairn-20260916T070513-4101561.dump` passed catalog, checksum and archive checks.
The prior binary is retained as `cairn-before-provider-failures`; prior engine
and Hermes plugin copies are in the coordination directory.

Migration 042, the shared coordination engine and Hermes plugin were installed.
The API answered at the new revision before the presence watcher, seven worker
supervisors and Hermes gateway were started. All ten services are active/running
with zero automatic restarts. CLI and API report clean revision `026a605`;
installed engine/plugin bytes match the release source. The API's existing
semantic-worker settings were preserved.

All seven slots registered live and retained their admission health. This does
not establish successful live provider calls. Skillpack `3e564f0` is pushed and
installed; its check reports all 49 skills resolve across existing harnesses.
The three selected reviewer responses were read and acknowledged. No review
request remains pending for this slice.
