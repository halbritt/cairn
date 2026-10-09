"""Delivery-budget regressions from CAIRN-146; fixtures only, no provider.

F1 checks bound Claude public-main stdout. F4 checks internal ambient
additionalContext, without asserting a cap on its serialized hook envelope.
"""
import unittest
from unittest.mock import patch

import test_claude_inbox_recall as bound
import test_claude_lifecycle as ambient

hook = ambient.hook


class BoundRequiredRefreshTests(unittest.TestCase):
    # Reuse fixture helpers without inheriting and rerunning the existing tests.
    freeze = bound.ClaudeInboxRecallTests.freeze
    freeze_base = bound.ClaudeInboxRecallTests.freeze_base
    memory_main = bound.ClaudeInboxRecallTests.memory_main
    compact_event = bound.ClaudeInboxRecallTests.compact_event
    ledger = bound.ClaudeInboxRecallTests.ledger

    def test_required_refresh_over_cap_refuses_instead_of_emitting(self):
        # Each event gets its own active grant: compact must not inherit a
        # pending requirement from a previous repeated-prompt refusal.
        for name, expected_code in (('repeated_prompt', 2), ('compact', 1)):
            with self.subTest(refresh=name):
                bound.ClaudeInboxRecallTests.setUp(self)
                self.fixture['search']['selected'] = [dict(
                    mandatory=True, record=dict(body='Small required guard.'))]
                self.freeze()
                event = dict(self.event, prompt='Inspect the fixture migration safeguards.')
                first = self.memory_main(event)
                self.assertEqual(first['code'], 0, first)
                before = self.ledger()
                self.assertIsNotNone(before.get('active'))
                self.assertFalse(before['grants'][before['active']]['required']['pending'])

                budget = self.mc['context_bytes']
                oversized = '必須条件' * 1000
                self.assertLess(len(oversized), budget)
                self.assertGreater(len(oversized.encode('utf-8')), budget)
                self.fixture['search']['selected'] = [dict(
                    mandatory=True, record=dict(body=oversized))]
                self.freeze()
                refresh = event if name == 'repeated_prompt' else self.compact_event()
                out = self.memory_main(refresh)
                self.assertNotIn(oversized[:64], out['stdout'],
                                 'oversized required context was emitted')
                self.assertLessEqual(len(out['stdout'].encode('utf-8')), budget,
                                     'hook stdout exceeds the configured cap')
                # UserPromptSubmit blocks with 2; Claude SessionStart reports
                # failure with 1 and retains pending requirements for next UPS.
                self.assertEqual(out['code'], expected_code, out)
                self.assertEqual(out['stdout'], '')
                after = self.ledger()
                self.assertEqual(after['active'], before['active'])
                self.assertTrue(after['grants'][after['active']]['required']['pending'])


class AmbientUtf8PreviewTests(unittest.TestCase):
    setUp = ambient.RecallCandidateTests.setUp
    entry = ambient.RecallCandidateTests.entry

    def test_multibyte_previews_stay_within_context_bytes(self):
        self.memory.config['context_bytes'] = budget = 1500
        entries = [self.entry(f'note-{i}', 'recover lease expiry ' + '界' * 90)
                   for i in range(6)]

        def service(command, **kwargs):
            if command[len(self.memory.command)] == 'search':
                return dict(ok=True, data=dict(status='READY',
                    destination=dict(name='hosted', allow_local=False), index=entries))
            raise hook.HookError('unavailable')

        # Substitute the CLI service boundary; search, packing and byte
        # accounting all run unchanged. Unavailable pulls leave whole previews.
        with patch.object(hook, 'run_json', side_effect=service):
            state = {}
            result = hook.recall(self.memory, self.event, state)
        text = result['hookSpecificOutput']['additionalContext']
        delivered = [entry['summary'] for entry in entries if entry['summary'] in text]
        self.assertGreaterEqual(len(delivered), 1, 'fixture must deliver a whole preview')
        self.assertLessEqual(len(text.encode('utf-8')), budget,
                             'additionalContext exceeds context_bytes in UTF-8 bytes')
        self.assertEqual(state['last_recall']['bytes'], len(text.encode('utf-8')))


if __name__ == '__main__':
    unittest.main()
