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


if __name__ == "__main__":
    unittest.main()
