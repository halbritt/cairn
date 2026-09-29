"""Delivery-sized bodies can need excerpts to fit the combined selector input."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import ReceiptCLI, hook


class SelectorFittingTests(unittest.TestCase):
    def run_hook(self, *, semantic_body=None, semantic_entry=None, selector_index=3):
        source = semantic_body or ('x' * 5000 + 'Renew the original lease before it expires.' + 'y' * 2000)
        lexical = ReceiptCLI({str(i): 'Incidental background. ' + 'z' * 6980 for i in range(3)})
        semantic = ReceiptCLI({'useful': source})
        semantic.entries[0].update({'class': 'A', 'match_span': dict(offset=5000, length=100)})
        semantic.entries[0].update(semantic_entry or {})
        for channel, cli in [('lexical', lexical), ('semantic', semantic)]:
            for entry in cli.entries:
                entry['pull_arguments']['receipt_id'] = channel
        inspected = []

        def transport(command, *, body, **kwargs):
            channel = ('semantic' if '--semantic' in command else 'lexical') if command[6] == 'search' \
                else json.loads(body)['receipt_id']
            cli = semantic if channel == 'semantic' else lexical
            response = cli(command, body=body, **kwargs)
            envelope = json.loads(response.stdout)
            if command[6] == 'search':
                data = envelope['data']
                data.update(receipt_id=channel, selected=[dict(body='Keep required context.')])
                if channel == 'semantic':
                    data.update(discovery=dict(state='ready'), page={})
            return subprocess.CompletedProcess(command, response.returncode, json.dumps(envelope), response.stderr)

        def selector(config, schema, prompt, request, timeout, stage='capture', observation=None):
            self.assertIs(schema, hook.SHORTLIST_SCHEMA)
            self.assertLessEqual(len(hook.encoded(request).encode()), 24000)
            inspected.extend(request['candidates'])
            return dict(structured_output=dict(index=selector_index if selector_index < len(inspected) else -1))

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                                      semantic_fallback=True, context_bytes=9500), 'selector-fitting')
            state = {}
            with patch.object(hook, 'bounded_command', side_effect=transport), \
                 patch.object(hook, 'select_json', side_effect=selector):
                result = hook.recall(memory, dict(hook_event_name='UserPromptSubmit', cwd=directory,
                                                 prompt='repair lease renewal'), state)
        self.assertIn('Keep required context.', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(lexical.credits, 1)
        self.assertEqual(semantic.credits, 3)
        self.assertEqual(semantic.pulls, [('useful', 'full')])
        return result['hookSpecificOutput']['additionalContext'], state, inspected

    def test_paid_body_uses_relevant_excerpt_when_combined_selector_input_is_full(self):
        output, state, inspected = self.run_hook()
        self.assertEqual(len(inspected), 4)
        self.assertEqual([view['source_extent'] for view in inspected], ['full_body'] * 3 + ['partial_span'])
        self.assertIn('Renew the original lease', inspected[-1]['body'])
        self.assertIn('Renew the original lease', output)
        self.assertLessEqual(len(output.encode()), 9500)
        self.assertEqual(state['seen'], {})
        self.assertNotIn('selector_input_budget', state['last_recall']['rejected'])

    def test_missing_unsafe_or_oversized_excerpt_keeps_required_context(self):
        cases = [dict(match_span=None), dict(**{'class': 'C'}), dict(conflicts=['conflict']),
                 dict(match_span=dict(offset=3000, length=4096))]
        for entry in cases:
            with self.subTest(entry=entry):
                output, state, inspected = self.run_hook(semantic_entry=entry)
                self.assertEqual(len(inspected), 3)
                self.assertNotIn('Renew the original lease', output)
                self.assertEqual(state['seen'], {})
                self.assertEqual(state['last_recall']['rejected']['selector_input_budget'], 1)

    def test_fallback_rejects_cut_utf8(self):
        source = 'x' * 4999 + '€Renew the original lease before it expires.' + 'y' * 2000
        output, state, inspected = self.run_hook(semantic_body=source)
        self.assertEqual(len(inspected), 3)
        self.assertNotIn('Renew the original lease', output)
        self.assertEqual(state['last_recall']['rejected']['selector_input_budget'], 1)

    def test_complete_body_that_fits_does_not_require_a_valid_hint(self):
        source = 'Renew the original lease before it expires.'
        output, state, inspected = self.run_hook(semantic_body=source, semantic_entry=dict(match_span=None))
        self.assertEqual(len(inspected), 4)
        self.assertEqual(inspected[-1], dict(index=3, body=source, source_extent='full_body'))
        self.assertIn(source, output)
        self.assertEqual(state['seen'], {'useful': 1})
        self.assertNotIn('selector_input_budget', state['last_recall']['rejected'])


if __name__ == '__main__':
    unittest.main()
