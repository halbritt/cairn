"""The tool-boundary inbox cue reads local watcher state and never claims work."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coordination", ROOT / "integrations/lifecycle/coordination.py")
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)

AGENT = dict(agent_id='11111111-1111-4111-8111-111111111111', execution_id='22222222-2222-4222-8222-222222222222',
             metadata=dict(state='busy', delivery_mode='existing-session'))


class InboxCue(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = dict(harness='claude', binding='account-one', repo='fixture', cairn='/absent/cairn',
                           socket='/absent/api.sock', token_file='/absent/token', state_dir=str(self.root / 'state'),
                           native_delivery=True, process_names=[])
        self.process = coordination.process_reference(os.getppid())
        self.event = dict(hook_event_name='PostToolUse', session_id='native-one', cwd=str(self.root),
                          tool_name='Bash', host_pid=os.getppid())
        self.path = coordination.state_path(self.config, 'native-one')

    def write(self, pending, **extra):
        state = dict(schema='cairn.native-session/1', process=self.process, agent=AGENT, **extra)
        if pending is not None:
            state['inbox_pending'] = dict(dict(requests=0, notices=0, responses=0, truncated=False, latest_position=0,
                                               execution_id=AGENT['execution_id'], observed_at=time.time()), **pending)
        coordination.write_state(self.path, state)

    def cue(self):
        with patch.object(coordination, 'call', side_effect=AssertionError('the cue must not call the API')):
            return coordination.handle(self.config, self.event)

    def test_cue_names_counts_once_per_new_arrival(self):
        self.write(dict(requests=1, notices=6, latest_position=1500))
        text = self.cue()['hookSpecificOutput']['additionalContext']
        self.assertIn('1 request, 6 notices waiting', text)
        self.assertIn('do not claim the inbox manually', text)
        self.assertEqual(self.cue(), {}, 'an unchanged backlog must not repeat after every tool')
        self.write(dict(requests=1, notices=6, latest_position=1501))
        self.assertIn('1 request, 6 notices', self.cue()['hookSpecificOutput']['additionalContext'])

    def test_no_cue_without_fresh_matching_waiting_work(self):
        cases = dict(
            absent=None,
            empty=dict(),
            stale=dict(requests=1, observed_at=time.time() - coordination.INBOX_PENDING_FRESH - 1),
            other_execution=dict(requests=1, execution_id='33333333-3333-4333-8333-333333333333'),
        )
        for name, pending in cases.items():
            with self.subTest(name):
                self.path.with_suffix('.cue').unlink(missing_ok=True)
                self.write(pending)
                self.assertEqual(self.cue(), {})
        self.write(dict(requests=1), retired=True)
        self.assertEqual(self.cue(), {})
        self.assertEqual(coordination.handle(dict(self.config, native_delivery=False), self.event), {})
        self.assertEqual(coordination.handle(dict(self.config, harness='opencode'), self.event), {})
        with patch.dict(os.environ, CAIRN_LIFECYCLE_CHILD='1'):
            self.assertEqual(self.cue(), {})

    def test_other_live_process_state_gets_no_cue(self):
        self.write(dict(requests=2, latest_position=9))
        other = dict(self.process, start=self.process['start'] + 1)
        state = json.loads(self.path.read_text())
        state['process'] = other
        coordination.write_state(self.path, state)
        self.assertEqual(self.cue(), {})

    def test_excluded_workspace_does_not_emit_or_consume_arrival(self):
        workspaces = {name: self.root / name / 'child' for name in ('current', 'recorded', 'metadata')}
        for workspace in workspaces.values():
            workspace.mkdir(parents=True)
        self.event['cwd'] = str(workspaces['current'])
        agent = dict(AGENT, metadata=dict(AGENT['metadata'], workspace=str(workspaces['metadata'])))
        marker = self.path.with_suffix('.cue')
        for name, workspace in workspaces.items():
            for ancestor in (False, True):
                for exclusion in ('.cairn-no-coordination', '.cairn-no-memory'):
                    with self.subTest(workspace=name, ancestor=ancestor, exclusion=exclusion):
                        marker.unlink(missing_ok=True)
                        self.write(dict(requests=1, latest_position=42), workspace=str(workspaces['recorded']))
                        state = json.loads(self.path.read_text())
                        state['agent'] = agent
                        coordination.write_state(self.path, state)
                        before = self.path.read_bytes()
                        opt_out = (workspace.parent if ancestor else workspace) / exclusion
                        opt_out.touch()
                        try:
                            self.assertEqual(self.cue(), {})
                            self.assertFalse(marker.exists(), 'excluded workspace must not consume its arrival cue')
                            self.assertEqual(self.path.read_bytes(), before)
                        finally:
                            opt_out.unlink()
                        self.assertIn('1 request', self.cue()['hookSpecificOutput']['additionalContext'])

    def test_truncated_counts_are_marked(self):
        self.write(dict(requests=100, truncated=True, latest_position=3))
        self.assertIn('100+ requests', self.cue()['hookSpecificOutput']['additionalContext'])

    def test_unassociated_wake_context_cannot_emit_or_consume_arrival(self):
        self.write(dict(requests=1, latest_position=42))
        before = self.path.read_bytes()
        context = self.root / 'wake.json'
        context.write_text(json.dumps(dict(schema='cairn.wake-context/0')))
        with patch.dict(os.environ, CAIRN_WAKE_CONTEXT=str(context)):
            self.assertEqual(self.cue(), {})
        self.assertFalse(self.path.with_suffix('.cue').exists())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('1 request', self.cue()['hookSpecificOutput']['additionalContext'])

    def test_watcher_refreshes_busy_counts_and_clears_idle(self):
        state = dict(agent=AGENT)
        reply = dict(requests=1, notices=2, responses=0, latest_position=42)
        with patch.object(coordination, 'call', return_value=reply) as call:
            coordination.refresh_inbox_pending(self.config, state)
        call.assert_called_once_with(self.config, 'session-inbox-pending',
                                     dict(agent_id=AGENT['agent_id'], execution_id=AGENT['execution_id']), timeout=1)
        self.assertEqual((state['inbox_pending']['requests'], state['inbox_pending']['notices'],
                          state['inbox_pending']['latest_position']), (1, 2, 42))
        idle = dict(agent=dict(AGENT, metadata=dict(AGENT['metadata'], state='idle')), inbox_pending=state['inbox_pending'])
        with patch.object(coordination, 'call', side_effect=AssertionError('idle sessions are woken, not cued')):
            coordination.refresh_inbox_pending(self.config, idle)
        self.assertNotIn('inbox_pending', idle)

    def test_watcher_drops_counts_from_older_api_or_malformed_reply(self):
        for effect in (coordination.CoordinationError('INVALID_REQUEST', 'unknown operation'), None):
            with self.subTest(effect=effect):
                state = dict(agent=AGENT, inbox_pending=dict(requests=5))
                kwargs = dict(side_effect=effect) if effect else dict(return_value=dict(requests='many'))
                with patch.object(coordination, 'call', **kwargs):
                    coordination.refresh_inbox_pending(self.config, state)
                self.assertNotIn('inbox_pending', state)


if __name__ == '__main__':
    unittest.main()
