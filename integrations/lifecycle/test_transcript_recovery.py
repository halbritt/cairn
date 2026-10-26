#!/usr/bin/env python3
"""Focused unit tests for integrations.lifecycle.transcript_recovery."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TranscriptRecoveryTests(unittest.TestCase):
    def test_native_locator_and_read_native_record(self):
        tr = _load("transcript_recovery")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            line = (json.dumps({"type": "event_msg", "ordinal": 0}) + "\n").encode("utf-8")
            path.write_bytes(line)
            digest = hashlib.sha256(line).hexdigest()
            locator = tr.native_locator(f"0:{len(line)}:{digest}")
            self.assertEqual(locator["offset"], 0)
            self.assertEqual(locator["length"], len(line))
            record = tr.read_native_record(path, locator)
            self.assertEqual(record["type"], "event_msg")
            with self.assertRaises(tr.TranscriptRecoveryError):
                tr.native_locator("invalid")

    def test_strict_native_line_rejects_duplicate_keys(self):
        tr = _load("transcript_recovery")
        with self.assertRaises(tr.TranscriptRecoveryError):
            tr.strict_native_line(b'{"a":1,"a":2}\n')

    def test_native_time_parses_utc_millis(self):
        tr = _load("transcript_recovery")
        dt = tr.native_time("2026-10-26T12:00:00.123Z")
        self.assertEqual(dt.year, 2026)
        with self.assertRaises(tr.TranscriptRecoveryError):
            tr.native_time("2026-10-26T12:00:00Z")

    def test_last_jsonl_record(self):
        tr = _load("transcript_recovery")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.jsonl"
            path.write_text('{"step": 1}\n{"step": 2, "status": "DONE"}\n')
            self.assertEqual(tr.last_jsonl_record(path), {"step": 2, "status": "DONE"})

    def test_verify_turn_recovery_dispatch_and_reexports(self):
        tr = _load("transcript_recovery")
        coord = _load("coordination")
        self.assertEqual(coord.CLAUDE_NON_TURN_RECORDS, tr.CLAUDE_NON_TURN_RECORDS)
        self.assertEqual(coord.NATIVE_REFUSAL_MESSAGE, tr.NATIVE_REFUSAL_MESSAGE)
        self.assertIs(coord.CLAUDE_NON_TURN_RECORDS, coord._transcript_recovery.CLAUDE_NON_TURN_RECORDS)
        self.assertTrue(callable(coord.codex_turn_RECOVERY))
        self.assertTrue(callable(coord.claude_turn_RECOVERY))
        self.assertTrue(callable(coord.opencode_turn_RECOVERY))
        self.assertTrue(callable(coord.hermes_turn_RECOVERY))
        self.assertTrue(callable(coord.verify_turn_recovery))
        with self.assertRaises(coord.CoordinationError):
            coord.verify_turn_recovery("opencode", "refused_wake")
        with self.assertRaises(coord.CoordinationError):
            coord.verify_turn_recovery("hermes", "refused_wake")
        with self.assertRaises(coord.CoordinationError):
            coord.verify_turn_recovery("unknown", "refused_wake")


if __name__ == "__main__":
    unittest.main()
