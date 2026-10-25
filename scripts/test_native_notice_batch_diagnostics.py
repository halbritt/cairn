import json
import os
from pathlib import Path
import tempfile
import unittest

from native_notice_batch_diagnostics import (
    AdvanceDiagnosticWriter,
    advance_tool_result_stage,
    classify_advance_tool_result,
)


class AdvanceDiagnosticWriterTests(unittest.TestCase):
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
