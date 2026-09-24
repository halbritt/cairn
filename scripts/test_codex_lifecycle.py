"""Codex lifecycle memory uses the shared engine with Codex transcripts and hooks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hook = module("cairn_lifecycle_codex", ROOT / "integrations/lifecycle/memory.py")
installer = module("install_codex_hooks", ROOT / "scripts/install-codex-hooks.py")
SESSION = "01a0d0ea-3941-74b1-812a-ec349e878da4"  # Codex session IDs are UUIDv7


def item(role, *texts, kind=None, kinds=None):
    content_type = kind or ("output_text" if role == "assistant" else "input_text")
    payload = dict(type="message", role=role, content=[dict(type=content_type, text=t) for t in texts])
    if kinds is not None:  # Codex 0.156 structural metadata, one kind per content part
        payload["internal_chat_message_metadata_passthrough"] = dict(turn_id="t", content_item_kinds=kinds)
    return dict(timestamp="2026-09-23T00:00:00Z", type="response_item", payload=payload)


class CodexTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "rollout.jsonl"

    def write(self, records):
        self.path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_rollout_keeps_owner_dialogue_and_omits_injected_context_tools_and_reasoning(self):
        self.write([
            dict(type="session_meta", payload=dict(id=SESSION, instructions="PRIVATE")),
            item("developer", "## Memory PRIVATE developer guidance"),
            item("user", "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>PRIVATE</INSTRUCTIONS>"),
            item("user", "<environment_context>\n  <cwd>/repo</cwd>PRIVATE</environment_context>"),
            item("user", "Use PostgreSQL for the store."),
            dict(type="response_item", payload=dict(type="reasoning", summary=[dict(type="summary_text", text="PRIVATE")])),
            dict(type="response_item", payload=dict(type="custom_tool_call", name="exec", input="PRIVATE")),
            dict(type="response_item", payload=dict(type="custom_tool_call_output", output="PRIVATE")),
            dict(type="event_msg", payload=dict(type="agent_message", message="duplicate of the item below")),
            item("assistant", "Decision recorded: PostgreSQL."),
            item("user", "<turn_aborted>PRIVATE</turn_aborted>"),
        ])
        excerpt = hook.conversation(self.path)
        self.assertNotIn("PRIVATE", json.dumps(excerpt, ensure_ascii=False))
        self.assertNotIn("duplicate", json.dumps(excerpt))
        self.assertEqual(excerpt, [dict(role="user", text="Use PostgreSQL for the store."),
                                   dict(role="assistant", text="Decision recorded: PostgreSQL.")])

    def test_structural_kinds_separate_injected_context_from_owner_text(self):
        self.write([
            item("user", "# AGENTS.md instructions for /repo\n<INSTRUCTIONS>PRIVATE</INSTRUCTIONS>",
                 "<environment_context>PRIVATE</environment_context>",
                 kinds=["agents_md.instructions", "environments.environment_context"]),
            item("user", "Cairn lifecycle memory: PRIVATE hook context", kinds=["hooks.additional_context"]),
            item("user", "# AGENTS.md instructions are wrong in this repo; fix the heading.", kinds=["user.text"]),
            item("user", "<recommended_plugins>PRIVATE</recommended_plugins>", "Also keep this owner line.",
                 kinds=["unknown", "user.text"]),
            item("assistant", "Fixed the heading.", kinds=["unknown"]),
        ])
        excerpt = hook.conversation(self.path)
        self.assertNotIn("PRIVATE", json.dumps(excerpt))
        self.assertEqual(excerpt, [
            dict(role="user", text="# AGENTS.md instructions are wrong in this repo; fix the heading."),
            dict(role="user", text="Also keep this owner line."),
            dict(role="assistant", text="Fixed the heading.")])

    def test_rollouts_without_kinds_strip_only_known_injected_forms(self):
        self.write([
            item("user", "<recommended_plugins>\nPRIVATE\n</recommended_plugins>", "# AGENTS.md instructions\nPRIVATE",
                 "<environment_context>\n  <cwd>/repo</cwd>PRIVATE\n</environment_context>"),
            item("user", "<recommended_plugins>PRIVATE</recommended_plugins>\nPlease fix the heading."),
            item("user", "<permissions instructions>PRIVATE</permissions instructions>\n<request>Fix this</request>"),
            item("user", "Use <b>bold</b> in the heading."),
        ])
        self.assertNotIn("PRIVATE", json.dumps(hook.conversation(self.path)))
        self.assertEqual(hook.conversation(self.path), [dict(role="user", text="Please fix the heading."),
                                                        dict(role="user", text="<request>Fix this</request>"),
                                                        dict(role="user", text="Use <b>bold</b> in the heading.")])

    def test_oversized_rollout_message_is_bounded_and_marked(self):
        self.write([item("user", "Summarize."), item("assistant", "日" * 10000)])
        excerpt = hook.conversation(self.path)
        self.assertIn("truncated", excerpt[-1]["text"])
        self.assertLessEqual(len(hook.encoded(excerpt).encode()), hook.TEXT_BYTES)

    def test_claude_transcripts_are_unchanged(self):
        self.write([dict(type="user", message=dict(content="Use PostgreSQL.")),
                    dict(type="assistant", message=dict(content=[dict(type="text", text="Recorded.")]))])
        self.assertEqual(hook.conversation(self.path),
                         [dict(role="user", text="Use PostgreSQL."), dict(role="assistant", text="Recorded.")])


def considered(event, state):
    """What a completed real capture records: the position of its snapshot."""
    state["capture_snapshot_marker"] = hook.codex_snapshot_marker(event["transcript_path"])
    return {}


class CodexHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        self.transcript = self.root / "rollout.jsonl"
        self.config = dict(harness="codex", cairn="cairn", claude="claude", socket="socket", token_file="token",
                           repo="shared", state_dir=str(self.root / "state"))
        self.event = dict(session_id=SESSION, cwd=str(self.root), transcript_path=str(self.transcript),
                          model="gpt-6-sol", turn_id="turn-1", stop_hook_active=False)

    def dialogue(self, pairs):
        records = []
        for n in range(pairs):
            records += [item("user", f"Request {n}: change the retry policy for the uploader."),
                        item("assistant", f"Changed the retry policy, step {n}.")]
        self.transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_stop_captures_only_after_enough_new_dialogue(self):
        calls = []
        def capture(memory, event, state):
            calls.append(event["hook_event_name"])
            return considered(event, state)
        with patch.object(hook, "capture", side_effect=capture):
            self.dialogue(1)
            self.assertEqual(hook.handle(self.config, dict(self.event, hook_event_name="Stop")), {})
            self.assertEqual(calls, [], "a short exchange started the selector")
            self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2)
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(calls, ["Stop"])
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(calls, ["Stop"], "unchanged dialogue started the selector again")
            hook.handle(self.config, dict(self.event, hook_event_name="PreCompact", trigger="auto"))
            self.assertEqual(calls, ["Stop", "PreCompact"], "compaction must always offer capture")

    def test_stop_threshold_does_not_saturate_on_long_sessions(self):
        calls = []
        with patch.object(hook, "capture", side_effect=lambda memory, event, state: calls.append(1) or considered(event, state)):
            self.dialogue(400)  # far larger than the bounded excerpt window
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(len(calls), 1)
            self.dialogue(400 + hook.CODEX_STOP_MIN_MESSAGES // 2)
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(len(calls), 2, "new dialogue beyond a saturated window was never captured")
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(len(calls), 2)

    def test_dialogue_appended_during_capture_is_not_marked_captured(self):
        self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2)  # six messages
        def select(config, schema, prompt, excerpt, timeout=35):
            self.assertEqual(len(excerpt["messages"]), hook.CODEX_STOP_MIN_MESSAGES)
            self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2 + 1)  # a new pair lands during selection
            return {"structured_output": {"checkpoint": None, "workstream": None, "memories": []}}
        with patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "select_json", side_effect=select):
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
        state = json.loads((Path(self.config["state_dir"]) / (SESSION + ".json")).read_text())
        self.assertEqual(hook.codex_new_messages(self.event, state), 2,
                         "messages that arrived during selection were marked as captured")

    def test_dialogue_appended_between_excerpt_and_marker_is_not_skipped(self):
        self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2)
        real = hook.conversation
        reads = []
        def racing_conversation(path, with_offsets=False):
            result = real(path, with_offsets)
            reads.append(1)
            if len(reads) == 2:  # between capture's excerpt read and any later read
                self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2 + 1)
            return result
        with patch.object(hook, "conversation", side_effect=racing_conversation), \
             patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "select_json", return_value={"structured_output": {"checkpoint": None, "workstream": None, "memories": []}}):
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
        state = json.loads((Path(self.config["state_dir"]) / (SESSION + ".json")).read_text())
        self.assertEqual(hook.codex_new_messages(self.event, state), 2, "dialogue appended after the excerpt read was skipped")

    def test_repeated_identical_messages_still_count_as_new(self):
        records = [item("user", f"step {n}") if n % 2 == 0 else item("assistant", "Done.") for n in range(6)]
        self.transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch.object(hook, "capture", side_effect=lambda memory, event, state: considered(event, state)) as capture:
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            with self.transcript.open("a") as out:
                for n in range(3):
                    out.write(json.dumps(item("user", "again")) + "\n" + json.dumps(item("assistant", "Done.")) + "\n")
            state = json.loads((Path(self.config["state_dir"]) / (SESSION + ".json")).read_text())
            self.assertEqual(hook.codex_new_messages(self.event, state), 6, "repeated content hid new dialogue")
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            self.assertEqual(capture.call_count, 2)

    def test_replaced_or_truncated_rollout_counts_as_new(self):
        self.dialogue(hook.CODEX_STOP_MIN_MESSAGES // 2)
        with patch.object(hook, "capture", side_effect=lambda memory, event, state: considered(event, state)):
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
        state = json.loads((Path(self.config["state_dir"]) / (SESSION + ".json")).read_text())
        self.transcript.write_text(json.dumps(item("user", "fresh")) + "\n")  # shorter than the saved offset
        self.assertEqual(hook.codex_new_messages(self.event, state), 1)
        other = self.root / "other.jsonl"; other.write_text(json.dumps(item("user", "forked")) + "\n")
        self.assertEqual(hook.codex_new_messages(dict(self.event, transcript_path=str(other)), state), 1)

    def test_snapshot_marker_is_scoped_to_codex(self):
        self.dialogue(1)
        state = {}
        with patch.object(hook.Memory, "checkpoint", return_value=None), \
             patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "select_json", return_value={"structured_output": {"checkpoint": None, "workstream": None, "memories": []}}):
            hook.capture(hook.Memory(dict(self.config, harness="claude"), SESSION), dict(self.event, hook_event_name="PreCompact"), state)
        self.assertNotIn("capture_snapshot_marker", state)

    def test_failed_capture_does_not_advance_the_marker(self):
        self.dialogue(hook.CODEX_STOP_MIN_MESSAGES)
        with patch.object(hook, "capture", side_effect=hook.HookError("selector failed")):
            with self.assertRaises(hook.HookError):
                hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
        with patch.object(hook, "capture", side_effect=lambda memory, event, state: considered(event, state)) as retry:
            hook.handle(self.config, dict(self.event, hook_event_name="Stop"))
            retry.assert_called_once()

    def test_prompt_during_background_capture_skips_retrieval_quietly(self):
        import fcntl, hashlib
        state_dir = Path(self.config["state_dir"]); state_dir.mkdir(parents=True)
        with (state_dir / (SESSION + ".lock")).open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the async Stop capture
            with patch.object(hook.Memory, "call", side_effect=AssertionError("retrieval ran under the capture lock")):
                self.assertEqual(hook.handle(self.config, dict(self.event, hook_event_name="UserPromptSubmit", prompt="next task")), {})
            with self.assertRaises(hook.HookError):  # a second capture still refuses rather than racing
                hook.handle(self.config, dict(self.event, hook_event_name="PreCompact"))
            with self.assertRaises(hook.HookError):  # other harnesses keep the existing refusal
                hook.handle(dict(self.config, harness="claude"), dict(self.event, hook_event_name="UserPromptSubmit", prompt="x"))

    def test_stop_continuation_and_other_harnesses_do_not_capture(self):
        with patch.object(hook, "capture", side_effect=AssertionError("unexpected capture")):
            self.dialogue(hook.CODEX_STOP_MIN_MESSAGES)
            self.assertEqual(hook.handle(self.config, dict(self.event, hook_event_name="Stop", stop_hook_active=True)), {})
            self.assertEqual(hook.handle(dict(self.config, harness="claude"), dict(self.event, hook_event_name="Stop",
                session_id="caed9473-b01a-41e7-95ce-c3c1f28d66b3")), {})

    def test_session_start_recall_uses_codex_task_scope(self):
        memory = hook.Memory(self.config, SESSION)
        self.assertEqual(memory.scope[:4], ["--repo", "shared", "--task", "codex/" + SESSION])
        result = dict(status="READY", destination=dict(name="hosted"), selected=[], index=[])
        with patch.object(hook.Memory, "call", return_value=result):
            self.assertEqual(hook.handle(self.config, dict(self.event, hook_event_name="SessionStart", source="startup")), {})


class CodexInstallerTests(unittest.TestCase):
    def test_installer_merges_hooks_preserves_coordination_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            hooks_path = home / "hooks.json"
            coordination = {"type": "command", "command": "python3 coordination.py hook --config codex-one.json", "timeout": 15}
            hooks_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [coordination]}], "SessionStart": [{"hooks": [coordination]}]}}))
            config = dict(cairn="/bin/cairn", claude="/bin/claude", socket="/s", token_file="/t", repo="shared", model="claude-sonnet-5")
            trusted = []
            with patch.object(installer, "trust", side_effect=lambda home_, path, command: trusted.append(command)):
                first = installer.install(hooks_path, home / "dest", config)
                second = installer.install(hooks_path, home / "dest", config)
            self.assertEqual(first, second)
            self.assertEqual(len(trusted), 2)
            data = json.loads(hooks_path.read_text())["hooks"]
            for event, spec in installer.EVENTS.items():
                ours = [h for g in data[event] for h in g["hooks"] if h["command"] == first]
                self.assertEqual(len(ours), 1, f"{event} not installed exactly once")
                self.assertEqual(ours[0].get("async", False), spec.get("async", False))
            self.assertIn(coordination, [h for g in data["Stop"] for h in g["hooks"]])
            self.assertIn(coordination, [h for g in data["SessionStart"] for h in g["hooks"]])
            self.assertNotIn("SessionEnd", installer.EVENTS, "SessionEnd allows too little time for capture")
            self.assertTrue(installer.EVENTS["Stop"]["async"])
            installed = json.loads((home / "dest" / "config.json").read_text())
            self.assertEqual(installed["harness"], "codex")
            self.assertTrue((home / "dest" / "lifecycle.py").exists())
            self.assertTrue((home / "hooks.json.before-cairn-lifecycle").exists())


if __name__ == "__main__":
    unittest.main()
