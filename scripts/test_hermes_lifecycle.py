"""Hermes boundary regressions; native execution is check_hermes_lifecycle.py."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

from test_claude_lifecycle import ROOT, hook, module


def adapter():
    # Minimal host contracts keep these tests runnable without installing Hermes.
    # The native fixture verifies the real provider, summary classifier and callbacks.
    modules = {
        'agent.memory_provider': types.SimpleNamespace(MemoryProvider=object),
        'agent.context_compressor': types.SimpleNamespace(is_compaction_summary_message=lambda m: m.get('_summary', False)),
        'hermes_constants': types.SimpleNamespace(get_hermes_home=Path.home),
        'tools.terminal_tool': types.SimpleNamespace(get_session_cwd=lambda _: None, resolve_task_overrides=lambda _: {}),
        'hermes_test_adapter.memory': hook,
    }
    spec = importlib.util.spec_from_file_location('hermes_test_adapter', ROOT / 'integrations/hermes/__init__.py', submodule_search_locations=[str(ROOT / "integrations/hermes")])
    module = importlib.util.module_from_spec(spec)
    modules["hermes_test_adapter"] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin = adapter()


class HermesBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        home_patch = patch.object(plugin,'get_hermes_home',return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        (self.home / 'cairn-lifecycle.json').write_text(json.dumps(dict(script='memory.py',engine_config='engine.json')))
        self.ctx = Mock()
        self.ctx.register_hook.side_effect = lambda *args: Mock()
        self.ctx.register_middleware.side_effect = lambda *args: Mock()
        self.provider = plugin.CairnProvider(self.ctx)
        self.provider.initialize('20260913_120000_a/b', hermes_home=str(self.home), platform='slack')
        cwd_patch = patch.object(self.provider,'cwd',return_value=str(self.home))
        cwd_patch.start()
        self.addCleanup(cwd_patch.stop)
        self.addCleanup(self.provider.shutdown)

    def test_original_dialogue_excludes_sidecars_tools_reasoning_and_summaries(self):
        actual = plugin.dialogue([
            dict(role='user',content='Keep PostgreSQL.',api_content='injected secret'),
            dict(role='assistant',content='tool narration',tool_calls=[{}]),
            dict(role='tool',content='raw tool payload'),
            dict(role='assistant',content='Summary',_summary=True),
            dict(role='assistant',content=[dict(type='thinking',thinking='hidden'),dict(type='text',text='Agreed.')])])
        self.assertEqual(actual,[dict(role='user',text='Keep PostgreSQL.'),dict(role='assistant',text='Agreed.')])
        self.assertLessEqual(len(json.dumps(plugin.dialogue([dict(role='user',content='é'*30000)]),ensure_ascii=False,separators=(',',':')).encode()),24000)

    def test_request_copies_do_not_accumulate_or_edit_original_history(self):
        self.provider.context = 'Cairn lifecycle memory: remembered'
        original = dict(messages=[dict(role='user',content='hello')])
        first = self.provider.request(original,session_id=self.provider.session_id)
        second = self.provider.request(original,session_id=self.provider.session_id)
        self.assertEqual(first,second)
        self.assertEqual(len(first['request']['messages']),2)
        self.assertEqual(original,dict(messages=[dict(role='user',content='hello')]))
        self.assertIsNone(self.provider.request(original,session_id='other-conversation'))

    def test_owner_turn_delivery_is_once_and_refreshes_next_turn(self):
        calls = []
        def invoke(event, **fields):
            calls.append((event,fields))
            return dict(hookSpecificOutput=dict(additionalContext=f'version-{len(calls)}'))
        with patch.object(self.provider,'invoke',side_effect=invoke):
            for turn in ('one','one','two'):
                self.provider.pre_turn(session_id=self.provider.session_id,turn_id=turn,user_message='continue')
        self.assertEqual(len(calls),2)
        self.assertEqual(self.provider.context,'version-2')
        self.assertEqual(calls[0][1]['source'],'resume')
        self.assertEqual(calls[0][1]['retained_record_ids'],[])

    def test_opt_out_and_child_run_do_not_recall(self):
        with patch.object(self.provider,'invoke') as invoke, patch.dict(os.environ,CAIRN_LIFECYCLE_DISABLED='1'):
            self.provider.pre_turn(session_id=self.provider.session_id,turn_id='disabled',user_message='continue')
            invoke.assert_not_called()
        with patch.object(self.provider,'invoke') as invoke:
            self.provider.pre_turn(session_id=self.provider.session_id,turn_id='child',parent_session_id='parent')
            invoke.assert_not_called()

    def test_failed_compaction_remains_retryable_and_diagnostic_has_no_payload(self):
        with patch.object(plugin,'run_engine',return_value=types.SimpleNamespace(returncode=1,stderr='PRIVATE',stdout='PRIVATE')) as run:
            with self.assertLogs(plugin.logger,level='WARNING') as logs:
                self.provider.on_pre_compress([dict(role='user',content='PRIVATE')])
                self.provider.on_session_end([dict(role='user',content='PRIVATE')])
        self.assertEqual(run.call_count,2)
        self.assertNotIn('PRIVATE',str(logs.output))

    def test_status_tracks_failure_then_retry_without_stale_recall_overwrite(self):
        original = [dict(role='user', content='PRIVATE selected work')]
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=1, stdout='PRIVATE')):
            with self.assertLogs(plugin.logger, level='WARNING'):
                self.provider.post_turn(session_id=self.provider.session_id, conversation_history=original)
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertTrue(record['pending'])
        self.assertEqual(record['last_capture']['outcome'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(record))
        response = dict(cairn_status=dict(last_recall=dict(outcome='empty', records=[]),
                                         last_capture=dict(outcome='saved', records=['obsolete'])))
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            self.provider.invoke('UserPromptSubmit', prompt='continue')
        self.assertEqual(plugin.read_control(self.home, self.provider.control_key)['last_capture']['outcome'], 'failed')
        response = dict(cairn_status=dict(last_capture=dict(outcome='saved', records=['confirmed'])))
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            self.provider.pre_command(command='cairn', cairn_retry=True, surface='gateway',
                                      session_key=self.provider.gateway_session_key or 'unmatched')
            self.assertTrue(self.provider.capture_pending)
            self.provider.pre_command(command='cairn', cairn_retry=True, surface='cli', session_key=self.provider.session_id)
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertFalse(record['pending'])
        self.assertEqual(record['last_capture']['records'], ['confirmed'])

    def test_status_storage_failure_does_not_turn_success_into_failed_capture(self):
        with patch.object(plugin, 'change_control', side_effect=OSError('PRIVATE')), patch.object(
                plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=0,
                stdout=json.dumps(dict(cairn_status=dict(last_capture=dict(outcome='saved')))))):
            with self.assertLogs(plugin.logger, level='WARNING') as logs:
                result = self.provider.invoke('SessionEnd', messages=[])
                self.provider.capture_result(result)
        self.assertIsNotNone(result)
        self.assertFalse(self.provider.capture_pending)
        self.assertNotIn('PRIVATE', str(logs.output))

    def test_timeout_kills_engine_and_selector_child(self):
        pidfile = self.home / 'child.pid'
        # The grandchild PID is published atomically so a loaded host never reads a partial file.
        command = [sys.executable, '-c', 'import os,subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); t=pathlib.Path(sys.argv[1]+".tmp"); t.write_text(str(p.pid)); os.replace(t,sys.argv[1]); time.sleep(60)', str(pidfile)]
        started = time.monotonic()
        with self.assertRaises(plugin.subprocess.TimeoutExpired):
            plugin.run_engine(command,{},timeout=1)
        self.assertLess(time.monotonic()-started,6)
        self.assertTrue(pidfile.exists())
        status = Path('/proc') / pidfile.read_text() / 'stat'
        # SIGKILL to the group is asynchronous: the reparented selector child can
        # still be scheduled briefly after run_engine returns. Require it to die
        # promptly rather than at the exact instant of return.
        deadline = time.monotonic() + 3
        while True:
            try:
                state = status.read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                return  # Reaped between the lookup and the read (open or read can fail).
            if state == 'Z' or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        self.assertEqual(state, 'Z', 'selector child is still running')

    def test_profile_guard_prevents_same_session_label_crossing_homes(self):
        self.provider.context='private profile context'
        with patch.object(plugin,'get_hermes_home',return_value=self.home/'other-profile'):
            self.assertIsNone(self.provider.request(dict(messages=[]),session_id=self.provider.session_id))

    def test_opted_out_dialogue_is_not_captured_after_reenabling(self):
        marker=self.home/'.cairn-no-memory'
        marker.touch()
        private=[dict(role='user',content='Do not remember this turn.')]
        with patch.object(self.provider,'invoke') as invoke:
            self.provider.post_turn(session_id=self.provider.session_id,conversation_history=private)
            self.provider.on_pre_compress(private)
            marker.unlink()
            self.provider.on_session_end(private)
            invoke.assert_not_called()
        self.assertEqual(self.provider.last_dialogue,[])

    def test_capture_is_synchronous_and_compaction_exit_coalesces(self):
        events=[]
        with patch.object(self.provider,'invoke',side_effect=lambda event,**_: events.append(event) or {}):
            self.provider.post_turn(session_id=self.provider.session_id,conversation_history=[dict(role='assistant',content='Selected guidance')])
            self.assertEqual(events,['SessionEnd'])
            self.provider.on_pre_compress([])
            self.provider.on_session_end([])
            self.assertEqual(events,['SessionEnd','PreCompact'])

    def test_shutdown_releases_registrations_and_session_switch_keeps_identity(self):
        self.provider.context='old context'
        self.provider.on_session_switch('new/slack:thread')
        with patch.object(self.provider,'invoke',return_value={}):
            self.provider.pre_turn(session_id='new/slack:thread',turn_id='new')
        self.assertEqual(self.provider.session_id,'new/slack:thread')
        self.assertEqual(self.provider.context,'')
        handles=list(self.provider.registrations)
        self.provider.shutdown()
        self.provider.shutdown()
        for handle in handles:
            handle.dispose.assert_called_once()

    def test_hermes_filename_key_preserves_original_scope_and_blocks_path_escape(self):
        config=dict(cairn='cairn',socket='socket',token_file='token',repo='shared',harness='hermes',state_dir=str(self.home/'state'))
        event=dict(hook_event_name='UserPromptSubmit',session_id='../../slack-thread',cwd=str(self.home),prompt='validation')
        with patch.object(hook,'recall',return_value={}) as recall:
            hook.handle(config,event)
        memory=recall.call_args.args[0]
        self.assertIn('hermes/../../slack-thread',memory.scope)
        self.assertEqual(len(list((self.home/'state').glob('*.json'))),1)
        self.assertFalse((self.home/'slack-thread.json').exists())

    def test_delayed_old_boundary_does_not_rebind_current_turn(self):
        current = [dict(role='user',content='Current owner correction.')]
        self.provider.on_session_switch('current')
        with patch.object(self.provider,'invoke',return_value={}) as invoke:
            self.provider.pre_turn(session_id='current',turn_id='current-turn',conversation_history=current)
            self.provider.on_session_switch('stale-queued-new')
            self.provider.on_session_end([dict(role='user',content='Previous work.')])
            self.assertEqual(invoke.call_count,1)
            self.provider.post_turn(session_id='current',conversation_history=current)
            self.assertEqual(self.provider.session_id,'current')

    def test_bare_resume_can_recall_project_handoff_without_session_uuid(self):
        event=dict(hook_event_name='UserPromptSubmit',session_id='fresh',cwd=str(self.home),prompt='continue',source='resume')
        intent=hook.retrieval_intent(event,{})
        self.assertTrue(hook.relevant(dict(summary=hook.workstream_prefix(event)+'PostgreSQL validation'),intent))
        self.assertFalse(hook.relevant(dict(summary='Unrelated weather facts'),intent))


    def test_failed_capture_is_not_silently_superseded_by_next_turn(self):
        first = [dict(role='user', content='PRIVATE selected first turn')]
        second = [dict(role='user', content='PRIVATE selected second turn')]
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=1, stdout='')):
            with self.assertLogs(plugin.logger, level='WARNING'):
                self.provider.post_turn(session_id=self.provider.session_id, conversation_history=first)
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertTrue(record['pending'])
        self.assertEqual(record['pending_digest'], plugin.dialogue_digest(plugin.dialogue(first)))
        with patch.object(self.provider, 'invoke', return_value={}) as invoke:
            self.provider.pre_turn(session_id=self.provider.session_id, turn_id='next', user_message='next question',
                                   conversation_history=second)
            self.assertEqual(invoke.call_count, 1)
            self.provider.post_turn(session_id=self.provider.session_id, conversation_history=second)
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertEqual(record['pending_digest'], plugin.dialogue_digest(plugin.dialogue(second)))
        self.assertFalse(record['pending'])

    def test_live_retry_attempts_the_pinned_failed_snapshot(self):
        first = plugin.dialogue([dict(role='user', content='PRIVATE selected first turn')])
        second = plugin.dialogue([dict(role='user', content='PRIVATE selected second turn')])
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=1, stdout='')):
            with self.assertLogs(plugin.logger, level='WARNING'):
                self.provider.post_turn(session_id=self.provider.session_id,
                                        conversation_history=[dict(role='user', content='PRIVATE selected first turn')])
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertTrue(record['pending'])
        self.assertEqual(record['pending_digest'], plugin.dialogue_digest(first))
        self.provider.pre_turn(session_id=self.provider.session_id, turn_id='next', user_message='next question',
                               conversation_history=[dict(role='user', content='PRIVATE selected second turn')])
        self.assertEqual(plugin.dialogue_digest(self.provider.last_dialogue), plugin.dialogue_digest(second))
        with patch.object(self.provider, 'invoke', return_value={}) as invoke:
            self.provider.pre_command(command='cairn', cairn_retry=True, surface='cli',
                                      session_key=self.provider.session_id)
            self.assertEqual(invoke.call_args.kwargs['messages'], first)
        record = plugin.read_control(self.home, self.provider.control_key)
        self.assertFalse(record['pending'])

    def test_cli_process_identity_does_not_reuse_pid_controls(self):
        controls = module('hermes_controls_identity_test', ROOT/'integrations/hermes/controls.py')
        boot = '11111111-1111-1111-1111-111111111111'
        def identity(start, boot_id=boot):
            status = '42 (worker with ) name) ' + ' '.join(['S'] + ['0'] * 18 + [str(start)])
            with patch.object(controls.os, 'getpid', return_value=42), patch.object(
                    controls.Path, 'read_text', side_effect=[status, boot_id]):
                return controls.conversation_key('cli')
        first = identity(100)
        self.assertEqual(first, identity(100))
        self.assertNotEqual(first, identity(101))
        self.assertNotEqual(first, identity(100, '22222222-2222-2222-2222-222222222222'))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            controls.set_context(home, first, [tmp, 'Old process'])
            self.assertEqual(controls.read_control(home, identity(101)), {})
            self.assertEqual(controls.read_control(home, 'cli/42'), {})

    def test_binding_survives_reload_isolates_threads_and_refuses_pending_change(self):
        controls = module('hermes_controls_test',ROOT/'integrations/hermes/controls.py')
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            a = controls.conversation_key('slack', 'team/channel/thread-a')
            b = controls.conversation_key('slack', 'team/channel/thread-b')
            self.assertIn('Migration', controls.set_context(home, a, [tmp, 'Migration']))
            self.assertEqual(controls.read_control(home,a)['binding']['workstream'],'Migration')
            self.assertEqual(controls.read_control(home,b),{})
            with controls.change_control(home,a) as state:
                state['pending']=True
            self.assertIn('unconfirmed',controls.set_context(home,a,['clear']))
            self.assertEqual(controls.read_control(home,a)['binding']['workstream'],'Migration')
            with controls.change_control(home,a) as state:
                state['pending']=False
            controls.set_context(home,a,['clear'])
            self.assertIsNone(controls.read_control(home,a)['binding'])

    def test_explicit_project_overrides_terminal_context_and_topic_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'chosen-project';project.mkdir()
            elsewhere=root/'terminal';elsewhere.mkdir()
            event=dict(cwd=str(elsewhere),project_path=str(project),workstream='Database migration',
                       hook_event_name='UserPromptSubmit',prompt='continue')
            intent=hook.retrieval_intent(event,{})
            self.assertEqual(intent['project'],'chosen-project')
            self.assertIn('Handoff: chosen-project / Database migration',intent['query'])
            self.assertIsNone(hook.file_hint(str(elsewhere/'other.go'),str(elsewhere),project))
            selected=dict(checkpoint='Pending tests.',workstream='Another topic',memories=[])
            with self.assertRaisesRegex(hook.HookError,'explicitly chosen'):
                hook.selected_writes(Mock(),event,selected,None,[])

    def test_explicit_context_captures_current_turn_without_prior_project_dialogue(self):
        provider=plugin.CairnProvider(Mock())
        provider.turn_capture=True
        messages=[dict(role='user',content='Old project secret topic'),dict(role='assistant',content='Old answer'),
                  dict(role='user',content='Current migration'),dict(role='assistant',content='Tests remain')]
        self.assertEqual(provider.selected_dialogue(messages),[dict(role='user',text='Current migration'),dict(role='assistant',text='Tests remain')])


if __name__=='__main__':
    unittest.main()
