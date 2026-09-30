"""Native-turn delegation containment, with no provider or operational store."""
import json
import concurrent.futures
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_claude_lifecycle import module, ROOT

hook = module('codex_turn_budget_hook', ROOT / 'integrations/lifecycle/memory.py')


class CodexTurnBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.git').mkdir()
        self.config = dict(cairn='unused', socket='unused', token_file='unused', repo='fixture',
                           harness='codex', recall_mode='agent_tools', context_bytes=9500,
                           state_dir=str(self.root / 'state'))
        self.event = dict(hook_event_name='UserPromptSubmit', cwd=str(self.root),
                          session_id='caed9473-b01a-41e7-95ce-c3c1f28d66b3',
                          turn_id='11111111-1111-4111-8111-111111111111',
                          prompt='Inspect the current migration procedure.')
        self.selected = []
        self.searches = []
        self.addCleanup(patch.stopall)
        patch.object(hook.Memory, 'search', side_effect=self.search).start()
        patch.object(hook.Memory, 'call', side_effect=AssertionError('no optional pulls')).start()
        patch.object(hook, 'select_json', side_effect=AssertionError('no selector')).start()

    def search(self, *args, **kwargs):
        self.searches.append((args, kwargs))
        return dict(status='READY', selected=self.selected, index=[
            dict(record_id='optional', version=1, summary='Inspect migration procedure.',
                 pull_arguments=dict(receipt_id='receipt', request_id='request', handle='handle'))],
            omitted={})

    def invoke(self, **changes):
        return hook.handle(self.config, dict(self.event, **changes))

    def text(self, result):
        return result.get('hookSpecificOutput', {}).get('additionalContext', '')

    def ledger_path(self):
        return Path(self.config['state_dir']) / (self.event['session_id'] + '.memory-budget.json')

    def ledger(self):
        return json.loads(self.ledger_path().read_text())

    def test_one_grant_survives_compaction_duplicate_and_binding_change(self):
        first = self.text(self.invoke())
        self.assertIn('remaining_memory_bytes', first)
        for changes in (dict(hook_event_name='SessionStart', source='compact', turn_id=None, prompt=''),
                        dict(hook_event_name='SessionStart', source='resume', turn_id=None, prompt=''),
                        dict(prompt='A clarification within the same native turn.'),
                        dict(workstream='Other workstream')):
            with self.subTest(changes=changes):
                self.assertEqual(self.text(self.invoke(**changes)), '')
        self.assertEqual(len(self.searches), 5, 'required-context searches still run')

    def test_startup_required_bytes_are_charged_before_first_turn_grant(self):
        self.selected = [dict(mandatory=True, record=dict(body='Keep complete 日本語 "qualifiers".'))]
        startup = self.text(self.invoke(hook_event_name='SessionStart', source='startup',
                                        turn_id=None, prompt=''))
        self.assertNotIn('remaining_memory_bytes', startup)
        self.assertIsNone(self.ledger()['active_turn_id'])
        submitted = self.text(self.invoke())
        view = json.loads(submitted[submitted.index('{"selected":'):])
        self.assertEqual(view['selected'], self.selected)
        self.assertLessEqual(len(startup.encode()) + len(submitted.encode()) +
                             view['remaining_memory_bytes'], self.config['context_bytes'])
        before = self.ledger()
        with self.assertRaisesRegex(hook.HookError, 'whole required context'):
            self.invoke(hook_event_name='SessionStart', source='compact', turn_id=None, prompt='')
        self.assertEqual(self.ledger(), before, 'refusal cannot refund reserved native bytes')

    def test_unbound_startup_required_context_is_whole_and_cumulative(self):
        self.selected = [dict(mandatory=True, record=dict(body='Preserve 日本語. ' * 30))]
        size = len(hook.render_recall(self.selected, []).encode())
        self.config['context_bytes'] = 2 * size
        for source in ('startup', 'resume'):
            text = self.text(self.invoke(hook_event_name='SessionStart', source=source,
                                         turn_id=None, prompt=''))
            self.assertEqual(len(text.encode()), size)
            self.assertNotIn('remaining_memory_bytes', text)
        with self.assertRaisesRegex(hook.HookError, 'whole required context'):
            self.invoke(hook_event_name='SessionStart', source='compact', turn_id=None, prompt='')

    def test_missing_invalid_turn_or_taskless_wake_never_opens_grant(self):
        from test_startup_recall import notification
        for value in (None, '', 'not-a-turn', True, [], self.event['turn_id'].replace('-', '')):
            with self.subTest(turn=value):
                self.assertEqual(self.text(self.invoke(turn_id=value)), '')
                self.assertEqual(self.ledger()['turns'], {})
        self.assertEqual(self.text(self.invoke(prompt=notification(), workstream='Explicit topic')), '')
        self.assertEqual(self.ledger()['turns'], {})
        self.assertIn('remaining_memory_bytes', self.text(self.invoke()))

    def test_different_turn_has_own_grant_but_old_turn_cannot_refresh(self):
        self.invoke()
        other = '22222222-2222-4222-8222-222222222222'
        self.assertIn('remaining_memory_bytes', self.text(self.invoke(turn_id=other)))
        original = self.ledger()['turns'][self.event['turn_id']]
        self.assertEqual(self.text(self.invoke()), '')
        self.assertEqual(self.ledger()['turns'][self.event['turn_id']], original)
        self.assertEqual(len(self.ledger()['turns']), 2)
        self.assertEqual(self.ledger()['active_turn_id'], other,
                         'a delayed old submission cannot move the current epoch backward')

    def test_capture_and_binding_reset_preserve_independent_ledger(self):
        self.invoke()
        before = self.ledger_path().read_bytes()
        with patch.object(hook, 'capture', return_value={}):
            self.invoke(hook_event_name='PreCompact', workstream='Different binding')
        self.assertEqual(self.ledger_path().read_bytes(), before)
        self.assertEqual(self.text(self.invoke(retained_record_ids=[])), '')
        self.assertEqual(self.ledger()['turns'], json.loads(before)['turns'])

    def test_missing_or_corrupt_ledger_cannot_refresh(self):
        self.invoke()
        original = self.ledger_path().read_bytes()
        self.ledger_path().unlink()
        with self.assertRaisesRegex(hook.HookError, 'ledger is missing'):
            self.invoke(workstream='New binding')
        for data in (b'{broken', b'{}', original.replace(b'"granted":true', b'"granted":1'),
                     original.replace(self.event['session_id'].encode(), b'foreign-session')):
            with self.subTest(data=data[:20]):
                self.ledger_path().write_bytes(data)
                with self.assertRaisesRegex(hook.HookError, 'ledger is corrupt'):
                    self.invoke()
                self.assertEqual(self.ledger_path().read_bytes(), data)

    def test_legacy_capture_binding_reset_cannot_hide_a_missing_ledger(self):
        self.invoke()
        # The deployed legacy handle replaces ordinary state with this mapping
        # on binding change before capture; no ledger metadata survives there.
        state_path = Path(self.config['state_dir']) / (self.event['session_id'] + '.json')
        hook.save_state(state_path, {'binding': [None, 'Legacy capture binding']})
        self.ledger_path().unlink()
        with self.assertRaisesRegex(hook.HookError, 'ledger is missing'):
            self.invoke(workstream='Legacy capture binding')

    def test_uncertain_save_after_ledger_write_never_refunds(self):
        save = hook.save_codex_budget
        def uncertain(path, state):
            save(path, state)
            if path == self.ledger_path():
                raise OSError('uncertain completion after durable replace')
        with patch.object(hook, 'save_codex_budget', side_effect=uncertain):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertTrue(self.ledger()['turns'][self.event['turn_id']]['granted'])
        self.assertEqual(self.text(self.invoke()), '')

    def test_final_hook_state_save_failure_preserves_prior_ledger_commit(self):
        save = hook.save_state
        def fail_hook_state(path, state):
            if path != self.ledger_path():
                raise OSError('hook state save failed after ledger commit')
            save(path, state)
        with patch.object(hook, 'save_state', side_effect=fail_hook_state):
            with self.assertRaises(OSError):
                self.invoke()
        before = self.ledger()['turns']
        self.assertEqual(self.text(self.invoke()), '')
        self.assertEqual(self.ledger()['turns'], before)

    def test_grant_fsync_and_replace_precede_hook_state_and_delivery(self):
        events = []
        fsync, replace, save = os.fsync, Path.replace, hook.save_state
        def sync(fd):
            events.append('directory sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file sync')
            fsync(fd)
        def renamed(source, destination):
            if destination == self.ledger_path():
                events.append('ledger replace')
            elif destination == self.ledger_path().with_suffix('.initialized'):
                events.append('marker replace')
            return replace(source, destination)
        def state_saved(path, state):
            events.append('hook state')
            save(path, state)
        with patch.object(hook.os, 'fsync', side_effect=sync), \
                patch.object(Path, 'replace', new=renamed), \
                patch.object(hook, 'save_state', side_effect=state_saved):
            result = self.invoke()
        events.append('returned context')
        self.assertIn('remaining_memory_bytes', self.text(result))
        self.assertEqual(events, ['file sync', 'marker replace', 'directory sync',
                                  'file sync', 'ledger replace', 'directory sync',
                                  'hook state', 'returned context'])

    def test_directory_sync_failure_refuses_output_and_does_not_refund_written_grant(self):
        fsync = os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and self.ledger_path().exists():
                raise OSError('directory sync failed')
            fsync(fd)
        with patch.object(hook.os, 'fsync', side_effect=sync):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertTrue(self.ledger()['turns'][self.event['turn_id']]['granted'])
        self.assertEqual(self.text(self.invoke()), '')

    def test_bootstrap_crash_after_marker_never_initializes_a_fresh_allowance(self):
        save = hook.save_codex_budget
        def fail_first_ledger(path, value):
            if path == self.ledger_path():
                raise OSError('crash after durable initialization marker')
            save(path, value)
        with patch.object(hook, 'save_codex_budget', side_effect=fail_first_ledger):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertTrue(self.ledger_path().with_suffix('.initialized').exists())
        self.assertFalse(self.ledger_path().exists())
        with self.assertRaisesRegex(hook.HookError, 'ledger is missing'):
            self.invoke()

    def test_config_changes_never_refund_an_existing_turn(self):
        self.invoke()
        before = self.ledger()['turns'][self.event['turn_id']]
        self.config['context_bytes'] = 5000
        with self.assertRaisesRegex(hook.HookError, 'whole required context'):
            self.invoke()
        self.assertEqual(self.ledger()['turns'][self.event['turn_id']], before)
        self.config['context_bytes'] = 12000
        self.assertEqual(self.text(self.invoke()), '')
        self.assertEqual(self.ledger()['turns'][self.event['turn_id']], before)

    def test_concurrent_submissions_cannot_both_grant(self):
        entered, release = threading.Event(), threading.Event()
        def held_search(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.search(*args, **kwargs)
        with patch.object(hook.Memory, 'search', side_effect=held_search), \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.invoke)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(pool.submit(self.invoke).result(timeout=3), {})
            finally:
                release.set()
            self.assertIn('remaining_memory_bytes', self.text(first.result(timeout=3)))
        self.assertEqual(len(self.searches), 1)
        self.assertEqual(self.text(self.invoke()), '')

    def test_ambient_keeps_existing_behavior_without_a_turn_ledger(self):
        self.config['recall_mode'] = 'ambient'
        self.selected = [dict(mandatory=True, record=dict(body='Keep whole required context.'))]
        with patch.object(hook.Memory, 'search', return_value=dict(selected=self.selected, index=[])):
            for _ in range(2):
                self.assertIn('Keep whole required context.', self.text(self.invoke(turn_id=None)))
        self.assertFalse(self.ledger_path().exists())


if __name__ == '__main__':
    unittest.main()
