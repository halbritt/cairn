# 0019 — Subscription model routing, no OpenRouter fallback

Status: accepted by the owner, 2026-10-19 (commit `82eb085b`).

## Decision

A model that one of the owner's subscriptions covers is reached through that
subscription. OpenRouter is not used for it.

## Consequences

- When subscription authentication fails or quota is exhausted, the affected
  operation stops and reports the problem. It does not fall back to OpenRouter.
- An available API key does not authorize the fallback.
- The rule applies to agents and subprocesses that a session launches.

The working rule is in [AGENTS.md](../../AGENTS.md#model-routing).
