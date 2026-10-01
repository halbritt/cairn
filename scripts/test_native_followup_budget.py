"""Native choice retains half the aggregate room when fixed context permits."""
import json
import hashlib
import unittest
import time
from unittest.mock import Mock

import test_eager_candidate_context as eager

hook = eager.hook


class NativeFollowupBudgetTests(unittest.TestCase):
    def test_preview_only_preserves_half_total_with_task_sized_wire_overhead(self):
        control = 'Assignment: ' + 'x' * 1526 + '\n' + 'Control: ' + 'x' * 760
        def measure(text):
            return len((hook.encoded(dict(hookSpecificOutput=dict(
                additionalContext=control + text))) + '\n').encode())
        entries = [dict(record_id=str(i), version=1, summary='"条件" ' * 25,
                        pull_arguments=dict(receipt_id='receipt', handle=str(i))) for i in range(3)]
        status = dict(rejected={})
        text = hook.render_agent_candidates([], dict(index=entries), 9500, status, measure=measure)
        view = json.loads(text[text.index('{"selected":'):])
        self.assertGreaterEqual(view['remaining_memory_bytes'], 4750)
        self.assertEqual(measure(text) + view['remaining_memory_bytes'], 9500)
        self.assertTrue(view['index'])
        self.assertEqual(view['index'], entries[:len(view['index'])])

    def test_fixed_required_above_half_keeps_native_followup_without_eager_calls(self):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        required = 'Required condition 日本語 "quoted". ' * 150
        case.result['selected'] = [dict(mandatory=True, record=dict(body=required))]
        text, view, state = case.invoke()
        self.assertEqual(view['selected'], case.result['selected'])
        self.assertEqual(view['index'], [])
        self.assertEqual(view['candidate_bodies'], [])
        self.assertGreater(view['remaining_memory_bytes'], 0)
        self.assertLess(view['remaining_memory_bytes'], 4750)
        self.assertEqual(state['last_recall']['outcome'], 'delegated')
        self.assertEqual(case.calls, [])
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)

    def test_fixed_exactly_half_does_not_spend_speculative_calls(self):
        result = dict(selected=[], index=[], omitted={})
        plain = hook.render_agent_candidates([], result, 9500, dict(rejected={}), [],
            dict(pull_calls=0, remaining_pull_calls=4, delivered_records=0, refusals={}))
        overhead = 4750 - len(plain.encode())
        def measure(text):
            return overhead + len(text.encode())
        result['index'] = [dict(record_id='optional', version=1, body_sha256='a' * 64,
                               summary='candidate', pull_arguments=dict(handle='optional'))]
        # returned_entries is presentation data, so match its one-digit width.
        memory = Mock()
        memory.call.side_effect = AssertionError('fixed context leaves no automatic capacity')
        status = dict(rejected={})
        text = hook.eager_agent_candidates(memory, result, 9500, status, time.monotonic()+5, measure=measure)
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['remaining_memory_bytes'], 4750)
        self.assertEqual(view['candidate_inspection']['pull_calls'], 0)
        self.assertEqual(measure(text), 4750)
        memory.call.assert_not_called()

    def test_long_inbox_task_remains_whole_with_a_usable_native_grant(self):
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        case = ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.body = 'Implement the fixture change and preserve its documented boundaries. ' * 30
        historical = case.fixture['history']['versions'][0]
        historical.update(body=case.body, body_bytes=len(case.body.encode()),
                          body_sha256=hashlib.sha256(case.body.encode()).hexdigest())
        required = dict(mandatory=True, record=dict(body='Keep the whole required condition.'))
        case.fixture['search']['selected'] = [required]
        case.freeze()
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn(case.body, text)
        view = json.loads(text[text.index('{"selected":'):])
        self.assertEqual(view['selected'], [required])
        self.assertGreater(view['remaining_memory_bytes'], 0)
        ledger = case.ledger()
        grant = ledger['grants'][ledger['active']]
        self.assertEqual(grant['native_allowance_bytes'], view['remaining_memory_bytes'])
        self.assertLessEqual(len(out['stdout'].encode()) + grant['native_allowance_bytes'], 9500)

    def test_smaller_ordinary_budget_still_delivers_whole_candidate_when_it_fits(self):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.config['context_bytes'] = 5000
        case.result['index'] = case.entries[:1]
        text, view, _ = case.invoke()
        self.assertEqual(view['candidate_bodies'][0]['response'], case.responses[case.entries[0]['record_id']])
        self.assertGreaterEqual(view['remaining_memory_bytes'], 2500)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 5000)

    def test_whole_required_over_total_is_refused_at_public_hook(self):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.result['selected'] = [dict(mandatory=True, record=dict(body='mandatory ' * 1500))]
        with self.assertRaises(hook.HookError):
            case.invoke()
        self.assertEqual(case.calls, [])


if __name__ == '__main__':
    unittest.main()
