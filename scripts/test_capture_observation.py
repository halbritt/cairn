"""Capture telemetry stays separate from note writes and the latest marker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py"
spec = importlib.util.spec_from_file_location("capture_observation_hook", SOURCE)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class CaptureObservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".git").mkdir()
        self.event = dict(hook_event_name="PreCompact", session_id="caed9473-b01a-41e7-95ce-c3c1f28d66b3",
                          cwd=str(self.root), messages=[dict(role="user", text="Keep the correction"),
                                                        dict(role="assistant", text="Recorded")])
        self.config = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared",
                           state_dir=str(self.root / "state"), capture_observations=True)
        self.reports = []
        self.model_result = dict(structured_output=dict(memories=[], workstream=None, checkpoint=None),
                                 modelUsage={"claude-sonnet": {}},
                                 usage=dict(input_tokens=135, output_tokens=20, cache_read_input_tokens=90),
                                 total_cost_usd=0.003)

    def transport(self, command, *, body, observation=None, **kwargs):
        if command[0] == "claude":
            if observation is not None:
                observation.update(elapsed_ms=12, outcome="completed")
            if isinstance(self.model_result, Exception):
                if observation is not None:
                    observation["outcome"] = "timeout" if hook.is_timeout(self.model_result) else "error"
                raise self.model_result
            return self.model_result
        self.assertEqual(command[-1], "capture-observation")
        self.reports.append(json.loads(body))
        return dict(observation_id="stored")

    def invoke(self, event=None, config=None):
        with patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "run_json", side_effect=self.transport):
            return hook.handle(config or self.config, event or self.event)

    def test_selected_usage_and_unchanged_skip_are_distinct_history(self):
        self.invoke()
        self.invoke()
        self.assertEqual([r["outcome"] for r in self.reports], ["nothing_selected", "unchanged"])
        first, skip = self.reports
        self.assertEqual(first["selector_outcome"], "completed")
        self.assertEqual((first["input_tokens"], first["cache_read_input_tokens"], first["cost_usd"]),
                         (135, 90, 0.003))
        self.assertEqual(first["reported_model"], "claude-sonnet")
        self.assertEqual(first["session_sha256"], skip["session_sha256"])
        self.assertEqual(first["snapshot_sha256"], skip["snapshot_sha256"])
        self.assertNotIn("input_tokens", skip)
        self.assertNotIn("selector_outcome", skip)
        self.assertNotIn("messages", json.dumps(self.reports))

    def test_timeout_and_provider_error_are_not_saved(self):
        for problem, expected in ((hook.HookError("timed out", code="TIMEOUT"), "timeout"),
                                  (dict(is_error=True), "error")):
            with self.subTest(expected=expected):
                self.model_result = problem
                with self.assertRaises(hook.HookError):
                    self.invoke()
                self.assertEqual(self.reports[-1]["outcome"], expected)
                self.assertEqual(self.reports[-1]["selector_outcome"], expected)
                self.assertNotIn("input_tokens", self.reports[-1])
                state = self.root / "state" / (self.event["session_id"] + ".json")
                self.assertFalse(state.exists() and "captured_digest" in json.loads(state.read_text()))

    def test_state_commit_failure_is_distinct_from_selector_result(self):
        with patch.object(hook, "save_state", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertEqual(self.reports[-1]["outcome"], "state_commit_error")
        self.assertEqual(self.reports[-1]["selector_outcome"], "completed")

    def test_prelaunch_selector_validation_is_not_counted_as_model_call(self):
        with self.assertRaises(hook.HookError):
            self.invoke(config=dict(self.config, recall_model="bad model name"))
        self.assertEqual(self.reports[-1]["outcome"], "error")
        self.assertNotIn("selector_outcome", self.reports[-1])
        self.assertNotIn("input_tokens", self.reports[-1])

    def test_write_failure_and_reporting_failure_do_not_advance_or_undo_capture(self):
        self.model_result = dict(structured_output=dict(memories=[], workstream="Work", checkpoint="Keep it"))
        with patch.object(hook, "save_note", side_effect=hook.HookError("write failed")):
            with self.assertRaises(hook.HookError):
                self.invoke()
        self.assertEqual(self.reports[-1]["outcome"], "write_error")
        self.reports.clear()
        def fail_report(command, *, body, **kwargs):
            if command[0] == "claude":
                return self.model_result
            raise OSError("report failed")
        with patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "save_note", return_value="record"), \
             patch.object(hook, "run_json", side_effect=fail_report):
            hook.handle(self.config, self.event)
        state = json.loads((self.root / "state" / (self.event["session_id"] + ".json")).read_text())
        self.assertIn("captured_digest", state)
        self.assertEqual(state["last_capture"]["outcome"], "saved")

    def test_courtesy_skip_and_opt_out(self):
        courtesy = dict(self.event, messages=[dict(role="user", text="Thanks"),
                                             dict(role="assistant", text="You're welcome")])
        self.invoke(courtesy)
        self.assertEqual(self.reports[-1]["outcome"], "courtesy")
        self.reports.clear()
        self.invoke(dict(self.event, session_id="bbae9473-b01a-41e7-95ce-c3c1f28d66b3"),
                 dict(self.config, capture_observations=False))
        self.assertEqual(self.reports, [])


if __name__ == "__main__":
    unittest.main()
