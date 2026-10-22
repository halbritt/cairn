"""CAIRN-188: a Codex thread left in systemError by a transient provider error (for example
server_overloaded) gets a normal idle wake after a bounded backoff. Errors that do not clear by
themselves (usage_limit_exceeded, auth) keep the CAIRN-179 suppression. Held work is never
replayed. Real pinned rollout files and a mocked store."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from test_codex_refusal_recovery import CodexBase
from test_postclaim_refusal_settlement import coord, SESSION, ATTEMPT, DELIVERY, RECORD

TURN = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
LATER = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
ENDED = datetime.fromisoformat('2026-10-22T07:25:25.335+00:00').timestamp()


def started(turn, ordinal, stamp):
    return dict(timestamp=stamp, ordinal=ordinal, type='event_msg', payload=dict(
        type='task_started', turn_id=turn, root_turn_id=turn, started_at=int(ENDED) - 300))


def completed(turn, ordinal, stamp, info, message=None):
    payload = dict(type='task_complete', turn_id=turn, root_turn_id=turn, last_agent_message=message,
                   started_at=int(ENDED) - 300, completed_at=int(ENDED))
    if info is not None:
        payload['error'] = dict(message='Selected model is at capacity.', codex_error_info=info)
    return dict(timestamp=stamp, ordinal=ordinal, type='event_msg', payload=payload)


class TransientErrorWakeBase(CodexBase):
    def setUp(self):
        super().setUp()
        self.endpoint = dict(endpoint=str(self.root / 'native-queue.sock'), owner='process')
        for key in ('inbox_intent', 'inbox_attempt', 'inbox_result_policy',
                    'inbox_completion', 'inbox_response', 'wake_refusal_diagnostic'):
            self.state.pop(key, None)
        self.state['agent']['metadata']['state'] = 'busy'
        self.ready = dict(delivery_id=DELIVERY, event_id=RECORD, request_id=ATTEMPT)
        self.set_error('server_overloaded')
        # As left by earlier cycles: a stale systemError observation for this session.
        self.state['native_health'] = dict(session=coord.session_ref(self.state['agent']),
                                           status_type='systemError', observed_at_epoch=0)
        self.save()

    def set_error(self, info, after=()):
        self.records = [
            started(TURN, 3500, '2026-10-22T07:20:15.000Z'),
            dict(timestamp='2026-10-22T07:25:01.634Z', ordinal=3549, type='event_msg',
                 payload=dict(type='token_count', info=None)),
            completed(TURN, 3551, '2026-10-22T07:25:25.335Z', info),
            # A child agent's completion after the error names another turn; it is not a turn start.
            dict(timestamp='2026-10-22T07:35:14.711Z', ordinal=3552, type='event_msg', payload=dict(
                type='item_completed', thread_id=SESSION, turn_id=LATER,
                item=dict(type='SubAgentActivity', kind='completed'))),
            *after,
        ]
        self.write_records()

    def api(self, config, op, request, **kwargs):
        if op == 'session-inbox-ready':
            self.ops.append((op, request))
            # As the store: readiness is answered only for an idle presence.
            if self.state['agent']['metadata'].get('state') != 'idle':
                return {}
            return dict(self.ready)
        if op == 'agent-context':
            self.ops.append((op, request))
            agent = dict(self.state['agent'], metadata=request['metadata'],
                         context_revision=request['expected_revision'] + 1)
            return agent
        return super().api(config, op, request, **kwargs)

    def prepare(self, now, status='systemError', queue_count=0):
        inspected = dict(status_type=status, updated_at=1700000500, queue_count=queue_count,
                         queued_ids=[], queued_present=None, peer=self.process)
        cycle = {}
        with self.patched(), patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord.time, 'time', return_value=now), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = inspected
            prepared = coord.prepare_idle_wake(self.cc, self.state, self.path, cycle)
            obs = coord.delivery_observation(self.cc, self.state, prepared, cycle)
        return prepared, obs

    def op_names(self):
        return [op for op, _ in self.ops]

    def assert_never_claims(self):
        for forbidden in ('session-inbox-claim', 'event-claim', 'event-complete', 'session-inbox-reconcile'):
            self.assertNotIn(forbidden, self.op_names())


class TransientErrorWakeTests(TransientErrorWakeBase):
    def test_server_overloaded_behind_busy_metadata_wakes_after_five_minutes(self):
        prepared, obs = self.prepare(ENDED + 301)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared['wake']['transport'], 'codex-queue')
        self.assertEqual(prepared['wake']['delivery_id'], DELIVERY)
        contexts = [r for op, r in self.ops if op == 'agent-context']
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]['metadata']['state'], 'idle')
        self.assertEqual(self.state['agent']['metadata']['state'], 'idle')
        record = self.state['codex_error_wake']
        self.assertEqual(record['decision'], 'woken')
        self.assertEqual(record['error']['kind'], 'server_overloaded')
        self.assertEqual(record['error']['turn_id'], TURN)
        self.assertEqual(record['error']['record'], self.locators[2])
        self.assertEqual(record['wakes'], 1)
        self.assertEqual(record['delivery_id'], DELIVERY)
        self.assertIn('transient', record['reason'])
        self.assertEqual(obs['condition'], 'automatic_available')
        self.assertNotIn('wake_refusal', self.state)
        # The errored turn ran no Stop, so its per-turn delivery latch is stale too; left set,
        # the woken turn's hook would admit no work.
        self.assertIs(self.state['delivered_since_idle'], False)
        self.assertEqual(record['latch'], 'cleared')
        self.assertEqual(json.loads(self.path.read_text())['codex_error_wake']['decision'], 'woken')
        self.assert_never_claims()

    def test_idle_metadata_with_transient_error_wakes_without_presence_change(self):
        self.state['agent']['metadata']['state'] = 'idle'
        self.save()
        prepared, _ = self.prepare(ENDED + 600)
        self.assertEqual(prepared['wake']['transport'], 'codex-queue')
        self.assertNotIn('agent-context', self.op_names())
        self.assertEqual(self.state['codex_error_wake']['decision'], 'woken')
        self.assert_never_claims()

    def test_backoff_before_five_minutes_keeps_presence_and_records_next_time(self):
        prepared, _ = self.prepare(ENDED + 120)
        self.assertIsNone(prepared)
        self.assertNotIn('agent-context', self.op_names())
        self.assertEqual(self.state['agent']['metadata']['state'], 'busy')
        record = self.state['codex_error_wake']
        self.assertEqual(record['decision'], 'backoff')
        self.assertEqual(record['next_eligible_epoch'], ENDED + 300)
        self.assertIs(self.state['delivered_since_idle'], True)
        self.assertIn('server_overloaded', record['reason'])
        self.assertNotIn('idle_wake', self.state)
        self.assert_never_claims()

    def test_usage_limit_keeps_cairn_179_suppression(self):
        self.set_error('usage_limit_exceeded')
        self.state['agent']['metadata']['state'] = 'idle'
        self.save()
        prepared, obs = self.prepare(ENDED + 86400)
        self.assertIsNone(prepared)
        self.assertNotIn('agent-context', self.op_names())
        self.assertEqual(self.state['codex_error_wake']['decision'], 'persistent')
        self.assertIs(self.state['delivered_since_idle'], True)
        self.assertEqual(self.state['wake_refusal']['condition'], 'native_transport_unavailable')
        self.assertEqual(obs['condition'], 'native_transport_unavailable')
        self.assertNotIn('idle_wake', self.state)
        self.assert_never_claims()

    def test_auth_and_unknown_errors_are_not_transient(self):
        for info in ('unauthorized', 'context_window_exceeded', 'bad_request', 'other', 'some_new_kind',
                     {'http_connection_failed': {'http_status_code': 401}},
                     {'http_connection_failed': {'http_status_code': 429}}):
            with self.subTest(info=info):
                self.assertEqual(coord.codex_error_class(info)[0], 'persistent')
        for info in ('server_overloaded', 'internal_server_error', 'response_stream_disconnected',
                     'response_stream_connection_failed', 'response_too_many_failed_attempts',
                     {'http_connection_failed': {'http_status_code': 503}},
                     {'http_connection_failed': {'http_status_code': None}}):
            with self.subTest(info=info):
                self.assertEqual(coord.codex_error_class(info)[0], 'transient')

    def test_error_free_or_answered_end_is_unverified_and_suppressed(self):
        for info, message in ((None, None), ('server_overloaded', 'partial answer')):
            with self.subTest(info=info, message=message):
                self.ops.clear()
                self.state.pop('codex_error_wake', None)
                self.state.pop('wake_refusal', None)
                self.state['agent']['metadata']['state'] = 'busy'
                self.records = [started(TURN, 1, '2026-10-22T07:20:15.000Z'),
                                completed(TURN, 2, '2026-10-22T07:25:25.335Z', info, message)]
                self.write_records()
                prepared, _ = self.prepare(ENDED + 3600)
                self.assertIsNone(prepared)
                self.assertEqual(self.state['codex_error_wake']['decision'], 'unverified')
                self.assertNotIn('agent-context', self.op_names())

    def test_turn_started_after_the_error_is_not_woken(self):
        self.set_error('server_overloaded', after=[started(LATER, 3553, '2026-10-22T07:40:00.000Z')])
        prepared, _ = self.prepare(ENDED + 3600)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_error_wake']['decision'], 'unverified')
        self.assertNotIn('agent-context', self.op_names())

    def test_held_work_is_never_replayed(self):
        self.state['inbox_attempt'] = dict(attempt_id=ATTEMPT, native_turn_id=TURN)
        self.save()
        prepared, _ = self.prepare(ENDED + 3600)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_error_wake']['decision'], 'held')
        self.assertNotIn('agent-context', self.op_names())
        self.assert_never_claims()

    def test_native_queue_items_block_the_wake(self):
        prepared, _ = self.prepare(ENDED + 3600, queue_count=1)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_error_wake']['decision'], 'held')
        self.assertNotIn('agent-context', self.op_names())

    def test_backoff_doubles_after_each_wake_and_is_capped(self):
        last = ENDED + 400
        for wakes, delay in ((1, 600), (2, 1200), (3, 2400), (4, 3600), (9, 3600)):
            with self.subTest(wakes=wakes):
                self.ops.clear()
                self.state.pop('idle_wake', None)
                self.state['agent']['metadata']['state'] = 'busy'
                self.state['codex_error_wake'] = dict(
                    schema='cairn.codex-error-wake/1', decision='woken', wakes=wakes, last_wake_at=last,
                    error=dict(kind='server_overloaded', turn_id=TURN, ended_at='2026-10-22T07:25:25.335Z',
                               record=self.locators[2]))
                prepared, _ = self.prepare(last + delay - 1)
                self.assertIsNone(prepared)
                self.assertEqual(self.state['codex_error_wake']['decision'], 'backoff')
                self.assertEqual(self.state['codex_error_wake']['next_eligible_epoch'], last + delay)
                prepared, _ = self.prepare(last + delay)
                self.assertIsNotNone(prepared)
                self.assertEqual(self.state['codex_error_wake']['wakes'], wakes + 1)

    def test_a_woken_turn_failing_again_keeps_its_backoff(self):
        # The previous wake at ENDED - 200 started the turn that failed at ENDED.
        self.state['codex_error_wake'] = dict(
            schema='cairn.codex-error-wake/1', decision='woken', wakes=2, last_wake_at=ENDED - 200,
            error=dict(kind='server_overloaded', turn_id=LATER, ended_at='2026-10-22T06:00:00.000Z',
                       record=dict(offset=0, length=1, sha256='0' * 64)))
        prepared, _ = self.prepare(ENDED + 301)
        self.assertIsNone(prepared)
        record = self.state['codex_error_wake']
        self.assertEqual((record['decision'], record['wakes']), ('backoff', 2))
        self.assertEqual(record['next_eligible_epoch'], ENDED - 200 + 1200)

    def test_an_old_chain_resets_for_an_unrelated_error(self):
        self.state['codex_error_wake'] = dict(
            schema='cairn.codex-error-wake/1', decision='woken', wakes=4, last_wake_at=ENDED - 7200,
            error=dict(kind='server_overloaded', turn_id=LATER, ended_at='2026-10-22T05:00:00.000Z',
                       record=dict(offset=0, length=1, sha256='0' * 64)))
        prepared, _ = self.prepare(ENDED + 301)
        self.assertIsNotNone(prepared)
        self.assertEqual(self.state['codex_error_wake']['wakes'], 1)

    def test_rollout_without_an_open_owner_is_unverified(self):
        self.rollout_handle.close()
        prepared, _ = self.prepare(ENDED + 3600)
        self.assertIsNone(prepared)
        self.assertEqual(self.state['codex_error_wake']['decision'], 'unverified')
        self.assertNotIn('agent-context', self.op_names())



class StoreDeliveryLookupTests(TransientErrorWakeBase):
    def test_store_lookup_sends_only_fields_the_api_accepts(self):
        """event-inspect decodes strictly (EventStatusRequest: event_id, after, limit). An extra
        delivery_id made every reconcile-codex-queue-wake store lookup fail as INVALID_REQUEST."""
        sent = []

        def strict(config, op, request, **kwargs):
            if op == 'session-inbox-ready':
                return {}
            sent.append(request)
            if set(request) - {'event_id', 'after', 'limit'}:
                raise coord.CoordinationError('INVALID_REQUEST', 'Cairn refused the lifecycle operation')
            return dict(event=dict(event_id=RECORD, repo=self.cc['repo']), deliveries=[dict(
                delivery_id=DELIVERY, state='handled', consumer=self.state['agent']['inbox'], attempts=1)])
        with patch.object(coord, 'call', side_effect=strict):
            found, _ = coord._codex_store_delivery(self.cc, self.state, DELIVERY, RECORD)
            missing, _ = coord._codex_store_delivery(self.cc, self.state, DELIVERY, None)
        self.assertEqual(found['state'], 'handled')
        self.assertEqual(sent, [dict(event_id=RECORD, limit=100)])
        self.assertIsNone(missing)


if __name__ == '__main__':
    unittest.main()
