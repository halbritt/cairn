"""Hermes selected-command boundary; native objects are checked separately."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_hermes_lifecycle as hermes

plugin = hermes.plugin


class SelectedMemoryCommands(unittest.TestCase):
    setUp = hermes.HermesBoundaryTests.setUp

    def select(self, commands):
        # A retained script is deliberately present: a selected failure must
        # never revive it, even if it would otherwise be runnable.
        self.provider.config['commands'] = commands

    def test_selected_literal_argv_and_event_payload_reach_owned_child(self):
        argv = ['/selected path/inbox', '--literal', '$HOME;$(false)', '日本語']
        self.select({'UserPromptSubmit': argv})
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(
                returncode=0, stdout='{}\n')) as run:
            self.assertEqual(self.provider.invoke('UserPromptSubmit', prompt='PRIVATE'), {})
        self.assertEqual(run.call_args.args[0], argv)
        self.assertEqual(run.call_args.args[1]['prompt'], 'PRIVATE')
        self.assertEqual(run.call_args.kwargs['timeout'], 13)

    def test_capture_and_tool_select_their_existing_host_deadlines(self):
        for event, timeout in (('PreCompact', 150), ('SessionEnd', 150),
                               ('PostToolUse', 13), ('PostToolUseFailure', 13)):
            with self.subTest(event=event):
                self.select({event: ['/selected/' + event]})
                with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(
                        returncode=0, stdout='{}\n')) as run:
                    self.assertEqual(self.provider.invoke(event), {})
                self.assertEqual(run.call_args.args[0], ['/selected/' + event])
                self.assertEqual(run.call_args.kwargs['timeout'], timeout)

    def test_invalid_selected_rows_never_launch_retained_python_or_leak_payload(self):
        for commands in (None, [], {}, {'SessionEnd': []}, {'SessionEnd': 'shell command'},
                         {'SessionEnd': ['/selected', '']}, {'SessionEnd': ['/bad\0path']},
                         {'SessionEnd': ['/selected', 1]}):
            with self.subTest(commands=commands):
                self.select(commands)
                with patch.object(plugin, 'run_engine') as run, self.assertLogs(plugin.logger, 'WARNING') as logs:
                    self.assertIsNone(self.provider.invoke('SessionEnd', messages=['PRIVATE']))
                run.assert_not_called()
                self.assertNotIn('PRIVATE', str(logs.output))
                record = plugin.read_control(self.home, self.provider.control_key)
                self.assertEqual(record['last_capture']['outcome'], 'failed')

    def test_selected_missing_refusing_and_malformed_child_remain_retryable(self):
        self.select({'SessionEnd': ['/selected/inbox']})
        failures = (FileNotFoundError('PRIVATE'),
                    types.SimpleNamespace(returncode=1, stdout='PRIVATE', stderr='PRIVATE'),
                    types.SimpleNamespace(returncode=0, stdout='["PRIVATE"]'))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                arguments = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch.object(plugin, 'run_engine', **arguments), self.assertLogs(plugin.logger, 'WARNING') as logs:
                    self.provider.post_turn(session_id=self.provider.session_id,
                                            conversation_history=[dict(role='user', content='PRIVATE')])
                self.assertTrue(self.provider.capture_pending)
                self.assertNotIn('PRIVATE', str(logs.output))

    def test_removed_selected_table_cannot_revive_retained_script(self):
        self.provider.config['lifecycle_selection'] = {'path': '/selected/manifest.json', 'sha256': 'a' * 64}
        with patch.object(plugin, 'run_engine', return_value=types.SimpleNamespace(returncode=1)) as run:
            with self.assertLogs(plugin.logger, 'WARNING'):
                self.assertIsNone(self.provider.invoke('SessionEnd', messages=['PRIVATE']))
        run.assert_not_called()

    def test_interrupt_cleans_owned_group_and_preserves_host_interruption(self):
        process = Mock(pid=987654)
        process.communicate.side_effect = [KeyboardInterrupt(), ('', '')]
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        with patch.object(plugin.subprocess, 'Popen', return_value=process), patch.object(plugin.os, 'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                plugin.run_engine(['/selected/inbox'], {})
        kill.assert_called_once_with(987654, plugin.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_process_group_values_at_or_below_one_are_never_signalled(self):
        for pid in (-1, 0, 1):
            process = Mock(pid=pid)
            process.communicate.side_effect = subprocess.TimeoutExpired('fixture', 1)
            process.__enter__ = Mock(return_value=process)
            process.__exit__ = Mock(return_value=False)
            with self.subTest(pid=pid), patch.object(plugin.subprocess, 'Popen', return_value=process), patch.object(plugin.os, 'killpg') as kill:
                with self.assertRaises(RuntimeError):
                    plugin.run_engine(['/selected/inbox'], {}, timeout=1)
                kill.assert_not_called()


class OwnedEngineInterruption(unittest.TestCase):
    def test_real_sigint_joins_only_this_engine_and_its_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pidfile = root / 'owned.json'
            engine = root / 'engine.py'
            engine.write_text('''import json,os,pathlib,subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
path=pathlib.Path(sys.argv[1]); pending=path.with_suffix('.pending')
pending.write_text(json.dumps([os.getpid(),p.pid])); pending.replace(path)
time.sleep(60)
''')
            driver = '''import json,sys
sys.path.insert(0,sys.argv[1])
import test_hermes_lifecycle as hermes
try: hermes.plugin.run_engine(json.loads(sys.argv[2]),{},timeout=20)
except KeyboardInterrupt: sys.exit(130)
'''
            unrelated = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', driver,
                                      str(Path(__file__).resolve().parent),
                                      json.dumps([sys.executable, '-I', '-B', str(engine), str(pidfile)])],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for process in (child, unrelated):
                    self.assertGreater(process.pid, 1)
                    self.assertEqual(Path('/proc', str(process.pid), 'exe').resolve(), Path(sys.executable).resolve())
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(pidfile.exists(), 'owned engine did not publish its child')
                owned = json.loads(pidfile.read_text())
                self.assertTrue(all(type(pid) is int and pid > 1 for pid in owned))
                child.send_signal(signal.SIGINT)
                stdout, stderr = child.communicate(timeout=6)
                self.assertEqual((child.returncode, stdout, stderr), (130, b'', b''))
                self.assertIsNone(unrelated.poll(), 'interruption reached another invocation')
                deadline = time.monotonic() + 3
                for pid in owned:
                    while True:
                        try:
                            state = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[0]
                        except (FileNotFoundError, ProcessLookupError):
                            break
                        if state == 'Z':
                            break
                        self.assertLess(time.monotonic(), deadline, 'owned descendant remained running')
                        time.sleep(0.02)
            finally:
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
                    child.communicate(timeout=6)
                if unrelated.poll() is None:
                    unrelated.kill()
                unrelated.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
