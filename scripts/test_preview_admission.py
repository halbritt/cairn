"""Host admission of ranked previews through recall's actual CLI boundary."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook


class PreviewAdmissionTests(unittest.TestCase):
    def run_hook(self, indices, lexical_count=3, semantic_count=5, shared_receipt=False,
                 starting_credits=4, body_index=0, overlap=False, refused_handles=()):
        entries = {}
        for channel, count in (('lexical', lexical_count), ('semantic', semantic_count)):
            receipt = 'shared' if shared_receipt else channel
            entries[channel] = [dict(record_id=f'{channel}-{i}', version=1, summary='candidate',
                                     pull_arguments=dict(receipt_id=receipt, handle=f'{channel}-{i}',
                                                         request_id=f'{channel}-{i}')) for i in range(count)]
        if overlap:
            for lexical, semantic in zip(entries['lexical'], entries['semantic']):
                semantic['record_id'] = lexical['record_id']
        credits = {('shared' if shared_receipt else channel): starting_credits for channel in entries}
        pulls, selections = [], []

        def transport(command, *, body, **kwargs):
            if command[6] == 'search':
                channel = 'semantic' if '--semantic' in command else 'lexical'
                receipt = 'shared' if shared_receipt else channel
                data = dict(status='READY', destination=dict(name='hosted'), index=entries[channel],
                            receipt_id=receipt, credits_remaining=credits[receipt],
                            selected=[dict(body='Required instruction')])
                if channel == 'semantic':
                    data.update(discovery=dict(state='ready'), page=dict(offset=0, next_offset=None))
            else:
                self.assertEqual(command[6], 'pull')
                args = json.loads(body)
                receipt = args['receipt_id']
                self.assertGreater(credits[receipt], 0, 'host dispatched beyond the receipt allowance')
                pulls.append(args['handle'])
                if args['handle'] in refused_handles:
                    return subprocess.CompletedProcess(command, 1, json.dumps(dict(status='BUDGET_REFUSED')), '')
                credits[receipt] -= 1
                source = next(entry for group in entries.values() for entry in group
                              if entry['pull_arguments']['handle'] == args['handle'])
                data = dict(selection=dict(record=dict(record_id=source['record_id'], version=1,
                                                        body='Useful ' + args['handle'])),
                            credits_remaining=credits[receipt])
            return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=data)), '')

        def select(config, schema, prompt, request, timeout, stage="capture", observation=None):
            preview = schema is hook.PREVIEW_SCHEMA
            selections.append('preview' if preview else 'body')
            return dict(structured_output=dict(indices=indices) if preview else dict(index=body_index))

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                                      semantic_fallback=True, context_bytes=9500), 'admission-test')
            event = dict(hook_event_name='UserPromptSubmit', cwd=directory, prompt='recover lease expiry')
            state = {}
            with patch.object(hook, 'bounded_command', side_effect=transport), \
                 patch.object(hook, 'select_json', side_effect=select):
                result = hook.recall(memory, event, state)
        self.assertIn('Required instruction', result['hookSpecificOutput']['additionalContext'])
        return result, state, pulls, selections

    def test_duplicate_channels_leave_room_for_later_ranked_guidance(self):
        result, state, pulls, selections = self.run_hook(list(range(8)), overlap=True, body_index=4)
        self.assertEqual(pulls, ['lexical-0', 'lexical-1', 'lexical-2', 'semantic-3', 'semantic-4'])
        self.assertEqual(state['seen'], {'semantic-4': 1})
        self.assertEqual(state['last_recall']['preview_receipt_budget_dropped'], 0)
        self.assertEqual(selections, ['preview', 'body'])
        self.assertIn('Useful semantic-4', result['hookSpecificOutput']['additionalContext'])

    def test_byte_refusals_leave_credit_for_later_ranked_candidates(self):
        result, state, pulls, selections = self.run_hook(list(range(8)), lexical_count=0,
            semantic_count=8, refused_handles=('semantic-0', 'semantic-1'), body_index=3)
        self.assertEqual(pulls, [f'semantic-{i}' for i in range(6)])
        self.assertEqual(state['seen'], {'semantic-5': 1})
        self.assertEqual(state['last_recall']['preview_receipt_budget_dropped'], 2)
        self.assertEqual(state['last_recall']['receipt_pull_calls']['semantic'], 6)
        self.assertIn('Useful semantic-5', result['hookSpecificOutput']['additionalContext'])

    def test_alternate_receipt_can_retry_a_refused_duplicate(self):
        result, state, pulls, selections = self.run_hook(list(range(8)), overlap=True,
            refused_handles=('lexical-0',), body_index=2)
        self.assertEqual(pulls, ['lexical-0', 'lexical-1', 'lexical-2',
                                 'semantic-0', 'semantic-3', 'semantic-4'])
        self.assertEqual(state['seen'], {'lexical-0': 1})
        self.assertIn('Useful semantic-0', result['hookSpecificOutput']['additionalContext'])

    def test_overselected_receipt_keeps_ranked_valid_subset(self):
        # Five semantic choices plus three lexical choices: retain the first
        # four semantic choices and all three lexical choices in ranked order.
        result, state, pulls, selections = self.run_hook([3, 0, 4, 1, 5, 2, 6, 7])
        self.assertEqual(pulls, ['semantic-0', 'lexical-0', 'semantic-1', 'lexical-1',
                                 'semantic-2', 'lexical-2', 'semantic-3'])
        self.assertEqual(selections, ['preview', 'body'])
        self.assertEqual(state['seen'], {'semantic-0': 1})
        self.assertEqual(state['last_recall']['preview_admitted'], 7)
        self.assertEqual(state['last_recall']['preview_receipt_budget_dropped'], 1)
        self.assertIn('Useful semantic-0', result['hookSpecificOutput']['additionalContext'])

    def test_alias_channels_share_one_actual_receipt_allowance(self):
        for credits in (4, 2):
            with self.subTest(credits=credits):
                result, state, pulls, selections = self.run_hook(list(range(7, -1, -1)),
                    lexical_count=4, semantic_count=4, shared_receipt=True, starting_credits=credits)
                self.assertEqual(pulls, ['semantic-3', 'semantic-2', 'semantic-1', 'semantic-0'][:credits])
                self.assertEqual(selections, ['preview', 'body'])
                self.assertEqual(state['last_recall']['preview_admitted'], credits)
                self.assertEqual(state['last_recall']['preview_receipt_budget_dropped'], 8 - credits)
                self.assertEqual(state['seen'], {'semantic-3': 1})
                self.assertIn('Useful semantic-3', result['hookSpecificOutput']['additionalContext'])

    def test_structurally_invalid_verdict_is_not_salvaged(self):
        for indices in (None, '0', [0, 0], [0, 10], [-1], [True], [0.5], list(range(9))):
            with self.subTest(indices=indices):
                result, state, pulls, selections = self.run_hook(indices, lexical_count=5, semantic_count=5)
                self.assertEqual(pulls, [])
                self.assertEqual(selections, ['preview'])
                self.assertEqual(state['seen'], {})
                self.assertIn('preview_invalid_verdict', state['last_recall']['rejected'])
                self.assertNotIn('Useful', result['hookSpecificOutput']['additionalContext'])

    def test_trimmed_admission_still_requires_positive_body_verdict(self):
        result, state, pulls, selections = self.run_hook([3, 0, 4, 1, 5, 2, 6, 7], body_index=-1)
        self.assertEqual(len(pulls), 7)
        self.assertEqual(selections, ['preview', 'body'])
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['rejected']['selector_not_relevant'], 7)
        self.assertNotIn('Useful', result['hookSpecificOutput']['additionalContext'])


if __name__ == '__main__':
    unittest.main()
