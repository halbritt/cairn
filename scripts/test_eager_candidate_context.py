"""Whole unverified candidates at the real hook boundary; no provider."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_claude_lifecycle import module, ROOT

hook = module('eager_candidate_hook', ROOT / 'integrations/lifecycle/memory.py')

class EagerCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.git').mkdir()
        self.config = dict(cairn='unused', socket='unused', token_file='unused', repo='fixture',
                           harness='claude', recall_mode='agent_tools', context_bytes=9500,
                           semantic_fallback=True, state_dir=str(self.root / 'state'))
        self.event = dict(hook_event_name='UserPromptSubmit', cwd=str(self.root),
                          session_id='caed9473-b01a-41e7-95ce-c3c1f28d66b3',
                          turn_id='11111111-1111-4111-8111-111111111111', prompt='Inspect migration safeguards.')
        self.entries, self.responses = [], {}
        for i in range(3):
            rid = f'00000000-0000-4000-8000-{i:012d}'
            body = f'Project fixture migration decision{i}: retain complete 日本語 and "quoted" safeguards.'
            entry = dict(record_id=rid, version=1, body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                         summary=body[:45], summary_span=dict(offset=0, length=45),
                         pull_arguments=dict(receipt_id='receipt', request_id=f'pull-{i}', handle=rid))
            self.entries.append(entry)
            self.responses[rid] = dict(selection=dict(record=dict(record_id=rid, version=1, body=body)),
                                       credits_remaining=3-i, bytes_remaining=20000-i*500)
        self.result = dict(status='READY', selected=[], index=self.entries, omitted={},
                           discovery=dict(state='ready', coverage=dict(indexed=3, eligible=3)))
        self.calls = []

    def pull(self, operation, *, payload, timeout):
        self.assertEqual(operation, 'pull')
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 2)
        self.assertNotIn('span', payload)
        self.calls.append(payload)
        return self.responses[payload['handle']]

    def invoke(self, **event):
        with patch.object(hook.Memory, 'search', return_value=self.result) as search, \
             patch.object(hook.Memory, 'call', side_effect=self.pull), \
             patch.object(hook, 'select_json', side_effect=AssertionError('no selector')):
            output = hook.handle(self.config, dict(self.event, **event))
        self.search = search
        state = json.loads((self.root / 'state' / (self.event['session_id'] + '.json')).read_text())
        text = output.get('hookSpecificOutput', {}).get('additionalContext', '')
        view = json.loads(text[text.index('{"selected":'):]) if text else {}
        return text, view, state

    def test_initial_context_delivers_two_whole_unverified_sources_and_remaining_handle(self):
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.search.call_args.kwargs['semantic'])
        self.assertLessEqual(self.search.call_args.kwargs['timeout'], 5)
        self.assertEqual([b['response'] for b in view['candidate_bodies']],
                         [self.responses[e['record_id']] for e in self.entries[:2]])
        self.assertEqual(view['index'], self.entries[2:])
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_search']['discovery'], self.result['discovery'])
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)
        self.assertGreater(view['remaining_memory_bytes'], 2500)
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['records'], [])
        self.assertEqual(state['last_recall']['outcome'], 'delegated')
        self.assertIn('unverified', text)

    def test_whole_context_refusal_retains_required_text_and_handles(self):
        self.result['selected'] = [dict(mandatory=True, record=dict(body='Keep this required rule whole.'))]
        body = '巨大な候補' * 3000
        entry = self.entries[0]
        entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        self.responses[entry['record_id']]['selection']['record']['body'] = body
        before = json.loads(json.dumps(self.result))
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['selected'], self.result['selected'])
        self.assertEqual(view['candidate_inspection']['refusals'], {'whole_context_budget': 1})
        self.assertEqual(view['index'], self.entries)
        self.assertNotIn(body[:100], text)
        self.assertEqual(self.result, before)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)

    def test_whole_pull_budget_refusal_is_visible_and_has_no_span_retry(self):
        def refuse(*args, **kwargs):
            self.calls.append(kwargs['payload'])
            raise hook.BudgetRefused('receipt full')
        self.pull = refuse
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(view['candidate_inspection']['refusals'], {'whole_pull_budget': 1})
        self.assertEqual(state['last_recall']['candidate_inspection']['pull_calls'], 1)
        self.assertEqual(view['candidate_bodies'], [])

    def conflict(self, count):
        group = dict(conflict_id='conflict', version=1,
                     members=[{k: e[k] for k in ('record_id', 'version')} for e in self.entries[:count]])
        for entry in self.entries[:count]:
            entry['conflicts'] = [group]
        self.responses[self.entries[0]['record_id']]['competing'] = [
            self.responses[e['record_id']]['selection'] for e in self.entries[1:count]]

    def test_competing_two_record_group_is_one_whole_pull_and_never_split(self):
        self.conflict(2)
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(len(view['candidate_bodies'][0]['response']['competing']), 1)
        self.assertEqual(view['candidate_inspection']['delivered_records'], 2)
        self.assertEqual(view['index'], self.entries[2:])

    def test_three_record_group_remains_complete_preview_without_eager_pull(self):
        self.conflict(3)
        text, view, state = self.invoke()
        self.assertEqual(self.calls, [])
        self.assertEqual(view['candidate_bodies'], [])
        # Either the full group fits or no part is emitted.
        self.assertIn(len(view['index']), (0, 3))
        self.assertEqual(view['candidate_inspection']['refusals'], {'record_limit': 1})

    def test_changed_or_missing_competing_body_cannot_enter_context(self):
        self.conflict(2)
        self.responses[self.entries[0]['record_id']]['competing'] = []
        _, view, _ = self.invoke()
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['candidate_inspection']['refusals'], {'whole_pull_unavailable': 1})
        self.assertEqual(len(self.calls), 1)

    def test_codex_turn_compaction_and_duplicate_cannot_repeat_eager_calls(self):
        self.config['harness'] = 'codex'
        first, view, _ = self.invoke()
        self.assertEqual(len(self.calls), 2)
        for event in (dict(), dict(hook_event_name='SessionStart', source='compact', prompt='', turn_id=None)):
            text, _, state = self.invoke(**event)
            self.assertEqual(text, '')
            self.assertEqual(len(self.calls), 2)
        ledger = json.loads((self.root / 'state' / (self.event['session_id'] + '.memory-budget.json')).read_text())
        grant = ledger['turns'][self.event['turn_id']]
        self.assertEqual(grant['emitted_hook_bytes'], len(first.encode()))
        self.assertEqual(grant['reserved_native_bytes'], view['remaining_memory_bytes'])

    def test_api_semantic_fallback_is_exposed_without_selector(self):
        self.result['discovery'] = dict(state='unavailable', reason='synthetic worker unavailable')
        _, view, _ = self.invoke()
        self.assertTrue(self.search.call_args.kwargs['semantic'])
        self.assertEqual(view['candidate_search']['discovery'], self.result['discovery'])
        self.config['semantic_fallback'] = False
        self.invoke()
        self.assertFalse(self.search.call_args.kwargs['semantic'])

    def test_search_consuming_deadline_prevents_optional_pull(self):
        clock = [100.0]
        def search(*args, **kwargs):
            clock[0] = 105.01
            return self.result
        with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(hook.Memory, 'search', side_effect=search), \
             patch.object(hook.Memory, 'call', side_effect=AssertionError('expired optional pull')):
            result = hook.handle(self.config, self.event)
        text = result['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['candidate_inspection']['refusals'], {'deadline': 1})
        self.assertEqual(view['candidate_inspection']['pull_calls'], 0)

    def test_eager_body_can_use_more_than_half_room_without_false_future_allowance(self):
        self.result['index'] = self.entries[:1]
        body = 'Large but whole guidance. ' * 190
        entry = self.entries[0]
        entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        self.responses[entry['record_id']]['selection']['record']['body'] = body
        text, view, _ = self.invoke()
        self.assertEqual(view['candidate_bodies'][0]['response']['selection']['record']['body'], body)
        self.assertGreater(len(text.encode()), 7000)
        self.assertLess(view['remaining_memory_bytes'], 2500)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)

if __name__ == '__main__':
    unittest.main()
