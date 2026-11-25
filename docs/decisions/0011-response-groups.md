# 0011 — Response groups

Status: verified, 2026-09-16. Source: decision receipt `cairn-response-groups-2026-09-16`.
For the current contract, see [response groups](../response-groups.md).

## Question

How should a bounded response group snapshot recipients and retain explicit response outcomes without turning acknowledgments or forgotten payloads into task acceptance?

## Decision

Snapshot actual deliveries and collect explicit replies in existing publication transactions.

## Options not taken

- Rejected: Read current topic membership during aggregation.
- Rejected: Treat delivery acknowledgment as a successful response.
- Rejected: Leave response aggregation unimplemented.

## Authority

Accepted coordination v1 implementation/deployment goal and trusted-host correction.

## Reopen when

- A pool or dynamic-recipient workflow needs response aggregation.
- New publication/restore paths change transaction lock ordering.
- Measured collection or observation backlog exceeds the bounded operating contract.
