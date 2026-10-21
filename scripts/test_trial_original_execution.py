"""Synthetic original-execution seam: a fake native binary, an emulated mount, no provider or model.

The real sandbox_command builds the bwrap argv. The emulator then runs the
mount source for the provider's absolute target, so the test can observe
which bytes ran at that target and which arguments reached them.
"""
import argparse
import collections
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import trial_native_copy as native_copy
import trial_task_eval as task

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('fake-claude 9.9.9 (synthetic)')
    sys.exit(0)
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(dict(executable=sys.argv[0], argv=sys.argv[1:])) + '\n')
hookstate = Path(os.environ['FAKE_HOOKSTATE'])
rows = [dict(schema='cairn.task-hook-observation/1', event=event, recall_attempted=False, recall=None, exit_code=0,
             stdout_bytes=40, stderr_bytes=0, process_seconds=0.1,
             memory_call_attempts={'search': 1, 'pull': 0, 'pull-evidence': 0, 'history': 0, 'other': 0})
        for event in ('SessionStart', 'UserPromptSubmit')]
(hookstate / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
print(json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text='Done.')]))))
print(json.dumps(dict(type='result', result='Done.', is_error=False, num_turns=1)))
'''


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Store:
    def __init__(self, root):
        self.binary = root / 'cairn'
        self.binary.write_text('#!/bin/sh\n')
        self.sockdir = root / 'sock'
        self.sockdir.mkdir()
        self.token_file = root / 'agent.token'
        self.token_file.write_text('synthetic')
        self.names = {}


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'home'
        (self.home / '.claude').mkdir(parents=True)
        home_patch = patch.object(Path, 'home', return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.binary = self.root / 'declared/claude'
        self.binary.parent.mkdir()
        self.binary.write_text(FAKE)
        self.binary.chmod(0o755)
        self.declared = sha256(self.binary)


class RetainedCopyTests(Fixture):
    def test_copy_is_hashed_versioned_private_and_distinct_from_the_child_target(self):
        out = self.root / 'out'
        out.mkdir()
        retained = native_copy.retain(str(self.binary), self.declared, out)
        copy = Path(retained['observed']['source'])
        self.assertEqual(copy, out / 'native/claude')
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o500)
        self.assertEqual(retained['declared'], dict(path=str(self.binary), sha256=self.declared))
        self.assertEqual(retained['observed']['sha256'], self.declared)
        self.assertEqual(retained['observed']['version'], 'fake-claude 9.9.9 (synthetic)')
        self.assertEqual(retained['observed']['target'], str(self.home / '.local/bin/claude'))
        self.assertNotEqual(retained['observed']['source'], retained['observed']['target'])
        # Swapping the declared source after the copy changes nothing that runs.
        self.binary.write_text('#!/bin/sh\necho swapped\n')
        self.assertEqual(sha256(copy), self.declared)

    def test_wrong_declared_sha_refuses_before_any_copy(self):
        out = self.root / 'out'
        out.mkdir()
        with self.assertRaisesRegex(ValueError, '^original_native_mismatch$'):
            native_copy.retain(str(self.binary), '0' * 64, out)
        self.assertFalse((out / 'native').exists())
        for path, digest in (('relative/claude', self.declared), (str(self.binary), 'ABC')):
            with self.assertRaisesRegex(ValueError, '^original_native_declaration$'):
                native_copy.retain(path, digest, out)


class BoundedCopyAndVersionTests(Fixture):
    def out(self):
        out = self.root / 'out'
        out.mkdir()
        return out

    def test_source_growing_after_the_hash_refuses_without_writing_past_the_hashed_size(self):
        size = self.binary.stat().st_size
        copy = self.root / 'partial'
        with open(self.binary, 'a') as grow:
            grow.write('# grown after hashing\n' * 1000)
        with self.assertRaisesRegex(ValueError, '^original_native_mismatch$'):
            native_copy._copy(str(self.binary), copy, size)
        self.assertLessEqual(copy.stat().st_size, size)

    def test_destination_open_failure_leaks_no_descriptor(self):
        destination = self.root / 'existing'
        destination.write_text('already here')
        before = len(os.listdir('/proc/self/fd'))
        with self.assertRaises(FileExistsError):
            native_copy._copy(str(self.binary), destination, self.binary.stat().st_size)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_retain_removes_the_copy_when_the_source_grows_between_hash_and_copy(self):
        out, real = self.out(), native_copy._hash

        def hash_then_grow(path):
            result = real(path)
            if Path(path) == self.binary:
                with open(self.binary, 'a') as grow:
                    grow.write('# grown\n')
            return result
        with patch.object(native_copy, '_hash', side_effect=hash_then_grow):
            with self.assertRaisesRegex(ValueError, '^original_native_mismatch$'):
                native_copy.retain(str(self.binary), self.declared, out)
        self.assertFalse((out / 'native/claude').exists())

    def refuses_version(self, program, code, **patches):
        self.binary.write_text('#!/usr/bin/env python3\nimport sys, time\n' + program + '\n')
        out = Path(tempfile.mkdtemp(dir=self.root))
        with patch.multiple(native_copy, **patches) if patches else contextlib.nullcontext():
            with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                native_copy.retain(str(self.binary), sha256(self.binary), out)
        self.assertFalse((out / 'native/claude').exists())
        custody = json.loads((out / 'native/version-custody.json').read_text())
        self.assertTrue(custody['leader_reaped'])
        return custody

    def test_oversized_version_stdout_or_stderr_is_a_bounded_refusal_with_the_child_reaped(self):
        self.refuses_version("sys.stdout.write('v' * 5000)", 'original_native_version_unbounded')
        custody = self.refuses_version("sys.stderr.write('e' * 5000); print('fake 1.0')",
                                       'original_native_version_unbounded')
        self.assertEqual(custody['stderr_bytes'], 5000)

    def test_version_timeout_is_refused_and_reaped(self):
        custody = self.refuses_version('time.sleep(30)', 'original_native_version_unobserved', VERSION_TIMEOUT=1)
        self.assertEqual(custody['classification'], 'timeout')


class ObservationShapeTests(unittest.TestCase):
    """Pure comparator: the retained-copy observation must have the normal producer's shape."""

    def setUp(self):
        import trial_original_execution as original
        self.original = original
        native = dict(path='/declared/claude', sha256='c' * 64)
        identity = dict(model_sha256='a' * 64, algorithm='synthetic-embedding/1')
        self.fragment = dict(native=native, model='m', effort='high', identity=dict(identity, dimensions=384))
        self.plan = dict(model='m', reasoning_effort='high',
                         original_native=dict(declared=dict(native), observed=dict(
                             source='/run/out/native/claude', sha256='c' * 64, version='fake 1.0',
                             observed_at='2026-10-09T19:59:30Z', target='/home/x/.local/bin/claude')),
                         memory_backends={arm: dict(readiness=dict(identity=dict(identity))) for arm in ('CURRENT', 'CAND')})
        self.records = [dict(model='m', original_native=dict(binary_sha256='c' * 64, effort='high',
                                                             target='/home/x/.local/bin/claude'))]

    def check(self, **observed):
        plan = json.loads(json.dumps(self.plan))
        for key, value in observed.items():
            if value is None:
                plan['original_native']['observed'].pop(key)
            else:
                plan['original_native']['observed'][key] = value
        return self.original.check_observed(plan, self.records, self.fragment)

    def test_valid_observation_passes(self):
        self.assertIsNone(self.check())

    def test_missing_or_malformed_source_and_observed_at_are_rejected(self):
        malformed = ('rejected', 'original_execution_observation_malformed')
        for change in (dict(source=None), dict(observed_at=None), dict(source='relative/native/claude'),
                       dict(source=7), dict(source='/home/x/.local/bin/claude', target='/home/x/.local/bin/claude'),
                       dict(observed_at='yesterday'), dict(observed_at=1)):
            with self.subTest(change=change):
                self.assertEqual(self.check(**change), malformed)

    def test_non_object_backend_values_are_rejected_not_raised(self):
        for value in ('string', 3, ['list'], dict(readiness='string'), dict(readiness=dict(identity='string'))):
            with self.subTest(value=value):
                plan = json.loads(json.dumps(self.plan))
                plan['memory_backends']['CURRENT'] = value
                self.assertEqual(self.original.check_observed(plan, self.records, self.fragment),
                                 ('rejected', 'original_execution_observation_malformed'))
        plan = json.loads(json.dumps(self.plan))
        plan['memory_backends'] = 'string'
        self.assertEqual(self.original.check_observed(plan, self.records, self.fragment),
                         ('rejected', 'original_execution_observation_malformed'))


class SpecialFileTests(Fixture):
    """Each case runs in a child with a short timeout so a blocking open cannot hang the suite."""

    def child(self, program):
        code = ('import sys; sys.path.insert(0, %r)\nimport trial_native_copy as native_copy\n' % str(Path(__file__).parent)
                + program)
        done = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=10,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        return done.stdout.strip()

    def test_fifo_source_refuses_without_blocking(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        result = self.child(f"""
try:
    native_copy.file_sha256({str(fifo)!r})
except ValueError as error:
    print(error)""")
        self.assertEqual(result, 'original_native_not_regular')

    def test_fifo_swapped_in_between_hash_and_copy_refuses_without_blocking(self):
        out = self.root / 'out'
        out.mkdir()
        result = self.child(f"""
import os
real = native_copy._hash
def hash_then_swap(path):
    result = real(path)
    os.unlink(path)
    os.mkfifo(path)
    return result
native_copy._hash = hash_then_swap
try:
    native_copy.retain({str(self.binary)!r}, {self.declared!r}, {str(out)!r})
except ValueError as error:
    print(error, os.path.exists({str(out / 'native/claude')!r}))""")
        self.assertEqual(result, 'original_native_not_regular False')

    def test_character_device_refuses(self):
        if not Path('/dev/null').exists():
            self.skipTest('no /dev/null')
        result = self.child("""
try:
    native_copy.file_sha256('/dev/null')
except ValueError as error:
    print(error)""")
        self.assertEqual(result, 'original_native_not_regular')


class RunAgentBoundaryTests(Fixture):
    def setUp(self):
        super().setUp()
        source = self.root / 'source'
        source.mkdir()
        (source / 'synthetic.txt').write_text('original\n')
        (source / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm initial\n')
        self.case = dict(id='synthetic', workspace='synthetic', _prospective_workspace=str(source),
                         copy_to='repo', cwd='repo', category='synthetic', provenance={'type': 'synthetic'},
                         expected=[], wordings={'task': 'Make a synthetic change.'},
                         correct=[dict(type='unchanged', path='synthetic.txt')], mistake=[])
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'memory.py').write_text('# synthetic hook\n')
        self.stores = {'CURRENT': dict(store=Store(self.root), hook=str(hook / 'memory.py'), configuration={})}
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)
        self.retained = native_copy.retain(str(self.binary), self.declared, self.out)
        self.args = SimpleNamespace(harness='claude', model='synthetic-model', wording='task', timeout=20, max_turns=2,
                                    semantic_recall=[], original_limits=True, selector_model='inert',
                                    reasoning_effort='xhigh', _original_native=self.retained)
        self.log = self.root / 'fake.log'
        self.commands = []

    def run_agent(self):
        real = task.sandbox_command

        def emulate(root, cwd, binds, env, argv, harness, **kwargs):
            command = real(root, cwd, binds, env, argv, harness, **kwargs)
            self.commands.append(dict(command=command, argv=list(argv)))
            hookstate = next(source for source, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            mounts = {command[i + 2]: (command[i], command[i + 1]) for i in range(len(command) - 2)
                      if command[i] in ('--bind', '--ro-bind')}
            _, source = mounts[argv[0]]
            return ['env', f'FAKE_LOG={self.log}', f'FAKE_HOOKSTATE={hookstate}', source, *argv[1:]]
        with patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'sandbox_command', side_effect=emulate):
            return task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)

    def test_retained_bytes_run_at_the_sandbox_target_with_the_declared_effort(self):
        record = self.run_agent()
        [launch] = self.commands
        command, argv = launch['command'], launch['argv']
        target = str(self.home / '.local/bin/claude')
        self.assertEqual(argv[0], target)
        self.assertEqual(argv[argv.index('--effort') + 1], 'xhigh')
        self.assertEqual(argv[argv.index('--model') + 1], 'synthetic-model')
        mounts = {command[i + 2]: (command[i], command[i + 1]) for i in range(len(command) - 2)
                  if command[i] in ('--bind', '--ro-bind')}
        mode, source = mounts[target]
        self.assertEqual((mode, source), ('--ro-bind', str(self.out / 'native/claude')))
        self.assertEqual(sha256(source), self.declared)
        # The existing legacy credentials bind is unchanged: read-write, ambient unless an account is selected.
        credentials = str(self.home / '.claude/.credentials.json')
        self.assertEqual(mounts[credentials], ('--bind', credentials))
        [executed] = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(executed['executable'], source)
        self.assertEqual(executed['argv'][executed['argv'].index('--effort') + 1], 'xhigh')
        self.assertEqual(record['original_native'], dict(binary_sha256=self.declared, effort='xhigh', target=target))
        self.assertEqual(record['model'], 'synthetic-model')

    def test_copy_changed_before_launch_never_starts_the_actor(self):
        copy = Path(self.retained['observed']['source'])
        copy.chmod(0o700)
        copy.write_text('#!/bin/sh\necho changed\n')
        with self.assertRaisesRegex(RuntimeError, 'original native copy changed'):
            self.run_agent()
        self.assertEqual(self.commands, [])

    def test_without_original_native_the_legacy_launch_is_unchanged(self):
        self.args._original_native = None
        claude = self.home / '.local/bin/claude'
        claude.parent.mkdir(parents=True)
        claude.write_text(FAKE)
        claude.chmod(0o755)
        real = task.sandbox_command
        captured = []

        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            captured.append((real(root, cwd, binds, env, argv, harness, **kwargs), list(argv), kwargs))
            hookstate = next(source for source, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            return ['env', f'FAKE_LOG={self.log}', f'FAKE_HOOKSTATE={hookstate}', str(claude), *argv[1:]]
        with patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'sandbox_command', side_effect=inert):
            record = task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)
        command, argv, kwargs = captured[0]
        self.assertEqual(argv[0], 'claude')
        self.assertNotIn('--effort', argv)
        self.assertEqual(kwargs, {})
        self.assertNotIn('original_native', record)


class CommandTests(Fixture):
    def agent(self, *extra):
        with patch('sys.stderr', io.StringIO()):
            return task.main(['agent', '--legacy-fixtures', '--output', str(self.root / 'never'), '--arms', *extra])

    def test_cli_requires_structured_pins_original_limits_and_explicit_effort(self):
        pins = ['--original-native-path', str(self.binary), '--original-native-sha256', self.declared]
        for extra in (pins[:2], pins[2:], pins + ['--reasoning-effort', 'high'],
                      pins + ['--original-limits'], pins + ['--original-limits', '--reasoning-effort', 'ultra']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.agent(*extra)
        self.assertFalse((self.root / 'never').exists())

    def test_cmd_agent_retains_the_copy_before_stores_and_never_runs_the_ambient_binary(self):
        store = Mock(backend='lexical', readiness=None, env={}, binary=Path('/bin/true'))
        store.root = store.home = self.root / 'store'
        store.root.mkdir()
        store.token_file = store.root / 'agent.token'
        store.ids = collections.defaultdict(lambda: 'synthetic-record')
        hook = self.root / 'memory.py'
        hook.write_text('# synthetic\n')
        order = []
        args = argparse.Namespace(model='synthetic-model', harness='claude', wording='task', cases=None, first_seed=0,
                                  seeds=1, paired_plan=[], arms=[], reasoning_effort='max', semantic_worker=None,
                                  embedding_worker=None, distractors=0, memory=[f'CURRENT:/bin/true:{hook}'],
                                  output=str(self.root / 'out'), semantic_recall=[], selector_model='inert',
                                  cold_readiness_timeout=10, parallel=1, max_turns=2, timeout=10, original_limits=True,
                                  original_native_path=str(self.binary), original_native_sha256=self.declared)
        run = Mock(return_value=Mock(stdout=b'ambient'))

        def trial_store(*a, **k):
            order.append(('store', (self.root / 'out/native/claude').exists()))
            return store

        def attempt(case, arm, seed, order_, args, stores, out):
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case['id'], arm=arm, seed=seed, outcome='correct')
        with patch.object(task, 'TrialStore', side_effect=trial_store), \
                patch.object(task, 'verify_frozen', return_value=dict(cases_sha256='c', corpus_sha256='k', labels_sha256='l')), \
                patch.object(task, 'load_cases', return_value=[dict(id='synthetic-a')]), \
                patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'run_agent', side_effect=attempt), \
                patch.object(task, 'cairn_json', return_value={}), patch.object(task, 'run', run), \
                contextlib.redirect_stdout(io.StringIO()):
            task.cmd_agent(args)
        self.assertEqual(order, [('store', True)])
        self.assertNotIn(['claude', '--version'], [call.args[0] for call in run.call_args_list])
        plan = json.loads((self.root / 'out/plan.json').read_text())
        self.assertEqual(plan['original_native']['declared'], dict(path=str(self.binary), sha256=self.declared))
        self.assertEqual(plan['original_native']['observed']['sha256'], self.declared)
        self.assertEqual(plan['harness_version'], 'fake-claude 9.9.9 (synthetic)')
        self.assertEqual(plan['reasoning_effort'], 'max')

    def test_cmd_agent_refuses_a_wrong_binary_before_any_store(self):
        args = argparse.Namespace(model='m', harness='claude', wording='task', cases=None, first_seed=0, seeds=1,
                                  paired_plan=[], arms=[], reasoning_effort='high', semantic_worker=None,
                                  embedding_worker=None, distractors=0, memory=[], output=str(self.root / 'out'),
                                  semantic_recall=[], selector_model='inert', cold_readiness_timeout=10, parallel=1,
                                  max_turns=2, timeout=10, original_limits=True,
                                  original_native_path=str(self.binary), original_native_sha256='0' * 64)
        with patch.object(task, 'TrialStore', side_effect=AssertionError('no store may start')), \
                patch.object(task, 'verify_frozen', return_value=dict(cases_sha256='c', corpus_sha256='k', labels_sha256='l')), \
                patch.object(task, 'load_cases', return_value=[dict(id='synthetic-a')]), \
                patch.object(task, 'run', Mock(return_value=Mock(stdout=b'ambient'))):
            with self.assertRaisesRegex(ValueError, '^original_native_mismatch$'):
                task.cmd_agent(args)


class ReadinessIdentityTests(unittest.TestCase):
    def test_readiness_retains_the_backend_identity_from_the_same_discovery_response(self):
        store = object.__new__(task.TrialStore)
        store.embedding_worker, store.backend = '/fixture/worker', 'embedding-command'
        store.root, store.server = Path('/tmp/fixture'), Mock()
        store.server.poll.return_value = None
        store.started = task.time.monotonic()
        identity = dict(model_sha256='a' * 64, algorithm='synthetic-embedding/1')
        store.agent = Mock(return_value=dict(discovery=dict(state='ready', coverage=dict(indexed=2, eligible=2), **identity)))
        with patch.object(task.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            store.wait_for_full_coverage(timeout=10)
        self.assertEqual(store.readiness['identity'], identity)


if __name__ == '__main__':
    unittest.main()
