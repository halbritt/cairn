# 0006 — Session identity and routing

Status: decided, 2026-09-15. Source: decision receipt `cairn-agent-coordination-v1-2026-09-15`.
For the current contract, see [agent sessions](../agent-sessions.md).

## Question

How should Cairn associate live sessions with stable UUIDs and route by current metadata within the existing trusted-host boundary?

## Decision

Live UUID identity plus fresh metadata and authenticated routing.

## Options not taken

- Rejected: Harness/account names or static slots alone identify live agents.
- Deferred: Replace PostgreSQL polling with a broker before resolving identity.

## Authority

Owner requested fungible UUID/ordinal identities, metadata-based targeting and a plan for deferred v1 work; existing design decisions were delegated.

## Reopen when

- Native resume/fork semantics contradict the proposed identity continuity.
- Supported session delivery requires a different adapter contract.
- Measured latency or availability requires revisiting the transport.
