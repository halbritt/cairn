# 0014 — Classifying an Agy HTTP 429 result

Status: verified, 2026-09-16. Source: decision receipt `cairn-agy-provider-failures-2026-09-16`.
For the current contract, see [worker provider failures](../provider-failures.md).

## Question

May the latest native Agy HTTP429 result suspend its worker slot when print mode exits with partial output?

## Decision

Latest-step-gated native HTTP429 result classification.

## Options not taken

- Rejected: Result ERROR429 alone.
- Rejected: Leave all Agy failures manual.
- Rejected: Arbitrary quota substrings or process exit alone.

## Authority

Accepted coordination v1 implementation/deployment goal.

## Reopen when

- Native step order or error formatting changes.
- A backend emits a different provider-error envelope.
- A recovered run is incorrectly classified.
