import json
import tempfile
import unittest
from pathlib import Path

import recovery_model_report as recovery
from workflow_model_report import opaque


EVENT = "11111111-1111-4111-8111-111111111111"
DELIVERY_ONE = "22222222-2222-4222-8222-222222222222"
DELIVERY_TWO = "33333333-3333-4333-8333-333333333333"
ATTEMPT = "44444444-4444-4444-8444-444444444444"


class RecoveryModelReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def evidence(self, observations):
        path = self.root / "evidence.json"
        path.write_text(json.dumps(dict(schema="cairn.recovery-evidence/1",
                                        observations=observations)))
        return recovery.read_evidence(path, 10)

    def observation(self, delivery_id=DELIVERY_ONE, **extra):
        return dict(event_id=EVENT, delivery_id=delivery_id, attempt_id=ATTEMPT,
                    phase="settlement", outcome="failed", recovery_state="retry_same_intent",
                    operator_action="manual_retry", code="API_CONNECTION_FAILED",
                    state_revision=2, reminder_emitted=False, **extra)

    def workflow(self):
        provider = dict(usage=dict(observed_responses=3))
        deliveries = [dict(delivery=opaque(identity), attempt=opaque(ATTEMPT), turn_ref="one-shared-turn",
                           provider=provider) for identity in (DELIVERY_ONE, DELIVERY_TWO)]
        return dict(schema="cairn.workflow-model-report/1",
                    workflows=[dict(event=opaque(EVENT), deliveries=deliveries)])

    def test_shared_turn_is_counted_once_and_identifiers_are_opaque(self):
        observations = self.evidence([self.observation(), self.observation(delivery_id=DELIVERY_TWO)])
        result = recovery.compile_report(self.workflow(), observations)
        phase = result["phases"]["settlement"]
        self.assertEqual(phase["observations"], 2)
        self.assertEqual(phase["provider_turns"], 1)
        self.assertEqual(phase["observed_responses"], 3)
        self.assertEqual(phase["joined_attempt"], 2)
        self.assertEqual(phase["operator_actions"], {"manual_retry": 2})
        self.assertNotIn(EVENT, json.dumps(result))
        self.assertNotIn(DELIVERY_ONE, json.dumps(result))
        self.assertNotIn(ATTEMPT, json.dumps(result))

    def test_pre_model_refusal_stays_unknown_without_a_matched_delivery(self):
        item = self.observation()
        item.update(phase="before_admission", outcome="refused", delivery_id=None,
                    recovery_state="pending", operator_action="unknown",
                    model_invocation="not_started")
        result = recovery.compile_report(self.workflow(), self.evidence([item]))
        row = result["observations"][0]
        self.assertEqual(row["delivery_join"], "not_applicable")
        self.assertEqual(row["provider_join"], "not_invoked")
        self.assertEqual(row["model_work_class"], "observed_pre_model_refusal")
        self.assertEqual(result["phases"]["before_admission"]["observed_responses"], 0)
        self.assertEqual(result["phases"]["before_admission"]["missing_provider"], 0)

    def test_absent_model_evidence_is_unknown_not_zero(self):
        item = self.observation()
        item.update(phase="before_admission", outcome="refused", delivery_id=None,
                    recovery_state="pending", operator_action="unknown")
        result = recovery.compile_report(self.workflow(), self.evidence([item]))
        self.assertEqual(result["observations"][0]["model_work_class"], "unknown")
        self.assertIsNone(result["phases"]["before_admission"]["observed_responses"])

    def test_delivery_without_exact_attempt_cannot_inherit_provider_turn(self):
        workflow = self.workflow()
        workflow["workflows"][0]["deliveries"][0]["attempt"] = opaque(DELIVERY_TWO)
        result = recovery.compile_report(workflow, self.evidence([self.observation()]))
        row = result["observations"][0]
        self.assertEqual(row["delivery_join"], "matched")
        self.assertEqual(row["attempt_join"], "mismatch")
        self.assertEqual(row["provider_join"], "unknown")
        self.assertIsNone(result["phases"]["settlement"]["observed_responses"])

    def test_provider_projection_without_turn_identity_stays_unknown(self):
        workflow = self.workflow()
        workflow["workflows"][0]["deliveries"][0]["turn_ref"] = None
        result = recovery.compile_report(workflow, self.evidence([self.observation()]))
        self.assertEqual(result["observations"][0]["provider_join"], "unknown")
        self.assertIsNone(result["phases"]["settlement"]["observed_responses"])

    def test_same_delivery_retried_attempt_uses_only_exact_attempt_turn(self):
        workflow = self.workflow()
        previous = dict(workflow['workflows'][0]['deliveries'][0],
                        attempt=opaque(DELIVERY_TWO), turn_ref='previous-turn',
                        provider=dict(usage=dict(observed_responses=99)))
        workflow['workflows'][0]['deliveries'].insert(0, previous)
        result = recovery.compile_report(workflow, self.evidence([self.observation()]))
        self.assertEqual(result['observations'][0]['attempt_join'], 'matched')
        self.assertEqual(result['phases']['settlement']['provider_turns'], 1)
        self.assertEqual(result['phases']['settlement']['observed_responses'], 3)

    def test_self_drain_attempt_joins_only_its_own_turn(self):
        item = self.observation()
        item['attempt_id'] = 'drain-' + ATTEMPT
        workflow = self.workflow()
        workflow['workflows'][0]['deliveries'][0]['attempt'] = opaque('drain-' + ATTEMPT)
        result = recovery.compile_report(workflow, self.evidence([item]))
        self.assertEqual(result['observations'][0]['attempt_join'], 'matched')
        self.assertEqual(result['observations'][0]['provider_join'], 'joined')

    def test_no_model_claim_conflicting_with_exact_provider_refuses(self):
        item = self.observation()
        item["model_invocation"] = "not_started"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            recovery.compile_report(self.workflow(), self.evidence([item]))

    def test_repeated_reminder_same_attempt_and_revision_is_reported(self):
        first = self.observation()
        first.update(reminder_emitted=True)
        second = self.observation(delivery_id=DELIVERY_TWO)
        second.update(reminder_emitted=True)
        result = recovery.compile_report(self.workflow(), self.evidence([first, second]))
        self.assertEqual(result["repeated_emitted_reminder_revisions"], 1)
        second["reminder_kind"] = "waiting_work"
        result = recovery.compile_report(self.workflow(), self.evidence([first, second]))
        self.assertEqual(result["repeated_emitted_reminder_revisions"], 0)

    def test_distinct_reminder_kinds_share_one_state_revision_without_colliding(self):
        first = self.observation()
        first.update(reminder_emitted=True, reminder_kind="turn_end")
        second = self.observation()
        second.update(reminder_emitted=True, reminder_kind="waiting_work")

        observations = self.evidence([first, second])
        result = recovery.compile_report(self.workflow(), observations)

        self.assertEqual(len(observations), 2)
        self.assertEqual(result["repeated_emitted_reminder_revisions"], 0)
        self.assertEqual(result["phases"]["settlement"]["provider_turns"], 1)
        self.assertEqual(result["phases"]["settlement"]["observed_responses"], 3)

        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.evidence([first, dict(first)])

    def test_duplicate_provider_response_identity_blocks_response_total(self):
        workflow = self.workflow()
        workflow["workflows"][0]["deliveries"][0]["provider"]["duplicate_response_id"] = True
        result = recovery.compile_report(workflow, self.evidence([self.observation()]))
        self.assertIsNone(result["phases"]["settlement"]["observed_responses"])

    def test_duplicate_workflow_event_cannot_replace_exact_join(self):
        workflow = self.workflow()
        workflow["workflows"].append(dict(event=workflow["workflows"][0]["event"], deliveries=[]))
        with self.assertRaisesRegex(ValueError, "duplicate workflow event"):
            recovery.compile_report(workflow, self.evidence([self.observation()]))

    def test_one_native_turn_cannot_have_conflicting_response_counts(self):
        workflow = self.workflow()
        workflow["workflows"][0]["deliveries"][1]["provider"] = dict(
            usage=dict(observed_responses=4))
        with self.assertRaisesRegex(ValueError, "conflicting provider projection"):
            recovery.compile_report(workflow, self.evidence([
                self.observation(), self.observation(delivery_id=DELIVERY_TWO)]))

    def test_provider_response_count_must_be_nonnegative_integer(self):
        for count in (-1, True, "3", None):
            with self.subTest(count=count):
                workflow = self.workflow()
                workflow["workflows"][0]["deliveries"][0]["provider"] = dict(
                    usage=dict(observed_responses=count))
                with self.assertRaisesRegex(ValueError, "invalid provider response count"):
                    recovery.compile_report(workflow, self.evidence([self.observation()]))

    def test_malformed_and_unbounded_metadata_refuse(self):
        cases = [dict(phase="guess"), dict(code="secret text"),
                 dict(reminder_emitted="yes"), dict(reminder_emitted=True, state_revision=None),
                 dict(operator_action="arbitrary"), dict(state_revision=-1),
                 dict(model_invocation="assumed"), dict(reminder_kind="unbounded text"),
                 dict(attempt_id=None)]
        for change in cases:
            with self.subTest(change=change):
                item = self.observation()
                item.update(change)
                with self.assertRaises(ValueError):
                    self.evidence([item])

    def test_duplicate_observation_and_over_limit_refuse(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.evidence([self.observation(), self.observation()])
        path = self.root / "evidence.json"
        with self.assertRaisesRegex(ValueError, "max-observations"):
            recovery.read_evidence(path, 1)


if __name__ == "__main__":
    unittest.main()
