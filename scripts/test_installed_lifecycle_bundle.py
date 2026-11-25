"""Installed lifecycle entrypoints run without a checkout or provider access."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The direct Claude/Codex/OpenCode memory-hook installers are retired (staged
# generations replace them); Hermes still installs the complete memory bundle.
hermes = load('install-hermes-integration')
coordination = load('install-agent-coordination')


@unittest.skipUnless(shutil.which('bwrap'), 'installed closure verification requires bwrap')
class InstalledLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = self.root / 'synthetic.token'
        self.token.write_text('synthetic fixture only')
        self.token.chmod(0o600)
        self.config = dict(cairn='/usr/bin/false', claude='/usr/bin/false',
                           socket=str(self.root / 'unused.sock'), token_file=str(self.token),
                           repo='fixture:lifecycle-bundle')

    def isolated(self, script, *args, payload=None, failure=False):
        command = ['bwrap', '--die-with-parent', '--unshare-net']
        for directory in ('/usr', '/lib', '/lib64'):
            if Path(directory).exists():
                command += ['--ro-bind', directory, directory]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                    '--bind', str(self.root), str(self.root), '--chdir', str(self.root),
                    '--setenv', 'HOME', str(self.root), '--setenv', 'PATH', '/usr/bin:/bin',
                    '--unsetenv', 'PYTHONPATH', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                    '--', '/usr/bin/python3', str(script), *map(str, args)]
        result = subprocess.run(command, input=json.dumps(payload or {}), text=True,
                                capture_output=True, timeout=15)
        if failure:
            self.assertNotEqual(result.returncode, 0)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def install_hermes(self, home):
        native = dict(executable=self.config['cairn'], socket=self.config['socket'],
                      token_file=self.config['token_file'], repo=self.config['repo'])
        skill = home / 'fixture-skill.md'
        if not skill.exists():
            skill.write_text('Selected synthetic fixture skill.\n')
        hermes.install(home, native, self.config['claude'], skill)
        binding = json.loads((home / 'cairn-lifecycle.json').read_text())
        return Path(binding['script']), Path(binding['engine_config'])

    def verify_memory(self, script, config_path):
        self.assertTrue(script.is_file())
        self.assertEqual(set(hermes.bundle.bundle_descriptor(script.parent)['files']),
                         set(hermes.bundle.MODULE_FILES))
        self.isolated(script, '--help')
        harness = json.loads(config_path.read_text()).get('harness', 'claude')
        session = 'ses_fixture' if harness == 'opencode' else '11111111-1111-4111-8111-111111111111'
        result = self.isolated(script, '--config', config_path, payload={
            'hook_event_name': 'PostToolUse', 'session_id': session,
            'cwd': str(self.root), 'tool_name': 'Read', 'tool_input': {'file_path': 'selected.go'}})
        self.assertEqual(json.loads(result.stdout), {'cairn_status': {}} if harness == 'hermes' else {})

    def test_hermes_memory_installation_is_complete(self):
        home = self.root / 'hermes'
        home.mkdir()
        self.verify_memory(*self.install_hermes(home))

    def test_coordination_installations_run_outside_checkout_for_every_harness(self):
        (self.root / '.cairn-no-coordination').touch()
        for harness in ('codex', 'claude', 'agy', 'opencode', 'hermes'):
            with self.subTest(harness=harness):
                home = self.root / harness
                home.mkdir()
                destination = home / 'runtime'
                settings = home if harness in ('opencode', 'hermes') else home / 'settings.json'
                config = dict(self.config, harness=harness, binding=harness + '-fixture')
                installed = coordination.install(destination, settings, config)
                entry = destination / 'coordination.py'
                self.assertEqual(set(coordination.bundle.bundle_descriptor(destination)['files']),
                                 set(coordination.bundle.MODULE_FILES))
                self.isolated(entry, '--help')
                result = self.isolated(entry, 'hook', '--config', installed, payload={
                    'hook_event_name': 'PostToolUse', 'session_id': 'synthetic-session',
                    'cwd': str(self.root)})
                self.assertEqual(json.loads(result.stdout), {})

    def test_explicit_memory_reinstall_preserves_config_and_refuses_enabled_binding(self):
        home = self.root / 'hermes'
        home.mkdir()
        script, config_path = self.install_hermes(home)
        binding = home / 'binding.json'
        binding.write_text(json.dumps({'enabled': True}))
        installed = json.loads(config_path.read_text())
        installed.update(inbox_recall_binding=str(binding), context_bytes=8123,
                         recall_mode='agent_tools', preview_model='retained-model')
        config_path.write_text(json.dumps(installed))
        runtime = script.parent
        before = {p: p.read_bytes() for p in [*runtime.iterdir(), config_path] if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'disable the recall binding'):
            self.install_hermes(home)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        binding.write_text(json.dumps({'enabled': False}))
        self.install_hermes(home)
        rewritten = json.loads(config_path.read_text())
        for field in ('inbox_recall_binding', 'context_bytes', 'recall_mode', 'preview_model', 'state_dir'):
            self.assertEqual(rewritten[field], installed[field])

    def customize_state(self, config_path, state_dir):
        config = json.loads(config_path.read_text())
        config['state_dir'] = str(state_dir)
        config_path.write_text(json.dumps(config))
        state_dir.mkdir()
        ledger = state_dir / 'session.inbox-recall.json'
        ledger.write_text(json.dumps({'schema': 'cairn.inbox-recall-grants/1',
                                      'session': 'session', 'grants': {'retained': {'receipt_id': 'fixture'}}}))
        marker = ledger.with_suffix('.initialized')
        marker.write_text('existing initialization marker\n')
        return {path: path.read_bytes() for path in (ledger, marker)}

    def test_hermes_memory_reinstall_retains_custom_state_and_existing_ledger(self):
        home = self.root / 'hermes'
        home.mkdir()
        _, config_path = self.install_hermes(home)
        custom_state = home / 'existing-state'
        retained = self.customize_state(config_path, custom_state)
        self.install_hermes(home)
        self.assertEqual(json.loads(config_path.read_text())['state_dir'], str(custom_state))
        self.assertEqual(retained, {path: path.read_bytes() for path in retained})

    def test_coordination_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses(self):
        for harness in ('codex', 'claude', 'agy', 'opencode', 'hermes'):
            with self.subTest(harness=harness):
                home = self.root / harness
                home.mkdir()
                destination = home / 'runtime'
                settings = home if harness in ('opencode', 'hermes') else home / 'settings.json'
                config = dict(self.config, harness=harness, binding=harness + '-fixture')
                installed = coordination.install(destination, settings, config)
                custom_state = home / 'existing-state'
                retained = self.customize_state(installed, custom_state)
                coordination.install(destination, settings, config)
                self.assertEqual(json.loads(installed.read_text())['state_dir'], str(custom_state))
                self.assertEqual(retained, {path: path.read_bytes() for path in retained})

    def test_installed_memory_refuses_changed_or_missing_helpers_before_state_write(self):
        home = self.root / 'hermes'
        home.mkdir()
        entry, config_path = self.install_hermes(home)
        state = Path(json.loads(config_path.read_text())['state_dir'])
        helper = entry.parent / 'grant_ledger.py'
        helper.write_bytes(helper.read_bytes() + b'\n# changed installed helper\n')
        result = self.isolated(entry, '--config', config_path, failure=True,
                               payload={'hook_event_name': 'PostToolUse', 'session_id': 'fixture'})
        self.assertIn('lifecycle bundle', result.stderr)
        self.assertFalse(state.exists())
        self.install_hermes(home)
        helper.unlink()
        self.isolated(entry, '--help', failure=True)
        self.assertFalse(state.exists())

    def test_shared_coordination_reinstall_refuses_another_enabled_account(self):
        destination = self.root / 'runtime'
        first = coordination.install(destination, self.root / 'first.json',
                                     dict(self.config, harness='codex', binding='first'))
        binding = self.root / 'binding.json'
        binding.write_text(json.dumps({'enabled': True}))
        config = json.loads(first.read_text())
        config['inbox_recall_binding'] = str(binding)
        first.write_text(json.dumps(config))
        before = (destination / 'coordination.py').read_bytes()
        with self.assertRaisesRegex(ValueError, 'disable the recall binding'):
            coordination.install(destination, self.root / 'second.json',
                                 dict(self.config, harness='claude', binding='second'))
        self.assertEqual((destination / 'coordination.py').read_bytes(), before)
        self.assertFalse((destination / 'bindings/second.json').exists())


if __name__ == '__main__':
    unittest.main()
