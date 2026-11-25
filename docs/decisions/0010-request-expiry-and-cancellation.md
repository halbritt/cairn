# 0010 — Request expiry and cancellation

Status: verified, 2026-09-16. Source: decision receipt `cairn-request-controls-2026-09-16`.
For the current contract, see [request expiry, deadlines and cancellation](../request-controls.md).

## Question

How should request expiry and cancellation fence completion while retaining supervisor holds until process termination is confirmed?

## Decision

Record stop under delivery lock, then supervisor confirms group emptiness before finishing hold.

## Options not taken

- Rejected: Release delivery hold immediately when cancellation is requested.
- Rejected: Kill an interactive CLI or shared gateway as a substitute for per-request native cancellation.
- Rejected: Leave request lifetime unbounded beyond the existing worker timeout.

## Authority

Accepted coordination v1 implementation/deployment goal and trusted-host correction.

## Reopen when

- A native host gains isolated per-request interruption and completion observation.
- New claim paths or delivery writers alter collection/delivery lock ordering.
- Measured control-poll or sweep load exceeds selected operating limits.
