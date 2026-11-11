"""Selected OpenCode plugins and shell rows against real installed Go/Source."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, encoded, write
from test_r02_compatibility_install import InstalledTools
import test_r04_hook_settings as hooks

pin = hooks.pin


class UnboundFixture(hooks.HookFixture):
    def configuration_plan(self, **kwargs):
        plan = super().configuration_plan(**kwargs)
        plan.update(profile_mode='unbound', enabled=False)
        for config, selected in [(self.memory, plan['memory_config']), (self.coord, plan['coordination_configs'][0])]:
            config['inbox_recall_binding'] = None
            # Preserve whitespace, a non-ASCII value and an opaque large int.
            raw = (json.dumps(dict(config, opaque=['café', 900719925474099312345]), indent=2) + '\n').encode()
            write(Path(selected['path']), raw)
            selected['sha256'] = hashlib.sha256(raw).hexdigest()
        return plan


class R04HostWiringTests(InstalledTools, unittest.TestCase):
    stage = hooks.R04HookSettingsTests.stage
    recall_api = hooks.R04HookSettingsTests.recall_api

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name, harness='opencode', bound=False):
        cls = hooks.HookFixture if bound else UnboundFixture
        f = cls(self.root / name, self.binary, stager=self.stager, harness=harness)
        # The external API fixture must accept the plugin's real disposal call.
        f.api.chmod(0o700)
        write(f.api, f.api.read_bytes().replace(b"elif op!='search':",
                                              b"elif op=='agent-leave': data={}\nelif op!='search':"), 0o500)
        f.renderer = f.package / 'stage_lifecycle_hooks.py'
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_hooks.py', f.renderer)
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_compatibility.py', f.package / 'stage_lifecycle_compatibility.py')
        f.settings = f.account / 'settings.json'
        write(f.settings, b'{"unrelated":{"keep":true},"python":"unused","script":"unused","engine_config":"unused"}\n')
        f.hook_root = f.root / 'hook-generations'
        f.hook_root.mkdir(mode=0o700)
        f.hook_plan = dict(id=str(uuid.uuid4()), selection=f.selected, harness=harness, account_home=str(f.account),
                           settings=pin(f.settings), coordination_index=0, replace_commands=dict(memory=[], coordination=[]))
        if harness == 'opencode':
            f.coord_settings = f.account / 'cairn-coordination.json'
            write(f.coord_settings, b'{"unrelated":"coordination","python":"unused","script":"unused","config":"unused"}\n')
            plugins = {}
            for role, name in [('memory', 'lifecycle'), ('coordination', 'coordination')]:
                target = f.package / (name + '.ts')
                shutil.copyfile(ROOT / 'integrations/opencode' / target.name, target)
                target.chmod(0o600)
                plugins[role] = pin(target)
            f.hook_plan.update(coordination_settings=pin(f.coord_settings), plugins=plugins)
        return f

    def selected(self, f):
        result = self.stage(f)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        selected = json.loads(result.stdout)
        directory = Path(selected['selection']['path']).parent
        record = json.loads((directory / 'hook-selection.json').read_bytes())
        for path in directory.iterdir():
            if path.is_file(): self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        return selected, directory, record

    def test_opencode_go_recall_and_coordination_keep_source_capture_and_reverse_inputs(self):
        f = self.fixture('ordinary host')
        calls = self.recall_api(f)
        selected, directory, record = self.selected(f)
        self.assertEqual((directory / 'previous-cairn-lifecycle.json').read_bytes(), f.settings.read_bytes())
        self.assertEqual((directory / 'previous-cairn-coordination.json').read_bytes(), f.coord_settings.read_bytes())
        for selected_config, input_pin in [(f.memory_path, f.plan['memory_config']), (f.coord_path, f.plan['coordination_configs'][0])]:
            self.assertEqual(selected_config.read_bytes(), Path(input_pin['path']).read_bytes())
        implementations = {(r['role'], r['event']): r['implementation'] for r in record['rows']}
        self.assertEqual({key for key, impl in implementations.items() if impl == 'go'},
                         {('memory', 'SessionStart'), ('memory', 'UserPromptSubmit')})
        self.assertTrue(all(r['implementation'] == 'python' for r in record['rows'] if r['role'] == 'coordination'))
        self.assertTrue(all(implementations['memory', event] == 'python'
                            for event in ('PreCompact', 'SessionEnd', 'PostToolUse', 'PostToolUseFailure')))
        # Arrange the documented staged layout only in a disposable account.
        home = f.root / 'installed home with spaces'
        (home / 'plugins').mkdir(parents=True, mode=0o700)
        for name, relative in selected['layout'].items():
            shutil.copyfile(directory / name, home / relative)
        self.assertEqual(json.loads((home / 'cairn-lifecycle.json').read_bytes())['unrelated'], {'keep': True})
        # No staged command may need the installer package or its source tree.
        shutil.rmtree(f.package)
        result = subprocess.run(['node', '--experimental-strip-types', str(ROOT / 'scripts/run_r04_opencode_hooks.mjs'),
                                 str(home), str(f.root)], env=f.env(), cwd=f.root, capture_output=True, timeout=45)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        self.assertEqual(json.loads(result.stdout), {'memory': True, 'coordination': True})
        self.assertIn('index', [operation for operation, _ in calls])
        self.assertTrue(all(auth == 'Bearer r04-synthetic-token' for _, auth in calls))
        states = [json.loads(path.read_bytes()) for path in Path(f.coord['state_dir']).glob('*.json')]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]['agent']['agent_id'], '33333333-3333-4333-8333-333333333333')
        memory_state = json.loads((Path(f.memory['state_dir']) / 'ses_wiring.json').read_bytes())
        self.assertIn(memory_state['last_capture']['outcome'], ('courtesy', 'unchanged'))

    def test_shell_transport_runs_go_opencode_recall_and_keeps_other_harnesses_on_source(self):
        for harness in ('opencode', 'codex', 'claude'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness=harness)
                if harness == 'opencode': self.recall_api(f)
                _, _, record = self.selected(f)
                memory = next(r for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'SessionStart')
                coord = next(r for r in record['rows'] if r['role'] == 'coordination')
                self.assertEqual((memory['implementation'], coord['implementation']),
                                 ('go' if harness == 'opencode' else 'python', 'python'))
                session = 'ses_shell' if harness == 'opencode' else SESSION
                event = dict(hook_event_name='SessionStart', session_id=session, cwd=str(f.root), host_pid=os.getpid())
                shutil.rmtree(f.package)
                result = subprocess.run(['/bin/sh', '-c', memory['hook']['command']], input=encoded(event),
                                        cwd=f.root, env=f.env(), capture_output=True, timeout=20)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_bound_and_provider_cells_never_silently_select_ordinary_go(self):
        for route in ('bound', 'provider'):
            with self.subTest(route=route):
                f = self.fixture(route, bound=route == 'bound')
                if route == 'provider':
                    f.memory['recall_mode'] = 'ambient'
                    f.publish_configuration()
                    f.hook_plan['selection'] = f.selected
                _, _, record = self.selected(f)
                rows = [r for r in record['rows'] if r['role'] == 'memory']
                self.assertTrue(all(r['implementation'] == 'python' for r in rows))
                self.assertTrue(any(r['reason'] == ('bound callback' if route == 'bound' else 'provider-selected recall') for r in rows))
                if route == 'bound': self.assertTrue(all(r['implementation'] == 'python' for r in record['rows']))

    def test_unbound_mode_refuses_a_bound_input_and_cannot_enable_binding(self):
        f = self.fixture('refusal')
        for change in ('bound', 'enabled'):
            with self.subTest(change=change):
                plan = copy.deepcopy(f.plan)
                plan['id'] = str(uuid.uuid4())
                if change == 'enabled': plan['enabled'] = True
                else:
                    config = json.loads(Path(plan['memory_config']['path']).read_bytes())
                    config['inbox_recall_binding'] = '/selected/old-binding.json'
                    path = f.root / 'bound-input.json'
                    write(path, encoded(config)); plan['memory_config'] = pin(path)
                result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((f.install_root / 'generations' / ('selection-' + plan['id'])).exists())

    def test_unsupported_harness_and_changed_plugin_or_selection_are_refused(self):
        for kind in ('agy', 'plugin', 'selection'):
            with self.subTest(kind=kind):
                f = self.fixture(kind)
                plan = copy.deepcopy(f.hook_plan)
                if kind == 'agy': plan['harness'] = 'agy'
                elif kind == 'plugin': plan['plugins']['memory']['sha256'] = '0' * 64
                else: plan['selection']['sha256'] = '0' * 64
                result = self.stage(f, plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((f.hook_root / ('selection-' + plan['id'])).exists())

    def test_changed_unbound_profile_refuses_before_source_fallback(self):
        f = self.fixture('changed profile')
        _, _, record = self.selected(f)
        row = next(r for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'PostToolUse')
        f.memory_path.chmod(0o600)
        write(f.memory_path, encoded(dict(f.memory, repo='changed-after-selection')), 0o400)
        event = dict(hook_event_name='PostToolUse', session_id='ses_changed', cwd=str(f.root),
                     tool_name='read', tool_input=dict(file_path=str(f.root / 'store.go')))
        result = subprocess.run(row['argv'], input=encoded(event), cwd=f.root, env=f.env(),
                                capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((Path(f.memory['state_dir']) / 'ses_changed.json').exists())
        self.assertEqual(f.api_calls(), [])


if __name__ == '__main__':
    unittest.main()
