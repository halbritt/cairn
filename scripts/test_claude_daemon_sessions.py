"""CAIRN-178: daemon-managed Claude background sessions bind their conversation worker, never
the daemon; they claim nothing at SessionStart and get an explicit no-wake reason. A start
claim already stranded on the daemon has an explicit, evidence-checked settlement."""
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
spec = importlib.util.spec_from_file_location('daemon_coordination', ROOT / 'integrations/lifecycle/coordination.py')
coord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coord)

VERSIONS = str(Path.home() / '.local/share/claude/versions')
NATIVE = 'd3bd854f-19f6-4e50-a609-f05d4e2b72db'
AGENT, EXEC = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
ATTEMPT, DELIVERY = '33333333-3333-4333-8333-333333333333', '44444444-4444-4444-8444-444444444444'
BOOT = 'fixture-boot'


def proc(pid, parent, comm, exe, argv, start=None):
    return dict(ref=dict(pid=pid, start=start or pid * 10, state='S', parent=parent, boot=BOOT),
                comm=comm, exe=exe, argv=argv)


def daemon_tree(transcript='/x/' + NATIVE + '.jsonl'):
    exe = VERSIONS + '/2.1.308'
    return {
        10: proc(10, 1, 'claude', exe, ['/home/u/.local/bin/claude', 'daemon', 'run', '--json-path', 'd.json']),
        20: proc(20, 10, '2.1.308', exe, ['claude', 'bg-pty-host', '--bg-pty-host', 's.sock', '--', exe, '--resume', transcript]),
        21: proc(21, 10, '2.1.308', exe, ['claude', 'bg-spare', '--bg-spare', 'c.sock']),
        30: proc(30, 20, '2.1.308', exe, [exe, '--resume', transcript, '--model', 'fable']),
        40: proc(40, 30, 'sh', '/usr/bin/dash', ['/bin/sh', '-c', 'hook']),
    }


class Tree:
    def __init__(self, tree, ppid):
        self.tree, self.ppid = tree, ppid

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(coord, 'proc_view', side_effect=lambda pid: dict(self.tree[pid])))
        self.stack.enter_context(patch.object(coord.os, 'getppid', return_value=self.ppid))
        self.stack.enter_context(patch.object(coord, 'proc_pids', side_effect=lambda: sorted(self.tree)))
        self.stack.enter_context(patch.object(coord, 'process_alive', side_effect=lambda ref: any(
            v['ref']['pid'] == ref.get('pid') and v['ref']['start'] == ref.get('start') for v in self.tree.values())))
        return self

    def __exit__(self, *exc):
        self.stack.close()


CONFIG = dict(harness='claude', process_names=['claude'], binding='claude-one')


class OwnerResolution(unittest.TestCase):
    def test_interactive_claude_parent_is_unchanged(self):
        tree = {5: proc(5, 1, 'claude', VERSIONS + '/2.1.308', ['/home/u/.local/bin/claude', '--channels=x']),
                6: proc(6, 5, 'sh', '/usr/bin/dash', ['/bin/sh'])}
        with Tree(tree, 6):
            self.assertEqual(coord.owner_process(CONFIG, {})['pid'], 5)
        with Tree(tree, 5):
            self.assertEqual(coord.owner_process(CONFIG, {})['pid'], 5)

    def test_daemon_worker_binds_the_conversation_process(self):
        with Tree(daemon_tree(), 40):
            owner = coord.owner_process(CONFIG, {})
            self.assertEqual(owner['pid'], 30)
            self.assertTrue(coord.claude_daemon_managed(owner))

    def test_daemon_and_pty_hosts_are_never_owners(self):
        tree = daemon_tree()
        tree[30] = dict(tree[30], exe='/usr/bin/other', comm='other')
        with Tree(tree, 40), self.assertRaises(coord.CoordinationError):
            coord.owner_process(CONFIG, {})

    def test_worker_is_recognized_only_under_its_exact_daemon(self):
        tree = daemon_tree()
        with Tree(tree, 40):
            worker = tree[30]['ref']
            self.assertTrue(coord.daemon_worker_of(worker, tree[10]['ref']))
            self.assertFalse(coord.daemon_worker_of(worker, dict(tree[10]['ref'], start=1)))
            self.assertFalse(coord.daemon_worker_of(tree[21]['ref'], tree[10]['ref']))

    def test_interactive_process_is_not_daemon_managed(self):
        tree = {5: proc(5, 1, 'claude', VERSIONS + '/2.1.308', ['/home/u/.local/bin/claude'])}
        with Tree(tree, 5):
            self.assertFalse(coord.claude_daemon_managed(coord.owner_process(CONFIG, {})))


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = self.root / 'ws'
        self.workspace.mkdir()
        self.config = dict(CONFIG, repo='/fixture', cairn='/fixture/cairn', socket='/fixture/api.sock',
                           token_file='/fixture/token', state_dir=str(self.root / 'state'), native_delivery=True,
                           config_home=str(self.root / 'home'), idle_wakeup='/fixture/herdr')
        self.transcript = coord.claude_transcript_path(self.config, dict(workspace=str(self.workspace),
                                                                          agent=dict(native_session_id=NATIVE)))
        self.transcript.parent.mkdir(parents=True)
        self.tree = daemon_tree(str(self.transcript))
        self.ops = []

    def api(self, config, op, request, **kwargs):
        self.ops.append((op, request))
        if op == 'session-inbox-reconcile':
            return dict(attempt_id=request['attempt_id'], finished_at='now', reason=request['reason'])
        if op == 'session-inbox-ready':
            return dict(delivery_id=DELIVERY)
        raise AssertionError('unexpected ' + op)


class StartClaim(Fixture):
    def state(self, process):
        return dict(process=process, workspace=str(self.workspace), agent=dict(
            agent_id=AGENT, execution_id=EXEC, native_session_id=NATIVE, display_name='agent-508',
            metadata=dict(state='idle', delivery_mode='existing-session', workspace=str(self.workspace))))

    def test_session_start_claims_nothing_in_a_daemon_managed_worker(self):
        with Tree(self.tree, 40):
            state = self.state(coord.owner_process(self.config, {}))
            path = coord.state_path(self.config, NATIVE)
            observation = dict(event='SessionStart', phase='start', native_turn_id=None)
            with patch.object(coord, 'call', side_effect=self.api), patch.object(coord, 'recover_inbox') as recover:
                self.assertEqual(coord.inbox_context(self.config, state, path, observation), '')
                recover.assert_not_called()
            self.assertNotIn('inbox_intent', state)

    def test_prompt_in_a_daemon_managed_worker_still_claims(self):
        with Tree(self.tree, 40):
            state = self.state(coord.owner_process(self.config, {}))
            path = coord.state_path(self.config, NATIVE)
            observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='claude-channel:p')
            with patch.object(coord, 'call', side_effect=self.api), patch.object(coord, 'recover_inbox'):
                coord.inbox_context(self.config, state, path, observation)
            self.assertIn('inbox_intent', state)

    def test_watcher_records_why_a_background_session_gets_no_wake(self):
        with Tree(self.tree, 40):
            state = self.state(coord.owner_process(self.config, {}))
            path = coord.state_path(self.config, NATIVE)
            with patch.object(coord, 'call', side_effect=self.api), \
                 patch.object(coord, 'coordination_excluded', return_value=False), contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(coord.prepare_idle_wake(self.config, state, path, {}))
        self.assertIn('daemon-managed background', state['wake_refusal']['reason'])
        self.assertEqual(state['wake_refusal']['condition'], 'native_transport_unavailable')
        self.assertNotIn('idle_wake', state)


class StrandedStartClaim(Fixture):
    def setUp(self):
        super().setUp()
        session = dict(agent_id=AGENT, execution_id=EXEC)
        daemon = dict(self.tree[10]['ref'])
        self.records = [dict(type='user', timestamp='2026-10-21T17:19:00.000Z'),
                        dict(type='assistant', timestamp='2026-10-21T17:20:09.055Z'),
                        dict(type='system', subtype='turn_duration', timestamp='2026-10-21T17:20:13.549Z'),
                        dict(type='last-prompt'), dict(type='cost-state'), dict(type='ai-title'),
                        dict(type='agent-name'), dict(type='artifact-comment-monitor'),
                        dict(type='artifact-autoreact-ledger')]
        self.write()
        self.state = dict(process=daemon, workspace=str(self.workspace),
            agent=dict(session, native_session_id=NATIVE, display_name='agent-508', inbox='agent/' + AGENT,
                       metadata=dict(state='idle', delivery_mode='existing-session')),
            inbox_intent=dict(request_id=ATTEMPT, session=session),
            inbox_attempt=dict(attempt_id=ATTEMPT, session=session, created_at='2026-10-21T21:55:01.13704-07:00',
                               delivery=dict(delivery_id=DELIVERY, lease_id=ATTEMPT, event=dict(kind='request'))),
            inbox_journals=[ATTEMPT], inbox_result_policy=dict(claim_request_id=ATTEMPT, version=1))
        self.path = coord.state_path(self.config, NATIVE)
        coord.write_state(self.path, self.state)

    def write(self):
        self.transcript.write_text(''.join(json.dumps(r) + '\n' for r in self.records))

    def recover(self, check_only=False):
        with Tree(self.tree, 1), patch.object(coord, 'call', side_effect=self.api), \
             contextlib.redirect_stderr(io.StringIO()):
            return coord.recover_daemon_start_claim(self.config, NATIVE, ATTEMPT, check_only=check_only)

    def assert_refused(self):
        before = self.path.read_bytes()
        with self.assertRaises(coord.CoordinationError):
            self.recover()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])

    def test_check_only_then_settle_turn_ended_and_rebind_worker(self):
        before = self.path.read_bytes()
        result = self.recover(check_only=True)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['worker']['pid'], 30)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ops, [])
        result = self.recover()
        self.assertEqual(result['settlement'], 'turn_ended')
        self.assertEqual([(r['attempt_id'], r['reason']) for op, r in self.ops if op == 'session-inbox-reconcile'],
                         [(ATTEMPT, 'turn_ended')])
        state = json.loads(self.path.read_text())
        self.assertNotIn('inbox_attempt', state)
        self.assertEqual(state['process']['pid'], 30)
        self.assertEqual(state['daemon_start_claim']['attempt_id'], ATTEMPT)

    def test_any_turn_after_the_claim_refuses(self):
        for record in (dict(type='user', timestamp='2026-10-22T05:00:00.000Z'),
                       dict(type='queue-operation', operation='enqueue', timestamp='2026-10-22T05:00:00.000Z'),
                       dict(type='attachment'), dict(type='unknown-kind')):
            with self.subTest(record=record['type']):
                self.records.append(record)
                self.write()
                self.assert_refused()
                self.records.pop()
                self.write()

    def test_turn_bound_or_journaled_attempt_refuses(self):
        self.state['inbox_attempt']['native_turn_id'] = 'claude-channel:p'
        coord.write_state(self.path, self.state)
        self.assert_refused()
        self.state['inbox_attempt'].pop('native_turn_id')
        coord.write_state(self.path, self.state)
        with patch.object(coord, 'attempt_commands_absent', return_value=False):
            self.assert_refused()

    def test_state_not_bound_to_a_daemon_or_missing_worker_refuses(self):
        self.tree.pop(30)
        self.assert_refused()
        self.tree = daemon_tree(str(self.transcript))
        self.state['process'] = dict(self.tree[30]['ref'])
        coord.write_state(self.path, self.state)
        self.assert_refused()


if __name__ == '__main__':
    unittest.main()
