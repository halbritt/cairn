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

    def test_eager_whole_read_keeps_public_pull_id_unused_for_first_span(self):
        import copy
        import time
        import uuid
        from unittest.mock import Mock
        self.result['index'] = self.entries[:1]
        entry = self.entries[0]
        entry['pull_arguments'].update(request_id=str(uuid.uuid4()), receipt_id=str(uuid.uuid4()))
        original = copy.deepcopy(entry)
        # A boundary double models the existing API's explicit request-ID contract.
        # It is not a replacement for core's authenticated idempotency tests.
        committed = {}
        debits = []
        def checked_pull(operation, *, payload, timeout):
            key = payload['request_id']
            intent = json.dumps(payload, sort_keys=True)
            if key in committed and committed[key] != intent:
                raise hook.HookError('IDEMPOTENCY_CONFLICT')
            if key not in committed:
                committed[key] = intent
                debits.append(copy.deepcopy(payload))
            return self.responses[payload['handle']]
        self.pull = checked_pull
        _, view, _ = self.invoke()
        supplied = view['candidate_bodies'][0]['pull_arguments']
        self.assertEqual(entry, original)
        self.assertEqual(supplied, original['pull_arguments'])
        self.assertEqual(len(debits), 1)
        private_id = debits[0]['request_id']
        self.assertNotEqual(private_id, supplied['request_id'])
        self.assertEqual(str(uuid.UUID(private_id)), private_id)
        memory = Mock()
        memory.call.side_effect = checked_pull
        hook.current_pull(memory, entry, time.monotonic() + 2)
        self.assertEqual(memory.call.call_args.kwargs['payload']['request_id'], private_id)
        self.assertEqual(len(debits), 1)
        span = dict(supplied, span=dict(offset=0, length=16))
        checked_pull('pull', payload=span, timeout=1)
        checked_pull('pull', payload=span, timeout=1)
        self.assertEqual(len(debits), 2)
        with self.assertRaisesRegex(hook.HookError, 'IDEMPOTENCY_CONFLICT'):
            checked_pull('pull', payload=dict(span, span=dict(offset=16, length=16)), timeout=1)
        self.assertEqual(entry, original)
        for field in ('request_id', 'receipt_id', 'handle'):
            changed = copy.deepcopy(entry)
            changed['pull_arguments'][field] = str(uuid.uuid4())
            with patch.object(memory, 'call', return_value=self.responses[entry['record_id']]) as call:
                hook.current_pull(memory, changed, time.monotonic() + 2)
            self.assertNotEqual(call.call_args.kwargs['payload']['request_id'], private_id)

    def test_optional_delivery_view_preserves_sources_and_usable_handles(self):
        from trial_source_delivery import hook_delivery
        self.result['index'] = self.entries[:1]
        entry = self.entries[0]
        response = self.responses[entry['record_id']]
        response['selection'].update(mandatory=False, evidence=[], authority=None,
                                     reason='optional relevance', category='note')
        record = response['selection']['record']
        record.update(**{'class': 'B'}, lifecycle='accepted', sensitivity='shareable',
                      kind='lesson', scope=dict(repository='fixture', task='*', run='*'),
                      observed_writer='authenticated-writer', witness='selected evidence',
                      written_at='2026-01-01T00:00:00Z', attribution_state='observed',
                      future_field=dict(condition='Preserve this too.'))
        before = json.loads(json.dumps(response))
        text, view, state = self.invoke()
        supplied = view['candidate_bodies'][0]
        compact = supplied['response']
        self.assertEqual(compact['selection']['record'], record)
        self.assertEqual(supplied['pull_arguments'], entry['pull_arguments'])
        self.assertEqual(compact['selection']['reason'], before['selection']['reason'])
        self.assertEqual(compact['selection']['category'], before['selection']['category'])
        self.assertIs(compact['selection']['mandatory'], False)
        self.assertNotIn('credits_remaining', compact)
        self.assertNotIn('bytes_remaining', compact)
        self.assertNotIn('evidence', compact['selection'])
        self.assertNotIn('authority', compact['selection'])
        self.assertEqual(response, before)
        def emitted(candidate):
            return json.dumps(dict(hookSpecificOutput=dict(additionalContext=hook.encoded(dict(
                candidate_bodies=[candidate])))))
        original = dict(pull_arguments=entry['pull_arguments'], response=before)
        self.assertEqual(hook_delivery(emitted(original)), hook_delivery(emitted(supplied)))
        self.assertLess(len(emitted(supplied).encode()), len(emitted(original).encode()))
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(state['seen'], {})

    def test_optional_view_does_not_erase_unknown_or_malformed_metadata(self):
        for value in (True, -1, 1.5, 'unknown', dict(future='preserve'), None):
            with self.subTest(value=value):
                candidate = dict(future_top=dict(keep='unknown'), pull_arguments=self.entries[0]['pull_arguments'],
                    response=dict(credits_remaining=value, bytes_remaining=value, future_response=['keep'],
                        selection=dict(record=dict(body='Known source', **{'class':'A'}), mandatory=False,
                                       evidence='malformed but not ours to discard', authority=dict(future='support'))))
                before = json.loads(json.dumps(candidate))
                self.assertEqual(hook.optional_delivery_view(candidate), before)
                self.assertEqual(candidate, before)

    def test_saturated_optional_response_fits_without_shortening_or_changing_handles(self):
        entry = self.entries[0]
        response = self.responses[entry['record_id']]
        response['selection'].update(mandatory=False, evidence=[], authority=[])
        response['selection']['record']['class'] = 'A'
        candidate = dict(pull_arguments=entry['pull_arguments'], response=response)
        original = json.loads(json.dumps(candidate))
        measure = lambda text: hook.codex_hook_cost(self.event, text)
        def render(budget):
            return hook.render_agent_candidates([], dict(status='READY', index=[], omitted={}),
                budget, dict(rejected={}), [candidate], dict(pull_calls=1), measure=measure)
        with patch.object(hook, 'optional_delivery_view', side_effect=lambda item: item):
            before = render(9500)
            # Cross the actual full-envelope boundary, without changing source,
            # handles, policy or the compact renderer's accounting.
            budget = 2 * measure(before) - 8
            with self.assertRaises(hook.ContextRefused):
                render(budget)
        text = render(budget)
        view = json.loads(text[text.index('{"selected":'):])
        shown = view['candidate_bodies'][0]
        self.assertEqual(shown['pull_arguments'], original['pull_arguments'])
        self.assertEqual(shown['response']['selection']['record'], original['response']['selection']['record'])
        self.assertLessEqual(measure(text) + view['remaining_memory_bytes'], budget)
        self.assertGreater(view['remaining_memory_bytes'], 0)
        self.assertEqual(candidate, original)

    def test_optional_compaction_keeps_support_and_protected_groups_complete(self):
        for protected in ('mandatory', 'C', 'competing', 'conflicts', 'unknown_class', 'support'):
            with self.subTest(protected=protected):
                record = dict(record_id=self.entries[0]['record_id'], version=1, body='日本語 condition', **{'class':'A'})
                selection = dict(record=record, mandatory=False, evidence=[], authority=[])
                response = dict(selection=selection, credits_remaining=3, bytes_remaining=4096)
                if protected == 'mandatory': selection['mandatory'] = True
                if protected == 'C': record['class'] = 'C'
                if protected == 'competing': response['competing'] = [dict(record=dict(record, body='Competing whole 日本語'), mandatory=False)]
                if protected == 'conflicts': selection['conflicts'] = [dict(members=[dict(record_id='other',version=1)])]
                if protected == 'unknown_class': record.pop('class')
                if protected == 'support':
                    selection.update(evidence=[dict(digest='a'*64,condition='Supported prerequisite')],
                                     authority=[dict(grant='existing authority metadata')])
                candidate = dict(pull_arguments=self.entries[0]['pull_arguments'], response=response)
                before = json.loads(json.dumps(candidate))
                selected = [dict(mandatory=True, record=dict(body='Keep the whole required instruction.'))]
                text = hook.render_agent_candidates(selected, dict(index=[], omitted={}), 9500,
                    dict(rejected={}), [candidate], dict(pull_calls=1))
                view = json.loads(text[text.index('{"selected":'):])
                self.assertEqual(view['selected'], selected)
                shown = view['candidate_bodies'][0]
                if protected != 'support': self.assertEqual(shown, before)
                else:
                    self.assertEqual(shown['response']['selection'], selection)
                    self.assertNotIn('credits_remaining', shown['response'])
                self.assertEqual(candidate, before)

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
        self.config['context_bytes'] = 7000
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
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 7000)
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
        self.responses[entry['record_id']]['selection'].update(mandatory=False, evidence=[], authority=None)
        before = json.loads(json.dumps(self.responses))
        text, view, state = self.invoke()
        self.assertEqual(len(self.calls), 1)  # The checked whole read already paid for these bytes.
        supplied = view['candidate_bodies'][0]
        self.assertEqual(supplied['pull_arguments'], entry['pull_arguments'])
        response = supplied['response']
        self.assertEqual(response['selection']['record']['body'], '')
        self.assertEqual(response['source_extent'], 'partial_span')
        self.assertNotIn('credits_remaining', response)
        self.assertNotIn('bytes_remaining', response)
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

    def test_receipt_refusal_can_deliver_one_checked_passage_with_two_actual_calls_and_native_reserve(self):
        self.optional(2)
        self.entries[0]['match_span'] = dict(offset=0, length=len('Old context. '))
        self.pull = self.receipt_span
        text, view, _ = self.invoke()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([bool(c.get('span')) for c in self.calls], [False, True])
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['delivered_records'], 1)
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(view['candidate_bodies'][0]['response']['source_extent'], 'partial_span')
        self.assertIn(self.entries[1], view['index'])
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

    def test_oversized_optional_passage_does_not_hide_later_checked_whole_note(self):
        self.config['context_bytes'] = 6500
        self.optional()
        first, later = self.entries[:2]
        first['match_span'] = dict(offset=0, length=4096)
        self.result['index'] = [first, later]
        body = 'Later migration guidance: preserve the rollback prerequisite. 日本語. ' * 12
        later['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        self.responses[later['record_id']]['selection']['record']['body'] = body
        required = dict(mandatory=True, record=dict(record_id='required', version=1,
                        body='Always preserve the complete required safeguard.', **{'class': 'C'}))
        self.result['selected'] = [required]
        def pull(operation, *, payload, timeout):
            if payload['handle'] == first['record_id']:
                return EagerCandidateTests.pull(self, operation, payload=payload, timeout=timeout)
            return EagerCandidateTests.pull(self, operation, payload=payload, timeout=timeout)
        self.pull = pull
        text, view, state = self.invoke()
        self.assertEqual(view['selected'], [required])
        self.assertEqual([item['response'] for item in view['candidate_bodies']],
                         [self.responses[later['record_id']]])
        self.assertEqual([call['handle'] for call in self.calls],
                         [first['record_id'], later['record_id']])
        self.assertEqual(view['candidate_inspection'], dict(pull_calls=2, remaining_pull_calls=2,
            delivered_records=1, refusals=dict(whole_context_budget=1, span_context_budget=1)))
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 6500)
        self.assertEqual(state['seen'], {})

    def test_after_oversized_passage_competing_group_is_delivered_whole(self):
        self.config['context_bytes'] = 6400
        self.optional()
        first, left, right = self.entries
        first['match_span'] = dict(offset=0, length=4096)
        members = [dict(record_id=e['record_id'], version=1) for e in (left, right)]
        for entry in (left, right):
            entry['conflicts'] = [dict(members=members)]
        self.result['index'] = self.entries
        pair = dict(self.responses[left['record_id']],
                    competing=[self.responses[right['record_id']]['selection']])
        def pull(operation, *, payload, timeout):
            if payload['handle'] == first['record_id']:
                return EagerCandidateTests.pull(self, operation, payload=payload, timeout=timeout)
            self.calls.append(payload)
            self.assertEqual({k: v for k, v in payload.items() if k != 'request_id'},
                             {k: v for k, v in left['pull_arguments'].items() if k != 'request_id'})
            self.assertNotEqual(payload['request_id'], left['pull_arguments']['request_id'])
            return pair
        self.pull = pull
        text, view, _ = self.invoke()
        self.assertEqual([item['response'] for item in view['candidate_bodies']], [pair])
        self.assertEqual(view['candidate_inspection']['delivered_records'], 2)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['refusals']['span_context_budget'], 1)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 6400)

    def test_after_oversized_passage_global_stops_remain_terminal(self):
        for stop in ('authority', 'identity', 'receipt_budget', 'deadline', 'two_calls'):
            with self.subTest(stop=stop):
                case = EagerCandidateTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.config['context_bytes'] = 5400
                case.optional(2)
                first, second, last = case.entries
                case.result['index'] = case.entries
                for entry in (first, second):
                    entry['match_span'] = dict(offset=0, length=4096)
                clock = [100.0]
                def pull(operation, *, payload, timeout):
                    self.assertNotEqual(payload['handle'], last['record_id'])
                    if payload['handle'] == first['record_id']:
                        result = EagerCandidateTests.pull(case, operation, payload=payload, timeout=timeout)
                        if stop == 'deadline': clock[0] = 106.0
                        return result
                    if stop == 'two_calls':
                        return case.receipt_span(operation, payload=payload, timeout=timeout)
                    case.calls.append(payload)
                    if stop == 'authority': raise hook.HookError('AUTHORITY_DENIED')
                    if stop == 'receipt_budget': raise hook.BudgetRefused('no remaining receipt allowance')
                    changed = json.loads(json.dumps(case.responses[second['record_id']]))
                    changed['selection']['record']['version'] = 2
                    return changed
                case.pull = pull
                with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]):
                    text, view, _ = case.invoke()
                self.assertEqual(view['candidate_bodies'], [])
                expected_calls = dict(authority=2, identity=2, receipt_budget=2, deadline=1, two_calls=2)[stop]
                self.assertEqual(len(case.calls), expected_calls)
                self.assertEqual(view['candidate_inspection']['pull_calls'], expected_calls)
                if stop != 'deadline':
                    self.assertEqual(view['candidate_inspection']['refusals']['span_context_budget'], 1)
                terminal = dict(authority='whole_pull_unavailable', identity='whole_pull_unavailable',
                                receipt_budget='pull_limit', deadline='deadline', two_calls='pull_limit')[stop]
                self.assertEqual(view['candidate_inspection']['refusals'][terminal], 1)
                self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 5400)

    def test_saturated_prior_body_is_not_evicted_by_extra_attempt_metadata(self):
        # An accepted whole response must survive the next failed call.
        # Neither a third call nor an uncharged retry may consume native choice.
        self.config['context_bytes'] = 6400
        self.optional()
        large, later, earlier = self.entries
        large['match_span'] = dict(offset=0, length=4096)
        self.result['index'] = [earlier, large, later]
        body = 'Earlier whole condition. ' * 35
        earlier['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        self.responses[earlier['record_id']]['selection']['record']['body'] = body
        def pull(operation, *, payload, timeout):
            if payload['handle'] == large['record_id']:
                return self.receipt_span(operation, payload=payload, timeout=timeout)
            if payload['handle'] == later['record_id']:
                self.calls.append(payload)
                raise hook.HookError('later authority unavailable')
            return EagerCandidateTests.pull(self, operation, payload=payload, timeout=timeout)
        self.pull = pull
        text, view, _ = self.invoke()
        self.assertEqual([item['response'] for item in view['candidate_bodies']],
                         [self.responses[earlier['record_id']]])
        self.assertEqual([call['handle'] for call in self.calls],
                         [earlier['record_id'], large['record_id']])
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['refusals'],
                         dict(whole_pull_budget=1, pull_limit=1))
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 6400)

    def test_passage_refuses_when_complete_preview_cannot_fit_without_more_reads(self):
        self.config['context_bytes'] = 5400
        self.optional()
        self.entries[0]['match_span'] = dict(offset=0, length=4096)
        self.entries[0]['summary_span'] = dict(offset=4000, length=96)
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

    def test_large_whole_candidate_leaves_handle_and_native_room(self):
        self.result['index'] = self.entries[:1]
        body = 'Large but whole guidance. ' * 190
        entry = self.entries[0]
        entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        self.responses[entry['record_id']]['selection']['record']['body'] = body
        text, view, _ = self.invoke()
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['index'], [entry])
        self.assertGreater(view['remaining_memory_bytes'], 3000)
        self.assertEqual(len(self.calls), 1)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)

if __name__ == '__main__':
    unittest.main()
