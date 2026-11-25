# 0005 — Hermes capture latency and workstream context

Status: verified, 2026-09-13. Source: decision receipt `cairn-hermes-improvements-2026-09-13`.
For the current contract, see [Hermes shared memory](../hermes-lifecycle.md).

## Question

How should the shared Cairn lifecycle engine and Hermes adapter reduce capture latency and add explicit workstream context, inspectable memory status, bounded semantic fallback and current concise checkpoints while preserving selected-write correctness?

## Decision

Five bounded changes using current native contracts and selector.

## Options not taken

- Rejected: Switch selector to Haiku or low effort.
- Rejected: Leave all five requested behaviors unchanged.
- Rejected: Persistent selector, background raw-dialogue queue or broad memory rewrite.

## Authority

Owner goal: take tasks 1 through 5 in order, complete them and deploy the changes.

## Reopen when

- Hermes changes command/provider/session/history contracts.
- Repeated semantic misses or relevance errors warrant revised checks.
- Representative end-to-end measurements demonstrate a different model or resource design is needed.
