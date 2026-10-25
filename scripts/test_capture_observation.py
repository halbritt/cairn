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
coordination_spec = importlib.util.spec_from_file_location(
    "capture_shadow_coordination", SOURCE.with_name("coordination.py"))
coordination = importlib.util.module_from_spec(coordination_spec)
coordination_spec.loader.exec_module(coordination)


def wake_message():
    return coordination.wake_message(dict(
        session=dict(agent_id="11111111-1111-4111-8111-111111111111",
                     execution_id="22222222-2222-4222-8222-222222222222"),
        delivery_id="33333333-3333-4333-8333-333333333333"))


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
        self.model_calls = 0
        self.model_result = dict(structured_output=dict(memories=[], workstream=None, checkpoint=None),
                                 modelUsage={"claude-sonnet": {}},
                                 usage=dict(input_tokens=135, output_tokens=20, cache_read_input_tokens=90),
                                 total_cost_usd=0.003)

    def transport(self, command, *, body, observation=None, **kwargs):
        if command[0] == "claude":
            self.model_calls += 1
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
        self.assertEqual(skip["admission_candidate"], "confirmed_duplicate")
        self.assertNotIn("input_tokens", skip)
        self.assertNotIn("selector_outcome", skip)
        self.assertNotIn("messages", json.dumps(self.reports))

    def test_exact_transport_candidate_is_shadow_only_and_correction_still_saves(self):
        self.invoke()
        self.assertEqual(self.model_calls, 1)
        wake = dict(role="user", text=wake_message())
        self.assertTrue(hook.taskless_notification(wake["text"]))
        self.invoke(dict(self.event, messages=self.event["messages"] + [wake]))
        self.assertEqual(self.model_calls, 2, "shadow candidate suppressed the selector")
        self.assertEqual(self.reports[-1]["method"], "cairn-lifecycle/capture-meter/2")
        self.assertEqual(self.reports[-1]["admission_candidate"], "transport_only")
        correction = dict(role="user", text="Correction: keep the unfinished deployment handoff")
        self.model_result = dict(structured_output=dict(memories=[], workstream="Deployment",
                                                        checkpoint="Unfinished deployment; keep the correction"))
        with patch.object(hook, "save_note", return_value="record"):
            self.invoke(dict(self.event, messages=self.event["messages"] + [wake, correction]))
        self.assertEqual(self.model_calls, 3)
        self.assertEqual(self.reports[-1]["outcome"], "saved")
        self.assertNotIn("admission_candidate", self.reports[-1])

    def test_unconfirmed_snapshot_cannot_be_a_shadow_candidate(self):
        self.invoke()
        state = self.root / "state" / (self.event["session_id"] + ".json")
        previous = json.loads(state.read_text())
        previous["captured_digest"] = "0" * 64
        state.write_text(json.dumps(previous))
        wake = dict(role="user", text=wake_message())
        self.invoke(dict(self.event, messages=self.event["messages"] + [wake]))
        self.assertEqual(self.model_calls, 2)
        self.assertNotIn("admission_candidate", self.reports[-1])

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

    def test_selected_write_survives_state_commit_failure_and_retries_same_snapshot(self):
        self.model_result["structured_output"] = dict(
            memories=[dict(record_id=None, kind="decision", title="Endpoint",
                           body="Use the corrected endpoint for this migration.")],
            workstream=None, checkpoint=None)
        write_attempts = []
        durable_writes = {}

        def persist(_memory, operation, payload=None, **_kwargs):
            self.assertEqual(operation, "create")
            write_attempts.append(payload)
            request_id = payload["request_id"]
            durable_writes.setdefault(request_id, payload)
            return dict(record_id="saved-record")

        with patch.object(hook, "save_state", side_effect=OSError("disk unavailable")), \
             patch.object(hook.Memory, "call", new=persist):
            with self.assertRaises(OSError):
                self.invoke()

        self.assertEqual(self.reports[-1]["outcome"], "state_commit_error")
        self.assertEqual(self.reports[-1]["selector_outcome"], "completed")
        self.assertEqual(len(write_attempts), 1, "the selected note was not saved before state failure")
        state_path = self.root / "state" / (self.event["session_id"] + ".json")
        self.assertFalse(state_path.exists(), "failed commit left a captured marker behind")

        with patch.object(hook.Memory, "call", new=persist):
            self.invoke()

        self.assertEqual(len(write_attempts), 2, "retry did not repeat the unconfirmed snapshot write")
        self.assertEqual(write_attempts[0], write_attempts[1],
                         "retry changed the idempotent selected-write request")
        self.assertEqual(len(durable_writes), 1, "retry created a duplicate durable note")
        committed = json.loads(state_path.read_text())
        self.assertEqual(committed["last_capture"]["outcome"], "saved")
        self.assertIn("captured_digest", committed)

    def test_concurrent_state_change_is_not_reported_as_confirmed_capture(self):
        def concurrent_update(command, *, body, observation=None, **kwargs):
            if command[0] == "claude":
                state_path = self.root / "state" / (self.event["session_id"] + ".json")
                state_path.write_text(json.dumps(dict(last_capture=dict(outcome="concurrent"))))
                observation.update(elapsed_ms=12, outcome="completed")
                return self.model_result
            self.reports.append(json.loads(body))
            return dict(observation_id="stored")

        with patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "run_json", side_effect=concurrent_update):
            with self.assertRaisesRegex(hook.HookError, "state changed before commit"):
                hook.handle(self.config, self.event)
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

    def test_codex_missing_transcript_reports_explicit_unavailable_outcome(self):
        event = dict(self.event, hook_event_name="PreCompact", session_id="d2ca4711-4159-4d0c-95d5-c8cc174ad890",
                     transcript_path=None)
        event.pop("messages")
        config = dict(self.config, harness="codex")
        result = self.invoke(event, config)
        self.assertIn("Cairn capture is pending", result["systemMessage"])
        self.assertEqual(len(self.reports), 1)
        report = self.reports[0]
        self.assertEqual(report["outcome"], "transcript_unavailable")
        self.assertNotIn("snapshot_sha256", report)
        self.assertNotIn("selector_outcome", report)
        self.assertEqual(self.model_calls, 0)


if __name__ == "__main__":
    unittest.main()
