"""Explicit absent-memory selections through the maintained offline publishers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

import stage_lifecycle_compatibility as installation
import stage_lifecycle_hooks as hooks
from test_f04_activation import ROOT, compat, encoded, module, overwrite_fixture, sha
from test_r02_compatibility_install import InstalledTools
from test_r04_hook_settings import HookFixture, pin


class CoordinationOnlyInstallationTests(InstalledTools, unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='coordination-only-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, name, harness='codex'):
        return HookFixture(self.root / name, self.binary, stager=self.stager,
                           harness=harness, coordination_only=True)

    def hook_plan(self, f):
        settings = f.root / 'settings.json'
        settings.write_bytes(encoded({'custom': ['preserved'], 'hooks': {
            'PreCompact': [{'hooks': [{'type': 'command', 'command': 'unrelated', 'timeout': 3}]}]}}))
        settings.chmod(0o600)
        return dict(id=str(uuid.uuid4()), selection=f.selected, harness=f.coord['harness'],
                    account_home=str(f.account), settings=pin(settings), coordination_index=0,
                    replace_commands={'memory': [], 'coordination': []})

    def test_installed_receipt_and_launchers_have_no_memory_role(self):
        for harness in ('codex', 'claude', 'agy', 'opencode'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness)
                self.assertEqual(f.receipt['memory_config'], {'absent': True})
                self.assertFalse(f.memory_path.exists())
                self.assertEqual(set(f.installed['launchers']), {'coordination:1:2', 'coordination:1:15'})
                result = f.installer_call('inspect', '--path', f.selected['path'], '--sha256', f.selected['sha256'])
                self.assertEqual((result.returncode, result.stderr), (0, b''))

    def test_missing_implicit_or_contradictory_memory_selection_refuses(self):
        f = self.fixture('refusals')
        for invalid in (None, {}, {'absent': False}, {'absent': 1}, {'absent': True, 'path': '/unused'}):
            with self.subTest(invalid=invalid):
                plan = f.configuration_plan()
                plan['memory_config'] = invalid
                result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((f.install_root / 'generations' / ('selection-' + plan['id'])).exists())
        for change in ({'enabled': True}, {'profile_mode': 'bound'}):
            plan = f.configuration_plan()
            plan.update(change)
            self.assertNotEqual(f.installer_call('stage', '--root', str(f.install_root), body=plan).returncode, 0)
        plan = f.configuration_plan()
        del plan['memory_config']
        self.assertNotEqual(f.installer_call('stage', '--root', str(f.install_root), body=plan).returncode, 0)

    def invoke(self, f, role):
        vector = compat.launch_vector(sys.executable, f.receipt_path, f.receipt_sha, role,
                                      role + '-hook.py', f.coord_path, 13 if role == 'memory' else 15,
                                      f.launch_root)
        launcher = compat.launcher_bytes(vector)
        directory = f.launch_root / sha(launcher)
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / 'inbox'
        if not path.exists():
            path.write_bytes(launcher)
            path.chmod(0o500)
        tail = [] if role == 'memory' else ['hook', '--config', str(f.coord_path)]
        return [subprocess.run(argv, input=f.raw, capture_output=True, timeout=20, cwd=f.root, env=f.env())
                for argv in ([*vector, '--', *tail], [str(f.binary), role, *vector[5:], '--', *tail])]

    def assert_preflight_refused_without_api(self, f, results):
        for result in results:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(b'compatibility_preflight', result.stderr)
        self.assertFalse(f.api.with_suffix('.calls').exists())
        self.assertFalse(Path(f.coord['state_dir']).exists())

    def test_memory_invocation_refuses_in_python_and_go_before_api(self):
        f = self.fixture('memory-refusal')
        self.assert_preflight_refused_without_api(f, self.invoke(f, 'memory'))

    def test_coordination_invocation_reaches_source_and_go_without_memory_state(self):
        f = self.fixture('coordination-positive')
        f.event.update(hook_event_name='SessionStart', prompt='', host_pid=os.getpid())
        f.raw = encoded(f.event)
        for result in self.invoke(f, 'coordination'):
            self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        self.assertIn('agent-register', f.api.with_suffix('.calls').read_text())
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_census_and_public_control_inspect_cover_coordination_state_only(self):
        import check_r06_agy_readback as readback
        observer = Path(self.build.name) / 'coordination-only-observer'
        subprocess.run(['go', 'test', '-c', '-o', str(observer), './internal/lifecycle/coordination'],
                       cwd=ROOT, check=True, timeout=120)
        evidence = self.root / 'census'
        evidence.mkdir(mode=0o700)
        readback.run(dict(control=self.binary, stager=self.stager, observer=observer), evidence,
                     association_only=True, coordination_only=True)
        f = json.loads((evidence / 'go-unit-fixture.json').read_bytes())
        self.assertFalse((Path(f['Root']) / 'memory-state').exists())
        result = subprocess.run([str(observer), '-test.run=^TestR06UnboundControlPreflightProcess$', '-test.v'],
                                env=dict(os.environ, CAIRN_R06_UNBOUND_FIXTURE=str(evidence / 'go-unit-fixture.json')),
                                cwd=ROOT, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'--- PASS: TestR06UnboundControlPreflightProcess', result.stdout)
        self.assertFalse((Path(f['Root']) / 'memory-state').exists())
        typed = self.root / 'typed-census'
        typed.mkdir(mode=0o700)
        readback.run(dict(control=self.binary, stager=self.stager, observer=observer), typed,
                     typed=True, coordination_only=True)
        refusal = json.loads((typed / 'gap-result.json').read_bytes())
        self.assertEqual(refusal['actual_error'], readback.AGY_GAP)
        self.assertEqual(refusal['native_executions'], 0)

    def test_receipt_manifest_disagreement_refuses_before_coordination_effects(self):
        f = self.fixture('mismatch')
        binding = json.loads(f.binding.read_bytes())
        binding['memory_config'] = {'absent': False}
        overwrite_fixture(f.binding, encoded(binding))
        f.receipt['binding']['sha256'] = sha(f.binding.read_bytes())
        overwrite_fixture(f.receipt_path, encoded(f.receipt))
        f.receipt_sha = sha(f.receipt_path.read_bytes())
        self.assert_preflight_refused_without_api(f, self.invoke(f, 'coordination'))

    def test_codex_and_agy_render_only_coordination_and_preserve_unrelated_hooks(self):
        for harness in ('codex', 'agy'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness)
                plan = self.hook_plan(f)
                destination = f.root / 'hook-generations'
                destination.mkdir(mode=0o700)
                result = hooks.stage(str(f.stager), destination, plan)
                metadata = json.loads((Path(result['selection']['path']).parent / 'hook-selection.json').read_bytes())
                self.assertEqual({row['role'] for row in metadata['rows']}, {'coordination'})
                settings = json.loads(Path(result['settings']['path']).read_bytes())
                self.assertEqual(settings['custom'], ['preserved'])
                self.assertEqual(settings['hooks']['PreCompact'][0]['hooks'][0]['command'], 'unrelated')
                if harness == 'codex':
                    commands = [row['command'] for groups in settings['hooks'].values() for group in groups for row in group['hooks']]
                    self.assertEqual(len(commands), len(metadata['rows']) + 1)

    def test_opencode_coordination_only_does_not_publish_a_memory_plugin(self):
        f = self.fixture('opencode-only', 'opencode')
        plan = self.hook_plan(f)
        coordination_settings = f.root / 'coordination-settings.json'
        coordination_settings.write_bytes(encoded({'custom': 'preserved'}))
        coordination_settings.chmod(0o600)
        plugin = f.package / 'coordination.ts'
        shutil.copyfile(ROOT / 'integrations/opencode/coordination.ts', plugin)
        plugin.chmod(0o600)
        plan.update(coordination_settings=pin(coordination_settings),
                    plugins={'coordination': pin(plugin)})
        destination = f.root / 'hook-generations'
        destination.mkdir(mode=0o700)
        result = hooks.stage(str(f.stager), destination, plan)
        self.assertNotIn('cairn-lifecycle.json', result['files'])
        self.assertNotIn('cairn-lifecycle.ts', result['files'])
        self.assertEqual(json.loads(Path(result['files']['cairn-coordination.json']['path']).read_bytes())['custom'], 'preserved')

    def retained(self, f):
        source = f.root / 'retained-inputs'
        bundle = module('coordination_only_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
        bundle.stage_runtime(ROOT / 'integrations/lifecycle', source)
        identity = str(uuid.uuid4())
        root = f.root / 'retained-generations'
        root.mkdir(mode=0o700)
        final = root / ('selection-' + identity)
        profile = source / 'coordination.json'
        profile.write_bytes(encoded(f.coord))
        profile.chmod(0o600)
        descriptor = lambda name: dict(path=str(final / name), sha256=sha((source / name).read_bytes()))
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False, memory_config={'absent': True},
                        coordination_configs=[descriptor('coordination.json')],
                        engine=dict(**descriptor('memory.py'), bundle=bundle.bundle_descriptor(source)),
                        bridge=dict(**descriptor('inbox_recall.py'), bundle=bundle.bundle_descriptor(source)))
        (source / 'binding.json').write_bytes(encoded(manifest))
        (source / 'retained-argv.json').write_bytes(encoded({'coordination': [sys.executable, '-I', '-B',
            str(final / 'coordination.py'), 'hook', '--config', str(final / 'coordination.json')]}))
        for path in source.iterdir():
            path.chmod(0o600)
        files = [dict(name=p.name, **pin(p)) for p in source.iterdir()]
        return installation.backend(str(f.stager), ['generation', '--root', str(root)], dict(id=identity, files=files))

    def test_retained_two_inspection_and_codex_hooks_keep_explicit_absence(self):
        f = self.fixture('retained')
        selected = self.retained(f)
        inspected = installation.inspect_retained(str(f.stager), selected)
        self.assertEqual(inspected['memory_config'], {'absent': True})
        self.assertEqual(set(inspected['launchers']), {'coordination'})
        plan = self.hook_plan(f)
        plan.update(selection=selected, legacy=True)
        rows, profiles, _ = hooks.hook_materials(str(f.stager), plan)
        self.assertEqual({row['role'] for row in rows}, {'coordination'})
        self.assertEqual(profiles['memory'], {'absent': True})


if __name__ == '__main__':
    unittest.main()
