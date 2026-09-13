import unittest

from trial_repair_assessment import repair_assessment


class RepairAssessmentTests(unittest.TestCase):
    def test_request_cap_does_not_become_task_or_capability_failure(self):
        relay = dict(limits=dict(requests=2), requests=[{}, {}], rejection_codes={'429': 1})
        events = [dict(type='error', error=dict(name='APIError', data=dict(statusCode=429)))]
        for passed in (False, True):
            with self.subTest(candidate_passed=passed):
                result = repair_assessment(1, dict(passed=passed), events, relay)
                self.assertEqual((result['task_outcome'], result['failure_domain'], result['failure_kind']),
                                 ('unknown', 'binding', 'quota'))

    def test_unclassified_nonzero_exit_stays_unknown(self):
        # A model's prose about quota is not a host-observed API error.
        events = [dict(type='text', part=dict(text='APIError 429 quota exceeded'))]
        result = repair_assessment(1, dict(passed=True), events, {})
        self.assertEqual((result['task_outcome'], result['failure_domain']), ('unknown', 'unknown'))

    def test_completed_process_still_needs_valid_artifact(self):
        for passed, outcome, domain in [(True, 'accepted', 'none'), (False, 'rejected', 'task')]:
            with self.subTest(passed=passed):
                result = repair_assessment(0, dict(passed=passed), [], {})
                self.assertEqual((result['task_outcome'], result['failure_domain']), (outcome, domain))
