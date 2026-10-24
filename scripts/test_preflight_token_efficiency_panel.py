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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight_token_efficiency_panel as panel
import token_efficiency_gate as gate
import evaluate_capture_admission as capture_eval


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
                                      for row in self.data["cases"]},
                "capture_selector": {"harness": "claude", "account": "fixture",
                                     "id": "fixture-capture-model", "route": "subscription",
                                     "effort_configuration": "unset"},
                "runs": [{"workflow_id": f"{arm}.unchanged_capture",
                          "case": "unchanged_capture", "arm": arm,
                          "stages": [{"stage_id": f"{arm}.seed", "role": "capture",
                                      "pin": "capture", "capture_phase": "seed"},
                                     {"stage_id": f"{arm}.repeat", "role": "capture",
                                      "pin": "capture", "capture_phase": "repeat"}]}
                         for arm in gate.ARMS]}
        gate.verify_fixture(plan, panel.FIXTURE)

    def test_unchanged_capture_seed_is_controller_only_and_bound_to_setup_hash(self):
        row = next(row for row in self.data["cases"] if row["id"] == "unchanged_capture")
        self.assertIn("identical snapshot", row["actor_task"])
        capture = row["setup"]["capture"]
        self.assertEqual(capture["harness_ref"], "plan.model.harness")
        self.assertEqual(capture["model_ref"], "plan.capture_selector.id")
        self.assertEqual([item["target"] for item in row["setup"]["transitions"]],
                         [capture["target"], capture["target"]])
        base = next(case for case in panel.manifest(self.data, self.fixture_hash)["cases"]
                    if case["id"] == row["id"])
        changed = copy.deepcopy(self.data)
        changed_row = next(item for item in changed["cases"] if item["id"] == row["id"])
        changed_row["setup"]["capture"]["seed_selection"]["memories"][0]["body"] += " Confirmed."
        panel.validate(changed)
        revised = next(case for case in panel.manifest(changed, "changed-fixture")["cases"]
                       if case["id"] == row["id"])
        self.assertEqual(base["actor_sha256"], revised["actor_sha256"])
        self.assertEqual(base["acceptance_check_sha256"], revised["acceptance_check_sha256"])
        self.assertNotEqual(base["setup_sha256"], revised["setup_sha256"])
        changed_task = copy.deepcopy(self.data)
        task_row = next(item for item in changed_task["cases"] if item["id"] == row["id"])
        task_row["actor_task"] += " Review the captured outcome."
        panel.validate(changed_task)
        revised_task = next(case for case in panel.manifest(changed_task, "changed-fixture")["cases"]
                            if case["id"] == row["id"])
        self.assertNotEqual(base["actor_sha256"], revised_task["actor_sha256"])
        self.assertEqual(base["setup_sha256"], revised_task["setup_sha256"])
        self.assertEqual(base["acceptance_check_sha256"], revised_task["acceptance_check_sha256"])

    def test_capture_binding_keeps_codex_hook_harness_and_claude_selector_model_separate(self):
        row = next(row for row in self.data["cases"] if row["id"] == "unchanged_capture")
        capture = row["setup"]["capture"]
        binding = panel.capture_binding(capture, {"model": {
            "harness": "codex", "id": "gpt-fixture"}, "capture_selector": {
            "harness": "claude", "id": "claude-fixture", "route": "subscription",
            "effort_configuration": "unset"}})
        self.assertEqual(binding, {"harness": "codex", "model": "claude-fixture"})

    def test_unchanged_capture_real_hook_confirms_seed_and_skips_identical_snapshot(self):
        # The hook makes both decisions; local selector/store responses supply
        # synthetic setup only. This does not run a provider or persist hook state.
        row = next(row for row in self.data["cases"] if row["id"] == "unchanged_capture")
        capture = row["setup"]["capture"]
        hook = capture_eval.hook
        # The local two-call hook uses Claude actor semantics. A Codex actor
        # binding is checked separately without inventing a Codex transcript.
        binding = panel.capture_binding(capture, {"model": {
            "harness": "claude", "id": "task-fixture-model"}, "capture_selector": {
            "harness": "claude", "id": "fixture-capture-model", "route": "subscription",
            "effort_configuration": "unset"}})

        class LocalMemory:
            def __init__(self):
                self.config = {**binding, "repo": "fixture:cairn-token-efficiency"}
                self.capture_observation = {}
                self.created = []

            def checkpoint(self, _title):
                return None

            def call(self, operation, *, payload):
                if operation != "create":
                    raise AssertionError(f"unexpected memory operation: {operation}")
                self.created.append(payload)
                return {"record_id": "synthetic-selected-record"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            event = {"hook_event_name": capture["hook_event"], "session_id": "synthetic-capture",
                     "cwd": str(root), "messages": copy.deepcopy(capture["dialogue"])}
            memory, state, selector_calls = LocalMemory(), {}, []

            def select(*_args, **_kwargs):
                selector_calls.append(1)
                return {"structured_output": copy.deepcopy(capture["seed_selection"])}

            with patch.object(hook, "handoff_candidates", return_value=[]), \
                 patch.object(hook, "durable_candidates", return_value=[]), \
                 patch.object(hook, "select_json", side_effect=select):
                first = hook.capture(memory, event, state)
                confirmed = state.get("captured_digest")
                self.assertEqual(selector_calls, [1])
                self.assertEqual(len(memory.created), 1)
                self.assertIn("synthetic-selected-record", first["systemMessage"])
                self.assertEqual(memory.created[0]["draft"]["kind"], "procedure")
                self.assertIn(capture["seed_selection"]["memories"][0]["body"],
                              memory.created[0]["draft"]["body"])
                self.assertEqual(state["last_capture"]["outcome"], "saved")
                self.assertEqual(state["last_capture"]["selector_calls"], 1)
                self.assertEqual(state["captured_messages"], len(capture["dialogue"]))
                self.assertIsInstance(confirmed, str)
                self.assertEqual(len(confirmed), 64)

                self.assertEqual(hook.capture(memory, event, state), {})
                self.assertEqual(selector_calls, [1], "identical snapshot reselected")
                self.assertEqual(len(memory.created), 1, "identical snapshot wrote again")
                self.assertEqual(state["captured_digest"], confirmed)
                self.assertEqual(state["last_capture"]["outcome"], "unchanged")
                self.assertEqual(state["last_capture"]["selector_calls"], 0)

    def test_lease_expiry_oracle_follows_native_hold_contract(self):
        case = next(row for row in self.data["cases"] if row["id"] == "lease_expiry")
        checks = case["acceptance_check"]
        self.assertIn("same current hold", case["actor_task"])
        self.assertIn("STALE_LEASE", " ".join(checks["must"]))
        self.assertIn("late=true", " ".join(checks["must"]))
        self.assertIn("HOLD_RELEASED", " ".join(checks["must_not"]))
        self.assertNotIn("expired claim cannot settle", str(checks))

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
        changed["cases"][19]["setup"].pop("capture")
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][19]["setup"]["transitions"][1]["target"] = "parser_review"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][19]["setup"]["capture"]["seed_selection"]["memories"] = []
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][19]["setup"]["capture"]["model_ref"] = "fixture-capture-model"
        changes.append(changed)
        changed = copy.deepcopy(self.data)
        changed["cases"][0]["setup"]["capture"] = copy.deepcopy(
            self.data["cases"][19]["setup"]["capture"])
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
                selected_body = self.data["cases"][19]["setup"]["capture"]["seed_selection"]["memories"][0]["body"]
                self.assertIn(selected_body, controller.read_text())
                self.assertNotIn(selected_body, (output / "actor/unchanged_capture.json").read_text())
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
