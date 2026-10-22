"""CAIRN-180: an Agy subagent conversation runs inside its parent's process and pane. It is
reported as having no wake transport, never as a missing terminal target."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agy_coordination', ROOT / 'integrations/lifecycle/coordination.py')
coord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coord)

PARENT, CHILD = '99a67178-57bb-4026-8da2-02163f7a34af', '35bd18bc-858b-4dd2-b451-6d06ceee14e1'
DELIVERY = '5a08ef40-adfe-4498-97f1-cdc8da18a74b'


class AgySubagent(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = dict(harness='agy', binding='agy-one', repo='/fixture', cairn='/fixture/cairn',
                           socket='/fixture/api.sock', token_file='/fixture/token', state_dir=str(self.root / 'state'),
                           native_delivery=True, idle_wakeup='/fixture/herdr', process_names=['agy'])
        self.process = coord.process_reference(os.getpid())
        self.states = {}
        for ordinal, native in ((454, PARENT), (455, CHILD)):
            state = dict(process=self.process, workspace=str(self.root), agent=dict(
                agent_id=f'{ordinal:08d}-0000-4000-8000-000000000000', execution_id='e', native_session_id=native,
                display_name=f'agent-{ordinal}', metadata=dict(state='idle', delivery_mode='existing-session',
                                                               workspace=str(self.root))))
            path = coord.state_path(self.config, native)
            coord.write_state(path, state)
            self.states[native] = (path, state)
        self.panes = [dict(agent='agy', agent_session=dict(agent='agy', kind='id', value=PARENT),
                           agent_status='idle', focused=False, pane_id='w36:p1')]

    def prepare(self, native):
        path, state = self.states[native]
        identifies = lambda config, st, host, env: host['agent_session']['value'] == st['agent']['native_session_id']
        with patch.object(coord, 'call', return_value=dict(delivery_id=DELIVERY)), \
             patch.object(coord, 'coordination_excluded', return_value=False), \
             patch.object(coord, 'herdr_environment', return_value={'HERDR_SOCKET_PATH': 'x'}), \
             patch.object(coord, 'herdr_call', return_value=dict(agents=self.panes)), \
             patch.object(coord, 'host_identifies_session', side_effect=identifies), \
             contextlib.redirect_stderr(io.StringIO()):
            return coord.prepare_idle_wake(self.config, state, path, {}), state

    def test_subagent_is_reported_as_having_no_wake_transport(self):
        prepared, state = self.prepare(CHILD)
        self.assertIsNone(prepared)
        self.assertEqual(state['wake_refusal']['condition'], 'native_transport_unavailable')
        self.assertIn('Agy subagent conversation inside agent-454', state['wake_refusal']['reason'])
        self.assertNotIn('idle_wake', state)

    def test_unrelated_missing_pane_keeps_terminal_target_missing(self):
        self.panes = []
        prepared, state = self.prepare(CHILD)
        self.assertEqual(state['wake_refusal']['condition'], 'terminal_target_missing')


if __name__ == '__main__':
    unittest.main()
