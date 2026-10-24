#!/usr/bin/env python3
"""Focused custody and blind-export tests for the synthetic CAIRN-227 panel."""
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight_token_efficiency_panel as panel
import token_efficiency_gate as gate


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.fixture_hash = panel.read_fixture()

    def test_all_cases_and_matching_arms_have_exact_custody(self):
        result = panel.manifest(self.data, self.fixture_hash)
        self.assertEqual(len(result["cases"]), 22)
        self.assertEqual(len(result["prospective_runs"]), 88)
        self.assertEqual(len({run["workflow_id"] for run in result["prospective_runs"]}), 88)
        for case in result["cases"]:
            matching = [run for run in result["prospective_runs"] if run["case"] == case["id"]]
            self.assertEqual(len(matching), 4)
            for run in matching:
                self.assertEqual(run["actor_sha256"], case["actor_sha256"])
                self.assertEqual(run["setup_sha256"], case["setup_sha256"])
                self.assertEqual(run["acceptance_check_sha256"], case["acceptance_check_sha256"])
        self.assertFalse(result["setup_executed"])
        self.assertEqual(result["native_join"], "unimplemented")
        self.assertEqual(set(result["source_body_sha256"]), set(self.data["sources"]))
        self.assertEqual(set(result["source_record_sha256"]), set(self.data["sources"]))
        burst = {run["arm"]: run for run in result["prospective_runs"]
                 if run["case"] == "routine_burst_urgent_request"}
        self.assertEqual(burst["baseline"]["intervention_sha256"],
                         burst["envelope"]["intervention_sha256"])
        self.assertEqual(burst["batch"]["intervention_sha256"],
                         burst["combined"]["intervention_sha256"])
        self.assertNotEqual(burst["baseline"]["intervention_sha256"],
                            burst["batch"]["intervention_sha256"])

    def test_existing_gate_accepts_fixture_custody_without_freezing_a_provider(self):
        plan = {"fixture_sha256": self.fixture_hash,
                "acceptance_checks": {row["id"]: gate.sha(row["acceptance_check"])
                                      for row in self.data["cases"]}}
        gate.verify_fixture(plan, panel.FIXTURE)

    def test_mutations_fail_closed(self):
        changes = []
        changed = copy.deepcopy(self.data)
        changed["cases"][0]["oracle"] = ["hidden"]
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][0]["actor_task"] = "short"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"].pop()
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][9]["setup"]["transitions"][0]["target"] = "missing_event"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][8]["setup"]["transitions"].reverse()
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][7]["setup"]["events"][0]["idempotency_key"] = "different"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][21]["setup"]["events"].append(
            {"id": "fake_handoff", "kind": "handoff", "recipient": "worker", "body": "bad"})
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][0] = []
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["sources"]["conflict_48"]["conflicts_with"] = []
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["sources"]["conflict_24"]["authority"]["mandatory"] = False
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["sources"]["trail_atlas"]["scope"]["repo"] = "fixture:trail"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["sources"]["handoff_state"]["kind"] = "handoff"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][1]["setup"]["events"][1]["causation"] = "missing_request"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["arm_interventions"]["routine_burst_urgent_request"]["baseline"] = {
            "routine_notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 30}}
        changes.append(changed)
        for candidate in changes:
            with self.subTest(candidate=changes.index(candidate)), self.assertRaises(ValueError):
                panel.validate(candidate)

    def test_duplicate_json_keys_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "duplicate.json"
            fixture.write_text('{"schema":"a","schema":"b"}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                panel.read_fixture(fixture)

    def test_export_is_new_owner_only_and_actor_has_no_oracle(self):
        with tempfile.TemporaryDirectory() as temp:
            private = Path(temp) / "private"
            private.mkdir(mode=0o700)
            original = panel.PRIVATE_PARENT
            panel.PRIVATE_PARENT = private
            try:
                output = private / "panel-a"
                panel.export(self.data, self.fixture_hash, output)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((output / "actor").stat().st_mode), 0o700)
                actor_files = sorted((output / "actor").glob("*.json"))
                self.assertEqual(len(actor_files), 22)
                for path in actor_files:
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    actor = json.loads(path.read_text())
                    self.assertEqual(set(actor), {"schema", "case", "task"})
                    self.assertNotIn("oracle", path.read_text().lower())
                    self.assertNotIn("acceptance", path.read_text().lower())
                controller = output / "controller.json"
                self.assertEqual(stat.S_IMODE(controller.stat().st_mode), 0o600)
                self.assertIn("acceptance_check", controller.read_text())
                with self.assertRaises(FileExistsError):
                    panel.export(self.data, self.fixture_hash, output)
                with self.assertRaises(ValueError):
                    panel.export(self.data, self.fixture_hash, Path(temp) / "outside")
                os.chmod(private, 0o755)
                with self.assertRaisesRegex(ValueError, "owner-only"):
                    panel.export(self.data, self.fixture_hash, private / "panel-b")
            finally:
                panel.PRIVATE_PARENT = original


if __name__ == "__main__":
    unittest.main()
