"""Agent-directed recall at the public hook and installer boundaries."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook
from test_claude_lifecycle import module, ROOT


class AgentToolRecallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '.git').mkdir()
        self.config = dict(cairn='fixture', claude='forbidden-selector', socket='fixture',
                           token_file='fixture', repo='fixture', state_dir=str(self.root / 'state'),
                           context_bytes=9500, semantic_fallback=True, recall_mode='agent_tools')
        self.event = dict(hook_event_name='UserPromptSubmit', cwd=str(self.root),
                          session_id='caed9473-b01a-41e7-95ce-c3c1f28d66b3', prompt='Repair lease expiry')
        self.required = [dict(mandatory=True, record=dict(record_id='required', version=1,
                                                        body='Preserve the current authority boundary.'))]
        self.commands = []

    def transport(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(command[:2], ['fixture', 'agent'], 'no hosted selector may run')
        self.assertEqual(command[6], 'search', 'no optional pull may run')
        self.assertNotIn('--semantic', command, 'no semantic discovery may run')
        result = dict(status='READY', destination=dict(name='hosted'), selected=self.required,
                      index=[dict(record_id='optional', version=3, summary='Repair lease expiry: unwanted preview',
                                  pull_arguments=dict(receipt_id='receipt', handle='optional', request_id='pull'))])
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=result)), '')

    def invoke(self, **event):
        with patch.object(hook, 'bounded_command', side_effect=self.transport):
            result = hook.handle(self.config, dict(self.event, **event))
        state = json.loads((Path(self.config['state_dir']) / (self.event['session_id'] + '.json')).read_text())
        return result, state

    def test_prompt_delegates_to_native_tools_without_optional_memory_or_seen_credit(self):
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.required[0]['record']['body'], text)
        self.assertIn('cairn_search', text)
        self.assertIn('cairn_pull', text)
        self.assertNotIn('unwanted preview', text)
        self.assertNotIn('"record_id":"optional"', text)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['outcome'], 'delegated')
        self.assertEqual(state['last_recall']['records'], [])
        self.assertEqual(state['last_recall']['inspected'], 0)

        self.assertIn('9500 UTF-8 bytes', text)
        self.assertNotIn('{budget}', text)
        # The OpenCode adapter parses from this marker to the end of the text.
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view, dict(selected=self.required, index=[]))

    def test_resume_compact_and_explicit_workstream_delegate_without_optional_calls(self):
        for event in (dict(hook_event_name='SessionStart', source='resume', prompt=''),
                      dict(hook_event_name='SessionStart', source='compact', prompt=''),
                      dict(hook_event_name='SessionStart', source='startup', prompt='', workstream='Lease repair')):
            with self.subTest(event=event):
                self.commands.clear()
                result, state = self.invoke(**event)
                self.assertIn('cairn_search', result['hookSpecificOutput']['additionalContext'])
                self.assertEqual(state['last_recall']['outcome'], 'delegated')
                self.assertEqual(len(self.commands), 1)
                self.assertEqual(state['seen'], {})

    def test_explicit_and_absent_ambient_keep_optional_body_delivery(self):
        self.config['semantic_fallback'] = False
        def transport(command, **kwargs):
            if command[6] == 'search':
                return self.transport(command, **kwargs)
            self.assertEqual(command[6], 'pull')
            result = dict(selection=dict(record=dict(record_id='optional', version=3,
                                                     body='Repair lease expiry with current generation checks.')))
            return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=result)), '')
        outputs = []
        for explicit in (False, True):
            self.config.pop('recall_mode', None)
            if explicit:
                self.config['recall_mode'] = 'ambient'
            with patch.object(hook, 'bounded_command', side_effect=transport):
                outputs.append(hook.handle(self.config, dict(self.event, hook_event_name='SessionStart', source='resume')))
        self.assertEqual(outputs[0], outputs[1])
        self.assertIn('current generation checks', outputs[0]['hookSpecificOutput']['additionalContext'])

    def test_taskless_start_and_wake_keep_existing_deferral(self):
        from test_startup_recall import notification
        for event, reason in ((dict(hook_event_name='SessionStart', source='startup', prompt=''), 'taskless_startup'),
                              (dict(prompt=notification()), 'taskless_notification')):
            with self.subTest(reason=reason):
                result, state = self.invoke(**event)
                self.assertNotIn('cairn_search', result['hookSpecificOutput']['additionalContext'])
                self.assertEqual(state['last_recall']['optional_deferred'], reason)
                self.assertNotEqual(state['last_recall']['outcome'], 'delegated')

    def test_no_room_for_cue_preserves_whole_required_context(self):
        self.required[0]['record']['body'] = '日' * 200
        self.config['context_bytes'] = len(hook.render_recall(self.required, []).encode())
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.required[0]['record']['body'], text)
        self.assertNotIn('cairn_search', text)
        self.assertEqual(len(text.encode()), self.config['context_bytes'])
        self.assertEqual(state['last_recall']['outcome'], 'delegation_omitted')
        self.assertEqual(state['last_recall']['rejected'], {'delegation_context_budget': 1})

    def test_oversize_required_context_refuses_whole(self):
        self.required[0]['record']['body'] = '日' * 4000
        with self.assertRaisesRegex(hook.HookError, 'no partial instructions'):
            self.invoke()

    def test_invalid_modes_fail_before_retrieval_and_replace_old_status(self):
        self.invoke()
        for mode in ('typo', '', None, True, [], {}):
            with self.subTest(mode=mode):
                self.config['recall_mode'] = mode
                self.commands.clear()
                with self.assertRaisesRegex(hook.HookError, 'recall_mode'):
                    self.invoke()
                self.assertEqual(self.commands, [])
                state = json.loads((Path(self.config['state_dir']) / (self.event['session_id'] + '.json')).read_text())
                self.assertEqual(state['last_recall']['outcome'], 'failed')
                self.assertEqual(state['last_recall']['error_type'], 'HookError')


class RecallModeInstallerTests(unittest.TestCase):
    def test_all_installers_preserve_mode_when_omitted_and_allow_explicit_ambient(self):
        for name in ('claude', 'codex', 'opencode', 'hermes'):
            with self.subTest(installer=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = dict(cairn='fixture', claude='fixture', socket='fixture', token_file='fixture', repo='fixture')
                filename = 'install-hermes-integration.py' if name == 'hermes' else f'install-{name}-hooks.py'
                installer = module('mode_' + name, ROOT / 'scripts' / filename)
                config_path = root / ('cairn/engine.json' if name == 'hermes' else 'dest/config.json')
                if name in ('claude', 'codex'):
                    if name == 'codex':
                        trust = patch.object(installer, 'trust')
                        trust.start()
                        self.addCleanup(trust.stop)
                    def install(**kwargs):
                        installer.install(root / 'settings.json', root / 'dest', config, **kwargs)
                elif name == 'opencode':
                    (root / 'cairn.json').write_text(json.dumps(dict(executable='fixture', socket='fixture', token_file='fixture', repo='fixture')))
                    def install(**kwargs):
                        installer.install(root, root / 'dest', 'fixture', **kwargs)
                else:
                    skill = root / 'SKILL.md'
                    skill.write_text('Selected fixture skill.')
                    def install(**kwargs):
                        installer.install(root, dict(executable='fixture', socket='fixture', token_file='fixture', repo='fixture'),
                                          'fixture', skill, **kwargs)
                install()
                self.assertNotIn('recall_mode', json.loads(config_path.read_text()))
                install(recall_mode='agent_tools')
                install()
                self.assertEqual(json.loads(config_path.read_text())['recall_mode'], 'agent_tools')
                install(recall_mode='ambient')
                self.assertEqual(json.loads(config_path.read_text())['recall_mode'], 'ambient')
                before = {str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}
                with self.assertRaisesRegex(ValueError, 'recall_mode'):
                    install(recall_mode='invalid')
                self.assertEqual({str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}, before)
                stored = json.loads(config_path.read_text())
                stored['recall_mode'] = None
                config_path.write_text(json.dumps(stored))
                before = {str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}
                with self.assertRaisesRegex(ValueError, 'recall_mode'):
                    install()
                self.assertEqual({str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
