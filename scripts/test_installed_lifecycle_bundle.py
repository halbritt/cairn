"""Installed lifecycle entrypoints run without a checkout or provider access."""
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


claude = load('install-claude-hooks')
codex = load('install-codex-hooks')
opencode = load('install-opencode-hooks')
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

    def verify_memory(self, script, config_path):
        self.assertTrue(script.is_file())
        self.assertEqual(set(claude.bundle.bundle_descriptor(script.parent)['files']),
                         set(claude.bundle.MODULE_FILES))
        self.isolated(script, '--help')
        harness = json.loads(config_path.read_text()).get('harness', 'claude')
        session = 'ses_fixture' if harness == 'opencode' else '11111111-1111-4111-8111-111111111111'
        result = self.isolated(script, '--config', config_path, payload={
            'hook_event_name': 'PostToolUse', 'session_id': session,
            'cwd': str(self.root), 'tool_name': 'Read', 'tool_input': {'file_path': 'selected.go'}})
        self.assertEqual(json.loads(result.stdout), {'cairn_status': {}} if harness == 'hermes' else {})

    def test_memory_installations_are_complete_for_every_supported_harness(self):
        for harness in ('claude', 'codex', 'opencode', 'hermes'):
            with self.subTest(harness=harness):
                home = self.root / harness
                home.mkdir()
                destination = home / 'runtime'
                if harness == 'claude':
                    command = claude.install(home / 'settings.json', destination, self.config)
                    script = Path(shlex.split(command)[1])
                    config_path = destination / 'config.json'
                elif harness == 'codex':
                    with patch.object(codex, 'trust'):
                        command = codex.install(home / 'hooks.json', destination, self.config)
                    script = Path(shlex.split(command)[1])
                    config_path = destination / 'config.json'
                else:
                    native = dict(executable=self.config['cairn'], socket=self.config['socket'],
                                  token_file=self.config['token_file'], repo=self.config['repo'])
                    if harness == 'opencode':
                        (home / 'cairn.json').write_text(json.dumps(native))
                        opencode.install(home, destination, self.config['claude'])
                    else:
                        skill = self.root / 'fixture-skill.md'
                        skill.write_text('Selected synthetic fixture skill.\n')
                        hermes.install(home, native, self.config['claude'], skill)
                    binding = json.loads((home / 'cairn-lifecycle.json').read_text())
                    script, config_path = Path(binding['script']), Path(binding['engine_config'])
                self.verify_memory(script, config_path)

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
        home = self.root / 'claude'
        home.mkdir()
        destination = home / 'runtime'
        settings = home / 'settings.json'
        claude.install(settings, destination, self.config)
        config_path = destination / 'config.json'
        binding = home / 'binding.json'
        binding.write_text(json.dumps({'enabled': True}))
        installed = json.loads(config_path.read_text())
        installed.update(inbox_recall_binding=str(binding), context_bytes=8123,
                         recall_mode='agent_tools', preview_model='retained-model')
        config_path.write_text(json.dumps(installed))
        before = {p: p.read_bytes() for p in destination.iterdir() if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'disable the recall binding'):
            claude.install(settings, destination, self.config)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        binding.write_text(json.dumps({'enabled': False}))
        claude.install(settings, destination, self.config)
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

    def test_memory_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses(self):
        for harness in ('claude', 'codex', 'opencode', 'hermes'):
            with self.subTest(harness=harness):
                home = self.root / harness
                home.mkdir()
                destination = home / 'runtime'
                config_path = destination / 'config.json'
                if harness == 'claude':
                    install = lambda: claude.install(home / 'settings.json', destination, self.config)
                elif harness == 'codex':
                    install = lambda: codex.install(home / 'hooks.json', destination, self.config)
                else:
                    native = dict(executable=self.config['cairn'], socket=self.config['socket'],
                                  token_file=self.config['token_file'], repo=self.config['repo'])
                    if harness == 'opencode':
                        (home / 'cairn.json').write_text(json.dumps(native))
                        install = lambda: opencode.install(home, destination, self.config['claude'])
                    else:
                        skill = home / 'skill.md'
                        skill.write_text('Synthetic fixture skill.\n')
                        config_path = home / 'cairn/engine.json'
                        install = lambda: hermes.install(home, native, self.config['claude'], skill)
                with patch.object(codex, 'trust'):
                    install()
                    custom_state = home / 'existing-state'
                    retained = self.customize_state(config_path, custom_state)
                    install()
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
        destination = self.root / 'runtime'
        settings = self.root / 'settings.json'
        command = claude.install(settings, destination, self.config)
        entry = Path(shlex.split(command)[1])
        helper = destination / 'grant_ledger.py'
        helper.write_bytes(helper.read_bytes() + b'\n# changed installed helper\n')
        result = self.isolated(entry, '--config', destination / 'config.json', failure=True,
                               payload={'hook_event_name': 'PostToolUse', 'session_id': 'fixture'})
        self.assertIn('lifecycle bundle', result.stderr)
        self.assertFalse((destination / 'state').exists())
        claude.install(settings, destination, self.config)
        helper.unlink()
        self.isolated(entry, '--help', failure=True)
        self.assertFalse((destination / 'state').exists())

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
