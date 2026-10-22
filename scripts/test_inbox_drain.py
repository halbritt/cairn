"""`coordination.py inbox drain`: an agent drains its OWN inbox, one item per call (CAIRN-183)."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drain_coordination', ROOT / 'integrations/lifecycle/coordination.py')
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)

AGENT, EXEC = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
DELIVERY, LEASE, EVENT = ('33333333-3333-4333-8333-333333333333', '44444444-4444-4444-8444-444444444444',
                          '55555555-5555-4555-8555-555555555555')
SENDER = 'agent/66666666-6666-4666-8666-666666666666'
CORRELATION = '77777777-7777-4777-8777-777777777777'


def delivery(kind='request'):
    return dict(delivery_id=DELIVERY, consumer='agent/' + AGENT, state='leased', lease_id=LEASE, attempts=1,
                event=dict(event_id=EVENT, kind=kind, **{'from': SENDER}, correlation_id=CORRELATION,
                           ref=dict(record_id='88888888-8888-4888-8888-888888888888', version=2)))


class InboxDrain(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = dict(harness='claude', binding='account-one', repo='/fixture/repo', cairn='/fixture/cairn',
                           socket='/fixture/api.sock', token_file='/fixture/token', state_dir=str(self.root / 'state'),
                           native_delivery=True, process_names=[])
        self.config_path = self.root / 'binding.json'
        self.config_path.write_text(json.dumps(self.config))
        self.state = dict(schema='cairn.native-session/1', process=coordination.process_reference(os.getpid()),
                          workspace=str(self.root),
                          agent=dict(agent_id=AGENT, execution_id=EXEC, inbox='agent/' + AGENT, display_name='agent-1',
                                     native_session_id='native-one',
                                     metadata=dict(state='busy', delivery_mode='existing-session')))
        self.path = coordination.state_path(self.config, 'native-one')
        coordination.write_state(self.path, self.state)
        self.calls = []
        self.reply = dict(delivery=delivery())

    def api(self, config, operation, request, timeout=4, session=None):
        self.calls.append((operation, request, session))
        return self.reply

    def drain(self, *extra, agent=AGENT, execution=EXEC):
        out, err = io.StringIO(), io.StringIO()
        argv = ['coordination.py', 'inbox', 'drain', '--config', str(self.config_path),
                '--agent-id', agent, '--execution-id', execution, *extra]
        with patch.object(coordination, 'call', side_effect=self.api), patch.object(sys, 'argv', argv), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = coordination.main()
        return code, out.getvalue(), err.getvalue()

    def test_claims_one_item_for_this_session_and_prints_exact_commands(self):
        code, out, _ = self.drain()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [('event-next', dict(repo='/fixture/repo', lease_seconds=900),
                                       dict(agent_id=AGENT, execution_id=EXEC))])
        result = json.loads(out)
        self.assertEqual(result['schema'], 'cairn.inbox-drain/1')
        self.assertEqual(result['delivery_id'], DELIVERY)
        self.assertEqual(result['kind'], 'request')
        self.assertEqual(result['read'], ['/fixture/cairn', 'agent', '--socket', '/fixture/api.sock',
                                          '--token-file', '/fixture/token', 'history'])
        self.assertEqual(result['read_input'], delivery()['event']['ref'])
        context_path = Path(result['context'])
        self.assertEqual(context_path.stat().st_mode & 0o077, 0)
        context = json.loads(context_path.read_text())
        self.assertEqual(context['schema'], 'cairn.session-inbox/1')
        self.assertEqual(context['delivery_id'], DELIVERY)
        self.assertEqual(context['lease_id'], LEASE)
        session_flags = ['--agent-id', AGENT, '--execution-id', EXEC]
        complete = context['commands']['complete']
        self.assertEqual(complete[:2], ['/fixture/cairn', 'complete'])
        for flag in (*session_flags, '--lease', LEASE, '--stdin', '--shareable'):
            self.assertIn(flag, complete)
        self.assertEqual(complete[-1], DELIVERY)
        respond = context['commands']['respond']
        self.assertEqual(respond[respond.index('--to') + 1], SENDER)
        self.assertEqual(respond[respond.index('--kind') + 1], 'response')
        self.assertEqual(respond[respond.index('--causation-id') + 1], EVENT)
        self.assertEqual(respond[respond.index('--correlation-id') + 1], CORRELATION)
        helper = [sys.executable, str(Path(coordination.__file__).resolve()), 'inbox', '--context', str(context_path)]
        self.assertEqual(result['completion'], helper + ['complete'])
        self.assertEqual(result['acknowledgement'], helper + ['ack'])
        self.assertEqual(result['response'], helper + ['respond'])
        self.assertIn('renew', result)

    def test_notice_has_no_response_command(self):
        self.reply = dict(delivery=delivery('notice'))
        result = json.loads(self.drain()[1])
        self.assertNotIn('response', result)
        self.assertNotIn('respond', json.loads(Path(result['context']).read_text())['commands'])

    def test_empty_inbox_or_native_hold_returns_no_delivery(self):
        self.reply = dict(delivery=None)
        code, out, _ = self.drain()
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)['delivery_id'])
        self.assertFalse((self.root / 'state' / 'inbox').exists())

    def test_active_native_attempt_refuses_before_any_claim(self):
        for field in ('inbox_intent', 'inbox_attempt'):
            with self.subTest(field=field):
                coordination.write_state(self.path, dict(self.state, **{field: {'attempt_id': 'x'}}))
                code, out, _ = self.drain()
                self.assertNotEqual(code, 0)
                self.assertEqual(json.loads(out)['status'], 'NATIVE_ACTIVE')
                self.assertEqual(self.calls, [])

    def test_only_this_bindings_live_session_may_drain(self):
        cases = dict(other_agent=dict(agent='99999999-9999-4999-8999-999999999999'),
                     other_execution=dict(execution='99999999-9999-4999-8999-999999999999'))
        for name, kwargs in cases.items():
            with self.subTest(name):
                code, out, _ = self.drain(**kwargs)
                self.assertNotEqual(code, 0)
                self.assertEqual(json.loads(out)['status'], 'NOT_THIS_SESSION')
        for name, change in dict(retired=dict(retired=True), ending=dict(ending=True),
                                 dead=dict(process=dict(self.state['process'], start=1))).items():
            with self.subTest(name):
                coordination.write_state(self.path, dict(self.state, **change))
                code, out, _ = self.drain()
                self.assertEqual(json.loads(out)['status'], 'NOT_THIS_SESSION')
        self.assertEqual(self.calls, [])

    def test_journaled_completion_then_reply_cites_the_committed_result(self):
        result = json.loads(self.drain()[1])
        sent = []

        def run(argv, input, capture_output, text, timeout):
            sent.append((argv, input))
            data = dict(result=dict(record_id='99999999-9999-4999-8999-999999999999', version=1))
            return type('R', (), dict(returncode=0, stderr='', stdout=json.dumps(dict(ok=True, status='OK', data=data))))()

        for kind, body, extra in (('complete', 'done', []),
                                  ('respond', '', ['--version', '1', '99999999-9999-4999-8999-999999999999'])):
            out = io.StringIO()
            with patch.object(coordination.subprocess, 'run', side_effect=run), \
                 patch.object(sys, 'stdin', io.StringIO(body)), contextlib.redirect_stdout(out), \
                 patch.object(sys, 'argv', ['coordination.py', 'inbox', '--context', result['context'], kind, *extra]):
                self.assertEqual(coordination.main(), 0)
        self.assertEqual(sent[0][1], 'done')
        self.assertEqual(sent[1][0][-3:], ['--version', '1', '99999999-9999-4999-8999-999999999999'])
        journal = json.loads(coordination.intent_path(self.config['state_dir'],
                                                      json.loads(Path(result['context']).read_text())['attempt_id']).read_text())
        self.assertEqual(journal['completion']['status'], 'committed')
        self.assertEqual(journal['response']['status'], 'committed')

    def test_lease_seconds_is_bounded(self):
        code, out, _ = self.drain('--lease-seconds', '4000')
        self.assertNotEqual(code, 0)
        self.assertEqual(self.calls, [])


    def test_cue_names_the_drain_wrapper_for_the_loaded_binding(self):
        pending = dict(requests=1, notices=0, responses=0)
        agent = dict(agent_id=AGENT, execution_id=EXEC)
        with patch.object(coordination, 'HOOK_CONFIG_PATH', str(self.config_path)):
            text = coordination.inbox_cue_text(pending, self.config, agent)
        self.assertIn(f'{coordination.Path(coordination.__file__).resolve()} inbox drain --config {self.config_path} '
                      f'--agent-id {AGENT} --execution-id {EXEC}', text)
        self.assertIn('cairn inbox next --token-file /fixture/token', text)
        with patch.object(coordination, 'HOOK_CONFIG_PATH', None):
            self.assertNotIn('inbox drain', coordination.inbox_cue_text(pending, self.config, agent))

if __name__ == '__main__':
    unittest.main()
