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

from test_claude_lifecycle import ROOT, hook


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
    spec = importlib.util.spec_from_file_location('hermes_test_adapter', ROOT / 'integrations/hermes/__init__.py', submodule_search_locations=[])
    module = importlib.util.module_from_spec(spec)
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

    def test_timeout_kills_engine_and_selector_child(self):
        pidfile = self.home / 'child.pid'
        command = [sys.executable, '-c', 'import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)', str(pidfile)]
        started = time.monotonic()
        with self.assertRaises(plugin.subprocess.TimeoutExpired):
            plugin.run_engine(command,{},timeout=.3)
        self.assertLess(time.monotonic()-started,3)
        self.assertTrue(pidfile.exists())
        status = Path('/proc') / pidfile.read_text() / 'stat'
        if status.exists():
            self.assertEqual(status.read_text().split()[2], 'Z', 'selector child is still running')

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


if __name__=='__main__':
    unittest.main()
