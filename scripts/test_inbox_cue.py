"""The tool-boundary inbox cue looks up new arrivals read-only and never claims work."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coordination", ROOT / "integrations/lifecycle/coordination.py")
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)

AGENT = dict(agent_id='11111111-1111-4111-8111-111111111111', execution_id='22222222-2222-4222-8222-222222222222',
             metadata=dict(state='busy', delivery_mode='existing-session'))
def counts(**values):
    return dict(dict(requests=0, notices=0, responses=0), **values)


REF = dict(agent_id=AGENT['agent_id'], execution_id=AGENT['execution_id'])
UNAVAILABLE = coordination.CoordinationError('API_UNAVAILABLE', 'Cairn lifecycle command is unavailable')


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
        self.marker = self.path.with_suffix('.cue')
        self.calls = []
        self.reply = UNAVAILABLE  # Every successful cue requires an explicit fresh reply.

    def write(self, pending, agent=AGENT, **extra):
        state = dict(schema='cairn.native-session/1', process=self.process, agent=agent, **extra)
        if pending is not None:
            state['inbox_pending'] = dict(counts(requests=0, notices=0, responses=0, truncated=False, latest_position=0,
                                               execution_id=AGENT['execution_id'], observed_at=time.time()), **pending)
        coordination.write_state(self.path, state)

    def lookup(self, config, operation, request, timeout=4, session=None):
        """Disposable stand-in for the authenticated API, recording every request."""
        self.calls.append((operation, request, timeout, session))
        reply = self.reply() if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    def cue(self, reply=None):
        if reply is not None:
            self.reply = reply
        with patch.object(coordination, 'call', side_effect=self.lookup):
            return coordination.handle(self.config, self.event)

    def text(self, result):
        return result['hookSpecificOutput']['additionalContext']

    def test_cue_names_counts_once_per_new_arrival(self):
        self.write(counts(requests=1, notices=6, latest_position=1500))
        text = self.text(self.cue(counts(requests=1, notices=6, responses=0, latest_position=1500)))
        self.assertIn('1 request, 6 notices waiting', text)
        self.assertIn('do not claim the inbox manually', text)
        self.assertEqual(self.cue(), {}, 'an unchanged backlog must not repeat after every tool')
        self.write(counts(requests=1, notices=6, latest_position=1501))
        self.assertIn('1 request, 6 notices', self.text(self.cue(counts(requests=1, notices=6, responses=0, latest_position=1501))))

    def test_arrival_after_the_last_refresh_is_cued_by_a_live_lookup(self):
        reply = counts(requests=2, notices=1, responses=0, latest_position=9)
        # The cached record is absent, stale, empty or an older, already cued backlog.
        cases = dict(never_refreshed=None,
                     stale_refresh=counts(requests=1, latest_position=5, observed_at=time.time() - 300),
                     empty_refresh=dict(),
                     older_backlog=counts(requests=1, latest_position=5))
        for name, cached in cases.items():
            with self.subTest(name):
                self.marker.unlink(missing_ok=True)
                self.calls.clear()
                self.write(cached)
                if name == 'older_backlog':
                    coordination.write_state(self.marker, dict(signature=[AGENT['execution_id'], 5, 1, 0, 0]))
                before = self.path.read_bytes()
                self.assertIn('2 requests, 1 notice waiting', self.text(self.cue(reply)))
                self.assertEqual(self.calls, [('session-inbox-pending', REF, coordination.INBOX_CUE_LOOKUP_TIMEOUT, None)])
                self.assertEqual(self.path.read_bytes(), before, 'the cue must not rewrite presence state')
                self.assertEqual(self.cue(), {}, 'the same arrival must not repeat')

    def test_cue_only_for_new_arrivals_across_lookups(self):
        self.write(None)
        steps = [
            (counts(requests=1, notices=6, latest_position=1500), '1 request, 6 notices'),
            (counts(requests=1, notices=6, latest_position=1500), None),  # unchanged backlog
            (counts(requests=1, notices=5, latest_position=1499), None),  # one notice was handled
            (counts(requests=1, notices=5, latest_position=1499), None),
            (counts(requests=1, notices=5, latest_position=1503), '1 request, 5 notices'),  # newer arrival, level counts
            (counts(requests=1, notices=6, latest_position=1503), '1 request, 6 notices'),  # older item eligible again
            (counts(requests=0, notices=0, latest_position=0), None),  # everything delivered
            (counts(requests=0, notices=0, responses=1, latest_position=2000), '1 response'),
        ]
        for index, (reply, expected) in enumerate(steps):
            with self.subTest(step=index, reply=reply):
                result = self.cue(reply)
                if expected is None:
                    self.assertEqual(result, {})
                else:
                    self.assertIn(expected + ' waiting', self.text(result))
        self.assertEqual(len(self.calls), len(steps))

    def test_truncated_live_counts_cue_a_new_position_without_count_growth(self):
        self.write(None)
        reply = counts(requests=100, truncated=True, latest_position=3)
        self.assertIn('100+ requests', self.text(self.cue(reply)))
        self.assertEqual(self.cue(), {}, 'an unchanged bounded observation must not repeat')
        self.assertIn('100+ requests', self.text(self.cue(counts(requests=100, notices=0, responses=0,
                                                            truncated=True, latest_position=4))))

    def test_shrinking_live_backlog_then_failed_lookup_never_repeats_cached_arrival(self):
        self.write(counts(requests=2, latest_position=9))
        self.assertIn('2 requests', self.text(self.cue(counts(requests=2, notices=0, responses=0, latest_position=9))))
        self.assertEqual(self.cue(counts(requests=1, notices=0, responses=0, latest_position=5)), {})
        marker = self.marker.read_bytes()
        self.assertEqual(self.cue(UNAVAILABLE), {})
        self.assertEqual(self.marker.read_bytes(), marker)
        self.assertIn('2 requests', self.text(self.cue(counts(requests=2, notices=0, responses=0, latest_position=10))))

    def test_incomplete_counts_cannot_replace_observed_arrival_state(self):
        self.write(None)
        self.assertTrue(self.cue(counts(requests=1, notices=0, responses=0, latest_position=7)))
        before = self.marker.read_bytes()
        for reply in ({}, dict(notices=0, responses=0), counts(requests=True),
                      counts(requests=1, latest_position=True), counts(requests=1, notices=0, responses=0,
                                                            latest_position=8, truncated='yes')):
            with self.subTest(reply=reply):
                self.assertEqual(self.cue(lambda reply=reply: reply), {})
                self.assertEqual(self.marker.read_bytes(), before)

    def test_lookup_failure_is_optional_and_leaves_no_trace(self):
        self.write(None)
        before = self.path.read_bytes()
        failures = dict(
            unreachable=UNAVAILABLE,
            refused=coordination.CoordinationError('STALE_SESSION', 'refused'),
            older_api=coordination.CoordinationError('INVALID_REQUEST', 'unknown operation'),
            not_a_mapping=['requests'],
            non_integer=counts(requests='many', latest_position=1),
            negative=counts(requests=-1, latest_position=1),
            other_failure=OSError('socket vanished'),
        )
        for name, failure in failures.items():
            with self.subTest(name):
                self.assertEqual(self.cue(failure if isinstance(failure, Exception) else (lambda f=failure: f)), {})
                self.assertFalse(self.marker.exists())
                self.assertEqual(self.path.read_bytes(), before)

    def test_unreachable_lookup_never_uses_a_watcher_record(self):
        for name, cached, cue in (
                ('fresh', counts(requests=1, latest_position=5), False),
                ('stale', counts(requests=1, latest_position=5, observed_at=time.time() - 91), False),
                ('other_execution', counts(requests=1, latest_position=5,
                                         execution_id='33333333-3333-4333-8333-333333333333'), False)):
            with self.subTest(name):
                self.marker.unlink(missing_ok=True)
                self.write(cached)
                result = self.cue(UNAVAILABLE)
                self.assertEqual(bool(result), cue)
                self.assertEqual(self.marker.exists(), cue)
        # A definite refusal means the earlier record no longer speaks for this session.
        self.marker.unlink(missing_ok=True)
        self.write(counts(requests=1, latest_position=5))
        self.assertEqual(self.cue(coordination.CoordinationError('NOT_FOUND', 'replaced')), {})
        self.assertFalse(self.marker.exists())

    def test_older_cached_record_cannot_move_the_baseline_back(self):
        self.write(counts(requests=1, latest_position=5))
        self.assertIn('2 requests', self.text(self.cue(counts(requests=2, latest_position=9))))
        self.assertEqual(self.cue(), {})
        self.assertEqual(self.cue(UNAVAILABLE), {}, 'an older record is not an arrival')
        self.assertEqual(json.loads(self.marker.read_text())['signature'], [AGENT['execution_id'], 9, 2, 0, 0])
        self.assertEqual(self.cue(counts(requests=2, latest_position=9)), {}, 'the cued arrival must not repeat')

    def test_lookup_is_bounded_inside_the_hook_deadlines(self):
        self.assertLessEqual(coordination.INBOX_CUE_LOOKUP_TIMEOUT, 0.5)  # OpenCode cuts the hook off at 1 second
        script = self.root / 'slow-cairn'
        script.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
        script.chmod(0o700)
        self.config['cairn'] = str(script)
        self.write(None)
        before = self.path.read_bytes()
        started = time.monotonic()
        self.assertEqual(coordination.handle(self.config, self.event), {})
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_only_the_read_only_lookup_is_requested_without_the_session_lock(self):
        self.write(None)
        with coordination.session_lock(self.path):  # The watcher or a hook owns presence.
            self.assertIn('1 request', self.text(self.cue(counts(requests=1, latest_position=2))))
        self.assertEqual([call[0] for call in self.calls], ['session-inbox-pending'])
        self.assertEqual(self.calls[0][1], REF)

    def test_no_cue_without_fresh_matching_waiting_work(self):
        cases = dict(
            absent=None,
            empty=dict(),
            stale=counts(requests=1, observed_at=time.time() - 90 - 1),
            other_execution=counts(requests=1, execution_id='33333333-3333-4333-8333-333333333333'),
        )
        for name, pending in cases.items():
            with self.subTest(name):
                self.marker.unlink(missing_ok=True)
                self.write(pending)
                self.assertEqual(self.cue(), {})
        self.write(counts(requests=1), retired=True)
        self.assertEqual(self.cue(counts(requests=1, latest_position=1)), {})
        self.assertEqual(coordination.handle(dict(self.config, native_delivery=False), self.event), {})
        self.assertEqual(coordination.handle(dict(self.config, harness='opencode'), self.event), {})
        with patch.dict(os.environ, CAIRN_LIFECYCLE_CHILD='1'):
            self.assertEqual(self.cue(), {})

    def test_sessions_native_delivery_would_not_hand_work_to_get_no_lookup(self):
        reply = counts(requests=1, latest_position=2)
        idle = dict(AGENT, metadata=dict(AGENT['metadata'], state='idle'))
        fresh_worker = dict(AGENT, metadata=dict(AGENT['metadata'], delivery_mode='fresh-worker'))
        cases = [dict(agent=idle), dict(agent=fresh_worker), dict(ending=True), dict(retired=True),
                 dict(agent=dict(AGENT, agent_id=''))]
        for case in cases:
            with self.subTest(case=sorted(case)):
                self.write(counts(requests=1, latest_position=2), **case)
                self.assertEqual(self.cue(reply), {})
                self.assertFalse(self.marker.exists())
        state = dict(schema='cairn.native-session/1', process=self.process)
        coordination.write_state(self.path, state)
        self.assertEqual(self.cue(reply), {})
        self.assertEqual(self.calls, [], 'no lookup for a session the cue cannot speak for')

    def test_other_live_process_state_gets_no_lookup_or_cue(self):
        self.write(counts(requests=2, latest_position=9))
        other = dict(self.process, start=self.process['start'] + 1)
        state = json.loads(self.path.read_text())
        state['process'] = other
        coordination.write_state(self.path, state)
        self.assertEqual(self.cue(counts(requests=2, latest_position=9)), {})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.marker.exists())

    def test_execution_replaced_during_the_lookup_voids_the_cue(self):
        replacement = dict(AGENT, execution_id='33333333-3333-4333-8333-333333333333')
        changes = dict(
            replaced=lambda state: state.update(agent=replacement),
            retired=lambda state: state.update(retired=True),
            ending=lambda state: state.update(ending=True),
            another_process=lambda state: state.update(process=dict(self.process, start=self.process['start'] + 1)),
            turn_ended=lambda state: state.update(agent=dict(AGENT, metadata=dict(AGENT['metadata'], state='idle'))),
        )
        for name, change in changes.items():
            with self.subTest(name):
                self.marker.unlink(missing_ok=True)
                self.write(None)

                def during_lookup(change=change):
                    state = json.loads(self.path.read_text())
                    change(state)
                    coordination.write_state(self.path, state)
                    return counts(requests=1, latest_position=3)
                self.assertEqual(self.cue(during_lookup), {})
                self.assertFalse(self.marker.exists(), 'a voided lookup must not consume the arrival')

    def test_shrinking_saturated_backlog_kind_shift_is_not_an_arrival(self):
        self.write(None)
        self.assertTrue(self.cue(counts(notices=99, requests=1, latest_position=101, truncated=True)))
        self.assertEqual(self.cue(counts(notices=100, latest_position=100, truncated=True)), {})
        self.assertEqual(json.loads(self.marker.read_text())['signature'],
                         [AGENT['execution_id'], 100, 0, 100, 0])
        self.assertTrue(self.cue(counts(notices=99, requests=1, latest_position=102, truncated=True)))

    def test_workspace_excluded_during_lookup_preserves_last_valid_marker(self):
        excluded = self.root / 'excluded' / 'child'
        excluded.mkdir(parents=True)
        for field in ('recorded', 'metadata', 'event'):
            for exclusion in ('.cairn-no-memory', '.cairn-no-coordination'):
                with self.subTest(field=field, exclusion=exclusion):
                    self.write(None)
                    coordination.write_state(self.marker, dict(signature=[AGENT['execution_id'], 1, 1, 0, 0]))
                    before = self.marker.read_bytes()
                    opt_out = excluded.parent / exclusion
                    if field == 'event':
                        self.event['cwd'] = str(excluded)

                    def during_lookup():
                        state = json.loads(self.path.read_text())
                        if field == 'recorded':
                            state['workspace'] = str(excluded)
                        elif field == 'metadata':
                            state['agent']['metadata']['workspace'] = str(excluded)
                        coordination.write_state(self.path, state)
                        opt_out.touch()
                        return counts(requests=2, latest_position=2)

                    try:
                        self.assertEqual(self.cue(during_lookup), {})
                        self.assertEqual(self.marker.read_bytes(), before)
                    finally:
                        opt_out.unlink(missing_ok=True)
                        self.event['cwd'] = str(self.root)

    def test_excluded_workspace_does_not_look_up_emit_or_consume_arrival(self):
        workspaces = {name: self.root / name / 'child' for name in ('current', 'recorded', 'metadata')}
        for workspace in workspaces.values():
            workspace.mkdir(parents=True)
        self.event['cwd'] = str(workspaces['current'])
        agent = dict(AGENT, metadata=dict(AGENT['metadata'], workspace=str(workspaces['metadata'])))
        reply = counts(requests=1, latest_position=42)
        for name, workspace in workspaces.items():
            for ancestor in (False, True):
                for exclusion in ('.cairn-no-coordination', '.cairn-no-memory'):
                    with self.subTest(workspace=name, ancestor=ancestor, exclusion=exclusion):
                        self.marker.unlink(missing_ok=True)
                        self.calls.clear()
                        self.write(counts(requests=1, latest_position=42), agent=agent, workspace=str(workspaces['recorded']))
                        before = self.path.read_bytes()
                        opt_out = (workspace.parent if ancestor else workspace) / exclusion
                        opt_out.touch()
                        try:
                            self.assertEqual(self.cue(reply), {})
                            self.assertEqual(self.calls, [], 'an opted-out workspace must not be looked up')
                            self.assertFalse(self.marker.exists(), 'excluded workspace must not consume its arrival cue')
                            self.assertEqual(self.path.read_bytes(), before)
                        finally:
                            opt_out.unlink()
                        self.assertIn('1 request', self.text(self.cue()))

    def test_truncated_counts_are_marked(self):
        self.write(counts(requests=100, truncated=True, latest_position=3))
        self.assertIn('100+ requests', self.text(self.cue(counts(requests=100, notices=0, responses=0, truncated=True, latest_position=3))))

    def test_unassociated_wake_context_cannot_look_up_emit_or_consume_arrival(self):
        self.write(counts(requests=1, latest_position=42))
        before = self.path.read_bytes()
        context = self.root / 'wake.json'
        context.write_text(json.dumps(dict(schema='cairn.wake-context/0')))
        with patch.dict(os.environ, CAIRN_WAKE_CONTEXT=str(context)):
            self.assertEqual(self.cue(counts(requests=1, latest_position=42)), {})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('1 request', self.text(self.cue()))

    def test_other_account_config_home_cannot_look_up_or_emit(self):
        self.write(None)
        home = self.root / 'account-home'
        home.mkdir()
        config = dict(self.config, config_home=str(home))
        self.reply = counts(requests=1, latest_position=1)
        with patch.dict(os.environ, CLAUDE_CONFIG_DIR=str(self.root / 'other-home')), \
                patch.object(coordination, 'call', side_effect=self.lookup):
            self.assertEqual(coordination.handle(config, self.event), {})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.marker.exists())

    def test_concurrent_callbacks_share_one_lookup_and_one_cue(self):
        self.write(None)
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            self.assertTrue(release.wait(10))
            return counts(requests=1, latest_position=4)
        self.reply = slow
        results = []
        with patch.object(coordination, 'call', side_effect=self.lookup):
            first = threading.Thread(target=lambda: results.append(coordination.handle(self.config, self.event)))
            first.start()
            self.assertTrue(entered.wait(10))
            for _ in range(3):  # Parallel tool completions skip instead of queueing or repeating.
                self.assertEqual(coordination.handle(self.config, self.event), {})
            self.assertEqual(len(self.calls), 1)
            release.set()
            first.join(10)
        self.assertFalse(first.is_alive())
        self.assertIn('1 request', self.text(results[0]))
        self.assertEqual(self.cue(counts(requests=1, latest_position=4)), {})

    def test_parallel_hook_processes_emit_the_arrival_once(self):
        api_log = self.root / 'api-calls'
        script = self.root / 'cairn'
        script.write_text(f'#!{sys.executable}\nimport json,sys,time\n'
                          f'open({str(api_log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")\n'
                          'sys.stdin.read()\ntime.sleep(.25)\n'
                          'print(json.dumps(dict(ok=True, data=dict(requests=1, notices=2, responses=0, latest_position=7))))\n')
        script.chmod(0o700)
        config = dict(self.config, cairn=str(script))
        config_path = self.root / 'coordination.json'
        config_path.write_text(json.dumps(config))
        state = dict(schema='cairn.native-session/1', process=coordination.process_reference(os.getpid()), agent=AGENT)
        coordination.write_state(self.path, state)
        event = json.dumps(dict(self.event, host_pid=os.getpid()))
        processes = [subprocess.Popen([sys.executable, '-B', str(ROOT / 'integrations/lifecycle/coordination.py'),
                                       'hook', '--config', str(config_path)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        for process in processes:  # Release every callback before collecting any, so they overlap.
            process.stdin.write(event)
            process.stdin.close()
        outputs = []
        for process in processes:
            output, error = process.stdout.read(), process.stderr.read()
            self.assertEqual(process.wait(timeout=10), 0, error)
            process.stdout.close()
            process.stderr.close()
            outputs.append(json.loads(output))
        cues = [self.text(output) for output in outputs if output]
        self.assertEqual(len(cues), 1, outputs)
        self.assertIn('1 request, 2 notices waiting', cues[0])
        calls = [json.loads(line) for line in api_log.read_text().splitlines()]
        self.assertTrue(calls and all(call[-1] == 'session-inbox-pending' for call in calls), calls)
        self.assertLess(len(calls), len(processes), 'callbacks behind an in-flight lookup must skip theirs')

    def test_watcher_refreshes_busy_counts_and_clears_idle(self):
        state = dict(agent=AGENT)
        reply = counts(requests=1, notices=2, responses=0, latest_position=42)
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
        for effect in (coordination.CoordinationError('INVALID_REQUEST', 'unknown operation'), None, 'negative'):
            with self.subTest(effect=effect):
                state = dict(agent=AGENT, inbox_pending=counts(requests=5))
                kwargs = (dict(side_effect=effect) if isinstance(effect, Exception) else
                          dict(return_value=counts(requests=-1) if effect else counts(requests='many')))
                with patch.object(coordination, 'call', **kwargs):
                    coordination.refresh_inbox_pending(self.config, state)
                self.assertNotIn('inbox_pending', state)


if __name__ == '__main__':
    unittest.main()
