"""Synthetic original per-arm recall controls and embedding identity preflight; no provider, model or worker."""
import argparse
import collections
import contextlib
import hashlib
import io
import json
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import trial_original_arms as controls
import trial_task_eval as task

ACTOR = r'''
import json, sys
from pathlib import Path
hookstate = Path(sys.argv[1])
rows = [dict(schema='cairn.task-hook-observation/1', event=event, recall_attempted=False, recall=None, exit_code=0,
             stdout_bytes=40, stderr_bytes=0, process_seconds=0.1,
             memory_call_attempts={'search': 1, 'pull': 0, 'pull-evidence': 0, 'history': 0, 'other': 0})
        for event in ('SessionStart', 'UserPromptSubmit')]
(hookstate / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
print(json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text='Done.')]))))
print(json.dumps(dict(type='result', result='Done.', is_error=False, num_turns=1)))
'''


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.recall = {}
        for label in ('CURRENT', 'CAND'):
            folder = self.root / 'recall' / label
            folder.mkdir(parents=True)
            (folder / 'memory.py').write_text(f'# synthetic {label} hook\nRECALL_TASK_VERSION = 1\n')
            (folder / 'inbox_recall.py').write_text(f'# synthetic {label} bridge\n')
            self.recall[label] = folder
        self.arms = dict(CURRENT=dict(recall_mode='agent_tools', semantic_fallback=True),
                         CAND=dict(recall_mode='ambient', semantic_fallback=False))
        self.memory = [f'{label}:/bin/true:{self.recall[label] / "memory.py"}' for label in ('CURRENT', 'CAND')]

    def controls(self, **changes):
        document = dict(schema=controls.SCHEMA, arms={
            label: dict(self.arms[label], recall_revision='r' * 40, hook=pin(self.recall[label] / 'memory.py'),
                        bridge=pin(self.recall[label] / 'inbox_recall.py')) for label in ('CURRENT', 'CAND')})
        for label, change in changes.items():
            document['arms'][label].update(change)
        path = self.root / 'original-arms.json'
        path.write_text(json.dumps(document))
        return str(path), pin(path)['sha256']


class LoadControlsTests(Fixture):
    def test_literal_controls_and_raw_blob_digests_are_loaded(self):
        path, digest = self.controls()
        record, configs = controls.load_controls(path, digest, self.memory)
        self.assertEqual(record['controls'], dict(path=path, sha256=digest))
        self.assertEqual(record['arms']['CURRENT'], dict(
            recall_mode='agent_tools', semantic_fallback=True, recall_revision='r' * 40,
            hook_sha256=pin(self.recall['CURRENT'] / 'memory.py')['sha256'],
            bridge_sha256=pin(self.recall['CURRENT'] / 'inbox_recall.py')['sha256']))
        self.assertEqual(configs['CAND']['recall_mode'], 'ambient')
        self.assertEqual(configs['CURRENT']['bridge'], str(self.recall['CURRENT'] / 'inbox_recall.py'))

    def test_refusals(self):
        other = self.root / 'elsewhere'
        other.mkdir()
        (other / 'inbox_recall.py').write_text('# moved\n')
        cases = [
            (dict(CAND=dict(recall_mode='ambient', semantic_fallback=True)),
             'original_execution_unsupported:semantic_fallback_selector'),
            (dict(CAND=dict(recall_mode='prospective')), 'original_arms_shape'),
            (dict(CAND=dict(semantic_fallback=1)), 'original_arms_shape'),
            (dict(CAND=dict(hook=pin(self.recall['CURRENT'] / 'memory.py'))), 'original_arms_recall_paths'),
            (dict(CAND=dict(bridge=pin(other / 'inbox_recall.py'))), 'original_arms_recall_paths'),
            (dict(CAND=dict(hook=dict(pin(self.recall['CAND'] / 'memory.py'), sha256='0' * 64))),
             'original_arms_recall_mismatch'),
        ]
        for change, code in cases:
            with self.subTest(code=code, change=change):
                path, digest = self.controls(**change)
                with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                    controls.load_controls(path, digest, self.memory)
        hook = pin(self.recall['CAND'] / 'memory.py')
        for bad in ({}, None, dict(hook, path=7), dict(hook, extra=True), dict(path=hook['path']), 'memory.py',
                    dict(hook, sha256=7)):
            with self.subTest(hook=bad):
                path, digest = self.controls(CAND=dict(hook=bad))
                with self.assertRaisesRegex(ValueError, '^original_arms_shape$'):
                    controls.load_controls(path, digest, self.memory)
        path, digest = self.controls()
        with self.assertRaisesRegex(ValueError, '^original_arms_shape$'):
            controls.load_controls(path, digest, self.memory[:1])
        with self.assertRaisesRegex(ValueError, '^original_arms_unavailable$'):
            controls.load_controls(path, '0' * 64, self.memory)

    def test_identity_declaration_is_typed_and_its_file_digest_is_only_a_pin(self):
        path = self.root / 'identity.json'
        path.write_text(json.dumps(dict(schema='cairn.embedding-model-identity/1', model_sha256='a' * 64,
                                        algorithm='synthetic/1', dimensions=384)))
        record, values = controls.load_identity(str(path), pin(path)['sha256'])
        self.assertEqual(values, dict(model_sha256='a' * 64, algorithm='synthetic/1', dimensions=384))
        self.assertEqual(record['declaration'], dict(path=str(path), sha256=pin(path)['sha256']))
        path.write_text('opaque')
        with self.assertRaisesRegex(ValueError, '^original_execution_unsupported:model_identity_declaration$'):
            controls.load_identity(str(path), pin(path)['sha256'])


class Store:
    def __init__(self, root):
        self.binary = root / 'cairn'
        self.binary.write_text('#!/bin/sh\n')
        self.sockdir = root / 'sock'
        self.sockdir.mkdir()
        self.token_file = root / 'agent.token'
        self.token_file.write_text('synthetic')
        self.names = {}


class RecallRunBase(Fixture):
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
        path, digest = self.controls()
        _, self.configs = controls.load_controls(path, digest, self.memory)
        self.stores = {label: dict(store=Store(self.root / label), hook=str(self.recall[label] / 'memory.py'),
                                   configuration=self.configs[label]) for label in ('CURRENT', 'CAND')
                       if not (self.root / label).mkdir()}
        self.args = SimpleNamespace(harness='claude', model='inert', wording='task', timeout=20, max_turns=2,
                                    semantic_recall=[], original_limits=True, selector_model='inert',
                                    _original_arms=self.configs)
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)
        self.launched = []

    def run_arm(self, arm):
        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            self.launched.append(dict(binds=binds, argv=argv))
            hookstate = next(src for src, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            return [sys.executable, '-c', ACTOR, str(hookstate)]
        with patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'sandbox_command', side_effect=inert):
            return task.run_agent(self.case, arm, 0, 0, self.args, self.stores, self.out)

    def attempt(self, arm, name):
        return self.out / f'runs/synthetic.{arm}.s0' / name


class RunAgentTests(RecallRunBase):
    def test_ambient_arm_without_fallback_is_carried_literally(self):
        record = self.run_arm('CAND')
        config = json.loads(self.attempt('CAND', 'hook-config.json').read_text())
        self.assertEqual((config['recall_mode'], config['semantic_fallback']), ('ambient', False))
        self.assertFalse(self.attempt('CAND', 'binding.json').exists())
        self.assertEqual(record['original_arm']['recall_mode'], 'ambient')

    def test_recall_source_changed_before_launch_never_starts_the_actor(self):
        (self.recall['CURRENT'] / 'inbox_recall.py').write_text('# changed\n')
        with self.assertRaisesRegex(RuntimeError, 'original recall source changed'):
            self.run_arm('CURRENT')
        self.assertEqual(self.launched, [])

    def test_ambient_fallback_never_reaches_a_selector(self):
        self.configs['CAND']['semantic_fallback'] = True
        with self.assertRaisesRegex(ValueError, 'semantic_fallback_selector'):
            self.run_arm('CAND')
        self.assertEqual(self.launched, [])



@unittest.skipUnless(shutil.which('bwrap'), 'needs the local filesystem sandbox')
class BindingPreflightTests(RecallRunBase):
    """Original agent_tools arms run their harness-specific binding check before the provider."""

    def setUp(self):
        super().setUp()
        self.home = self.root / 'home'
        (self.home / '.local/bin').mkdir(parents=True)
        (self.home / '.local/bin/claude').write_text('#!/bin/sh\n')
        (self.home / '.claude').mkdir()
        (self.home / '.claude/.credentials.json').write_text('{}')
        home = patch.object(Path, 'home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        # The real reviewed engine and bridge, so the binding check validates actual sources.
        for name in ('memory.py', 'inbox_recall.py'):
            shutil.copy2(task.ROOT / 'integrations/lifecycle' / name, self.recall['CURRENT'] / name)
            (self.recall['CURRENT'] / name).chmod(0o600)
        self.reload()

    def reload(self):
        path, digest = self.controls()
        _, self.configs = controls.load_controls(path, digest, self.memory)
        for label, store in self.stores.items():
            store['configuration'] = self.configs[label]
        self.args._original_arms = self.configs

    def run_preflighted(self, extra=()):
        real = task.sandbox_command
        self.actors = 0

        def emulate(root, cwd, binds, env, argv, harness, **kwargs):
            if argv[:2] == ['python3', '-c'] and 'sys.argv[1:]' in argv[2]:
                return real(root, cwd, binds, env, argv, harness, **kwargs)  # the real sandboxed check
            self.actors += 1
            self.actor_binds = binds
            hookstate = next(src for src, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            return [sys.executable, '-c', ACTOR, str(hookstate)]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(task, 'load_corpus', return_value=[]))
            stack.enter_context(patch.object(task, 'sandbox_command', side_effect=emulate))
            for context in extra:
                stack.enter_context(context)
            return task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)

    def test_agent_tools_arm_carries_its_literal_controls_and_binds_the_raw_recall_blobs(self):
        record = self.run_preflighted()
        self.assertEqual(self.actors, 1)
        config = json.loads(self.attempt('CURRENT', 'hook-config.json').read_text())
        self.assertEqual((config['recall_mode'], config['semantic_fallback']), ('agent_tools', True))
        self.assertNotIn('model', config)  # no selector for an agent_tools arm
        binding = json.loads(self.attempt('CURRENT', 'binding.json').read_text())
        self.assertEqual(binding['bridge']['sha256'], pin(self.recall['CURRENT'] / 'inbox_recall.py')['sha256'])
        self.assertEqual(binding['engine']['sha256'], pin(self.recall['CURRENT'] / 'memory.py')['sha256'])
        mounts = {str(target): str(source) for source, target, _ in self.actor_binds}
        self.assertEqual(mounts['/tmp/trial/hook'], str(self.recall['CURRENT']))
        self.assertIn('/tmp/trial/binding.json', mounts)
        self.assertEqual(record['original_arm'], dict(
            recall_mode='agent_tools', semantic_fallback=True, recall_revision='r' * 40,
            hook_sha256=pin(self.recall['CURRENT'] / 'memory.py')['sha256'],
            bridge_sha256=pin(self.recall['CURRENT'] / 'inbox_recall.py')['sha256']))

    def test_valid_binding_and_task_key_pass_the_real_check_then_launch_once(self):
        record = self.run_preflighted()
        check = json.loads(self.attempt('CURRENT', 'original-binding-check.json').read_text())
        self.assertEqual(check['exit'], 0)
        self.assertEqual(check['argv'], task.BINDING_CHECK)
        self.assertEqual(self.actors, 1)
        self.assertEqual(record['original_arm']['recall_mode'], 'agent_tools')

    def _configure_codex(self):
        self.args.harness = 'codex'
        self.args.original_limits = False
        self.args.codex_weekly_limits = True
        self.args.model = 'gpt-6-luna'
        self.args.reasoning_effort = 'high'
        self.args.codex_install = str(self.root / 'codex-install')
        binary = self.root / 'codex'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o700)
        self.args.codex_binary = str(binary)
        self.args.codex_binary_sha256 = pin(binary)['sha256']
        auth = self.home / '.codex' / 'auth.json'
        auth.parent.mkdir()
        auth.write_text('{}')
        self.args.codex_auth_file = str(auth)
        self.args.codex_auth_sha256 = pin(auth)['sha256']

    def _run_codex_preflighted(self):
        self._configure_codex()
        with patch.object(task.codex_account_selection, 'verify_auth_file'), \
                patch.object(task.codex_weekly_limits, 'assess', return_value=dict(
                    schema='cairn.codex-weekly-limit-assessment/1', status='within_codex_weekly_limits',
                    failures=[], unknown=[])), \
                patch.object(task, 'parse_stream', return_value=dict(
                    commands=[], writes=[], memory_calls=[], answer='Done.', turns=1,
                    completed=True, is_error=False, admission_failure=None)), \
                patch.object(task, 'read_custody', return_value=dict(
                    classification='observed', leader_reaped=True, cleanup=dict(status='observed_clear'))), \
                patch.object(task, 'codex_custody_failure', return_value=None):
            return self.run_preflighted()

    def test_codex_uses_native_turn_binding_without_claude_task_key_and_keeps_weekly_limits(self):
        record = self._run_codex_preflighted()
        check = json.loads(self.attempt('CURRENT', 'original-binding-check.json').read_text())
        self.assertEqual(check['exit'], 0)
        self.assertEqual(check['argv'], task.CODEX_BINDING_CHECK)
        self.assertEqual(self.actors, 1)
        self.assertEqual(record['codex_weekly_limits']['status'], 'within_codex_weekly_limits')
        config = json.loads(self.attempt('CURRENT', 'hook-config.json').read_text())
        self.assertEqual((config['harness'], config['recall_mode']), ('codex', 'agent_tools'))
        self.assertNotIn('recall_task_key', config)
        expected_context = task.codex_weekly_limits.hook_context_bytes(
            self.case['wordings']['task'], task.MEMORY_INSTRUCTION)
        self.assertEqual(config['context_bytes'], expected_context)
        hooks = json.loads(self.attempt('CURRENT', 'codex-home/hooks.json').read_text())['hooks']
        self.assertEqual(hooks['UserPromptSubmit'][0]['hooks'][0]['additionalContextLimit'], expected_context)
        mcp = json.loads(self.attempt('CURRENT', 'mcp.json').read_text())['mcpServers']['cairn']['args']
        self.assertEqual(mcp[-len(task.legacy_limits.mcp_tool_room_arguments()):],
                         task.legacy_limits.mcp_tool_room_arguments())

    def test_codex_refuses_before_provider_when_pinned_engine_lacks_native_turn_route(self):
        engine = self.recall['CURRENT'] / 'memory.py'
        source = engine.read_text()
        self.assertIn('def codex_agent_context(', source)
        engine.write_text(source.replace('def codex_agent_context(', 'def unsupported_codex_agent_context(', 1))
        engine.chmod(0o600)
        self.reload()
        self._configure_codex()
        with patch.object(task.codex_account_selection, 'verify_auth_file'):
            with self.assertRaisesRegex(RuntimeError, '^original_recall_binding_invalid'):
                self.run_preflighted()
        self.assertEqual(self.actors, 0)
        self.assertNotEqual(json.loads(self.attempt('CURRENT', 'original-binding-check.json').read_text())['exit'], 0)

    def test_invalid_bridge_refuses_before_the_provider(self):
        (self.recall['CURRENT'] / 'inbox_recall.py').write_text('# not the reviewed bridge\n')
        (self.recall['CURRENT'] / 'inbox_recall.py').chmod(0o600)
        self.reload()
        with self.assertRaisesRegex(RuntimeError, '^original_recall_binding_invalid'):
            self.run_preflighted()
        self.assertEqual(self.actors, 0)
        self.assertNotEqual(json.loads(self.attempt('CURRENT', 'original-binding-check.json').read_text())['exit'], 0)

    def test_invalid_recall_task_key_refuses_before_the_provider(self):
        real = task.bind_ordinary_claude

        def bad_key(base, config, bridge, **kwargs):
            binds = real(base, config, bridge, **kwargs)
            written = json.loads((base / 'hook-config.json').read_text())
            written['recall_task_key'] = 'not-a-uuid'
            (base / 'hook-config.json').write_text(json.dumps(written))
            return binds
        with self.assertRaisesRegex(RuntimeError, '^original_recall_binding_invalid'):
            self.run_preflighted(extra=(patch.object(task, 'bind_ordinary_claude', side_effect=bad_key),))
        self.assertEqual(self.actors, 0)

class IdentityPreflightTests(Fixture):
    def cmd_agent(self, observed):
        identity = self.root / 'identity.json'
        identity.write_text(json.dumps(dict(schema='cairn.embedding-model-identity/1', model_sha256='a' * 64,
                                            algorithm='synthetic/1', dimensions=384)))
        stores = []

        def trial_store(root, binary, label, **kwargs):
            store = Mock(backend='embedding-command', env={}, binary=Path('/bin/true'))
            store.root = store.home = Path(root)
            store.root.mkdir(parents=True)
            store.token_file = store.root / 'agent.token'
            store.ids = collections.defaultdict(lambda: 'synthetic-record')
            store.readiness = None if observed is None else dict(identity=dict(observed))
            stores.append(store)
            return store
        launched = []
        path, digest = self.controls()
        args = argparse.Namespace(model='inert', harness='claude', wording='task', cases=None, first_seed=0, seeds=1,
                                  paired_plan=[], arms=[], reasoning_effort=None, semantic_worker=None,
                                  embedding_worker='/fixture/launcher', distractors=0, memory=self.memory,
                                  output=str(self.root / 'out'), semantic_recall=[], selector_model='inert',
                                  cold_readiness_timeout=10, parallel=1, max_turns=2, timeout=10, original_limits=True,
                                  original_arms=path, original_arms_sha256=digest,
                                  original_embedding_identity=str(identity),
                                  original_embedding_identity_sha256=pin(identity)['sha256'])

        def attempt(case, arm, seed, order, args, stores_, out):
            launched.append(arm)
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case['id'], arm=arm, seed=seed, outcome='correct')
        with patch.object(task, 'TrialStore', side_effect=trial_store), \
                patch.object(task, 'verify_frozen', return_value=dict(cases_sha256='c', corpus_sha256='k', labels_sha256='l')), \
                patch.object(task, 'load_cases', return_value=[dict(id='synthetic-a')]), \
                patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'run_agent', side_effect=attempt), \
                patch.object(task, 'cairn_json', return_value={}), \
                patch.object(task, 'run', return_value=Mock(stdout=b'inert')), contextlib.redirect_stdout(io.StringIO()):
            task.cmd_agent(args)
        return launched, stores

    def test_matching_discovery_identity_admits_attempts_and_ignores_dimensions(self):
        launched, _ = self.cmd_agent(dict(model_sha256='a' * 64, algorithm='synthetic/1'))
        self.assertEqual(sorted(launched), ['CAND', 'CURRENT'])
        plan = json.loads((self.root / 'out/plan.json').read_text())
        self.assertEqual(plan['original_embedding_identity']['declared']['dimensions'], 384)
        self.assertEqual(plan['original_arms']['arms']['CURRENT']['recall_mode'], 'agent_tools')

    def test_mismatch_or_missing_identity_refuses_before_any_attempt(self):
        for observed in (dict(model_sha256='b' * 64, algorithm='synthetic/1'),
                         dict(model_sha256='a' * 64, algorithm='other/1'), dict(model_sha256=None, algorithm=None), None):
            with self.subTest(observed=observed):
                with self.assertRaisesRegex(ValueError, '^original_execution_identity_mismatch$'):
                    self.cmd_agent(observed)
                import shutil
                shutil.rmtree(self.root / 'out')

    def test_cli_requires_pairs_and_original_limits(self):
        path, digest = self.controls()
        for extra in (['--original-arms', path], ['--original-embedding-identity-sha256', digest],
                      ['--original-arms', path, '--original-arms-sha256', digest]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
                task.main(['agent', '--legacy-fixtures', '--output', str(self.root / 'never'), '--arms', *extra])
        self.assertFalse((self.root / 'never').exists())


if __name__ == '__main__':
    unittest.main()
