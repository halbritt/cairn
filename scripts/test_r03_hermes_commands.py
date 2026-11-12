"""Hermes selected-command boundary; native objects are checked separately."""
import subprocess
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


if __name__ == '__main__':
    unittest.main()
