# 0013 — Recognizing native subscription-limit errors

Status: verified, 2026-09-16. Source: decision receipt `cairn-codex-subscription-limits-2026-09-16`.
For the current contract, see [worker provider failures](../provider-failures.md).

## Question

Which native terminal provider errors may suspend a worker account without treating tool text or recovered retries as quota failures?

## Decision

Recognize observed terminal templates in the existing bounded stream parser.

## Options not taken

- Rejected: Leave subscription-limit failures unclassified.
- Rejected: Match arbitrary stderr or tool text for quota keywords.

## Authority

Accepted coordination v1 implementation/deployment goal.

## Reopen when

- Native diagnostic templates or event envelopes change.
- A verified Agy quota discriminator becomes available.
