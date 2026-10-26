import json
import os
from pathlib import Path
import tempfile
import unittest

from native_notice_batch_diagnostics import (
    AdvanceDiagnosticWriter,
    advance_tool_result_stage,
    classify_advance_tool_result,
    classify_setup_filename,
    fixture_api_argv,
)


class AdvanceDiagnosticWriterTests(unittest.TestCase):
    def test_setup_trace_records_allowlisted_stages_and_filename_role_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable, private = base / "disposable", base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            missing_codex = base / "secret-host-path" / "codex"
            path = private / "setup.jsonl"
            writer = AdvanceDiagnosticWriter(path, disposable)
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            writer.record_setup_stage("validate_codex_paths", "start")
            writer.record_setup_failure("FileNotFoundError",
                classify_setup_filename(missing_codex, {"selected_codex": missing_codex}))
            writer.close()

            text = path.read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(rows, [
                {"operation": "setup_stage", "stage": "validate_codex_paths", "phase": "start"},
                {"operation": "setup_failure", "stage": "validate_codex_paths",
                 "exception_class": "FileNotFoundError", "filename_role": "selected_codex"},
            ])
            self.assertNotIn(str(missing_codex), text)
            self.assertEqual(classify_setup_filename("/unmapped/private/path", {}), "unknown")

    def test_fixture_api_argv_matches_integration_harness_order(self):
        self.assertEqual(fixture_api_argv("cairn", "/api.sock", "/carol.token",
            "agents", "list", "--harness", "codex"),
            ["cairn", "agents", "list", "--socket", "/api.sock",
             "--token-file", "/carol.token", "--harness", "codex"])
        self.assertEqual(fixture_api_argv("cairn", "/api.sock", "/carol.token",
            "version", raw=True),
            ["cairn", "agent", "--socket", "/api.sock", "--token-file",
             "/carol.token", "version"])

    def test_classifies_nested_advance_tool_success_terminal_and_error_shapes(self):
        def wrapped(response, exit_code=0):
            execution = {"output": json.dumps(response), "exit_code": exit_code}
            return {"call_id": "synthetic", "output": json.dumps(execution)}

        self.assertEqual(classify_advance_tool_result(wrapped({
            "schema": "cairn.compact-inbox-batch/1", "ok": True,
            "next": "private successor cue", "batch_index": 2, "batch_limit": 3,
        })), "advance_successor")
        self.assertEqual(classify_advance_tool_result(wrapped({
            "schema": "cairn.compact-inbox-batch/1", "ok": True,
            "next": None, "remainder": "batch_limit",
        })), "advance_limit")
        self.assertEqual(classify_advance_tool_result(wrapped({
            "schema": "cairn.compact-inbox-batch/1", "ok": False,
            "status": "private error text",
        })), "tool_error")
        self.assertEqual(classify_advance_tool_result(wrapped({}, exit_code=1)), "tool_error")
        self.assertEqual(classify_advance_tool_result({"output": "not json"}), "other")
        native_exec = {"output": [{"type": "input_text", "text": (
            "Chunk ID: 1\nWall time: 0.01 seconds\nProcess exited with code 0\n"
            "Original token count: 25\nOutput:\n" + json.dumps({
                "schema": "cairn.compact-inbox-batch/1", "ok": True,
                "next": "private successor cue", "batch_index": 2, "batch_limit": 3,
            }))}]}
        self.assertEqual(advance_tool_result_stage(native_exec), "success")
        self.assertEqual(classify_advance_tool_result(native_exec), "advance_successor")

    def test_advance_result_stage_codes_are_fixed_and_payload_free(self):
        def wrapped(inner, exit_code=0):
            return {"output": json.dumps({"output": inner, "exit_code": exit_code})}

        response = json.dumps({"schema": "cairn.compact-inbox-batch/1", "ok": True,
                               "next": "private successor cue", "batch_index": 2,
                               "batch_limit": 3})
        self.assertEqual(advance_tool_result_stage({"is_error": True}), "is_error")
        self.assertEqual(advance_tool_result_stage({"output": "private text"}), "outer_json_invalid")
        self.assertEqual(advance_tool_result_stage(wrapped(response, exit_code=7)), "exit_nonzero")
        self.assertEqual(advance_tool_result_stage(wrapped("private text")), "inner_json_invalid")
        self.assertEqual(advance_tool_result_stage(wrapped(json.dumps({"schema": "other", "ok": True}))),
                         "schema_or_ok_invalid")
        self.assertEqual(advance_tool_result_stage(wrapped(response)), "success")

    def test_retains_only_sanitized_classes_before_caller_assertions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable = base / "disposable"
            private = base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            path = private / "advance.jsonl"
            writer = AdvanceDiagnosticWriter(path, disposable)
            writer.record_advance({
                "schema": "cairn.compact-inbox-batch/1", "ok": True,
                "next": "secret cue with private source and handle", "batch_index": 2,
                "batch_limit": 3, "private source": "must not be retained",
            }, 0)
            writer.record_advance({
                "schema": "cairn.compact-inbox-batch/1", "ok": True,
                "next": None, "remainder": "batch_limit", "status": "private source text",
                "batch_index": 999999999,
            }, 0)
            writer.close()

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["next_type"] for row in rows], ["string", "null"])
            self.assertEqual(rows[0]["batch_index"], 2)
            self.assertEqual(rows[0]["batch_limit"], 3)
            self.assertEqual(rows[1]["remainder"], "batch_limit")
            self.assertEqual(rows[1]["status"], "other")
            self.assertIsNone(rows[1]["batch_index"])
            self.assertNotIn("private source", path.read_text())
            self.assertNotIn("next", rows[0])
            self.assertNotIn("secret cue", path.read_text())
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_retains_actor_response_and_tool_result_classes_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable = base / "disposable"
            private = base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            path = private / "actor.jsonl"
            writer = AdvanceDiagnosticWriter(path, disposable)
            writer.record_actor_exchange("read_notice", ["read_success", "private source text"],
                                         stage="read_tool_result")
            writer.record_actor_exchange("unexpected", ["tool_error"],
                                         stage="advance_tool_result")
            writer.record_advance_result_stage("inner_json_invalid")
            writer.record_actor_exchange("secret response", ["secret tool payload"],
                                         stage="private stage")
            writer.close()

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[0], {"operation": "actor_exchange",
                "response_class": "read_notice",
                "tool_result_classes": ["other", "read_success"],
                "stage": "read_tool_result"})
            self.assertEqual(rows[1], {"operation": "actor_exchange",
                "response_class": "unexpected", "tool_result_classes": ["tool_error"],
                "stage": "advance_tool_result"})
            self.assertEqual(rows[2], {"operation": "advance_result_stage",
                "stage_code": "inner_json_invalid"})
            self.assertEqual(rows[3], {"operation": "actor_exchange",
                "response_class": "unexpected", "tool_result_classes": ["other"],
                "stage": "unknown"})
            self.assertNotIn("secret", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_retains_hook_store_and_terminal_fixed_codes_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable = base / "disposable"
            private = base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            path = private / "actor.jsonl"
            writer = AdvanceDiagnosticWriter(path, disposable)
            writer.record_preflight("token_identity", "matched")
            writer.record_preflight("api_socket", "available")
            writer.record_preflight("api_version", "authenticated")
            writer.record_fixture_failure("StopIteration")
            writer.record_hook_metadata([
                {"event": "UserPromptSubmit", "turn_id": "secret-turn",
                 "source": "secret-source", "keys": ["token"],
                 "exit_code": 0, "stderr_code": "INVALID_HOST",
                 "stderr_class": "FileNotFoundError"},
                {"event": "UserPromptSubmit", "turn_id": "secret-turn"},
                {"event": "not-an-event", "exit_code": 19,
                 "stderr_code": "SECRET", "stderr_class": "SecretClass"},
            ])
            writer.record_store_slot(1, status_class="available", observed=True, pending=False, leased=True,
                                     handled=False, attempts_zero=False, attempted=True)
            writer.record_store_slot(4, status_class="not_created", observed=False)
            writer.record_actor_terminal("not_observed")
            writer.close()

            text = path.read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(rows[0], {"operation": "preflight", "check": "token_identity",
                                      "result": "matched"})
            self.assertEqual(rows[1], {"operation": "preflight", "check": "api_socket",
                                      "result": "available"})
            self.assertEqual(rows[2], {"operation": "preflight", "check": "api_version",
                                      "result": "authenticated"})
            self.assertEqual(rows[3], {"operation": "fixture_failure",
                                      "exception_class": "StopIteration"})
            self.assertEqual(rows[4], {"operation": "hook", "event_class": "UserPromptSubmit",
                "phase": "exit", "exit_class": "zero", "stderr_code": "INVALID_HOST",
                "stderr_class": "FileNotFoundError"})
            self.assertEqual(rows[5]["phase"], "entry")
            self.assertEqual(rows[6]["event_class"], "other")
            self.assertEqual(rows[6]["stderr_code"], "none")
            self.assertEqual(rows[7], {"operation": "store_slot", "slot": 1,
                "status_class": "available",
                "observed": True, "pending": False, "leased": True, "handled": False,
                "attempts_zero": False, "attempted": True})
            self.assertEqual(rows[8]["observed"], False)
            self.assertEqual(rows[8]["status_class"], "not_created")
            self.assertIsNone(rows[8]["pending"])
            self.assertEqual(rows[9], {"operation": "actor_terminal",
                                      "terminal_class": "not_observed"})
            for forbidden in ("secret-turn", "secret-source", "secret-token-value", "SecretClass"):
                self.assertNotIn(forbidden, text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failing_advance_class_keeps_stage_row_before_assertion(self):
        def wrapped(response):
            return {"output": json.dumps({"output": json.dumps(response), "exit_code": 0})}

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable, private = base / "disposable", base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            path = private / "advance.jsonl"
            writer = AdvanceDiagnosticWriter(path, disposable)
            result = wrapped({"schema": "wrong", "ok": True, "next": "private"})
            try:
                result_stage = advance_tool_result_stage(result)
                writer.record_advance_result_stage(result_stage)
                with self.assertRaisesRegex(AssertionError, "advance response was not a successor"):
                    if classify_advance_tool_result(result) != "advance_successor":
                        raise AssertionError("advance response was not a successor")
            finally:
                writer.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows, [{"operation": "advance_result_stage",
                                     "stage_code": "schema_or_ok_invalid"}])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private", path.read_text())

    def test_requires_private_absolute_path_outside_disposable_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            disposable = base / "disposable"
            private = base / "private"
            disposable.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "absolute"):
                AdvanceDiagnosticWriter(Path("relative.jsonl"), disposable)
            with self.assertRaisesRegex(ValueError, "outside"):
                AdvanceDiagnosticWriter(disposable / "advance.jsonl", disposable)

            broad = base / "broad"
            broad.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                AdvanceDiagnosticWriter(broad / "advance.jsonl", disposable)

            existing = private / "existing.jsonl"
            existing.write_text("occupied")
            with self.assertRaises(FileExistsError):
                AdvanceDiagnosticWriter(existing, disposable)


if __name__ == "__main__":
    unittest.main()
