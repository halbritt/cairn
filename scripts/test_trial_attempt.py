"""One attempt through the provider seam: typed profile, inert adapter, one record builder.

The unit tests need no provider, sandbox or database. ``AttemptAgainstDisposableStore``
runs the whole parse -> custody -> grade -> record path against a real disposable
TrialStore under ``scripts/trial-task-eval.sh`` with ``CAIRN_TASK_EVAL_BINARY`` set;
it never launches a model.
"""
import argparse
from contextlib import ExitStack
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import trial_attempt as attempt
import trial_task_eval as task


def synthetic_case(root):
    source = root / 'source'
    source.mkdir()
    (source / 'synthetic.txt').write_text('original\n')
    (source / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm initial\n')
    return dict(id='synthetic', workspace='synthetic', _prospective_workspace=str(source),
                copy_to='repo', cwd='repo', category='synthetic', provenance={'type': 'synthetic'},
                expected=[], wordings={'task': 'Make a synthetic change.'},
                correct=[dict(type='unchanged', path='synthetic.txt')], mistake=[])


class StubStore:
    def __init__(self, root):
        self.binary = root / 'cairn'
        self.binary.write_text('#!/bin/sh\n')
        self.sockdir = root / 'sock'
        self.sockdir.mkdir()
        self.token_file = root / 'agent.token'
        self.token_file.write_text('synthetic')
        self.names = {}


class ExecutionProfileTests(unittest.TestCase):
    def test_mode_is_read_once_from_the_namespace(self):
        plain = attempt.ExecutionProfile.from_args(SimpleNamespace(harness='claude', model='m'))
        self.assertEqual((plain.mode, plain.weekly, plain.memory_instruction), ('plain', False, attempt.MEMORY_INSTRUCTION))
        original = attempt.ExecutionProfile.from_args(SimpleNamespace(harness='claude', model='m', original_limits=True))
        self.assertEqual((original.mode, original.weekly), ('original', True))
        weekly = attempt.ExecutionProfile.from_args(SimpleNamespace(harness='codex', model='m', codex_weekly_limits=True,
                                                                    codex_binary='/b', codex_binary_sha256='s'))
        self.assertEqual((weekly.mode, weekly.codex.binary), ('codex_weekly', '/b'))
        self.assertIs(weekly.limit_checker, attempt.codex_weekly_limits)

    def test_prospective_input_supersedes_original_controls(self):
        profile = attempt.ExecutionProfile.from_args(SimpleNamespace(
            harness='claude', model='m', _prospective=dict(notes=[]), _original_arms={'A': {}}, original_limits=True))
        self.assertEqual(profile.mode, 'prospective')
        self.assertIsNone(profile.original_arms)
        self.assertEqual(profile.memory_instruction, attempt.PROSPECTIVE_MEMORY_INSTRUCTION)

    def test_unknown_harness_or_mode_refuses(self):
        with self.assertRaisesRegex(ValueError, 'unknown harness'):
            attempt.ExecutionProfile(harness='openrouter')
        with self.assertRaisesRegex(ValueError, 'unknown execution mode'):
            attempt.ExecutionProfile(mode='replay')

    def test_profile_document_round_trips_and_refuses_unknown_fields(self):
        document = attempt.profile_document(dict(harness='codex', model='m', original_limits=False))
        self.assertEqual(document['schema'], attempt.PROFILE_SCHEMA)
        self.assertEqual(attempt.profile_fields(document), dict(harness='codex', model='m', original_limits=False))
        with self.assertRaisesRegex(ValueError, 'execution_profile_unknown_field:output'):
            attempt.profile_fields(dict(schema=attempt.PROFILE_SCHEMA, agent=dict(output='/x')))
        with self.assertRaisesRegex(ValueError, 'execution_profile_shape'):
            attempt.profile_fields(dict(schema='other', agent={}))


class ProviderSelectionTests(unittest.TestCase):
    def test_real_adapter_follows_the_declared_harness(self):
        self.assertIsInstance(attempt.provider_for(attempt.ExecutionProfile(harness='claude')), attempt.ClaudeProvider)
        self.assertIsInstance(attempt.provider_for(attempt.ExecutionProfile(harness='codex')), attempt.CodexProvider)

    def test_injected_adapter_must_match_the_harness(self):
        profile = attempt.ExecutionProfile(harness='claude').with_provider(attempt.InertProvider('codex'))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            attempt.provider_for(profile)

    def test_environment_never_selects_the_inert_adapter(self):
        with patch.dict(os.environ, {'CAIRN_TRIAL_PROVIDER': 'inert', 'TRIAL_PROVIDER': 'inert'}):
            self.assertIsInstance(attempt.provider_for(attempt.ExecutionProfile(harness='claude')), attempt.ClaudeProvider)

    def test_claude_argv_shapes(self):
        plain = SimpleNamespace(profile=attempt.ExecutionProfile(harness='claude', model='m', max_turns=3))
        self.assertEqual(attempt.ClaudeProvider().setup(plain).args[:4], ['claude', '-p', '--model', 'm'])
        native = SimpleNamespace(profile=attempt.ExecutionProfile(
            harness='claude', model='m', effort='high', max_turns=3, mode='original',
            original_native=dict(observed=dict(target='/opt/claude'))))
        args = attempt.ClaudeProvider().setup(native).args
        self.assertEqual(args[:6], ['/opt/claude', '-p', '--model', 'm', '--effort', 'high'])
        self.assertIn('bypassPermissions', args)

    def test_codex_setup_mounts_only_the_selected_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / 'install'
            (install / 'codex/bin').mkdir(parents=True)
            auth = root / 'auth.json'
            auth.write_text('{}')
            (root / 'base').mkdir()
            profile = attempt.ExecutionProfile(harness='codex', model='m', effort='high',
                                               codex=attempt.CodexSelection(install=str(install), auth_file=str(auth)))
            setup = attempt.CodexProvider().setup(SimpleNamespace(profile=profile, home=root / 'home', base=root / 'base',
                                                                  assigned_account=None))
            self.assertEqual(setup.env, dict(CODEX_HOME=str(root / 'home/.codex')))
            self.assertIn((auth.resolve(), root / 'home/.codex/auth.json', 'ro'), setup.binds)
            self.assertIn('--ignore-user-config', setup.args)
            self.assertNotIn('OPENAI_API_KEY', json.dumps(setup.args))


class InertAttemptTests(unittest.TestCase):
    """The full attempt with the inert adapter and no module-global patching."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.case = synthetic_case(self.root)
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'memory.py').write_text('# synthetic hook\n')
        self.stores = {'CURRENT': dict(store=StubStore(self.root), hook=str(hook / 'memory.py'), configuration={})}
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)

    def run_arm(self, arm, **namespace):
        args = SimpleNamespace(harness='claude', model='inert', wording='task', timeout=20, max_turns=2,
                               semantic_recall=[], selector_model='inert', **namespace)
        provider = attempt.InertProvider('claude', answer='Left unchanged.', seconds=0.1)
        with patch.object(task, 'load_corpus', return_value=[]):
            record = task.run_agent(self.case, arm, 0, 0, args, self.stores, self.out, provider=provider)
        return record, provider

    def test_original_limits_memory_arm_records_from_typed_results(self):
        record, provider = self.run_arm('CURRENT', original_limits=True)
        base = self.out / 'runs/synthetic.CURRENT.s0'
        self.assertEqual(json.loads((base / 'result.json').read_text()), record)
        self.assertEqual(record['outcome'], 'correct')
        self.assertEqual(record['original_limits']['status'], 'within_original_limits')
        self.assertEqual(record['process_custody']['classification'], 'completed')
        self.assertEqual(record['process_custody']['cleanup']['status'], 'observed_clear')
        self.assertEqual(record['stream_sha256'], hashlib.sha256((base / 'stream.jsonl').read_bytes()).hexdigest())
        self.assertEqual(record['prompt_sha256'], hashlib.sha256(b'Make a synthetic change.').hexdigest())
        self.assertEqual(record['memory']['recall_observation'], 'per_invocation')
        self.assertEqual(set(record['baseline_custody']), {'bytes', 'sha256'})
        launched = [row for row in provider.launched if not row['prerequisite']]
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]['argv'][-2:], ['--', 'Make a synthetic change.'])
        settings = json.loads((base / 'settings.json').read_text())
        self.assertEqual({hook['timeout'] for rows in settings['hooks'].values() for row in rows for hook in row['hooks']},
                         {5})

    def test_plain_none_arm_has_no_memory_and_no_custody(self):
        record, _ = self.run_arm('none')
        self.assertEqual(record['memory'], {})
        self.assertNotIn('process_custody', record)
        self.assertNotIn('original_limits', record)
        self.assertEqual(record['trace']['completed'], True)

    def test_failed_record_recovers_only_retained_typed_evidence(self):
        profile = attempt.ExecutionProfile(harness='codex', model='m', mode='codex_weekly')
        base = self.root / 'failed'
        base.mkdir()
        (base / 'stream.jsonl').write_text(json.dumps(dict(type='turn.completed', usage={})) + '\n')
        toolkit = task.attempt_toolkit()
        record = attempt.failed_attempt_record(self.case, 'CAND', 1, profile, RuntimeError('boom'), base, toolkit,
                                               account=dict(account_id='acct', auth_sha256='a' * 64))
        self.assertEqual((record['run_id'], record['outcome'], record['error']), ('synthetic.CAND.s1', 'harness_error', 'boom'))
        self.assertEqual((record['account_id'], record['observer_failure_reason']), ('acct', 'codex_attempt_exception'))
        self.assertIn('trace', record)


class ExecutionProfileCliTests(unittest.TestCase):
    """The pinned profile is the deep entry; the flags remain a compatible adapter."""

    def write(self, root, fields):
        path = root / 'profile.json'
        path.write_bytes(json.dumps(attempt.profile_document(fields)).encode())
        path.chmod(0o600)
        return str(path), hashlib.sha256(path.read_bytes()).hexdigest()

    def parse(self, argv):
        seen = {}
        def capture(args, provider=None):
            seen.update(vars(args))
            return 0
        with patch.object(task, 'cmd_agent', side_effect=capture):
            self.assertEqual(task.main(argv), 0)
        return seen

    def test_profile_fields_apply_like_the_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, digest = self.write(root, dict(model='opus', max_turns=7, timeout=120, seeds=2))
            seen = self.parse(['agent', '--output', str(root / 'out'), '--legacy-fixtures',
                               '--execution-profile', path, digest])
            self.assertEqual((seen['model'], seen['max_turns'], seen['timeout'], seen['seeds']), ('opus', 7, 120, 2))
            flagged = self.parse(['agent', '--output', str(root / 'out'), '--legacy-fixtures', '--model', 'opus',
                                  '--execution-profile', path, digest])
            self.assertEqual(flagged['model'], 'opus')

    def test_conflicting_flag_and_changed_bytes_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, digest = self.write(root, dict(model='opus'))
            with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit):
                task.main(['agent', '--output', str(root / 'out'), '--legacy-fixtures', '--model', 'haiku',
                           '--execution-profile', path, digest])
            self.assertIn('--model conflicts with the execution profile', stderr.getvalue())
            with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit):
                task.main(['agent', '--output', str(root / 'out'), '--legacy-fixtures',
                           '--execution-profile', path, '0' * 64])
            self.assertIn('execution profile refused', stderr.getvalue())

    def test_profile_cannot_bypass_flag_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, digest = self.write(root, dict(original_limits=True, harness='codex', model='m'))
            with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit):
                task.main(['agent', '--output', str(root / 'out'), '--legacy-fixtures',
                           '--execution-profile', path, digest])
            self.assertIn('--original-limits applies only to legacy Claude fixtures', stderr.getvalue())


@unittest.skipUnless(os.environ.get('CAIRN_TASK_EVAL_PG') and os.environ.get('CAIRN_TASK_EVAL_BINARY'),
                     'requires owned wrapper and explicit test binary')
class AttemptAgainstDisposableStore(unittest.TestCase):
    """cmd_agent end to end with the inert adapter and a real disposable store; no model quota."""

    def test_campaign_records_every_arm_without_a_provider(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='cairn-attempt-e2e-')))
            case = synthetic_case(root)
            note = dict(id='cobalt', kind='lesson', body='Synthetic cobalt restore fixture.', shareable=True)
            hook = Path(__file__).resolve().parents[1] / 'integrations/lifecycle/memory.py'
            args = argparse.Namespace(
                output=str(root / 'out'), harness='claude', model='inert', reasoning_effort='high', wording='task',
                cases=None, first_seed=0, seeds=1, arms=['none', 'direct'], distractors=0, paired_plan=[],
                memory=['CAND:' + os.environ['CAIRN_TASK_EVAL_BINARY'] + ':' + str(hook)],
                semantic_recall=[], selector_model='inert', selector_binary=None, semantic_worker=None,
                embedding_worker=None, max_turns=2, timeout=60, parallel=1, cold_readiness_timeout=120,
                original_limits=True, codex_weekly_limits=False, prospective_input=None)
            provider = attempt.InertProvider('claude', answer='Left unchanged.')
            stack.enter_context(patch.object(task, 'verify_frozen', return_value=dict(
                cases_sha256='c', corpus_sha256='k', labels_sha256='l')))
            stack.enter_context(patch.object(task, 'load_cases', return_value=[case]))
            stack.enter_context(patch.object(task, 'load_corpus', return_value=[note]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            status = task.cmd_agent(args, provider=provider)
            report = json.loads((root / 'out/agent.json').read_text())
            self.assertEqual(status, 0, report.get('measurement_failures'))
            self.assertEqual(report['harness_version'], 'inert')
            self.assertEqual(report['admission']['state'], 'complete')
            records = {row['arm']: row for row in report['records']}
            self.assertEqual(set(records), {'none', 'direct', 'CAND'})
            self.assertTrue(all(row['outcome'] == 'correct' for row in records.values()), records)
            cand = records['CAND']
            self.assertEqual(cand['original_limits']['status'], 'within_original_limits')
            self.assertEqual(cand['process_custody']['cleanup']['status'], 'observed_clear')
            mcp = json.loads((root / 'out/runs/synthetic.CAND.s0/mcp.json').read_text())
            self.assertEqual(mcp['mcpServers']['cairn']['args'][-len(task.legacy_limits.mcp_tool_room_arguments()):],
                             task.legacy_limits.mcp_tool_room_arguments())
            self.assertEqual(report['memory']['CAND']['backend'], 'lexical')


if __name__ == '__main__':
    unittest.main()
