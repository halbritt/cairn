"""Pre-admission vetoes retain evidence without completing or replaying inbox work."""
import contextlib
import fcntl
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_claude_inbox_recall as claude_fixture
from test_inbox_recall_bridge import coord, SESSION, AGENT, EXEC, DELIVERY


class WakeRefusalTests(unittest.TestCase):
    freeze_base = claude_fixture.ClaudeInboxRecallTests.freeze_base
    freeze = claude_fixture.ClaudeInboxRecallTests.freeze
    # Reuse only the disposable fixture, not the parent's test cases.
    def setUp(self):
        claude_fixture.ClaudeInboxRecallTests.setUp(self)
        self.process = coord.process_reference(os.getpid())
        self.state['process'] = self.process
        self.state.pop('inbox_intent')
        self.state.pop('inbox_attempt')
        self.state['agent']['metadata']['state'] = 'idle'
        self.wake = dict(delivery_id=DELIVERY, request_id=EXEC, event_id=AGENT,
            session=dict(agent_id=AGENT, execution_id=EXEC), transport='claude-channel',
            parent=self.process, native_id=SESSION, status='submitted', attempted_at=1.0)
        self.state['idle_wake'] = copy.deepcopy(self.wake)
        self.freeze()
        self.path = coord.state_path(self.cc, SESSION)

    def api(self, config, operation, request, **kwargs):
        if operation == 'event-inspect':
            return dict(event=dict(event_id=AGENT, repo=self.cc['repo']), deliveries=[dict(
                delivery_id=DELIVERY, consumer=self.state['agent']['inbox'], state='pending', attempts=0)])
        self.assertEqual(operation, 'session-inbox-ready')
        return dict(delivery_id=DELIVERY, event_id=AGENT)

    def main_refused(self):
        out, err = io.StringIO(), io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(self.event).encode()))
        with patch.object(coord, 'effective_inbox_config', side_effect=BlockingIOError('PRIVATE PATH SECRET')), \
             patch.object(coord, 'owner_process', return_value=self.process), \
             patch.object(coord, 'call', side_effect=self.api) as api, \
             patch.object(coord, 'handle', side_effect=AssertionError('must not admit')), \
             patch.object(sys, 'argv', ['coordination.py', 'hook', '--config', str(self.root/'coordination.json')]), \
             patch.object(sys, 'stdin', stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = coord.main()
        return code, out.getvalue(), err.getvalue(), api.call_args_list

    def test_public_veto_records_exact_receipt_and_reconciles_without_admission(self):
        result = self.main_refused()
        self.assertEqual(result[0:2], (2, ''))
        self.assertNotIn('PRIVATE', result[2])
        self.assertIn('phase=activation error_class=BlockingIOError', result[2])
        state = json.loads(self.path.read_text())
        self.assertNotIn('idle_wake', state)
        receipt = json.loads((self.path.parent/'wake-refusals'/ (state['wake_rejection'] + '.json')).read_text())
        self.assertEqual(receipt['wake'], self.wake)
        self.assertEqual(receipt['native_prompt_id'], self.event['prompt_id'])
        self.assertEqual(receipt['phase'], 'activation')
        self.assertEqual(receipt['error_class'], 'BlockingIOError')
        self.assertEqual([c.args[1] for c in result[3]], ['event-inspect', 'session-inbox-ready'])
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())

    def test_existing_work_or_uncertain_wake_is_not_reset(self):
        for field in ('inbox_intent', 'inbox_attempt', 'inbox_result_policy', 'inbox_journals', 'inbox_close', 'active_prompt'):
            with self.subTest(field=field):
                self.state[field] = {'pending': True}
                coord.write_state(self.path, self.state)
                result = self.main_refused()
                current = json.loads(self.path.read_text())
                self.assertEqual(current['idle_wake'], self.wake)
                self.assertEqual(current[field], self.state[field])
                self.assertEqual(result[3], [])
                self.state.pop(field)
        self.state['idle_wake']['status'] = 'uncertain'
        coord.write_state(self.path, self.state)
        with coord.session_lock(self.path), patch.object(coord, 'call') as api:
            self.assertFalse(coord.reconcile_rejected_wake(self.cc, self.state, self.path))
            api.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), self.state)

    def test_exact_envelope_and_owner_required(self):
        original = self.event['prompt']
        for text in ('quoted: ' + original, original + ' owner instructions', original.replace(AGENT, EXEC, 1)):
            with self.subTest(text=text[:15]):
                self.event['prompt'] = text
                self.main_refused()
                self.assertEqual(json.loads(self.path.read_text()), self.state)
        self.event['prompt'] = original
        self.state['process']['start'] += 1
        coord.write_state(self.path, self.state)
        self.main_refused()
        self.assertEqual(json.loads(self.path.read_text()), self.state)

    def test_api_failure_keeps_durable_receipt_and_marker(self):
        with patch.object(coord, 'reconcile_rejected_wake', side_effect=coord.CoordinationError('API_UNAVAILABLE', 'SECRET')):
            result = self.main_refused()
        self.assertEqual(result[0:2], (2, ''))
        self.assertNotIn('SECRET', result[2])
        state = json.loads(self.path.read_text())
        self.assertEqual(state['idle_wake'], self.wake)
        self.assertTrue((self.path.parent/'wake-refusals'/(state['wake_rejection']+'.json')).exists())
        with coord.session_lock(self.path), patch.object(coord, 'call', side_effect=lambda c,op,r,**kw: self.api(c,op,r,**kw) if op == 'event-inspect' else {'delivery_id': EXEC}):
            self.assertFalse(coord.reconcile_rejected_wake(self.cc, state, self.path))
        self.assertEqual(json.loads(self.path.read_text()), state)
        with coord.session_lock(self.path), patch.object(coord, 'call', side_effect=self.api):
            self.assertTrue(coord.reconcile_rejected_wake(self.cc, state, self.path))
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))

    def test_grant_and_memory_lock_preserved(self):
        directory = self.root/'memory'; directory.mkdir(exist_ok=True)
        ledger = directory/f'{SESSION}.inbox-recall.json'
        identity = {'delivery_id': DELIVERY}
        data = dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={
            'a'*64: dict(identity=identity, limit_bytes=9500, output_bytes=1000, native_allowance_bytes=8500)})
        ledger.write_text(json.dumps(data)); before = ledger.read_bytes()
        self.main_refused()
        self.assertEqual(json.loads(self.path.read_text())['idle_wake'], self.wake)
        self.assertEqual(ledger.read_bytes(), before)
        ledger.unlink()
        with (directory/f'{SESSION}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.main_refused()
        self.assertEqual(result[0], 2)
        self.assertEqual(json.loads(self.path.read_text())['idle_wake'], self.wake)
        self.assertFalse(ledger.exists())

    def test_changed_marker_and_receipt_drift_refuse(self):
        with patch.object(coord, 'reconcile_rejected_wake', return_value=False):
            self.main_refused()
        state = json.loads(self.path.read_text())
        state['idle_wake']['request_id'] = AGENT
        coord.write_state(self.path, state)
        with coord.session_lock(self.path), patch.object(coord, 'call') as api:
            self.assertFalse(coord.reconcile_rejected_wake(self.cc, state, self.path))
            api.assert_not_called()
        target = self.path.parent/'wake-refusals'/(state['wake_rejection']+'.json')
        record = json.loads(target.read_text()); record['wake'] = state['idle_wake']
        target.write_text(json.dumps(record))
        with coord.session_lock(self.path), self.assertRaises(ValueError):
            coord.reconcile_rejected_wake(self.cc, state, self.path)

    def test_receipt_and_clear_write_failure_leave_submitted_marker(self):
        original = coord.write_state
        for fail_clear in (False, True):
            coord.write_state(self.path, self.state)
            def save(path, value):
                if (fail_clear and path == self.path and 'idle_wake' not in value) or (not fail_clear and path.parent.name == 'wake-refusals'):
                    raise OSError('PRIVATE durable failure')
                original(path, value)
            with patch.object(coord, 'write_state', side_effect=save):
                result = self.main_refused()
            self.assertEqual(result[0:2], (2, ''))
            self.assertNotIn('PRIVATE', result[2])
            self.assertEqual(json.loads(self.path.read_text())['idle_wake'], self.wake)

    def test_exact_veto_before_transport_ack_cannot_resurrect_wake(self):
        self.state['idle_wake']['status'] = 'uncertain'
        self.state['idle_wake'].update(endpoint='fixture.sock', bridge=self.process)
        coord.write_state(self.path, self.state)
        wake = copy.deepcopy(self.state['idle_wake'])
        def send(*args):
            self.assertEqual(self.main_refused()[0], 2)
            self.assertNotIn('idle_wake', json.loads(self.path.read_text()))
        from types import SimpleNamespace
        channel = SimpleNamespace(write=send, ChannelUnavailable=RuntimeError, ChannelError=RuntimeError)
        with patch.object(coord, 'channel_helper', return_value=channel):
            coord.submit_idle_wake(self.cc, self.path, dict(wake=wake, process=self.process))
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))

    def test_receipt_accepts_only_late_transport_ack_and_failed_clear_keeps_caller(self):
        self.state['idle_wake']['status'] = 'uncertain'
        coord.write_state(self.path, self.state)
        with patch.object(coord, 'reconcile_rejected_wake', return_value=False):
            self.main_refused()
        state = json.loads(self.path.read_text())
        state['idle_wake']['status'] = 'submitted'
        coord.write_state(self.path, state)
        before = copy.deepcopy(state)
        with coord.session_lock(self.path), patch.object(coord, 'call', side_effect=self.api), \
             patch.object(coord, 'write_state', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                coord.reconcile_rejected_wake(self.cc, state, self.path)
        self.assertEqual(state, before)
        self.assertEqual(json.loads(self.path.read_text()), before)
        with coord.session_lock(self.path), patch.object(coord, 'call', side_effect=self.api):
            self.assertTrue(coord.reconcile_rejected_wake(self.cc, state, self.path))

    def test_missing_event_or_nonzero_attempts_never_clear(self):
        for state_value, attempts in [('leased', 1), ('pending', 1), ('pending', None), ('pending', False)]:
            with self.subTest(state=state_value, attempts=attempts):
                coord.write_state(self.path, self.state)
                original = self.api
                def checked(c, op, r, **kw):
                    result = original(c, op, r, **kw)
                    if op == 'event-inspect':
                        result['deliveries'][0].update(state=state_value, attempts=attempts)
                    return result
                with patch.object(self, 'api', side_effect=checked):
                    self.main_refused()
                self.assertEqual(json.loads(self.path.read_text())['idle_wake'], self.wake)
        self.state['idle_wake'].pop('event_id')
        coord.write_state(self.path, self.state)
        result = self.main_refused()
        self.assertEqual(result[3], [])
        self.assertEqual(json.loads(self.path.read_text())['idle_wake'], self.state['idle_wake'])
        self.assertIn('wake_rejection', json.loads(self.path.read_text()))

    def test_unindexed_legacy_journal_and_unreadable_receipt_are_not_empty(self):
        directory = self.path.parent/'intents'; directory.mkdir()
        (directory/'legacy.json').write_text(json.dumps(dict(session=dict(agent_id=AGENT), attempt_id=EXEC)))
        result = self.main_refused()
        self.assertEqual(result[3], [])
        state = json.loads(self.path.read_text())
        self.assertEqual(state['idle_wake'], self.wake)
        (directory/'legacy.json').unlink()
        target = self.path.parent/'wake-refusals'/(state['wake_rejection']+'.json')
        target.chmod(0o644)
        with coord.session_lock(self.path), self.assertRaises(ValueError):
            coord.reconcile_rejected_wake(self.cc, state, self.path)
        self.assertEqual(json.loads(self.path.read_text()), state)

    def test_future_wake_records_only_valid_authenticated_event_identity(self):
        self.cc['idle_wakeup'] = 'fixture'
        channel = dict(socket='fixture.sock', process=self.process, parent=self.process)
        for event_id in (AGENT, None, 'not-a-uuid'):
            with self.subTest(event_id=event_id):
                state = copy.deepcopy(self.state); state.pop('idle_wake')
                response = dict(delivery_id=DELIVERY)
                if event_id is not None:
                    response['event_id'] = event_id
                with patch.object(coord, 'claude_channel_admission', side_effect=lambda c,n,p: (c,None)), \
                     patch.object(coord, 'call', return_value=response), \
                     patch.object(coord, 'codex_queue_endpoint', return_value=None), \
                     patch.object(coord, 'claude_channel_endpoint', return_value=channel):
                    prepared = coord.prepare_idle_wake(self.cc, state, self.path)
                self.assertIsNotNone(prepared)
                self.assertEqual(prepared['wake'].get('event_id'), AGENT if event_id == AGENT else None)

if __name__ == '__main__':
    unittest.main()
