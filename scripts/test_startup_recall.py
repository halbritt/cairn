"""Taskless startup recall through the public hook and CLI JSON boundary."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook


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
