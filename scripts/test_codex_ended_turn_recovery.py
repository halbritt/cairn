"""CAIRN-179: explicit settlement of a held Codex attempt whose queue turn ended on a provider
error (for example usage_limit_exceeded) without running Stop. Real pinned rollout files and a
mocked store; nothing is replayed or reissued."""
import contextlib
import hashlib
import io
import json
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

from test_codex_refusal_recovery import CodexBase
from test_postclaim_refusal_settlement import coord, SESSION, PROMPT, ATTEMPT

OTHER_TURN = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


class EndedTurnBase(CodexBase):
    def setUp(self):
        super().setUp()
        self.state.pop('wake_refusal_diagnostic')
        start = int(datetime.fromisoformat('2026-10-18T23:00:00+00:00').timestamp())
        self.records = [
            dict(timestamp='2026-10-18T22:59:59.000Z', ordinal=8, type='event_msg', payload=dict(
                type='task_complete', turn_id=OTHER_TURN, root_turn_id=OTHER_TURN, last_agent_message='earlier')),
            dict(timestamp='2026-10-18T23:00:00.000Z', ordinal=10, type='event_msg', payload=dict(
                type='task_started', turn_id=PROMPT, root_turn_id=PROMPT, started_at=start,
                model_context_window=258400, collaboration_mode_kind='default',
                turn_attribution=dict(turn_id=PROMPT, root_turn_id=PROMPT, turn_trigger='queue',
                                      parent_turn_id=None, initiating_agent_path=None))),
            dict(timestamp='2026-10-18T23:00:00.100Z', ordinal=11, type='turn_context', payload=dict(
                turn_id=PROMPT, root_turn_id=PROMPT, cwd=str(self.root))),
            dict(timestamp='2026-10-18T23:00:00.150Z', ordinal=12, type='response_item', payload=dict(
                type='message', role='user', content=[dict(type='input_text', text='wake')])),
            dict(timestamp='2026-10-18T23:00:00.160Z', ordinal=13, type='event_msg', payload=dict(
                type='item_completed', turn_id=PROMPT, thread_id=SESSION,
                item=dict(type='UserMessage', id='x', content=[]))),
            dict(timestamp='2026-10-18T23:00:00.300Z', ordinal=14, type='response_item', payload=dict(
                type='message', role='developer', content=[dict(type='input_text', text='Cairn context')])),
            dict(timestamp='2026-10-18T23:00:00.900Z', ordinal=15, type='event_msg', payload=dict(
                type='token_count', info=None)),
            dict(timestamp='2026-10-18T23:00:01.000Z', ordinal=16, type='event_msg', payload=dict(
                type='task_complete', turn_id=PROMPT, root_turn_id=PROMPT, last_agent_message=None,
                error=dict(message='usage limit', codex_error_info='usage_limit_exceeded'),
                started_at=start, completed_at=start + 1, duration_ms=1000)),
        ]
        self.write_records()
        self.health = 'systemError'
        self.save()

    def write_records(self):
        super().write_records()
        self.span = [self.locators[1], self.locators[-1]]

    def recover(self, check_only=False):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
             patch.object(coord, 'probe_codex_native_health',
                          side_effect=lambda config, state, **kw: dict(status_type=self.health)):
            return coord.recover_codex_ended_turn(self.cc, SESSION, ATTEMPT, self.span, check_only=check_only)


class EndedTurnRecoveryTests(EndedTurnBase):
    def test_check_only_reports_eligible_without_mutation(self):
        before = self.path.read_bytes()
        result = self.recover(check_only=True)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['native_record']['codex_error_info'], 'usage_limit_exceeded')
        self.assertEqual(result['native_record']['records'], self.span)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def test_settles_turn_ended_without_replay(self):
        result = self.recover()
        self.assertEqual(result['settlement'], 'turn_ended')
        self.assertEqual(result['recovery_origin'], 'codex_ended_turn')
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        self.assertEqual({op for op, _ in self.ops}, {'session-inbox-reconcile'})
        state = self.load()
        self.assertNotIn('inbox_attempt', state)
        self.assertEqual(state['codex_ended_turn']['native_turn_id'], PROMPT)

    def test_journaled_completion_reconciles_as_delivery_completed(self):
        def api(config, op, request, **kwargs):
            self.ops.append((op, request))
            return dict(attempt_id=request['attempt_id'], finished_at='now', reason=request['reason'])
        with patch.object(coord, 'call', side_effect=api), patch.object(coord, 'recover_inbox'), \
             patch.object(coord, 'flush_inbox_intents', return_value=True), \
             patch.object(coord, 'journal_committed', return_value=True), \
             patch.object(coord, 'process_alive', return_value=True), \
             patch.object(coord, 'probe_codex_native_health', return_value=dict(status_type='idle')):
            coord.recover_codex_ended_turn(self.cc, SESSION, ATTEMPT, self.span)
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'delivery_completed')])

    def test_active_or_unknown_thread_refuses(self):
        for status in ('active', 'unavailable', 'unknown'):
            with self.subTest(status=status):
                self.health = status
                self.assert_refused_unchanged()

    def test_tool_call_or_assistant_output_in_the_turn_refuses(self):
        for extra in (dict(type='response_item', payload=dict(type='function_call', name='exec', call_id='c')),
                      dict(type='response_item', payload=dict(type='message', role='assistant', content=[])),
                      dict(type='event_msg', payload=dict(type='exec_command_begin', turn_id=PROMPT)),
                      dict(type='event_msg', payload=dict(type='item_completed', turn_id=PROMPT,
                                                          item=dict(type='AgentMessage')))):
            with self.subTest(extra=extra['payload']['type']):
                saved = list(self.records)
                self.records.insert(-1, dict(timestamp='2026-10-18T23:00:00.950Z', ordinal=0, **extra))
                for index, record in enumerate(self.records[1:], start=10):
                    record['ordinal'] = index
                self.write_records()
                self.assert_refused_unchanged()
                self.records = saved
                for index, record in enumerate(self.records[1:], start=10):
                    record['ordinal'] = index

    def test_later_records_of_the_turn_refuse(self):
        self.records.append(dict(timestamp='2026-10-18T23:00:02.000Z', ordinal=17, type='event_msg',
                                 payload=dict(type='task_started', turn_id=PROMPT, root_turn_id=PROMPT)))
        self.write_records()
        self.span = [self.locators[1], self.locators[-2]]
        self.assert_refused_unchanged()

    def test_later_unrelated_turns_are_allowed(self):
        self.records.append(dict(timestamp='2026-10-18T23:05:00.000Z', ordinal=17, type='event_msg',
                                 payload=dict(type='task_started', turn_id=OTHER_TURN, root_turn_id=OTHER_TURN)))
        self.write_records()
        self.span = [self.locators[1], self.locators[-2]]
        self.assertEqual(self.recover(check_only=True)['status'], 'eligible')

    def test_completion_without_provider_error_refuses(self):
        self.records[-1]['payload'].pop('error')
        self.write_records()
        self.assert_refused_unchanged()
        self.records[-1]['payload'].update(error=dict(message='x', codex_error_info='usage_limit_exceeded'),
                                           last_agent_message='I did it')
        self.write_records()
        self.assert_refused_unchanged()

    def test_different_turn_or_claim_outside_the_turn_refuses(self):
        self.state['inbox_attempt']['native_turn_id'] = OTHER_TURN
        self.state['inbox_intent']['native_turn_id'] = OTHER_TURN
        self.save()
        self.assert_refused_unchanged()
        self.state['inbox_attempt']['native_turn_id'] = PROMPT
        self.state['inbox_intent']['native_turn_id'] = PROMPT
        self.state['inbox_attempt']['created_at'] = '2026-10-18T23:00:05.000+00:00'
        self.save()
        self.assert_refused_unchanged()

    def test_pin_mismatch_or_noncontiguous_ordinals_refuse(self):
        self.span[1] = dict(self.span[1], sha256='0' * 64)
        self.assert_refused_unchanged()
        self.write_records()
        self.records[3]['ordinal'] = 30
        self.write_records()
        self.assert_refused_unchanged()

    def test_command_line_check_only(self):
        out = io.StringIO()
        argv = ['coordination.py', 'recover-codex-ended-turn', '--config', str(self.root / 'coordination.json'),
                '--native-session-id', SESSION, '--attempt-id', ATTEMPT, '--check-only',
                '--start-record', '{offset}:{length}:{sha256}'.format(**self.span[0]),
                '--end-record', '{offset}:{length}:{sha256}'.format(**self.span[1])]
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
             patch.object(coord, 'probe_codex_native_health', return_value=dict(status_type='systemError')), \
             patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out):
            self.assertEqual(coord.main(), 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'eligible')


if __name__ == '__main__':
    unittest.main()
