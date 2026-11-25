# 0004 — Hermes extension path

Status: recommended, 2026-09-13. Source: decision receipt `cairn-hermes-plan-2026-09-13`.
For the current contract, see [Hermes shared memory](../hermes-lifecycle.md).

## Question

Which Hermes extension path should be probed first for Cairn recall and selected capture while preserving native memory and reusing the existing lifecycle engine?

## Decision

Provider plugin plus pre-turn hook, verified first.

## Options not taken

- Deferred: MCP and skill alone.
- Deferred: Hooks-only plugin.

## Authority

Owner requested a Hermes integration plan, then explicitly selected CLI and messaging gateways for the first integration.

## Reopen when

- Native callback or provider registration differs from source.
- Retained API context cannot be bounded using supported interfaces.
- An existing external provider must remain active, or a configured gateway lacks a required lifecycle callback.
