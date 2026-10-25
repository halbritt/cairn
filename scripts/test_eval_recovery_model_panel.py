"""Regression tests for current and historical recovery classification panels."""
import hashlib
import json
import unittest

from eval_recovery_model_panel import PANEL, PANEL_V2, evaluate


FROZEN_V1_SHA256 = "305c87dd267df559afa6d853c417de07a96d1b475f264c81c861c164f3b7024f"


class RecoveryModelPanelTests(unittest.TestCase):
    def panel(self, path):
        return json.loads(path.read_text())

    def test_historical_v1_remains_compatible_and_byte_stable(self):
        self.assertEqual(hashlib.sha256(PANEL.read_bytes()).hexdigest(), FROZEN_V1_SHA256)
        result = evaluate(self.panel(PANEL))
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["panel_schema"], "cairn.recovery-panel/1")
        self.assertEqual(result["cases"], 10)
        self.assertEqual(result["panel_sha256"], FROZEN_V1_SHA256)

    def test_v2_preserves_exact_diagnostics_and_transient_provider_work(self):
        result = evaluate(self.panel(PANEL_V2))
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["panel_schema"], "cairn.recovery-panel/2")
        self.assertEqual(result["cases"], 12)
        self.assertEqual(result["joined_owner_prompt_native_turns"], 1)
        self.assertIsNone(result["joined_owner_prompt_responses"])
        self.assertEqual(result["phase_responses"]["admission"], 0)
        self.assertEqual(result["phase_provider_turns"]["admission"], 0)
        self.assertEqual(result["phase_provider_turns"]["tool"], 1)
        self.assertIsNone(result["phase_responses"]["tool"])
        self.assertEqual(result["phase_operator_actions"]["tool"],
                         {"intervention": 1, "manual_retry": 1})

        panel = self.panel(PANEL_V2)
        case = next(case for case in panel["cases"]
                    if case["id"] == "transient_provider_failure_same_attempt")
        self.assertEqual(case["observation"]["code"], "PROVIDER_TRANSIENT_FAILURE")
        self.assertEqual(case["observation"]["recovery_state"], "retry_same_intent")
        self.assertEqual(case["observation"]["operator_action"], "manual_retry")

    def test_v2_requires_declared_case_count(self):
        panel = self.panel(PANEL_V2)
        panel["expected_case_count"] -= 1
        with self.assertRaisesRegex(ValueError, "cases changed"):
            evaluate(panel, panel_path=PANEL_V2)


if __name__ == "__main__":
    unittest.main()
