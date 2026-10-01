"""Initial optional inspection leaves measured room and calls for task-driven choice."""
import hashlib
import json
import unittest

import test_eager_candidate_context as eager


class SelectionReserveTests(unittest.TestCase):
    def fixture(self):
        case = eager.EagerCandidateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_public_hook_receipt_refusals_leave_two_calls_for_native_choice(self):
        case = self.fixture()
        case.optional(2)
        case.entries[0]['match_span'] = dict(offset=0, length=len('Old context. '))
        case.pull = case.receipt_span
        text, view, state = case.invoke()
        self.assertEqual(len(case.calls), 2)
        self.assertEqual([bool(c.get('span')) for c in case.calls], [False, True])
        self.assertEqual(view['candidate_inspection']['pull_calls'], 2)
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 2)
        self.assertEqual(len(view['candidate_bodies']), 1)
        self.assertEqual(view['candidate_bodies'][0]['response']['source_extent'], 'partial_span')
        self.assertIn(case.entries[1], view['index'])
        self.assertEqual(state['seen'], {})
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 9500)


    def test_bound_inbox_preserves_half_post_fixed_wire_room_and_total_policy(self):
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        self.bound_reserve(ClaudeInboxRecallTests())

    def test_bound_codex_preserves_half_post_fixed_wire_room_and_total_policy(self):
        from test_inbox_recall_bridge import InboxRecallBridgeTests
        self.bound_reserve(InboxRecallBridgeTests())

    def bound_reserve(self, case):
        case.setUp()
        self.addCleanup(case.doCleanups)
        required = 'Whole instruction: preserve 日本語 and "quoted" constraints.\n'
        case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        body = 'Detailed optional guidance. ' * 100
        entry = case.fixture['search']['index'][0]
        entry['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        case.fixture['pull']['selection']['record']['body'] = body
        case.freeze()
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        envelope = json.loads(out['stdout'])
        text = envelope['hookSpecificOutput']['additionalContext']
        prefix, raw = text.split('{"selected":', 1)
        view = json.loads('{"selected":' + raw)
        fixed = dict(view, index=[], candidate_bodies=[], remaining_memory_bytes=9500,
                     candidate_inspection=dict(pull_calls=0, remaining_pull_calls=4,
                                               delivered_records=0, refusals={}))
        envelope['hookSpecificOutput']['additionalContext'] = prefix + eager.hook.encoded(fixed)
        fixed_bytes = len((json.dumps(envelope, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
        reserve = (9500 - fixed_bytes) // 2
        self.assertGreater(reserve, 1000)
        self.assertGreaterEqual(view['remaining_memory_bytes'], reserve)
        self.assertLessEqual(len(out['stdout'].encode()), 9500 - reserve)
        self.assertIn(case.body, text)
        self.assertIn(required, view['selected'][0]['record']['body'])
        self.assertIn('9500', prefix)  # Total policy is not the eager ceiling.
        self.assertEqual(view['index'][0]['pull_arguments'], entry['pull_arguments'])
        self.assertEqual(view['candidate_bodies'], [])
        self.assertEqual(view['candidate_inspection']['pull_calls'], 1)
        ledger = json.loads((case.root / 'memory' / (case.event['session_id'] + '.inbox-recall.json')).read_text())
        grant = ledger['grants'][ledger['active']]
        self.assertEqual(grant['native_allowance_bytes'], view['remaining_memory_bytes'])
        self.assertEqual(grant['output_bytes'], len(out['stdout'].encode()))

    def test_saturated_whole_survives_later_receipt_failure(self):
        case = self.fixture()
        case.config['context_bytes'] = 5073
        case.optional()
        large, later, earlier = case.entries
        large['match_span'] = dict(offset=0, length=4096)
        case.result['index'] = [earlier, large, later]
        body = 'X' * 1154
        earlier['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        case.responses[earlier['record_id']]['selection']['record']['body'] = body
        def pull(operation, *, payload, timeout):
            if payload['handle'] == large['record_id']:
                return case.receipt_span(operation, payload=payload, timeout=timeout)
            return eager.EagerCandidateTests.pull(case, operation, payload=payload, timeout=timeout)
        case.pull = pull
        text, view, _ = case.invoke()
        self.assertEqual([item['response'] for item in view['candidate_bodies']],
                         [case.responses[earlier['record_id']]])
        self.assertEqual(len(case.calls), 1)
        self.assertEqual(case.calls[0], earlier['pull_arguments'])
        self.assertEqual(view['candidate_inspection']['remaining_pull_calls'], 3)
        self.assertLessEqual(len(text.encode()) + view['remaining_memory_bytes'], 5073)

    def test_near_cap_required_context_is_whole_without_optional_calls(self):
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        case = ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        required = 'Mandatory current condition. ' * 230
        case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        case.freeze()
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn(required, text)
        self.assertIn(case.body, text)
        self.assertNotIn('candidate_bodies', text)
        self.assertIn('optional_recall_unavailable', text)
        self.assertEqual([c for c in case.calls() if c['op'] == 'pull'], [])
        ledger = case.ledger()
        grant = ledger['grants'][ledger['active']]
        self.assertLessEqual(len(out['stdout'].encode()) + grant['native_allowance_bytes'], 9500)


if __name__ == '__main__':
    unittest.main()
