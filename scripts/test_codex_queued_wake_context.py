"""CAIRN-364: Codex queued wake admission across stale delivered_since_idle latches,
pre-execution refusal on missing/mismatched/reused wake context, consumed-queue
reconciliation, and Go/Python frontend parity."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_f04_activation as f04
import test_inbox_recall_bridge as irb
from test_inbox_recall_bridge import (
    ROOT, coord,
    SESSION, TURN, AGENT, EXEC, DELIVERY, ATTEMPT, RECORD,
)

TURN_1 = '01a1b24f-3fcb-7e00-9b90-5368b28bdd19'
TURN_2 = '01a1b26a-46e3-7a03-b273-80da3e590f3a'
TURN_3 = '01a1b277-8888-7000-8000-111111111111'
QUEUED_ID = '01a1b26a-46e0-7503-9a2a-e2f56d63f758'
NOTICE_DELIVERY = '0e413b5b-4b15-48b6-be0a-1262b592096a'
NOTICE_EVENT = '227c4cf2-4fd6-4e99-a63f-04574abd7839'
NOTICE_SOURCE = 'ec16e586-3afb-45fd-97da-1d0716ab0619'
NOTICE_ATTEMPT = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa'
NEXT_DELIVERY = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'
NEXT_ATTEMPT = 'cccccccc-3333-4333-8333-cccccccccccc'
OTHER_EXEC = 'dddddddd-4444-4444-8444-dddddddddddd'


class CodexQueuedWakeContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bin_dir = tempfile.TemporaryDirectory()
        cls.lifecycle_bin = Path(cls._bin_dir.name) / 'cairn-lifecycle'
        subprocess.run(
            ['go', 'build', '-buildvcs=false', '-o', str(cls.lifecycle_bin), './cmd/cairn-lifecycle'],
            cwd=ROOT, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls._bin_dir.cleanup()

    def setUp(self):
        self.bridge = irb.InboxRecallBridgeTests()
        self.bridge.setUp()
        self.addCleanup(self.bridge.doCleanups)
        self.root = self.bridge.root
        self.sock_path = self.root / 'codex-queue.sock'
        self.endpoint = dict(endpoint=str(self.sock_path), owner='process')
        self.herdr = self.root / 'herdr'
        self.herdr.write_text('#!/bin/sh\nexit 1\n')
        self.herdr.chmod(0o700)
        self.cc = self.bridge.cc
        self.cc['idle_wakeup'] = str(self.herdr)
        self.mc = self.bridge.mc
        self.process = self.bridge.process
        self.session = dict(agent_id=AGENT, execution_id=EXEC)
        self.notice_body = 'Harmless team coordination notice for CAIRN-364.'
        self.bridge.fixture['search']['selected'] = [
            dict(mandatory=True, record=dict(body='Mandatory project direction rule.'))
        ]
        self.state = self.bridge.state
        # Reproduce post-request state: prior request completed, Stop was missed so
        # delivered_since_idle remains True while metadata reports idle and nothing is held.
        self.state.pop('inbox_intent', None)
        self.state.pop('inbox_attempt', None)
        self.state['delivered_since_idle'] = True
        self.state['seen_codex_turns'] = [TURN_1]
        self.state['agent']['metadata']['state'] = 'idle'
        self.ready = dict(delivery_id=NOTICE_DELIVERY, event_id=NOTICE_EVENT, request_id=NOTICE_ATTEMPT)
        self.claimable = {
            NOTICE_DELIVERY: dict(
                delivery_id=NOTICE_DELIVERY, lease_id=NOTICE_ATTEMPT, state='leased',
                event=dict(event_id=NOTICE_EVENT, kind='notice',
                           ref=dict(record_id=NOTICE_SOURCE, version=1), **{'from': 'agent/coordinator'}),
            ),
            NEXT_DELIVERY: dict(
                delivery_id=NEXT_DELIVERY, lease_id=NEXT_ATTEMPT, state='leased',
                event=dict(event_id=TURN_3, kind='notice',
                           ref=dict(record_id=RECORD, version=2), **{'from': 'agent/coordinator'}),
            ),
        }
        self.claims = []
        self.context_updates = []
        self.bridge.freeze()

    def state_path(self):
        return coord.state_path(self.cc, SESSION)

    def load_state(self):
        return json.loads(self.state_path().read_text())

    def save_state(self, state):
        self.state = state
        coord.write_state(self.state_path(), state)

    def wake_prompt_for(self, delivery_id, agent_id=AGENT, execution_id=EXEC):
        return coord.wake_message(dict(session=dict(agent_id=agent_id, execution_id=execution_id),
                                       delivery_id=delivery_id))

    def api(self, config, op, request, **kwargs):
        if op == 'session-inbox-ready':
            return dict(self.ready) if self.ready else {}
        if op == 'session-inbox-claim':
            self.claims.append(dict(request))
            target_id = request.get('delivery_id') or (self.ready or {}).get('delivery_id')
            delivery = self.claimable.get(target_id)
            if not delivery:
                return {}
            attempt = dict(attempt_id=request['request_id'], session=request['session'],
                           native_turn_id=request.get('native_turn_id'), delivery=delivery)
            return dict(attempt=attempt)
        if op in ('event-renew', 'session-inbox-renew'):
            did = request['delivery_id']
            return self.claimable[did]
        if op == 'agent-context':
            self.context_updates.append(dict(request))
            current = self.load_state()['agent']
            return dict(current, metadata=request['metadata'],
                        context_revision=request['expected_revision'] + 1)
        if op == 'history':
            return self.bridge.fixture['history']
        if op == 'session-delivery-observe':
            return dict(applied=True)
        if op == 'session-inbox-release':
            return dict(released=True)
        if op == 'session-inbox-reconcile':
            attempt = (self.load_state().get('inbox_attempt') or {})
            if request.get('reason') == 'delivery_completed' and not attempt.get('finished_at'):
                raise coord.CoordinationError('DELIVERY_ACTIVE', 'delivery is still active')
            return dict(released=True)
        if op == 'event-inspect':
            return dict(deliveries=list(self.claimable.values()))
        if op == 'event-delivery':
            did = request['delivery_id']
            return self.claimable.get(did) or dict(delivery_id=did, state='cancelled', attempts=0)
        raise AssertionError(f'unexpected store op {op}: {request}')

    def enqueue_wake(self, *, started=False, queued_id=QUEUED_ID):
        state = self.load_state()
        cycle = {}
        with patch.object(coord, 'call', side_effect=self.api), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.enqueue.return_value = (queued_id, started)
            prepared = coord.prepare_idle_wake(self.cc, state, self.state_path(), cycle)
            self.assertIsNotNone(prepared)
            coord.submit_idle_wake(self.cc, self.state_path(), prepared)
        return self.load_state()

    def run_prompt(self, turn_id, prompt, *, owner=None, alive=True, heartbeat_err=None, api_fn=None):
        ev = dict(session_id=SESSION, turn_id=turn_id, cwd=str(self.root),
                  hook_event_name='UserPromptSubmit', prompt=prompt)
        hb_side = heartbeat_err if heartbeat_err else (lambda cfg, st: st['agent'])
        with patch.object(coord, 'owner_process', return_value=owner or self.process), \
                patch.object(coord, 'process_alive', return_value=alive), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'heartbeat', side_effect=hb_side), \
                patch.object(coord, 'associate_wake'), \
                patch.object(coord, 'call', side_effect=api_fn or self.api):
            return coord.handle(self.cc, ev, _inbox_resolved=True)

    def test_completed_prior_request_latch_admits_queued_notice_with_mandatory_context_and_advances_next(self):
        # 1. Enqueue wake when started=False (Codex auto-start race from bug-B.json -> status='queued').
        enqueued = self.enqueue_wake(started=False, queued_id=QUEUED_ID)
        self.assertEqual(enqueued['idle_wake']['status'], 'queued')
        self.assertEqual(enqueued['idle_wake']['queued_submission_id'], QUEUED_ID)
        self.assertIs(enqueued['delivered_since_idle'], True)

        # 2. Fresh queue turn TURN_2 crosses stale delivered_since_idle latch, claims NOTICE_DELIVERY once,
        # clears idle_wake, and injects mandatory notice context + explicit acknowledgement command.
        res = self.run_prompt(TURN_2, self.wake_prompt_for(NOTICE_DELIVERY))
        text = res['hookSpecificOutput']['additionalContext']
        self.assertEqual(len(self.claims), 1)
        self.assertEqual(self.claims[0]['delivery_id'], NOTICE_DELIVERY)
        self.assertEqual(self.claims[0]['native_turn_id'], TURN_2)
        self.assertIn('Cairn has a notice from agent/coordinator for this conversation.', text)
        self.assertIn('Mandatory project direction rule.', text)
        self.assertIn('"schema":"cairn.inbox-recall/1"', text)

        after_claim = self.load_state()
        self.assertNotIn('idle_wake', after_claim)
        self.assertEqual(after_claim['inbox_attempt']['delivery']['delivery_id'], NOTICE_DELIVERY)
        ctx_file = Path(self.cc['state_dir']) / 'inbox' / (after_claim['inbox_attempt']['attempt_id'] + '.json')
        ctx_json = json.loads(ctx_file.read_text())
        self.assertEqual(ctx_json['delivery_id'], NOTICE_DELIVERY)
        self.assertEqual(ctx_json['source']['record_id'], NOTICE_SOURCE)
        self.assertEqual(ctx_json['acknowledgement'][-1], 'ack')

        # 3. Settle NOTICE_DELIVERY explicitly and run Stop; verify a subsequent distinct message progresses once.
        after_claim['inbox_attempt']['finished_at'] = '2026-11-06T19:51:31Z'
        self.save_state(after_claim)
        stop_ev = dict(session_id=SESSION, turn_id=TURN_2, cwd=str(self.root), hook_event_name='Stop')
        with patch.object(coord, 'owner_process', return_value=self.process), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'heartbeat', side_effect=lambda cfg, st: st['agent']), \
                patch.object(coord, 'associate_wake'), \
                patch.object(coord, 'call', side_effect=self.api):
            coord.handle(self.cc, stop_ev, _inbox_resolved=True)

        self.ready = dict(delivery_id=NEXT_DELIVERY, event_id=TURN_3, request_id=NEXT_ATTEMPT)
        self.enqueue_wake(started=True, queued_id='queued-next')
        calls_path = self.root / 'calls.jsonl'
        if calls_path.exists():
            calls_path.unlink()
        res_next = self.run_prompt(TURN_3, self.wake_prompt_for(NEXT_DELIVERY))
        self.assertIn('Cairn has a notice from agent/coordinator for this conversation.',
                      res_next['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['delivery_id'] for c in self.claims], [NOTICE_DELIVERY, NEXT_DELIVERY])

    def test_missing_mismatched_and_reused_turn_refuse_before_execution_and_reconcile_consumed_queue(self):
        # 1. Reused turn_id (TURN_1 already in seen_codex_turns) refuses pre-claim, pops idle_wake, keeps idle.
        self.enqueue_wake(started=False)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt(TURN_1, self.wake_prompt_for(NOTICE_DELIVERY))
        saved = self.load_state()
        self.assertEqual(self.claims, [])
        self.assertNotIn('idle_wake', saved)
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'seen_turn')
        self.assertEqual(saved['agent']['metadata']['state'], 'idle')

        # 2. Bound wake whose delivery is no longer claimable refuses pre-execution (never emits source:null).
        self.enqueue_wake(started=False)
        self.claimable.pop(NOTICE_DELIVERY)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt(TURN_2, self.wake_prompt_for(NOTICE_DELIVERY))
        saved = self.load_state()
        self.assertNotIn('idle_wake', saved)
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'unclaimed_bound_wake')
        self.assertEqual(saved['agent']['metadata']['state'], 'idle')

        # 3. Consumed native queue item when ready head changes transitions queued -> refused/queue_item_missing,
        # and after explicit reconciliation a later unrelated message progresses once.
        self.claimable[NOTICE_DELIVERY] = dict(
            delivery_id=NOTICE_DELIVERY, lease_id=NOTICE_ATTEMPT, state='cancelled', attempts=0,
            event=dict(event_id=NOTICE_EVENT, kind='notice', ref=dict(record_id=NOTICE_SOURCE, version=1),
                       **{'from': 'agent/coordinator'}))
        self.enqueue_wake(started=False, queued_id=QUEUED_ID)
        self.ready = dict(delivery_id=NEXT_DELIVERY, event_id=TURN_3, request_id=NEXT_ATTEMPT)
        state = self.load_state()
        cycle = {}
        with patch.object(coord, 'call', side_effect=self.api), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.inspect_thread.return_value = dict(
                status_type='idle', updated_at=100, queue_count=0, queued_ids=[],
                queued_present=False, peer=self.process)
            prep = coord.prepare_idle_wake(self.cc, state, self.state_path(), cycle)
            self.assertIsNone(prep)
            self.assertEqual(state['idle_wake']['status'], 'refused')
            self.assertEqual(state['idle_wake']['reconciliation_required'], 'queue_item_missing')
            reconciled = coord.reconcile_codex_queue_wake(
                self.cc, SESSION, NOTICE_DELIVERY, QUEUED_ID, outcome='cancelled')
            self.assertEqual(reconciled['status'], 'reconciled')
        self.enqueue_wake(started=True, queued_id='queued-after-reconcile')
        res_after = self.run_prompt(TURN_3, self.wake_prompt_for(NEXT_DELIVERY))
        self.assertIn('Cairn has a notice from agent/coordinator for this conversation.',
                      res_after['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['delivery_id'] for c in self.claims], [NOTICE_DELIVERY, NEXT_DELIVERY])

    def test_native_queue_ownership_ordinary_prompts_and_ledger_grant_conflict(self):
        self.enqueue_wake(started=False)
        # Ordinary user prompt while codex-queue owns admission must NOT claim NOTICE_DELIVERY or pop idle_wake.
        user_res = self.run_prompt('turn-owner-prompt', 'Explain the schema layout.')
        self.assertEqual(self.claims, [])
        self.assertNotIn('inbox ack', user_res['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.load_state()['idle_wake']['delivery_id'], NOTICE_DELIVERY)

        # 1. Real Codex .memory-budget.json turn grant on TURN_2 refuses bound Codex queued wake pre-claim.
        st = self.load_state()
        st['agent']['metadata']['state'] = 'idle'
        self.save_state(st)
        mem_dir = Path(self.mc['state_dir'])
        mem_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        budget_path = mem_dir / (SESSION + '.memory-budget.json')
        budget_path.write_text(json.dumps(dict(
            schema='cairn.codex-memory-grants/1', session_id=SESSION, active_turn_id=TURN_2,
            turns={TURN_2: dict(limit_bytes=9500, emitted_hook_bytes=400, reserved_native_bytes=0, granted=True)},
            pending=dict(limit_bytes=9500, emitted_hook_bytes=0, reserved_native_bytes=0, granted=False),
        )))
        budget_path.chmod(0o600)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt(TURN_2, self.wake_prompt_for(NOTICE_DELIVERY))
        saved = self.load_state()
        self.assertEqual(self.claims, [])
        self.assertNotIn('idle_wake', saved)
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'ledger_grant')
        self.assertEqual(saved['wake_preclaim_refusal']['grant_kind'], 'ordinary_context')

        # 2. Prior unadmitted_wake grant with matching wake_sha256 under an earlier turn_id also refuses pre-claim.
        budget_path.unlink()
        self.enqueue_wake(started=False)
        wake_text = self.wake_prompt_for(NOTICE_DELIVERY)
        wake_hash = hashlib.sha256(wake_text.encode('utf-8')).hexdigest()
        prior_identity = dict(native_owner=TURN_1, wake_sha256=wake_hash, session=self.session,
                              boundary='unadmitted_wake')
        prior_key = hashlib.sha256(json.dumps(prior_identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        ledger_path = mem_dir / (SESSION + '.inbox-recall.json')
        ledger_path.write_text(json.dumps(dict(
            schema='cairn.inbox-recall-grants/1', session=SESSION, active=prior_key, pending_bytes=0,
            grants={prior_key: dict(
                identity=prior_identity, wake_sha256=wake_hash,
                limit_bytes=9500, output_bytes=400, native_allowance_bytes=0,
            )},
        )))
        ledger_path.chmod(0o600)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt(TURN_3, wake_text)
        saved = self.load_state()
        self.assertEqual(self.claims, [])
        self.assertNotIn('idle_wake', saved)
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'ledger_grant')
        self.assertEqual(saved['wake_preclaim_refusal']['grant_kind'], 'unadmitted_wake')

        # 3. Joined/reused Codex turn_id on a non-queue session does NOT claim waiting work via ordinary_codex_binding.
        self.setUp()
        st = self.load_state()
        st['delivered_since_idle'] = False
        self.save_state(st)
        ev_joined = dict(session_id=SESSION, turn_id=TURN_1, cwd=str(self.root),
                         hook_event_name='UserPromptSubmit', prompt='Follow-up in same turn.')
        with patch.object(coord, 'owner_process', return_value=self.process), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=None), \
                patch.object(coord, 'heartbeat', side_effect=lambda cfg, s: s['agent']), \
                patch.object(coord, 'associate_wake'), \
                patch.object(coord, 'call', side_effect=self.api):
            coord.handle(self.cc, ev_joined, _inbox_resolved=True)
        self.assertEqual(self.claims, [])

    def test_negative_controls_wrong_execution_wrong_delivery_held_work_fenced_process_and_uncertain_wake(self):
        # 1. Another execution wake prompt -> wake_session_mismatch BEFORE inbox_context (even when non-queue & latch False).
        st = self.load_state()
        st['delivered_since_idle'] = False
        self.save_state(st)
        ev_other = dict(session_id=SESSION, turn_id='turn-other-exec-nonqueue', cwd=str(self.root),
                        hook_event_name='UserPromptSubmit',
                        prompt=self.wake_prompt_for(NOTICE_DELIVERY, execution_id=OTHER_EXEC))
        with patch.object(coord, 'owner_process', return_value=self.process), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=None), \
                patch.object(coord, 'heartbeat', side_effect=lambda cfg, s: s['agent']), \
                patch.object(coord, 'associate_wake'), \
                patch.object(coord, 'call', side_effect=self.api):
            with self.assertRaises(coord.NativePromptRefused):
                coord.handle(self.cc, ev_other, _inbox_resolved=True)
        self.assertEqual(self.claims, [])
        self.assertEqual(self.load_state()['wake_preclaim_refusal']['reason'], 'wake_session_mismatch')

        self.setUp()
        self.enqueue_wake(started=False)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt('turn-other-exec', self.wake_prompt_for(NOTICE_DELIVERY, execution_id=OTHER_EXEC))
        saved = self.load_state()
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'wake_session_mismatch')
        self.assertEqual(saved['idle_wake']['delivery_id'], NOTICE_DELIVERY)
        self.assertEqual(self.claims, [])

        # 2. Wrong delivery UUID text on codex-queue session -> unbound_wake_prompt; real idle_wake preserved.
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt('turn-wrong-deliv', self.wake_prompt_for(NEXT_DELIVERY))
        saved = self.load_state()
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'unbound_wake_prompt')
        self.assertEqual(saved['idle_wake']['delivery_id'], NOTICE_DELIVERY)
        self.assertEqual(self.claims, [])

        # 3. Prior held work for DELIVERY prevents claiming or injecting mismatched context for NOTICE_DELIVERY.
        held_state = self.load_state()
        held_state['inbox_intent'] = dict(request_id=ATTEMPT, session=self.session, native_turn_id=TURN_1)
        held_state['inbox_attempt'] = dict(
            attempt_id=ATTEMPT, session=self.session, native_turn_id=TURN_1,
            delivery=dict(delivery_id=DELIVERY, lease_id=ATTEMPT, state='leased',
                          event=dict(event_id=TURN, kind='request',
                                     ref=dict(record_id=RECORD, version=2), **{'from': 'agent/sender'})))
        self.claimable[DELIVERY] = held_state['inbox_attempt']['delivery']
        self.save_state(held_state)
        with self.assertRaises(coord.NativePromptRefused):
            self.run_prompt('turn-held-conflict', self.wake_prompt_for(NOTICE_DELIVERY))
        saved = self.load_state()
        self.assertEqual(saved['wake_preclaim_refusal']['reason'], 'prior_held_work')
        self.assertEqual(saved['inbox_attempt']['delivery']['delivery_id'], DELIVERY)
        self.assertEqual(self.claims, [])

        # 4. Live process conflict -> SESSION_BUSY wrapped as NativePromptRefused without claiming.
        self.setUp()
        self.enqueue_wake(started=False)
        other_proc = dict(pid=self.process['pid'] + 777, start=777, boot='other')
        with self.assertRaises(coord.NativePromptRefused) as ctx_busy:
            self.run_prompt('turn-proc-conflict', self.wake_prompt_for(NOTICE_DELIVERY), owner=other_proc, alive=True)
        self.assertEqual(ctx_busy.exception.__cause__.code, 'SESSION_BUSY')
        self.assertEqual(self.claims, [])

        # 5. Uncertain wake submission blocks duplicate wake / automatic uncertain replay.
        st = self.load_state()
        st['idle_wake']['status'] = 'uncertain'
        st['idle_wake'].pop('queued_submission_id', None)
        self.save_state(st)
        cycle = {}
        with patch.object(coord, 'call', side_effect=self.api), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint):
            prep = coord.prepare_idle_wake(self.cc, st, self.state_path(), cycle)
        self.assertIsNone(prep)
        self.assertEqual(st['wake_refusal']['condition'], 'wake_retained')

        # 6. Fenced/stopped session raises STALE_SESSION without claiming.
        self.setUp()
        self.enqueue_wake(started=False)

        def fenced_heartbeat(cfg, s):
            raise coord.CoordinationError('STALE_SESSION', 'session execution is stopped')

        def fenced_api(config, op, request, **kwargs):
            if op == 'agent-directory':
                return dict(agents=[dict(agent_id=AGENT, execution_id=EXEC, stopped=True,
                                         metadata=self.state['agent']['metadata'])])
            return self.api(config, op, request, **kwargs)

        with self.assertRaises((coord.CoordinationError, coord.NativePromptRefused)):
            self.run_prompt(TURN_3, self.wake_prompt_for(NOTICE_DELIVERY),
                            heartbeat_err=fenced_heartbeat, api_fn=fenced_api)
        self.assertEqual(self.claims, [])

        # 7. Overlapping unrelated organic notice during submit_idle_wake preserves queued_submission_id.
        self.setUp()
        st = self.load_state()
        cycle = {}
        with patch.object(coord, 'call', side_effect=self.api), \
                patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'codex_queue_endpoint', return_value=self.endpoint):
            prepared = coord.prepare_idle_wake(self.cc, st, self.state_path(), cycle)
        organic_state = self.load_state()
        organic_state['inbox_intent'] = dict(request_id='org-1', session=self.session, native_turn_id=TURN_1)
        organic_state['inbox_attempt'] = dict(
            attempt_id='org-1', session=self.session, native_turn_id=TURN_1, admission_kind='organic',
            delivery=dict(delivery_id=DELIVERY, lease_id='org-1', state='leased',
                          event=dict(event_id=TURN, kind='notice',
                                     ref=dict(record_id=RECORD, version=2), **{'from': 'agent/sender'})))
        self.save_state(organic_state)
        with patch.object(coord, 'process_alive', return_value=True), \
                patch.object(coord, 'queue_helper') as qmod:
            qmod.return_value.enqueue.return_value = (QUEUED_ID, False)
            coord.submit_idle_wake(self.cc, self.state_path(), prepared)
        after_submit = self.load_state()
        self.assertEqual(after_submit['idle_wake']['status'], 'queued')
        self.assertEqual(after_submit['idle_wake']['queued_submission_id'], QUEUED_ID)

    def test_public_go_lifecycle_frontend_admits_queued_notice_and_refuses_unclaimed_bound_wake(self):
        import http.server
        import socketserver
        import threading

        fx = f04.Fixture(self.root / 'f04-public', self.lifecycle_bin, harness='codex')
        fx.coord['idle_wakeup'] = str(self.herdr)
        notice_delivery = dict(
            delivery_id=DELIVERY, lease_id=ATTEMPT, state='leased',
            event=dict(event_id=NOTICE_EVENT, kind='notice',
                       ref=dict(record_id=NOTICE_SOURCE, version=1), **{'from': 'agent/coordinator'}),
        )
        token = fx.root / 'recall-token'
        sock = fx.root / 'recall.sock'
        f04.write(token, b'f04-synthetic-token', 0o600)
        for cfg in (fx.memory, fx.coord):
            cfg.update(socket=str(sock), token_file=str(token))
        fx.publish_configuration()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(inner):
                req = json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                if inner.headers.get('Authorization') != 'Bearer f04-synthetic-token':
                    inner.send_error(401)
                    return
                op = inner.path.removeprefix('/v1/')
                if op == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1', entities_omitted=True))
                elif op == 'index':
                    data = dict(
                        package=dict(
                            receipt_id=SESSION, nonce='', seal='blake3:' + 'a' * 64,
                            semantic=dict(
                                schema='cairn.semantic/17', mode='index', status='READY',
                                scope=req['scope'], purpose='context',
                                destination=dict(name='hosted', allow_local=False),
                                query='sha256:fixture-query', available_tokens=32000,
                                selected=[dict(mandatory=True, record=dict(body='Public frontend rule.'))],
                                omitted={}, index=[],
                            ),
                        ),
                        handles=[], expires_at='2030-01-01T00:00:00Z',
                        credits_remaining=4, bytes_remaining=24000,
                    )
                elif op == 'event-renew':
                    data = dict(notice_delivery, state='leased')
                elif op == 'recall-observation':
                    data = {}
                else:
                    inner.send_error(400)
                    return
                inner.send_response(200)
                inner.end_headers()
                inner.wfile.write(f04.encoded(dict(schema='cairn.response/1', ok=True, status='OK', data=data)))

        class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        srv = UnixServer(str(sock), Handler)
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), thr.join()))

        # Register session via SessionStart first so .agent and state file exist with real process identity.
        fx.event.update(hook_event_name='SessionStart', prompt='', host_pid=os.getpid())
        fx.raw = f04.encoded(fx.event)
        reg = fx.public('coordination')
        self.assertEqual((reg.returncode, reg.stderr), (0, b''), reg.stdout)

        state_files = list(Path(fx.coord['state_dir']).glob('*.json'))
        self.assertEqual(len(state_files), 1)
        sp = state_files[0]
        saved = json.loads(sp.read_text())
        saved['delivered_since_idle'] = True
        saved['seen_codex_turns'] = [TURN_1]
        saved['idle_wake'] = dict(
            transport='codex-queue', status='queued', queued_submission_id=QUEUED_ID,
            native_id=SESSION, delivery_id=DELIVERY,
            session=dict(agent_id=AGENT, execution_id=EXEC),
        )
        coord.write_state(sp, saved)

        fx.replies({
            'session-inbox-claim': [dict(ok=True, status='OK', claim_identity=True, data=dict(
                attempt=dict(native_turn_id=TURN_2, delivery=notice_delivery)))],
            'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE', message='active', exit=4)],
            'event-renew': [dict(ok=True, status='OK', data=notice_delivery)],
        })
        fx.event.update(hook_event_name='UserPromptSubmit', turn_id=TURN_2, prompt=fx.wake(), host_pid=os.getpid())
        fx.raw = f04.encoded(fx.event)
        res = fx.public('coordination')
        self.assertEqual((res.returncode, res.stderr), (0, b''), res.stdout)
        out_text = json.loads(res.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('Cairn notice from agent/coordinator.', out_text)
        self.assertIn('Acknowledgement command:', out_text)
        self.assertIn('Public frontend rule.', out_text)
        self.assertNotIn('idle_wake', json.loads(sp.read_text()))

        # Unclaimed bound wake through the public Go frontend exits 2 and records unclaimed_bound_wake.
        saved2 = json.loads(sp.read_text())
        saved2.pop('inbox_intent', None)
        saved2.pop('inbox_attempt', None)
        saved2['idle_wake'] = dict(
            transport='codex-queue', status='queued', queued_submission_id='q-2',
            native_id=SESSION, delivery_id=NEXT_DELIVERY,
            session=dict(agent_id=AGENT, execution_id=EXEC),
        )
        coord.write_state(sp, saved2)
        fx.replies({'session-inbox-claim': [dict(ok=True, status='OK', data={})]})
        fx.event.update(turn_id=TURN_3, prompt=self.wake_prompt_for(NEXT_DELIVERY), host_pid=os.getpid())
        fx.raw = f04.encoded(fx.event)
        refused = fx.public('coordination')
        self.assertEqual((refused.returncode, refused.stdout), (2, b''))
        self.assertIn(b'NATIVE_PROMPT_REFUSED', refused.stderr)
        after_refused = json.loads(sp.read_text())
        self.assertNotIn('idle_wake', after_refused)
        self.assertEqual(after_refused['wake_preclaim_refusal']['reason'], 'unclaimed_bound_wake')


if __name__ == '__main__':
    unittest.main()
