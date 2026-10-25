import json
import os
from pathlib import Path
import tempfile
import unittest

from native_notice_batch_diagnostics import AdvanceDiagnosticWriter


class AdvanceDiagnosticWriterTests(unittest.TestCase):
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
