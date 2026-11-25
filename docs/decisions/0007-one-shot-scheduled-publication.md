# 0007 — One-shot scheduled publication

Status: verified, 2026-09-16. Source: decision receipt `cairn-event-scheduling-2026-09-16`.
For the current contract, see [one-shot event scheduling](../event-scheduling.md).

## Question

How should one-shot scheduled publication atomically materialize one occurrence and retain a terminal skipped or fired record across retries and scheduler restarts?

## Decision

Commit scheduled event, fanout and occurrence outcome in one PostgreSQL transaction.

## Options not taken

- Rejected: Publish in one transaction then mark the schedule fired in another.
- Rejected: Keep shell timers without retained occurrence identity.

## Authority

Accepted coordination v1 implementation/deployment goal and trusted-host correction.

## Reopen when

- Another schedule/event creator changes the lock order.
- Recurring calendars or hosted scheduling need a concrete caller contract.
- Measured queue contention exceeds bounded rotation requirements.
