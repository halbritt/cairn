"""Preview time allocation through public recall and the CLI JSON boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import ReceiptCLI, hook


class PreviewDeadlineTests(unittest.TestCase):
    def run_hook(self, search_seconds, preview_seconds, pull_seconds=0, body_seconds=0):
        clock = [0.0]
        cli = ReceiptCLI({str(i): 'Useful guidance ' + str(i) for i in range(5)})
        searches, selectors = [], []

        def transport(command, *, body, timeout, **kwargs):
            if command[6] == 'search':
                duration = search_seconds[len(searches)]
                searches.append(timeout)
            else:
                duration = pull_seconds
            self.assertLessEqual(duration, timeout)
            clock[0] += duration
            result = cli(command, body=body, timeout=timeout, **kwargs)
            if command[6] == 'search':
                response = json.loads(result.stdout)
                response['data']['selected'] = [dict(body='Required instruction')]
                if '--semantic' in command:
                    offset = int(command[command.index('--offset') + 1])
                    response['data'].update(discovery=dict(state='ready'),
                                            page=dict(offset=offset, next_offset=offset + 10))
                result.stdout = json.dumps(response)
            return result

        def selector(config, schema, prompt, request, timeout):
            preview = schema is hook.PREVIEW_SCHEMA
            selectors.append(dict(stage='preview' if preview else 'body', timeout=timeout))
            duration = preview_seconds if preview else body_seconds
            clock[0] += min(duration, timeout)
            if duration > timeout:
                raise hook.HookError('command timed out; memory operation not confirmed')
            return dict(structured_output=dict(indices=[0]) if preview else dict(index=0))

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                                      semantic_fallback=True, context_bytes=9500), 'preview-test')
            event = dict(hook_event_name='UserPromptSubmit', cwd=directory, prompt='recover lease expiry')
            state = {}
            with patch.object(hook.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(hook, 'bounded_command', side_effect=transport), \
                 patch.object(hook, 'select_json', side_effect=selector):
                result = hook.recall(memory, event, state)
        self.assertEqual(len(searches), 5)
        self.assertLessEqual(clock[0], 11)
        self.assertIn('Required instruction', result['hookSpecificOutput']['additionalContext'])
        return result, state, cli.pulls, selectors

    def test_preview_longer_than_three_seconds_still_requires_body_verification(self):
        result, state, pulls, selectors = self.run_hook([1, 0, 0, 0, 0], 4, pull_seconds=0.5)
        self.assertIn('Useful guidance 0', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'0': 1})
        self.assertEqual(pulls, [('0', 'full')])
        self.assertEqual(selectors, [dict(stage='preview', timeout=5), dict(stage='body', timeout=5.5)])

    def test_slow_discovery_clips_preview_and_expired_hook_stops_later_work(self):
        result, state, pulls, selectors = self.run_hook([1, 2, 2, 2, 1.75], 2.25)
        self.assertEqual(selectors, [dict(stage='preview', timeout=2.25)])
        self.assertEqual(pulls, [])
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['elapsed_seconds'], 11)
        self.assertNotIn('Useful guidance', result['hookSpecificOutput']['additionalContext'])

    def test_preview_timeout_preserves_required_context_without_retry(self):
        result, state, pulls, selectors = self.run_hook([1, 0, 0, 0, 0], 6)
        self.assertEqual(selectors, [dict(stage='preview', timeout=5)])
        self.assertEqual(pulls, [])
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['elapsed_seconds'], 6)
        self.assertEqual(state['last_recall']['rejected']['preview_timeout'], 25)
        self.assertNotIn('Useful guidance', result['hookSpecificOutput']['additionalContext'])

    def test_long_preview_and_pulls_do_not_reset_body_deadline(self):
        result, state, pulls, selectors = self.run_hook([1, 0, 0, 0, 0], 5,
                                                      pull_seconds=1.5, body_seconds=4)
        self.assertEqual(selectors, [dict(stage='preview', timeout=5), dict(stage='body', timeout=3.5)])
        self.assertEqual(pulls, [('0', 'full')])
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['last_recall']['elapsed_seconds'], 11)
        self.assertEqual(state['last_recall']['rejected']['verification_timeout'], 1)
        self.assertNotIn('Useful guidance', result['hookSpecificOutput']['additionalContext'])


if __name__ == '__main__':
    unittest.main()
