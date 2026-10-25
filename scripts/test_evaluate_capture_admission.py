import json
import unittest

import evaluate_capture_admission as audit


class CaptureAdmissionEvaluationTests(unittest.TestCase):
    def test_frozen_cases_preserve_required_selection_and_failures(self):
        report = audit.evaluate()
        cases, comparison = report["cases"], report["comparison"]
        self.assertEqual(set(cases), {"same_snapshot", "transport_only", "owner_correction",
                                      "changed_decision", "unfinished_handoff",
                                      "unfinished_handoff_at_session_end", "status_only",
                                      "quota_failure", "source_failure"})
        self.assertEqual(comparison, dict(unchanged_selector_calls=7, candidate_cases=3,
                                          candidate_selector_calls=2, required_selected_writes=4,
                                          candidate_required_misses=0, candidate_uncertain_cases=2,
                                          unchanged_empty_status_calls=1))
        self.assertFalse(cases["owner_correction"]["candidate"])
        self.assertFalse(cases["changed_decision"]["candidate"])
        self.assertFalse(cases["unfinished_handoff"]["candidate"])
        self.assertFalse(cases["unfinished_handoff_at_session_end"]["candidate"])
        self.assertEqual(cases["changed_decision"]["selected_writes"], 1)
        self.assertEqual(cases["unfinished_handoff_at_session_end"]["selected_writes"], 1)
        self.assertEqual(cases["transport_only"]["selector_calls"], 1,
                         "shadow candidate suppressed the selector")
        self.assertFalse(cases["quota_failure"]["marker_advanced"])
        self.assertFalse(cases["source_failure"]["marker_advanced"])
        self.assertEqual(cases["source_failure"]["outcome"], "PAYLOAD_UNAVAILABLE")
        rendered = json.dumps(report)
        self.assertNotIn("Plan the migration", rendered)
        self.assertNotIn("Correction: use", rendered)


if __name__ == "__main__":
    unittest.main()
