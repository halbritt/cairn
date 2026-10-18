"""Explicit Codex refusal recovery with real pinned files and a mocked store; no live delivery."""
import hashlib
import contextlib
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_postclaim_refusal_settlement import Base, coord, SESSION, PROMPT, ATTEMPT, DELIVERY


class CodexRecoveryTests(Base):
    def setUp(self):
        super().setUp()
        self.cc.update(harness='codex', config_home=str(self.root))
        self.mc['harness'] = 'codex'
        for name, value in (('memory.json', self.mc), ('coordination.json', self.cc)):
            (self.root / name).write_text(json.dumps(value))
        manifest = json.loads(self.binding.read_text())
        for spec in [manifest['memory_config'], *manifest['coordination_configs']]:
            spec['sha256'] = hashlib.sha256(Path(spec['path']).read_bytes()).hexdigest()
        self.binding.write_text(json.dumps(manifest))
        self.state.pop('active_prompt')
        self.state['agent']['metadata']['harness'] = 'codex'
        for key in ('inbox_intent', 'inbox_attempt'):
            self.state[key]['native_turn_id'] = PROMPT
        self.state['inbox_attempt']['created_at'] = '2026-10-18T23:00:00.200+00:00'
        start = int(datetime.fromisoformat('2026-10-18T23:00:00+00:00').timestamp())
        self.state['wake_refusal_diagnostic'] = dict(phase='bridge_after_claim', origin='hook',
            settlement='held', error_class='ValueError', cause_class='OtherError', attempt_id=ATTEMPT,
            delivery_id=DELIVERY, native_turn_id=PROMPT, at=start + .8, grant='not_checked')
        self.records = [
            dict(timestamp='2026-10-18T23:00:00.000Z', ordinal=10, type='event_msg', payload=dict(
                type='task_started', turn_id=PROMPT, root_turn_id=PROMPT, started_at=start,
                model_context_window=258400, collaboration_mode_kind='default',
                turn_attribution=dict(turn_id=PROMPT, root_turn_id=PROMPT, turn_trigger='queue',
                                      parent_turn_id=None, initiating_agent_path=None))),
            dict(timestamp='2026-10-18T23:00:00.100Z', ordinal=11, type='turn_context', payload=dict(
                turn_id=PROMPT, root_turn_id=PROMPT, cwd=str(self.root))),
            dict(timestamp='2026-10-18T23:00:01.000Z', ordinal=12, type='event_msg', payload=dict(
                type='task_complete', turn_id=PROMPT, root_turn_id=PROMPT, last_agent_message=None,
                started_at=start, completed_at=start + 1, duration_ms=1000)),
        ]
        directory = self.root / 'sessions'
        directory.mkdir()
        self.rollout = directory / ('rollout-fixture-' + SESSION + '.jsonl')
        self.write_records()
        self.rollout_handle = self.rollout.open('rb')
        self.addCleanup(self.rollout_handle.close)
        self.save()

    def write_records(self):
        raw = [json.dumps(r).encode() + b'\n' for r in self.records]
        self.rollout.write_bytes(b''.join(raw))
        offset = 0
        self.locators = []
        for row in raw:
            self.locators.append(dict(offset=offset, length=len(row), sha256=hashlib.sha256(row).hexdigest()))
            offset += len(row)

    def recover(self, check_only=False):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            return coord.recover_codex_refused_wake(self.cc, SESSION, ATTEMPT, self.locators,
                                                    check_only=check_only)

    def test_check_only_validates_live_open_rollout_without_mutation(self):
        before = self.path.read_bytes()
        result = self.recover(check_only=True)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['native_record']['records'], self.locators)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def test_settles_only_the_matching_failed_attempt_without_replay(self):
        result = self.recover()
        self.assertEqual(result['settlement'], 'turn_ended')
        self.assertEqual(result['recovery_origin'], 'codex_native_records')
        self.assertEqual({op for op, _ in self.ops}, {'session-inbox-reconcile'})
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        self.assertNotIn('inbox_attempt', self.load())
        self.assertNotIn('idle_wake', self.load())

    def test_store_failure_retains_end_proof_for_watcher_without_lease_renewal(self):
        self.fail_reconcile = True
        self.assertEqual(self.recover()['settlement'], 'turn_ended_pending')
        self.assertTrue(self.load()['inbox_turn_ended'])
        self.fail_reconcile = False
        self.ops.clear()
        with self.patched():
            coord.watch_inbox(self.cc, self.load(), self.path)
        self.assertNotIn('event-renew', [op for op, _ in self.ops])
        self.assertNotIn('inbox_attempt', self.load())

    def assert_refused_unchanged(self):
        before = self.path.read_bytes()
        with self.assertRaises((coord.CoordinationError, ValueError, OSError)):
            self.recover()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def test_present_or_unknown_grant_refuses(self):
        self.write_grant(attempt_id=ATTEMPT)
        self.assert_refused_unchanged()
        self.ledger.unlink()
        self.assert_refused_unchanged()

    def test_evidence_hash_mismatch_and_noncontiguous_records_refuse(self):
        self.locators[0]['sha256'] = '0' * 64
        self.assert_refused_unchanged()
        self.write_records()
        self.locators[1]['offset'] += 1
        self.assert_refused_unchanged()

    def test_missing_open_rollout_refuses(self):
        self.rollout_handle.close()
        self.assert_refused_unchanged()

    def test_replaced_rollout_inode_refuses(self):
        replacement = self.rollout.with_suffix('.replacement')
        replacement.write_bytes(self.rollout.read_bytes())
        replacement.replace(self.rollout)
        self.assert_refused_unchanged()

    def test_open_rollout_outside_configured_home_refuses(self):
        self.cc['config_home'] = str(self.root / 'another-home')
        self.assert_refused_unchanged()

    def test_unflushed_intent_retains_pending_settlement(self):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'flush_inbox_intents', return_value=False):
            result = coord.recover_codex_refused_wake(self.cc, SESSION, ATTEMPT, self.locators)
        self.assertEqual(result['settlement'], 'turn_ended_pending')
        self.assertTrue(self.load()['inbox_turn_ended'])
        self.assertEqual(self.ops, [])

    def test_cli_check_only_uses_exact_pins_without_mutation(self):
        argv = ['coordination.py', 'recover-codex-refused-wake', '--config',
                str(self.root / 'coordination.json'), '--native-session-id', SESSION,
                '--attempt-id', ATTEMPT, '--check-only']
        for name, locator in zip(('start', 'context', 'end'), self.locators):
            argv.extend(['--' + name + '-record', '{offset}:{length}:{sha256}'.format(**locator)])
        before = self.path.read_bytes()
        output = io.StringIO()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            coord.main()
        self.assertEqual(json.loads(output.getvalue())['status'], 'eligible')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def test_record_semantic_mismatches_refuse(self):
        changes = (
            lambda r: r[0].update(ordinal=True),
            lambda r: r[1].update(ordinal=13),
            lambda r: r[0]['payload']['turn_attribution'].update(turn_trigger='user'),
            lambda r: r[0]['payload'].update(turn_id=SESSION),
            lambda r: r[1].update(type='response_item'),
            lambda r: r[1]['payload'].update(cwd='/another-project'),
            lambda r: r[2]['payload'].update(type='task_started'),
            lambda r: r[2]['payload'].update(last_agent_message='executed work'),
            lambda r: r[2]['payload'].update(extra_execution_field=True),
            lambda r: r[2].update(timestamp='2026-10-18T23:00:00.150Z'),
            lambda r: r[2].update(timestamp='2026-10-18T23:01:00.000Z'),
        )
        original = json.dumps(self.records)
        for change in changes:
            with self.subTest(change=change):
                self.records = json.loads(original)
                change(self.records)
                self.write_records()
                self.assert_refused_unchanged()

    def test_changed_attempt_effects_and_host_identity_refuse(self):
        changes = (
            lambda s: s['wake_refusal_diagnostic'].update(attempt_id=SESSION),
            lambda s: s['wake_refusal_diagnostic'].update(delivery_id=SESSION),
            lambda s: s['wake_refusal_diagnostic'].update(native_turn_id=SESSION),
            lambda s: s['wake_refusal_diagnostic'].update(origin='caller'),
            lambda s: s['wake_refusal_diagnostic'].update(at=0),
            lambda s: s['inbox_attempt']['session'].update(execution_id=SESSION),
            lambda s: s['inbox_attempt'].update(tools=[{'status': 'running'}]),
            lambda s: s.update(tool_calls=[{'status': 'running'}]),
            lambda s: s.update(active_prompt={'id': PROMPT, 'joined': True}),
            lambda s: s.update(ending=True),
        )
        original = json.dumps(self.state)
        for change in changes:
            with self.subTest(change=change):
                self.state = json.loads(original)
                change(self.state)
                self.save()
                self.assert_refused_unchanged()


if __name__ == '__main__':
    unittest.main()
