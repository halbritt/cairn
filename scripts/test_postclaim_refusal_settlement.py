"""A Claude channel wake refused after claim and before any grant settles explicitly; doubt keeps the hold.

Synthetic only: a real pinned bridge binding and ledger in a temporary directory, the API patched to record
calls. No provider, live session or store is touched.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


bundle = module('postclaim_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
coord = module('postclaim_refusal_coord', ROOT / 'integrations/lifecycle/coordination.py')
SESSION = '11111111-1111-4111-8111-111111111111'
PROMPT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER_PROMPT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
AGENT = '33333333-3333-4333-8333-333333333333'
EXEC = '44444444-4444-4444-8444-444444444444'
DELIVERY = '55555555-5555-4555-8555-555555555555'
ATTEMPT = '66666666-6666-4666-8666-666666666666'
RECORD = '77777777-7777-4777-8777-777777777777'
TURN = 'claude-channel:' + PROMPT
FAIL_SEARCH = '''#!/usr/bin/env python3
import json,pathlib,sys
root=pathlib.Path(__file__).parent
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(dict(args=sys.argv[1:]))+'\\n')
print(json.dumps(dict(ok=False,status='TIMEOUT')));sys.exit(7)
'''


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / '.git').mkdir()
        cli = self.root / 'cairn'
        cli.write_text(FAIL_SEARCH)
        cli.chmod(0o700)
        self.binding = self.root / 'binding.json'
        common = dict(cairn=str(cli), socket=str(self.root / 'api.sock'), token_file=str(self.root / 'token'),
                      repo='fixture', harness='claude', inbox_recall_binding=str(self.binding))
        self.memory_dir = self.root / 'memory'
        self.mc = dict(common, state_dir=str(self.memory_dir), context_bytes=9500, recall_mode='agent_tools', recall_observations=False)
        self.cc = dict(common, state_dir=str(self.root / 'coordination'), binding='fixture', native_delivery=True)
        for name, value in (('memory.json', self.mc), ('coordination.json', self.cc)):
            (self.root / name).write_text(json.dumps(value))
            (self.root / name).chmod(0o600)
        bundle.stage_runtime(ROOT / 'integrations/lifecycle', self.root)

        def ref(path):
            value = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if path.suffix == '.py':
                value['bundle'] = bundle.bundle_descriptor(path.parent)
            return value
        self.binding.write_text(json.dumps(dict(schema='cairn.inbox-recall-binding/2', enabled=True,
            bridge=ref(self.root / 'inbox_recall.py'), engine=ref(self.root / 'memory.py'),
            memory_config=ref(self.root / 'memory.json'), coordination_configs=[ref(self.root / 'coordination.json')])))
        self.binding.chmod(0o600)
        self.memory_dir.mkdir(mode=0o700)
        # As the shared activation decision leaves them before any claim: an initialized, grant-free ledger.
        self.ledger = self.memory_dir / (SESSION + '.inbox-recall.json')
        self.ledger.write_text(json.dumps(dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={},
                                               inbox_activation={TURN: True})))
        self.ledger.with_suffix('.initialized').write_text(json.dumps(dict(schema='cairn.inbox-recall-initialized/1', session=SESSION)))
        self.process = dict(pid=os.getpid(), start=123, boot='fixture')
        session = dict(agent_id=AGENT, execution_id=EXEC)
        self.delivery = dict(delivery_id=DELIVERY, lease_id=ATTEMPT, state='leased', event=dict(
            event_id=RECORD, kind='request', ref=dict(record_id=RECORD, version=1), **{'from': 'agent/sender'}))
        self.state = dict(process=self.process, workspace=str(self.root), agent=dict(session, native_session_id=SESSION,
            display_name='agent-fixture', inbox='agent/' + AGENT, context_revision=1,
            metadata=dict(harness='claude', model='', project=self.root.name, workspace=str(self.root), state='busy',
                          delivery_mode='existing-session')),
            inbox_intent=dict(session=session, request_id=ATTEMPT, delivery_id=DELIVERY, native_turn_id=TURN),
            inbox_attempt=dict(attempt_id=ATTEMPT, session=session, native_turn_id=TURN, delivery=self.delivery),
            inbox_journals=[ATTEMPT], inbox_result_policy=dict(claim_request_id=ATTEMPT, version=1),
            inbox_completion='88888888-8888-4888-8888-888888888888', inbox_response='99999999-9999-4999-8999-999999999990',
            active_prompt=dict(id=TURN), delivered_since_idle=True)
        self.path = coord.state_path(self.cc, SESSION)
        self.save()
        self.ops = []
        self.fail_reconcile = False

    def save(self):
        coord.write_state(self.path, self.state)

    def load(self):
        return json.loads(self.path.read_text())

    def api(self, config, op, request, **kwargs):
        self.ops.append((op, request))
        if op == 'session-inbox-reconcile':
            if request['reason'] == 'delivery_completed':  # As the store: an uncompleted delivery is still active.
                raise coord.CoordinationError('DELIVERY_ACTIVE', 'native delivery has not been explicitly completed')
            if self.fail_reconcile:
                raise coord.CoordinationError('API_UNAVAILABLE', 'fixture outage')
            return dict(attempt_id=request['attempt_id'], finished_at='now', reason=request['reason'])
        if op == 'event-renew':
            return self.delivery
        if op == 'agent-context':
            return self.state['agent']
        raise AssertionError('unexpected API operation ' + op)

    @contextlib.contextmanager
    def patched(self):
        with patch.object(coord, 'call', side_effect=self.api), patch.object(coord, 'recover_inbox'), \
             patch.object(coord, 'flush_inbox_intents', return_value=True), \
             patch.object(coord, 'journal_committed', return_value=False):
            yield

    def refusal(self):
        try:
            raise ValueError('required/source inbox memory operation unavailable') from coord.CoordinationError('TIMEOUT', 'fixture')
        except ValueError as exc:
            return exc

    def settle(self, turn=TURN, **kwargs):
        state = self.load()
        with self.patched(), coord.session_lock(self.path):
            return coord.settle_refused_claude_wake(self.cc, state, self.path, turn, self.refusal(), **kwargs), state

    def write_grant(self, **identity):
        ledger = dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={'a' * 64: dict(
            identity=dict(dict(attempt_id='x', delivery_id='y', native_owner='z'), **identity),
            limit_bytes=9500, output_bytes=100, native_allowance_bytes=9400)}, active='a' * 64, pending_bytes=0)
        (self.memory_dir / (SESSION + '.inbox-recall.json')).write_text(json.dumps(ledger))

    def reconciles(self):
        return [r for op, r in self.ops if op == 'session-inbox-reconcile']


class SettlementTests(Base):
    def test_refusal_preserves_only_allowlisted_cause_diagnostics(self):
        class HookError(Exception):
            pass
        for code, reason, expected in (
                ('TIMEOUT', 'NONZERO_EXIT', {'cause_code': 'TIMEOUT', 'cause_reason': 'NONZERO_EXIT'}),
                ('VERSION_CONFLICT', 'NONZERO_EXIT', {'cause_code': 'VERSION_CONFLICT', 'cause_reason': 'NONZERO_EXIT'}),
                ('private error text', 'private path', {}),
                ({'secret': 'value'}, ['private'], {})):
            with self.subTest(code=code):
                cause = HookError('private payload must not be retained')
                cause.code, cause.reason = code, reason
                error = ValueError('private wrapper text')
                error.__cause__ = cause
                self.write_grant(attempt_id=ATTEMPT)  # Observe only; keep the hold.
                with self.patched(), coord.session_lock(self.path):
                    diagnostic = coord.settle_refused_claude_wake(
                        self.cc, self.load(), self.path, TURN, error)
                self.assertEqual(diagnostic['cause_class'], 'HookError')
                self.assertEqual({k: v for k, v in diagnostic.items()
                                  if k in ('cause_code', 'cause_reason')}, expected)
                self.assertEqual(diagnostic['settlement'], 'held')
                self.assertNotIn('private', json.dumps(diagnostic))
                self.assertEqual(self.reconciles(), [])

    def test_postclaim_refusal_without_grant_settles_as_turn_ended(self):
        diagnostic, state = self.settle()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('turn_ended', 'absent'))
        self.assertEqual((diagnostic['error_class'], diagnostic['cause_class'], diagnostic['phase']), ('ValueError', 'CoordinationError', 'bridge_after_claim'))
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        saved = self.load()
        self.assertFalse(any(k in saved for k in ('inbox_intent', 'inbox_attempt', 'inbox_turn_ended', 'active_prompt')))
        self.assertEqual(saved['wake_refusal_diagnostic']['settlement'], 'turn_ended')
        # Fixed vocabulary only: no message text, prompt or path is retained.
        self.assertNotIn('memory operation', json.dumps(saved['wake_refusal_diagnostic']))

    def test_no_automatic_replay_or_new_claim(self):
        self.settle()
        self.assertEqual({op for op, _ in self.ops}, {'session-inbox-reconcile'})
        self.assertNotIn('idle_wake', self.load())

    def test_pregenerated_command_ids_and_a_claim_only_journal_do_not_block(self):
        target = coord.intent_path(self.cc['state_dir'], ATTEMPT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(dict(schema='cairn.inbox-intent/1', attempt_id=ATTEMPT, delivery_id=DELIVERY)))
        diagnostic, _ = self.settle()
        self.assertEqual(diagnostic['settlement'], 'turn_ended')

    def test_unrelated_journals_are_preserved_without_blocking_exact_refusal(self):
        prior = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        # CAIRN-170: retained prior obligations stranded a later refused wake.
        for pending in (False, True):
            with self.subTest(pending=pending):
                self.setUp()
                self.state['inbox_journals'].insert(0, prior)
                self.save()
                target = coord.intent_path(self.cc['state_dir'], prior)
                if pending:
                    coord.write_state(target, dict(schema='cairn.inbox-intent/1', attempt_id=prior,
                        completion=dict(kind='complete', status='pending', code='TIMEOUT',
                                        argv=[str(self.root / 'cairn')], input='')))
                before = target.read_bytes() if target.exists() else None
                with patch.object(coord, 'call', side_effect=self.api), coord.session_lock(self.path):
                    diagnostic = coord.settle_refused_claude_wake(
                        self.cc, self.load(), self.path, TURN, self.refusal())
                self.assertEqual(diagnostic['settlement'], 'turn_ended')
                self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()],
                                 [(ATTEMPT, 'turn_ended')])
                self.assertEqual(self.load()['inbox_journals'], [prior, ATTEMPT])
                self.assertEqual(target.read_bytes() if target.exists() else None, before)

    def test_missing_or_substituted_ledger_is_unknown_not_absent(self):
        for name, change in (('missing ledger', lambda: self.ledger.unlink()),
                             ('missing marker', lambda: self.ledger.with_suffix('.initialized').unlink()),
                             ('both missing', lambda: (self.ledger.unlink(), self.ledger.with_suffix('.initialized').unlink())),
                             ('symlinked ledger', lambda: (self.ledger.rename(self.root / 'elsewhere.json'),
                                                           self.ledger.symlink_to(self.root / 'elsewhere.json')))):
            with self.subTest(case=name):
                self.setUp()
                change()
                diagnostic, _ = self.settle()
                self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'unknown'), name)
                self.assertEqual(self.reconciles(), [])

    def test_unrelated_grants_do_not_block(self):
        self.write_grant()
        diagnostic, _ = self.settle()
        self.assertEqual(diagnostic['grant'], 'absent')

    def test_any_grant_for_the_attempt_delivery_or_prompt_keeps_the_hold(self):
        for identity in (dict(attempt_id=ATTEMPT), dict(delivery_id=DELIVERY), dict(native_owner=TURN)):
            with self.subTest(identity=identity):
                self.setUp()
                self.write_grant(**identity)
                diagnostic, _ = self.settle()
                self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'present'))
                self.assertEqual(self.reconciles(), [])
                saved = self.load()
                self.assertEqual(saved['inbox_intent']['request_id'], ATTEMPT)
                self.assertNotIn('inbox_turn_ended', saved)

    def test_busy_or_corrupt_ledger_is_unknown_and_keeps_the_hold(self):
        lock = (self.memory_dir / (SESSION + '.lock')).open('a')
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            diagnostic, _ = self.settle()
        finally:
            lock.close()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'unknown'))
        (self.memory_dir / (SESSION + '.inbox-recall.json')).write_text('{not json')
        diagnostic, _ = self.settle()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'unknown'))
        self.assertEqual(self.reconciles(), [])

    def test_joined_owner_prompt_keeps_the_hold_for_its_own_stop(self):
        self.state['active_prompt'] = dict(id=TURN, joined=True)
        self.save()
        diagnostic, _ = self.settle()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'not_checked'))
        self.assertEqual(self.reconciles(), [])

    def test_cross_turn_identity_and_progress_are_refused(self):
        cases = {
            'another prompt': dict(turn='claude-channel:' + OTHER_PROMPT),
            'attempt identity': dict(change=lambda s: s['inbox_attempt'].update(attempt_id='99999999-9999-4999-8999-999999999999')),
            'session identity': dict(change=lambda s: s['inbox_attempt'].update(session=dict(agent_id=AGENT, execution_id=AGENT))),
            'completion journaled': dict(journal=dict(completion=dict(kind='complete', status='pending'))),
            'response journaled': dict(journal=dict(response=dict(kind='respond', status='committed'))),
            'unreadable journal': dict(journal='not json'),
            'ambiguous journal content': dict(journal=dict(result=dict(status='uncertain'))),
            'journal for another attempt': dict(journal=dict(attempt_id='99999999-9999-4999-8999-999999999999')),
            'observed tool call': dict(change=lambda s: s.update(tool_calls=[dict(id='t')])),
            'attempt tools': dict(change=lambda s: s['inbox_attempt'].update(tools=[dict(id='t')])),
            'cancel pending': dict(change=lambda s: s['inbox_attempt'].update(cancel=dict(by='op'))),
            'prompt no longer active': dict(change=lambda s: s.update(active_prompt=dict(id='claude-channel:' + OTHER_PROMPT))),
            'codex harness': dict(config=dict(harness='codex')),
        }
        for name, case in cases.items():
            with self.subTest(case=name):
                self.setUp()
                if 'change' in case:
                    case['change'](self.state)
                    self.save()
                if 'config' in case:
                    self.cc = dict(self.cc, **case['config'])
                if 'journal' in case:
                    target = coord.intent_path(self.cc['state_dir'], ATTEMPT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(case['journal'] if isinstance(case['journal'], str) else json.dumps(
                        dict(dict(schema='cairn.inbox-intent/1', attempt_id=ATTEMPT), **case['journal'])))
                diagnostic, _ = self.settle(turn=case.get('turn', TURN))
                self.assertEqual(diagnostic['settlement'], 'held', name)
                self.assertEqual(self.reconciles(), [], name)


class TurnBoundaryTests(Base):
    def stop_observation(self, prompt):
        return dict(event='Stop', phase='idle', native_turn_id='claude-channel:' + prompt)

    def test_actual_stop_from_another_prompt_mismatches_while_held_and_not_after_settlement(self):
        self.write_grant(attempt_id=ATTEMPT)  # Ambiguous: hold kept.
        self.settle()
        state = self.load()
        with self.patched(), patch.object(coord, 'claude_session_config', side_effect=lambda c, *a: c):
            with self.assertRaises(coord.CoordinationError) as caught:
                coord.inbox_context(self.cc, state, self.path, self.stop_observation(OTHER_PROMPT))
        self.assertEqual(caught.exception.code, 'NATIVE_TURN_MISMATCH')
        self.setUp()
        self.settle()
        state = self.load()
        with self.patched(), patch.object(coord, 'claude_session_config', side_effect=lambda c, *a: c):
            self.assertEqual(coord.inbox_context(self.cc, state, self.path, self.stop_observation(OTHER_PROMPT)), '')

    def test_failed_reconcile_hands_settlement_to_the_watcher_which_never_renews(self):
        self.fail_reconcile = True
        diagnostic, _ = self.settle()
        self.assertEqual(diagnostic['settlement'], 'turn_ended_pending')
        saved = self.load()
        self.assertTrue(saved['inbox_turn_ended'])
        self.fail_reconcile = False
        self.ops.clear()
        with self.patched():
            coord.watch_inbox(self.cc, saved, self.path)
        self.assertNotIn('event-renew', [op for op, _ in self.ops])
        self.assertEqual([r['reason'] for r in self.reconciles()], ['turn_ended'])
        self.assertNotIn('inbox_intent', self.load())

    def test_held_attempt_keeps_existing_renewal(self):
        self.write_grant(attempt_id=ATTEMPT)
        self.settle()
        self.ops.clear()
        with self.patched():
            coord.watch_inbox(self.cc, self.load(), self.path)
        self.assertIn('event-renew', [op for op, _ in self.ops])
        # Unchanged watcher behavior: only its refused completion probe, never a turn_ended settlement.
        self.assertEqual({r['reason'] for r in self.reconciles()}, {'delivery_completed'})
        self.assertEqual(self.load()['inbox_intent']['request_id'], ATTEMPT)


class HandleTests(Base):
    def test_actual_claude_hook_settles_after_required_bridge_search_timeout(self):
        wake = coord.wake_message(dict(session=dict(agent_id=AGENT, execution_id=EXEC), delivery_id=DELIVERY))
        event = dict(hook_event_name='UserPromptSubmit', session_id=SESSION, prompt_id=PROMPT, cwd=str(self.root),
                     prompt=f'<channel source="cairn-events" native_session_id="{SESSION}" agent_id="{AGENT}" '
                            f'execution_id="{EXEC}" delivery_id="{DELIVERY}">\n{wake}\n</channel>')
        self.state.pop('active_prompt')  # The hook's own admission records this prompt.
        self.save()
        with self.patched(), patch.object(coord, 'owner_process', return_value=self.process), \
             patch.object(coord, 'heartbeat', return_value=self.state['agent']), patch.object(coord, 'associate_wake'), \
             patch.object(coord, 'claude_session_config', side_effect=lambda c, *a: c):
            with self.assertRaises(coord.NativePromptRefused):
                coord.handle(self.cc, event, _inbox_resolved=True)
        saved = self.load()
        self.assertEqual(saved['wake_refusal_diagnostic']['settlement'], 'turn_ended', saved.get('wake_refusal_diagnostic'))
        self.assertEqual(saved['wake_refusal_diagnostic']['cause_class'], 'HookError')
        self.assertEqual(saved['wake_refusal_diagnostic']['cause_code'], 'TIMEOUT')
        self.assertEqual(saved['wake_refusal_diagnostic']['cause_reason'], 'NONZERO_EXIT')
        self.assertEqual(saved['wake_refusal_diagnostic']['recall_phase'], 'required_search')
        self.assertEqual(saved['wake_refusal_diagnostic']['recall_operation'], 'search')
        self.assertEqual((saved['wake_refusal_diagnostic']['recall_attempts'],
                          saved['wake_refusal_diagnostic']['recall_max_attempts']), (2, 2))
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()],
                         [(ATTEMPT, 'delivery_completed'), (ATTEMPT, 'turn_ended')])
        calls = [json.loads(line)['args'] for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 2)  # The bridge retries only the same required search intent.
        self.assertTrue(all('search' in args for args in calls))
        self.assertTrue(all('--kind' in args and args[args.index('--kind') + 1] == 'decision' for args in calls))
        self.assertTrue(all('--' in args for args in calls))
        self.assertFalse(any(k in saved for k in ('inbox_intent', 'active_prompt')))
        self.assertNotIn('inbox_attempt', saved)
        self.assertNotIn('inbox_close', saved)
        self.assertNotIn('inbox_completion', saved)
        self.assertNotIn('inbox_response', saved)
        self.assertFalse((self.memory_dir / (SESSION + '.inbox-recall.json')).exists() and json.loads(
            (self.memory_dir / (SESSION + '.inbox-recall.json')).read_text())['grants'])


class PreClaimGrantConflictTests(Base):
    """CAIRN-173: an existing recall grant for the wake's native prompt refuses the wake before any claim."""

    def setUp(self):
        super().setUp()
        for key in ('inbox_intent', 'inbox_attempt', 'inbox_journals', 'inbox_result_policy',
                    'inbox_completion', 'inbox_response', 'active_prompt'):
            self.state.pop(key)
        self.state['delivered_since_idle'] = False
        self.state['idle_wake'] = dict(transport='claude-channel', delivery_id=DELIVERY,
                                       session=dict(agent_id=AGENT, execution_id=EXEC))
        self.save()
        wake = coord.wake_message(dict(session=dict(agent_id=AGENT, execution_id=EXEC), delivery_id=DELIVERY))
        self.event = dict(hook_event_name='UserPromptSubmit', session_id=SESSION, prompt_id=PROMPT, cwd=str(self.root),
                          prompt=f'<channel source="cairn-events" native_session_id="{SESSION}" agent_id="{AGENT}" '
                                 f'execution_id="{EXEC}" delivery_id="{DELIVERY}">\n{wake}\n</channel>')

    def ledger_with(self, identity, required=None):
        grant = dict(identity=identity, limit_bytes=9500, output_bytes=1200, native_allowance_bytes=0)
        if required is not None:
            grant['required'] = required
        ledger = dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={'b' * 64: grant},
                      active='b' * 64, pending_bytes=0)
        self.ledger.write_text(json.dumps(ledger))
        return self.ledger.read_bytes()

    def run_hook(self):
        claims = []

        def claim(config, state, path):  # As the store: the bound intent claims exactly its delivery.
            intent = state.get('inbox_intent')
            if intent and not state.get('inbox_attempt'):
                claims.append(dict(intent))
                state['inbox_attempt'] = dict(attempt_id=intent['request_id'], session=intent['session'],
                                              native_turn_id=intent['native_turn_id'], delivery=self.delivery)
                state['inbox_journals'] = [intent['request_id']]
                coord.write_state(path, state)
        with self.patched(), patch.object(coord, 'owner_process', return_value=self.process), \
             patch.object(coord, 'heartbeat', return_value=self.state['agent']), patch.object(coord, 'associate_wake'), \
             patch.object(coord, 'recover_inbox', side_effect=claim), \
             patch.object(coord, 'claude_session_config', side_effect=lambda c, *a: c):
            with self.assertRaises(coord.NativePromptRefused):
                coord.handle(self.cc, self.event, _inbox_resolved=True)
        return claims

    def assert_refused_before_claim(self, claims, before, grant, kind):
        saved = self.load()
        self.assertEqual(claims, [], 'a delivery was claimed into a conflicting prompt')
        self.assertFalse(any(k in saved for k in ('inbox_intent', 'inbox_attempt', 'idle_wake')))
        self.assertEqual(self.ledger.read_bytes(), before, 'grant ledger changed: deleted, rebound or charged')
        self.assertEqual(self.ops, [], 'no store operation may follow a pre-claim refusal')
        diagnostic = saved['wake_preclaim_refusal']
        self.assertEqual((diagnostic['phase'], diagnostic['reason'], diagnostic['grant'], diagnostic['grant_kind']),
                         ('before_claim', 'ledger_grant', grant, kind))
        self.assertEqual((diagnostic['delivery_id'], diagnostic['native_turn_id']), (DELIVERY, TURN))
        self.assertNotIn('wake_refusal_diagnostic', saved, 'pre-claim refusal is not post-claim delivery evidence')
        self.assertEqual(saved['active_prompt'].get('refused_delivery'), DELIVERY)
        self.assertTrue(saved['active_prompt'].get('joined'))
        calls = self.root / 'calls.jsonl'
        self.assertFalse(calls.exists(), 'pre-claim refusal read memory or source')

    def test_ordinary_prompt_grant_then_bound_channel_wake_for_same_owner_is_refused_before_claim(self):
        # Required status of the ordinary grant stays pending and uncharged; no optional allowance is opened.
        before = self.ledger_with(dict(native_owner=TURN, boundary='ordinary_prompt'),
                                  required=dict(count=1, selection_sha256='c' * 64, pending=True))
        self.assert_refused_before_claim(self.run_hook(), before, 'present', 'ordinary_context')

    def test_genuine_inbox_grant_for_same_owner_is_refused_before_claim(self):
        before = self.ledger_with(dict(attempt_id='99999999-9999-4999-8999-999999999999',
            delivery_id='99999999-9999-4999-8999-999999999998', event_id=RECORD,
            session=dict(agent_id=AGENT, execution_id=EXEC), source=dict(record_id=RECORD, version=1),
            native_owner=TURN, boundary='claude_prompt_delivery'))
        self.assert_refused_before_claim(self.run_hook(), before, 'present', 'inbox_delivery')

    def test_ambiguous_required_only_wake_emission_is_refused_before_claim(self):
        before = self.ledger_with(dict(native_owner=TURN, wake_sha256='d' * 64,
            session=dict(agent_id=AGENT, execution_id=EXEC), boundary='unadmitted_wake'))
        self.assert_refused_before_claim(self.run_hook(), before, 'present', 'unadmitted_wake')

    def test_corrupt_ledger_is_ambiguous_and_refused_before_claim(self):
        self.ledger.write_text('{not json')
        self.assert_refused_before_claim(self.run_hook(), b'{not json', 'unknown', 'unknown')

    def test_unrelated_grant_still_claims_and_required_failure_settles_exactly(self):
        # A grant for another prompt is no conflict: the wake claims its own prompt, and a later
        # required-context failure in the bridge settles that exact attempt without a grant.
        before = self.ledger_with(dict(native_owner='claude-channel:' + OTHER_PROMPT, boundary='ordinary_prompt'))
        claims = self.run_hook()
        saved = self.load()
        self.assertEqual([(c['delivery_id'], c['native_turn_id']) for c in claims], [(DELIVERY, TURN)])
        self.assertNotIn('wake_preclaim_refusal', saved)
        self.assertEqual(self.ledger.read_bytes(), before, 'failed required read opened or charged a grant')
        attempt = claims[0]['request_id']
        self.assertEqual(saved['wake_refusal_diagnostic']['attempt_id'], attempt)
        self.assertEqual((saved['wake_refusal_diagnostic']['settlement'], saved['wake_refusal_diagnostic']['grant']),
                         ('turn_ended', 'absent'))
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles() if r['reason'] == 'turn_ended'],
                         [(attempt, 'turn_ended')])

    def test_held_post_claim_diagnostic_distinguishes_ordinary_context_from_inbox_delivery(self):
        for identity, kind, evidence in (
                (dict(native_owner=TURN, boundary='ordinary_prompt'), 'ordinary_context', 'ordinary_grant_present_manual_review'),
                (dict(attempt_id=ATTEMPT, native_owner=TURN, boundary='claude_prompt_delivery'), 'inbox_delivery',
                 'grant_present_manual_review')):
            with self.subTest(kind=kind):
                Base.setUp(self)
                self.ledger_with(identity)
                diagnostic, _ = self.settle()
                self.assertEqual((diagnostic['settlement'], diagnostic['grant'], diagnostic['grant_kind']),
                                 ('held', 'present', kind))
                self.assertEqual(self.reconciles(), [])
                with patch.object(coord, 'process_alive', return_value=True):
                    obs = coord.held_attempt_refusal_observation(self.cc, self.load())
                self.assertEqual((obs['grant'], obs['grant_kind'], obs['evidence_state']), ('present', kind, evidence))


class OperatorRecoveryTests(Base):
    def recover(self, session=SESSION, attempt=ATTEMPT, turn=TURN):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            return coord.recover_refused_wake(self.cc, session, attempt, turn)

    def hold_with_busy_ledger(self):
        lock = (self.memory_dir / (SESSION + '.lock')).open('a')
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            diagnostic, _ = self.settle()
        finally:
            lock.close()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant'], diagnostic['origin']), ('held', 'unknown', 'hook'))

    def test_host_recorded_hold_settles_later_once_no_grant_is_proven(self):
        self.hold_with_busy_ledger()
        state = self.load()
        state['active_prompt'] = dict(id='claude-channel:' + OTHER_PROMPT)  # A later prompt ran meanwhile.
        coord.write_state(self.path, state)
        result = self.recover()
        self.assertEqual((result['settlement'], result['origin'], result['grant']), ('turn_ended', 'operator_recorded', 'absent'))
        self.assertEqual([r['reason'] for r in self.reconciles()], ['turn_ended'])
        self.assertNotIn('inbox_intent', self.load())

    def test_attempt_stuck_before_the_repair_has_no_supported_recovery(self):
        before = self.path.read_bytes()  # No host-written refusal record exists, as on the live stuck host.
        with self.assertRaises(coord.CoordinationError):
            self.recover()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.reconciles(), [])

    def test_mismatched_or_forged_records_change_nothing(self):
        self.hold_with_busy_ledger()
        before = self.path.read_bytes()
        for name, kwargs in (('other session', dict(session='22222222-2222-4222-8222-222222222222')),
                             ('other attempt', dict(attempt='99999999-9999-4999-8999-999999999999')),
                             ('other prompt', dict(turn='claude-channel:' + OTHER_PROMPT))):
            with self.subTest(case=name):
                with self.assertRaises((coord.CoordinationError, FileNotFoundError)):
                    self.recover(**kwargs)
                self.assertEqual(self.path.read_bytes(), before, name)
        for name, change in (('caller-written origin', dict(origin='operator')), ('already settled', dict(settlement='turn_ended')),
                             ('other phase', dict(phase='activation'))):
            with self.subTest(case=name):
                state = json.loads(before)
                state['wake_refusal_diagnostic'].update(change)
                coord.write_state(self.path, state)
                forged = self.path.read_bytes()
                with self.assertRaises(coord.CoordinationError):
                    self.recover()
                self.assertEqual(self.path.read_bytes(), forged, name)
        self.assertEqual(self.reconciles(), [])

    def test_a_record_cannot_override_a_grant_or_missing_ledger(self):
        self.hold_with_busy_ledger()
        self.write_grant(delivery_id=DELIVERY)
        with self.assertRaises(coord.CoordinationError):
            self.recover()
        self.ledger.unlink()
        with self.assertRaises(coord.CoordinationError):
            self.recover()
        self.assertEqual(self.load()['inbox_intent']['request_id'], ATTEMPT)
        self.assertEqual(self.reconciles(), [])


class NativeRecordRecoveryTests(Base):
    """Settlement of a pre-repair stuck attempt from three pinned native records (synthetic transcript)."""

    def setUp(self):
        super().setUp()
        self.home = self.root / 'claude-home'
        self.cc = dict(self.cc, config_home=str(self.home))
        self.config_path = self.root / 'coordination.json'
        self.config_path.write_text(json.dumps(self.cc))
        manifest = json.loads(self.binding.read_text())  # Re-pin the changed coordination config, as an installer would.
        manifest['coordination_configs'] = [dict(path=str(self.config_path), sha256=hashlib.sha256(self.config_path.read_bytes()).hexdigest())]
        self.binding.write_text(json.dumps(manifest))
        self.state['active_prompt'] = None
        self.state['inbox_attempt']['created_at'] = '2026-10-02T17:12:57.638562-07:00'
        self.state['inbox_attempt']['delivery'] = dict(self.delivery, attempts=1)
        self.save()
        self.transcript = self.home / 'projects' / coord.re.sub(r'[^A-Za-z0-9]', '-', str(self.root)) / (SESSION + '.jsonl')
        self.transcript.parent.mkdir(parents=True)
        self.write_transcript()

    def channel(self, delivery=DELIVERY):
        return (f'<channel source="cairn-events" native_session_id="{SESSION}" agent_id="{AGENT}" execution_id="{EXEC}" delivery_id="{delivery}">\n'
                + coord.wake_message(dict(session=dict(agent_id=AGENT, execution_id=EXEC), delivery_id=delivery)) + '\n</channel>')

    def records(self, *, refusal=None, enqueue=None, dequeue=None):
        command = f'/usr/bin/python3 /opt/coordination.py hook --config {self.config_path}'
        content = ('UserPromptSubmit operation blocked by hook:\n[' + command + ']: ' + coord.NATIVE_REFUSAL_MESSAGE
                   + '\n\n\nOriginal prompt: ' + self.channel())
        first = dict(type='queue-operation', operation='enqueue', timestamp='2026-10-03T00:12:57.360Z', sessionId=SESSION, content=self.channel())
        second = dict(type='queue-operation', operation='dequeue', timestamp='2026-10-03T00:12:57.387Z', sessionId=SESSION)
        third = dict(parentUuid='8d2e83c9-f054-4565-b380-6bbd87bbccb1', isSidechain=False, type='system', subtype='informational',
                     content=content, isMeta=False, timestamp='2026-10-03T00:12:59.893Z', uuid='ff239a94-757c-44c5-b42a-b494ed1c7d7b',
                     level='warning', preventContinuation=True, userType='external', entrypoint='cli', cwd=str(self.root),
                     sessionId=SESSION, version='2.1.285', gitBranch='main', slug='fixture-slug')
        for value, change in ((first, enqueue), (second, dequeue), (third, refusal)):
            if change:
                change(value)
        return [json.dumps(r, separators=(',', ':')).encode() + b'\n' for r in (first, second, third)]

    def write_transcript(self, raws=None, prefix=b'{"type":"user","note":"unrelated earlier line"}\n'):
        raws = raws or self.records()
        self.transcript.write_bytes(prefix + b''.join(raws))
        offset, self.locators = len(prefix), []
        for raw in raws:
            self.locators.append(dict(offset=offset, length=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
            offset += len(raw)

    def recover(self, locators=None, attempt=ATTEMPT):
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            return coord.recover_native_refused_wake(self.cc, str(self.config_path), SESSION, attempt, locators or self.locators)

    def assert_refused(self, name, **kwargs):
        before = self.path.read_bytes()
        with self.assertRaises((coord.CoordinationError, OSError), msg=name):
            self.recover(**kwargs)
        self.assertEqual(self.path.read_bytes(), before, name)
        self.assertEqual(self.reconciles(), [], name)

    def test_pinned_native_records_settle_the_stuck_attempt(self):
        result = self.recover()
        self.assertEqual((result['settlement'], result['origin'], result['grant']), ('turn_ended', 'native_record', 'absent'))
        self.assertEqual(result['native_record']['refusal_uuid'], 'ff239a94-757c-44c5-b42a-b494ed1c7d7b')
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()], [(ATTEMPT, 'turn_ended')])
        self.assertEqual({op for op, _ in self.ops}, {'session-inbox-reconcile'})  # No claim, wake or replay.

    def test_wrong_file_bytes_or_locators_are_refused(self):
        changed = [dict(item) for item in self.locators]
        changed[2]['sha256'] = '0' * 64
        self.assert_refused('changed pin', locators=changed)
        shifted = [dict(item, offset=item['offset'] + 1) for item in self.locators]
        self.assert_refused('shifted locators', locators=shifted)
        gap = [dict(item) for item in self.locators]
        gap[1] = dict(gap[1], offset=gap[1]['offset'] - 1, length=gap[1]['length'] + 1)
        self.assert_refused('not one line', locators=gap)
        self.transcript.write_bytes(self.transcript.read_bytes().replace(b'2026-10-03T00:12:59.893Z', b'2026-10-03T00:12:59.894Z'))
        self.assert_refused('edited file')
        self.write_transcript()
        self.transcript.rename(self.transcript.with_name('elsewhere.jsonl'))
        self.transcript.symlink_to(self.transcript.with_name('elsewhere.jsonl'))
        self.assert_refused('symlinked session file')
        self.transcript.unlink()
        self.assert_refused('missing session file')

    def test_records_for_another_session_delivery_or_binding_are_refused(self):
        cases = {
            'other session': dict(refusal=lambda r: r.update(sessionId='22222222-2222-4222-8222-222222222222')),
            'other delivery wake': dict(enqueue=lambda r: r.update(content=self.channel('99999999-9999-4999-8999-999999999999'))),
            'other binding command': dict(refusal=lambda r: r.update(content=r['content'].replace(str(self.config_path), '/other/binding.json'))),
            'activation-phase refusal': dict(refusal=lambda r: r.update(content=r['content'].replace(
                'bound inbox recall unavailable; no source or memory grant delivered', 'inbox activation decision unavailable'))),
            'joined-wake refusal': dict(refusal=lambda r: r.update(content=r['content'].replace(
                'bound inbox recall unavailable; no source or memory grant delivered', 'joined channel wake rejected; a fresh copy retries when idle'))),
            'continuation not prevented': dict(refusal=lambda r: r.update(preventContinuation=False)),
            'unknown extra key': dict(refusal=lambda r: r.update(extra=1)),
            'unknown timestamp shape': dict(refusal=lambda r: r.update(timestamp='2026-10-03T00:12:59Z')),
            'enqueue after dequeue': dict(enqueue=lambda r: r.update(timestamp='2026-10-03T00:12:57.400Z')),
            'dequeue shape': dict(dequeue=lambda r: r.update(content='x')),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.write_transcript(self.records(**change))
                self.assert_refused(name)

    def test_duplicate_keys_are_unknown_shape(self):
        raws = self.records()
        raws[1] = raws[1].replace(b'"operation":"dequeue"', b'"operation":"dequeue","operation":"dequeue"')
        self.write_transcript(raws)
        self.assert_refused('duplicate key')

    def test_ambiguous_ownership_or_progress_is_refused(self):
        cases = {
            'second attempt': lambda s: s['inbox_attempt']['delivery'].update(attempts=2),
            'claim before enqueue': lambda s: s['inbox_attempt'].update(created_at='2026-10-02T17:12:57.300000-07:00'),
            'claim after refusal': lambda s: s['inbox_attempt'].update(created_at='2026-10-02T17:13:00.000000-07:00'),
            'owning prompt still active': lambda s: s.update(active_prompt=dict(id=TURN)),
            'intent owned by another prompt': lambda s: s['inbox_intent'].update(native_turn_id='claude-channel:' + OTHER_PROMPT),
            'observed tools': lambda s: s.update(tool_calls=[dict(id='t')]),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.setUp()
                change(self.state)
                self.save()
                self.assert_refused(name)
        self.setUp()
        self.assert_refused('other attempt', attempt='99999999-9999-4999-8999-999999999999')

    def test_records_cannot_override_a_grant_missing_ledger_or_journal(self):
        self.write_grant(delivery_id=DELIVERY)
        self.assert_refused('grant present')
        self.setUp()
        self.ledger.unlink()
        self.assert_refused('missing ledger')
        self.setUp()
        target = coord.intent_path(self.cc['state_dir'], ATTEMPT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(dict(schema='cairn.inbox-intent/1', attempt_id=ATTEMPT, completion=dict(status='pending'))))
        self.assert_refused('journaled completion')


class HeldRefusalObservationTests(Base):
    def api(self, config, op, request, **kwargs):
        if op == 'session-delivery-observe':
            self.ops.append((op, request))
            return {}
        return super().api(config, op, request, **kwargs)

    def test_actual_claude_hook_records_allowlisted_recall_phase_and_attempt_counts(self):
        wake = coord.wake_message(dict(session=dict(agent_id=AGENT, execution_id=EXEC), delivery_id=DELIVERY))
        event = dict(hook_event_name='UserPromptSubmit', session_id=SESSION, prompt_id=PROMPT, cwd=str(self.root),
                     prompt=f'<channel source="cairn-events" native_session_id="{SESSION}" agent_id="{AGENT}" '
                            f'execution_id="{EXEC}" delivery_id="{DELIVERY}">\n{wake}\n</channel>')
        self.state.pop('active_prompt')
        self.save()
        with self.patched(), patch.object(coord, 'owner_process', return_value=self.process), \
             patch.object(coord, 'heartbeat', return_value=self.state['agent']), patch.object(coord, 'associate_wake'), \
             patch.object(coord, 'inbox_context', return_value=''), \
             patch.object(coord, 'claude_session_config', side_effect=lambda c, *a: c):
            with self.assertRaises(coord.NativePromptRefused):
                coord.handle(self.cc, event, _inbox_resolved=True)
        diag = self.load()['wake_refusal_diagnostic']
        self.assertEqual(diag['recall_phase'], 'required_search')
        self.assertEqual(diag['recall_operation'], 'search')
        self.assertEqual(diag['recall_attempts'], 2)
        self.assertEqual(diag['recall_max_attempts'], 2)
        self.assertIsInstance(diag['deadline_remaining_ms'], int)

    def test_held_attempt_refusal_observation_and_inspect_cli_expose_exact_diagnosis(self):
        self.write_grant(attempt_id=ATTEMPT)
        cause = coord.CoordinationError('TIMEOUT', 'SECRET message')
        cause.reason = 'NONZERO_EXIT'
        cause.cairn_recall_failure = dict(
            recall_phase='required_search', recall_operation='search',
            recall_attempts=2, recall_max_attempts=2, deadline_remaining_ms=8500,
            raw_stderr='SECRET STDERR')
        err = ValueError('bridge failed')
        err.__cause__ = cause
        with self.patched():
            coord.settle_refused_claude_wake(self.cc, self.state, self.path, TURN, err)
        state = self.load()
        with patch.object(coord, 'process_alive', return_value=True):
            obs = coord.held_attempt_refusal_observation(self.cc, state, now=state['wake_refusal_diagnostic']['at'] + 5)
        self.assertIsNotNone(obs)
        self.assertEqual(obs['schema'], 'cairn.held-attempt-refusal/1')
        self.assertEqual(obs['authority'], 'diagnostic_only')
        self.assertEqual(obs['store_hold'], 'leased')
        self.assertEqual(obs['attempt_id'], ATTEMPT)
        self.assertEqual(obs['delivery_id'], DELIVERY)
        self.assertEqual(obs['native_turn_id'], TURN)
        self.assertEqual(obs['cause_code'], 'TIMEOUT')
        self.assertEqual(obs['cause_reason'], 'NONZERO_EXIT')
        self.assertEqual(obs['recall_phase'], 'required_search')
        self.assertEqual(obs['recall_operation'], 'search')
        self.assertEqual((obs['recall_attempts'], obs['recall_max_attempts']), (2, 2))
        self.assertEqual(obs['deadline_remaining_ms'], 8500)
        self.assertEqual(obs['grant'], 'present')
        self.assertEqual(obs['evidence_state'], 'grant_present_manual_review')
        self.assertEqual(obs['age_seconds'], 5)
        self.assertNotIn('SECRET', json.dumps(obs))
        # report_delivery_observation refreshes held_refusal_observation and clears it once settled.
        with self.patched(), patch.object(coord, 'process_alive', return_value=True):
            coord.report_delivery_observation(self.cc, state, None, {})
            self.assertEqual(state['held_refusal_observation']['attempt_id'], ATTEMPT)
            coord.write_state(self.path, state)
            inspected = coord.inspect_held_refusal(self.cc, SESSION, ATTEMPT)
            self.assertEqual(inspected['attempt_id'], ATTEMPT)
            with self.assertRaises(coord.CoordinationError):
                coord.inspect_held_refusal(self.cc, SESSION, '99999999-9999-4999-8999-999999999999')
            # Stale or mismatched diagnostic is rejected and never carried across attempts.
            state['inbox_attempt']['attempt_id'] = '99999999-9999-4999-8999-999999999999'
            self.assertIsNone(coord.held_attempt_refusal_observation(self.cc, state))
            coord.report_delivery_observation(self.cc, state, None, {})
            self.assertNotIn('held_refusal_observation', state)


if __name__ == '__main__':
    unittest.main()
