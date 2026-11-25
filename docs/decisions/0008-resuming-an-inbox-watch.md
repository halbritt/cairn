# 0008 — Resuming an inbox watch

Status: verified, 2026-09-16. Source: decision receipt `cairn-inbox-watch-2026-09-16`.
For the current contract, see [watching inbox arrivals](../inbox-watch.md).

## Question

How should read-only inbox watch resume across late pool assignment, API restart and restore?

## Decision

Delivery sequence with serialized insertion and bounded repeated reads.

## Options not taken

- Rejected: Reuse publication positions.
- Rejected: Retain manual publication-history polling only.
- Deferred: Add long-poll notification infrastructure.

## Authority

Accepted v1 implementation/deployment goal and trusted-host correction.

## Reopen when

- New delivery creation paths.
- Observed collection-lock contention.
- Changed downstream processing guarantee or delivery retention policy.
