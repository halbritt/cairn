"""Focused structural checks for the provider-free full inbox fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("check_codex_full_inbox_native.py")
SPEC = importlib.util.spec_from_file_location("codex_full_inbox_fixture", SCRIPT)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
META_SPEC = importlib.util.spec_from_file_location(
    "codex_hook_metadata", Path(__file__).with_name("record_codex_hook_metadata.py"))
hook_metadata = importlib.util.module_from_spec(META_SPEC)
META_SPEC.loader.exec_module(hook_metadata)


class FullInboxFixtureTests(unittest.TestCase):
    def test_actor_export_contains_task_and_notice_but_not_grade(self):
        data = {"cases": [{"id": "single_notice",
            "actor_task": "Process the single routine build notice for Cairn and record its exact disposition.",
            "setup": {"events": [{"id": "notice_a", "kind": "notice",
                                   "body": "Build 81 completed; artifact checksum is 7b1a."}]},
            "acceptance_check": {"must": ["HIDDEN_GRADE_SENTINEL"],
                                 "must_not": ["HIDDEN_NEGATIVE_SENTINEL"]}}]}
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(fixture, "FIXTURE", fixture_path):
                task, body = fixture._fixture_task_and_notice()
        self.assertEqual(task, "Process the single routine build notice for Cairn and record its exact disposition.")
        self.assertEqual(body, "Build 81 completed; artifact checksum is 7b1a.")
        self.assertNotIn("acceptance_check", task + body)
        self.assertNotIn("must_not", task + body)
        self.assertNotIn("HIDDEN_GRADE_SENTINEL", task + body)
        self.assertNotIn("HIDDEN_NEGATIVE_SENTINEL", task + body)

    def test_batch_actor_exports_four_synthetic_notice_sources_only(self):
        task, bodies = fixture._fixture_task_and_notice("batch")
        self.assertEqual(bodies, fixture.BATCH_BODIES)
        self.assertEqual(len(bodies), 4)
        self.assertIn("first three", task)
        self.assertIn("batch limit", task)
        self.assertNotIn("acceptance_check", task)

    def test_batch_actor_requires_trace_before_native_codex_setup(self):
        with self.assertRaisesRegex(ValueError, "private diagnostic"):
            fixture.check("/bin/false", "/tmp/not-created", {}, lambda *_args, **_kwargs: None,
                          scenario="batch")

    def test_fixture_exposes_only_actor_visible_fields(self):
        # The stream is line-oriented SSE; inspect its final response.completed record.
        records = [line[6:] for line in fixture.response_stream("resp-fixture", dict(
            type="message", id="msg-fixture", role="assistant", status="completed",
            content=[dict(type="output_text", text="Local scripted response", annotations=[])]))
            .decode().splitlines() if line.startswith("data: ")]
        completed = next(json.loads(row) for row in records if json.loads(row)["type"] == "response.completed")
        self.assertEqual(completed["response"]["usage"],
                         {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        self.assertEqual(completed["response"]["output"][0]["content"][0]["text"],
                         "Local scripted response")

    def test_fixture_refuses_direct_codex_without_bwrap_auth_fixture(self):
        with self.assertRaisesRegex(RuntimeError, "explicit Node Codex entry"):
            fixture.check("/bin/false", "/tmp/not-created", {}, lambda *_args, **_kwargs: None)

    def test_hook_error_diagnostic_is_allowlisted_and_discards_raw_text(self):
        tags = hook_metadata.safe_stderr_tags(
            "Cairn coordination: FileNotFoundError; /secret/path/token UUID aaaa "
            "INVALID_HOST: do not retain this detail")
        self.assertEqual(tags, {"stderr_code": "INVALID_HOST", "stderr_class": "FileNotFoundError"})
        self.assertNotIn("secret", json.dumps(tags))
        self.assertNotIn("aaaa", json.dumps(tags))


if __name__ == "__main__":
    unittest.main()
