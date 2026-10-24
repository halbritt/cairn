"""CAIRN-227 grade ledger structure tests; no task outcomes are fabricated."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import test_token_efficiency_gate as gate_tests
import token_efficiency_gate as gate
import token_efficiency_grades as grades


class GradeGateTests(unittest.TestCase):
    def setUp(self):
        fixture = gate_tests.TokenEfficiencyGateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.controller = {"schema": "cairn.token-efficiency-panel-manifest/1",
                           "fixture_sha256": fixture.frozen["plan"]["fixture_sha256"],
                           "prospective_runs": [
                               {"workflow_id": row["workflow_id"], "case": row["case"],
                                "arm": row["arm"],
                                "acceptance_check_sha256": fixture.frozen["plan"]["acceptance_checks"][row["case"]]}
                               for row in fixture.plan["runs"]]}
        self.plan = grades.freeze(fixture.frozen, fixture.fixture, self.controller)
        for row in self.plan["workflows"]:
            for stage in row["stages"]:
                self.assertEqual(stage["provider"]["account"], fixture.model["account"])
                self.assertEqual(stage["provider"]["id"], fixture.model["id"])
                self.assertEqual(stage["pin"], fixture.pins[stage["pin_name"]])

    def ledger(self):
        rows = []
        for ref in self.plan["workflows"]:
            rows.append({**ref, "verdict": "unrun", "reviewer": "reviewer-ref",
                         "independence_assertion": True, "rationale": "not run in this fixture",
                         "artifacts": []})
        return {"schema": "cairn.token-efficiency-grade-ledger/1",
                "grade_plan_sha256": gate.sha(self.plan), "grades": rows}

    def test_complete_structural_ledger_never_asserts_acceptance(self):
        ledger = self.ledger()
        first = ledger["grades"][0]
        evidence = self.root / "task-output.txt"
        evidence.write_text("opaque reviewed artifact\n")
        evidence.chmod(0o600)
        first["verdict"] = "accepted"
        first["artifacts"] = [{"path": str(evidence),
                               "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
        result = grades.report(self.plan, ledger, self.root)
        self.assertEqual(result["counts"], {"accepted": 1, "failed": 0, "unknown": 0, "unrun": 87})
        self.assertEqual(result["independent_task_acceptance"], "externally_asserted_unverified")
        self.assertEqual(result["release_decision"], "defer")

    def test_missing_workflow_refuses(self):
        ledger = self.ledger()
        ledger["grades"].pop()
        with self.assertRaisesRegex(ValueError, "exactly 88"):
            grades.report(self.plan, ledger)

    def test_false_independence_assertion_refuses(self):
        ledger = self.ledger()
        ledger["grades"][0]["independence_assertion"] = False
        with self.assertRaisesRegex(ValueError, "external independence assertion"):
            grades.report(self.plan, ledger)

    def test_changed_evidence_refuses(self):
        ledger = self.ledger()
        artifact = self.root / "task-output.txt"
        artifact.write_text("before\n")
        artifact.chmod(0o600)
        ledger["grades"][0].update(verdict="failed", artifacts=[{
            "path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}])
        artifact.write_text("after\n")
        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            grades.report(self.plan, ledger, self.root)

    def test_controller_acceptance_hash_must_match(self):
        controller = copy.deepcopy(self.controller)
        controller["prospective_runs"][0]["acceptance_check_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "acceptance check mismatch"):
            grades.freeze(self.fixture.frozen, self.fixture.fixture, controller)


if __name__ == "__main__":
    unittest.main()
