import json
import unittest

from codex_recovery_fixture_report import compile_report


EVENT = "11111111-1111-4111-8111-111111111111"
DELIVERY = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
TURN = "44444444-4444-4444-8444-444444444444"


def receipt():
    return dict(schema="cairn.codex-recovery-probe/1",
        refusal=dict(event_id=EVENT, delivery_id=DELIVERY, attempt_id=ATTEMPT,
            native_turn_id=TURN, request_id="55555555-5555-4555-8555-555555555555",
            code="STALE_LEASE", admission_state="leased", store_attempt_id=ATTEMPT,
            store_attempt_native_turn_id=TURN),
        settlement=dict(event_id=EVENT, delivery_id=DELIVERY, attempt_id=ATTEMPT,
            native_turn_id=TURN, state="handled", attempts=1))


class CodexRecoveryFixtureReportTests(unittest.TestCase):
    def test_exact_store_attempt_and_turn_join_keeps_provider_usage_unknown(self):
        result = compile_report(receipt())
        self.assertEqual(result["schema"], "cairn.recovery-model-report/3")
        self.assertEqual(result["refusal"], dict(phase="settlement", code="STALE_LEASE",
                                                 state_preserved=True))
        self.assertEqual(result["recovery"]["path"], "retry_same_attempt")
        self.assertEqual(result["settlement"]["disposition"], "handled")
        self.assertEqual(result["provider_turn"],
                         dict(status="not_observed", responses=None, usage=None))
        encoded = json.dumps(result)
        for raw in (EVENT, DELIVERY, ATTEMPT, TURN):
            self.assertNotIn(raw, encoded)

    def test_mismatched_turn_or_attempt_refuses_to_join(self):
        value = receipt()
        value["settlement"]["native_turn_id"] = "66666666-6666-4666-8666-666666666666"
        with self.assertRaisesRegex(ValueError, "identities do not match"):
            compile_report(value)
        value = receipt()
        value["refusal"]["store_attempt_id"] = DELIVERY
        with self.assertRaisesRegex(ValueError, "not bound"):
            compile_report(value)

    def test_refusal_without_confirmed_recovery_is_not_reported_as_recovered(self):
        value = receipt()
        value["settlement"]["attempts"] = 2
        with self.assertRaisesRegex(ValueError, "recovery settlement"):
            compile_report(value)


if __name__ == "__main__":
    unittest.main()
