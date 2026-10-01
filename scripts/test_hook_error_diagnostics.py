"""Public hook diagnostics distinguish failures without exposing exception data."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_claude_inbox_recall as claude_fixture
import test_inbox_recall_bridge as codex_fixture

memory = codex_fixture.memory


class HookErrorDiagnosticsTests(unittest.TestCase):
    def fixture(self, claude=True):
        case = (claude_fixture.ClaudeInboxRecallTests() if claude else
                codex_fixture.InboxRecallBridgeTests())
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_actual_activation_lock_preserves_exit_stdout_and_single_grant(self):
        case = self.fixture()
        case.fixture['search']['selected'] = [dict(
            mandatory=True, record=dict(body='Keep the complete fixture instruction.'))]
        case.freeze()
        directory = Path(case.mc['state_dir'])
        directory.mkdir(mode=0o700, exist_ok=True)
        state_path = codex_fixture.coord.state_path(case.cc, case.event['session_id'])
        before = state_path.read_bytes()

        def public():
            return subprocess.run(
                [sys.executable, str(case.root/'memory.py'), '--config', str(case.root/'memory.json')],
                input=json.dumps(case.event), text=True, capture_output=True, timeout=10)

        with (directory/(case.event['session_id']+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = public()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr,
                'Cairn lifecycle diagnostic: phase=activation error_class=BlockingIOError\n'
                'Cairn lifecycle: invalid lifecycle input or unavailable local file; '
                'use native tools or an explicit handoff.\n')
            self.assertFalse((directory/(case.event['session_id']+'.inbox-recall.json')).exists())
            self.assertEqual(case.calls(), [])
            self.assertEqual(state_path.read_bytes(), before)
        released = public()
        self.assertEqual((released.returncode, released.stdout, released.stderr), (0, '{}\n', ''))
        admitted = case.invoke(main=True)
        self.assertEqual(admitted['code'], 0, admitted)
        self.assertIn('Keep the complete fixture instruction.', admitted['stdout'])
        self.assertEqual(len(case.ledger()['grants']), 1)
        calls = case.calls()
        with (directory/(case.event['session_id']+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            refused = public()
        self.assertEqual((refused.returncode, refused.stdout), (2, ''))
        self.assertEqual(refused.stderr,
            'Cairn lifecycle diagnostic: phase=activation error_class=BlockingIOError\n'
            'Cairn: current required context could not be restored.\n')
        self.assertEqual(case.invoke(main=True)['code'], 2)
        self.assertEqual(len(case.ledger()['grants']), 1)
        self.assertEqual(case.calls(), calls)

    def test_redacts_exception_message_path_and_custom_class_name(self):
        case = self.fixture()
        private_error = type('PrivateCustomerPath', (OSError,), {})
        with patch.object(memory, 'effective_inbox_config', side_effect=private_error('/private/token secret')):
            result = case.memory_main(case.event)
        self.assertEqual((result['code'], result['stdout']), (1, ''))
        self.assertIn('phase=activation error_class=OSError\n', result['stderr'])
        for private in ('PrivateCustomerPath', '/private/token', 'secret'):
            self.assertNotIn(private, result['stderr'])

    def test_configuration_and_input_failures_have_distinct_fixed_phases(self):
        case = self.fixture()
        config = case.root/'memory.json'
        saved = config.read_bytes()
        config.write_text('{ private configuration')
        result = case.memory_main(case.event)
        self.assertEqual((result['code'], result['stdout']), (1, ''))
        self.assertIn('phase=configuration error_class=ValueError\n', result['stderr'])
        config.write_bytes(saved)
        result = case.memory_main(['private event'])
        self.assertEqual((result['code'], result['stdout']), (1, ''))
        self.assertIn('phase=input error_class=HookError\n', result['stderr'])
        self.assertNotIn('private', result['stderr'])

    def test_codex_required_compact_stop_output_is_unchanged(self):
        case = self.fixture(claude=False)
        with patch.object(memory, 'handle', side_effect=memory.RequiredContextRefused('fixed refusal')):
            result = case.memory_main(case.compact_event())
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['stdout'], memory.encoded({
            'continue': False, 'stopReason': 'Bound inbox recall cannot restore required context.'})+'\n')
        self.assertEqual(result['stderr'],
            'Cairn lifecycle diagnostic: phase=handle error_class=RequiredContextRefused\n')


if __name__ == '__main__':
    unittest.main()
