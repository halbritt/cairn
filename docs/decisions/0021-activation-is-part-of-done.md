# 0021 — Activation is part of done

Status: accepted by the owner, 2026-11-24 (commit `70e06493`).

## Decision

Work the owner requested is finished when it is in use. A requested feature
that is merged but switched off is unfinished.

## Consequences

- Owner-requested behavior ships enabled on every harness and live binding
  that supports it. An opt-in flag exists only when the owner asks for one.
- Activation does not wait for trials or usefulness measurements that the
  owner did not ask for. The report states what was not measured.
- Host steps outside the repository (bindings, hooks, manifests, MCP flags)
  are part of the task.
- The report names what was turned on, where, and how to turn it off.
- A change that supersedes something names and removes it in the same change.

This does not widen authority. The limits on unimplemented authority,
consequential reads, destination delivery, production data and model routing
still hold. The working rule is in
[AGENTS.md](../../AGENTS.md#activation-is-part-of-done).
