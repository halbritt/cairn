"""Inert full run_agent boundary under --original-limits: fake actor, fake hooks, no provider or model."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trial_legacy_limits as limits
import trial_native_capture as native_capture
import trial_task_eval as task

ACTOR = r'''
import json, sys
from pathlib import Path
hookstate, seconds, answer = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
rows = [dict(schema='cairn.task-hook-observation/1', event=event, recall_attempted=False, recall=None, exit_code=0,
             stdout_bytes=40, stderr_bytes=0, process_seconds=seconds,
             memory_call_attempts={'search': 1, 'pull': 0, 'pull-evidence': 0, 'history': 0, 'other': 0})
        for event in ('SessionStart', 'UserPromptSubmit')]
(hookstate / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
print(json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)]))))
print(json.dumps(dict(type='result', result=answer, is_error=False, num_turns=1)))
'''


class Store:
    def __init__(self, root):
        self.binary = root / 'cairn'
        self.binary.write_text('#!/bin/sh\n')
        self.sockdir = root / 'sock'
        self.sockdir.mkdir()
        self.token_file = root / 'agent.token'
        self.token_file.write_text('synthetic')
        self.names = {}


class OriginalLimitsBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'synthetic.txt').write_text('original\n')
        (source / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm initial\n')
        self.prompt = 'Make a synthetic change.'
        self.case = dict(id='synthetic', workspace='synthetic', _prospective_workspace=str(source),
                         copy_to='repo', cwd='repo', category='synthetic', provenance={'type': 'synthetic'},
                         expected=[], wordings={'task': self.prompt},
                         correct=[dict(type='unchanged', path='synthetic.txt')], mistake=[])
        self.args = SimpleNamespace(harness='claude', model='inert', wording='task', timeout=20, max_turns=2,
                                    semantic_recall=[], original_limits=True, selector_model='inert')
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'memory.py').write_text('# synthetic hook\n')
        self.stores = {'CURRENT': dict(store=Store(self.root), hook=str(hook / 'memory.py'), configuration={})}
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)
        self.launched = []

    def run_actor(self, seconds=0.1, answer='Done.', extra=()):
        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            self.launched.append(dict(binds=binds, env=env, argv=argv))
            hookstate = next(source for source, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            return [sys.executable, '-c', ACTOR, str(hookstate), str(seconds), answer]
        grade = patch.object(task, 'grade', side_effect=task.grade)
        evaluate = patch.object(task, 'evaluate', side_effect=task.evaluate)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(task, 'load_corpus', return_value=[]))
            stack.enter_context(patch.object(task, 'sandbox_command', side_effect=inert))
            self.grade, self.evaluate = stack.enter_context(grade), stack.enter_context(evaluate)
            for context in extra:
                stack.enter_context(context)
            return task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)

    def attempt(self, name):
        return self.out / 'runs/synthetic.CURRENT.s0' / name

    def test_original_values_are_applied_in_the_launched_configuration_and_capture(self):
        record = self.run_actor()
        settings = json.loads(self.attempt('settings.json').read_text())
        timeouts = {hook['timeout'] for rows in settings['hooks'].values() for row in rows for hook in row['hooks']}
        self.assertEqual(timeouts, {5})
        mcp = json.loads(self.attempt('mcp.json').read_text())['mcpServers']['cairn']['args']
        self.assertEqual(mcp[-4:], ['--tokens', '32000', '--max-tokens', '32000'])
        config = json.loads(self.attempt('hook-config.json').read_text())
        charge = limits.input_charge(self.prompt, task.MEMORY_INSTRUCTION)
        self.assertEqual(config['context_bytes'], 9500 - charge)
        timing = json.loads(self.attempt('stream-timing.json').read_text())
        self.assertEqual([row['line'] for row in timing], [1, 2])
        self.assertEqual(record['original_limits']['status'], 'within_original_limits')
        self.assertEqual(record['original_limits']['input_bytes'], charge)
        self.assertEqual(record['original_limits']['hook_wire_bytes'], 80)
        self.assertEqual(record['process_custody']['cleanup']['status'], 'observed_clear')
        self.assertEqual(record['outcome'], 'correct')
        self.grade.assert_called_once()
        self.assertEqual(self.launched[0]['argv'][-2:], ['--', self.prompt])
        # agent-504's retained pre-actor baseline is still produced on original-mode rows.
        self.assertEqual(set(record['baseline_custody']), {'bytes', 'sha256'})
        self.assertRegex(record['baseline_custody']['sha256'], '^[0-9a-f]{64}$')

    def test_exceeding_rows_are_recorded_as_limit_failures(self):
        record = self.run_actor(seconds=6)
        self.assertEqual(record['original_limits']['failures'], ['hook_exceeded_5_seconds'])
        self.tearDown_out()
        record = self.run_actor(answer='x' * 8193)
        self.assertEqual(record['original_limits']['failures'], ['final_answer_exceeded_8192_bytes'])

    def tearDown_out(self):
        self.out = self.root / 'output-2'
        self.out.mkdir(mode=0o700)

    def test_exit_zero_with_unknown_cleanup_is_never_graded(self):
        original_cleanup = native_capture.OwnedProcesses.cleanup

        def unknown_cleanup(owner):
            result = original_cleanup(owner)
            owner.errors.add('process_scan_unknown')  # agent-341's injection, test-only
            return result
        record = self.run_actor(extra=(patch.object(native_capture.OwnedProcesses, 'cleanup', unknown_cleanup),))
        custody = record['process_custody']
        self.assertEqual((custody['legacy_exit_code'], custody['leader_reaped'], custody['cleanup']['status'],
                          custody['cleanup']['errors']), (0, True, 'unknown', ['process_scan_unknown']))
        self.assertEqual((record['outcome'], record['execution_failure']), ('harness_error', 'process_custody_unknown'))
        self.grade.assert_not_called()
        self.evaluate.assert_not_called()

    def test_missing_custody_is_never_graded(self):
        record = self.run_actor(extra=(patch.object(native_capture, 'retain_metadata', lambda *a, **k: None),))
        self.assertEqual(record['process_custody']['classification'], 'unknown')
        self.assertEqual((record['outcome'], record['execution_failure']), ('harness_error', 'process_custody_unknown'))
        self.grade.assert_not_called()
        self.evaluate.assert_not_called()

    def test_without_the_flag_the_legacy_configuration_is_unchanged(self):
        self.args.original_limits = False
        record = self.run_actor()
        settings = json.loads(self.attempt('settings.json').read_text())
        self.assertEqual({hook['timeout'] for rows in settings['hooks'].values() for row in rows for hook in row['hooks']},
                         {13})
        mcp = json.loads(self.attempt('mcp.json').read_text())['mcpServers']['cairn']['args']
        self.assertNotIn('--tokens', mcp)
        self.assertEqual(json.loads(self.attempt('hook-config.json').read_text())['context_bytes'],
                         task.CLAUDE_CONTEXT_BYTES)
        self.assertFalse(self.attempt('stream-timing.json').exists())
        self.assertNotIn('original_limits', record)
        self.grade.assert_called_once()


class OriginalLimitsReportTests(unittest.TestCase):
    def test_rows_outside_the_original_limits_are_measurement_failures(self):
        import argparse
        import collections
        from unittest.mock import Mock
        cases = [dict(id='synthetic-a'), dict(id='synthetic-b'), dict(id='synthetic-c')]
        statuses = {'synthetic-a': 'within_original_limits', 'synthetic-b': 'failed', 'synthetic-c': None}

        def attempt(case, arm, seed, order, args, stores, out):
            status = statuses[case['id']]
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case['id'], arm=arm, seed=seed, outcome='correct',
                        original_limits=None if status is None else dict(status=status))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Mock(backend='lexical', readiness=None, env={}, binary=Path('/bin/true'))
            store.root = store.home = root / 'store'
            store.root.mkdir()
            store.token_file = store.root / 'agent.token'
            store.ids = collections.defaultdict(lambda: 'synthetic-record')
            hook = root / 'memory.py'
            hook.write_text('# synthetic\n')
            args = argparse.Namespace(model='inert', harness='claude', wording='task', cases=None, first_seed=0, seeds=1,
                                      paired_plan=[], arms=[], reasoning_effort=None, semantic_worker=None,
                                      embedding_worker=None, distractors=0, memory=[f'CURRENT:/bin/true:{hook}'],
                                      output=str(root / 'out'), semantic_recall=[], selector_model='inert',
                                      cold_readiness_timeout=10, parallel=1, max_turns=2, timeout=10, original_limits=True)
            with patch.object(task, 'TrialStore', return_value=store), \
                    patch.object(task, 'verify_frozen', return_value=dict(cases_sha256='c', corpus_sha256='k', labels_sha256='l')), \
                    patch.object(task, 'load_cases', return_value=cases), patch.object(task, 'load_corpus', return_value=[]), \
                    patch.object(task, 'run_agent', side_effect=attempt), \
                    patch.object(task, 'cairn_json', return_value={}), \
                    patch.object(task, 'run', return_value=Mock(stdout=b'inert')), contextlib.redirect_stdout(io.StringIO()):
                code = task.cmd_agent(args)
            report = json.loads((root / 'out/agent.json').read_text())
            plan = json.loads((root / 'out/plan.json').read_text())
        self.assertEqual(code, 2)
        self.assertEqual(report['measurement_failures'], ['synthetic-b.CURRENT.s0', 'synthetic-c.CURRENT.s0'])
        self.assertEqual(plan['original_limits'], limits.LIMITS)

    def test_cli_accepts_the_flag_only_for_legacy_claude_task_wording(self):
        for extra in (['--harness', 'codex', '--model', 'x'], ['--wording', 'direct']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                with patch('sys.stderr'):
                    task.main(['agent', '--legacy-fixtures', '--output', '/nonexistent/out', '--original-limits', *extra])


if __name__ == '__main__':
    unittest.main()
