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

    def test_compact_instructions_leave_room_for_whole_conflict_and_native_inspection(self):
        self.config['context_bytes'] = 5000
        self.result['selected'] = [dict(mandatory=True, record=dict(
            record_id='required', version=1, body='Retain both competing positions whole.'))]
        self.conflict(2)
        for entry in self.entries[:2]:
            body = 'Keep 日本語 conditions and "quoted" prerequisites. ' * 10
            entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
            self.responses[entry['record_id']]['selection']['record']['body'] = body
        original = json.loads(json.dumps(self.result))
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(view['selected'], self.result['selected'])
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(view['candidate_bodies'][0]['response'], self.responses[self.entries[0]['record_id']])
        self.assertEqual(view['candidate_inspection']['delivered_records'], 2)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 1)
        self.assertGreaterEqual(view['remaining_memory_bytes'], 500)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 5000)
        self.assertEqual(self.result, original)
        self.assertEqual(state['seen'], {})

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

    def test_oversized_optional_source_delivers_checked_passage_at_5400(self):
        self.config['context_bytes'] = 5400
        self.result['index'] = self.entries[:1]
        entry = self.entries[0]
        passage = 'Migration prerequisite: preserve 日本語 and "quoted" conditions. '
        body = 'Unrelated earlier status. ' * 250 + passage + ' Later history.' * 250
        offset = body.encode().index(passage.encode())
        entry.update({'class': 'A', 'match_span': dict(offset=offset, length=len(passage.encode())),
                      'body_sha256': hashlib.sha256(body.encode()).hexdigest()})
        self.responses[entry['record_id']]['selection']['record'].update(body=body, **{'class': 'A'})
        before = json.loads(json.dumps(self.responses))
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)  # The checked whole read already paid for these bytes.
        supplied = view['candidate_bodies'][0]
        self.assertEqual(supplied['pull_arguments'], entry['pull_arguments'])
        response = supplied['response']
        self.assertEqual(response['selection']['record']['body'], '')
        self.assertEqual(response['source_extent'], 'partial_span')
        self.assertEqual(response['span_origin'], 'whole_pull')
        self.assertEqual(response['span']['body'], passage)
        self.assertEqual(response['span']['source_sha256'], entry['body_sha256'])
        self.assertEqual(response['span']['sha256'], hashlib.sha256(passage.encode()).hexdigest())
        self.assertEqual(response['span']['offset'], offset)
        self.assertEqual(response['span']['end'], offset + len(passage.encode()))
        self.assertEqual(response['span']['total_bytes'], len(body.encode()))
        self.assertEqual(view['candidate_inspection']['refusals'], {'whole_context_budget': 1})
        self.assertEqual(view['candidate_inspection']['delivered_records'], 1)
        self.assertEqual(view['index'], [])
        self.assertLessEqual(len(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit', 'additionalContext': text}},
            ensure_ascii=False).encode()), 5400)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 5400)
        self.assertEqual(self.responses, before)
        self.assertEqual(state['seen'], {})

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

    def optional(self, count=1):
        self.result['index'] = self.entries[:count]
        for entry in self.result['index']:
            body = 'Old context. ' * 800 + 'Checked 日本語 prerequisite.'
            entry.update({'class': 'A', 'match_span': dict(offset=len(('Old context. ' * 800).encode()),
                          length=len('Checked 日本語 prerequisite.'.encode())),
                          'body_sha256': hashlib.sha256(body.encode()).hexdigest()})
            self.responses[entry['record_id']]['selection']['record'].update(body=body, **{'class': 'A'})

    def receipt_span(self, operation, *, payload, timeout):
        self.calls.append(payload)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 2)
        if 'span' not in payload:
            raise hook.BudgetRefused('whole exceeds receipt')
        entry = next(e for e in self.entries if e['record_id'] == payload['handle'])
        self.assertNotEqual(payload['request_id'], entry['pull_arguments']['request_id'])
        result = json.loads(json.dumps(self.responses[entry['record_id']]))
        source = result['selection']['record']['body'].encode()
        offset, length = payload['span']['offset'], payload['span']['length']
        part = source[offset:offset + length]
        result['selection']['record']['body'] = ''
        result['span'] = dict(offset=offset, end=offset + len(part), total_bytes=len(source),
                             body=part.decode(), sha256=hashlib.sha256(part).hexdigest(),
                             source_sha256=hashlib.sha256(source).hexdigest())
        return result

    def test_receipt_refusal_can_deliver_two_checked_passages_with_four_actual_calls(self):
        self.optional(2)
        # The first matched passage already includes its opening, leaving both
        # remaining receipt calls for the second matched passage.
        self.entries[0]['match_span'] = dict(offset=0, length=len('Old context. '))
        self.pull = self.receipt_span
        text, view, _ = self.invoke()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([bool(c.get('span')) for c in self.calls], [False, True, False, True])
        self.assertEqual(view['candidate_inspection']['pull_calls'], 4)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 0)
        self.assertEqual(view['candidate_inspection']['delivered_records'], 2)
        self.assertEqual(len(view['candidate_bodies']), 2)
        self.assertEqual(view['candidate_bodies'][1]['source_opening_excerpt'],
                         dict(status='unavailable', reason='pull_limit'))
        self.assertTrue(all(b['response']['source_extent'] == 'partial_span' for b in view['candidate_bodies']))
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)

    def test_required_c_and_competing_sources_remain_whole_or_omitted(self):
        for mode in ('entry_mandatory', 'selected_mandatory', 'response_mandatory', 'class_c', 'competing'):
            with self.subTest(mode=mode):
                case = EagerCandidateTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.optional(2 if mode == 'competing' else 1)
                entry = case.entries[0]
                if mode == 'entry_mandatory': entry['mandatory'] = True
                if mode == 'selected_mandatory':
                    case.result['selected'] = [dict(mandatory=True, record=dict(record_id=entry['record_id'],
                        version=1, body='Required condition remains whole.'))]
                if mode == 'response_mandatory': case.responses[entry['record_id']]['selection']['mandatory'] = True
                if mode == 'class_c': entry['class'] = 'C'
                if mode == 'competing': case.conflict(2)
                _, view, _ = case.invoke()
                self.assertEqual(view['candidate_bodies'], [])
                self.assertEqual(len(case.calls), 1)
                self.assertEqual(view['selected'], case.result['selected'])

    def test_stale_whole_response_never_triggers_passage_retry(self):
        self.optional()
        entry = self.entries[0]
        self.responses[entry['record_id']]['selection']['record']['version'] = 2
        _, view, _ = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['candidate_inspection']['refusals'], {'whole_pull_unavailable': 1})

    def test_span_source_identity_and_utf8_cut_are_refused(self):
        for mode in ('source_hash', 'version', 'utf8'):
            with self.subTest(mode=mode):
                case = EagerCandidateTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.optional()
                if mode == 'utf8':
                    case.entries[0]['match_span']['offset'] += len('Checked '.encode()) + 1
                else:
                    def corrupt(*args, **kwargs):
                        result = case.receipt_span(*args, **kwargs)
                        if mode == 'source_hash': result['span']['source_sha256'] = '0' * 64
                        else: result['selection']['record']['version'] = 2
                        return result
                    case.pull = corrupt
                _, view, _ = case.invoke()
                self.assertEqual(view['candidate_bodies'], [])
                self.assertEqual(len(case.calls), 1 if mode == 'utf8' else 2)
                self.assertEqual(view['candidate_inspection']['refusals']['span_unavailable'], 1)

    def test_missing_hint_or_expired_deadline_does_not_count_undispatched_span(self):
        for mode in ('hint', 'deadline'):
            with self.subTest(mode=mode):
                case = EagerCandidateTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.optional()
                clock = [100.0]
                if mode == 'hint':
                    case.entries[0].pop('match_span'); case.entries[0].pop('summary_span')
                def refuse(*args, **kwargs):
                    case.calls.append(kwargs['payload'])
                    if mode == 'deadline': clock[0] = 105.01
                    raise hook.BudgetRefused('whole exceeds receipt')
                case.pull = refuse
                with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]):
                    _, view, _ = case.invoke()
                self.assertEqual(len(case.calls), 1)
                self.assertEqual(view['candidate_inspection']['pull_calls'], 1)
                self.assertEqual(view['candidate_bodies'], [])

    def test_passage_still_too_large_refuses_without_shortening_or_more_reads(self):
        self.config['context_bytes'] = 5400
        self.optional()
        self.entries[0]['match_span'] = dict(offset=0, length=4096)
        _, view, _ = self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['candidate_inspection']['refusals'],
                         {'whole_context_budget': 1, 'span_context_budget': 1})

    def test_bound_claude_public_main_charges_complete_passage_envelope_and_no_duplicate(self):
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        case = ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
        case.mc['context_bytes'] = 5400
        case.fixture['search']['discovery'] = dict(state='ready', coverage=dict(indexed=1, eligible=1))
        body = 'Earlier unrelated history. ' * 400 + 'Preserve 日本語 rollback prerequisites.'
        passage = 'Preserve 日本語 rollback prerequisites.'
        entry = case.fixture['search']['index'][0]
        entry.update({'class': 'A', 'match_span': dict(offset=body.encode().index(passage.encode()),
                     length=len(passage.encode())), 'body_sha256': hashlib.sha256(body.encode()).hexdigest()})
        case.fixture['pull']['selection']['record'].update(body=body, **{'class': 'A'})
        case.freeze()
        event = dict(case.event, prompt='Inspect rollback prerequisites.', prompt_id='ordinary-passage-task')
        first = case.memory_main(event)
        self.assertEqual(first['code'], 0, first)
        state = json.loads((Path(case.mc['state_dir']) / (event['session_id'] + '.json')).read_text())
        self.assertEqual(state['last_recall']['search_discovery'], case.fixture['search']['discovery'])
        output = json.loads(first['stdout'])
        text = output['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['candidate_bodies'][0]['response']['span']['body'], passage)
        self.assertEqual(view['candidate_bodies'][0]['response']['source_extent'], 'partial_span')
        self.assertLessEqual(len(first['stdout'].encode()) + view['remaining_memory_bytes'], 5400)
        calls = [x for x in case.calls() if x['op'] == 'pull']
        self.assertEqual(len(calls), 1)
        second = case.memory_main(event)
        self.assertEqual(second['code'], 0, second)
        self.assertEqual(len([x for x in case.calls() if x['op'] == 'pull']), 1)
        self.assertNotIn('partial_span', second['stdout'])

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

    def test_search_discovery_status_survives_delegation_and_optional_refusal(self):
        for discovery, refusal in ((dict(state='ready', coverage=dict(indexed=3, eligible=3)), False),
                                   (dict(state='unavailable', reason='fixture worker absent'), True)):
            with self.subTest(discovery=discovery):
                case = EagerCandidateTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.result['discovery'] = discovery
                if refusal:
                    def refuse(*args, **kwargs):
                        raise hook.BudgetRefused('receipt full')
                    case.pull = refuse
                _, view, state = case.invoke()
                self.assertEqual(state['last_recall']['search_discovery'], discovery)
                self.assertEqual(view['candidate_search']['discovery'], discovery)
                self.assertEqual(state['last_recall']['outcome'], 'delegated')
                self.assertEqual(bool(view['candidate_inspection']['refusals'].get('whole_pull_budget')), refusal)

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
