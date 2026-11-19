"""CAIRN-363: reconcile natively idle Codex sessions stranded behind stale busy metadata,
surface untrusted/missing lifecycle hooks per live app-server process, and keep active,
held, tool-running, error, or mismatched sessions protected."""
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from datetime import datetime
from unittest.mock import patch

from test_codex_refusal_recovery import CodexBase
from test_postclaim_refusal_settlement import coord, SESSION, ATTEMPT, DELIVERY, RECORD

TURN = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
TURN_2 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
DELIVERY_2 = '88888888-8888-4888-8888-888888888888'
ATTEMPT_2 = '99999999-9999-4999-8999-999999999999'
ENDED = datetime.fromisoformat('2026-11-06T21:29:44.104+00:00').timestamp()


def turn_started(turn, ordinal, stamp):
    return dict(timestamp=stamp, ordinal=ordinal, type='event_msg', payload=dict(
        type='task_started', turn_id=turn, root_turn_id=turn, started_at=int(ENDED) - 4))


def turn_completed(turn, ordinal, stamp, error=None, message='Completed prior task.'):
    payload = dict(type='task_complete', turn_id=turn, root_turn_id=turn,
                   last_agent_message=message, started_at=int(ENDED) - 4, completed_at=int(ENDED))
    if error is not None:
        payload['error'] = error
    return dict(timestamp=stamp, ordinal=ordinal, type='event_msg', payload=payload)


class StaleBusyWakeBase(CodexBase):
    def setUp(self):
        if getattr(self, 'rollout_handle', None) is not None:
            self.rollout_handle.close()
        super().setUp()
        self.endpoint = dict(endpoint=str(self.root / 'native-queue.sock'), owner='process')
        for key in ('inbox_intent', 'inbox_attempt', 'inbox_result_policy',
                    'inbox_completion', 'inbox_response', 'wake_refusal_diagnostic'):
            self.state.pop(key, None)
        self.state['agent']['metadata']['state'] = 'busy'
        self.state['delivered_since_idle'] = True
        self.ready = dict(delivery_id=DELIVERY, event_id=RECORD, request_id=ATTEMPT)
        self.set_clean_turn_end()
        self.hook_probe = dict(status='ready', missing_events=[], untrusted_events=[], reason=None)
        self.save()

    def set_clean_turn_end(self, after=()):
        self.records = [
            turn_started(TURN, 100, '2026-11-06T21:29:40.104Z'),
            turn_completed(TURN, 101, '2026-11-06T21:29:44.104Z'),
            *after,
        ]
        self.write_records()

    def api(self, config, op, request, **kwargs):
        if op == 'session-inbox-ready':
            self.ops.append((op, request))
            return dict(self.ready) if self.ready else {}
        if op == 'agent-context':
            self.ops.append((op, request))
            agent = dict(self.state['agent'], metadata=request['metadata'],
                         context_revision=request['expected_revision'] + 1)
            return agent
        if op == 'session-delivery-observe':
            self.ops.append((op, request))
            if getattr(self, 'reject_new_conditions', False) and request.get('condition') in (
                    'native_idle_stale_busy', 'lifecycle_hooks_untrusted'):
                raise coord.CoordinationError('INVALID_REQUEST', 'unknown delivery observation condition')
            return dict(applied=True)
        return super().api(config, op, request, **kwargs)

    def prepare(self, now, status='idle', queue_count=0, peer=None, process_alive=True):
        inspected = dict(status_type=status, updated_at=int(ENDED), queue_count=queue_count,
                         queued_ids=['q1'] * queue_count, queued_present=bool(queue_count) if queue_count else None,
                         peer=self.process if peer is None else peer)
        cycle = {}
        with self.patched(), patch.object(coord, 'process_alive', return_value=process_alive), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord.time, 'time', return_value=now), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = inspected
            qmod.return_value.inspect_hooks.return_value = dict(self.hook_probe)
            prepared = coord.prepare_idle_wake(self.cc, self.state, self.path, cycle)
            obs = coord.delivery_observation(self.cc, self.state, prepared, cycle)
        return prepared, obs


class StaleBusyWakeTests(StaleBusyWakeBase):
    def test_grace_window_then_reconciles_busy_to_idle_and_advances_subsequent_message(self):
        # Within 5s grace window after task_complete, do not race Stop; report native_idle_stale_busy.
        prepared_early, obs_early = self.prepare(ENDED + 2.0)
        self.assertIsNone(prepared_early)
        self.assertEqual(self.state['agent']['metadata']['state'], 'busy')
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'grace_period')
        self.assertEqual(obs_early['condition'], 'native_idle_stale_busy')
        self.assertEqual(obs_early['delivery_id'], DELIVERY)

        # Past grace window with verified rollout task_complete and ready hooks:
        # forces a fresh post-grace health probe, corrects busy -> idle, clears stale
        # delivered_since_idle latch, and prepares wake once.
        prepared, obs = self.prepare(ENDED + 6.0)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared['wake']['transport'], 'codex-queue')
        self.assertEqual(prepared['wake']['delivery_id'], DELIVERY)
        self.assertEqual(self.state['agent']['metadata']['state'], 'idle')
        self.assertIs(self.state['delivered_since_idle'], False)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'wake_allowed')
        self.assertEqual(self.state['codex_idle_wake']['presence'], 'busy_corrected_to_idle')
        self.assertEqual(self.state['codex_idle_wake']['latch'], 'cleared')
        self.assertEqual(self.state['codex_idle_wake']['turn']['turn_id'], TURN)
        self.assertEqual(self.state['codex_idle_wake']['turn']['record'], self.locators[1])
        self.assertEqual(obs['condition'], 'automatic_available')
        self.assertNotIn('wake_refusal', self.state)

        # After explicit completion/acknowledgement of the first request, a later distinct message advances.
        self.state.pop('idle_wake', None)
        self.ready = dict(delivery_id=DELIVERY_2, event_id=RECORD, request_id=ATTEMPT_2)
        prepared_2, obs_2 = self.prepare(ENDED + 70.0)
        self.assertIsNotNone(prepared_2)
        self.assertEqual(prepared_2['wake']['delivery_id'], DELIVERY_2)
        self.assertEqual(obs_2['condition'], 'automatic_available')

    def test_untrusted_or_missing_hooks_block_reconciliation_and_surface_specific_condition(self):
        for status, missing, untrusted, reason in (
            ('untrusted', [], ['stop'], 'required Cairn lifecycle hooks untrusted or disabled for stop'),
            ('missing', ['userPromptSubmit'], [], 'required Cairn coordination hooks missing for userPromptSubmit'),
            ('error', [], [], 'native hooks/list reported configuration errors'),
        ):
            with self.subTest(hook_status=status):
                self.setUp()
                self.hook_probe = dict(status=status, missing_events=missing,
                                       untrusted_events=untrusted, reason=reason)
                prepared, obs = self.prepare(ENDED + 30.0)
                self.assertIsNone(prepared)
                self.assertEqual(self.state['agent']['metadata']['state'], 'busy')
                self.assertEqual(self.state['codex_idle_wake']['decision'], 'hooks_untrusted')
                self.assertEqual(obs['condition'], 'lifecycle_hooks_untrusted')
                self.assertEqual(obs['delivery_id'], DELIVERY)
                self.assertNotIn('agent-context', [op for op, _ in self.ops])

    def test_pre_migration_server_fallback_on_session_delivery_observe(self):
        self.reject_new_conditions = True
        self.hook_probe = dict(status='untrusted', missing_events=[], untrusted_events=['stop'],
                               reason='required Cairn lifecycle hooks untrusted or disabled for stop')
        cycle = {}
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord.time, 'time', return_value=ENDED + 30.0), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = dict(
                status_type='idle', updated_at=int(ENDED), queue_count=0,
                queued_ids=[], queued_present=None, peer=self.process)
            qmod.return_value.inspect_hooks.return_value = dict(self.hook_probe)
            prepared = coord.prepare_idle_wake(self.cc, self.state, self.path, cycle)
            coord.report_delivery_observation(self.cc, self.state, prepared, cycle)
        self.assertEqual(self.state['delivery_observation']['value']['condition'], 'native_transport_unavailable')

    def test_negative_controls_protect_active_turns_tools_holds_queues_and_wrong_peers(self):
        # 1. Genuinely active native turn
        prepared, obs = self.prepare(ENDED + 30.0, status='active')
        self.assertIsNone(prepared)
        self.assertEqual(obs['condition'], 'busy')

        # 2. Active turn started after task_complete in rollout
        self.setUp()
        self.set_clean_turn_end(after=[turn_started(TURN_2, 102, '2026-11-06T21:29:45.000Z')])
        prepared, obs = self.prepare(ENDED + 30.0)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'unverified')
        self.assertEqual(obs['condition'], 'native_idle_stale_busy')

        # 3. In-flight tool_calls
        self.setUp()
        self.state['tool_calls'] = {'call-1': {'name': 'bash'}}
        self.save()
        prepared, obs = self.prepare(ENDED + 30.0)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'held')
        self.assertEqual(obs['condition'], 'busy')

        # 4. Queued items still present in native queue
        self.setUp()
        prepared, obs = self.prepare(ENDED + 30.0, queue_count=1)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'held')
        self.assertEqual(obs['condition'], 'busy')

        # 5. Uncertain retained wake
        self.setUp()
        self.state['idle_wake'] = dict(session=coord.session_ref(self.state['agent']),
                                       delivery_id=DELIVERY, status='uncertain', transport='codex-queue')
        prepared, _ = self.prepare(ENDED + 30.0)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'held')

        # 6. Mismatched or missing socket peer process
        self.setUp()
        wrong_peer = dict(pid=self.process['pid'] + 999, start=999, boot='other')
        prepared, _ = self.prepare(ENDED + 30.0, peer=wrong_peer)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['agent']['metadata']['state'], 'busy')

        self.setUp()
        cycle = {}
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord.time, 'time', return_value=ENDED + 30.0), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = dict(
                status_type='idle', updated_at=int(ENDED), queue_count=0,
                queued_ids=[], queued_present=None, peer=None)
            qmod.return_value.inspect_hooks.return_value = dict(self.hook_probe)
            prepared = coord.prepare_idle_wake(self.cc, self.state, self.path, cycle)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['agent']['metadata']['state'], 'busy')

        # 7. Unobserved hooks (e.g. hooks/list unsupported) fail closed to unverified
        self.setUp()
        self.hook_probe = dict(status='unobserved', missing_events=[], untrusted_events=[], reason=None)
        prepared, obs = self.prepare(ENDED + 30.0)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'unverified')
        self.assertEqual(obs['condition'], 'native_idle_stale_busy')

    def test_post_grace_reprobe_and_normal_stop_recovery_clear_stale_idle_diagnostics(self):
        # 1. Grace poll caches idle health at ENDED+2; if native thread becomes active before ENDED+6,
        # post-grace forced re-probe catches it and does NOT reconcile busy -> idle.
        prepared_early, obs_early = self.prepare(ENDED + 2.0, status='idle')
        self.assertIsNone(prepared_early)
        self.assertEqual(obs_early['condition'], 'native_idle_stale_busy')

        prepared_active, obs_active = self.prepare(ENDED + 6.0, status='active')
        self.assertIsNone(prepared_active)
        self.assertEqual(self.state['agent']['metadata']['state'], 'busy')
        self.assertEqual(obs_active['condition'], 'busy')
        self.assertNotIn('agent-context', [op for op, _ in self.ops])

        # 2. If normal Stop completes after a grace_period poll (setting metadata state='idle'),
        # the next cycle clears codex_idle_wake and reports automatic_available, not native_idle_stale_busy.
        self.setUp()
        self.prepare(ENDED + 2.0, status='idle')
        self.assertEqual(self.state['codex_idle_wake']['decision'], 'grace_period')
        self.state['agent']['metadata']['state'] = 'idle'
        self.state['agent']['context_revision'] += 1
        self.state['delivered_since_idle'] = False
        self.save()
        prepared_idle, obs_idle = self.prepare(ENDED + 3.0, status='idle')
        self.assertIsNotNone(prepared_idle)
        self.assertNotIn('codex_idle_wake', self.state)
        self.assertEqual(obs_idle['condition'], 'automatic_available')

    def test_two_simultaneous_app_server_processes_do_not_cross_contaminate_hook_trust(self):
        from test_codex_queue import NativeQueue
        # Exercise real codex_queue.inspect_hooks against two independent live Unix WebSocket endpoints
        # representing two app-server processes sharing one subscription profile (CODEX_HOME).
        qt_a = NativeQueue()
        qt_a.setUp()
        self.addCleanup(qt_a.doCleanups)
        qt_b = NativeQueue()
        qt_b.setUp()
        self.addCleanup(qt_b.doCleanups)

        coord_cmd = '/opt/cairn/inbox hook --config /opt/cairn/coordination-0.json'
        mem_cmd = '/opt/cairn/launch/abc123/inbox'
        unrelated_cmd = '/home/user/.local/bin/git-stop-hygiene-hook'

        def make_hooks(stop_trusted):
            return [dict(cwd=str(self.root), warnings=[], errors=[], hooks=[
                dict(eventName='userPromptSubmit', command=coord_cmd, enabled=True, trustStatus='trusted'),
                dict(eventName='userPromptSubmit', command=mem_cmd, enabled=True, trustStatus='trusted'),
                dict(eventName='stop', command=unrelated_cmd, enabled=True, trustStatus='untrusted'),
                dict(eventName='stop', command=coord_cmd, enabled=True,
                     trustStatus='trusted' if stop_trusted else 'untrusted'),
                dict(eventName='stop', command=mem_cmd, enabled=True,
                     trustStatus='trusted' if stop_trusted else 'untrusted'),
            ])]

        # Process A trusts updated hooks; Process B still holds untrusted cached Stop hooks.
        qt_a.serve(hooks_data=make_hooks(True))
        qt_b.serve(hooks_data=make_hooks(False))
        res_a = qt_a.queue.inspect_hooks(qt_a.path, qt_a.process, 'native-one', str(self.root))
        res_b = qt_b.queue.inspect_hooks(qt_b.path, qt_b.process, 'native-one', str(self.root))
        qt_a.join()
        qt_b.join()
        self.assertEqual(res_a['status'], 'ready')
        self.assertEqual(res_b['status'], 'untrusted')
        self.assertEqual(res_b['untrusted_events'], ['stop'])


if __name__ == '__main__':
    unittest.main()
