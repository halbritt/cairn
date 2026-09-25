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

    def test_weak_matches_omitted_and_delivered_versions_reset_with_context(self):
        memory = hook.Memory(self.config, "session-one")
        entry = dict(record_id="r", version=1, summary="startup failure fix", pull_arguments={})
        state = {}
        event = dict(self.event, hook_event_name="UserPromptSubmit", prompt="startup failure")
        with patch.object(memory, "search", return_value=dict(index=[entry])), \
             patch.object(memory, "call", return_value=dict(selection=dict(record=dict(body="saved fix")))):
            self.assertEqual(hook.recall(memory, dict(event, prompt="unrelated layout"), state), {})
            self.assertIn("saved fix", str(hook.recall(memory, event, state)))
            self.assertEqual(hook.recall(memory, event, state), {})
            entry["version"] = 2
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
        record = dict(body=title + "\n\nTests pending.", kind="note")
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
             patch.object(memory, "call", return_value=dict(selection=dict(record=dict(body="file guidance")))):
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
    def test_paraphrase_requires_full_body_verification_and_preserves_required_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            event = dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='Which durable backend is used here?')
            entry = dict(record_id='saved', version=2, summary='PostgreSQL operational store',
                         pull_arguments=dict(handle='current'))
            lexical = dict(index=[], selected=[dict(body='mandatory first')])
            semantic = dict(index=[entry], selected=[dict(body='mandatory second')], discovery=dict(state='ready'))
            pulled = dict(selection=dict(record=dict(body='Use PostgreSQL for this project.')))
            for verdict in (True, False):
                state = {}
                with patch.object(memory, 'search', side_effect=[dict(lexical), semantic]) as search, \
                     patch.object(memory, 'call', return_value=pulled) as pull, \
                     patch.object(hook, 'select_json', return_value=dict(structured_output=dict(relevant=verdict))) as model:
                    result = hook.recall(memory, event, state)
                text = result['hookSpecificOutput']['additionalContext']
                self.assertIn('mandatory first', text)
                self.assertIn('mandatory second', text)
                self.assertEqual('Use PostgreSQL' in text, verdict)
                self.assertEqual(pull.call_count, 1)
                self.assertTrue(search.call_args.kwargs['semantic'])
                self.assertEqual(model.call_args.kwargs['timeout'], 8)
                self.assertLessEqual(len(text.encode()), hook.CONTEXT_BYTES)
                self.assertEqual(state['last_recall']['discovery'], 'verified' if verdict else 'not_relevant')

    def test_worker_fallback_and_precise_requests_do_not_invoke_relevance_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            for prompt, calls in [('Which durable backend is used?', 2), ('repair core/store.go', 1), ('repair "ExactError"', 1)]:
                state = dict(hints=dict(errors=['ExpiredError'], error_at=0))
                with patch.object(memory, 'search', return_value=dict(index=[], discovery=dict(state='unavailable'))) as search, \
                     patch.object(hook, 'select_json') as model:
                    result = hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt=prompt), state)
                self.assertEqual(result, {})
                self.assertEqual(search.call_count, calls)
                model.assert_not_called()
                self.assertEqual(state['last_recall']['discovery'], 'unavailable' if calls == 2 else 'lexical')

    def test_oversized_combined_mandatory_context_refuses_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = hook.Memory(dict(semantic_fallback=True, cairn='unused', socket='unused',
                                      token_file='unused', repo='fixture'), 'semantic')
            with patch.object(memory, 'search', side_effect=[
                    dict(selected=['a'*6500]), dict(selected=['b'*6500], discovery=dict(state='unavailable'))]):
                with self.assertRaises(hook.HookError):
                    hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=tmp, prompt='durable backend'), {})

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
