"""Behavior at the host/memory boundary, using no operational database or model."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hook = module("cairn_lifecycle", ROOT / "integrations/claude/lifecycle.py")
installer = module("install_claude_hooks", ROOT / "scripts/install-claude-hooks.py")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.transcript = self.root / "conversation.jsonl"
        self.event = dict(hook_event_name="PreCompact", session_id="caed9473-b01a-41e7-95ce-c3c1f28d66b3", cwd=str(self.root),
                          transcript_path=str(self.transcript))
        self.config = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared")

    def write_dialogue(self, messages):
        self.transcript.write_text("\n".join(json.dumps(m) for m in messages) + "\n")

    def test_excerpt_omits_tools_reasoning_and_sidechains_and_bounds_unicode(self):
        self.write_dialogue([
            dict(type="user", message=dict(content="Use PostgreSQL.")),
            dict(type="assistant", message=dict(content=[dict(type="thinking", thinking="PRIVATE"),
                dict(type="tool_use", input={"secret": "PRIVATE"}), dict(type="text", text="Decision recorded.")])),
            dict(type="user", message=dict(content=[dict(type="tool_result", content="PRIVATE")])),
            dict(type="assistant", isSidechain=True, message=dict(content="PRIVATE")),
            dict(type="assistant", message=dict(content="日" * 10000)),
        ])
        excerpt = hook.conversation(self.transcript)
        self.assertNotIn("PRIVATE", json.dumps(excerpt))
        self.assertLessEqual(len(hook.encoded(excerpt).encode()), hook.TEXT_BYTES)
        self.assertIn("truncated", excerpt[-1]["text"])

    def test_recall_injects_real_index_handles_without_connection_arguments(self):
        memory = hook.Memory(self.config, "session-one")
        result = dict(status="READY", destination=dict(name="hosted"), selected=[{"mandatory": True}],
                      index=[dict(record_id="record", version=1, summary="useful", pull_arguments={"handle": "exact"},
                                  pull_command="private paths")])
        with patch.object(memory, "call", side_effect=[result, {"selection": {"record": {"body": "useful body"}}}]) as call:
            output = hook.recall(memory, dict(self.event, hook_event_name="UserPromptSubmit", prompt="fix startup"))
        text = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"handle":"exact"', text)
        self.assertIn('"mandatory":true', text)
        self.assertNotIn("private paths", text)
        self.assertIn("fix startup", call.call_args_list[0].args[1][-1])

    def test_empty_search_is_normal_and_allows_first_checkpoint(self):
        memory = hook.Memory(self.config, "session-one")
        empty = dict(ok=True, data=dict(status="SCOPE_EMPTY", destination=dict(name="hosted"), index=[]))
        with patch.object(hook, "run_json", return_value=empty):
            self.assertIsNone(memory.checkpoint(hook.title_for(self.event)))
            output = hook.recall(memory, dict(self.event, hook_event_name="SessionStart"))
            self.assertIn('"index":[]', output["hookSpecificOutput"]["additionalContext"])

    def test_local_profile_and_oversize_mandatory_context_refused(self):
        memory = hook.Memory(self.config, "session-one")
        for result in [dict(status="READY", destination=dict(name="local")),
                       dict(status="READY", destination=dict(name="hosted"), selected=["x" * hook.CONTEXT_BYTES])]:
            with patch.object(memory, "call", return_value=result), self.assertRaises(hook.HookError):
                hook.recall(memory, dict(self.event, hook_event_name="SessionStart"))

    def test_resume_and_compaction_find_same_session_checkpoint(self):
        memory = hook.Memory(self.config, "session-one")
        with patch.object(memory, "search", return_value={}) as search:
            for source in ("resume", "compact"):
                hook.recall(memory, dict(self.event, hook_event_name="SessionStart", source=source))
                self.assertEqual(search.call_args.args[0], '"' + hook.title_for(self.event) + '"')

    def test_capture_updates_one_note_and_null_does_not_write(self):
        self.write_dialogue([dict(type="user", message=dict(content="Use PostgreSQL; tests are pending."))])
        memory = hook.Memory(self.config, "session-one")
        previous = dict(record_id="existing", version=4, body="Previous checkpoint")
        with patch.object(memory, "checkpoint", return_value=previous), patch.object(memory, "call", return_value={"record_id": "existing"}) as call:
            with patch.object(hook, "run_json", return_value={"structured_output": {"checkpoint": None}}):
                self.assertEqual(hook.capture(memory, self.event), {})
                call.assert_not_called()
            with patch.object(hook, "run_json", return_value={"structured_output": {"checkpoint": "Decision: PostgreSQL. Tests pending."}}) as model:
                hook.capture(memory, self.event)
                self.assertEqual(call.call_args.args[0], "revise")
                payload = call.call_args.kwargs["payload"]
                self.assertEqual(payload["expected_version"], 4)
                self.assertEqual(payload["record_id"], "existing")
                first_request = payload["request_id"]
                hook.capture(memory, self.event)
                self.assertEqual(call.call_args.kwargs["payload"]["request_id"], first_request)
                self.assertEqual(model.call_args.kwargs["env"]["CAIRN_LIFECYCLE_CHILD"], "1")
                self.assertIn('{"disableAllHooks":true}', model.call_args.args[0])

    def test_invalid_model_output_never_writes(self):
        self.write_dialogue([dict(type="user", message=dict(content="Useful decision"))])
        memory = hook.Memory(self.config, "session-one")
        for result in [{}, {"is_error": True}, {"structured_output": {"checkpoint": False}},
                       {"structured_output": {"checkpoint": "x" * (hook.NOTE_BYTES + 1)}}]:
            with patch.object(memory, "checkpoint", return_value=None), patch.object(memory, "call") as call, \
                 patch.object(hook, "run_json", return_value=result), self.assertRaises(hook.HookError):
                hook.capture(memory, self.event)
            call.assert_not_called()

    def test_disable_and_child_guard_do_not_read_transcript_or_memory(self):
        for name in ("CAIRN_LIFECYCLE_CHILD", "CAIRN_LIFECYCLE_DISABLED"):
            with patch.dict(os.environ, {name: "1"}), patch.object(hook, "Memory") as memory:
                self.assertEqual(hook.handle(self.config, self.event), {})
                memory.assert_not_called()
        (self.root / ".cairn-no-memory").touch()
        self.assertEqual(hook.handle(self.config, self.event), {})

    def test_project_checkpoint_and_opt_out_survive_subdirectory_work(self):
        (self.root / ".git").mkdir()
        subdir = self.root / "src"
        subdir.mkdir()
        nested = dict(self.event, cwd=str(subdir))
        self.assertEqual(hook.title_for(nested), hook.title_for(self.event))
        (self.root / ".cairn-no-memory").touch()
        self.assertEqual(hook.handle(self.config, nested), {})

    def test_search_timeout_is_labelled_and_does_not_inject_or_write(self):
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="task")
        with patch.object(hook.subprocess, "run", side_effect=subprocess.TimeoutExpired("cairn", 5)):
            with self.assertRaisesRegex(hook.HookError, "timed out"):
                hook.handle(self.config, event)

    def test_same_session_capture_cannot_race(self):
        state = self.root / "state"
        state.mkdir()
        config = dict(self.config, state_dir=str(state))
        with (state / (self.event["session_id"] + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(hook, "capture") as capture, self.assertRaisesRegex(hook.HookError, "already running"):
                hook.handle(config, self.event)
            capture.assert_not_called()
        with patch.object(hook, "capture", return_value={}) as capture:
            hook.handle(config, self.event)
            capture.assert_called_once()

    def test_failed_write_is_not_reported_as_saved(self):
        self.write_dialogue([dict(type="user", message=dict(content="Useful decision"))])
        memory = hook.Memory(self.config, "session-one")
        with patch.object(memory, "checkpoint", return_value=None), \
             patch.object(hook, "run_json", return_value={"structured_output": {"checkpoint": "Selected decision"}}), \
             patch.object(memory, "call", side_effect=hook.HookError("write failed")):
            with self.assertRaisesRegex(hook.HookError, "write failed"):
                hook.capture(memory, self.event)

    def test_stale_optional_body_keeps_index_with_refresh_notice(self):
        memory = hook.Memory(self.config, "session-one")
        view = dict(selected=[], index=[dict(record_id="r", version=1, summary="preview", pull_arguments={})])
        with patch.object(memory, "search", return_value=view), \
             patch.object(memory, "call", side_effect=hook.HookError("stale")):
            output = hook.recall(memory, dict(self.event, hook_event_name="UserPromptSubmit"))
        self.assertIn("search again", output["hookSpecificOutput"]["additionalContext"])
        self.assertIn("preview", output["hookSpecificOutput"]["additionalContext"])

    def test_installer_preserves_unrelated_settings_and_is_idempotent(self):
        settings = self.root / "settings.json"
        original = dict(model="chosen", permissions={"deny": ["Bash"]},
                        hooks={"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "existing"}]}]})
        settings.write_text(json.dumps(original))
        destination = self.root / "hook files"
        installer.install(settings, destination, self.config)
        first = settings.read_bytes()
        installer.install(settings, destination, self.config)
        self.assertEqual(settings.read_bytes(), first)
        updated = json.loads(first)
        self.assertEqual(updated["permissions"], original["permissions"])
        self.assertEqual(updated["hooks"]["SessionStart"][0], original["hooks"]["SessionStart"][0])
        self.assertEqual(json.loads(settings.with_name("settings.json.before-cairn-lifecycle").read_text()), original)


if __name__ == "__main__":
    unittest.main()
