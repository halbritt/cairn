# 0015 — Claude terminal status and earlier rate-limit observations

Status: verified, 2026-09-16. Source: decision receipt `cairn-claude-terminal-status-2026-09-16`.
For the current contract, see [worker provider failures](../provider-failures.md).

## Question

Should a terminal Claude API error preserve an earlier rate-limit observation when its final HTTP status differs or is missing?

## Decision

Require explicit terminal429 before retaining candidate.

## Options not taken

- Rejected: Preserve earlier429 for all terminal API errors.
- Rejected: Classify free-text errors or change all harness parsers.

## Authority

Accepted coordination v1 implementation/deployment goal.

## Reopen when

- Native Claude event schema changes.
- Another current terminal error can preserve a stale candidate.
- Any independent slot-health, cleanup or recovery regression.
