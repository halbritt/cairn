#!/usr/bin/env python3
"""Unit tests for scripts/check_weekly_paired_eval.py (CAIRN-107 & CAIRN-10)."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_weekly_paired_eval as wpe


class TestUsagePrecheck(unittest.TestCase):
    def test_precheck_passes_when_allowance_covers_full_126_attempts(self) -> None:
        scenarios = wpe.default_weekly_precheck_scenarios()
        result = wpe.check_usage_precheck(scenarios["plan_demand"], scenarios["sufficient_quota"])
        self.assertTrue(result["admitted"])
        self.assertEqual(result["status"], "admitted_for_precheck")
        self.assertEqual(result["code"], wpe.PRECHECK_OK)
        self.assertEqual(result["cohort"]["attempts"], 126)
        self.assertEqual(result["cohort"]["pairs"], 63)
        self.assertEqual(result["shortfalls"], [])

    def test_precheck_refuses_http_429_cutoff_at_15_of_126_attempts(self) -> None:
        scenarios = wpe.default_weekly_precheck_scenarios()
        result = wpe.check_usage_precheck(scenarios["plan_demand"], scenarios["http_429_cutoff_quota"])
        self.assertFalse(result["admitted"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], wpe.PRECHECK_INSUFFICIENT_HEADROOM)
        shortfall_roles = {item["role"] for item in result["shortfalls"]}
        self.assertIn("primary_actor", shortfall_roles)
        self.assertIn("secondary_actor", shortfall_roles)
        self.assertIn("weekly_pool", shortfall_roles)

    def test_precheck_refuses_when_secondary_grader_lacks_headroom(self) -> None:
        scenarios = wpe.default_weekly_precheck_scenarios()
        quota = copy.deepcopy(scenarios["sufficient_quota"])
        quota["roles"]["secondary_grader"]["remaining_tokens"] = 100_000
        result = wpe.check_usage_precheck(scenarios["plan_demand"], quota)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["code"], wpe.PRECHECK_INSUFFICIENT_HEADROOM)
        self.assertEqual(len(result["shortfalls"]), 1)
        self.assertEqual(result["shortfalls"][0]["role"], "secondary_grader")
        self.assertEqual(result["shortfalls"][0]["token_deficit"], 530_000)

    def test_precheck_refuses_invalid_cohort_geometry(self) -> None:
        scenarios = wpe.default_weekly_precheck_scenarios()
        bad_plan = copy.deepcopy(scenarios["plan_demand"])
        bad_plan["attempts"] = 15
        result = wpe.check_usage_precheck(bad_plan, scenarios["sufficient_quota"])
        self.assertFalse(result["admitted"])
        self.assertEqual(result["code"], wpe.PRECHECK_INVALID_COHORT)



class TestDualGraderAgreement(unittest.TestCase):
    def test_dual_grader_agreement_kappa_and_dispute_routing(self) -> None:
        cohort = wpe.default_dual_grader_cohort()
        report = wpe.evaluate_dual_grader_agreement(cohort)
        self.assertEqual(report["total_pairs"], 3)
        self.assertEqual(report["changed_pair_count"], 3)
        self.assertEqual(report["agreed_pair_count"], 2)
        self.assertEqual(report["disagreed_pair_count"], 1)
        self.assertAlmostEqual(report["pair_agreement_rate"], 2 / 3, places=5)
        self.assertIsNotNone(report["sample_verdict_kappa"])
        self.assertGreater(report["sample_verdict_kappa"], 0.5)
        self.assertEqual(len(report["dispute_queue"]), 1)
        dispute = report["dispute_queue"][0]
        self.assertEqual(dispute["pair_id"], "pair-hermes-sigterm-s0")
        self.assertEqual(dispute["dispute_owner"], "owner")
        self.assertTrue(dispute["blinding_preserved"])
        self.assertIn("task_completion", dispute["disagreed_criteria"])

    def test_disputes_default_to_owner_without_declared_destination(self) -> None:
        # CAIRN-107: "Disagreements go to the owner, never to a script."
        cohort = copy.deepcopy(wpe.default_dual_grader_cohort())
        for pair in cohort:
            pair.pop("dispute_owner", None)
        report = wpe.evaluate_dual_grader_agreement(cohort)
        self.assertEqual([d["dispute_owner"] for d in report["dispute_queue"]], ["owner"])
        routed = [p for p in report["pairs"] if p["dispute_routed"]]
        self.assertEqual([p["dispute_owner"] for p in routed], ["owner"])

    def test_dispute_destination_other_than_owner_is_refused(self) -> None:
        for destination in ("coordinator-adjudicator", "script", ""):
            cohort = copy.deepcopy(wpe.default_dual_grader_cohort())
            cohort[1]["dispute_owner"] = destination
            with self.assertRaisesRegex(ValueError, "disputes go to the owner"):
                wpe.evaluate_dual_grader_agreement(cohort)
        with self.assertRaisesRegex(ValueError, "disputes go to the owner"):
            wpe.evaluate_dual_grader_agreement(wpe.default_dual_grader_cohort(),
                                               default_dispute_owner="coordinator-adjudicator")

    def test_dual_grader_refuses_unblinded_arm_label(self) -> None:
        cohort = copy.deepcopy(wpe.default_dual_grader_cohort())
        cohort[0]["primary"]["verdicts"]["sample-a1"] = "CURRENT"
        with self.assertRaisesRegex(ValueError, "Unblinded arm label exposed"):
            wpe.evaluate_dual_grader_agreement(cohort)

    def test_dual_grader_refuses_non_independent_grader_sessions(self) -> None:
        cohort = copy.deepcopy(wpe.default_dual_grader_cohort())
        cohort[0]["secondary"]["agent_id"] = cohort[0]["primary"]["agent_id"]
        with self.assertRaisesRegex(ValueError, "independent agents/sessions"):
            wpe.evaluate_dual_grader_agreement(cohort)


class TestDurableTaskValueLedger(unittest.TestCase):
    def test_ledger_preserves_positive_and_negative_trials_without_inflating_claims(self) -> None:
        observations = wpe.default_durable_value_observations()
        ledger = wpe.summarize_durable_task_value(observations)
        self.assertEqual(ledger["total_observations"], 9)
        self.assertEqual(ledger["counts"]["positive_transfer"], 4)
        self.assertEqual(ledger["counts"]["null_or_negative_trial"], 5)
        self.assertFalse(ledger["automatic_retrieval_net_benefit_established"])
        self.assertTrue(ledger["cross_agent_coordination_and_curated_transfer_observed"])
        negative_tickets = {row["ticket"] for row in ledger["negative_or_null_trials"]}
        self.assertEqual(
            negative_tickets,
            {"CAIRN-40", "CAIRN-49", "CAIRN-109", "CAIRN-113", "CAIRN-115"},
        )
        for harness in wpe.SUPPORTED_HARNESSES:
            self.assertGreaterEqual(ledger["by_harness"][harness]["positive_transfer"], 1)
        self.assertGreater(ledger["costs"]["total_context_bytes"], 0)
        self.assertGreater(ledger["costs"]["p95_latency_ms"], 0)

    def test_cli_run_all_checks_exits_zero(self) -> None:
        self.assertEqual(wpe.main(["--compact"]), 0)


if __name__ == "__main__":
    unittest.main()
