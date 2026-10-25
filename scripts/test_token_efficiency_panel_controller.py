"""Controller custody for the frozen semantic-unavailable panel source."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import token_efficiency_panel_controller as controller


class SemanticUnavailableControllerTests(unittest.TestCase):
    def test_all_four_arm_adapters_bind_exact_source_and_unrun_transition(self):
        expected = {
            "sources": ["lexical_anchor"],
            "events": [],
            "transitions": [{"phase": "before_search", "action": "disable_semantic",
                             "target": "lexical_anchor"}],
            "publishers": {},
            "sessions": {"worker": "worker"},
        }
        databases = set()
        for arm in ("baseline", "envelope", "batch", "combined"):
            workflow = f"{arm}.semantic_unavailable"
            with self.subTest(arm=arm):
                self.assertEqual(controller.case_name(workflow), "semantic_unavailable")
                self.assertEqual(controller.CASE_ADAPTERS["semantic_unavailable"], expected)
                databases.add(controller.database_name(workflow))
        self.assertEqual(len(databases), 4)

    def test_source_forgotten_case_keeps_exact_post_admission_transition(self):
        expected_setup = {
            "sources": [],
            "events": [{"id": "request_missing_source", "kind": "request",
                        "recipient": "worker",
                        "body": "Read source SRC-7 and summarize it."}],
            "transitions": [{"phase": "after_admission_before_read",
                             "action": "remove_source",
                             "target": "request_missing_source"}],
            "publishers": {"request_missing_source": "sender"},
            "sessions": {"worker": "worker"},
        }
        expected_adapter = expected_setup | {
            "events": [{"id": "request_missing_source", "kind": "request",
                        "recipient": "worker"}],
        }
        fixture, _ = controller.panel.read_fixture()
        row = next(case for case in fixture["cases"]
                   if case["id"] == "source_forgotten_before_read")
        self.assertEqual(row["setup"], {key: expected_setup[key]
                                       for key in ("sources", "events", "transitions")})
        databases = set()
        for arm in ("baseline", "envelope", "batch", "combined"):
            workflow = f"{arm}.source_forgotten_before_read"
            with self.subTest(arm=arm):
                self.assertEqual(controller.case_name(workflow),
                                 "source_forgotten_before_read")
                self.assertEqual(controller.CASE_ADAPTERS["source_forgotten_before_read"],
                                 expected_adapter)
                databases.add(controller.database_name(workflow))
        self.assertEqual(len(databases), 4)


if __name__ == "__main__":
    unittest.main()
