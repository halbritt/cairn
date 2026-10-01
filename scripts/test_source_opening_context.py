"""Bounded original-source context beside optional matched passages; no provider."""
import base64
import hashlib
import json
import unittest
import time
from types import SimpleNamespace
from unittest.mock import patch

import test_claude_inbox_recall as claude
import test_eager_candidate_context as eager


class SourceOpeningTests(unittest.TestCase):
    def test_tight_native_envelope_keeps_heading_and_match_before_extra_previews(self):
        case = claude.ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.mc['context_bytes'] = 6318
        opening = ('Historical audit handoff\nProject: fixture-engine (the audit tool was Surveyor).\n'
                   'Context: prior audit repair, not current report implementation.\n')
        passage = 'Report validation: preserve existing evidence and avoid new model calls. 日本語.\n'
        body = opening + 'Earlier audit finding details. ' * 350 + passage
        entry = case.fixture['search']['index'][0]
        entry.update({'class': 'A', 'match_span': dict(offset=body.encode().index(passage.encode()),
                     length=len(passage.encode())), 'body_sha256': hashlib.sha256(body.encode()).hexdigest()})
        case.fixture['pull']['selection']['record'].update(body=body, **{'class': 'A'},
            scope=dict(repo='/shared/collection', task_id='*', run_id='*'), kind='note',
            observed_writer='fixture-importer', attribution_state='self', claim_type='self',
            written_at='2026-09-01T00:00:00Z')
        case.fixture['search']['index'].append(dict(entry, record_id='00000000-0000-4000-8000-000000000199',
            summary='Another potentially useful report source. ' * 10,
            pull_arguments=dict(entry['pull_arguments'], handle='other-preview')))
        case.freeze()
        event = dict(case.event, prompt='Inspect report fallback preservation.', prompt_id='source-opening-task')
        out = case.memory_main(event)
        self.assertEqual(out['code'], 0, out)
        text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        view = json.loads(text[text.index('{"selected":'):])
        candidate = view['candidate_bodies'][0]
        self.assertEqual(candidate['response']['span']['body'], passage)
        context = candidate['source_opening_excerpt']
        self.assertEqual(context['status'], 'provided')
        self.assertEqual(context['span']['body'], opening)
        self.assertEqual(context['origin'], 'whole_pull')
        self.assertEqual(context['span']['source_sha256'], entry['body_sha256'])
        self.assertEqual(context['span']['end'], len(opening.encode()))
        self.assertLessEqual(len(out['stdout'].encode()) + view['remaining_memory_bytes'], 6318)
        self.assertIn(opening, text.replace('\\n', '\n'))
        self.assertEqual(len([c for c in case.calls() if c['op'] == 'pull']), 2)
        from trial_source_delivery import hook_delivery
        delivery = hook_delivery(out['stdout'])
        self.assertEqual(delivery['status'], 'observed')
        emitted_opening = [item for item in delivery['items']
                           if item.get('source_component') == 'source_opening_excerpt']
        self.assertEqual(len(emitted_opening), 1)
        self.assertEqual(emitted_opening[0]['delivered_bytes'], len(opening.encode()))
        self.assertEqual(emitted_opening[0]['delivered_sha256'], hashlib.sha256(opening.encode()).hexdigest())

    def span_case(self, opening='Original project: fixture-engine.\n', count=1):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.optional(count)
        for entry in case.result['index']:
            body = opening + 'Earlier unrelated details. ' * 350 + 'Checked prerequisite.'
            entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
            entry['match_span'] = dict(offset=body.encode().index(b'Checked prerequisite.'),
                                      length=len(b'Checked prerequisite.'))
            case.responses[entry['record_id']]['selection']['record']['body'] = body
        def pull(operation, *, payload, timeout):
            case.calls.append(payload)
            if 'span' not in payload:
                raise eager.hook.BudgetRefused('whole does not fit receipt')
            result = json.loads(json.dumps(case.responses[payload['handle']]))
            source = result['selection']['record']['body'].encode()
            offset, length = payload['span']['offset'], payload['span']['length']
            part = source[offset:offset + length]
            result['selection']['record']['body'] = ''
            result['span'] = dict(offset=offset, end=offset + len(part), total_bytes=len(source),
                sha256=hashlib.sha256(part).hexdigest(), source_sha256=hashlib.sha256(source).hexdigest())
            try:
                result['span']['body'] = part.decode()
            except UnicodeDecodeError:
                result['span']['body_base64'] = base64.b64encode(part).decode()
            return result
        case.pull = pull
        return case

    def checked_opening(self, case):
        # Exercise the existing checked-opening boundary with one paid passage.
        # Eager whole-refusal + span now consumes both automatic calls, so that
        # production path cannot reach a third-call opening any longer.
        entry = case.entries[0]
        hint = entry['match_span']
        args = dict(entry['pull_arguments'], span=hint)
        response = case.pull('pull', payload=args, timeout=1)
        passage = eager.hook.checked_optional_span(entry, response, hint)
        candidate = dict(pull_arguments=entry['pull_arguments'], response=passage)
        progress = dict(pull_calls=1, remaining_pull_calls=3)
        supplied = eager.hook.attach_source_opening(SimpleNamespace(call=case.pull), entry, None,
            candidate, progress, time.monotonic() + 5, lambda item: None)
        return supplied, progress

    def paid_whole(self, case):
        def pull(operation, *, payload, timeout):
            case.calls.append(payload)
            self.assertNotIn('span', payload)
            return case.responses[payload['handle']]
        case.pull = pull
        return case

    def half_room_boundary(self, text):
        prefix, raw = text.split('{"selected":', 1)
        fixed = dict(json.loads('{"selected":' + raw), index=[], candidate_bodies=[],
            remaining_memory_bytes=9500, candidate_inspection=dict(pull_calls=0,
                remaining_pull_calls=4, delivered_records=0, refusals={}))
        return 2 * len(text.encode()) - len((prefix + eager.hook.encoded(fixed)).encode())

    def test_span_only_opening_uses_one_existing_credit_and_trims_utf8_boundary(self):
        # Byte 768 bisects the final character; the API returns exact base64 bytes.
        opening = '界' * 255 + 'ab界'
        case = self.span_case(opening)
        candidate, progress = self.checked_opening(case)
        self.assertEqual(len(case.calls), 2)
        self.assertEqual(case.calls[-1]['span'], dict(offset=0, length=768))
        context = candidate['source_opening_excerpt']
        self.assertEqual(context['origin'], 'span_pull')
        self.assertEqual(context['span']['body'], '界' * 255 + 'ab')
        self.assertEqual(context['span']['end'], 767)
        self.assertEqual(context['span']['sha256'], hashlib.sha256(('界' * 255 + 'ab').encode()).hexdigest())
        self.assertEqual(progress['pull_calls'], 2)
        self.assertEqual(progress['remaining_pull_calls'], 2)

    def test_failed_opening_preserves_original_passage_and_charges_failed_call(self):
        for failure in ('unavailable', 'version', 'source_hash', 'total', 'span_hash', 'offset',
                        'base64', 'mandatory', 'class_c', 'competing', 'malformed_span'):
            with self.subTest(failure=failure):
                case = self.span_case()
                pull = case.pull
                def changed(operation, *, payload, timeout):
                    response = pull(operation, payload=payload, timeout=timeout)
                    if payload['span']['offset'] == 0:
                        if failure == 'unavailable':
                            raise eager.hook.HookError('temporary fixture outage')
                        if failure == 'version': response['selection']['record']['version'] = 2
                        if failure == 'source_hash': response['span']['source_sha256'] = '0' * 64
                        if failure == 'total': response['span']['total_bytes'] += 1
                        if failure == 'span_hash': response['span']['sha256'] = '0' * 64
                        if failure == 'offset': response['span']['offset'] = 1
                        if failure == 'base64':
                            response['span'].pop('body'); response['span']['body_base64'] = 'not base64!'
                        if failure == 'mandatory': response['selection']['mandatory'] = True
                        if failure == 'class_c': response['selection']['record']['class'] = 'C'
                        if failure == 'competing': response['competing'] = [response['selection']]
                        if failure == 'malformed_span': response['span'] = None
                    return response
                case.pull = changed
                candidate, progress = self.checked_opening(case)
                self.assertEqual(candidate['response']['span']['body'], 'Checked prerequisite.')
                self.assertEqual(candidate['source_opening_excerpt'],
                                 dict(status='unavailable', reason='pull_unavailable'))
                self.assertEqual(progress['pull_calls'], 2)
                self.assertEqual(len(case.calls), 2)

    def test_prior_whole_and_required_survive_second_call_receipt_failure(self):
        case = self.span_case(count=2)
        first = case.entries[0]
        body = 'Existing whole independent guidance.'
        first['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        case.responses[first['record_id']]['selection']['record']['body'] = body
        case.result['selected'] = [dict(mandatory=True, record=dict(body='Whole mandatory condition.'))]
        pull = case.pull
        def fourth_fails(operation, *, payload, timeout):
            if payload['handle'] == first['record_id']:
                case.calls.append(payload)
                return case.responses[first['record_id']]
            response = pull(operation, payload=payload, timeout=timeout)
            if payload['span']['offset'] == 0:
                raise eager.hook.BudgetRefused('last credit unavailable')
            return response
        case.pull = fourth_fails
        _, view, _ = case.invoke()
        self.assertEqual(view['selected'], case.result['selected'])
        self.assertEqual(view['candidate_bodies'][0]['response'], case.responses[first['record_id']])
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(view['candidate_inspection']['refusals']['pull_limit'], 1)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
        self.assertEqual(len(case.calls), 2)

    def test_deadline_after_match_does_not_dispatch_opening(self):
        case = self.span_case()
        clock = [100.0]
        pull = case.pull
        def expired(operation, *, payload, timeout):
            response = pull(operation, payload=payload, timeout=timeout)
            clock[0] = 105.01
            return response
        case.pull = expired
        with patch.object(eager.hook.time, 'monotonic', side_effect=lambda: clock[0]):
            _, view, _ = case.invoke()
        self.assertEqual(len(case.calls), 2)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_bodies'][0]['source_opening_excerpt'],
                         dict(status='unavailable', reason='pull_limit'))

    def test_passage_at_opening_needs_no_companion_call_or_duplicate_bytes(self):
        case = self.span_case()
        case.entries[0]['match_span'] = dict(offset=0, length=len(b'Original project: fixture-engine.\n'))
        _, view, _ = case.invoke()
        self.assertEqual(len(case.calls), 2)
        self.assertEqual(view['candidate_bodies'][0]['source_opening_excerpt'],
                         dict(status='passage_starts_at_opening'))

    def test_disjoint_prefix_preserves_multiline_crlf_exactly(self):
        prefix = 'Original task\r\nProject: α\r\n'
        case = self.paid_whole(self.span_case(prefix))
        case.entries[0]['match_span']['offset'] = len(prefix.encode())
        case.entries[0]['match_span']['length'] = len('Earlier unrelated details.')
        _, view, _ = case.invoke()
        candidate = view['candidate_bodies'][0]
        opening = candidate['source_opening_excerpt']['span']
        self.assertEqual(opening['body'], prefix)
        self.assertEqual(opening['end'], candidate['response']['span']['offset'])
        self.assertEqual(opening['sha256'], hashlib.sha256(prefix.encode()).hexdigest())

    def test_pair_too_large_keeps_exact_original_passage_with_missing_context(self):
        calibration = self.paid_whole(self.span_case('Context ' * 96))
        initial_text, initial_view, _ = calibration.invoke()
        self.assertEqual(initial_view['candidate_bodies'][0]['source_opening_excerpt']['status'], 'provided')
        # Put the complete pair beyond the current renderer's byte boundary,
        # independently of the instruction wording or omission presentation.
        budget = self.half_room_boundary(initial_text) - 40
        case = self.paid_whole(self.span_case('Context ' * 96))
        case.config['context_bytes'] = budget
        text, view, _ = case.invoke()
        candidate = view['candidate_bodies'][0]
        self.assertEqual(candidate['response']['span']['body'], 'Checked prerequisite.')
        self.assertEqual(candidate['source_opening_excerpt'],
                         dict(status='unavailable', reason='context_budget'))
        self.assertNotIn('Context Context', text)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], budget)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 1)

    def test_saturated_source_and_opening_stop_before_later_inspection(self):
        calibration = self.paid_whole(self.span_case('Context ' * 96))
        initial_text, initial_view, _ = calibration.invoke()
        self.assertEqual(initial_view['candidate_bodies'][0]['source_opening_excerpt']['status'], 'provided')
        # Fill the current rendered boundary, independent of cue wording. A
        # subsequent inspection must stop before risking already selected context.
        budget = self.half_room_boundary(initial_text)
        case = self.paid_whole(self.span_case('Context ' * 96))
        case.config['context_bytes'] = budget
        case.result['index'].append(dict(case.entries[1], body_sha256='unknown'))
        text, view, _ = case.invoke()
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(view['candidate_bodies'][0]['response']['span']['body'], 'Checked prerequisite.')
        self.assertEqual(view['candidate_bodies'][0]['source_opening_excerpt'],
                         initial_view['candidate_bodies'][0]['source_opening_excerpt'])
        self.assertEqual(len(case.calls), 1)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 3)
        self.assertEqual(view['candidate_inspection']['delivered_records'], 1)
        self.assertNotIn('unverifiable_identity', view['candidate_inspection']['refusals'])
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], budget)


if __name__ == '__main__':
    unittest.main()
