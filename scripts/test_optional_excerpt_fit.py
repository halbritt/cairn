"""Checked optional excerpts fit actual hook framing without more source reads."""
import copy
import hashlib
import json
import time
import unittest
from unittest.mock import Mock, patch

import test_eager_candidate_context as eager

hook = eager.hook


class OptionalExcerptFitTests(unittest.TestCase):
    def fixture(self, *, overhead=2400, unicode=False, preview_offset=48):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.optional()
        entry = case.entries[0]
        body = ('Context history. ' * 200 +
                ('条件 日本語 "quoted" \\ details. ' if unicode else 'Retain each stated prerequisite. ') * 1500)
        source = body.encode()
        start = len(('Context history. ' * 200).encode())
        # Requests and previews start/end at valid UTF-8 boundaries. The preview
        # starts inside the checked passage, which itself starts inside the note.
        passage = source[start:start + 1536].decode('utf-8', errors='ignore').encode()
        preview_offset = len(passage.decode()[:preview_offset].encode())
        preview = passage[preview_offset:preview_offset + 154].decode('utf-8', errors='ignore').encode()
        entry.update(body_sha256=hashlib.sha256(source).hexdigest(),
                     summary='...' + preview.decode() + '...',
                     summary_span=dict(offset=start + preview_offset, length=len(preview)),
                     match_span=dict(offset=start, length=len(passage)))
        response = case.responses[entry['record_id']]
        response['selection'].update(mandatory=False, evidence=[], authority=[])
        response['selection']['record'].update(body=body, kind='lesson', sensitivity='shareable',
            scope=dict(repo='fixture', task_id='*', run_id='*'), observed_writer='fixture-writer',
            witness='testimony', attribution_state='self', written_at='2026-01-01T00:00:00Z')
        case.pull = case.receipt_span
        required = dict(mandatory=True, record=dict(record_id='required', version=1,
                        body='Keep this required condition whole.', **{'class': 'C'}))
        case.result['selected'] = [required]
        control = 'Task/control data: ' + ('x' * overhead) + '\n'
        def measure(text):
            return 1 + len(hook.encoded(dict(hookSpecificOutput=dict(
                hookEventName='UserPromptSubmit', additionalContext=control + text))).encode())
        memory = Mock()
        case.fetched = []
        def pull(*args, **kwargs):
            response = case.pull(*args, **kwargs)
            case.fetched.append(copy.deepcopy(response))
            return response
        memory.call.side_effect = pull
        return case, entry, memory, measure

    def run_case(self, fixture, budget=9500):
        case, entry, memory, measure = fixture
        status = dict(rejected={})
        text = hook.eager_agent_candidates(memory, case.result, budget, status,
                                          time.monotonic() + 5, measure=measure)
        view = json.loads(text[text.index('{"selected":'):])
        return text, view, status

    def test_oversized_checked_passage_fits_without_extra_calls_or_losing_preview(self):
        for unicode in (False, True):
            with self.subTest(unicode=unicode):
                fixture = self.fixture(unicode=unicode)
                case, entry, memory, measure = fixture
                original = copy.deepcopy(case.responses)
                text, view, status = self.run_case(fixture)
                self.assertEqual(len(view['candidate_bodies']), 1)
                response = view['candidate_bodies'][0]['response']
                span = response['span']
                source = original[entry['record_id']]['selection']['record']['body'].encode()
                self.assertGreater(len(span['body'].encode()), entry['summary_span']['length'])
                self.assertLess(len(span['body'].encode()), entry['match_span']['length'])
                self.assertEqual(span['body'].encode(), source[span['offset']:span['end']])
                self.assertEqual(span['sha256'], hashlib.sha256(span['body'].encode()).hexdigest())
                self.assertEqual(span['source_sha256'], entry['body_sha256'])
                self.assertEqual(span['total_bytes'], len(source))
                self.assertLessEqual(span['offset'], entry['summary_span']['offset'])
                preview = entry['summary_span']
                self.assertGreater(preview['offset'], span['offset'])
                self.assertGreaterEqual(span['end'], preview['offset'] + preview['length'])
                relative = preview['offset'] - span['offset']
                self.assertEqual(span['body'].encode()[relative:relative + preview['length']],
                                 entry['summary'][3:-3].encode())
                self.assertEqual(response['source_extent'], 'partial_span')
                self.assertEqual(response['selection']['record']['body'], '')
                self.assertIn('partial_span omits context: pull current whole notes for broader claims', text)
                self.assertEqual(view['selected'], case.result['selected'])
                self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
                self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
                self.assertEqual(len(case.calls), 2)
                self.assertEqual(case.calls[-1]['span'], entry['match_span'])
                self.assertEqual(case.responses, original)
                fixed = dict(view, index=[], candidate_bodies=[], remaining_memory_bytes=9500,
                             candidate_inspection=dict(pull_calls=0, remaining_pull_calls=4,
                                                       delivered_records=0, refusals={}))
                fixed_text = hook.AGENT_TOOLS_CUE.format(budget=9500) + hook.encoded(fixed)
                reserve = (9500 - measure(fixed_text)) // 2
                self.assertGreaterEqual(view['remaining_memory_bytes'], reserve)
                self.assertLessEqual(measure(text) + view['remaining_memory_bytes'], 9500)

    def test_fitting_changes_delivery_only_not_fetched_bytes_or_receipt_balances(self):
        fixture = self.fixture(unicode=True)
        case, entry, memory, _ = fixture
        with patch.object(hook, 'fit_optional_excerpt', side_effect=hook.ContextRefused('original refusal')):
            _, before, _ = self.run_case(fixture)
        self.assertEqual(before['candidate_bodies'], [])
        fetched_before = copy.deepcopy(case.fetched)
        calls_before = [{k: v for k, v in call.items() if k != 'request_id'} for call in case.calls]
        case.calls.clear()
        case.fetched.clear()
        _, after, _ = self.run_case(fixture)
        self.assertEqual(len(after['candidate_bodies']), 1)
        self.assertEqual(case.fetched, fetched_before)
        self.assertEqual([{k: v for k, v in call.items() if k != 'request_id'} for call in case.calls], calls_before)
        self.assertEqual(len(case.fetched[0]['span']['body'].encode()), entry['match_span']['length'])
        self.assertEqual(case.fetched[0]['bytes_remaining'], 20000)
        self.assertEqual(case.fetched[0]['credits_remaining'], 3)
        self.assertEqual(after['candidate_inspection']['pull_calls'], before['candidate_inspection']['pull_calls'])

    def test_refuses_if_full_preview_and_additional_checked_bytes_cannot_fit(self):
        for preview in ('at_end', 'missing', 'outside'):
            with self.subTest(preview=preview):
                fixture = self.fixture(preview_offset=1382)
                case, entry, _, _ = fixture
                if preview == 'missing':
                    del entry['summary_span']
                elif preview == 'outside':
                    entry['summary_span']['offset'] += 10000
                _, view, status = self.run_case(fixture)
                self.assertEqual(view['candidate_bodies'], [])
                self.assertEqual(len(case.calls), 2)
                self.assertEqual(view['candidate_inspection']['refusals']['span_context_budget'], 1)
                self.assertEqual(view['candidate_inspection']['delivered_records'], 0)
                self.assertEqual(status['candidate_body_records'], 0)

    def test_fitting_deadline_refuses_even_after_a_tentative_fit(self):
        for expiry in ('span_return', 'fitting'):
            with self.subTest(expiry=expiry):
                fixture = self.fixture()
                case, _, memory, measure = fixture
                clock = [100.0]
                original_pull = memory.call.side_effect
                def pull(*args, **kwargs):
                    response = original_pull(*args, **kwargs)
                    if expiry == 'span_return':
                        clock[0] = 106.0
                    return response
                memory.call.side_effect = pull
                original_fit = hook.fit_optional_excerpt
                tentative = []
                def fit(entry, candidate, render, deadline):
                    def timed_render(item):
                        result = render(item)
                        tentative.append(item)
                        clock[0] = 106.0
                        return result
                    return original_fit(entry, candidate, timed_render, deadline)
                status = dict(rejected={})
                with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]), \
                     patch.object(hook, 'fit_optional_excerpt', side_effect=fit):
                    text = hook.eager_agent_candidates(memory, case.result, 9500, status, 105.0, measure=measure)
                view = json.loads(text[text.index('{"selected":'):])
                self.assertEqual(view['candidate_bodies'], [])
                self.assertEqual(view['candidate_inspection']['delivered_records'], 0)
                self.assertEqual(view['candidate_inspection']['refusals']['deadline'], 1)
                self.assertEqual(status['candidate_body_records'], 0)
                self.assertEqual(len(case.calls), 2)
                self.assertEqual(len(tentative), 1 if expiry == 'fitting' else 0)

    def test_last_checked_excerpt_can_fit_after_call_cap_without_another_pull(self):
        fixture = self.fixture()
        case, _, _, _ = fixture
        case.result['index'] = case.entries
        _, view, _ = self.run_case(fixture)
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(len(case.calls), 2)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['refusals']['pull_limit'], 1)

    def test_bound_public_hooks_charge_complete_fitted_output_with_task_and_required_context(self):
        from pathlib import Path
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        from test_inbox_recall_bridge import InboxRecallBridgeTests
        from trial_source_delivery import hook_delivery
        for factory in (ClaudeInboxRecallTests, InboxRecallBridgeTests):
            with self.subTest(harness=factory.__name__):
                case = factory()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.body = 'Implement the fixture change and preserve its documented boundaries. ' * 30
                historical = case.fixture['history']['versions'][0]
                historical.update(body=case.body, body_bytes=len(case.body.encode()),
                                  body_sha256=hashlib.sha256(case.body.encode()).hexdigest())
                source = ('Earlier context. ' * 200 + '条件 retain the source prerequisite. ' * 400).encode()
                start = len(('Earlier context. ' * 200).encode())
                passage = source[start:start + 1536].decode('utf-8', errors='ignore').encode()
                preview = passage[100:254].decode('utf-8', errors='ignore').encode()
                entry = case.fixture['search']['index'][0]
                entry.update({'class': 'A', 'body_sha256': hashlib.sha256(source).hexdigest(),
                    'match_span': dict(offset=start, length=len(passage)),
                    'summary_span': dict(offset=start + 100, length=len(preview)),
                    'summary': '...' + preview.decode() + '...'})
                case.fixture['pull']['selection']['record'].update(body=source.decode(), **{'class': 'A'})
                case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(
                    record_id='88888888-8888-4888-8888-888888888888', version=1,
                    body='Keep the complete required condition.', **{'class': 'C'}))]
                case.freeze()
                out = case.invoke(main=True)
                self.assertEqual(out['code'], 0, out)
                text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
                view = json.loads(text[text.index('{"selected":'):])
                self.assertEqual(len(view['candidate_bodies']), 1)
                response = view['candidate_bodies'][0]['response']
                span = response['span']
                self.assertLess(len(span['body'].encode()), len(passage))
                self.assertGreater(len(span['body'].encode()), len(preview))
                self.assertEqual(span['body'].encode()[100:100 + len(preview)], preview)
                self.assertEqual(response['span_origin'], 'whole_pull')
                self.assertEqual(response['source_extent'], 'partial_span')
                self.assertEqual(view['selected'], case.fixture['search']['selected'])
                self.assertIn(case.body, text)
                self.assertEqual(len([c for c in case.calls() if c['op'] == 'pull']), 1)
                ledger = json.loads((Path(case.mc['state_dir']) / (case.event['session_id'] + '.inbox-recall.json')).read_text())
                grant = ledger['grants'][ledger['active']]
                self.assertEqual(grant['output_bytes'], len(out['stdout'].encode()))
                self.assertEqual(grant['native_allowance_bytes'], view['remaining_memory_bytes'])
                self.assertLessEqual(grant['output_bytes'] + grant['native_allowance_bytes'], 9500)
                # Isolate the recall JSON from the task/control prefix for the
                # existing delivery extractor; it must report a partial source.
                observed = hook_delivery(json.dumps(dict(hookSpecificOutput=dict(
                    additionalContext=hook.encoded(view)))))
                self.assertEqual(observed['status'], 'observed')
                optional = [item for item in observed['items'] if item['record_id'] == entry['record_id']]
                self.assertEqual(len(optional), 1)
                self.assertEqual(optional[0]['extent'], 'partial_span')
                self.assertEqual(optional[0]['delivered_bytes'], len(span['body'].encode()))


if __name__ == '__main__':
    unittest.main()
