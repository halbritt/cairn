import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from check_codex_compact_native import _context_for_event
from codex_fixture_diagnostic import update as update_diagnostic
from record_codex_hook_metadata import owner_process_class, safe_output_tags


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
            self.assertEqual(data["context_lookup"], dict(stage="auto_context", context_files=1,
                matching_contexts=0, result="missing"))
            self.assertFalse(data["user_prompt_submit"]["entry_seen"])
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
            self.assertEqual(json.loads(diagnostic.read_text())["context_lookup"]["result"], "matched")
            with self.assertRaisesRegex(ValueError, "invalid fixture context lookup stage"):
                _context_for_event(inbox, EVENT, "unbounded", diagnostic)

    def test_hook_and_context_sections_merge_without_payload_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            diagnostic = root / "summary.json"
            update_diagnostic(diagnostic, "user_prompt_submit", dict(entry_seen=True,
                exit_seen=False, owner_process_class="configured_name_match"),
                increment=("observation_count",))
            update_diagnostic(diagnostic, "user_prompt_submit", dict(exit_seen=True,
                exit_code=1, stderr_code="NATIVE_TURN_MISMATCH", output_status=None,
                output_code=None))
            update_diagnostic(diagnostic, "context_lookup", dict(stage="notice_context",
                context_files=0, matching_contexts=0, result="missing"))
            self.assertEqual(os.stat(diagnostic).st_mode & 0o777, 0o600)
            data = json.loads(diagnostic.read_text())
            self.assertEqual(data["user_prompt_submit"], dict(entry_seen=True, exit_seen=True,
                observation_count=1,
                owner_process_class="configured_name_match", exit_code=1,
                stderr_code="NATIVE_TURN_MISMATCH", stderr_class=None,
                output_status=None, output_code=None))
            self.assertEqual(set(data), {"schema", "user_prompt_submit", "context_lookup"})

    def test_each_user_prompt_entry_replaces_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = Path(directory) / "summary.json"
            update_diagnostic(diagnostic, "user_prompt_submit", dict(entry_seen=True,
                exit_seen=False, exit_code=None, stderr_code=None, stderr_class=None,
                output_status=None, output_code=None, owner_process_class="none"),
                increment=("observation_count",))
            update_diagnostic(diagnostic, "user_prompt_submit", dict(entry_seen=True,
                exit_seen=False, exit_code=None, stderr_code=None, stderr_class=None,
                output_status=None, output_code=None, owner_process_class="configured_name_match"),
                increment=("observation_count",))
            data = json.loads(diagnostic.read_text())["user_prompt_submit"]
            self.assertEqual(data["observation_count"], 2)
            self.assertFalse(data["exit_seen"])
            self.assertIsNone(data["exit_code"])
            self.assertEqual(data["owner_process_class"], "configured_name_match")

    def test_hook_sanitizers_keep_only_allowlisted_tags(self):
        self.assertEqual(safe_output_tags('{"status":"refused","code":"INVALID_HOST"}'),
                         dict(output_status="refused", output_code="INVALID_HOST"))
        self.assertEqual(safe_output_tags('{"message":"private prompt text"}'),
                         dict(output_status=None, output_code=None))
        self.assertEqual(owner_process_class([], {"host_pid": 123}), "not_applicable")

    def test_owner_probe_reuses_configured_name_lookup_without_retaining_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
            config_path = root / "binding.json"
            parent_name = Path(f"/proc/{os.getppid()}/comm").read_text().strip()
            config_path.write_text(json.dumps(dict(harness="codex", process_names=[parent_name])))
            command = [sys.executable, str(script), "hook", "--config", str(config_path)]
            self.assertEqual(owner_process_class(command, {}), "configured_name_match")
            config_path.write_text(json.dumps(dict(harness="codex", process_names=[])))
            self.assertEqual(owner_process_class(command, {}), "none")

    def test_diagnostic_rejects_unbounded_or_non_allowlisted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = Path(directory) / "summary.json"
            with self.assertRaisesRegex(ValueError, "invalid owner process classification"):
                update_diagnostic(diagnostic, "user_prompt_submit", dict(owner_process_class="/proc/123"))
            with self.assertRaisesRegex(ValueError, "invalid context diagnostic count"):
                update_diagnostic(diagnostic, "context_lookup", dict(stage="notice_context",
                    context_files=1001, matching_contexts=0, result="missing"))

if __name__ == "__main__":
    unittest.main()
