"""Failed recall replaces stale success without retaining private error text."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_claude_lifecycle import hook


class RecallFailureObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.git').mkdir()
        self.session = '7ab28a48-92f3-4a4e-9da7-f8ca68229d37'
        self.config = dict(cairn='fixture', socket='fixture', token_file='fixture',
                           repo='fixture', state_dir=str(self.root / 'state'))
        self.event = dict(hook_event_name='UserPromptSubmit', session_id=self.session,
                          cwd=str(self.root), prompt='private owner prompt')
        self.state_path = self.root / 'state' / (self.session + '.json')

    def search_failure(self, command, **kwargs):
        self.assertEqual(command[0], 'fixture')
        return subprocess.CompletedProcess(command, 1, '', 'private transport error')

    def test_failed_initial_search_replaces_previous_success_and_records_duration(self):
        self.state_path.parent.mkdir()
        previous = dict(seen={'prior-note': 2}, workstream='Handoff: test / existing',
                        last_recall=dict(at=1, hook_event_name='SessionStart', outcome='recalled', records=[{'record_id': 'prior-note', 'version': 2}],
                                         bytes=400, duration_ms=1))
        self.state_path.write_text(json.dumps(previous))
        with patch.object(hook, 'bounded_command', side_effect=self.search_failure):
            with self.assertRaises(hook.HookError):
                hook.handle(self.config, self.event)
        saved = json.loads(self.state_path.read_text())
        status = saved['last_recall']
        self.assertEqual(status['outcome'], 'failed')
        self.assertEqual(status['error_type'], 'HookError')
        self.assertEqual(status['hook_event_name'], 'UserPromptSubmit')
        self.assertEqual(status['records'], [])
        self.assertEqual(status['bytes'], 0)
        self.assertGreaterEqual(status['duration_ms'], 0)
        self.assertGreater(status['at'], 1)
        self.assertEqual(saved['seen'], previous['seen'])
        self.assertEqual(saved['workstream'], previous['workstream'])
        self.assertNotIn('private', json.dumps(saved))

    def test_failed_resume_does_not_commit_seen_reset_and_next_success_clears_failure(self):
        self.state_path.parent.mkdir()
        self.state_path.write_text(json.dumps({'seen': {'prior-note': 2}}))
        event = dict(self.event, hook_event_name='SessionStart', source='resume')
        with patch.object(hook, 'bounded_command', side_effect=self.search_failure):
            with self.assertRaises(hook.HookError):
                hook.handle(self.config, event)
        saved = json.loads(self.state_path.read_text())
        self.assertEqual(saved['seen'], {'prior-note': 2})
        self.assertEqual(saved['last_recall']['outcome'], 'failed')
        self.assertEqual(saved['last_recall']['hook_event_name'], 'SessionStart')
        empty = dict(ok=True, data=dict(status='READY', destination={'name': 'hosted'}, selected=[], index=[]))
        with patch.object(hook, 'bounded_command', return_value=subprocess.CompletedProcess(
                ['fixture'], 0, json.dumps(empty), '')):
            self.assertEqual(hook.handle(self.config, event), {})
        saved = json.loads(self.state_path.read_text())
        self.assertEqual(saved['seen'], {})
        self.assertEqual(saved['last_recall']['outcome'], 'empty')
        self.assertEqual(saved['last_recall']['hook_event_name'], 'SessionStart')
        self.assertNotIn('error_type', saved['last_recall'])

    def test_oversized_required_context_records_failure_without_optional_calls_or_payloads(self):
        required = 'private required instruction ' * hook.CONTEXT_BYTES
        response = dict(ok=True, data=dict(status='READY', destination={'name': 'hosted'},
                                          selected=[{'body': required}], index=[]))
        with patch.object(hook, 'bounded_command', return_value=subprocess.CompletedProcess(
                ['fixture'], 0, json.dumps(response), '')) as transport:
            with self.assertRaisesRegex(hook.HookError, 'no partial instructions'):
                hook.handle(self.config, self.event)
        self.assertEqual(transport.call_count, 1)
        status = json.loads(self.state_path.read_text())['last_recall']
        self.assertEqual(status['outcome'], 'failed')
        self.assertEqual(status['records'], [])
        self.assertEqual(status['bytes'], 0)
        self.assertNotIn('private', json.dumps(status))

    def test_status_write_failure_surfaces_both_failures_and_preserves_previous_file(self):
        self.state_path.parent.mkdir()
        previous = json.dumps({'last_recall': {'outcome': 'recalled'}})
        self.state_path.write_text(previous)
        with patch.object(hook, 'bounded_command', side_effect=self.search_failure), \
             patch.object(hook.tempfile, 'NamedTemporaryFile', side_effect=OSError('private storage detail')):
            with self.assertRaisesRegex(hook.HookError, 'recall failed and failure status could not be saved') as error:
                hook.handle(self.config, self.event)
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(self.state_path.read_text(), previous)


if __name__ == '__main__':
    unittest.main()
