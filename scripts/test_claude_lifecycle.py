"""Behavior at the host/memory boundary, using no operational database or model."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hook = module("cairn_lifecycle", ROOT / "integrations/lifecycle/memory.py")
installer = module("install_claude_hooks", ROOT / "scripts/install-claude-hooks.py")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        self.transcript = self.root / "conversation.jsonl"
        self.event = dict(hook_event_name="PreCompact", session_id="caed9473-b01a-41e7-95ce-c3c1f28d66b3", cwd=str(self.root),
                          transcript_path=str(self.transcript))
        self.candidates = patch.object(hook, "durable_candidates", return_value=[])
        self.candidates.start()
        self.handoffs = patch.object(hook, "handoff_candidates", return_value=[])
        self.handoffs.start()
        self.addCleanup(self.handoffs.stop)
        self.addCleanup(self.candidates.stop)
        self.config = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared", state_dir=str(self.root / "state"))

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
                      index=[dict(record_id="record", version=1, summary="fix startup failure", pull_arguments={"handle": "exact"},
                                  pull_command="private paths")])
        with patch.object(memory, "call", side_effect=[result, {"selection": {"record": {
                "record_id": "record", "version": 1, "body": "fix startup failure: useful body"}}}]) as call:
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
            self.assertEqual(output, {})

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
                self.assertIn( '"' + hook.title_for(self.event) + '"', search.call_args.args[0])

    def test_capture_updates_one_note_and_null_does_not_write(self):
        self.write_dialogue([dict(type="user", message=dict(content="Use PostgreSQL; tests are pending."))])
        memory = hook.Memory(self.config, "session-one")
        previous = dict(record_id="existing", version=4, body=hook.workstream_prefix(self.event) + "Storage\n\nPrevious checkpoint")
        with patch.object(memory, "checkpoint", return_value=previous), patch.object(memory, "call", return_value={"record_id": "existing"}) as call:
            with patch.object(hook, "run_json", return_value={"structured_output": {"memories": [], "workstream": None, "checkpoint": None}}):
                self.assertEqual(hook.capture(memory, self.event), {})
                call.assert_not_called()
            with patch.object(hook, "run_json", return_value={"structured_output": {"memories": [], "workstream": "Storage", "checkpoint": "Decision: PostgreSQL. Tests pending."}}) as model:
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
        for result in [{}, {"is_error": True}, {"structured_output": {"memories": [], "workstream": "Storage", "checkpoint": False}},
                       {"structured_output": {"memories": [], "workstream": "Storage", "checkpoint": "x" * (hook.NOTE_BYTES + 1)}}]:
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
        (self.root / ".git").mkdir(exist_ok=True)
        subdir = self.root / "src"
        subdir.mkdir()
        nested = dict(self.event, cwd=str(subdir))
        self.assertEqual(hook.title_for(nested), hook.title_for(self.event))
        (self.root / ".cairn-no-memory").touch()
        self.assertEqual(hook.handle(self.config, nested), {})

    def test_search_timeout_is_labelled_and_does_not_inject_or_write(self):
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="task")
        with patch.object(hook, "bounded_command", side_effect=subprocess.TimeoutExpired("cairn", 5)):
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
             patch.object(hook, "run_json", return_value={"structured_output": {"memories": [], "workstream": "Storage", "checkpoint": "Selected decision"}}), \
             patch.object(memory, "call", side_effect=hook.HookError("write failed")):
            with self.assertRaisesRegex(hook.HookError, "write failed"):
                hook.capture(memory, self.event)

    def test_stale_optional_body_keeps_index_with_refresh_notice(self):
        memory = hook.Memory(self.config, "session-one")
        view = dict(selected=[], index=[dict(record_id="r", version=1, summary="startup failure preview", pull_arguments={})])
        with patch.object(memory, "search", return_value=view), \
             patch.object(memory, "call", side_effect=hook.HookError("stale")):
            output = hook.recall(memory, dict(self.event, hook_event_name="UserPromptSubmit", prompt="startup failure"))
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
        self.assertTrue(json.loads((destination / "config.json").read_text())["semantic_fallback"])
        installer.install(settings, destination, dict(self.config, semantic_fallback=False))
        self.assertFalse(json.loads((destination / "config.json").read_text())["semantic_fallback"])

    def test_weak_matches_omitted_and_delivered_versions_reset_with_context(self):
        memory = hook.Memory(self.config, "session-one")
        entry = dict(record_id="r", version=1, summary="startup failure fix", pull_arguments={})
        state = {}
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="startup failure")
        with patch.object(memory, "search", return_value=dict(index=[entry])), \
             patch.object(memory, "call", return_value=dict(selection=dict(record=dict(
                 record_id="r", version=1, body="startup failure saved fix")))) as pull:
            self.assertEqual(hook.recall(memory, dict(event, prompt="unrelated layout"), state), {})
            self.assertIn("saved fix", str(hook.recall(memory, event, state)))
            self.assertEqual(hook.recall(memory, event, state), {})
            entry["version"] = 2
            pull.return_value["selection"]["record"]["version"] = 2
            self.assertIn("saved fix", str(hook.recall(memory, event, state)))
            self.assertIn("saved fix", str(hook.recall(memory,
                dict(event, hook_event_name="SessionStart", source="compact"), state)))

    def test_selected_durable_correction_updates_supplied_record_separately(self):
        self.write_dialogue([dict(type="user", message=dict(content="Prefer PostgreSQL; SQLite is superseded."))])
        memory = hook.Memory(self.config, "session-one")
        old = dict(record_id="decision", version=7, kind="decision", body="Earlier SQLite decision")
        selection = dict(workstream="Storage", checkpoint="Transaction tests remain pending.", memories=[dict(
            record_id="decision", kind="decision", title="Storage", body="PostgreSQL supersedes SQLite; owner correction.")])
        with patch.object(hook, "durable_candidates", return_value=[old]), \
             patch.object(memory, "checkpoint", return_value=None), \
             patch.object(hook, "run_json", return_value=dict(structured_output=selection)), \
             patch.object(memory, "call", return_value=dict(record_id="saved")) as call:
            hook.capture(memory, self.event)
        self.assertEqual([c.args[0] for c in call.call_args_list], ["revise", "create"])
        revised = call.call_args_list[0].kwargs["payload"]
        self.assertEqual((revised["record_id"], revised["expected_version"]), ("decision", 7))
        self.assertIn("supersedes", revised["body"])
        self.assertEqual(call.call_args_list[1].kwargs["payload"]["draft"]["kind"], "note")

    def test_invalid_durable_target_prevents_all_writes(self):
        self.write_dialogue([dict(type="user", message=dict(content="Useful decision"))])
        memory = hook.Memory(self.config, "session-one")
        selection = dict(workstream="Storage", checkpoint="Valid checkpoint", memories=[dict(
            record_id="invented", kind="decision", title="Storage", body="New decision")])
        with patch.object(memory, "checkpoint", return_value=None), \
             patch.object(hook, "run_json", return_value=dict(structured_output=selection)), \
             patch.object(memory, "call") as call, self.assertRaisesRegex(hook.HookError, "unsupplied"):
            hook.capture(memory, self.event)
        call.assert_not_called()

    def test_fresh_session_reuses_named_workstream_and_unrelated_task_is_separate(self):
        memory = hook.Memory(self.config, "fresh-session")
        old = dict(record_id="handoff", version=3, kind="note",
                   body=hook.workstream_prefix(self.event) + "Storage migration\n\nTests pending.")
        selection = dict(workstream="Storage migration", checkpoint="Tests passed; deploy next.", memories=[])
        with patch.object(memory, "checkpoint", return_value=None), \
             patch.object(memory, "call", return_value=dict(record_id="saved")) as call:
            writes = hook.selected_writes(memory, self.event, selection, None, [], [old])
            for write in writes:
                hook.save_note(memory, *write)
            self.assertEqual(call.call_args.args[0], "revise")
            self.assertEqual(call.call_args.kwargs["payload"]["record_id"], "handoff")
            selection["workstream"] = "Editor layout"
            for write in hook.selected_writes(memory, self.event, selection, None, [], [old]):
                hook.save_note(memory, *write)
            self.assertEqual(call.call_args.args[0], "create")
            self.assertIn("Editor layout", call.call_args.kwargs["payload"]["draft"]["body"])

    def test_unchanged_compaction_exit_skips_model_but_new_content_triggers_it(self):
        messages = [dict(type="user", uuid="owner", message=dict(content="Transaction checks pass; deploy next."))]
        self.write_dialogue(messages)
        memory = hook.Memory(self.config, "session")
        state = {}
        selection = dict(checkpoint="Deploy next.", workstream="Storage", memories=[])
        with patch.object(memory, "checkpoint", return_value=None), \
             patch.object(hook, "run_json", return_value=dict(structured_output=selection)) as model, \
             patch.object(memory, "call", return_value=dict(record_id="saved")):
            hook.capture(memory, self.event, state)
            self.write_dialogue(messages + [
                dict(type="user", isCompactSummary=True, message=dict(content="Host-generated summary")),
                dict(type="user", message=dict(content="<command-name>/compact</command-name>")),
                messages[0]])
            hook.capture(memory, dict(self.event, hook_event_name="SessionEnd"), state)
            self.assertEqual(model.call_count, 1)
            self.write_dialogue(messages + [dict(type="assistant", uuid="new", message=dict(content="Deployment verified."))])
            hook.capture(memory, self.event, state)
            self.assertEqual(model.call_count, 2)

    def test_null_selection_is_cached_but_failed_save_can_retry(self):
        self.write_dialogue([dict(type="user", message=dict(content="Useful decision"))])
        memory = hook.Memory(self.config, "session")
        state = {}
        with patch.object(memory, "checkpoint", return_value=None), \
             patch.object(hook, "run_json", return_value=dict(structured_output=dict(
                 checkpoint="Deploy next.", workstream="Storage", memories=[]))) as model, \
             patch.object(memory, "call", side_effect=hook.HookError("write failed")):
            with self.assertRaises(hook.HookError):
                hook.capture(memory, self.event, state)
            self.assertNotIn("captured_digest", state)
            model.return_value = dict(structured_output=dict(checkpoint=None, workstream=None, memories=[]))
            hook.capture(memory, self.event, state)
            hook.capture(memory, self.event, state)
            self.assertEqual(model.call_count, 2)

    def test_opencode_identity_and_retained_context_use_the_shared_engine(self):
        event = dict(self.event, session_id="ses_native123", hook_event_name="PostToolUse", tool_name="Read",
                     tool_input=dict(file_path=str(self.root / "store.go")))
        config = dict(self.config, harness="opencode")
        hook.handle(config, event)
        state = json.loads((self.root / "state/ses_native123.json").read_text())
        self.assertIn("store.go", state["hints"]["files"])
        self.assertIn("opencode/ses_native123", hook.Memory(config, event["session_id"]).scope)
        with self.assertRaises(hook.HookError):
            hook.handle(config, dict(event, session_id="../../outside"))
        memory = hook.Memory(config, event["session_id"])
        state = {"seen": {"retained": 1, "evicted": 1}}
        with patch.object(memory, "search", return_value={}):
            hook.recall(memory, dict(event, hook_event_name="UserPromptSubmit", retained_record_ids=["retained"]), state)
        self.assertEqual(state["seen"], {"retained": 1})

    def test_continuation_prefers_project_without_joining_unrelated_workstreams(self):
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="Continue editor layout")
        intent = hook.retrieval_intent(event, {})
        self.assertIn('"' + hook.workstream_prefix(event).rstrip() + '"', intent["query"])
        self.assertFalse(hook.relevant(dict(summary=hook.workstream_prefix(event) + "Storage migration"), intent))

    def test_retrieved_handoff_binds_resume_to_workstream(self):
        memory = hook.Memory(self.config, "session")
        title = hook.workstream_prefix(self.event) + "Storage migration"
        record = dict(record_id="handoff", version=1, body=title + "\n\nTests pending.", kind="note")
        entry = dict(record_id="handoff", version=1, summary=record["body"], pull_arguments={})
        state = {}
        with patch.object(memory, "search", return_value=dict(index=[entry])) as search, \
             patch.object(memory, "call", return_value=dict(selection=dict(record=record))):
            hook.recall(memory, dict(self.event, hook_event_name="UserPromptSubmit", prompt="Continue storage migration"), state)
            self.assertEqual(state["workstream"], title)
            hook.recall(memory, dict(self.event, hook_event_name="SessionStart", source="resume"), state)
            self.assertIn('"' + title + '"', search.call_args.args[0])

    def test_identical_new_topic_is_not_duplicated_in_fresh_session(self):
        memory = hook.Memory(self.config, "session-one")
        body = hook.clip(self.root.name, 40) + ": Storage\n\nUse PostgreSQL."
        old = dict(record_id="existing", kind="decision", version=1, body=body)
        selection = dict(workstream=None, checkpoint=None, memories=[dict(record_id=None, kind="decision",
                         title="Storage", body="Use PostgreSQL.")])
        with patch.object(memory, "checkpoint", return_value=old), patch.object(memory, "call") as call:
            for write in hook.selected_writes(memory, self.event, selection, None, []):
                self.assertIsNone(hook.save_note(memory, *write))
        call.assert_not_called()

    def test_file_and_error_hints_survive_long_prompt_and_expire(self):
        state = {}
        hook.observe(dict(self.event, hook_event_name="PostToolUse", tool_name="Read",
                          tool_input=dict(file_path=str(self.root / "core/currentness.go")),
                          tool_response="RAW FILE CONTENT"), state)
        hook.observe(dict(self.event, hook_event_name="PostToolUseFailure",
                          error="EADDRINUSE: RAW OUTPUT"), state)
        event = dict(self.event, hook_event_name="UserPromptSubmit",
                     prompt="please " * 300 + 'repair "client/socket.go"')
        intent = hook.retrieval_intent(event, state)
        self.assertIn("core/currentness.go", intent["files"])
        self.assertIn('"client/socket.go"', intent["query"])
        self.assertIn("eaddrinuse", intent["words"])
        self.assertNotIn("RAW", str(state))
        self.assertTrue(hook.relevant(dict(summary="repair EADDRINUSE"), intent))
        with patch.object(hook.time, "time", return_value=hook.time.time() + 901):
            intent = hook.retrieval_intent(event, state)
        self.assertNotIn("core/currentness.go", intent["files"])
        self.assertNotIn("eaddrinuse", intent["words"])

    def test_associated_files_and_mandatory_context_are_retained(self):
        memory = hook.Memory(self.config, "session-one")
        entry = dict(record_id="r", version=1, summary="opaque summary", pull_arguments={},
                     entities=[dict(kind="file", name="core/store.go")])
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="core/store.go")
        with patch.object(memory, "search", return_value=dict(selected=["required"], index=[entry])) as search, \
             patch.object(memory, "call", return_value=dict(selection=dict(record=dict(
                 record_id="r", version=1, body="file guidance")))):
            state = {}
            self.assertIn("file guidance", str(hook.recall(memory, event, state)))
            self.assertEqual(search.call_args.kwargs["entities"], ["core/store.go"])
            result = str(hook.recall(memory, event, state))
            self.assertIn("required", result)
            self.assertNotIn("file guidance", result)


class CourtesyCaptureTests(unittest.TestCase):
    def test_only_complete_courtesy_pairs_skip_selection(self):
        from unittest.mock import Mock
        memory = Mock(config={})
        courtesy = [dict(role='user', text='Thanks!'), dict(role='assistant', text="You're welcome.")]
        state = {}
        self.assertEqual(hook.capture(memory, dict(messages=courtesy), state), {})
        memory.checkpoint.assert_not_called()
        self.assertEqual(state['captured_messages'], 2)
        self.assertFalse(hook.courtesy_only([dict(role='user', text='Thanks, use PostgreSQL 17.'), courtesy[1]]))
        self.assertFalse(hook.courtesy_only([courtesy[0], dict(role='assistant', text='The deployment is done.')]))
        self.assertFalse(hook.courtesy_only([dict(role='user', text='Yes'), courtesy[1]]))
        self.assertFalse(hook.courtesy_only(courtesy[:1]))

    def test_uncaptured_or_changed_prefix_cannot_be_hidden_by_courtesy(self):
        from unittest.mock import Mock
        memory = Mock(config={})
        memory.checkpoint.side_effect = hook.HookError('selection path reached')
        original = [dict(role='user', text='Correct the database to PostgreSQL 17.'), dict(role='assistant', text='Updated.')]
        thanks = [dict(role='user', text='Thanks!'), dict(role='assistant', text="You're welcome.")]
        event = dict(messages=original+thanks,cwd='/tmp',session_id='courtesy')
        for state in ({}, {'captured_messages':2,'captured_digest':'unconfirmed'}):
            with self.assertRaisesRegex(hook.HookError,'selection path reached'):
                hook.capture(memory,event,state)
        digest = hashlib.sha256(hook.encoded([hook.CAPTURE_PROMPT,hook.CAPTURE_SCHEMA,None,original]).encode()).hexdigest()
        state = dict(captured_messages=2,captured_digest=digest)
        self.assertEqual(hook.capture(memory,event,state), {})
        self.assertEqual(state['captured_messages'],4)
        event['messages'][0]['text']='A new correction'
        with self.assertRaisesRegex(hook.HookError,'selection path reached'):
            hook.capture(memory,event,state)


class CandidateBudgetTests(unittest.TestCase):
    def test_only_explicit_budget_refusal_has_the_budget_type(self):
        for status, expected in [('BUDGET_REFUSED', hook.BudgetRefused), ('UNAUTHORIZED', hook.HookError)]:
            child = ['import json,sys',
                     'print(json.dumps({"ok": False, "status": sys.argv[1]}))',
                     'print("private payload", file=sys.stderr)', 'sys.exit(2)']
            with self.assertRaises(expected) as raised:
                hook.run_json([sys.executable, '-c', ';'.join(child), status])
            self.assertNotIn('private', str(raised.exception))
            self.assertIs(type(raised.exception), expected)

    def test_memory_command_output_is_bounded_on_both_pipes(self):
        for stream in ('stdout', 'stderr'):
            child = ('import sys; sys.%s.write("x" * %d); sys.%s.flush()' %
                     (stream, hook.COMMAND_OUTPUT_BYTES + 1, stream))
            with self.assertRaisesRegex(hook.HookError, 'output exceeded limit'):
                hook.run_json([sys.executable, '-c', child])

    def test_memory_command_reads_input_while_child_writes_output(self):
        child = ('import json,sys; sys.stdout.write(" " * 131072); '
                 'data=json.load(sys.stdin); print(json.dumps(data))')
        self.assertEqual(hook.run_json([sys.executable, '-c', child], body=json.dumps({'ok': True}) + ' ' * 131072),
                         {'ok': True})

    def test_optional_candidate_budget_preserves_read_records_but_transport_failure_propagates(self):
        from unittest.mock import Mock
        event = dict(cwd='/tmp/cairn-budget-project', hook_event_name='SessionEnd')
        entries = [dict(summary='PostgreSQL validation', pull_arguments=dict(handle=str(n))) for n in range(3)]
        with patch.object(hook, 'relevant', return_value=True):
            for function, kind in [(hook.handoff_candidates, 'note'), (hook.durable_candidates, 'decision')]:
                record = dict(record_id='read', kind=kind, body=hook.workstream_prefix(event)+'Validation\n\nRead body', **{'class':'A'})
                memory = Mock()
                memory.search.return_value = dict(index=entries)
                memory.call.side_effect = [dict(selection=dict(record=record)), hook.BudgetRefused('budget')]
                args = (memory,event,[],{}) if kind=='note' else (memory,event,[])
                self.assertEqual(function(*args), [record])
                self.assertEqual(memory.call.call_count, 2)
                memory.call.side_effect = hook.HookError('transport failed')
                with self.assertRaisesRegex(hook.HookError, 'transport failed'):
                    function(*args)

    def test_unsupplied_matching_handoff_is_never_overwritten_after_candidate_budget_stop(self):
        from unittest.mock import Mock
        event = dict(cwd='/tmp/cairn-budget-project')
        memory = Mock()
        memory.checkpoint.return_value = dict(body=hook.workstream_prefix(event)+'Validation\n\nUnread old body')
        selected = dict(workstream='Validation', checkpoint='New body', memories=[])
        with self.assertRaisesRegex(hook.HookError, 'needs reconciliation'):
            hook.selected_writes(memory,event,selected,None,[],[])
        memory.call.assert_not_called()

    def test_generated_note_title_pushing_body_past_limit_is_rejected(self):
        from unittest.mock import Mock
        event = dict(cwd='/tmp/cairn-budget-project')
        memory = Mock()
        long_body = "x" * (hook.NOTE_BYTES - 10)
        selection = dict(workstream=None, checkpoint=None, memories=[
            dict(record_id=None, kind="decision", title="Long Title", body=long_body)
        ])
        with self.assertRaisesRegex(hook.HookError, "invalid note fields"):
            hook.selected_writes(memory, event, selection, None, [])


class SemanticFallbackTests(unittest.TestCase):
    def test_semantic_passage_over_context_budget_never_reaches_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture', context_bytes=1000), 'semantic')
            event = dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='recover lease expiry')
            body = 'recover lease expiry ' + 'x' * 3500
            entry = dict(record_id='saved', version=1, summary='opaque',
                         match_span=dict(offset=0, length=3500), pull_arguments=dict(handle='saved'))
            whole = dict(selection=dict(record=dict(record_id='saved', version=1, body=body)))
            excerpt = body[:3500]
            span = dict(selection=dict(record=dict(record_id='saved', version=1, body='', **{'class': 'A'})),
                        span=dict(offset=0, end=3500, total_bytes=len(body), body=excerpt,
                                  sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                                  source_sha256=hashlib.sha256(body.encode()).hexdigest()))
            with patch.object(memory, 'search', side_effect=[dict(index=[]),
                     dict(index=[entry], discovery=dict(state='ready'))]), \
                 patch.object(memory, 'call', side_effect=[whole, span]), \
                 patch.object(hook, 'select_json') as model:
                state = {}
                result = hook.recall(memory, event, state)
            self.assertEqual(result, {})
            self.assertEqual(state['seen'], {})
            self.assertEqual(state['last_recall']['discovery'], 'context_budget')
            model.assert_not_called()

    def test_long_hit_uses_current_span_and_preserves_coverage_without_marking_full_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture', context_bytes=9500), 'semantic')
            event = dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='recover lease expiry')
            body = 'Unrelated prefix. ' * 700 + 'Recover lease expiry by renewing the original claim.'
            passage = 'Recover lease expiry by renewing the original claim.'
            offset = len(body.encode()) - len(passage.encode())
            digest = hashlib.sha256(body.encode()).hexdigest()
            entry = dict(record_id='saved', version=2, class_='A', summary='unrelated prefix',
                         body_sha256=digest, match_span=dict(offset=offset, length=len(passage.encode())),
                         pull_arguments=dict(request_id='original', receipt_id='receipt', handle='handle'))
            entry['class'] = entry.pop('class_')
            whole = dict(selection=dict(record=dict(record_id='saved', version=2, class_='A', body=body)))
            span = dict(selection=dict(record=dict(record_id='saved', version=2, class_='A', body='')),
                        span=dict(offset=offset, end=len(body.encode()), total_bytes=len(body.encode()),
                                  body=passage, sha256=hashlib.sha256(passage.encode()).hexdigest(),
                                  source_sha256=digest))
            whole['selection']['record']['class'] = whole['selection']['record'].pop('class_')
            span['selection']['record']['class'] = span['selection']['record'].pop('class_')
            lexical = dict(index=[], selected=[dict(body='mandatory instruction')])
            semantic = dict(index=[entry], discovery=dict(state='ready', coverage=dict(indexed=1, eligible=2)))
            with patch.object(memory, 'search', side_effect=[lexical, semantic]), \
                 patch.object(memory, 'call', side_effect=[whole, span]) as pull, \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))) as model:
                state = {}
                output = hook.recall(memory, event, state)
            text = output['hookSpecificOutput']['additionalContext']
            self.assertIn(passage, text)
            self.assertIn('mandatory instruction', text)
            self.assertNotIn('Unrelated prefix. ' * 10, text)
            self.assertIn('"source_extent":"partial_span"', text)
            self.assertIn('"coverage":{"indexed":1,"eligible":2}', text)
            self.assertIn('"match_span"', text)
            self.assertEqual(state['seen'], {})
            self.assertEqual(state['last_recall']['source_extent'], 'partial_span')
            self.assertEqual(state['last_recall']['partial_record'], 'saved')
            self.assertIsNone(state['last_recall']['expanded'])
            self.assertEqual(state['last_recall']['coverage'], dict(indexed=1, eligible=2))
            self.assertEqual(pull.call_args_list[1].kwargs['payload']['span'], entry['match_span'])
            self.assertNotEqual(pull.call_args_list[1].kwargs['payload']['request_id'], 'original')
            self.assertTrue(model.called)
            self.assertLessEqual(len(text.encode()), 9500)

    def test_paraphrase_requires_full_body_verification_and_preserves_required_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            event = dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='Which durable backend is used here?')
            entry = dict(record_id='saved', version=2, summary='PostgreSQL operational store',
                         pull_arguments=dict(handle='current'))
            lexical = dict(index=[], selected=[dict(body='mandatory first')])
            semantic = dict(index=[entry], selected=[dict(body='mandatory second')], discovery=dict(state='ready'))
            pulled = dict(selection=dict(record=dict(record_id='saved', version=2,
                                                     body='Use PostgreSQL for this project.')))
            for verdict in (True, False):
                state = {}
                with patch.object(memory, 'search', side_effect=[dict(lexical), semantic]) as search, \
                     patch.object(memory, 'call', return_value=pulled) as pull, \
                     patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0 if verdict else -1))) as model:
                    result = hook.recall(memory, event, state)
                text = result['hookSpecificOutput']['additionalContext']
                self.assertIn('mandatory first', text)
                self.assertIn('mandatory second', text)
                self.assertEqual('Use PostgreSQL' in text, verdict)
                self.assertEqual(pull.call_count, 1)
                self.assertTrue(search.call_args.kwargs['semantic'])
                self.assertGreater(model.call_args.kwargs['timeout'], 0)
                self.assertLessEqual(model.call_args.kwargs['timeout'], 8)
                self.assertLessEqual(len(text.encode()), hook.CONTEXT_BYTES)
                self.assertEqual(state['last_recall']['discovery'], 'verified' if verdict else 'not_relevant')

    def test_unavailable_worker_and_precise_requests_do_not_invoke_relevance_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            for prompt in ('Which durable backend is used?', 'repair core/store.go', 'repair "ExactError"'):
                state = dict(hints=dict(errors=['ExpiredError'], error_at=0))
                with patch.object(memory, 'search', return_value=dict(index=[], discovery=dict(state='unavailable'))) as search, \
                     patch.object(hook, 'select_json') as model:
                    result = hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt=prompt), state)
                self.assertEqual(result, {})
                self.assertEqual(search.call_count, 2)
                model.assert_not_called()
                self.assertEqual(state['last_recall']['discovery'], 'unavailable')

    def test_oversized_combined_mandatory_context_refuses_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            with patch.object(memory, 'search', side_effect=[
                    dict(selected=['a'*6500]), dict(selected=['b'*6500], discovery=dict(state='unavailable'))]):
                with self.assertRaises(hook.HookError):
                    hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='durable backend'), {})

    def test_semantic_shortlist_tries_later_current_body_after_irrelevant_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            event = dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='Which durable backend is used here?')
            entries = [dict(record_id=name, version=1, summary='opaque', pull_arguments=dict(handle=name))
                       for name in ('first', 'second')]
            lexical = dict(index=[], selected=[dict(body='required')])
            semantic = dict(index=entries, discovery=dict(state='ready'))
            bodies = [dict(selection=dict(record=dict(record_id=name, version=1, body=body)))
                      for name, body in [('first', 'Unrelated note.'), ('second', 'Use PostgreSQL for durable storage.')]]
            with patch.object(memory, 'search', side_effect=[lexical, semantic]) as search, \
                 patch.object(memory, 'call', side_effect=bodies) as pull, \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=1))) as model:
                state = {}
                result = hook.recall(memory, event, state)
            text = result['hookSpecificOutput']['additionalContext']
            self.assertIn('PostgreSQL', text)
            self.assertIn('required', text)
            self.assertNotIn('Unrelated note.', text)
            self.assertEqual(state['seen'], {'second': 1})
            self.assertEqual(state['last_recall']['discovery'], 'verified')
            self.assertEqual((search.call_count, pull.call_count, model.call_count), (2, 2, 1))


class RecallCandidateTests(unittest.TestCase):
    def test_later_page_guidance_is_verified_before_injection(self):
        self.memory.config['semantic_fallback'] = True
        noise = [self.entry('noise-' + str(i), 'Routine status') for i in range(10)]
        useful = self.entry('useful', 'Keep overnight proposals additions-only')
        pages = [dict(index=[], selected=[]),
                 dict(index=noise, receipt_id='page0', credits_remaining=4, discovery=dict(state='ready'),
                      page=dict(offset=0, next_offset=10)),
                 dict(index=[useful], receipt_id='page1', credits_remaining=4, discovery=dict(state='ready'),
                      page=dict(offset=10, next_offset=None))]
        def select(config, schema, prompt, request, **kwargs):
            if 'previews' in request:
                indices = [p['index'] for p in request['previews'] if 'additions-only' in p['summary']]
                return dict(structured_output=dict(indices=indices))
            return dict(structured_output=dict(index=0))
        with patch.object(self.memory, 'search', side_effect=pages) as search, \
             patch.object(self.memory, 'call', return_value=self.pulled('useful', 'Keep overnight proposals additions-only.')) as pull, \
             patch.object(hook, 'select_json', side_effect=select):
            result = hook.recall(self.memory, self.event, {})
        self.assertIn('additions-only', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(pull.call_count, 1)
        self.assertEqual([c.kwargs.get('offset') for c in search.call_args_list], [None, 0, 10])
        self.assertEqual(search.call_args_list[1].args, search.call_args_list[2].args)
        self.assertEqual(search.call_args_list[1].kwargs['entities'], search.call_args_list[2].kwargs['entities'])

    def test_paging_stops_at_resource_bound_and_preserves_required_context(self):
        self.memory.config['semantic_fallback'] = True
        required = dict(body='Required project instruction')
        def search(query, **kwargs):
            if not kwargs.get('semantic'):
                return dict(index=[], selected=[required])
            offset = kwargs['offset']
            return dict(index=[self.entry(str(i)) for i in range(offset, offset + 10)],
                        receipt_id=str(offset), credits_remaining=4, discovery=dict(state='ready'),
                        page=dict(offset=offset, next_offset=offset + 10))
        with patch.object(self.memory, 'search', side_effect=search) as searched, \
             patch.object(hook, 'select_json', return_value=dict(structured_output=dict(indices=[]))):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('Required project instruction', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(searched.call_count, 5)
        self.assertEqual(state['last_recall']['semantic_previews'], 32)

    def test_whole_server_page_is_inspected_within_total_preview_bound(self):
        self.memory.config['semantic_fallback'] = True
        entries = [self.entry(str(i)) for i in range(10)] + [self.entry('useful', 'Concrete guidance')]
        with patch.object(self.memory, 'search', side_effect=[dict(index=[]),
                 dict(index=entries, discovery=dict(state='ready'), page=dict(next_offset=None))]), \
             patch.object(self.memory, 'call', return_value=self.pulled('useful', 'Concrete guidance')), \
             patch.object(hook, 'select_json', side_effect=[dict(structured_output=dict(indices=[10])),
                                                          dict(structured_output=dict(index=0))]):
            result = hook.recall(self.memory, self.event, {})
        self.assertIn('Concrete guidance', result['hookSpecificOutput']['additionalContext'])

    def test_invalid_page_cursor_does_not_repeat_search_or_drop_required_context(self):
        self.memory.config['semantic_fallback'] = True
        with patch.object(self.memory, 'search', side_effect=[dict(index=[], selected=[dict(body='Required')]),
                 dict(index=[], discovery=dict(state='ready'), page=dict(next_offset=0))]) as search:
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('Required', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(search.call_count, 2)
        self.assertEqual(state['last_recall']['rejected']['invalid_page_cursor'], 1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.git').mkdir()
        self.memory = hook.Memory(dict(cairn='unused', socket='unused', token_file='unused',
                                       repo='fixture', context_bytes=9500), 'candidates')
        self.event = dict(hook_event_name='UserPromptSubmit', cwd=str(self.root),
                          prompt='recover lease expiry')

    def entry(self, name, summary='unrelated preview'):
        return dict(record_id=name, version=1, summary=summary, pull_arguments=dict(handle=name))

    def pulled(self, name, body, version=1):
        return dict(selection=dict(record=dict(record_id=name, version=version, body=body)))

    def test_verified_shortlist_skips_overlapping_status_and_recovers_semantic_guidance(self):
        self.memory.config['semantic_fallback'] = True
        noise = self.entry('noise', 'nightly review status')
        useful = self.entry('useful', 'opaque preview')
        for prompt in ('Improve nightly review of bin records',
                       'Improve nightly review of bin records in core/store.go'):
            with self.subTest(prompt=prompt):
                lexical = dict(index=[noise], selected=[dict(body='required instruction')])
                semantic = dict(index=[noise, useful], discovery=dict(state='ready'))
                bodies = [self.pulled('noise', 'Nightly inbox review status. No bin-content guidance.'),
                          self.pulled('useful', 'Keep nightly bin review additions-only; do not propose removals.')]
                with patch.object(self.memory, 'search', side_effect=[lexical, semantic]) as search, \
                     patch.object(self.memory, 'call', side_effect=bodies) as pull, \
                     patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=1))) as model:
                    state = {}
                    result = hook.recall(self.memory, dict(self.event, prompt=prompt), state)
                output = result['hookSpecificOutput']['additionalContext']
                self.assertIn('additions-only', output)
                self.assertIn('required instruction', output)
                self.assertNotIn('Nightly inbox review status', output)
                self.assertNotIn('"record_id":"noise"', output)
                self.assertEqual(state['seen'], {'useful': 1})
                self.assertEqual((search.call_count, pull.call_count, model.call_count), (2, 2, 1))
                self.assertTrue(search.call_args.kwargs['semantic'])
                self.assertEqual(search.call_args.args[0],
                                 hook.retrieval_intent(dict(self.event, prompt=prompt), {})['query'])

    def test_verified_shortlist_abstains_when_model_unavailable(self):
        self.memory.config['semantic_fallback'] = True
        entry = self.entry('noise', 'nightly review status')
        with patch.object(self.memory, 'search', side_effect=[dict(index=[entry]),
                 dict(index=[], discovery=dict(state='unavailable'))]), \
             patch.object(self.memory, 'call', return_value=self.pulled('noise', 'Nightly review status only.')), \
             patch.object(hook, 'select_json', side_effect=hook.HookError('model unavailable')):
            state = {}
            self.assertEqual(hook.recall(self.memory, dict(self.event, prompt='nightly review bin records'), state), {})
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['discovery'], 'verification_unavailable')

    def test_semantic_rank_eight_can_be_admitted_from_previews(self):
        self.memory.config['semantic_fallback'] = True
        entries = [self.entry(str(i)) for i in range(8)]
        with patch.object(self.memory, 'search', side_effect=[dict(index=[]),
                 dict(index=entries, discovery=dict(state='ready'))]), \
             patch.object(self.memory, 'call', return_value=self.pulled('7', 'Candidate 7')), \
             patch.object(hook, 'select_json', side_effect=[dict(structured_output=dict(indices=[7])),
                 dict(structured_output=dict(index=0))]) as model:
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('Candidate 7', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'7': 1})
        self.assertEqual(state['last_recall']['shortlist_candidates'], 1)
        self.assertEqual(model.call_count, 2)

    def test_exhausted_lexical_receipt_does_not_stop_semantic_receipt(self):
        self.memory.config['semantic_fallback'] = True
        lexical = [self.entry(f'lex-{i}') for i in range(5)]
        useful = self.entry('semantic')
        with patch.object(self.memory, 'search', side_effect=[
                 dict(index=lexical, receipt_id='lex', credits_remaining=4),
                 dict(index=[useful], receipt_id='sem', credits_remaining=4, discovery=dict(state='ready'))]), \
             patch.object(self.memory, 'call', side_effect=[hook.BudgetRefused('lexical receipt exhausted'),
                                                           self.pulled('semantic', 'Renew the original lease claim.')]) as pull, \
             patch.object(hook, 'select_json', side_effect=[dict(structured_output=dict(indices=[0, 5])),
                 dict(structured_output=dict(index=0))]) as model:
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('Renew the original lease claim.', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'semantic': 1})
        self.assertEqual(state['last_recall']['rejected']['budget_refused'], 1)
        self.assertEqual((pull.call_count, model.call_count), (2, 2))

    def test_admitted_bodies_respect_each_receipts_four_credits(self):
        self.memory.config['semantic_fallback'] = True
        lexical = [self.entry(f'lex-{i}') for i in range(6)]
        semantic = [self.entry(f'sem-{i}') for i in range(6)]
        for entry in lexical:
            entry['pull_arguments']['receipt_id'] = 'lex-receipt'
        for entry in semantic:
            entry['pull_arguments']['receipt_id'] = 'sem-receipt'
        counts = dict(lexical=0, semantic=0)
        def current_body(operation, *, payload, timeout):
            channel = 'lexical' if payload['receipt_id'] == 'lex-receipt' else 'semantic'
            counts[channel] += 1
            self.assertLessEqual(counts[channel], 4)
            return dict(self.pulled(payload['handle'], f'Guidance from {payload["handle"]}'),
                        credits_remaining=4-counts[channel])
        with patch.object(self.memory, 'search', side_effect=[
                 dict(index=lexical, receipt_id='lex-receipt', credits_remaining=4),
                 dict(index=semantic, receipt_id='sem-receipt', credits_remaining=4,
                      discovery=dict(state='ready'))]), \
             patch.object(self.memory, 'call', side_effect=current_body), \
             patch.object(hook, 'select_json', side_effect=[
                 dict(structured_output=dict(indices=[0, 1, 2, 3, 6, 7, 8, 9])),
                 dict(structured_output=dict(index=7))]):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('Guidance from sem-3', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(counts, dict(lexical=4, semantic=4))
        self.assertEqual(state['last_recall']['receipt_attempts'], dict(lexical=4, semantic=4))
        self.assertEqual(state['seen'], {'sem-3': 1})

    def test_preview_and_body_calls_share_aggregate_input_budget(self):
        self.memory.config['semantic_fallback'] = True
        lexical = [self.entry(f'lex-{i}') for i in range(5)]
        semantic = self.entry('semantic')
        body = 'Renew the original lease claim.'
        cap = []
        def select(config, schema, prompt, request, timeout=8):
            if schema is hook.PREVIEW_SCHEMA:
                first_body_request = dict(project=str(self.root), request=self.event['prompt'],
                                          workstream=None, startup=False,
                                          candidates=[dict(index=0, body=body, source_extent='full_body')])
                cap.append(len(hook.encoded(request).encode()) + len(hook.encoded(first_body_request).encode()))
                hook.SELECTOR_INPUT_BYTES = cap[0]
                return dict(structured_output=dict(indices=[0, 5]))
            return dict(structured_output=dict(index=0))
        with patch.object(hook, 'SELECTOR_INPUT_BYTES', 24000), \
             patch.object(self.memory, 'search', side_effect=[dict(index=lexical, credits_remaining=4),
                 dict(index=[semantic], credits_remaining=4, discovery=dict(state='ready'))]), \
             patch.object(self.memory, 'call', side_effect=[self.pulled('lex-0', body),
                                                         self.pulled('semantic', body)]), \
             patch.object(hook, 'select_json', side_effect=select):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn(body, result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['last_recall']['selector_input_bytes'], cap[0])
        self.assertEqual(state['last_recall']['rejected']['selector_input_budget'], 1)
        self.assertEqual(state['last_recall']['shortlist_candidates'], 1)

    def test_named_startup_status_noise_needs_applicability_verification(self):
        self.memory.config['semantic_fallback'] = True
        entries = [self.entry('status', 'fixture: release status') | {'kind': 'decision'},
                   self.entry('direction', 'fixture: durable direction') | {'kind': 'decision'}]
        event = dict(self.event, hook_event_name='SessionStart', prompt='', workstream='Database validation')
        with patch.object(self.memory, 'search', return_value=dict(index=entries)) as search, \
             patch.object(self.memory, 'call', side_effect=[
                 self.pulled('status', 'fixture: release status only'),
                 self.pulled('direction', 'fixture: use disposable databases for tests')]), \
             patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=1))) as model:
            state = {}
            result = hook.recall(self.memory, event, state)
        output = result['hookSpecificOutput']['additionalContext']
        self.assertIn('disposable databases', output)
        self.assertNotIn('release status only', output)
        self.assertEqual(state['seen'], {'direction': 1})
        self.assertEqual(search.call_count, 1)
        self.assertTrue(model.call_args.args[3]['startup'])

    def test_selector_input_budget_omits_whole_candidates_and_reports_extent(self):
        self.memory.config['semantic_fallback'] = True
        entries = [self.entry('first'), self.entry('second')]
        prompt = 'recover lease expiry'
        first_body = 'Renew the original lease claim. ' * 30
        second_body = 'A different lease status note. ' * 30
        event = dict(self.event, prompt=prompt)
        base = dict(project=str(self.root), request=prompt, workstream=None, startup=False, candidates=[])
        first_view = dict(index=0, body=first_body, source_extent='full_body')
        cap = len(hook.encoded(dict(base, candidates=[first_view])).encode())
        with patch.object(hook, 'SELECTOR_INPUT_BYTES', cap), \
             patch.object(self.memory, 'search', side_effect=[dict(index=entries, selected=[dict(body='required')]),
                 dict(index=[], discovery=dict(state='unavailable'))]), \
             patch.object(self.memory, 'call', side_effect=[self.pulled('first', first_body),
                                                         self.pulled('second', second_body)]), \
             patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))) as model:
            state = {}
            result = hook.recall(self.memory, event, state)
        output = result['hookSpecificOutput']['additionalContext']
        self.assertIn(first_body, output)
        self.assertIn('required', output)
        self.assertNotIn(second_body, output)
        self.assertEqual(state['seen'], {'first': 1})
        self.assertEqual(state['last_recall']['rejected']['selector_input_budget'], 1)
        self.assertEqual(state['last_recall']['selector_input_bytes'], cap)
        self.assertEqual(state['last_recall']['shortlist_source_extents'], dict(full_body=1, partial_span=0))
        self.assertEqual(len(model.call_args.args[3]['candidates']), 1)

    def test_selector_allowance_starts_after_pulls_but_never_extends_hook_deadline(self):
        self.memory.config['semantic_fallback'] = True
        entry = self.entry('candidate')
        lexical = dict(index=[entry], selected=[dict(body='required')])
        semantic = dict(index=[], discovery=dict(state='unavailable'))
        clock = [0.0]
        def slow_pull(*args):
            clock[0] += 6.0
            return self.pulled('candidate', 'Renew the original lease claim.')
        with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.memory, 'search', side_effect=[lexical, semantic]), \
             patch.object(hook, 'fitting_candidate', side_effect=slow_pull), \
             patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))) as model:
            state = {}
            hook.recall(self.memory, self.event, state)
        self.assertEqual(model.call_args.kwargs['timeout'], 5.0)
        self.assertEqual(state['last_recall']['pull_seconds'], 6.0)
        self.assertEqual(state['last_recall']['elapsed_seconds'], 6.0)

        clock[0] = 0.0
        def exhausted_pull(*args):
            clock[0] += hook.RECALL_SECONDS + 0.1
            return self.pulled('candidate', 'Renew the original lease claim.')
        with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.memory, 'search', side_effect=[lexical, semantic]), \
             patch.object(hook, 'fitting_candidate', side_effect=exhausted_pull), \
             patch.object(hook, 'select_json') as model:
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertIn('required', result['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('Renew the original lease', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['discovery'], 'verification_unavailable')
        model.assert_not_called()

    def test_real_selector_timeout_keeps_required_context(self):
        self.memory.config['semantic_fallback'] = True
        with tempfile.TemporaryDirectory() as tmp:
            sleeper = Path(tmp) / 'sleeping-selector'
            sleeper.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(2)\n')
            sleeper.chmod(0o700)
            self.memory.config['claude'] = str(sleeper)
            entry = self.entry('candidate')
            with patch.object(hook, 'SEMANTIC_MODEL_SECONDS', 0.05), \
                 patch.object(self.memory, 'search', side_effect=[
                     dict(index=[entry], selected=[dict(body='required instruction')]),
                     dict(index=[], discovery=dict(state='unavailable'))]), \
                 patch.object(self.memory, 'call', return_value=self.pulled('candidate', 'Renew the original lease claim.')):
                state = {}
                result = hook.recall(self.memory, self.event, state)
        output = result['hookSpecificOutput']['additionalContext']
        self.assertIn('required instruction', output)
        self.assertNotIn('Renew the original lease', output)
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['discovery'], 'verification_unavailable')
        self.assertEqual(state['last_recall']['rejected']['verification_timeout'], 1)

    def test_span_rejects_stale_version_changed_bytes_and_ineligible_class(self):
        body = 'Recover lease expiry.'
        entry = self.entry('saved') | dict(class_='A', body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                                          match_span=dict(offset=0, length=len(body)))
        entry['class'] = entry.pop('class_')
        valid = dict(selection=dict(record=dict(record_id='saved', version=1, body='', **{'class': 'A'})),
                     span=dict(offset=0, end=len(body), total_bytes=len(body), body=body,
                               sha256=hashlib.sha256(body.encode()).hexdigest(),
                               source_sha256=entry['body_sha256']))
        with patch.object(self.memory, 'call', return_value=valid):
            self.assertEqual(hook.current_span_pull(self.memory, entry, time.monotonic() + 2)['source_extent'],
                             'partial_span')
        stale = json.loads(json.dumps(valid))
        stale['selection']['record']['version'] = 2
        changed = json.loads(json.dumps(valid))
        changed['span']['body'] = 'Changed lease expiry.'
        wrong_source = json.loads(json.dumps(valid))
        wrong_source['span']['source_sha256'] = '0' * 64
        for response in (stale, changed, wrong_source):
            with self.subTest(response=response), patch.object(self.memory, 'call', return_value=response):
                with self.assertRaises(hook.HookError):
                    hook.current_span_pull(self.memory, entry, time.monotonic() + 2)
        for blocked in (entry | {'class': 'C'}, entry | {'conflicts': [dict(position='other')]},
                        entry | {'match_span': dict(offset=0, length=5000)}):
            with self.subTest(blocked=blocked), patch.object(self.memory, 'call') as call:
                with self.assertRaises(hook.HookError):
                    hook.current_span_pull(self.memory, blocked, time.monotonic() + 2)
                call.assert_not_called()

    def test_full_body_hash_must_match_preview(self):
        entry = self.entry('saved') | {'body_sha256': hashlib.sha256(b'original').hexdigest()}
        with patch.object(self.memory, 'call', return_value=self.pulled('saved', 'changed')):
            with self.assertRaisesRegex(hook.HookError, 'hash changed'):
                hook.current_pull(self.memory, entry, time.monotonic() + 2)

    def test_partial_span_that_exceeds_context_budget_is_not_injected(self):
        self.memory.config['context_bytes'] = 1000
        entry = self.entry('saved', 'recover lease expiry') | dict(match_span=dict(offset=0, length=3500))
        prefix = 'recover lease expiry '
        body = prefix + 'x' * (3500 - len(prefix))
        whole = self.pulled('saved', body + 'y' * 10000)
        span = dict(selection=dict(record=dict(record_id='saved', version=1, body='', **{'class': 'A'})),
                    span=dict(offset=0, end=len(body), total_bytes=len(body) + 10000, body=body,
                              sha256=hashlib.sha256(body.encode()).hexdigest(),
                              source_sha256=hashlib.sha256((body + 'y' * 10000).encode()).hexdigest()))
        with patch.object(self.memory, 'search', return_value=dict(index=[entry])), \
             patch.object(self.memory, 'call', side_effect=[whole, span]):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        self.assertNotIn('source_extent', result.get('hookSpecificOutput', {}).get('additionalContext', ''))
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['rejected']['context_budget'], 1)

    def test_buried_body_is_inspected_despite_irrelevant_preview(self):
        entries = [self.entry('weak'), self.entry('useful')]
        bodies = [self.pulled('weak', 'Unrelated topic.'),
                  self.pulled('useful', 'x' * 700 + '\nRecover lease expiry by renewing the original claim.')]
        with patch.object(self.memory, 'search', return_value=dict(index=entries)) as search, \
             patch.object(self.memory, 'call', side_effect=bodies):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn('renewing the original claim', text)
        self.assertNotIn('Unrelated topic.', text)
        self.assertEqual(state['seen'], {'useful': 1})
        self.assertEqual(state['last_recall']['inspected'], 2)
        self.assertEqual(search.call_args.kwargs['room'], hook.RECALL_SEARCH_ROOM)
        self.assertLessEqual(len(text.encode()), 9500)

    def test_oversized_and_changed_first_bodies_do_not_hide_later_candidate(self):
        entries = [self.entry('first', 'recover lease expiry'), self.entry('second', 'recover lease expiry')]
        for first in (self.pulled('first', 'recover lease expiry ' + 'x' * 12000),
                      self.pulled('first', 'recover lease expiry', version=2)):
            with self.subTest(first=first['selection']['record']['version'], size=len(first['selection']['record']['body'])):
                with patch.object(self.memory, 'search', return_value=dict(index=entries)), \
                     patch.object(self.memory, 'call', side_effect=[first, self.pulled('second', 'Recover lease expiry safely.')]):
                    state = {}
                    result = hook.recall(self.memory, self.event, state)
                text = result['hookSpecificOutput']['additionalContext']
                self.assertIn('Recover lease expiry safely.', text)
                self.assertNotIn('x' * 100, text)
                self.assertEqual(state['seen'], {'second': 1})
                self.assertLessEqual(len(text.encode()), 9500)

    def test_no_answer_neighbor_is_omitted_and_does_not_mark_seen(self):
        entry = self.entry('neighbor')
        state = {}
        with patch.object(self.memory, 'search', return_value=dict(index=[entry])), \
             patch.object(self.memory, 'call', return_value=self.pulled('neighbor', 'An unrelated topic.')):
            self.assertEqual(hook.recall(self.memory, self.event, state), {})
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['rejected']['not_relevant'], 1)

    def test_elapsed_recall_deadline_prevents_late_optional_pull(self):
        entry = self.entry('candidate', 'recover lease expiry')
        with patch.object(hook.time, 'monotonic', side_effect=[0, 0, hook.RECALL_SECONDS + 1,
                                                              hook.RECALL_SECONDS + 1]), \
             patch.object(self.memory, 'search', return_value=dict(index=[entry])), \
             patch.object(self.memory, 'call') as pull:
            state = {}
            result = hook.recall(self.memory, self.event, state)
        pull.assert_not_called()
        self.assertIn('search again', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {})

class CheckpointCurrentnessTests(unittest.TestCase):
    def test_completion_revises_only_a_supplied_topic_and_preserves_compare_and_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(cairn='unused', socket='unused', token_file='unused', repo='fixture'), 'checkpoint')
            event = dict(cwd=tmp, workstream='Migration')
            old = dict(record_id='existing', version=7, body=hook.workstream_prefix(event)+'Migration\n\nNext: tests and deploy.')
            selection = dict(checkpoint='Tests passed and deployment verified. Entire migration complete.',
                             checkpoint_state='complete', workstream='Migration', memories=[])
            writes = hook.selected_writes(memory, event, selection, old, [])
            self.assertEqual(len(writes), 1)
            self.assertIs(writes[0][2], old)
            self.assertIn('Status: complete', writes[0][0])
            self.assertNotIn('Next: tests', writes[0][0])
            with patch.object(memory, 'call', return_value=dict(record_id='existing')) as call:
                hook.save_note(memory, *writes[0][:2], writes[0][2])
            self.assertEqual(call.call_args.kwargs['payload']['expected_version'], 7)
            with self.assertRaises(hook.HookError):
                hook.selected_writes(memory, event, selection, None, [])
            expanded = hook.selected_writes(memory, event, dict(selection, checkpoint='x'*3500), old, [])
            self.assertEqual(len(expanded), 1)
            with self.assertRaises(hook.HookError):
                hook.selected_writes(memory, event, dict(selection, checkpoint='x'*(hook.CHECKPOINT_BYTES+1)), old, [])


if __name__ == "__main__":
    unittest.main()
