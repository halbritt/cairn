"""Weekly profile transport preserves selected fields; no provider or real account."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_weekly_run as weekly


class WeeklyExecutionProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'harness/scripts/trial_task_eval.py'
        self.source.parent.mkdir(parents=True)
        self.source.write_text("EXECUTION_PROFILE_SCHEMA = 'cairn.task-eval.execution-profile/1'\n")
        self.spec = dict(
            harness=dict(root=str(self.source.parents[1])), seeds=[1, 2, 3], distractors=1900,
            actor_model='sonnet', embedding=dict(worker=dict(path='/worker')),
            arms={name: dict(binary=dict(path=f'/{name}/cairn'), hook=dict(path=f'/{name}/memory.py'))
                  for name in weekly.ARMS},
            original_execution=dict(native=dict(path='/selected/claude', sha256='a' * 64), effort='high',
                                    embedding=dict(model_identity=dict(path='/identity.json', sha256='b' * 64))),
            original_arms=dict(path='/arms.json', sha256='c' * 64),
            writer=dict(cgroup='/delegated/writer', controls=dict(path='/controls.json', sha256='d' * 64)),
            case_materials=dict(path='/materials.json', sha256='e' * 64))
        auth = self.root / 'auth.json'
        auth.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='synthetic-account'))))
        self.account = dict(auth_file=str(auth), auth_sha256=hashlib.sha256(auth.read_bytes()).hexdigest())

    def parsed(self, argv):
        with patch.object(weekly.te, 'cmd_agent', return_value=0) as command:
            self.assertEqual(weekly.te.main(argv[1:]), 0)
        fields = vars(command.call_args.args[0]).copy()
        fields.pop('execution_profile')
        return fields

    def test_profile_and_historical_harness_parse_identically_for_both_providers(self):
        for harness in ('claude', 'codex'):
            with self.subTest(harness=harness):
                spec = copy.deepcopy(self.spec)
                account = None
                if harness == 'codex':
                    spec['actor_model'] = 'gpt-6-luna'
                    execution = spec['original_execution']
                    execution['harness'] = 'codex'
                    execution['provider'] = execution.pop('native')
                    spec['account_assignment'] = dict(path='/assignment.json', sha256='1' * 64)
                    account = self.account
                run = self.root / harness
                run.mkdir()
                output = run / 'agent'
                self.source.write_text("EXECUTION_PROFILE_SCHEMA = 'cairn.task-eval.execution-profile/1'\n")
                argv = weekly.execution_argv(spec, output, run, auth_account=account)
                self.assertEqual(argv[:5], [str(self.source.with_name('trial-task-eval.sh')),
                                           'agent', '--legacy-fixtures', '--output', str(output)])
                self.assertEqual(argv[5], '--execution-profile')
                self.assertEqual(len(argv), 8)
                path = Path(argv[6])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), argv[7])
                selected = self.parsed(argv)
                self.source.write_text('# historical harness\n')
                legacy = weekly.execution_argv(spec, output, run, auth_account=account)
                self.assertNotIn('--execution-profile', legacy)
                self.assertEqual(self.parsed(legacy), selected)
                self.assertEqual(selected['memory'], ['CURRENT:/CURRENT/cairn:/CURRENT/memory.py',
                                                     'CAND:/CAND/cairn:/CAND/memory.py'])
                self.assertEqual(selected['original_writer_controls_sha256'], 'd' * 64)
                self.assertEqual(selected['case_materials_sha256'], 'e' * 64)
                self.assertEqual(selected['seeds'], 3)
                self.assertEqual(selected['first_seed'], 1)
                self.assertEqual(selected['parallel'], 1 if harness == 'codex' else 3)
                if harness == 'codex':
                    self.assertTrue(selected['codex_weekly_limits'])
                    self.assertEqual(selected['codex_auth_sha256'], self.account['auth_sha256'])
                    self.assertEqual(selected['codex_account_assignment_sha256'], '1' * 64)
                else:
                    self.assertTrue(selected['original_limits'])
                    self.assertEqual(selected['max_turns'], 40)

    def test_current_harness_declares_the_schema_its_parser_accepts(self):
        self.assertEqual(weekly.te.EXECUTION_PROFILE_SCHEMA, weekly.te.trial_attempt.PROFILE_SCHEMA)

    def test_comment_does_not_advertise_a_profile_interface(self):
        self.source.write_text('# "--execution-profile" and EXECUTION_PROFILE_SCHEMA are not declarations\n')
        argv = weekly.execution_argv(self.spec, self.root / 'agent', self.root)
        self.assertNotIn('--execution-profile', argv)
        self.assertFalse((self.root / 'execution-profile.json').exists())

    def test_unsupported_or_ambiguous_capability_refuses(self):
        for source, code in (
                ("EXECUTION_PROFILE_SCHEMA = 'future/99'\n", 'harness_execution_profile_unsupported'),
                ("EXECUTION_PROFILE_SCHEMA = schema()\n", 'harness_execution_profile_invalid'),
                ("EXECUTION_PROFILE_SCHEMA = None\n", 'harness_execution_profile_invalid'),
                ("EXECUTION_PROFILE_SCHEMA = 'cairn.task-eval.execution-profile/1'\n" * 2,
                 'harness_execution_profile_invalid')):
            with self.subTest(code=code):
                self.source.write_text(source)
                with self.assertRaisesRegex(weekly.paired.Refusal, '^' + code + '$'):
                    weekly.execution_argv(self.spec, self.root / 'agent', self.root)
                self.assertFalse((self.root / 'execution-profile.json').exists())

    def test_profile_launch_failure_never_retries_with_flags(self):
        spec = dict(self.spec, postgres=dict(bin_dir='/disposable/pg'),
                    account=dict(account_id='synthetic', config_home='/selected/claude-home'))
        with patch.object(weekly.subprocess, 'run') as run:
            run.return_value.returncode = 2
            result = weekly.LegacyHarnessExecutor().execute(spec, self.root)
        self.assertEqual(result['exit_code'], 2)
        run.assert_called_once()
        self.assertIn('--execution-profile', run.call_args.args[0])
        self.assertNotIn('--model', run.call_args.args[0])

    def test_changed_profile_refuses_before_actor_without_fallback(self):
        argv = weekly.execution_argv(self.spec, self.root / 'agent', self.root)
        path = Path(argv[-2])
        document = json.loads(path.read_text())
        document['agent']['model'] = 'other'
        path.write_text(json.dumps(document))
        with patch.object(weekly.te, 'cmd_agent') as command, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                weekly.te.main(argv[1:])
        self.assertEqual(error.exception.code, 2)
        command.assert_not_called()

    def test_profile_is_not_overwritten_on_repeated_execution(self):
        weekly.execution_argv(self.spec, self.root / 'agent', self.root)
        with self.assertRaises(FileExistsError):
            weekly.execution_argv(self.spec, self.root / 'agent', self.root)
