# 0009 — Operator recovery of uncertain execution

Status: verified, 2026-09-16. Source: decision receipt `cairn-operator-recovery-2026-09-16`.
For the current contract, see [operator review and explicit reissue](../event-recovery.md).

## Question

How should an operator inspect uncertain execution and explicitly create one traceable successor request without replaying active work or rewriting prior attribution?

## Decision

New request and trace under existing operator authority.

## Options not taken

- Rejected: Reset the failed delivery or automatically retry uncertain work.
- Rejected: Create another delivery under the old event.
- Rejected: Keep failure inspection and manual publication separate only.

## Authority

Accepted v1 implementation/deployment goal and trusted-host correction.

## Reopen when

- A new attempt creator bypasses delivery locking.
- Acceptance semantics change or need independent attestation.
- Evidence of review-query or collection-lock contention.
