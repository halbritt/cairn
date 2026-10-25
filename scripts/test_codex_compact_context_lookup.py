import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from check_codex_compact_native import _context_for_event, compact_sessionstart_turn_id_matches
from codex_fixture_diagnostic import append_ready_observation, turn_join_observation, update as update_diagnostic
from record_codex_hook_metadata import owner_process_class, ready_window_observation, safe_output_tags


EVENT = "11111111-1111-4111-8111-111111111111"


class ContextLookupTests(unittest.TestCase):
    def test_patched_compact_sessionstart_must_match_the_active_turn(self):
        rows = [dict(event="SessionStart", source="startup", phase="exit", turn_id="other"),
                dict(event="SessionStart", source="compact", phase="exit", turn_id="active")]
        self.assertTrue(compact_sessionstart_turn_id_matches(rows, "active"))
        self.assertFalse(compact_sessionstart_turn_id_matches(rows, "other"))
        self.assertFalse(compact_sessionstart_turn_id_matches(rows[:1], "active"))

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
            update_diagnostic(diagnostic, "post_hook_admission", dict(state_read="observed",
                agent_registered=True, inbox_intent=False, active_attempt=False, delivery_latch=False,
                pending_query="observed", eligible_pending=True))
            self.assertEqual(data["context_lookup"]["result"], "missing")
            self.assertEqual(set(data), {"schema", "user_prompt_submit", "context_lookup",
                "post_hook_admission", "turn_join"})

    def test_admission_observation_requires_fixed_flags_and_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = Path(directory) / "summary.json"
            with self.assertRaisesRegex(ValueError, "invalid admission observation flag"):
                update_diagnostic(diagnostic, "post_hook_admission", dict(
                    state_read="observed", agent_registered=True, inbox_intent=False,
                    active_attempt=False, delivery_latch=False, pending_query="observed",
                    eligible_pending="delivery-id"))
            with self.assertRaisesRegex(ValueError, "invalid admission observation status"):
                update_diagnostic(diagnostic, "post_hook_admission", dict(
                    state_read="observed", pending_query="not-known"))

    def test_ready_window_keeps_only_exact_comparison_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = "native-session-fixture"
            config = dict(binding="fixture-binding", state_dir=str(root / "state"),
                cairn="/fixture/cairn", socket="/fixture/api.sock", token_file="/fixture/token")
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            event_key = hashlib.sha256((config["binding"] + "\0" + native).encode()).hexdigest()
            state_path = Path(config["state_dir"]) / (event_key + ".json")
            state_path.parent.mkdir()
            state_path.write_text(json.dumps(dict(agent=dict(agent_id="agent-fixture",
                execution_id="execution-fixture"))))
            diagnostic = root / "summary.json"
            expected_path = diagnostic.with_suffix(".ready-window.expected.json")
            expected_path.write_text(json.dumps(dict(scenario="notice", native_session_id=native,
                agent_id="agent-fixture", execution_id="execution-fixture",
                event_id="expected-event", delivery_id="expected-delivery")))
            command = [sys.executable, "coordination.py", "--config", str(config_path)]
            event = dict(session_id=native)
            response = subprocess.CompletedProcess([], 0, json.dumps(dict(status="OK", data=dict(
                event_id="expected-event", delivery_id="expected-delivery"))), "")
            with mock.patch("record_codex_hook_metadata.subprocess.run", return_value=response):
                values = ready_window_observation(command, event, diagnostic)
            self.assertEqual(values, dict(_scenario="notice", response_class="ok", ready_delivery_present=True,
                event_delivery_match=True, session_identity_match=True))
            values.pop("_scenario")
            rows_path = root / "fixture-ready-window.jsonl"
            append_ready_observation(rows_path, "notice", "before", values)
            append_ready_observation(rows_path, "notice", "after", dict(response_class="ok",
                ready_delivery_present=False, event_delivery_match=False, session_identity_match=True))
            rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
            self.assertEqual([row["sequence"] for row in rows], [1, 2])
            self.assertEqual([row["phase"] for row in rows], ["before", "after"])
            self.assertTrue(all(row["scenario"] == "notice" for row in rows))
            self.assertEqual(os.stat(rows_path).st_mode & 0o777, 0o600)
            text = rows_path.read_text()
            self.assertNotIn("expected-event", text)
            self.assertNotIn("expected-delivery", text)

    def test_ready_window_missing_delivery_is_not_an_exact_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = "native-session-fixture"
            config = dict(binding="fixture-binding", state_dir=str(root / "state"),
                cairn="/fixture/cairn", socket="/fixture/api.sock", token_file="/fixture/token")
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            event_key = hashlib.sha256((config["binding"] + "\0" + native).encode()).hexdigest()
            state_path = Path(config["state_dir"]) / (event_key + ".json")
            state_path.parent.mkdir()
            state_path.write_text(json.dumps(dict(agent=dict(agent_id="agent-fixture",
                execution_id="execution-fixture"))))
            diagnostic = root / "summary.json"
            diagnostic.with_suffix(".ready-window.expected.json").write_text(json.dumps(dict(
                scenario="notice",
                native_session_id=native, agent_id="agent-fixture", execution_id="execution-fixture",
                event_id="expected-event", delivery_id="expected-delivery")))
            command = [sys.executable, "coordination.py", "--config", str(config_path)]
            response = subprocess.CompletedProcess([], 0, json.dumps(dict(status="OK", data={})), "")
            with mock.patch("record_codex_hook_metadata.subprocess.run", return_value=response):
                values = ready_window_observation(command, dict(session_id=native), diagnostic)
            self.assertEqual(values, dict(_scenario="notice", response_class="ok", ready_delivery_present=False,
                event_delivery_match=False, session_identity_match=True))
            values.pop("_scenario")
            with self.assertRaisesRegex(ValueError, "invalid ready observation flag"):
                append_ready_observation(root / "fixture-ready-window.jsonl", "notice", "before",
                    dict(response_class="ok", ready_delivery_present="private-id"))

    def test_auto_compact_turn_join_retains_only_fixed_presence_and_equality(self):
        fixture = dict(attempt_native_turn_id="private-original-turn",
            observed_hook_events=[dict(event="SessionStart", source="compact", phase="exit", turn_id=None)],
            compact_sessionstart_outcomes=dict(coordination=dict(error_code="NATIVE_TURN_MISMATCH")))
        values = turn_join_observation(fixture)
        self.assertEqual(values, dict(attempt_turn_present=True,
            compact_sessionstart_turn_present=False, turn_equal=False,
            coordination_error_code="NATIVE_TURN_MISMATCH", source_gate="owner_turn_guard"))
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = Path(directory) / "summary.json"
            update_diagnostic(diagnostic, "turn_join", values)
            self.assertEqual(os.stat(diagnostic).st_mode & 0o777, 0o600)
            self.assertNotIn("private-original-turn", diagnostic.read_text())

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
