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

    def context(self):
        """Write the native context exactly as the hook does, without a live API."""
        self.replies({'event-renew': [dict(ok=True, status='OK', data=self.state['inbox_attempt']['delivery'])],
                      'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')]})
        observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='')
        self.state['delivered_since_idle'] = False
        with unittest.mock.patch.object(coordination, 'recover_inbox'):
            coordination.inbox_context(self.config, self.state, self.path, observation)
        target = self.root / 'state' / 'inbox' / (ATTEMPT + '.json')
        (self.root / 'log.jsonl').unlink(missing_ok=True)
        return json.loads(target.read_text())

    def run_context(self, argv, body=''):
        return subprocess.run(argv, input=body, capture_output=True, text=True, timeout=30)

    def journal(self):
        return json.loads(coordination.intent_path(self.config['state_dir'], ATTEMPT).read_text())

    def test_context_routes_every_command_through_the_journal(self):
        context = self.context()
        for key in ('completion', 'acknowledgement', 'response'):
            self.assertEqual(context[key][:3], [sys.executable, str(SCRIPT.resolve()), 'inbox'])
        self.assertEqual(context['commands']['complete'][:2], [str(self.cairn), 'complete'])
        self.assertIn('--lease', context['commands']['complete'])
        self.assertEqual(context['request_ids']['completion'], self.state['inbox_completion'])
        self.assertEqual(context['request_ids']['response'], self.state['inbox_response'])

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
