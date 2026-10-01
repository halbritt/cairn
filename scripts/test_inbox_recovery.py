"""Native inbox commands survive lost replies and partitions without silent loss."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'integrations/lifecycle/coordination.py'
spec = importlib.util.spec_from_file_location('coordination_recovery', SCRIPT)
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)

# A scripted stand-in for the Cairn CLI. Each invocation appends its argv and
# stdin to the log and answers from the next scripted reply for its operation.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
root = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
body = sys.stdin.read()
operation = argv[-1] if argv[0] == 'agent' else argv[0]
with open(os.path.join(root, 'log.jsonl'), 'a') as log:
    log.write(json.dumps(dict(operation=operation, argv=argv, input=body)) + '\n')
path = os.path.join(root, 'replies.json')
replies = json.load(open(path))
queue = replies.get(operation) or [dict(ok=True, status='OK', data={})]
reply = queue.pop(0) if len(queue) > 1 else queue[0]
json.dump(replies, open(path, 'w'))
if reply.get('hang'):
    print('')
    sys.exit(7)
print(json.dumps(dict(schema='cairn.response/1', **reply)))
sys.exit(0 if reply['ok'] else 4)
'''

AGENT = 'a5d7b51e-6f47-4e5e-9a55-2b0d6b3f2a11'
EXECUTION = 'b8e0c3a2-0d34-4c93-8f43-1f7f3c2b9e22'
ATTEMPT = 'c1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607'
DELIVERY = 'd1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607'
LEASE = 'e1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607'
EVENT = 'f1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607'
RESULT = dict(record_id='9a2b3c4d-5e6f-4a70-8b91-c2d3e4f50617', version=1)
HANDLED = dict(ok=True, status='OK', data=dict(state='handled', result=RESULT))
LOST = dict(ok=False, status='API_CONNECTION_FAILED')


class InboxRecovery(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cairn = self.root / 'cairn'
        self.cairn.write_text(FAKE)
        self.cairn.chmod(0o700)
        self.config = dict(cairn=str(self.cairn), socket=str(self.root / 'api.sock'), token_file=str(self.root / 'token'),
                           state_dir=str(self.root / 'state'), binding='fixture', harness='claude', repo='fixture',
                           native_delivery=True)
        Path(self.config['token_file']).write_text('fixture-token-value')
        self.path = self.root / 'state' / 'session.json'
        session = dict(agent_id=AGENT, execution_id=EXECUTION)
        delivery = dict(delivery_id=DELIVERY, lease_id=LEASE, state='leased',
                        event=dict(event_id=EVENT, kind='request', ref=dict(record_id=RESULT['record_id'], version=1),
                                   **{'from': 'agent/requester'}))
        self.state = dict(agent=dict(session, display_name='agent-1', inbox='agent/' + AGENT,
                                     native_session_id='native-1'),
                          process=dict(pid=os.getpid()),
                          inbox_intent=dict(request_id=ATTEMPT, session=session),
                          inbox_attempt=dict(attempt_id=ATTEMPT, session=session, delivery=delivery))
        coordination.write_state(self.path, self.state)
        self.replies({})

    def replies(self, replies):
        (self.root / 'replies.json').write_text(json.dumps(replies))

    def log(self):
        path = self.root / 'log.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def context(self, with_rendered=False):
        """Write the native context exactly as the hook does, without a live API."""
        self.replies({'event-renew': [dict(ok=True, status='OK', data=self.state['inbox_attempt']['delivery'])],
                      'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')]})
        observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='')
        self.state['delivered_since_idle'] = False
        with unittest.mock.patch.object(coordination, 'recover_inbox'):
            rendered = coordination.inbox_context(self.config, self.state, self.path, observation)
        target = self.root / 'state' / 'inbox' / (ATTEMPT + '.json')
        (self.root / 'log.jsonl').unlink(missing_ok=True)
        context = json.loads(target.read_text())
        return (context, rendered) if with_rendered else context

    def run_context(self, argv, body=''):
        return subprocess.run(argv, input=body, capture_output=True, text=True, timeout=30)

    def journal(self):
        return json.loads(coordination.intent_path(self.config['state_dir'], ATTEMPT).read_text())

    def test_only_requests_cue_memory_after_exact_task_source_read(self):
        for kind in ('request', 'response', 'notice'):
            with self.subTest(kind=kind):
                self.state['inbox_attempt']['delivery']['event']['kind'] = kind
                with unittest.mock.patch.object(coordination, 'call', wraps=coordination.call) as calls:
                    context, rendered = self.context(with_rendered=True)
                self.assertEqual([call.args[1] for call in calls.call_args_list],
                                 ['session-inbox-reconcile', 'event-renew'])
                self.assertEqual(context['read_input'], dict(record_id=RESULT['record_id'], version=1))
                self.assertEqual(context['source'], context['read_input'])
                self.assertEqual(context['delivery_id'], DELIVERY)
                self.assertEqual(context['lease_id'], LEASE)
                self.assertIn('Read its exact selected source', rendered)
                self.assertIn('For a response or notice, read it and run acknowledgement', rendered)
                if kind == 'request':
                    self.assertIn('For a substantive request, after reading that source', rendered)
                    self.assertIn("within the owner's existing authorization", rendered)
                    self.assertIn('search ordinary Cairn memory using the actual assignment', rendered)
                    self.assertIn('pull relevant current notes with their complete pull_arguments', rendered)
                    self.assertIn('check applicability against the task and current source before acting', rendered)
                    self.assertIn('not the wake notification', rendered)
                    self.assertIn('does not grant new authority', rendered)
                    self.assertIn('skip an optional re-pull of the assignment only when both record_id and version '
                                  'match the source you already read in this turn', rendered)
                    self.assertIn('context.source / read_input', rendered)
                    self.assertIn('A newer version may contain a correction; inspect it for relevance', rendered)
                    self.assertIn('Read mandatory selected instructions whole even if they match', rendered)
                    self.assertIn('one semantic rephrase within the existing recall budget', rendered)
                    self.assertIn('discovery.state', rendered)
                    self.assertLess(rendered.index('Read its exact selected source'),
                                    rendered.index('search ordinary Cairn memory'))
                    self.assertLess(rendered.index('search ordinary Cairn memory'),
                                    rendered.index('For a request, handle it'))
                else:
                    self.assertNotIn('search ordinary Cairn memory', rendered)
                    self.assertNotIn('For a substantive request', rendered)
                    self.assertNotIn('pull_arguments', rendered)
                    self.assertNotIn('optional re-pull', rendered)
                    self.assertNotIn('semantic rephrase', rendered)

    def test_context_routes_every_command_through_the_journal(self):
        context = self.context()
        for key in ('completion', 'acknowledgement', 'response'):
            self.assertEqual(context[key][:3], [sys.executable, str(SCRIPT.resolve()), 'inbox'])
        self.assertEqual(context['commands']['complete'][:2], [str(self.cairn), 'complete'])
        self.assertIn('--lease', context['commands']['complete'])
        self.assertEqual(context['request_ids']['completion'], self.state['inbox_completion'])
        self.assertEqual(context['request_ids']['response'], self.state['inbox_response'])

    def test_new_admission_pins_result_scope_before_claim(self):
        attempt = self.state.pop('inbox_attempt')
        self.state.pop('inbox_intent')
        coordination.write_state(self.path, self.state)
        # The subprocess observes the persisted policy before it can answer
        # admission; response identity comes from that same request.
        self.cairn.write_text(FAKE.replace("print(json.dumps(dict(schema='cairn.response/1', **reply)))",
            "if operation == 'session-inbox-claim':\n"
            "    state = json.load(open(os.path.join(root, 'state', 'session.json')))\n"
            "    request = json.loads(body)\n"
            "    assert state['inbox_result_policy'] == dict(claim_request_id=request['request_id'], version=1)\n"
            "    reply['data']['attempt']['attempt_id'] = request['request_id']\n"
            "print(json.dumps(dict(schema='cairn.response/1', **reply)))"))
        self.replies({'session-inbox-claim': [dict(ok=True, status='OK', data=dict(attempt=attempt))],
                      'event-renew': [dict(ok=True, status='OK', data=attempt['delivery'])],
                      'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')]})
        observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='')
        coordination.inbox_context(self.config, self.state, self.path, observation)
        identity = self.state['inbox_attempt']['attempt_id']
        context = json.loads((self.root / 'state' / 'inbox' / (identity + '.json')).read_text())
        command = context['commands']['complete']
        self.assertEqual(command[command.index('--task') + 1], 'coordination-result/' + DELIVERY)
        self.assertEqual(command[command.index('--run') + 1], '*')
        self.assertNotIn('--task', context['commands']['ack'])
        self.assertNotIn('--task', context['commands']['respond'])

    def test_lost_new_claim_recovers_persisted_policy_and_overwrites_stale_marker(self):
        attempt = self.state.pop('inbox_attempt')
        self.state.pop('inbox_intent')
        # This is the state an old watcher can leave after clearing known
        # inbox fields. A genuinely new admission replaces its stale policy.
        self.state['inbox_result_policy'] = dict(claim_request_id=EVENT, version=1)
        self.replies({'session-inbox-claim': [LOST]})
        observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='')
        with self.assertRaises(coordination.CoordinationError):
            coordination.inbox_context(self.config, self.state, self.path, observation)
        recovered = json.loads(self.path.read_text())
        identity = recovered['inbox_intent']['request_id']
        self.assertNotEqual(identity, EVENT)
        self.assertEqual(recovered['inbox_result_policy'], dict(claim_request_id=identity, version=1))
        self.assertNotIn('inbox_attempt', recovered)
        attempt['attempt_id'] = identity
        self.replies({'session-inbox-claim': [dict(ok=True, status='OK', data=dict(attempt=attempt))],
                      'event-renew': [dict(ok=True, status='OK', data=attempt['delivery'])],
                      'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')]})
        coordination.inbox_context(self.config, recovered, self.path, observation)
        claims = [entry for entry in self.log() if entry['operation'] == 'session-inbox-claim']
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], claims[1])
        context = json.loads((self.root / 'state' / 'inbox' / (identity + '.json')).read_text())
        argv = context['commands']['complete']
        self.assertEqual(argv[argv.index('--task') + 1], 'coordination-result/' + DELIVERY)

    def test_existing_and_uncertain_admissions_remain_legacy(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                if uncertain:
                    attempt = self.state.pop('inbox_attempt')
                    self.replies({'session-inbox-claim': [dict(ok=True, status='OK', data=dict(attempt=attempt))]})
                    coordination.recover_inbox(self.config, self.state, self.path)
                context = self.context()
                self.assertNotIn('--task', context['commands']['complete'])
                self.assertNotIn('--run', context['commands']['complete'])
                self.assertNotIn('inbox_result_policy', self.state)

    def test_matching_invalid_policy_refuses_context(self):
        for policy in (None, {}, dict(claim_request_id=ATTEMPT, version=2),
                       dict(claim_request_id=ATTEMPT, version=True),
                       dict(claim_request_id=ATTEMPT, version=1, extra='unknown')):
            with self.subTest(policy=policy):
                self.state['inbox_result_policy'] = policy
                with self.assertRaises(coordination.CoordinationError) as caught:
                    self.context()
                self.assertEqual(caught.exception.code, 'INVALID_HOST')
                self.assertFalse((self.root / 'state' / 'inbox' / (ATTEMPT + '.json')).exists())

    def test_policy_cannot_bind_a_different_attempt_or_session(self):
        self.state['inbox_result_policy'] = dict(claim_request_id=ATTEMPT, version=1)
        for field, value in (('attempt_id', EVENT), ('session', dict(agent_id=AGENT, execution_id=EVENT))):
            with self.subTest(field=field):
                old = self.state['inbox_attempt'][field]
                self.state['inbox_attempt'][field] = value
                with self.assertRaises(coordination.CoordinationError) as caught:
                    self.context()
                self.assertEqual(caught.exception.code, 'INVALID_HOST')
                self.state['inbox_attempt'][field] = old

    def test_old_watcher_cleanup_marker_does_not_scope_another_legacy_admission(self):
        # An old watcher removes only its known inbox fields and can leave the
        # new marker behind. A subsequent old-host claim has another identity.
        self.state['inbox_result_policy'] = dict(claim_request_id=EVENT, version=1)
        context = self.context()
        self.assertNotIn('--task', context['commands']['complete'])
        coordination.clear_inbox(self.state, self.path)
        self.assertNotIn('inbox_result_policy', self.state)

    def test_scoped_journal_survives_lost_reply_and_watcher_replay(self):
        self.state['inbox_result_policy'] = dict(claim_request_id=ATTEMPT, version=1)
        context = self.context()
        self.replies({'complete': [LOST]})
        result = self.run_context(context['completion'], 'Selected scoped result')
        self.assertEqual(json.loads(result.stdout)['status'], 'API_CONNECTION_FAILED')
        original = self.journal()['completion']
        self.assertIn('--task', original['argv'])
        # An older coordinator would regenerate this context without flags.
        # The pending journal must refuse that changed argv without rewriting it.
        command = context['commands']['complete']
        offset = command.index('--task')
        del command[offset:offset + 4]
        target = self.root / 'state' / 'inbox' / (ATTEMPT + '.json')
        target.write_text(json.dumps(context))
        changed = self.run_context(context['completion'], 'Selected scoped result')
        self.assertEqual(json.loads(changed.stdout)['status'], 'IDEMPOTENCY_CONFLICT')
        self.replies({'complete': [HANDLED], 'event-renew': [dict(ok=True, status='OK', data=self.state['inbox_attempt']['delivery'])]})
        coordination.watch_inbox(self.config, self.state, self.path)
        sent = [entry for entry in self.log() if entry['operation'] == 'complete']
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(self.journal()['completion']['status'], 'committed')

    def test_completion_is_durable_before_it_is_sent(self):
        context = self.context()
        # The fake refuses to answer if the journal is not already on disk.
        guard = self.root / 'cairn'
        guard.write_text(FAKE.replace("body = sys.stdin.read()",
            "body = sys.stdin.read()\njournal = os.path.join(root, 'state', 'intents', '" + ATTEMPT + ".json')\n"
            "assert json.load(open(journal))['completion']['status'] == 'pending'"))
        self.replies({'complete': [HANDLED]})
        result = self.run_context(context['completion'], 'Selected result')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['data']['state'], 'handled')
        slot = self.journal()['completion']
        self.assertEqual((slot['status'], slot['result'], slot['input']), ('committed', RESULT, 'Selected result'))
        path = coordination.intent_path(self.config['state_dir'], ATTEMPT)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('fixture-token-value', path.read_text())

    def test_lost_completion_is_replayed_identically_before_reconciliation(self):
        context = self.context()
        self.replies({'complete': [LOST, HANDLED]})
        result = self.run_context(context['completion'], 'Selected result')
        self.assertEqual(json.loads(result.stdout)['status'], 'API_CONNECTION_FAILED')
        self.assertEqual(self.journal()['completion']['status'], 'pending')
        # The turn ends during the partition: the host keeps the hold.
        self.replies({'complete': [LOST], 'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        observation = dict(event='Stop', phase='idle', native_turn_id='')
        self.state['delivered_since_idle'] = True
        with unittest.mock.patch.object(coordination, 'recover_inbox'):
            coordination.inbox_context(self.config, self.state, self.path, observation)
        self.assertTrue(self.state.get('inbox_turn_ended'))
        self.assertIn('inbox_intent', self.state)
        self.assertNotIn('session-inbox-reconcile', [entry['operation'] for entry in self.log()])
        # A later watcher cycle, after the partition heals, replays the exact
        # request and only then reconciles the ended turn.
        self.replies({'complete': [HANDLED], 'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        coordination.watch_inbox(self.config, self.state, self.path)
        sent = [entry for entry in self.log() if entry['operation'] == 'complete']
        self.assertEqual(len({json.dumps(entry['argv']) for entry in sent}), 1)
        self.assertTrue(all(entry['input'] == 'Selected result' for entry in sent))
        operations = [entry['operation'] for entry in self.log()]
        self.assertLess(max(i for i, op in enumerate(operations) if op == 'complete'),
                        operations.index('session-inbox-reconcile'))
        reconcile = json.loads([entry for entry in self.log() if entry['operation'] == 'session-inbox-reconcile'][0]['input'])
        # The journal proves explicit handling, so the ended turn is not reported as unhandled.
        self.assertEqual(reconcile['reason'], 'delivery_completed')
        self.assertNotIn('inbox_intent', self.state)
        self.assertEqual(self.journal()['completion']['status'], 'committed')

    def test_committed_command_replays_through_the_api_and_a_different_body_is_refused(self):
        context = self.context()
        self.replies({'complete': [HANDLED, HANDLED, LOST, dict(ok=False, status='STALE_SESSION')]})
        self.run_context(context['completion'], 'Selected result')
        again = self.run_context(context['completion'], 'Selected result')
        self.assertEqual(json.loads(again.stdout)['data']['result'], RESULT)
        sent = [e for e in self.log() if e['operation'] == 'complete']
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]['argv'], sent[1]['argv'])
        # A local copy of an earlier success never answers for the store.
        cut = self.run_context(context['completion'], 'Selected result')
        self.assertEqual(json.loads(cut.stdout)['status'], 'API_CONNECTION_FAILED')
        restored = self.run_context(context['completion'], 'Selected result')
        self.assertEqual(json.loads(restored.stdout)['status'], 'STALE_SESSION')
        slot = self.journal()['completion']
        self.assertEqual((slot['status'], slot['result'], slot['replay_code']), ('committed', RESULT, 'STALE_SESSION'))
        other = self.run_context(context['completion'], 'A different result')
        self.assertEqual(json.loads(other.stdout)['status'], 'IDEMPOTENCY_CONFLICT')
        ack = self.run_context(context['acknowledgement'])
        self.assertEqual(json.loads(ack.stdout)['status'], 'IDEMPOTENCY_CONFLICT')

    def test_refused_command_keeps_its_identity(self):
        context = self.context()
        self.replies({'complete': [dict(ok=False, status='HOLD_RELEASED')]})
        self.run_context(context['completion'], 'Selected result')
        for argv, body in ((context['completion'], 'A corrected result'), (context['acknowledgement'], '')):
            changed = self.run_context(argv, body)
            self.assertEqual(json.loads(changed.stdout)['status'], 'IDEMPOTENCY_CONFLICT')
        slot = self.journal()['completion']
        self.assertEqual((slot['status'], slot['kind'], slot['input']), ('refused', 'complete', 'Selected result'))
        self.assertEqual(len([e for e in self.log() if e['operation'] in ('complete', 'ack')]), 1)

    def test_definitive_refusal_is_final_and_reconciliation_proceeds(self):
        context = self.context()
        self.replies({'complete': [dict(ok=False, status='HOLD_RELEASED')]})
        self.run_context(context['completion'], 'Selected result')
        slot = self.journal()['completion']
        self.assertEqual((slot['status'], slot['code']), ('refused', 'HOLD_RELEASED'))
        self.assertTrue(coordination.flush_inbox_intents(self.config, self.state))
        self.assertEqual(len([e for e in self.log() if e['operation'] == 'complete']), 1)

    def test_reply_needs_the_committed_result_and_is_replayed_after_completion(self):
        context = self.context()
        self.replies({'complete': [LOST, HANDLED], 'publish': [LOST, dict(ok=True, status='OK', data={})]})
        # No reply before this attempt's completion has committed.
        early = self.run_context([*context['response'], '--version', '1', RESULT['record_id']])
        self.assertEqual(json.loads(early.stdout)['status'], 'INVALID_REQUEST')
        self.run_context(context['completion'], 'Selected result')
        pending = self.run_context([*context['response'], '--version', '1', RESULT['record_id']])
        self.assertEqual(json.loads(pending.stdout)['status'], 'INVALID_REQUEST')
        self.run_context(context['completion'], 'Selected result')
        missing = self.run_context(context['response'])
        self.assertEqual(json.loads(missing.stdout)['status'], 'INVALID_REQUEST')
        for version, record in (('2', RESULT['record_id']), ('1', '0a2b3c4d-5e6f-4a70-8b91-c2d3e4f50617')):
            other = self.run_context([*context['response'], '--version', version, record])
            self.assertEqual(json.loads(other.stdout)['status'], 'INVALID_REQUEST')
        self.assertNotIn('publish', [e['operation'] for e in self.log()])
        self.run_context([*context['response'], '--version', '1', RESULT['record_id']])
        self.assertEqual(self.journal()['response']['status'], 'pending')
        self.assertTrue(coordination.flush_inbox_intents(self.config, self.state))
        published = [e['argv'] for e in self.log() if e['operation'] == 'publish']
        self.assertEqual(len(published), 2)
        self.assertEqual(published[0], published[1])
        self.assertEqual(published[0][-3:], ['--version', '1', RESULT['record_id']])

    def released_with_pending_reply(self):
        """Root's live sequence: the host commits the completion and reconciles the
        attempt before the still-running agent journals its reply, which then
        cannot reach the API from the agent's sandbox."""
        context = self.context()
        self.replies({'complete': [LOST]})
        self.run_context(context['completion'], 'Selected result')
        self.replies({'complete': [HANDLED], 'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertNotIn('inbox_intent', self.state)
        self.assertIn(ATTEMPT, self.state['inbox_journals'])
        self.replies({'publish': [LOST]})
        self.run_context([*context['response'], '--version', '1', RESULT['record_id']])
        self.assertEqual(self.journal()['response']['status'], 'pending')
        (self.root / 'log.jsonl').unlink()
        return context

    def assert_reply_replayed(self, context):
        published = [e['argv'] for e in self.log() if e['operation'] == 'publish']
        self.assertEqual(len(published), 1)
        argv = published[0]
        # The exact journaled request: original execution, original request UUID.
        self.assertEqual(argv[argv.index('--execution-id') + 1], EXECUTION)
        self.assertEqual(argv[argv.index('--request-id') + 1], context['request_ids']['response'])
        self.assertEqual(argv[-3:], ['--version', '1', RESULT['record_id']])
        self.assertEqual(self.journal()['response']['status'], 'committed')
        operations = [e['operation'] for e in self.log()]
        self.assertNotIn('session-inbox-claim', operations)
        self.assertNotIn('session-inbox-reconcile', operations)
        self.assertNotIn(ATTEMPT, self.state['inbox_journals'])

    def test_reply_journaled_after_reconciliation_is_replayed_by_the_watcher(self):
        context = self.released_with_pending_reply()
        self.replies({'publish': [dict(ok=True, status='OK', data={})]})
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assert_reply_replayed(context)
        self.assertNotIn(ATTEMPT, json.loads(self.path.read_text())['inbox_journals'])

    def test_reply_journaled_after_reconciliation_is_replayed_at_stop(self):
        context = self.released_with_pending_reply()
        self.replies({'publish': [dict(ok=True, status='OK', data={})]})
        self.state['delivered_since_idle'] = True
        with unittest.mock.patch.object(coordination, 'recover_inbox'):
            coordination.inbox_context(self.config, self.state, self.path,
                                       dict(event='Stop', phase='idle', native_turn_id=''))
        self.assert_reply_replayed(context)

    def test_reply_journal_survives_a_watcher_restart_and_older_state(self):
        context = self.released_with_pending_reply()
        # A restarted watcher reads only the persisted state and journal files.
        self.state = json.loads(self.path.read_text())
        self.assertIn(ATTEMPT, self.state['inbox_journals'])
        # A state written before the list existed seeds it from its journals,
        # including a journal whose session was only named in its argv.
        self.state.pop('inbox_journals')
        target = coordination.intent_path(self.config['state_dir'], ATTEMPT)
        journal = json.loads(target.read_text())
        journal.pop('session')
        target.write_text(json.dumps(journal))
        self.replies({'publish': [LOST, dict(ok=True, status='OK', data={})]})
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(self.state['inbox_journals'], [ATTEMPT])
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(len([e for e in self.log() if e['operation'] == 'publish']), 2)
        self.assertEqual(self.journal()['response']['status'], 'committed')
        self.assertEqual(self.state['inbox_journals'], [])

    def legacy_reconciled_completion(self, kind='request', context_present=True):
        """agent-203's ordering: a journal from before kind/session were recorded,
        its completion committed, the attempt reconciled, and no reply yet."""
        context = self.context()
        if kind != 'request':
            target = self.root / 'state' / 'inbox' / (ATTEMPT + '.json')
            target.write_text(json.dumps(dict(context, kind=kind)))
        self.replies({'complete': [HANDLED]})
        self.run_context(context['completion'], 'Selected result')
        path = coordination.intent_path(self.config['state_dir'], ATTEMPT)
        journal = json.loads(path.read_text())
        journal.pop('kind')
        journal.pop('session')
        path.write_text(json.dumps(journal))
        if not context_present:
            (self.root / 'state' / 'inbox' / (ATTEMPT + '.json')).rename(self.root / 'context.json')
        for key in ('inbox_journals', 'inbox_intent', 'inbox_attempt'):
            self.state.pop(key, None)
        coordination.write_state(self.path, self.state)
        return context

    def test_legacy_committed_request_waits_for_its_reply(self):
        context = self.legacy_reconciled_completion()
        # The watcher scans before the agent has journaled its reply.
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(self.state['inbox_journals'], [ATTEMPT])
        self.replies({'publish': [LOST]})
        lost = self.run_context([*context['response'], '--version', '1', RESULT['record_id']])
        self.assertEqual(json.loads(lost.stdout)['status'], 'API_CONNECTION_FAILED')
        self.replies({'publish': [dict(ok=True, status='OK', data={})]})
        coordination.watch_inbox(self.config, self.state, self.path)
        published = [e['argv'] for e in self.log() if e['operation'] == 'publish']
        self.assertEqual(len(published), 2)
        self.assertEqual(published[0], published[1])
        self.assertEqual(self.journal()['response']['status'], 'committed')
        self.assertEqual(self.state['inbox_journals'], [])

    def test_legacy_committed_journal_of_unknown_kind_is_kept(self):
        self.legacy_reconciled_completion(context_present=False)
        coordination.watch_inbox(self.config, self.state, self.path)
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(self.state['inbox_journals'], [ATTEMPT])

    def test_legacy_notice_journal_is_final(self):
        self.legacy_reconciled_completion(kind='notice')
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(self.state['inbox_journals'], [])

    def test_leave_waits_for_a_reply_journaled_after_reconciliation(self):
        self.released_with_pending_reply()
        self.replies({'publish': [LOST]})
        with self.assertRaises(coordination.CoordinationError) as caught:
            coordination.finish_presence(self.config, self.state, self.path)
        self.assertEqual(caught.exception.code, 'INTENT_PENDING')
        self.assertNotIn('agent-leave', [e['operation'] for e in self.log()])
        self.replies({'publish': [dict(ok=True, status='OK', data={})], 'agent-leave': [dict(ok=True, status='OK', data={})]})
        coordination.finish_presence(self.config, self.state, self.path)
        operations = [e['operation'] for e in self.log()]
        self.assertLess(len(operations) - 1 - operations[::-1].index('publish'), operations.index('agent-leave'))
        self.assertTrue(self.state['retired'])

    def test_journals_of_other_conversations_are_not_replayed(self):
        other = coordination.intent_path(self.config['state_dir'], 'b1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607')
        coordination.write_state(other, dict(schema='cairn.inbox-intent/1', attempt_id=other.stem, kind='request',
            session=dict(agent_id='0a5d7b51-6f47-4e5e-9a55-2b0d6b3f2a11', execution_id=EXECUTION),
            completion=dict(kind='complete', argv=[str(self.cairn), 'complete'], input='', status='pending')))
        self.state.pop('inbox_journals', None)
        self.replies({'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')],
                      'event-renew': [dict(ok=True, status='OK', data=self.state['inbox_attempt']['delivery'])]})
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual(self.state['inbox_journals'], [ATTEMPT])
        self.assertNotIn('complete', [e['operation'] for e in self.log()])

    def test_leave_waits_for_an_uncertain_command(self):
        context = self.context()
        self.replies({'complete': [LOST]})
        self.run_context(context['completion'], 'Selected result')
        with self.assertRaises(coordination.CoordinationError) as caught:
            coordination.finish_presence(self.config, self.state, self.path)
        self.assertEqual(caught.exception.code, 'INTENT_PENDING')
        self.assertNotIn('agent-leave', [e['operation'] for e in self.log()])
        self.assertTrue(json.loads(self.path.read_text())['ending'])
        self.replies({'complete': [HANDLED], 'agent-leave': [dict(ok=True, status='OK', data={})],
                      'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        coordination.finish_presence(self.config, self.state, self.path)
        operations = [e['operation'] for e in self.log()]
        self.assertLess(operations.index('complete', 1), operations.index('agent-leave'))
        self.assertTrue(self.state['retired'])

    def test_possibly_committed_reconciliation_keeps_its_reason_and_uuid(self):
        self.replies({'session-inbox-reconcile': [LOST]})
        with self.assertRaises(coordination.CoordinationError):
            coordination.release_inbox(self.config, self.state, self.path, 'turn_ended', fenced=True)
        saved = dict(self.state['inbox_close'])
        # The committed turn_ended would refuse a different reason with
        # VERSION_CONFLICT; the watcher must retry the saved request instead.
        self.replies({'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        self.assertTrue(coordination.release_inbox(self.config, self.state, self.path, 'delivery_completed', fenced=True))
        sent = [json.loads(e['input']) for e in self.log() if e['operation'] == 'session-inbox-reconcile']
        self.assertEqual(sent[-1], saved)
        self.assertNotIn('inbox_intent', self.state)

    def test_active_delivery_refusal_frees_the_reason(self):
        self.replies({'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')]})
        self.assertFalse(coordination.release_inbox(self.config, self.state, self.path, 'delivery_completed', fenced=True))
        self.assertNotIn('inbox_close', self.state)
        self.replies({'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        self.assertTrue(coordination.release_inbox(self.config, self.state, self.path, 'turn_ended', fenced=True))
        sent = [json.loads(e['input']) for e in self.log() if e['operation'] == 'session-inbox-reconcile']
        self.assertEqual([r['reason'] for r in sent], ['delivery_completed', 'turn_ended'])
        self.assertNotEqual(sent[0]['request_id'], sent[1]['request_id'])

    def test_lapsed_lease_keeps_the_hold_without_raising(self):
        self.replies({'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')],
                      'event-renew': [dict(ok=False, status='STALE_LEASE')]})
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertTrue(self.state['inbox_attempt']['lease_lapsed'])
        coordination.watch_inbox(self.config, self.state, self.path)
        self.assertEqual([e['operation'] for e in self.log()].count('event-renew'), 1)
        self.assertIn('inbox_intent', json.loads(self.path.read_text()))


if __name__ == '__main__':
    unittest.main()
