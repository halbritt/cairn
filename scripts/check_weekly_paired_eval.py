#!/usr/bin/env python3
"""Deterministic checker for CAIRN-107 weekly paired evaluation and CAIRN-10 task value.

Integrates three verification gates without modifying frozen trial_sdk sources:
1. Usage pre-check gate (`check_usage_precheck`) preventing mid-cohort HTTP 429
   cutoffs (such as the 2026-09-29 15/126 weekly limit truncation) before any
   21-case x 2-arm x 3-seed (126-attempt / 63-pair) weekly evaluation launches.
2. Dual-blind second-grader agreement and dispute routing
   (`evaluate_dual_grader_agreement`) computing per-criterion agreement,
   Cohen's kappa, changed-pair counts, and unblinded-safe dispute routing.
3. Cross-turn, cross-session, and cross-harness durable task-value ledger
   (`summarize_durable_task_value`) separating verified positive transfers and
   cross-agent review catches from null/negative automatic retrieval trials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from typing import Any, Mapping, Sequence

WEEKLY_CASES = 21
WEEKLY_ARMS = 2
WEEKLY_SEEDS = 3
WEEKLY_ATTEMPTS = WEEKLY_CASES * WEEKLY_ARMS * WEEKLY_SEEDS  # 126
WEEKLY_PAIRS = WEEKLY_CASES * WEEKLY_SEEDS  # 63

PRECHECK_OK = "PRECHECK_OK"
PRECHECK_INSUFFICIENT_HEADROOM = "PRECHECK_INSUFFICIENT_HEADROOM"
PRECHECK_INVALID_COHORT = "PRECHECK_INVALID_COHORT"

REQUIRED_ROLES = ("primary_actor", "secondary_actor", "primary_grader", "secondary_grader")
SUPPORTED_HARNESSES = ("Codex", "Claude Code", "OpenCode", "Agy", "Hermes")
BLIND_FORBIDDEN_KEYS = frozenset({"arm", "arm_map", "unblinded_arm", "CURRENT", "CAND"})


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_usage_precheck(
    plan_demand: Mapping[str, Any],
    quota_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate provider quota headroom before admitting a 126-attempt weekly run."""
    cases = int(plan_demand.get("cases", 0))
    arms = int(plan_demand.get("arms", 0))
    seeds = int(plan_demand.get("seeds", 0))
    attempts = int(plan_demand.get("attempts", cases * arms * seeds))
    pairs = int(plan_demand.get("pairs", cases * seeds))
    if (cases, arms, seeds, attempts, pairs) != (
        WEEKLY_CASES,
        WEEKLY_ARMS,
        WEEKLY_SEEDS,
        WEEKLY_ATTEMPTS,
        WEEKLY_PAIRS,
    ):
        return {
            "schema": "cairn.weekly-usage-precheck/1",
            "status": "refused",
            "code": PRECHECK_INVALID_COHORT,
            "admitted": False,
            "reason": f"Expected 21x2x3=126 attempts (63 pairs), got {cases}x{arms}x{seeds}={attempts} ({pairs} pairs)",
        }

    roles_demand = plan_demand.get("roles", {})
    allowances = quota_snapshot.get("roles", {})
    shortfalls: list[dict[str, Any]] = []
    role_summaries: dict[str, dict[str, Any]] = {}

    total_required_tokens = 0
    total_required_requests = 0
    total_remaining_tokens = 0
    total_remaining_requests = 0

    for role in REQUIRED_ROLES:
        if role not in roles_demand or role not in allowances:
            shortfalls.append({"role": role, "reason": "missing_role_declaration"})
            continue
        req = roles_demand[role]
        rem = allowances[role]
        req_tokens = int(req.get("required_tokens", 0))
        req_requests = int(req.get("required_requests", 0))
        rem_tokens = int(rem.get("remaining_tokens", 0))
        rem_requests = int(rem.get("remaining_requests", 0))
        http_429 = bool(rem.get("http_429_active", False))
        cutoff_attempt = rem.get("projected_cutoff_attempt")

        total_required_tokens += req_tokens
        total_required_requests += req_requests
        total_remaining_tokens += rem_tokens
        total_remaining_requests += rem_requests

        role_ok = (
            not http_429
            and rem_tokens >= req_tokens
            and rem_requests >= req_requests
            and (cutoff_attempt is None or int(cutoff_attempt) >= WEEKLY_ATTEMPTS)
        )
        role_summaries[role] = {
            "required_tokens": req_tokens,
            "remaining_tokens": rem_tokens,
            "required_requests": req_requests,
            "remaining_requests": rem_requests,
            "http_429_active": http_429,
            "projected_cutoff_attempt": cutoff_attempt,
            "sufficient": role_ok,
        }
        if not role_ok:
            shortfalls.append(
                {
                    "role": role,
                    "token_deficit": max(0, req_tokens - rem_tokens),
                    "request_deficit": max(0, req_requests - rem_requests),
                    "http_429_active": http_429,
                    "projected_cutoff_attempt": cutoff_attempt,
                }
            )

    weekly_pool = quota_snapshot.get("weekly_pool", {})
    pool_rem_tokens = int(weekly_pool.get("remaining_tokens", total_remaining_tokens))
    pool_rem_requests = int(weekly_pool.get("remaining_requests", total_remaining_requests))
    pool_429 = bool(weekly_pool.get("http_429_active", False))
    pool_cutoff = weekly_pool.get("observed_cutoff_attempt")
    if (
        pool_429
        or pool_rem_tokens < total_required_tokens
        or pool_rem_requests < total_required_requests
        or (pool_cutoff is not None and int(pool_cutoff) < WEEKLY_ATTEMPTS)
    ):
        shortfalls.append(
            {
                "role": "weekly_pool",
                "token_deficit": max(0, total_required_tokens - pool_rem_tokens),
                "request_deficit": max(0, total_required_requests - pool_rem_requests),
                "http_429_active": pool_429,
                "observed_cutoff_attempt": pool_cutoff,
            }
        )

    admitted = len(shortfalls) == 0
    return {
        "schema": "cairn.weekly-usage-precheck/1",
        "status": "admitted_for_precheck" if admitted else "refused",
        "code": PRECHECK_OK if admitted else PRECHECK_INSUFFICIENT_HEADROOM,
        "admitted": admitted,
        "cohort": {"cases": cases, "arms": arms, "seeds": seeds, "attempts": attempts, "pairs": pairs},
        "totals": {
            "required_tokens": total_required_tokens,
            "remaining_tokens": pool_rem_tokens,
            "required_requests": total_required_requests,
            "remaining_requests": pool_rem_requests,
        },
        "roles": role_summaries,
        "shortfalls": shortfalls,
    }


def _cohens_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float | None:
    """Compute nominal Cohen's kappa between two equal-length rating sequences."""
    n = len(labels_a)
    if n == 0 or n != len(labels_b):
        return None
    categories = sorted(set(labels_a) | set(labels_b))
    observed_agree = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    expected_agree = 0.0
    for cat in categories:
        p_a = sum(1 for a in labels_a if a == cat) / n
        p_b = sum(1 for b in labels_b if b == cat) / n
        expected_agree += p_a * p_b
    if abs(1.0 - expected_agree) < 1e-12:
        return 1.0 if abs(1.0 - observed_agree) < 1e-12 else 0.0
    return round((observed_agree - expected_agree) / (1.0 - expected_agree), 6)


def _contains_unblinded_token(obj: Any) -> bool:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if str(k) in BLIND_FORBIDDEN_KEYS or _contains_unblinded_token(v):
                return True
        return False
    if isinstance(obj, (list, tuple)):
        return any(_contains_unblinded_token(item) for item in obj)
    if isinstance(obj, str):
        return obj in ("CURRENT", "CAND")
    return False


def evaluate_dual_grader_agreement(
    pairs: Sequence[Mapping[str, Any]],
    *,
    default_dispute_owner: str = "coordinator-adjudicator",
) -> dict[str, Any]:
    """Join primary and independent secondary blind-grader judgments over paired attempts."""
    if not pairs:
        raise ValueError("pairs must be non-empty")

    criterion_matches: dict[str, list[bool]] = {}
    primary_sample_verdicts: list[str] = []
    secondary_sample_verdicts: list[str] = []
    primary_preferences: list[str] = []
    secondary_preferences: list[str] = []
    dispute_queue: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    changed_pair_count = 0
    agreed_pair_count = 0

    for pair in pairs:
        if _contains_unblinded_token(pair):
            raise ValueError(f"Unblinded arm label exposed in blind pair packet: {pair.get('pair_id')}")
        pair_id = str(pair["pair_id"])
        case_id = str(pair["case"])
        seed = int(pair["seed"])
        samples = list(pair["samples"])
        if len(samples) != 2 or samples[0] == samples[1]:
            raise ValueError(f"Pair {pair_id} must declare two distinct blind sample IDs")
        is_changed = bool(pair.get("changed", True))
        if is_changed:
            changed_pair_count += 1
        dispute_owner = str(pair.get("dispute_owner", default_dispute_owner))
        primary = pair["primary"]
        secondary = pair["secondary"]
        if primary.get("agent_id") == secondary.get("agent_id") or primary.get("session_id") == secondary.get("session_id"):
            raise ValueError(f"Pair {pair_id} primary and secondary graders must be independent agents/sessions")

        p_verdicts = primary.get("verdicts", {})
        s_verdicts = secondary.get("verdicts", {})
        for sample_id in samples:
            if sample_id not in p_verdicts or sample_id not in s_verdicts:
                raise ValueError(f"Pair {pair_id} missing verdict for sample {sample_id}")
            primary_sample_verdicts.append(str(p_verdicts[sample_id]))
            secondary_sample_verdicts.append(str(s_verdicts[sample_id]))

        p_pref = str(primary.get("preference", "tie"))
        s_pref = str(secondary.get("preference", "tie"))
        primary_preferences.append(p_pref)
        secondary_preferences.append(s_pref)

        p_criteria = dict(primary.get("criteria", {}))
        s_criteria = dict(secondary.get("criteria", {}))
        p_criteria.setdefault("verdict_match", p_verdicts == s_verdicts)
        s_criteria.setdefault("verdict_match", p_verdicts == s_verdicts)
        all_crit_keys = sorted(set(p_criteria) | set(s_criteria))
        disagreed_criteria: list[str] = []
        for crit in all_crit_keys:
            if crit == "verdict_match":
                continue
            match = p_criteria.get(crit) == s_criteria.get(crit)
            criterion_matches.setdefault(crit, []).append(match)
            if not match:
                disagreed_criteria.append(crit)

        verdicts_agree = all(p_verdicts[sid] == s_verdicts[sid] for sid in samples)
        pref_agree = p_pref == s_pref
        criterion_matches.setdefault("sample_verdicts", []).append(verdicts_agree)
        criterion_matches.setdefault("pair_preference", []).append(pref_agree)

        pair_agreed = verdicts_agree and pref_agree and not disagreed_criteria
        if pair_agreed:
            agreed_pair_count += 1
        else:
            dispute_queue.append(
                {
                    "pair_id": pair_id,
                    "case": case_id,
                    "seed": seed,
                    "samples": samples,
                    "dispute_owner": dispute_owner,
                    "blinding_preserved": True,
                    "verdicts_agree": verdicts_agree,
                    "preference_agree": pref_agree,
                    "disagreed_criteria": disagreed_criteria,
                    "primary_sha256": canonical_sha256(primary),
                    "secondary_sha256": canonical_sha256(secondary),
                }
            )
        pair_reports.append(
            {
                "pair_id": pair_id,
                "case": case_id,
                "seed": seed,
                "changed": is_changed,
                "agreed": pair_agreed,
                "dispute_routed": not pair_agreed,
                "dispute_owner": dispute_owner if not pair_agreed else None,
            }
        )

    per_criterion_agreement = {
        crit: round(sum(1 for m in matches if m) / len(matches), 6)
        for crit, matches in sorted(criterion_matches.items())
        if matches
    }
    return {
        "schema": "cairn.weekly-dual-grader-agreement/1",
        "total_pairs": len(pairs),
        "changed_pair_count": changed_pair_count,
        "agreed_pair_count": agreed_pair_count,
        "disagreed_pair_count": len(dispute_queue),
        "pair_agreement_rate": round(agreed_pair_count / len(pairs), 6),
        "per_criterion_agreement": per_criterion_agreement,
        "sample_verdict_kappa": _cohens_kappa(primary_sample_verdicts, secondary_sample_verdicts),
        "preference_kappa": _cohens_kappa(primary_preferences, secondary_preferences),
        "blinding_preserved": True,
        "dispute_queue": dispute_queue,
        "pairs": pair_reports,
    }


def summarize_durable_task_value(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate source-linked task value observations across turns, sessions, and harnesses."""
    if not observations:
        raise ValueError("observations must be non-empty")

    positive_transfers: list[dict[str, Any]] = []
    negative_or_null_trials: list[dict[str, Any]] = []
    unknown_or_exploratory: list[dict[str, Any]] = []
    by_harness: dict[str, dict[str, int]] = {
        h: {"positive_transfer": 0, "null_or_negative_trial": 0, "unknown_or_exploratory": 0}
        for h in SUPPORTED_HARNESSES
    }
    by_horizon: dict[str, int] = {"turn": 0, "session": 0, "cross_session": 0, "cross_harness": 0}
    context_bytes_samples: list[int] = []
    latency_ms_samples: list[int] = []

    for obs in observations:
        obs_id = str(obs["id"])
        source_doc = str(obs.get("source", "")).strip()
        if not source_doc:
            raise ValueError(f"Observation {obs_id} must include a non-empty source document link")
        category = str(obs["category"])
        if category not in ("positive_transfer", "null_or_negative_trial", "unknown_or_exploratory"):
            raise ValueError(f"Observation {obs_id} has unsupported category: {category}")
        harnesses = list(obs.get("harnesses", []))
        if not harnesses or any(h not in SUPPORTED_HARNESSES for h in harnesses):
            raise ValueError(f"Observation {obs_id} must list valid supported harnesses")
        horizon = str(obs.get("horizon", "session"))
        if horizon not in by_horizon:
            raise ValueError(f"Observation {obs_id} has unsupported horizon: {horizon}")
        by_horizon[horizon] += 1
        for h in harnesses:
            by_harness[h][category] += 1

        ctx_bytes = int(obs.get("net_context_bytes", 0))
        lat_ms = int(obs.get("latency_ms", 0))
        if ctx_bytes < 0 or lat_ms < 0:
            raise ValueError(f"Observation {obs_id} cost metrics must be non-negative")
        context_bytes_samples.append(ctx_bytes)
        latency_ms_samples.append(lat_ms)

        record = {
            "id": obs_id,
            "ticket": obs.get("ticket"),
            "title": str(obs.get("title", obs_id)),
            "category": category,
            "horizon": horizon,
            "harnesses": harnesses,
            "source": source_doc,
            "net_context_bytes": ctx_bytes,
            "latency_ms": lat_ms,
            "causal_claim_scope": str(obs.get("causal_claim_scope", "bounded_observation_only")),
        }
        if category == "positive_transfer":
            positive_transfers.append(record)
        elif category == "null_or_negative_trial":
            negative_or_null_trials.append(record)
        else:
            unknown_or_exploratory.append(record)

    sorted_lat = sorted(latency_ms_samples)
    p95_idx = min(len(sorted_lat) - 1, max(0, int(round(0.95 * (len(sorted_lat) - 1)))))
    return {
        "schema": "cairn.durable-task-value-ledger/1",
        "total_observations": len(observations),
        "counts": {
            "positive_transfer": len(positive_transfers),
            "null_or_negative_trial": len(negative_or_null_trials),
            "unknown_or_exploratory": len(unknown_or_exploratory),
        },
        "by_harness": by_harness,
        "by_horizon": by_horizon,
        "costs": {
            "total_context_bytes": sum(context_bytes_samples),
            "median_context_bytes": int(statistics.median(context_bytes_samples)),
            "median_latency_ms": int(statistics.median(latency_ms_samples)),
            "p95_latency_ms": int(sorted_lat[p95_idx]),
        },
        "positive_transfers": positive_transfers,
        "negative_or_null_trials": negative_or_null_trials,
        "unknown_or_exploratory": unknown_or_exploratory,
        "automatic_retrieval_net_benefit_established": False,
        "cross_agent_coordination_and_curated_transfer_observed": len(positive_transfers) > 0,
    }


def default_weekly_precheck_scenarios() -> dict[str, dict[str, Any]]:
    """Return canonical pre-check scenarios (sufficient headroom vs 2026-09-29 15/126 HTTP 429 cutoff)."""
    plan_demand = {
        "cases": WEEKLY_CASES,
        "arms": WEEKLY_ARMS,
        "seeds": WEEKLY_SEEDS,
        "attempts": WEEKLY_ATTEMPTS,
        "pairs": WEEKLY_PAIRS,
        "roles": {
            "primary_actor": {"required_tokens": 1_890_000, "required_requests": 63},
            "secondary_actor": {"required_tokens": 1_890_000, "required_requests": 63},
            "primary_grader": {"required_tokens": 630_000, "required_requests": 63},
            "secondary_grader": {"required_tokens": 630_000, "required_requests": 63},
        },
    }
    sufficient_quota = {
        "roles": {
            "primary_actor": {"remaining_tokens": 3_000_000, "remaining_requests": 150, "http_429_active": False},
            "secondary_actor": {"remaining_tokens": 3_000_000, "remaining_requests": 150, "http_429_active": False},
            "primary_grader": {"remaining_tokens": 1_200_000, "remaining_requests": 150, "http_429_active": False},
            "secondary_grader": {"remaining_tokens": 1_200_000, "remaining_requests": 150, "http_429_active": False},
        },
        "weekly_pool": {"remaining_tokens": 8_400_000, "remaining_requests": 600, "http_429_active": False},
    }
    http_429_cutoff_quota = {
        "roles": {
            "primary_actor": {
                "remaining_tokens": 225_000,
                "remaining_requests": 8,
                "http_429_active": True,
                "projected_cutoff_attempt": 15,
            },
            "secondary_actor": {
                "remaining_tokens": 225_000,
                "remaining_requests": 7,
                "http_429_active": True,
                "projected_cutoff_attempt": 15,
            },
            "primary_grader": {"remaining_tokens": 630_000, "remaining_requests": 63, "http_429_active": False},
            "secondary_grader": {"remaining_tokens": 630_000, "remaining_requests": 63, "http_429_active": False},
        },
        "weekly_pool": {
            "remaining_tokens": 1_710_000,
            "remaining_requests": 141,
            "http_429_active": True,
            "observed_cutoff_attempt": 15,
        },
    }
    return {
        "plan_demand": plan_demand,
        "sufficient_quota": sufficient_quota,
        "http_429_cutoff_quota": http_429_cutoff_quota,
    }


def default_dual_grader_cohort() -> list[dict[str, Any]]:
    """Return representative blind comparison packets for dual-grader agreement and dispute routing."""
    return [
        {
            "pair_id": "pair-deploy-stamp-s0",
            "case": "deploy-stamp",
            "seed": 0,
            "samples": ["sample-a1", "sample-b1"],
            "changed": True,
            "dispute_owner": "coordinator-adjudicator",
            "primary": {
                "agent_id": "grader-primary-1",
                "session_id": "sess-gp-1",
                "verdicts": {"sample-a1": "mistake", "sample-b1": "correct"},
                "preference": "sample-b1",
                "criteria": {"task_completion": True, "mistake_avoidance": True, "provenance_preserved": True},
            },
            "secondary": {
                "agent_id": "grader-secondary-2",
                "session_id": "sess-gs-2",
                "verdicts": {"sample-a1": "mistake", "sample-b1": "correct"},
                "preference": "sample-b1",
                "criteria": {"task_completion": True, "mistake_avoidance": True, "provenance_preserved": True},
            },
        },
        {
            "pair_id": "pair-hermes-sigterm-s0",
            "case": "hermes-sigterm",
            "seed": 0,
            "samples": ["sample-a2", "sample-b2"],
            "changed": True,
            "dispute_owner": "coordinator-adjudicator",
            "primary": {
                "agent_id": "grader-primary-1",
                "session_id": "sess-gp-1",
                "verdicts": {"sample-a2": "correct", "sample-b2": "mistake"},
                "preference": "sample-a2",
                "criteria": {"task_completion": True, "mistake_avoidance": True, "provenance_preserved": True},
            },
            "secondary": {
                "agent_id": "grader-secondary-2",
                "session_id": "sess-gs-2",
                "verdicts": {"sample-a2": "incomplete", "sample-b2": "mistake"},
                "preference": "tie",
                "criteria": {"task_completion": False, "mistake_avoidance": True, "provenance_preserved": True},
            },
        },
        {
            "pair_id": "pair-local-ci-s1",
            "case": "local-ci",
            "seed": 1,
            "samples": ["sample-a3", "sample-b3"],
            "changed": True,
            "dispute_owner": "coordinator-adjudicator",
            "primary": {
                "agent_id": "grader-primary-1",
                "session_id": "sess-gp-1",
                "verdicts": {"sample-a3": "correct", "sample-b3": "mistake"},
                "preference": "sample-a3",
                "criteria": {"task_completion": True, "mistake_avoidance": True, "provenance_preserved": True},
            },
            "secondary": {
                "agent_id": "grader-secondary-2",
                "session_id": "sess-gs-2",
                "verdicts": {"sample-a3": "correct", "sample-b3": "mistake"},
                "preference": "sample-a3",
                "criteria": {"task_completion": True, "mistake_avoidance": True, "provenance_preserved": True},
            },
        },
    ]


def default_durable_value_observations() -> list[dict[str, Any]]:
    """Return the source-linked CAIRN-10 task-value ledger across turns, sessions, and harnesses."""
    return [
        {
            "id": "obs-cross-harness-storage-transfer",
            "ticket": "CAIRN-10",
            "title": "Cross-harness storage note transfer (Codex -> OpenCode)",
            "category": "positive_transfer",
            "horizon": "cross_harness",
            "harnesses": ["Codex", "OpenCode"],
            "source": "docs/verification/mcp-2026-09-08.md",
            "net_context_bytes": 1420,
            "latency_ms": 110,
            "causal_claim_scope": "verified_curated_transfer_without_baseline_control",
        },
        {
            "id": "obs-opencode-config-procedure-repair",
            "ticket": "CAIRN-10",
            "title": "OpenCode MCP configuration procedure correction and fresh-session reuse",
            "category": "positive_transfer",
            "horizon": "cross_session",
            "harnesses": ["Codex", "OpenCode"],
            "source": "docs/verification/mcp-host-use-2026-09-08.md",
            "net_context_bytes": 1680,
            "latency_ms": 140,
            "causal_claim_scope": "verified_before_after_procedure_reuse",
        },
        {
            "id": "obs-reviewed-lesson-recurrence-avoidance",
            "ticket": "CAIRN-10",
            "title": "Reviewed-lesson recurrence avoidance on repeated repair",
            "category": "positive_transfer",
            "horizon": "cross_session",
            "harnesses": ["Codex"],
            "source": "docs/verification/reviewed-recurrence-2026-09-08.md",
            "net_context_bytes": 1850,
            "latency_ms": 190,
            "causal_claim_scope": "bounded_recurrence_avoidance_matches_direct_context",
        },
        {
            "id": "obs-predeploy-cross-agent-review-defects",
            "ticket": "CAIRN-10",
            "title": "Pre-deploy cross-agent review defects caught across harnesses",
            "category": "positive_transfer",
            "horizon": "cross_harness",
            "harnesses": ["Codex", "Claude Code", "OpenCode", "Agy", "Hermes"],
            "source": "docs/verification/task-eval-adjudication-2026-09-28.md",
            "net_context_bytes": 2400,
            "latency_ms": 320,
            "causal_claim_scope": "verified_multi_agent_coordination_and_audit_catch",
        },
        {
            "id": "obs-cairn-40-paired-trial",
            "ticket": "CAIRN-40",
            "title": "CAIRN-40 paired retrieval trial (null net candidate advantage)",
            "category": "null_or_negative_trial",
            "horizon": "session",
            "harnesses": ["Claude Code"],
            "source": "docs/verification/task-eval-baseline-2026-09-28.md",
            "net_context_bytes": 1881,
            "latency_ms": 500,
            "causal_claim_scope": "paired_trial_null_candidate_advantage",
        },
        {
            "id": "obs-cairn-49-paired-trial",
            "ticket": "CAIRN-49",
            "title": "CAIRN-49 native Striatum recurrence paired comparison (timeout/null)",
            "category": "null_or_negative_trial",
            "horizon": "session",
            "harnesses": ["Codex"],
            "source": "docs/verification/native-permissions-2026-09-08.md",
            "net_context_bytes": 2150,
            "latency_ms": 1300,
            "causal_claim_scope": "paired_trial_no_admitted_repair",
        },
        {
            "id": "obs-cairn-109-paired-trial",
            "ticket": "CAIRN-109",
            "title": "CAIRN-109 candidate vs baseline v4 regraded paired comparison (0 improved, 2 regressed)",
            "category": "null_or_negative_trial",
            "horizon": "cross_session",
            "harnesses": ["Claude Code"],
            "source": "docs/verification/task-eval-adjudication-2026-09-28.md",
            "net_context_bytes": 1881,
            "latency_ms": 1300,
            "causal_claim_scope": "paired_trial_candidate_regression_on_local_ci",
        },
        {
            "id": "obs-cairn-113-paired-trial",
            "ticket": "CAIRN-113",
            "title": "CAIRN-113 1,900-distractor collection growth & relevance gate attrition",
            "category": "null_or_negative_trial",
            "horizon": "turn",
            "harnesses": ["Claude Code"],
            "source": "docs/verification/task-eval-baseline-2026-09-28.md",
            "net_context_bytes": 1881,
            "latency_ms": 2450,
            "causal_claim_scope": "retrieval_funnel_distractor_displacement",
        },
        {
            "id": "obs-cairn-115-paired-trial",
            "ticket": "CAIRN-115",
            "title": "CAIRN-115 weekly cohort HTTP 429 truncation at 15/126 attempts",
            "category": "null_or_negative_trial",
            "horizon": "cross_session",
            "harnesses": ["Claude Code", "Codex"],
            "source": "docs/verification/task-eval-adjudication-2026-09-28.md",
            "net_context_bytes": 1881,
            "latency_ms": 1800,
            "causal_claim_scope": "incomplete_cohort_refused_as_effectiveness_evidence",
        },
    ]


def run_all_checks() -> dict[str, Any]:
    scenarios = default_weekly_precheck_scenarios()
    precheck_pass = check_usage_precheck(scenarios["plan_demand"], scenarios["sufficient_quota"])
    precheck_refusal = check_usage_precheck(scenarios["plan_demand"], scenarios["http_429_cutoff_quota"])
    grader_report = evaluate_dual_grader_agreement(default_dual_grader_cohort())
    value_ledger = summarize_durable_task_value(default_durable_value_observations())
    ok = (
        precheck_pass["admitted"] is True
        and precheck_pass["code"] == PRECHECK_OK
        and precheck_refusal["admitted"] is False
        and precheck_refusal["code"] == PRECHECK_INSUFFICIENT_HEADROOM
        and grader_report["blinding_preserved"] is True
        and grader_report["disagreed_pair_count"] == 1
        and value_ledger["counts"]["positive_transfer"] == 4
        and value_ledger["counts"]["null_or_negative_trial"] == 5
        and value_ledger["automatic_retrieval_net_benefit_established"] is False
    )
    return {
        "schema": "cairn.weekly-paired-eval-and-task-value-check/1",
        "status": "ok" if ok else "failed",
        "precheck_pass": precheck_pass,
        "precheck_refusal_429": precheck_refusal,
        "dual_grader_agreement": grader_report,
        "durable_task_value_ledger": value_ledger,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true", help="Emit single-line canonical JSON")
    args = parser.parse_args(argv)
    report = run_all_checks()
    if args.compact:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
