import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from trial_host import TrialHost


class TrialHostTest(unittest.TestCase):
    def test_spawn_is_confirmed_before_wrapper_can_execute_and_retry_cannot_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'executed'
            scope = dict(repo='fixture:host', task_id='réparation', run_id='run')
            # This child stands in for the CLI transport, not a model or a
            # successful task. It only exposes the actual process boundary.
            command = [sys.executable, '-c',
                       'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("ran"); print("réponse")', str(marker)]
            host = TrialHost(root / 'host', command, {}, scope)

            def observed(operation, request):
                self.assertEqual(operation, 'spawn')
                self.assertFalse(marker.exists())
                self.assertEqual(request['scope'], scope)
                self.assertTrue((host.root / 'process-started.json').exists())
                self.assertEqual(json.loads((host.root / 'spawn.pending.json').read_text()), request)
                return dict(attempt_id=request['attempt_id'], state='running')

            with patch.object(host, 'call', side_effect=observed):
                result = host.run([], timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, 'réponse\n'.encode())
            self.assertEqual(json.loads((host.root / 'intent.json').read_text())['scope'], scope)
            self.assertEqual(marker.read_text(), 'ran')
            self.assertTrue((host.root / 'spawn.confirmed.json').exists())
            self.assertFalse((host.root / 'spawn.pending.json').exists())
            with self.assertRaises(FileExistsError):
                host.run([], timeout=5)
            self.assertEqual((host.root / 'intent.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn('command', json.loads((host.root / 'intent.json').read_text()))


    def test_lost_spawn_response_keeps_retry_payload_and_never_releases_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'must-not-execute'
            host = TrialHost(root / 'host', [sys.executable, '-c',
                             'import pathlib,sys; pathlib.Path(sys.argv[1]).touch()', str(marker)],
                             {}, dict(repo='r', task_id='t', run_id='u'))
            with patch.object(host, 'call', side_effect=RuntimeError('response lost')):
                with self.assertRaisesRegex(RuntimeError, 'response lost'):
                    host.run([], timeout=5)
            self.assertFalse(marker.exists())
            spawn = json.loads((host.root / 'spawn.pending.json').read_text())
            terminal = json.loads((host.root / 'terminal.pending.json').read_text())
            self.assertEqual(spawn['attempt_id'], terminal['attempt_id'])
            self.assertNotEqual(terminal['state'], 'completed')
            self.assertNotEqual(json.loads((host.root / 'process.json').read_text())['returncode'], 0)
            self.assertFalse((host.root / 'spawn.confirmed.json').exists())
            with self.assertRaises(FileExistsError):
                host.run([], timeout=5)

    def test_terminal_response_loss_retains_exact_request_and_zero_exit_is_not_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            host = TrialHost(Path(directory) / 'host', [sys.executable, '-c', 'pass'], {},
                             dict(repo='r', task_id='t', run_id='u'))
            with patch.object(host, 'call', return_value=dict(attempt_id=host.attempt_id, state='running')):
                self.assertEqual(host.run([], timeout=5).returncode, 0)
            sent = []

            def lost(operation, request):
                sent.append((operation, request))
                raise RuntimeError('terminal response lost')

            with patch.object(host, 'call', side_effect=lost):
                with self.assertRaisesRegex(RuntimeError, 'terminal response lost'):
                    host.finish('')
            pending = json.loads((host.root / 'terminal.pending.json').read_text())
            self.assertEqual(sent, [('terminal', pending)])
            self.assertEqual(pending['state'], 'no_result')
            self.assertFalse((host.root / 'terminal.confirmed.json').exists())

    def test_timeout_observes_process_end_and_leaves_recoverable_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            host = TrialHost(Path(directory) / 'host', [sys.executable, '-c',
                             'import time; time.sleep(60)'], {}, dict(repo='r', task_id='t', run_id='u'))
            with patch.object(host, 'call', return_value=dict(attempt_id=host.attempt_id, state='running')):
                with self.assertRaises(subprocess.TimeoutExpired):
                    host.run([], timeout=.1)
            self.assertLess(host.result.returncode, 0)
            terminal = json.loads((host.root / 'terminal.pending.json').read_text())
            self.assertEqual(terminal['state'], 'killed')
            self.assertEqual(terminal['result_ref'], '')


    def test_postprocess_failure_can_preserve_terminal_without_sending_or_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            host = TrialHost(Path(directory) / 'host', [sys.executable, '-c', 'pass'], {},
                             dict(repo='r', task_id='t', run_id='u'))
            with patch.object(host, 'call', return_value=dict(attempt_id=host.attempt_id, state='running')):
                host.run([], timeout=5)
            with patch.object(host, 'call', side_effect=AssertionError('must not send')):
                host.retain_terminal()
                first = (host.root / 'terminal.pending.json').read_bytes()
                host.retain_terminal()
            self.assertEqual((host.root / 'terminal.pending.json').read_bytes(), first)
            self.assertEqual(json.loads(first)['state'], 'no_result')


if __name__ == '__main__':
    unittest.main()
