"""Selector diagnostics from real child processes, without provider calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_claude_lifecycle import hook
from test_receipt_accounting import ReceiptCLI


class SelectorProcessObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def executable(self, body):
        path = self.root / 'selector'
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o700)
        return str(path)

    def test_success_records_process_and_reported_time_without_payloads(self):
        command = self.executable(
            'import json, sys, time\n'
            'sys.stdin.read()\n'
            'time.sleep(0.02)\n'
            'sys.stderr.write("private diagnostic"); sys.stderr.flush()\n'
            'print(json.dumps({"structured_output":{"indices":[]},'
            '"duration_ms":12,"duration_api_ms":8,"total_cost_usd":0.01,'
            '"usage":{"input_tokens":7,"output_tokens":2,"secret":"private"}}))\n')
        observation = {}
        result = hook.select_json({'claude': command, 'model': 'test-model'}, {},
                                  'private system prompt', {'request': 'private task'},
                                  stage='preview', observation=observation)
        self.assertEqual(result['structured_output'], {'indices': []})
        self.assertEqual(observation['outcome'], 'completed')
        self.assertEqual(observation['requested_model'], 'test-model')
        self.assertEqual(observation['reported_duration_ms'], 12)
        self.assertEqual(observation['reported_api_duration_ms'], 8)
        self.assertEqual(observation['reported_cost_usd'], 0.01)
        self.assertEqual(observation['usage'], {'input_tokens': 7, 'output_tokens': 2})
        self.assertEqual(observation['returncode'], 0)
        self.assertLessEqual(observation['spawn_ms'], observation['first_stdout_ms'])
        self.assertLessEqual(observation['first_stdout_ms'], observation['elapsed_ms'])
        self.assertGreaterEqual(observation['first_stdout_ms'], 20)
        self.assertGreater(observation['stdout_bytes'], 0)
        self.assertGreater(observation['stderr_bytes'], 0)
        self.assertNotIn('private', json.dumps(observation))

    def test_hook_retains_separate_preview_and_body_process_observations(self):
        command = self.executable(
            'import json, sys\n'
            'request=json.load(sys.stdin)\n'
            'choice={"indices":[0]} if "previews" in request else {"index":0}\n'
            'print(json.dumps({"structured_output":choice,"duration_ms":3}))\n')
        (self.root / '.git').mkdir()
        cli = ReceiptCLI({str(i): 'Use the recorded repair procedure.' for i in range(5)})
        original = hook.bounded_command

        def transport(args, **kwargs):
            return cli(args, **kwargs) if args[0] == 'fixture' else original(args, **kwargs)

        config = dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                      claude=command, model='test-model', semantic_fallback=True,
                      state_dir=str(self.root / 'state'))
        session = '8c1800e1-b082-4417-945c-ed2b6feaaae2'
        event = dict(hook_event_name='UserPromptSubmit', session_id=session,
                     cwd=str(self.root), prompt='repair')
        with patch.object(hook, 'bounded_command', side_effect=transport):
            output = hook.handle(config, event)
        state = json.loads((self.root / 'state' / (session + '.json')).read_text())
        recall = state['last_recall']
        for stage in ('preview_process', 'model_process'):
            self.assertEqual(recall[stage]['outcome'], 'completed')
            self.assertGreater(recall[stage]['stdout_bytes'], 0)
        self.assertEqual(recall['discovery'], 'verified')
        self.assertEqual(cli.pulls, [('0', 'full')])
        self.assertIn('recorded repair procedure', output['hookSpecificOutput']['additionalContext'])

    def test_preview_timeout_retains_diagnostics_and_does_not_claim_verification(self):
        command = self.executable('import time\ntime.sleep(2)\n')
        (self.root / '.git').mkdir()
        cli = ReceiptCLI({str(i): 'Use the recorded repair procedure.' for i in range(5)})
        original = hook.bounded_command

        def transport(args, **kwargs):
            return cli(args, **kwargs) if args[0] == 'fixture' else original(args, **kwargs)

        config = dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                      claude=command, semantic_fallback=True, state_dir=str(self.root / 'state'))
        session = '8c1800e1-b082-4417-945c-ed2b6feaaae2'
        event = dict(hook_event_name='UserPromptSubmit', session_id=session,
                     cwd=str(self.root), prompt='repair')
        with patch.object(hook, 'bounded_command', side_effect=transport), \
             patch.object(hook, 'PREVIEW_MODEL_SECONDS', 0.1):
            output = hook.handle(config, event)
        state = json.loads((self.root / 'state' / (session + '.json')).read_text())
        recall = state['last_recall']
        self.assertEqual(recall['discovery'], 'verification_unavailable')
        observation = recall['preview_process']
        self.assertEqual(observation['outcome'], 'timeout')
        self.assertGreaterEqual(observation['elapsed_ms'], 100)
        self.assertNotIn('first_stdout_ms', observation)
        self.assertNotIn('reported_cost_usd', observation)
        self.assertNotIn('model_process', recall)
        self.assertEqual(cli.pulls, [])
        self.assertEqual(output, {})

    def test_process_failures_are_distinct_and_do_not_retain_child_output(self):
        cases = (
            ('nonzero_exit', 'import sys\nsys.stderr.write("private diagnostic"); sys.exit(4)\n'),
            ('invalid_json', 'print("private invalid response")\n'),
            ('timeout', 'import time\nprint("private partial response", flush=True)\ntime.sleep(2)\n'),
        )
        for expected, child in cases:
            with self.subTest(outcome=expected):
                observation = {}
                command = self.executable(child)
                with self.assertRaises(hook.HookError):
                    hook.select_json({'claude': command}, {}, 'private prompt', {},
                                     timeout=0.1 if expected == 'timeout' else 5, observation=observation)
                self.assertEqual(observation['outcome'], expected)
                self.assertNotIn('reported_duration_ms', observation)
                self.assertNotIn('reported_cost_usd', observation)
                self.assertNotIn('private', json.dumps(observation))
        observation = {}
        with self.assertRaises(hook.HookError):
            hook.select_json({'claude': str(self.root / 'absent')}, {}, '', {},
                             observation=observation)
        self.assertEqual(observation['outcome'], 'spawn_failed')
        self.assertIsNone(observation['returncode'])
        self.assertNotIn('spawn_ms', observation)

    def test_invalid_reported_metrics_are_not_converted_to_measurements(self):
        command = self.executable(
            'import json\n'
            'print(json.dumps({"structured_output":{"index":-1},"is_error":True,'
            '"duration_ms":True,"duration_api_ms":-3,"total_cost_usd":float("nan"),'
            '"usage":{"input_tokens":False,"output_tokens":-1,"unknown":999}}))\n')
        observation = {}
        hook.select_json({'claude': command}, {}, '', {}, observation=observation)
        self.assertEqual(observation['outcome'], 'reported_error')
        self.assertEqual(observation['usage'], {})
        for key in ('reported_duration_ms', 'reported_api_duration_ms', 'reported_cost_usd'):
            self.assertNotIn(key, observation)


if __name__ == '__main__':
    unittest.main()
