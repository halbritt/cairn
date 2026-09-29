"""Distinct checked passages through the lifecycle hook's CLI boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook


class PassageAdmissionTests(unittest.TestCase):
    def run_hook(self, source, semantic_span, *, lexical_span=None, semantic_version=1,
                 refuse_lexical_full=False, selector_limit=hook.SELECTOR_INPUT_BYTES):
        digest = hashlib.sha256(source.encode()).hexdigest()
        credits = {'lexical': 4, 'semantic': 4}
        pulls, inspected = [], []

        def transport(command, *, body, **kwargs):
            if command[6] == 'search':
                channel = 'semantic' if '--semantic' in command else 'lexical'
                entry = dict(record_id='note', version=semantic_version if channel == 'semantic' else 1,
                             summary='lease handling', body_sha256=digest,
                             **{'class': 'A'}, pull_arguments=dict(receipt_id=channel, handle='note', request_id=channel))
                if channel == 'semantic':
                    entry['match_span'] = semantic_span
                else:
                    entry['summary_span'] = lexical_span or dict(offset=0, length=min(154, len(source.encode())))
                data = dict(status='READY', destination=dict(name='hosted'), index=[entry], receipt_id=channel,
                            credits_remaining=credits[channel], selected=[dict(body='Keep required context.')])
                if channel == 'semantic':
                    data.update(discovery=dict(state='ready'), page={})
            else:
                args = json.loads(body)
                channel = args['receipt_id']
                if refuse_lexical_full and channel == 'lexical' and 'span' not in args:
                    return subprocess.CompletedProcess(command, 1, json.dumps(dict(status='BUDGET_REFUSED')), '')
                self.assertGreater(credits[channel], 0)
                credits[channel] -= 1
                pulls.append(channel)
                data = dict(selection=dict(record=dict(record_id='note',
                            version=semantic_version if channel == 'semantic' else 1, body=source, **{'class': 'A'})),
                            credits_remaining=credits[channel])
                if 'span' in args:
                    start = args['span']['offset']
                    part = source.encode()[start:start + args['span']['length']]
                    data['selection']['record']['body'] = ''
                    data['span'] = dict(offset=start, end=start + len(part), body=part.decode(),
                                        total_bytes=len(source.encode()), source_sha256=digest,
                                        sha256=hashlib.sha256(part).hexdigest())
            return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=data)), '')

        def selector(config, schema, prompt, request, timeout, stage='capture', observation=None):
            self.assertIs(schema, hook.SHORTLIST_SCHEMA)
            inspected.extend(request['candidates'])
            choice = next((i for i, candidate in enumerate(inspected)
                           if 'Renew the original lease' in candidate['body']), -1)
            return dict(structured_output=dict(index=choice))

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                                      semantic_fallback=True, context_bytes=9500), 'passage-admission')
            state = {}
            with patch.object(hook, 'bounded_command', side_effect=transport), \
                 patch.object(hook, 'select_json', side_effect=selector), \
                 patch.object(hook, 'SELECTOR_INPUT_BYTES', selector_limit):
                result = hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=directory,
                                                 prompt='repair lease renewal'), state)
        self.assertIn('Keep required context.', result['hookSpecificOutput']['additionalContext'])
        return result, state, pulls, inspected, credits

    def test_distinct_passage_of_same_note_can_supply_applicable_guidance(self):
        source = 'Background. ' + 'x' * 7000 + '\nRenew the original lease before it expires.\n' + 'y' * 7000
        result, state, pulls, inspected, credits = self.run_hook(source,
            dict(offset=source.index('Renew'), length=100))
        self.assertEqual(pulls, ['lexical', 'semantic'])
        self.assertEqual(len(inspected), 2)
        self.assertNotIn('Renew the original lease', inspected[0]['body'])
        self.assertIn('Renew the original lease', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {})
        self.assertEqual(credits, {'lexical': 3, 'semantic': 3})
        self.assertEqual(state['last_recall']['source_extent'], 'partial_span')

    def test_full_body_covers_same_version_without_another_pull(self):
        result, state, pulls, inspected, credits = self.run_hook(
            'Renew the original lease before it expires.', dict(offset=0, length=20))
        self.assertEqual(pulls, ['lexical'])
        self.assertEqual(len(inspected), 1)
        self.assertEqual(credits, {'lexical': 3, 'semantic': 4})
        self.assertEqual(state['seen'], {'note': 1})

    def test_full_body_does_not_cover_another_version(self):
        _, _, pulls, inspected, _ = self.run_hook(
            'Renew the original lease before it expires.', dict(offset=0, length=20), semantic_version=2)
        self.assertEqual(pulls, ['lexical', 'semantic'])
        self.assertEqual(len(inspected), 2)

    def test_identical_checked_partials_are_not_repeated_or_refunded(self):
        source = 'Renew the original lease before it expires. ' + 'x' * 14000
        for offset in (0, 13900):
            with self.subTest(offset=offset):
                _, state, pulls, inspected, credits = self.run_hook(source,
                    dict(offset=offset, length=1536 if offset == 0 else 4096),
                    lexical_span=dict(offset=offset, length=100))
                self.assertEqual(pulls, ['lexical', 'semantic'])
                self.assertEqual(len(inspected), 1)
                self.assertEqual(credits, {'lexical': 3, 'semantic': 3})
                self.assertEqual(state['seen'], {})
                self.assertEqual(inspected[0]['body'], source[offset:offset + 1536])

    def test_overlapping_different_passages_remain_eligible(self):
        source = 'Background. ' + 'x' * 1000 + 'Renew the original lease.' + 'y' * 14000
        _, _, pulls, inspected, _ = self.run_hook(source, dict(offset=900, length=1536))
        self.assertEqual(pulls, ['lexical', 'semantic'])
        self.assertEqual(len(inspected), 2)
        self.assertNotEqual(inspected[0]['body'], inspected[1]['body'])

    def test_partial_does_not_hide_full_body_from_another_receipt(self):
        source = 'x' * 1600 + 'Renew the original lease before it expires.'
        result, state, pulls, inspected, credits = self.run_hook(source,
            dict(offset=0, length=1536), refuse_lexical_full=True)
        self.assertEqual(pulls, ['lexical', 'semantic'])
        self.assertEqual([view['source_extent'] for view in inspected], ['partial_span', 'full_body'])
        self.assertIn('Renew the original lease', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'note': 1})
        self.assertEqual(credits, {'lexical': 3, 'semantic': 3})

    def test_budget_rejected_partial_does_not_hide_smaller_passage(self):
        source = 'x' * 7000 + 'Renew the original lease before it expires.' + 'y' * 7000
        result, state, pulls, inspected, credits = self.run_hook(source,
            dict(offset=7000, length=100), selector_limit=1600)
        self.assertEqual(pulls, ['lexical', 'semantic'])
        self.assertEqual(len(inspected), 1)
        self.assertEqual(state['last_recall']['rejected']['selector_input_budget'], 1)
        self.assertLessEqual(state['last_recall']['selector_input_bytes'], 1600)
        self.assertEqual(credits, {'lexical': 3, 'semantic': 3})
        self.assertIn('Renew the original lease', result['hookSpecificOutput']['additionalContext'])


if __name__ == '__main__':
    unittest.main()
