# 0016 — Waking an existing idle session

Status: verified, 2026-09-16. Source: decision receipt `cairn-idle-session-wakeups-2026-09-16`.
For the current contract, see [automated agent wakeups](../agent-wakeups.md).

## Question

How should the native session watcher wake an existing idle conversation on pending inbox work while preserving session identity, single consumer ownership and uncertain prompt outcomes?

## Decision

Existing watcher with read-only readiness and retained host intent.

## Options not taken

- Rejected: Retain manual-only idle prompting.
- Rejected: Automatically retry uncertain prompts on a cooldown.
- Rejected: Hold session lock through terminal prompting.

## Authority

Owner instruction: continue on v1 until complete, including the identified idle wakeup gap.

## Reopen when

- Herdr changes its prompt/session reporting contract.
- An observed submission reaches the wrong native session.
- Watcher retry or native ownership regression.
- Need an atomic idle/composer reservation guarantee.
