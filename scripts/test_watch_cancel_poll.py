"""The watcher polls a pending OpenCode cancellation faster than its normal cycle (CAIRN-40)."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from integrations.lifecycle import coordination


class FakeStop:
    """A stop event over a fake monotonic clock; waiting advances the clock."""

    def __init__(self, stop_on_wait=None):
        self.now = 1000.0
        self.waits = []
        self.stop_on_wait = stop_on_wait
        self.stopped = False

    def clock(self):
        return self.now

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.now += timeout
        if self.stop_on_wait == len(self.waits):
            self.stopped = True
        return self.stopped


class PendingCancelPassTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'session.json'
        self.session = dict(agent_id='agent-one', execution_id='execution-one')
        self.attempt = dict(attempt_id='attempt-one', session=self.session,
                            native_turn_id='turn-one', turn_exclusive=True,
                            cancel=dict(requested_at='now'), turn_stop_state='',
                            delivery=dict(delivery_id='delivery-one', lease_id='lease-one'))
        self.state = dict(agent=dict(**self.session, native_session_id='ses-one'),
                          process=coordination.process_reference(os.getpid()),
                          inbox_intent=dict(request_id='attempt-one', session=self.session,
                                            native_turn_id='turn-one'),
                          inbox_attempt=copy.deepcopy(self.attempt), delivered_since_idle=True,
                          opencode_request_endpoint='/unused/bridge.sock')
        self.config = dict(harness='opencode', native_delivery=True, binding='fixture',
                           cairn='/unused/cairn', socket='/unused/api.sock',
                           token_file='/unused/token', state_dir=self.temp.name)
        coordination.write_state(self.path, self.state)

    def saved(self):
        return json.loads(self.path.read_text())

    def refuse(self, what):
        return AssertionError(f'the cancellation pass must not run {what}')

    def test_pass_runs_only_the_cancel_step_and_keeps_polling(self):
        operations, sent = [], []

        def store_call(_config, operation, request, **_kwargs):
            operations.append(operation)
            self.assertEqual(operation, 'session-inbox-control')
            self.assertEqual(request, self.session)
            return dict(attempt=copy.deepcopy(self.attempt))

        with patch.object(coordination, 'call', side_effect=store_call), \
                patch.object(coordination, 'heartbeat', side_effect=self.refuse('a heartbeat')), \
                patch.object(coordination, 'prepare_idle_wake', side_effect=self.refuse('idle wake')), \
                patch.object(coordination, 'refresh_inbox_pending', side_effect=self.refuse('pending refresh')), \
                patch.object(coordination, 'submit_opencode_cancel',
                             side_effect=lambda _config, _path, request: sent.append(request)):
            pending = coordination.watch_pending_cancels(self.config)
        self.assertTrue(pending)
        self.assertEqual(operations, ['session-inbox-control'])  # no renewal or completion
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]['request_id'], sent[0]['expected_turn_id']), ('delivery-one', 'turn-one'))
        self.assertEqual(self.saved()['cancel_submission']['status'], 'uncertain')
        self.assertIn('inbox_intent', self.saved())

    def test_repeated_passes_never_resend_the_native_stop(self):
        sent = []
        with patch.object(coordination, 'call', return_value=dict(attempt=copy.deepcopy(self.attempt))) as call, \
                patch.object(coordination, 'submit_opencode_cancel',
                             side_effect=lambda _config, _path, request: sent.append(request)):
            results = [coordination.watch_pending_cancels(self.config) for _ in range(4)]
        self.assertEqual(results, [True] * 4)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(len(sent), 1)  # an uncertain submission is never retried automatically

    def test_confirmation_needs_the_pinned_stop_and_a_clear_scan_then_releases_once(self):
        self.config['opencode_cancel_enabled'] = True
        steps = iter([
            dict(self.attempt),                                     # pass 1: stop goes out, turn still running
            dict(self.attempt, turn_stop_state='ended'),            # pass 2: the hook recorded the exact turn end
        ])
        operations = []

        def store_call(_config, operation, request, **_kwargs):
            operations.append(operation)
            if operation == 'session-inbox-control':
                return dict(attempt=copy.deepcopy(next(steps)))
            if operation == 'session-tool-stop':
                self.assertEqual(request['terminal_scan'], 'clear')
                return dict(self.attempt, turn_stop_state='ended', terminal_scan='clear', tools=[])
            if operation == 'session-inbox-reconcile':
                self.assertEqual(request['reason'], 'cancel_confirmed')
                return dict(self.attempt, finished_at='now')
            self.fail(f'unexpected operation {operation}')

        scan_module = types.SimpleNamespace(markers=lambda *_args, **_kwargs: dict(
            clear=True, coverage='complete', processes=[], unknown=[]))
        with patch.dict('sys.modules', {'process_scan': scan_module}), \
                patch.object(coordination, 'opencode_capture_available', return_value=True), \
                patch.object(coordination, 'call', side_effect=store_call), \
                patch.object(coordination, 'submit_opencode_cancel'):
            first = coordination.watch_pending_cancels(self.config)
            self.assertEqual(operations, ['session-inbox-control'])
            second = coordination.watch_pending_cancels(self.config)
            third = coordination.watch_pending_cancels(self.config)
        self.assertEqual((first, second, third), (True, False, False))
        self.assertEqual(operations, ['session-inbox-control', 'session-inbox-control',
                                      'session-tool-stop', 'session-inbox-reconcile'])
        self.assertNotIn('inbox_intent', self.saved())  # the third pass found nothing pending

    def test_a_clear_scan_without_the_turn_stop_is_still_refused(self):
        self.config['opencode_cancel_enabled'] = True
        scan_module = types.SimpleNamespace(markers=lambda *_args, **_kwargs: self.fail('scan is premature'))
        with patch.dict('sys.modules', {'process_scan': scan_module}), \
                patch.object(coordination, 'call', return_value=dict(attempt=copy.deepcopy(self.attempt))), \
                patch.object(coordination, 'submit_opencode_cancel'):
            self.assertTrue(coordination.watch_pending_cancels(self.config))
        self.assertIn('inbox_intent', self.saved())

    def test_only_a_live_process_with_an_unconfirmed_cancel_is_advanced(self):
        stale = dict(self.state['process'], start=self.state['process']['start'] + 1)
        confirmed = copy.deepcopy(self.state['inbox_attempt'])
        confirmed['cancel']['confirmed_at'] = 'then'
        cases = {
            'process is gone': dict(process=stale),
            'cancel already confirmed': dict(inbox_attempt=confirmed),
            'no cancel requested': dict(inbox_attempt={k: v for k, v in self.attempt.items() if k != 'cancel'}),
            'no saved intent': dict(inbox_intent=None),
            'retired session': dict(retired=True),
            'ending session': dict(ending=True),
            'unregistered session': dict(agent=None),
        }
        for name, change in cases.items():
            with self.subTest(name):
                state = copy.deepcopy(self.state)
                for key, value in change.items():
                    if value is None:
                        state.pop(key)
                    else:
                        state[key] = value
                coordination.write_state(self.path, state)
                with patch.object(coordination, 'call', side_effect=self.refuse('a store call')):
                    self.assertFalse(coordination.watch_pending_cancels(self.config))

    def test_other_harnesses_are_never_advanced(self):
        for harness in ('claude', 'codex', 'hermes', 'agy'):
            with self.subTest(harness):
                config = dict(self.config, harness=harness)
                with patch.object(coordination, 'call', side_effect=self.refuse('a store call')):
                    self.assertFalse(coordination.watch_pending_cancels(config))
                self.assertEqual(coordination.pending_cancel_configs([config]), [])

    def test_a_failure_ends_fast_polling_and_is_reported_not_retried(self):
        failures = []

        def store_call(_config, operation, _request, **_kwargs):
            failures.append(operation)
            raise coordination.CoordinationError('API_UNAVAILABLE', 'response lost')

        errors = io.StringIO()
        with patch.object(coordination, 'call', side_effect=store_call), contextlib.redirect_stderr(errors):
            self.assertFalse(coordination.watch_pending_cancels(self.config))
        self.assertEqual(failures, ['session-inbox-control'])  # no retry inside the pass
        self.assertIn('Cairn presence fixture: API_UNAVAILABLE: response lost', errors.getvalue())
        self.assertIn('inbox_intent', self.saved())  # the hold stays for the full cycle

    def test_a_busy_session_lock_is_asked_again_shortly(self):
        @contextlib.contextmanager
        def busy(_path):
            raise coordination.CoordinationError('SESSION_BUSY', 'hook owns presence')
            yield

        with patch.object(coordination, 'session_lock', busy), \
                patch.object(coordination, 'call', side_effect=self.refuse('a store call')):
            self.assertTrue(coordination.watch_pending_cancels(self.config))

    def test_pending_cancel_configs_reads_only_saved_state(self):
        with patch.object(coordination, 'call', side_effect=self.refuse('a store call')):
            self.assertEqual(coordination.pending_cancel_configs([self.config]), [self.config])
            broken = dict(self.config, state_dir=str(Path(self.temp.name) / 'missing'))
            self.assertEqual(coordination.pending_cancel_configs([broken]), [])
            (Path(self.temp.name) / 'corrupt.json').write_text('{')
            (Path(self.temp.name) / 'not-an-object.json').write_text('[]')
            self.assertEqual(coordination.pending_cancel_configs([self.config]), [self.config])
            errors = io.StringIO()
            with patch.object(coordination, 'call', return_value=dict(attempt=copy.deepcopy(self.attempt))), \
                    patch.object(coordination, 'submit_opencode_cancel'), contextlib.redirect_stderr(errors):
                self.assertTrue(coordination.watch_pending_cancels(self.config))  # unreadable siblings hide nothing
            self.assertEqual(errors.getvalue(), '')


class NextCycleWaitTests(unittest.TestCase):
    def wait(self, stop, configs, seconds):
        coordination.wait_for_next_cycle(stop, configs, seconds, clock=stop.clock)

    def test_idle_watcher_keeps_one_plain_wait(self):
        stop = FakeStop()
        with patch.object(coordination, 'pending_cancel_configs', return_value=[]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=AssertionError('fast pass')):
            self.wait(stop, [dict(binding='idle')], 29.5)
        self.assertEqual(stop.waits, [29.5])

    def test_pending_cancel_is_polled_every_two_seconds_then_the_cycle_resumes(self):
        stop, config = FakeStop(), dict(binding='pending')
        results = iter([True, True, False])
        passes = []

        def fast_pass(received):
            passes.append((received, stop.now))
            return next(results)

        with patch.object(coordination, 'pending_cancel_configs', return_value=[config]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=fast_pass):
            self.wait(stop, [config], 30)
        self.assertEqual(stop.waits, [2, 2, 2, 24])
        self.assertEqual([moment - 1000 for _, moment in passes], [2, 4, 6])
        self.assertEqual(stop.now - 1000, 30)  # never later than the next full cycle

    def test_a_cancel_that_stays_pending_never_delays_the_full_cycle(self):
        stop, config = FakeStop(), dict(binding='pending')
        with patch.object(coordination, 'pending_cancel_configs', return_value=[config]), \
                patch.object(coordination, 'watch_pending_cancels', return_value=True) as fast_pass:
            self.wait(stop, [config], 7)
        self.assertEqual(stop.waits, [2, 2, 2, 1])
        self.assertEqual(fast_pass.call_count, 3)
        self.assertEqual(stop.now - 1000, 7)

    def test_only_bindings_that_remain_pending_keep_being_polled(self):
        stop = FakeStop()
        first, second = dict(binding='first'), dict(binding='second')
        seen = []

        def fast_pass(config):
            seen.append(config['binding'])
            return config is second and seen.count('second') < 2

        with patch.object(coordination, 'pending_cancel_configs', return_value=[first, second]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=fast_pass):
            self.wait(stop, [first, second], 30)
        self.assertEqual(seen, ['first', 'second', 'second'])

    def test_a_stop_request_ends_the_wait_immediately(self):
        stop, config = FakeStop(stop_on_wait=1), dict(binding='pending')
        with patch.object(coordination, 'pending_cancel_configs', return_value=[config]), \
                patch.object(coordination, 'watch_pending_cancels', side_effect=AssertionError('fast pass')):
            self.wait(stop, [config], 30)
        self.assertEqual(stop.waits, [2])

    def test_cancel_confirms_two_polls_after_the_discovering_cycle_not_a_full_cycle_later(self):
        # Real cancel step and saved state; only the store, native stop and scan are fixtures.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'session.json'
        session = dict(agent_id='agent-one', execution_id='execution-one')
        attempt = dict(attempt_id='attempt-one', session=session, native_turn_id='turn-one', turn_exclusive=True,
                       cancel=dict(requested_at='now'), turn_stop_state='',
                       delivery=dict(delivery_id='delivery-one', lease_id='lease-one'))
        config = dict(harness='opencode', native_delivery=True, binding='fixture', cairn='/unused/cairn',
                      socket='/unused/api.sock', token_file='/unused/token', state_dir=temp.name,
                      opencode_cancel_enabled=True)
        coordination.write_state(path, dict(
            agent=dict(**session, native_session_id='ses-one'), process=coordination.process_reference(os.getpid()),
            inbox_intent=dict(request_id='attempt-one', session=session, native_turn_id='turn-one'),
            inbox_attempt=copy.deepcopy(attempt), opencode_request_endpoint='/unused/bridge.sock'))
        stop = FakeStop()
        events = []

        def store_call(_config, operation, _request, **_kwargs):
            events.append((stop.now - 1000, operation))
            if operation == 'session-inbox-control':
                # The pinned TurnEnd arrives between the first and second poll.
                return dict(attempt=dict(attempt, turn_stop_state='ended' if stop.now - 1000 >= 3 else ''))
            if operation == 'session-tool-stop':
                return dict(attempt, turn_stop_state='ended', terminal_scan='clear', tools=[])
            if operation == 'session-inbox-reconcile':
                return dict(attempt, finished_at='now')
            self.fail(f'unexpected operation {operation}')

        scan_module = types.SimpleNamespace(markers=lambda *_args, **_kwargs: dict(
            clear=True, coverage='complete', processes=[], unknown=[]))
        with patch.dict('sys.modules', {'process_scan': scan_module}), \
                patch.object(coordination, 'opencode_capture_available', return_value=True), \
                patch.object(coordination, 'call', side_effect=store_call), \
                patch.object(coordination, 'submit_opencode_cancel'):
            self.wait(stop, [config], 30)
        reconciled = [moment for moment, operation in events if operation == 'session-inbox-reconcile']
        self.assertEqual(reconciled, [4])  # second poll; the old cadence needed the next 30-second cycle
        self.assertEqual(stop.waits, [2, 2, 26])
        self.assertNotIn('inbox_intent', json.loads(path.read_text()))


class WatchLoopWiringTests(unittest.TestCase):
    def test_full_cycles_stay_thirty_seconds_and_hand_their_bindings_to_the_wait(self):
        self.assertEqual((coordination.WATCH_INTERVAL, coordination.CANCEL_POLL_INTERVAL), (30, 2))
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / 'bindings'
            config_dir.mkdir()
            binding = dict(harness='opencode', native_delivery=True, binding='fixture', repo='fixture:repo',
                           cairn='/unused/cairn', socket='/unused/api.sock', token_file='/unused/token',
                           state_dir=str(Path(directory) / 'state'))
            (config_dir / 'fixture.json').write_text(json.dumps(binding))
            waits, cycles = [], []

            def next_cycle(stop, configs, seconds):
                waits.append((copy.deepcopy(configs), seconds))
                stop.set()

            with patch.object(sys, 'argv', ['coordination.py', 'watch', '--config-dir', str(config_dir)]), \
                    patch.object(coordination, 'watch_once', side_effect=lambda config: cycles.append(config['binding'])), \
                    patch.object(coordination, 'wait_for_next_cycle', side_effect=next_cycle):
                self.assertEqual(coordination.main(), 0)
        self.assertEqual(cycles, ['fixture'])
        self.assertEqual([[config['binding'] for config in configs] for configs, _ in waits], [['fixture']])
        self.assertTrue(1 <= waits[0][1] <= 30)


if __name__ == '__main__':
    unittest.main()
