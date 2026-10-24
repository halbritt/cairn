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

from test_postclaim_refusal_settlement import Base, coord, SESSION, PROMPT, ATTEMPT, DELIVERY, RECORD


class CodexBase(Base):
    def setUp(self):
        super().setUp()
        self.cc.update(harness='codex', config_home=str(self.root), idle_wakeup=str(self.root / 'cairn'))
        self.mc['harness'] = 'codex'
        for name, value in (('memory.json', self.mc), ('coordination.json', self.cc)):
            (self.root / name).write_text(json.dumps(value))
            (self.root / name).chmod(0o600)
        manifest = json.loads(self.binding.read_text())
        for spec in [manifest['memory_config'], *manifest['coordination_configs']]:
            spec['sha256'] = hashlib.sha256(Path(spec['path']).read_bytes()).hexdigest()
        self.binding.write_text(json.dumps(manifest))
        self.binding.chmod(0o600)
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

    def assert_refused_unchanged(self):
        before = self.path.read_bytes()
        with self.assertRaises((coord.CoordinationError, ValueError, OSError)):
            self.recover()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def recover(self, check_only=False):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            return coord.recover_codex_refused_wake(self.cc, SESSION, ATTEMPT, self.locators,
                                                    check_only=check_only)


class CodexRecoveryTests(CodexBase):
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

    def test_recovery_preserves_unused_prior_journal_obligations(self):
        prior = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        self.state['inbox_journals'].insert(0, prior)
        self.save()
        with patch.object(coord, 'call', side_effect=self.api), \
                patch.object(coord, 'process_alive', return_value=True):
            self.assertEqual(coord.recover_codex_refused_wake(
                self.cc, SESSION, ATTEMPT, self.locators, check_only=True)['status'], 'eligible')
            self.assertEqual(coord.recover_codex_refused_wake(
                self.cc, SESSION, ATTEMPT, self.locators)['settlement'], 'turn_ended')
        self.assertEqual(self.load()['inbox_journals'], [prior, ATTEMPT])
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()],
                         [(ATTEMPT, 'turn_ended')])

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

    def test_watcher_automatically_settles_held_codex_refusal_once_rollout_proves_empty_turn(self):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            obs = coord.held_attempt_refusal_observation(self.cc, self.load())
            self.assertEqual(obs['evidence_state'], 'codex_rollout_ready')
            coord.watch_inbox(self.cc, self.load(), self.path)
        saved = self.load()
        self.assertEqual(saved['wake_refusal_diagnostic']['settlement'], 'turn_ended')
        self.assertEqual(saved['wake_refusal_diagnostic']['recovery_origin'], 'codex_native_records')
        self.assertEqual(saved['wake_refusal_diagnostic']['native_record']['records'], self.locators)
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        self.assertNotIn('event-renew', [op for op, _ in self.ops])
        self.assertNotIn('inbox_attempt', saved)
        self.assertNotIn('inbox_intent', saved)

    def test_watcher_keeps_hold_when_codex_rollout_incomplete_or_grant_present(self):
        # 1. Incomplete rollout (only 2 records, task_complete not yet written): keeps hold and renews lease.
        self.records = self.records[:2]
        self.write_records()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            obs = coord.held_attempt_refusal_observation(self.cc, self.load())
            self.assertEqual(obs['evidence_state'], 'awaiting_codex_turn_end')
            coord.watch_inbox(self.cc, self.load(), self.path)
        self.assertEqual(self.load()['wake_refusal_diagnostic']['settlement'], 'held')
        self.assertIn('event-renew', [op for op, _ in self.ops])
        # 2. Grant present in ledger: keeps hold even with complete rollout.
        self.setUp()
        self.write_grant(attempt_id=ATTEMPT)
        self.ops.clear()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            coord.watch_inbox(self.cc, self.load(), self.path)
        self.assertEqual(self.load()['wake_refusal_diagnostic']['settlement'], 'held')
        self.assertIn('event-renew', [op for op, _ in self.ops])
        self.assertIn('inbox_attempt', self.load())


class LegacyPreGrantLedgerTests(CodexBase):
    """A refusal recorded before the bridge wrote any ledger, from a bridge that predates
    initializing it before the required lookup. Recovery is explicit and narrow."""

    def setUp(self):
        super().setUp()
        self.ledger.unlink()
        self.marker = self.ledger.with_suffix('.initialized')
        self.marker.unlink()
        self.state['wake_refusal_diagnostic'].update(
            recall_phase='required_search', recall_operation='search', recall_attempts=2,
            recall_max_attempts=2, cause_code='TIMEOUT')
        self.save()
        self.budget = self.memory_dir / (SESSION + '.memory-budget.json')
        self.write_budget()

    def write_budget(self, activation=True, turns=None):
        ledger = dict(schema='cairn.codex-memory-grants/1', session_id=SESSION, active_turn_id=None,
                      turns=turns or {}, pending=dict(limit_bytes=9500, emitted_hook_bytes=0,
                                                      reserved_native_bytes=0, granted=False))
        if activation is not None:
            ledger['inbox_activation'] = {PROMPT: activation}
        self.budget.write_text(json.dumps(ledger))
        self.budget.chmod(0o600)

    def test_explicit_recovery_settles_required_search_refusal_without_any_ledger(self):
        checked = self.recover(check_only=True)
        self.assertEqual((checked['status'], checked['grant']), ('eligible', 'absent'))
        self.assertEqual(checked['grant_evidence'], 'pre_grant_refusal_without_ledger')
        self.assertEqual(self.ops, [])
        result = self.recover()
        self.assertEqual((result['settlement'], result['grant']), ('turn_ended', 'absent'))
        self.assertEqual(result['grant_evidence'], 'pre_grant_refusal_without_ledger')
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        self.assertNotIn('inbox_attempt', self.load())
        # Recovery reads; it never fabricates the bridge's ledger or marker.
        self.assertFalse(self.ledger.exists())
        self.assertFalse(self.marker.exists())

    def test_any_other_recorded_phase_or_grant_check_still_refuses(self):
        for change in (dict(recall_phase='task_source'), dict(recall_phase='optional_search'),
                       dict(recall_phase=None), dict(grant='unknown'), dict(grant='absent')):
            with self.subTest(change=change):
                diagnostic = self.load()['wake_refusal_diagnostic']
                original = dict(diagnostic)
                diagnostic.update(change)
                self.state['wake_refusal_diagnostic'] = {k: v for k, v in diagnostic.items() if v is not None}
                self.save()
                self.assert_refused_unchanged()
                self.state['wake_refusal_diagnostic'] = original
                self.save()

    def test_one_file_without_the_other_still_refuses(self):
        self.marker.write_text(json.dumps(dict(schema='cairn.inbox-recall-initialized/1', session=SESSION)))
        self.assert_refused_unchanged()
        self.marker.unlink()
        self.ledger.write_text(json.dumps(dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={})))
        self.assert_refused_unchanged()

    def test_bridge_directory_must_record_this_turn_without_a_turn_grant(self):
        for activation, turns in ((None, None), (False, None),
                                  (True, {PROMPT: dict(limit_bytes=9500, emitted_hook_bytes=0,
                                                       reserved_native_bytes=0, granted=True)})):
            with self.subTest(activation=activation, turns=bool(turns)):
                self.write_budget(activation, turns)
                self.assert_refused_unchanged()
        self.budget.unlink()
        self.assert_refused_unchanged()

    def test_automatic_settlement_still_holds_without_ledger(self):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            coord.watch_inbox(self.cc, self.load(), self.path)
        saved = self.load()
        self.assertEqual(saved['wake_refusal_diagnostic']['settlement'], 'held')
        self.assertIn('inbox_attempt', saved)
        self.assertIn('event-renew', [op for op, _ in self.ops])
        self.assertNotIn('turn_ended', [r['reason'] for r in self.reconciles()])


class CodexNativeHealthTests(CodexBase):
    def setUp(self):
        super().setUp()
        self.endpoint = dict(endpoint=str(self.root / 'native-queue.sock'), owner='process')
        for key in ('inbox_intent', 'inbox_attempt', 'inbox_result_policy',
                    'inbox_completion', 'inbox_response', 'wake_refusal_diagnostic'):
            self.state.pop(key, None)
        self.state['agent']['metadata']['state'] = 'busy'
        self.ready = dict(delivery_id=DELIVERY, event_id=RECORD, request_id=ATTEMPT)
        self.save()

    def api(self, config, op, request, **kwargs):
        if op == 'session-inbox-ready':
            self.ops.append((op, request))
            return dict(self.ready)
        return super().api(config, op, request, **kwargs)

    def test_system_error_behind_busy_session_surfaces_unavailable_and_does_not_claim_or_wake(self):
        inspected = dict(
            status_type='systemError', updated_at=1700000500, queue_count=0,
            queued_ids=[], queued_present=None, peer=self.process)
        cycle = {}
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = inspected
            prepared = coord.prepare_idle_wake(self.cc, self.state, self.path, cycle)
            obs = coord.delivery_observation(self.cc, self.state, prepared, cycle)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['native_health']['status_type'], 'systemError')
        self.assertEqual(self.state['native_health']['process_liveness'], 'alive')
        self.assertEqual(self.state['native_health']['native_readiness'], 'error')
        self.assertEqual(self.state['native_health']['task_metadata']['reported_state'], 'busy')
        self.assertEqual(self.state['native_health']['task_metadata']['freshness'], 'stale_busy')
        self.assertIn('do not automatically claim, restart, or replay',
                      self.state['native_health']['next_action'])
        self.assertIn('pending delivery', self.state['native_health']['next_action'])
        self.assertNotIn('pending request', self.state['native_health']['next_action'])
        self.assertEqual(self.state['wake_refusal']['condition'], 'native_transport_unavailable')
        self.assertIn('pending delivery', self.state['wake_refusal']['reason'])
        self.assertNotIn('pending request source', self.state['wake_refusal']['reason'])
        self.assertEqual(obs['condition'], 'native_transport_unavailable')
        self.assertEqual(obs['delivery_id'], DELIVERY)
        self.assertNotIn('idle_wake', self.state)
        for forbidden in ('session-inbox-claim', 'event-claim', 'event-complete'):
            self.assertNotIn(forbidden, [op for op, _ in self.ops])

    def test_health_poll_is_rate_limited_and_cli_reports_structured_dimensions(self):
        calls = []

        def fake_inspect(*args, **kwargs):
            calls.append((args, kwargs))
            return dict(status_type='active', updated_at=1700000100, queue_count=1,
                        queued_ids=['queued-one'], queued_present=True, peer=self.process)

        self.state['idle_wake'] = dict(
            transport='codex-queue', queued_submission_id='queued-one',
            delivery_id=DELIVERY, client_id=DELIVERY)
        self.save()
        with patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.side_effect = fake_inspect
            first = coord.probe_codex_native_health(self.cc, self.state, now=1000.0)
            second = coord.probe_codex_native_health(self.cc, self.state, now=1010.0)
            third = coord.probe_codex_native_health(self.cc, self.state, now=1061.0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(first['observed_at_epoch'], second['observed_at_epoch'])
        self.assertEqual(third['status_type'], 'active')
        self.assertIs(third['queued_present'], True)
        argv = ['coordination.py', 'check-codex-native-health', '--config',
                str(self.root / 'coordination.json'), '--native-session-id', SESSION]
        output = io.StringIO()
        with patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod, \
                patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            qmod.return_value.inspect_thread.side_effect = fake_inspect
            coord.main()
        report = json.loads(output.getvalue())
        self.assertEqual(report['status_type'], 'active')
        self.assertEqual(report['process_liveness'], 'alive')
        self.assertEqual(report['task_metadata']['reported_state'], 'busy')


QUEUED_ID = 'queued-submission-one'


class CodexQueueReconciliationTests(CodexBase):
    def setUp(self):
        super().setUp()
        self.endpoint = dict(endpoint=str(self.root / 'native-queue.sock'), owner='process')
        for key in ('inbox_intent', 'inbox_attempt', 'inbox_result_policy',
                    'inbox_completion', 'inbox_response', 'wake_refusal_diagnostic'):
            self.state.pop(key, None)
        self.state['agent']['metadata']['state'] = 'idle'
        self.attempt_epoch = int(datetime.fromisoformat('2026-10-18T23:00:00+00:00').timestamp()) + 0.5
        self.state['idle_wake'] = dict(
            transport='codex-queue', session=coord.session_ref(self.state['agent']),
            native_id=SESSION, delivery_id=DELIVERY, client_id=DELIVERY,
            event_id=RECORD, request_id=ATTEMPT, queued_submission_id=QUEUED_ID,
            endpoint=self.endpoint['endpoint'], owner='process',
            status='refused', reconciliation_required='queue_item_missing',
            attempted_at=self.attempt_epoch, last_start='missing')
        self.ready = dict(delivery_id=DELIVERY, event_id=RECORD, request_id=ATTEMPT)
        self.store_deliveries = [dict(
            self.delivery, state='pending', attempts=0, consumer=self.state['agent']['inbox'])]
        self.save()

    def write_owner_turn_records(self):
        for r in self.records:
            if 'turn_attribution' in r['payload']:
                r['payload']['turn_attribution']['turn_trigger'] = 'user'
        self.write_records()

    def api(self, config, op, request, **kwargs):
        if op == 'session-inbox-ready':
            self.ops.append((op, request))
            return dict(self.ready)
        if op == 'event-inspect':
            self.ops.append((op, request))
            return dict(event=dict(event_id=RECORD, repo='fixture'),
                        deliveries=list(self.store_deliveries))
        return super().api(config, op, request, **kwargs)

    def inspect_fixture(self, status_type='idle', queued_present=False, queue_count=0, queued_ids=()):
        return dict(
            status_type=status_type, updated_at=1700000200, queue_count=queue_count,
            queued_ids=list(queued_ids), queued_present=queued_present, peer=self.process)

    def test_never_admitted_clears_stale_wake_and_releases_subsequent_inbox_traffic(self):
        self.write_owner_turn_records()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='idle', queued_present=False)
            check = coord.reconcile_codex_queue_wake(
                self.cc, SESSION, DELIVERY, QUEUED_ID, records=self.locators, check_only=True)
            self.assertEqual(check['status'], 'eligible')
            self.assertEqual(check['outcome'], 'never_admitted')
            self.assertIn('idle_wake', self.load())
            settled = coord.reconcile_codex_queue_wake(
                self.cc, SESSION, DELIVERY, QUEUED_ID, records=self.locators)
        self.assertEqual(settled['status'], 'reconciled')
        self.assertEqual(settled['outcome'], 'never_admitted')
        loaded = self.load()
        self.assertNotIn('idle_wake', loaded)
        self.assertEqual(loaded['wake_reconciled']['outcome'], 'never_admitted')
        for forbidden in ('session-inbox-claim', 'event-claim', 'event-complete'):
            self.assertNotIn(forbidden, [op for op, _ in self.ops])
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint):
            prepared = coord.prepare_idle_wake(self.cc, loaded, self.path)
        self.assertIsNotNone(prepared)
        self.assertEqual(loaded['idle_wake']['delivery_id'], DELIVERY)
        self.assertEqual(loaded['idle_wake']['status'], 'uncertain')

    def test_admitted_active_retains_wake_when_queued_or_leased_without_replay(self):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='active', queued_present=True, queue_count=1, queued_ids=(QUEUED_ID,))
            result = coord.reconcile_codex_queue_wake(self.cc, SESSION, DELIVERY, QUEUED_ID)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'admitted_active')
        self.assertEqual(self.load()['idle_wake']['queued_submission_id'], QUEUED_ID)
        # Active rollout-proven queue turn (before task_complete) also settles admitted_active:
        self.records = self.records[:2]
        self.state['idle_wake']['attempted_at'] = self.attempt_epoch - 1.0
        self.save()
        self.write_records()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='active', queued_present=False)
            result = coord.reconcile_codex_queue_wake(
                self.cc, SESSION, DELIVERY, QUEUED_ID, records=self.locators)
        self.assertEqual(result['outcome'], 'admitted_active')
        self.assertEqual(self.load()['idle_wake']['queued_submission_id'], QUEUED_ID)

    def test_completed_from_store_or_rollout_proof_clears_wake_without_duplicate_completion(self):
        self.store_deliveries = [dict(
            self.delivery, state='handled', attempts=1, consumer=self.state['agent']['inbox'])]
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='idle', queued_present=False)
            result = coord.reconcile_codex_queue_wake(self.cc, SESSION, DELIVERY, QUEUED_ID)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'completed')
        self.assertNotIn('idle_wake', self.load())
        self.assertNotIn('event-complete', [op for op, _ in self.ops])
        # Rollout-proven queue turn completion also settles completed when store still reports pending:
        self.records[2]['payload']['last_agent_message'] = 'completed queued request'
        self.write_records()
        self.state['idle_wake'] = dict(
            transport='codex-queue', session=coord.session_ref(self.state['agent']),
            native_id=SESSION, delivery_id=DELIVERY, client_id=DELIVERY,
            event_id=RECORD, request_id=ATTEMPT, queued_submission_id=QUEUED_ID,
            endpoint=self.endpoint['endpoint'], owner='process',
            status='refused', reconciliation_required='queue_item_missing',
            attempted_at=self.attempt_epoch - 1.0, last_start='missing')
        self.save()
        self.store_deliveries = [dict(
            self.delivery, state='pending', attempts=0, consumer=self.state['agent']['inbox'])]
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='idle', queued_present=False)
            result = coord.reconcile_codex_queue_wake(
                self.cc, SESSION, DELIVERY, QUEUED_ID, records=self.locators)
        self.assertEqual(result['outcome'], 'completed')
        self.assertIsNotNone(result['native_record'])
        self.assertNotIn('idle_wake', self.load())

    def test_cancelled_delivery_clears_stale_wake_without_mutating_store(self):
        self.store_deliveries = [dict(
            self.delivery, state='cancelled', attempts=0, consumer=self.state['agent']['inbox'])]
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='idle', queued_present=False)
            result = coord.reconcile_codex_queue_wake(self.cc, SESSION, DELIVERY, QUEUED_ID)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertNotIn('idle_wake', self.load())
        self.assertEqual([op for op, _ in self.ops], ['session-inbox-ready', 'event-inspect'])

    def test_active_owner_goal_continuation_and_dequeue_race_stay_unknown_without_replay(self):
        # Active owner turn or unleased queue-start vs automatic-dequeue race must remain
        # 'unknown' and never clear or re-enqueue while the thread is active.
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='active', queued_present=False)
            result = coord.reconcile_codex_queue_wake(self.cc, SESSION, DELIVERY, QUEUED_ID)
            with self.assertRaisesRegex(coord.CoordinationError, 'not never_admitted'):
                coord.reconcile_codex_queue_wake(
                    self.cc, SESSION, DELIVERY, QUEUED_ID, outcome='never_admitted')
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(self.load()['idle_wake']['queued_submission_id'], QUEUED_ID)
        self.assertEqual(self.load()['wake_reconciliation']['outcome'], 'unknown')
        for forbidden in ('session-inbox-claim', 'event-claim', 'event-complete'):
            self.assertNotIn(forbidden, [op for op, _ in self.ops])

    def test_cli_reconcile_codex_queue_wake_validates_outcomes_and_pins(self):
        self.write_owner_turn_records()
        argv = ['coordination.py', 'reconcile-codex-queue-wake', '--config',
                str(self.root / 'coordination.json'), '--native-session-id', SESSION,
                '--delivery-id', DELIVERY, '--queued-submission-id', QUEUED_ID,
                '--outcome', 'never_admitted']
        for locator in self.locators:
            argv.extend(['--record', '{offset}:{length}:{sha256}'.format(**locator)])
        output = io.StringIO()
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod, \
                patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            qmod.return_value.inspect_thread.return_value = self.inspect_fixture(
                status_type='idle', queued_present=False)
            coord.main()
        report = json.loads(output.getvalue())
        self.assertEqual(report['status'], 'reconciled')
        self.assertEqual(report['outcome'], 'never_admitted')
        self.assertNotIn('idle_wake', self.load())


if __name__ == '__main__':
    unittest.main()





