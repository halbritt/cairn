import json
import os
import tempfile
import unittest
from pathlib import Path

from check_codex_compact_native import _context_for_event


EVENT = "11111111-1111-4111-8111-111111111111"


class ContextLookupTests(unittest.TestCase):
    def test_missing_context_writes_fixed_stage_diagnostic_without_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "unrelated.json").write_text(json.dumps({"event_id": "other"}))
            diagnostic = root / "diagnostics" / "stage.json"
            with self.assertRaisesRegex(LookupError, "fixture_context_missing:auto_context"):
                _context_for_event(inbox, EVENT, "auto_context", diagnostic)
            self.assertEqual(os.stat(diagnostic).st_mode & 0o777, 0o600)
            data = json.loads(diagnostic.read_text())
            self.assertEqual(data, dict(schema="cairn.codex-fixture-context-lookup/1",
                stage="auto_context", context_files=1, matching_contexts=0, result="missing"))
            self.assertNotIn(EVENT, diagnostic.read_text())

    def test_exact_unique_context_is_selected_and_stage_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            expected = inbox / "context.json"
            expected.write_text(json.dumps({"event_id": EVENT}))
            diagnostic = root / "stage.json"
            self.assertEqual(_context_for_event(inbox, EVENT, "notice_context", diagnostic), expected)
            self.assertEqual(json.loads(diagnostic.read_text())["result"], "matched")
            with self.assertRaisesRegex(ValueError, "invalid fixture context lookup stage"):
                _context_for_event(inbox, EVENT, "unbounded", diagnostic)

if __name__ == "__main__":
    unittest.main()
