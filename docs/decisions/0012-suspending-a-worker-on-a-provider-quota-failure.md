# 0012 — Suspending a worker on a provider quota failure

Status: verified, 2026-09-16. Source: decision receipt `cairn-provider-failure-2026-09-16`.
For the current contract, see [worker provider failures](../provider-failures.md).

## Question

How should observed native provider quota failures atomically suspend the owning worker binding without confusing generic model output with provider errors or blocking restore cleanup?

## Decision

Persist selected observation and update owning health with the wake report.

## Options not taken

- Rejected: Infer account failure from generic stderr or tool text.
- Rejected: Retain manual health changes only.

## Authority

Accepted coordination v1 goal, deployment authorization and trusted-host correction.

## Reopen when

- Native error envelopes change.
- A supported envelope falsely suspends an account.
- Agy supplies a verified observation surface.
