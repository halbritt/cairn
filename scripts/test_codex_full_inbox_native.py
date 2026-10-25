"""Focused structural checks for the provider-free full inbox fixture."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
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
DIAG_SPEC = importlib.util.spec_from_file_location(
    "native_notice_batch_diagnostics", Path(__file__).with_name("native_notice_batch_diagnostics.py"))
diagnostics = importlib.util.module_from_spec(DIAG_SPEC)
DIAG_SPEC.loader.exec_module(diagnostics)


class FullInboxFixtureTests(unittest.TestCase):
    def test_registration_waits_for_matching_stop_hook_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "stop.json"
            def write_marker():
                time.sleep(0.05)
                marker.write_text(json.dumps(dict(event="Stop", turn_id="turn-1", exit_code=0)))
            writer = threading.Thread(target=write_marker)
            writer.start()
            result = fixture._wait_for_registration_hook(marker, "turn-1", timeout=1)
            writer.join()
        self.assertEqual(result["turn_id"], "turn-1")

    def test_registration_rejects_unrelated_or_failed_stop_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "stop.json"
            marker.write_text(json.dumps(dict(event="Stop", turn_id="other", exit_code=0)))
            with self.assertRaisesRegex(AssertionError, "does not match"):
                fixture._wait_for_registration_hook(marker, "turn-1", timeout=1)
            marker.write_text(json.dumps(dict(event="Stop", turn_id="turn-1", exit_code=1)))
            with self.assertRaisesRegex(AssertionError, "returned nonzero"):
                fixture._wait_for_registration_hook(marker, "turn-1", timeout=1)

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

    def test_batch_trace_precedes_codex_path_setup_and_classifies_missing_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "actor-root"
            private = base / "private"
            private.mkdir(mode=0o700)
            trace_path = private / "actor.jsonl"
            absent_codex = base / "secret" / "codex"
            with self.assertRaises(FileNotFoundError):
                fixture.check(str(absent_codex), root, {"cairn": "/missing/cairn"},
                    lambda *_args, **_kwargs: None, candidate_root=base,
                    codex_entry="/missing/entry.js", codex_root="/missing/package",
                    scenario="batch", diagnostic_output=trace_path)
            rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
            text = trace_path.read_text()
            trace_mode = trace_path.stat().st_mode & 0o777
        self.assertEqual(rows[0], {"operation": "setup_stage",
            "stage": "create_trace_directory", "phase": "complete"})
        self.assertEqual(rows[1], {"operation": "setup_stage",
            "stage": "check_entry", "phase": "start"})
        self.assertEqual(rows[-2], {"operation": "setup_stage",
            "stage": "validate_codex_paths", "phase": "start"})
        self.assertEqual(rows[-1], {"operation": "setup_failure",
            "stage": "validate_codex_paths", "exception_class": "FileNotFoundError",
            "filename_role": "selected_codex"})
        self.assertNotIn(str(absent_codex), text)
        self.assertEqual(trace_mode, 0o600)

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

    def test_staged_stop_wrapper_runs_without_repository_import_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / ".codex"
            profile.mkdir(mode=0o700)
            wrapper = fixture._stage_hook_metadata_wrapper(profile)
            metadata = profile / "hook-metadata.jsonl"
            stop_marker = profile / "stop-complete.json"
            payload = json.dumps({"hook_event_name": "Stop", "session_id": "synthetic",
                                  "turn_id": "turn", "cwd": temporary, "model": "fixture"})
            result = subprocess.run(
                [sys.executable, str(wrapper), "--metadata", str(metadata),
                 "--stop-complete", str(stop_marker), "--", "/usr/bin/true"],
                input=payload, text=True, capture_output=True, timeout=10,
                cwd=temporary, env={"PATH": "/usr/bin:/bin"})
            stages = [json.loads(line)["probe_stage"] for line in metadata.read_text().splitlines()
                      if "probe_stage" in json.loads(line)]
            marker_created = stop_marker.exists()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stages, ["wrapper_entry", "payload_valid", "command_start", "command_complete"])
        self.assertTrue(marker_created)

    def test_hook_error_diagnostic_is_allowlisted_and_discards_raw_text(self):
        tags = hook_metadata.safe_stderr_tags(
            "Cairn coordination: FileNotFoundError; /secret/path/token UUID aaaa "
            "INVALID_HOST: do not retain this detail")
        self.assertEqual(tags, {"stderr_code": "INVALID_HOST", "stderr_class": "FileNotFoundError"})
        self.assertNotIn("secret", json.dumps(tags))
        self.assertNotIn("aaaa", json.dumps(tags))

    def test_stop_hook_probe_records_private_fixed_invocation_and_error_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            private = base / "private"
            disposable = base / "disposable"
            private.mkdir(mode=0o700)
            disposable.mkdir(mode=0o700)
            metadata = private / "hook-metadata.jsonl"
            trace_path = private / "actor.jsonl"
            payload = json.dumps({"hook_event_name": "Stop", "turn_id": "private-turn",
                                  "token": "private-auth"})
            argv = ["record_codex_hook_metadata.py", "--metadata", str(metadata),
                    "--stop-complete", str(private / "stop.json"), "--", "/bin/true"]
            with patch.object(hook_metadata.sys, "argv", argv), \
                    patch("sys.stdin", io.StringIO(payload)), \
                    patch.object(hook_metadata.subprocess, "run",
                                 side_effect=subprocess.TimeoutExpired("/bin/true", 14)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    hook_metadata.main()
            rows = [json.loads(line) for line in metadata.read_text().splitlines()]
            writer = diagnostics.AdvanceDiagnosticWriter(trace_path, disposable)
            writer.record_hook_metadata(rows)
            writer.close()
            trace = trace_path.read_text()
            trace_mode = trace_path.stat().st_mode & 0o777
        probe_rows = [row for row in map(json.loads, trace.splitlines())
                      if row["operation"] == "hook_probe"]
        self.assertEqual([row["stage"] for row in probe_rows],
                         ["wrapper_entry", "payload_valid", "command_start", "command_timeout"])
        self.assertIn('"stage": "wrapper_entry"', trace)
        self.assertIn('"stage": "command_timeout"', trace)
        self.assertIn('"event_class": "Stop"', trace)
        self.assertIn('"error_class": "TimeoutExpired"', trace)
        self.assertNotIn("turn_id", trace)
        self.assertNotIn("output", trace)
        self.assertEqual(trace_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
