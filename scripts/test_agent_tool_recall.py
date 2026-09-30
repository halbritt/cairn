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
        self.entries = [dict(record_id='optional', version=3, summary='Repair lease expiry: candidate preview',
                             summary_span=dict(offset=90, length=39),
                             pull_arguments=dict(receipt_id='receipt', handle='optional', request_id='pull'))]
        self.commands = []

    def transport(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(command[:2], ['fixture', 'agent'], 'no hosted selector may run')
        self.assertEqual(command[6], 'search', 'no optional pull may run')
        self.assertNotIn('--semantic', command, 'no semantic discovery may run')
        result = dict(status='READY', destination=dict(name='hosted'), selected=self.required,
                      index=self.entries, omitted=dict(OPTIONAL_BUDGET=7, NO_LEXICAL_MATCH=2))
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=result)), '')

    def invoke(self, **event):
        with patch.object(hook, 'bounded_command', side_effect=self.transport):
            result = hook.handle(self.config, dict(self.event, **event))
        state = json.loads((Path(self.config['state_dir']) / (self.event['session_id'] + '.json')).read_text())
        return result, state

    def test_prompt_offers_complete_candidates_without_pulls_or_seen_credit(self):
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.required[0]['record']['body'], text)
        self.assertIn('cairn_search', text)
        self.assertIn('cairn_pull', text)
        self.assertIn('unverified candidate previews', text)
        self.assertIn('candidate preview', text)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['outcome'], 'delegated')
        self.assertEqual(state['last_recall']['records'], [])
        self.assertEqual(state['last_recall']['inspected'], 0)
        self.assertEqual(state['last_recall']['candidate_previews'], 1)

        self.assertIn('9500 UTF-8 bytes', text)
        self.assertNotIn('{budget}', text)
        # The OpenCode adapter parses from this marker to the end of the text.
        view = json.loads(text[text.index('{"selected":'):])
        remaining = view.pop('remaining_memory_bytes')
        self.assertGreater(remaining, 0)
        self.assertLessEqual(len(text.encode()) + remaining, self.config['context_bytes'])
        self.assertEqual(view, dict(selected=self.required, index=self.entries,
                                   candidate_search=dict(status='READY', omitted=dict(OPTIONAL_BUDGET=7,
                                                                                    NO_LEXICAL_MATCH=2),
                                                         returned_entries=1)))

    def test_reported_remaining_memory_fits_full_unicode_context_and_whole_required_text(self):
        self.required[0]['record']['body'] = 'Keep 日本語 qualifiers and "quoted" context. '
        self.entries[0]['summary'] = '日本語 café \\ quoted "candidate"'
        for budget in (4300, 9500, 10000, 65536):
            with self.subTest(budget=budget):
                self.config['context_bytes'] = budget
                result, _ = self.invoke()
                text = result['hookSpecificOutput']['additionalContext']
                view = json.loads(text[text.index('{"selected":'):])
                remaining = view['remaining_memory_bytes']
                self.assertEqual(view['selected'], self.required)
                self.assertGreaterEqual(remaining, 0)
                spare = budget - len(text.encode())
                self.assertLessEqual(remaining, spare)
                self.assertLessEqual(spare - remaining, len(str(budget)))

    def test_candidates_keep_server_order_handles_spans_and_conflicts_with_three_entry_cap(self):
        self.entries = [dict(record_id=str(i), version=2, summary='Preview ' + str(i),
                             summary_span=dict(offset=11, length=14),
                             conflicts=[dict(record_id='peer', version=1)],
                             pull_arguments=dict(receipt_id='receipt', handle=str(i), request_id='pull-' + str(i)))
                        for i in range(5)]
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['index'], self.entries[:3])
        self.assertEqual(view['candidate_search']['returned_entries'], 5)
        self.assertEqual(state['last_recall']['candidate_previews'], 3)
        self.assertEqual(state['last_recall']['records'], [])
        self.assertEqual(len(self.commands), 1)

    def test_oversize_unicode_candidate_omits_whole_tail_and_preserves_required_context(self):
        self.entries[0]['summary'] = '\"日\\\n' * 600
        self.entries[0]['pull_command'] = 'cairn agent pull receipt optional'
        self.entries.append(dict(record_id='later', version=1, summary='Shorter later-ranked candidate',
                                 pull_arguments=dict(receipt_id='receipt', handle='later', request_id='later')))
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['selected'], self.required)
        self.assertEqual(view['index'], [])
        self.assertEqual(view['candidate_search']['returned_entries'], 2)
        self.assertLessEqual(len(text.encode()), self.config['context_bytes'])
        self.assertEqual(state['last_recall']['candidate_previews'], 0)

    def test_native_candidates_omit_shell_commands_before_packing_without_changing_sources(self):
        receipt = '4eb7877c-7cbb-41bf-8a4c-8c8a145a1c84'
        entries = []
        for i in range(4):
            identity = f'00000000-0000-4000-8000-{i:012d}'
            pull = dict(request_id=identity, receipt_id=receipt, handle=identity)
            entries.append(dict(record_id=identity, version=2, **{'class': 'A'}, kind='procedure',
                                summary='Preserve source names. 日本語', body_sha256='a' * 64,
                                summary_span=dict(offset=120, length=35), pull_arguments=pull,
                                pull_command='/home/operator/.local/bin/cairn agent --socket '
                                '/home/operator/.local/share/cairn/api.sock --token-file '
                                '/home/operator/.local/share/cairn/hosted-agent.token pull '
                                f'--request-id {identity} {receipt} {identity}'))
        entries[0].update(match_span=dict(offset=80, length=140),
                          conflicts=[dict(record_id=entries[1]['record_id'], version=2)],
                          future_metadata=dict(labels=['keep this'], enabled=True))
        source = dict(status='READY', destination=dict(name='hosted'), selected=self.required,
                      index=entries, omitted={})
        before = json.loads(json.dumps(source))
        with patch.object(hook.Memory, 'search', return_value=source) as search, \
                patch.object(hook.Memory, 'call', side_effect=AssertionError('no optional calls')):
            result = hook.handle(self.config, self.event)
        search.assert_called_once()
        self.assertEqual(source, before, 'presentation must not mutate the search response')
        text = result['hookSpecificOutput']['additionalContext']
        marker = text.index('{"selected":')
        view = json.loads(text[marker:])
        self.assertEqual(view['selected'], self.required)
        expected = [{key: value for key, value in entry.items() if key != 'pull_command'}
                    for entry in entries[:3]]
        self.assertEqual(view['index'], expected)
        self.assertEqual(view['candidate_search']['returned_entries'], 4)
        # The old shell-bearing envelope crowds out the third realistic entry.
        base_bytes = len((text[:marker] + hook.encoded(dict(view, index=[]))).encode())
        limit = min(2000, (self.config['context_bytes'] - base_bytes) // 2)
        original_two = len((text[:marker] + hook.encoded(dict(view, index=entries[:2]))).encode())
        original_three = len((text[:marker] + hook.encoded(dict(view, index=entries[:3]))).encode())
        self.assertLessEqual(original_two - base_bytes, limit)
        self.assertGreater(original_three - base_bytes, limit)
        self.assertLessEqual(len(text.encode()) - base_bytes, limit)
        self.assertLessEqual(len(text.encode()), self.config['context_bytes'])

    def test_required_context_leaves_room_for_cue_but_not_a_candidate(self):
        self.required[0]['record']['body'] = 'Required instruction. ' * 180
        self.entries[0]['summary'] = '日' * 200
        self.config['context_bytes'] = 7300
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['selected'], self.required)
        self.assertEqual(view['index'], [])
        self.assertIn('unverified candidate previews', text)
        self.assertEqual(state['last_recall']['outcome'], 'delegated')

    def test_empty_candidates_preserve_omission_reasons_without_optional_calls(self):
        self.entries = []
        result, state = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['index'], [])
        self.assertEqual(view['candidate_search']['omitted']['OPTIONAL_BUDGET'], 7)
        self.assertEqual(state['last_recall']['candidate_previews'], 0)
        self.assertEqual(len(self.commands), 1)

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
