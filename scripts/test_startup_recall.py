"""Taskless startup recall through the public hook and CLI JSON boundary."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook


coordination_spec = importlib.util.spec_from_file_location(
    'wake_coordination', Path(__file__).resolve().parents[1] / 'integrations/lifecycle/coordination.py')
coordination = importlib.util.module_from_spec(coordination_spec)
coordination_spec.loader.exec_module(coordination)


def notification():
    # Generate from the producer: wording drift must break the consumer contract test.
    return coordination.wake_message(dict(
        session=dict(agent_id='11111111-1111-4111-8111-111111111111',
                     execution_id='22222222-2222-4222-8222-222222222222'),
        delivery_id='33333333-3333-4333-8333-333333333333'))


def channel_notification():
    return ('<channel source="cairn-events" '
            'native_session_id="caed9473-b01a-41e7-95ce-c3c1f28d66b3" '
            'agent_id="11111111-1111-4111-8111-111111111111" '
            'execution_id="22222222-2222-4222-8222-222222222222" '
            'delivery_id="33333333-3333-4333-8333-333333333333">\n'
            + notification() + '\n</channel>')


class StartupRecallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'fixture'
        (self.root / '.git').mkdir(parents=True)
        self.config = dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                           state_dir=str(self.root / 'state'), context_bytes=9500)
        self.event = dict(hook_event_name='SessionStart', source='startup', cwd=str(self.root),
                          session_id='caed9473-b01a-41e7-95ce-c3c1f28d66b3')
        self.required = [dict(mandatory=True, record=dict(record_id='required', version=1,
                                                        body='Required instruction'))]
        self.body = 'fixture: use concise field names'
        self.kind = 'preference'
        self.pull_version = 1
        self.searches, self.pulls = [], []

    def transport(self, command, *, body, **kwargs):
        if command[6] == 'search':
            self.searches.append(command)
            entry = dict(record_id='optional', version=1, kind=self.kind, summary=self.body,
                         pull_arguments=dict(receipt_id='receipt', handle='optional', request_id='pull'))
            kinds = [command[i + 1] for i, value in enumerate(command) if value == '--kind']
            entries = [entry] if not kinds or self.kind in kinds else []
            data = dict(status='READY', destination=dict(name='hosted'), selected=self.required,
                        index=entries, receipt_id='receipt', credits_remaining=4)
        else:
            self.assertEqual(command[6], 'pull')
            self.pulls.append(json.loads(body))
            data = dict(selection=dict(record=dict(record_id='optional', version=self.pull_version, body=self.body)),
                        credits_remaining=3)
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=data)), '')

    def test_plain_notification_defers_optional_work_and_preserves_required_context(self):
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json') as model:
                self.searches.clear()
                event = dict(self.event, hook_event_name='UserPromptSubmit', prompt=notification())
                original = dict(event)
                result = hook.handle(dict(self.config, semantic_fallback=semantic), event)
                context = result['hookSpecificOutput']['additionalContext']
                self.assertIn('Required instruction', context)
                self.assertNotIn(self.body, context)
                self.assertEqual(event, original)
                self.assertEqual(len(self.searches), 1)
                self.assertEqual(self.searches[0][-1], self.root.name)
                self.assertNotIn('--semantic', self.searches[0])
                self.assertEqual(self.pulls, [])
                model.assert_not_called()
                state = json.loads((Path(self.config['state_dir']) / (event['session_id'] + '.json')).read_text())
                self.assertEqual(state['last_recall']['optional_deferred'], 'taskless_notification')

    def test_exact_channel_notification_defers_optional_work(self):
        self.required = []
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json') as model:
                self.searches.clear()
                event = dict(self.event, hook_event_name='UserPromptSubmit', prompt=channel_notification())
                self.assertEqual(hook.handle(dict(self.config, semantic_fallback=semantic), event), {})
                self.assertEqual(len(self.searches), 1)
                self.assertEqual(self.searches[0][-1], self.root.name)
                self.assertEqual(self.pulls, [])
                model.assert_not_called()

    def test_notification_preserves_seen_and_later_real_prompt_recovers(self):
        for semantic in (False, True):
            for notice in (notification(), channel_notification()):
                with self.subTest(semantic=semantic, channel=notice.startswith('<')), \
                     patch.object(hook, 'bounded_command', side_effect=self.transport), \
                     patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                    config = dict(self.config, semantic_fallback=semantic)
                    state_path = Path(config['state_dir']) / (self.event['session_id'] + '.json')
                    state_path.parent.mkdir(exist_ok=True)
                    state_path.write_text(json.dumps(dict(seen={'already-delivered': 7},
                                                         hints=dict(files={'other.py': 9999999999}))))
                    event = dict(self.event, hook_event_name='UserPromptSubmit', prompt=notice)
                    hook.handle(config, event)
                    state = json.loads(state_path.read_text())
                    self.assertEqual(state['seen'], {'already-delivered': 7})
                    self.assertNotIn('--entity-file', self.searches[-1])
                    actual = hook.handle(config, dict(event, prompt='Use concise field names'))
                    self.assertIn(self.body, actual['hookSpecificOutput']['additionalContext'])
                    self.assertIn('Required instruction', actual['hookSpecificOutput']['additionalContext'])

    def test_notification_oversize_required_context_is_refused_whole(self):
        self.required[0]['record']['body'] = '\u65e5' * 4000
        for semantic in (False, True):
            for notice in (notification(), channel_notification()):
                with self.subTest(semantic=semantic), \
                     patch.object(hook, 'bounded_command', side_effect=self.transport), \
                     patch.object(hook, 'select_json') as model:
                    with self.assertRaisesRegex(hook.HookError, 'no partial instructions'):
                        hook.handle(dict(self.config, semantic_fallback=semantic),
                                    dict(self.event, hook_event_name='UserPromptSubmit', prompt=notice))
                    self.assertEqual(self.pulls, [])
                    model.assert_not_called()

    def test_embedded_quoted_or_unknown_notifications_keep_prompt_recall(self):
        notices = (notification(), channel_notification())
        prompts = []
        for notice in notices:
            prompts.extend(['Use concise field names.\n' + notice,
                            notice + '\nUse concise field names.',
                            'The following is a quote: "' + notice + '". Use concise field names.'])
        prompts.extend([notification() + '\n', notification().replace('wakeup.', 'wake-up.'),
                        channel_notification().replace('source="cairn-events"', 'source="unknown"'),
                        channel_notification().replace('agent_id="11111111', 'agent_id="99999999'),
                        channel_notification().replace('\n</channel>', '</channel>'),
                        '<channel source="cairn-events">' + notification() + '</channel>',
                        notification().replace('33333333-3333-4333-8333-333333333333', 'not-a-uuid')])
        # Malformed notices remain ordinary input, even when no separate task text exists.
        self.body = 'fixture: automatic inbox wakeup uses concise field names'
        for semantic in (False, True):
            for prompt in prompts:
                with self.subTest(semantic=semantic, prompt=prompt[:60]), \
                     patch.object(hook, 'bounded_command', side_effect=self.transport), \
                     patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                    state_path = Path(self.config['state_dir']) / (self.event['session_id'] + '.json')
                    state_path.unlink(missing_ok=True)
                    result = hook.handle(dict(self.config, semantic_fallback=semantic),
                                         dict(self.event, hook_event_name='UserPromptSubmit', prompt=prompt))
                    self.assertIn(self.body, result['hookSpecificOutput']['additionalContext'])
                    self.assertNotIn('optional_deferred', json.loads(state_path.read_text())['last_recall'])

    def test_notification_with_explicit_workstream_or_resume_keeps_recall(self):
        self.kind = 'note'
        self.body = hook.workstream_prefix(self.event) + 'Database migration\nNext: verify transactions.'
        for semantic in (False, True):
            for name in ('SessionStart', 'UserPromptSubmit'):
                for source, topic in (('startup', 'Database migration'), ('resume', ''), ('compact', '')):
                    with self.subTest(semantic=semantic, name=name, source=source), \
                         patch.object(hook, 'bounded_command', side_effect=self.transport), \
                         patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                        state_path = Path(self.config['state_dir']) / (self.event['session_id'] + '.json')
                        state_path.parent.mkdir(exist_ok=True)
                        state_path.write_text(json.dumps(dict(workstream=self.body.split('\n')[0])))
                        result = hook.handle(dict(self.config, semantic_fallback=semantic),
                                             dict(self.event, hook_event_name=name, prompt=notification(),
                                                  source=source, workstream=topic))
                        self.assertIn(hook.encoded(self.body), result['hookSpecificOutput']['additionalContext'])

    def test_taskless_startup_delivers_required_context_without_optional_work(self):
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))) as model:
                self.searches.clear()
                self.pulls.clear()
                result = hook.handle(dict(self.config, semantic_fallback=semantic), self.event)
                context = result['hookSpecificOutput']['additionalContext']
                self.assertIn('Required instruction', context)
                self.assertNotIn(self.body, context)
                self.assertEqual(len(self.searches), 1)
                self.assertEqual(self.pulls, [])
                model.assert_not_called()
                state = json.loads((Path(self.config['state_dir']) / (self.event['session_id'] + '.json')).read_text())
                self.assertEqual(state['seen'], {})

    def test_empty_or_blank_startup_does_not_emit_optional_previews(self):
        self.required = []
        for semantic in (False, True):
            for source in (None, 'startup', 'clear'):
                with self.subTest(semantic=semantic, source=source), \
                     patch.object(hook, 'bounded_command', side_effect=self.transport), \
                     patch.object(hook, 'select_json') as model:
                    event = dict(self.event, prompt='  ', workstream=' ', source=source)
                    self.assertEqual(hook.handle(dict(self.config, semantic_fallback=semantic), event), {})
                    self.assertEqual(self.pulls, [])
                    model.assert_not_called()

    def test_oversize_required_context_is_refused_whole(self):
        self.required[0]['record']['body'] = '\u65e5' * 4000
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json') as model:
                with self.assertRaisesRegex(hook.HookError, 'no partial instructions'):
                    hook.handle(dict(self.config, semantic_fallback=semantic), self.event)
                self.assertEqual(self.pulls, [])
                model.assert_not_called()

    def test_next_prompt_recovers_optional_guidance_after_startup_deferral(self):
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                config = dict(self.config, semantic_fallback=semantic)
                startup = hook.handle(config, self.event)
                self.assertNotIn(self.body, startup['hookSpecificOutput']['additionalContext'])
                prompt = hook.handle(config, dict(self.event, hook_event_name='UserPromptSubmit',
                                                  prompt='Use concise field names'))
                self.assertIn(self.body, prompt['hookSpecificOutput']['additionalContext'])
                self.assertIn('Required instruction', prompt['hookSpecificOutput']['additionalContext'])

    def test_prompt_supplied_at_session_start_keeps_optional_recall(self):
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                result = hook.handle(dict(self.config, semantic_fallback=semantic),
                                     dict(self.event, prompt='Use concise field names'))
                self.assertIn(hook.encoded(self.body), result['hookSpecificOutput']['additionalContext'])

    def test_explicit_workstream_and_resume_compaction_keep_handoff_recall(self):
        self.kind = 'note'
        self.body = hook.workstream_prefix(self.event) + 'Database migration\nNext: verify transactions.'
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                config = dict(self.config, semantic_fallback=semantic)
                # A named workstream must not be hidden by startup's kind filter.
                event = dict(self.event, workstream='Database migration')
                result = hook.handle(config, event)
                self.assertIn(hook.encoded(self.body), result['hookSpecificOutput']['additionalContext'])
                for source in ('resume', 'compact'):
                    result = hook.handle(config, dict(event, source=source))
                    self.assertIn(hook.encoded(self.body), result['hookSpecificOutput']['additionalContext'])

    def test_resume_without_explicit_topic_reuses_bound_workstream(self):
        self.kind = 'note'
        self.body = hook.workstream_prefix(self.event) + 'Database migration\nNext: verify transactions.'
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                config = dict(self.config, semantic_fallback=semantic)
                hook.handle(config, dict(self.event, hook_event_name='UserPromptSubmit',
                                         prompt='Continue Database migration'))
                for source in ('resume', 'compact'):
                    result = hook.handle(config, dict(self.event, source=source))
                    self.assertIn(hook.encoded(self.body), result['hookSpecificOutput']['additionalContext'])
                    self.assertIn('"' + self.body.split('\n')[0] + '"', self.searches[-1][-1])

    def test_resume_still_refuses_changed_optional_version(self):
        self.kind = 'note'
        self.body = hook.title_for(self.event) + '\nNext: verify transactions.'
        self.pull_version = 2
        for semantic in (False, True):
            with self.subTest(semantic=semantic), \
                 patch.object(hook, 'bounded_command', side_effect=self.transport), \
                 patch.object(hook, 'select_json') as model:
                result = hook.handle(dict(self.config, semantic_fallback=semantic),
                                     dict(self.event, source='resume'))
                context = result['hookSpecificOutput']['additionalContext']
                self.assertIn('Required instruction', context)
                self.assertNotIn('"expanded"', context)
                model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
