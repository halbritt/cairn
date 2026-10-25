"""Optional presentation over canonical search data; real public hook output."""
import copy
import hashlib
import json
import unittest

from test_eager_candidate_context import hook
import test_claude_inbox_recall as claude_fixture


# Current API reasons; a future reason is intentionally not treated as known.
ZEROS = dict.fromkeys(('CONTEXT_MISSING CURRENTNESS_MISMATCH OUTSIDE_VALIDITY '
    'CLASS_NOT_CONSEQUENTIAL AUTHORITY_INACTIVE POLICY_UNENFORCEABLE OPEN_CONFLICT '
    'ATTRIBUTION_UNRECONCILED EVIDENCE_UNAVAILABLE NO_LEXICAL_MATCH REDUNDANT '
    'NO_FAILURE_MATCH NO_ENTITY_MATCH').split(), 0)


def view(text):
    return json.loads(text[text.index('{"selected":'):])


class OptionalSearchViewTests(unittest.TestCase):
    def test_known_zero_only_projection_preserves_unknowns_and_canonical_input(self):
        omitted = dict(ZEROS, OPTIONAL_BUDGET=7, NO_RETRIEVAL_MATCH=93,
                       FUTURE_REASON=0, TOTAL_BUDGET=False, KIND_FILTERED='0',
                       SEARCH_OFFSET=None, BROWSE_OFFSET=-1)
        discovery = dict(state='ready', algorithm='fixture/1', model_sha256='a'*64,
                         scores_sha256='b'*64, coverage=dict(indexed=8, eligible=8), future=0)
        result = dict(index=[], omitted=omitted, discovery=discovery, status='READY')
        before = copy.deepcopy(result)
        selected = [dict(mandatory=True, record=dict(body='Whole required 日本語 guard.'))]
        inspection = dict(pull_calls=0, delivered_records=0, remaining_pull_calls=4, refusals={})
        text = hook.render_agent_candidates(selected, result, 9500, dict(rejected={}), [], inspection)
        shown = view(text)
        self.assertEqual(shown['selected'], selected)
        self.assertEqual(shown['candidate_inspection'], inspection)
        self.assertEqual(shown['candidate_search']['omitted'],
                         {k: v for k, v in omitted.items() if k not in ZEROS})
        self.assertEqual(shown['candidate_search']['discovery'],
                         {k: v for k, v in discovery.items() if k != 'scores_sha256'})
        self.assertEqual(result, before)
        self.assertNotIn('PAGE_OFFSET', shown['candidate_search']['omitted'])

    def test_malformed_discovery_digest_and_counter_containers_remain_visible(self):
        for omitted in (None, [], 'unknown', 0, False):
            for digest in (None, 0, False, 'unknown', {'future': 'opaque'}):
                with self.subTest(omitted=omitted, digest=digest):
                    discovery = dict(state='lexical_fallback', scores_sha256=digest)
                    result = dict(index=[], omitted=omitted, discovery=discovery)
                    before = copy.deepcopy(result)
                    text = hook.render_agent_candidates([], result, 9500, dict(rejected={}))
                    self.assertEqual(view(text)['candidate_search']['omitted'], omitted)
                    self.assertEqual(view(text)['candidate_search']['discovery'], discovery)
                    self.assertEqual(result, before)
        for discovery in ('unknown', [], False):
            result = dict(index=[], discovery=discovery)
            text = hook.render_agent_candidates([], result, 9500, dict(rejected={}))
            self.assertEqual(view(text)['candidate_search']['discovery'], discovery)

    def test_compact_view_distinguishes_known_zero_unknown_and_absent_reasons(self):
        class Observer:
            config = {'recall_view': 'compact/1'}

        result = dict(index=[], status='READY', omitted=dict(OPTIONAL_BUDGET=0,
            FUTURE_REASON=0, NO_LEXICAL_MATCH=3))
        before = copy.deepcopy(result)
        text = hook.render_agent_candidates([], result, 9500, dict(rejected={}), observer=Observer())
        shown = view(text)
        self.assertEqual(shown['schema'], 'cairn.hook-recall-compact/1')
        self.assertEqual(shown['candidate_search']['omitted'], result['omitted'])
        self.assertNotIn('TOTAL_BUDGET', shown['candidate_search']['omitted'])
        self.assertEqual(result, before)

    def test_bound_public_hook_admits_whole_unicode_source_and_later_refusal(self):
        case = claude_fixture.ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
        # Generic synthetic source, deliberately near the automatic half of the 9500-byte envelope.
        body = '日本語 "guard"\n' + 'x' * 1400
        selection = case.fixture['pull']['selection']
        selection.update(mandatory=False, evidence=[], authority=[], reason='checked candidate')
        record = selection['record']
        record.update(body=body, **{'class': 'A'}, lifecycle='active', sensitivity='shareable',
                      attribution_state='self', kind='note', scope=dict(repository='fixture', task='*', run='*'),
                      witness='synthetic witness', written_at='2026-01-01T00:00:00Z',
                      future_provenance={'condition': 'Preserve this nonempty provenance.'})
        entry = case.fixture['search']['index'][0]
        entry.update(body_sha256=hashlib.sha256(body.encode()).hexdigest(), **{'class': 'A'},
                     match_span=dict(offset=0, length=len('日本語 "guard"\n'.encode())))
        # Later candidate lacks a verifiable identity. Its refusal must be included
        # without evicting the already-admitted whole source.
        case.fixture['search']['index'].append(dict(record_id='00000000-0000-4000-8000-000000000009', version=1))
        required = [dict(mandatory=True, record=dict(body='Whole required guard 日本語.' + 'r'*200))]
        case.fixture['search'].update(selected=required, omitted=dict(ZEROS, OPTIONAL_BUDGET=2),
            discovery=dict(state='ready', algorithm='fixture/1', model_sha256='a'*64,
                           scores_sha256='b'*64, coverage=dict(indexed=2, eligible=2)))
        canonical = copy.deepcopy(case.fixture)
        case.freeze()
        event = dict(case.event, prompt='Inspect fixture safeguards.', prompt_id='whole-near-cap')
        first = case.memory_main(event)
        self.assertEqual(first['code'], 0, first)
        text = json.loads(first['stdout'])['hookSpecificOutput']['additionalContext']
        shown = view(text)
        self.assertEqual(shown['selected'], required)
        candidate = shown['candidate_bodies'][0]
        self.assertEqual(candidate['response']['selection']['record'], record)
        self.assertEqual(candidate['pull_arguments'], entry['pull_arguments'])
        self.assertNotIn('span', candidate['response'])
        self.assertEqual(shown['candidate_inspection']['refusals'], dict(unverifiable_identity=1))
        self.assertEqual(shown['candidate_inspection']['pull_calls'], 1)
        wire = len(first['stdout'].encode())
        self.assertLessEqual(wire + shown['remaining_memory_bytes'], 9500)
        self.assertGreater(wire, 3500)
        self.assertEqual(case.fixture, canonical)
        self.assertEqual(len([c for c in case.calls() if c['op'] == 'pull']), 1)
        second = case.memory_main(event)
        self.assertEqual(second['code'], 0, second)
        self.assertNotIn('candidate_bodies', second['stdout'])
        self.assertEqual(len([c for c in case.calls() if c['op'] == 'pull']), 1)

    def test_bound_compact_view_keeps_whole_required_and_optional_without_extra_reads(self):
        case = claude_fixture.ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
        case.mc['recall_view'] = 'compact/1'
        required = [dict(mandatory=True, record=dict(body='Whole mandatory 日本語 source.'))]
        case.fixture['search']['selected'] = required
        case.fixture['search']['omitted'] = dict(ZEROS, OPTIONAL_BUDGET=0, NO_LEXICAL_MATCH=2)
        body = '日本語 "whole"\n' + 'x' * 1400
        case.fixture['pull']['selection']['record']['body'] = body
        case.fixture['search']['index'][0]['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        case.fixture['search']['index'].append(dict(record_id='00000000-0000-4000-8000-000000000009', version=1))
        case.freeze()
        out = case.memory_main(dict(case.event, prompt='Inspect the fixture source.', prompt_id='compact-bound'))
        self.assertEqual(out['code'], 0, out)
        text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        shown = view(text)
        self.assertEqual(shown['schema'], 'cairn.hook-recall-compact/1')
        self.assertEqual(shown['selected'], required)
        self.assertEqual(shown['candidate_search']['omitted'], case.fixture['search']['omitted'])
        self.assertEqual(shown['candidate_bodies'][0]['response'], case.fixture['pull'])
        self.assertEqual(shown['candidate_inspection']['refusals'], dict(unverifiable_identity=1))
        self.assertEqual([call['op'] for call in case.calls()], ['search', 'pull'])
        self.assertLessEqual(len(out['stdout'].encode()) + shown['remaining_memory_bytes'], 9500)

    def test_compact_native_stale_handle_is_reported_without_repeating_the_dead_handle(self):
        for recall_view, refusal, schema in (
                ('compact/1', 'stale_handle', 'cairn.hook-recall-compact/1'),
                ('full', 'whole_pull_unavailable', None)):
            with self.subTest(recall_view=recall_view):
                case = claude_fixture.ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.mc['recall_view'] = recall_view
                entry = case.fixture['search']['index'][0]
                entry.update(body_sha256=hashlib.sha256(b'Current candidate body.').hexdigest(),
                             **{'class': 'A'}, kind='note', summary_span=dict(offset=0, length=12))
                case.fixture['search']['selected'] = []
                case.fixture['fail_pull'] = 'STALE_HANDLE'
                case.freeze()

                out = case.memory_main(dict(case.event, prompt='Inspect the current candidate.'))

                self.assertEqual(out['code'], 0, out)
                text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
                shown = view(text)
                if schema is None:
                    self.assertNotIn('schema', shown)
                else:
                    self.assertEqual(shown['schema'], schema)
                self.assertEqual(shown['index'][0]['pull_arguments'], entry['pull_arguments'])
                self.assertEqual(shown['candidate_inspection']['refusals'], {refusal: 1})
                self.assertEqual(shown['candidate_bodies'], [])
                self.assertNotIn('Current candidate body.', text)
                if recall_view == 'compact/1':
                    self.assertEqual(shown['candidate_recovery'],
                        'This receipt-scoped handle is stale. Search again for a fresh handle '
                        'and read its current source; do not retry this handle.')
                    self.assertLessEqual(len(text.encode()) + shown['remaining_memory_bytes'],
                                         case.mc['context_bytes'])
                else:
                    self.assertNotIn('candidate_recovery', shown)
                self.assertEqual([call['op'] for call in case.calls()], ['search', 'pull'])


if __name__ == '__main__':
    unittest.main()
