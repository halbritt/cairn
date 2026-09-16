# Agent event fabric implementation plan

Owner authorization: 2026-09-15, finish the specification, choose the design,
implement it and iterate until delivered. Contract: [event fabric v1](../agent-event-fabric.md).

1. Add operational PostgreSQL event, subscription/history and delivery tables.
   Implement commit-ordered publication/fanout, exact references and idempotent
   mutation responses using the existing transaction machinery.
2. Implement authenticated polling, expiring leases, retry/renew and atomic
   completion with an optional ordinary result. Add bounded history, status and
   metrics. Preserve current source/destination/forgetting rules.
3. Expose the operations through the local API and agent CLI, with top-level
   convenience commands, offline help, structured errors and practical examples.
4. Exercise failures and concurrency in disposable PostgreSQL tests. Add a real
   CLI integration probe with two identities and API restart. Run the full
   integration, static and lifecycle gates; fix failures before proceeding.
5. Review the final code for swallowed errors, missing transaction boundaries,
   stale lease races and untested claims. Update README and implementation status
   with actual evidence. Back up the dedicated operational store, install the
   binary and additive migration, restart the API, and verify the deployed build
   through read-only authenticated calls. Keep synthetic traffic in disposable
   tests. Existing profiles can use their principal inbox immediately.

The first engine is PostgreSQL polling. All original required messaging behavior
is retained: direct/topic routing, durable offline delivery, at-least-once
delivery, retry-safe logical effects within the stated boundary, provenance,
transport-independent agent APIs, and recovery. NATS is deferred because the
durable delivery contract already belongs to PostgreSQL. No agent is launched
or sent a real work request as part of verification.

## Progress

- Specification, schema, store, API, CLI and named profile provisioning implemented.
- Full integration, static checks, lifecycle backup/restore and 101 Python tests passed.
- Operational backup completed; installation and final profile verification in progress.
