"""CAIRN-180: a finished Agy subagent conversation leaves presence.

Agy runs subagents as further conversations inside the parent's process, so the
shared process keeps them "online" after they finish. The watcher stops one only
on Agy's own evidence: the parent's subagent registry names it, and either the
parent killed it, or it is idle, its last turn ended, and it has been quiet.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agy_presence_coordination', ROOT / 'integrations/lifecycle/coordination.py')
coord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coord)

PARENT, CHILD = '99a67178-57bb-4026-8da2-02163f7a34af', '35bd18bc-858b-4dd2-b451-6d06ceee14e1'
HOUR = 3600


class AgySubagentPresence(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'agy'
        self.config = dict(harness='agy', binding='agy-one', repo='/fixture', cairn='/fixture/cairn',
                           socket='/fixture/api.sock', token_file='/fixture/token', state_dir=str(self.root / 'state'),
                           native_delivery=True, idle_wakeup='/fixture/herdr', process_names=['agy'],
                           agy_home=str(self.home))
        self.process = coord.process_reference(os.getpid())
        self.now = time.time()
        self.states = {}
        for ordinal, native in ((454, PARENT), (455, CHILD)):
            self.save(native, self.session(ordinal, native))
        self.registry('SUBAGENT_STATE_ALIVE')
        self.transcript(dict(source='MODEL', type='PLANNER_RESPONSE', status='DONE'), age=2 * HOUR)

    def session(self, ordinal, native, process=None, state='idle'):
        return dict(schema='cairn.native-session/1', process=process or self.process, workspace=str(self.root),
                    inbox_journals=[], agent=dict(
                        agent_id=f'{ordinal:08d}-0000-4000-8000-000000000000', execution_id=f'{ordinal}-exec',
                        native_session_id=native, display_name=f'agent-{ordinal}',
                        metadata=dict(state=state, delivery_mode='existing-session', workspace=str(self.root))))

    def save(self, native, state):
        path = coord.state_path(self.config, native)
        coord.write_state(path, state)
        self.states[native] = path

    def load(self, native):
        return json.loads(self.states[native].read_text())

    def registry(self, value, parent=PARENT, child=CHILD):
        folder = self.home / 'brain' / parent / '.system_generated' / 'subagents'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{child}.json').write_text(json.dumps(dict(
            conversationId=child, subagentDescriptor=dict(typeName='self', role='PCB & Fab Engineer'),
            state=value, spawnStepIndex=253)))

    def transcript(self, last, age):
        logs = self.home / 'brain' / CHILD / '.system_generated' / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        created = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.now - age))
        lines = [dict(step_index=1, source='USER_EXPLICIT', type='USER_INPUT', status='DONE', created_at=created),
                 dict(dict(step_index=2, created_at=created), **last)]
        path = logs / 'transcript.jsonl'
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        os.utime(path, (self.now - age, self.now - age))

    def finished(self, native=CHILD):
        return coord.agy_subagent_finished(self.config, self.load(native), self.states[native], now=self.now)

    def test_idle_quiet_subagent_with_ended_turn_is_finished(self):
        evidence = self.finished()
        self.assertEqual(evidence['parent']['display_name'], 'agent-454')
        self.assertEqual(evidence['registry_state'], 'SUBAGENT_STATE_ALIVE')

    def test_killed_subagent_is_finished_without_quiet_period(self):
        self.registry('SUBAGENT_STATE_KILLED')
        self.transcript(dict(source='MODEL', type='RUN_COMMAND', status='RUNNING'), age=10)
        self.assertEqual(self.finished()['registry_state'], 'SUBAGENT_STATE_KILLED')

    def test_busy_subagent_is_not_finished(self):
        self.save(CHILD, self.session(455, CHILD, state='busy'))
        self.assertIsNone(self.finished())

    def test_recent_turn_is_not_finished(self):
        self.transcript(dict(source='MODEL', type='PLANNER_RESPONSE', status='DONE'), age=HOUR - 60)
        self.assertIsNone(self.finished())

    def test_turn_not_ended_is_not_finished(self):
        self.transcript(dict(source='MODEL', type='RUN_COMMAND', status='RUNNING'), age=2 * HOUR)
        self.assertIsNone(self.finished())

    def test_undelivered_message_keeps_subagent(self):
        folder = self.home / 'brain' / CHILD / '.system_generated' / 'messages' / 'undelivered'
        folder.mkdir(parents=True)
        (folder / 'm.json').write_text('{}')
        self.assertIsNone(self.finished())

    def test_missing_transcript_is_not_finished(self):
        (self.home / 'brain' / CHILD / '.system_generated' / 'logs' / 'transcript.jsonl').unlink()
        self.assertIsNone(self.finished())

    def test_unknown_registry_state_is_not_finished(self):
        self.registry('SUBAGENT_STATE_UNSPECIFIED')
        self.assertIsNone(self.finished())

    def test_registry_naming_another_conversation_is_not_finished(self):
        path = self.home / 'brain' / PARENT / '.system_generated' / 'subagents' / f'{CHILD}.json'
        path.write_text(json.dumps(dict(conversationId=PARENT, state='SUBAGENT_STATE_KILLED')))
        self.assertIsNone(self.finished())

    def test_top_level_conversation_without_registry_is_not_finished(self):
        self.assertIsNone(self.finished(PARENT))
        (self.home / 'brain' / PARENT / '.system_generated' / 'subagents' / f'{CHILD}.json').unlink()
        self.assertIsNone(self.finished())

    def test_parent_in_another_process_is_not_evidence(self):
        other = dict(self.process, pid=self.process['pid'] + 1)
        self.save(PARENT, self.session(454, PARENT, process=other))
        self.assertIsNone(self.finished())

    def test_retired_parent_is_not_evidence(self):
        parent = self.load(PARENT)
        parent['retired'] = True
        self.save(PARENT, parent)
        self.assertIsNone(self.finished())

    def test_inbox_attempt_keeps_subagent(self):
        state = self.load(CHILD)
        state['inbox_intent'] = dict(request_id='r', session={})
        self.save(CHILD, state)
        self.assertIsNone(self.finished())

    def test_other_harness_is_never_finished(self):
        self.config['harness'] = 'codex'
        self.assertIsNone(self.finished())

    def watch(self):
        calls = []

        def call(config, operation, payload, timeout=None):
            calls.append((operation, payload))
            if operation == 'agent-heartbeat':
                return self.load(PARENT)['agent'] if payload['agent_id'].startswith('00000454') else self.load(CHILD)['agent']
            return {}
        with patch.object(coord, 'call', side_effect=call), \
             patch.object(coord, 'time', wraps=time) as clock, \
             patch.object(coord, 'watch_inbox', return_value=None), \
             patch.object(coord, 'refresh_inbox_pending', return_value=None), \
             patch.object(coord, 'prepare_idle_wake', return_value=None), \
             patch.object(coord, 'report_delivery_observation', return_value=None), \
             contextlib.redirect_stderr(io.StringIO()):
            clock.time.return_value = self.now
            coord.watch_once(self.config)
        return calls

    def test_watcher_stops_finished_subagent_through_agent_leave(self):
        calls = self.watch()
        child = self.load(CHILD)
        self.assertTrue(child['retired'])
        self.assertEqual(child['ending_reason'], 'agy_subagent_finished')
        self.assertEqual(child['ending_evidence']['parent_native_session_id'], PARENT)
        leaves = [payload for operation, payload in calls if operation == 'agent-leave']
        self.assertEqual([payload['agent_id'] for payload in leaves], [self.load(CHILD)['agent']['agent_id']])
        self.assertFalse(self.load(PARENT).get('retired'))
        self.assertIn('agent-heartbeat', [operation for operation, _ in calls])

    def test_watcher_keeps_running_subagent(self):
        self.save(CHILD, self.session(455, CHILD, state='busy'))
        calls = self.watch()
        self.assertFalse(self.load(CHILD).get('retired'))
        self.assertNotIn('agent-leave', [operation for operation, _ in calls])


if __name__ == '__main__':
    unittest.main()
