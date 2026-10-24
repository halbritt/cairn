# Codex weekly multi-account assignment: source verification

Status: implemented and synthetically verified, 2026-10-24. This extends the
CAIRN-107 Codex Luna weekly route to pin an explicit account for every frozen
case/seed pair. It does not admit or run a campaign.

## Assignment and capacity contract

`scripts/trial_weekly_accounts.py` validates the complete assignment document
against the frozen 21-case, two-arm, three-seed plan. It contains one row for
each of the 126 `(case, arm, seed)` identities. The default
`balanced-case-seed-rotation/1` map assigns pair `(case_index, seed_index)` to
`accounts[(case_index + seed_index) % 3]`, then emits both arm rows. Each pair
stays on one account. The default produces 21 paired identities / 42 actor
attempts per account, and every case is observed on all three accounts. An
alternate explicit assignment is accepted only when it covers all 126
identities exactly once, keeps each pair together, uses only pinned accounts,
and uses each account. The assignment
file SHA-256 is part of the capacity plan digest, live selection, run claim,
attempt plan and retry identity.

The capacity validator accepts provider workload `/3` only with that plan pin.
CURRENT, CAND and both grader roles are split into account-specific jobs whose
counts must match assigned pairs. Route bindings must preserve each job's
account. Absolute allowance resources and a held reservation are checked for
every account; shared pool/window resources continue to be summed. Percentage
usage is never converted to an allowance. The existing workload `/2` contract
remains unchanged.

## Account and model preflight

Before a Codex weekly claim, the runner verifies every pinned auth-file digest,
`auth_mode=chatgpt`, and the provider account ID stored in that file. Once the
capacity receipt and live selection pass, it runs the pinned Codex CLI's
read-only `account/rateLimits/read` and `model/list` under each account's
`CODEX_HOME`. API-key and OpenRouter environment variables are removed. An
unavailable status, `ordinaryUsageAllowed=false`, or absent `gpt-6-luna` model
catalog entry refuses the claim. Model catalog presence is not proof of
per-account entitlement; that requires a real inference request. The current
rate-limit response exposes percentage use but no absolute allowance, so it
cannot satisfy the capacity receipt. The [Codex app-server protocol](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
documents these read operations.

The harness checks the selected auth-file identity and digest immediately
before every assigned attempt, mounts only that account's auth file read-only,
and records the account ID and auth digest on the attempt. The Codex command
continues to explicitly select `model_provider="openai"`, `gpt-6-luna`, and
sequential execution; there is no API-key or OpenRouter fallback. A complete
agent result is rejected if its plan or attempt records differ from the pinned
assignment. A retry of the same weekly slot must retain the plan hash, account
assignment, and account set.

## Verification and limits

Focused synthetic tests cover assignment coverage and rotation, account/auth
identity binding, per-account workload and reserve arithmetic, route mismatch
refusal, catalog/status gates, the runner's assignment flags, account-to-pair
selection, retry pins, and result assignment checks. They make no model or
provider inference calls. A read-only local preflight of the three named
Codex homes confirmed distinct ChatGPT account identity claims, `gpt-6-luna`
catalog presence and `ordinaryUsageAllowed=true`; the weekly responses
contained no absolute allowance. The utilization percentages are retained only
as status observations, never as capacity evidence.

No per-account absolute-capacity receipt, complete run reservation, or matching
root-issued live selection exists. The runner therefore cannot launch the
126-attempt evaluation. Two consecutive complete weekly runs, independent
blind grading and reviewed automatic candidate admission remain unestablished.
