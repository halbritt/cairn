"""Idle-wakeup installation covers the selected account home via the Herdr CLI."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
installer_spec = importlib.util.spec_from_file_location('coordination_installer', ROOT / 'scripts/install-agent-coordination.py')
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)

HERDR = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(__file__).parent
state = json.loads((root/'herdr.json').read_text())
args = sys.argv[1:]
if args[:2] == ['integration', 'status']:
    for line in state['status']:
        print(line)
elif args[:2] == ['integration', 'install']:
    with (root/'installs.jsonl').open('a') as file:
        file.write(json.dumps(dict(target=args[2], herdr_env=os.environ.get('HERDR_ENV'),
            herdr_socket=os.environ.get('HERDR_SOCKET_PATH'),
            CLAUDE_CONFIG_DIR=os.environ.get('CLAUDE_CONFIG_DIR'),
            ANTIGRAVITY_CLI_CONFIG_DIR=os.environ.get('ANTIGRAVITY_CLI_CONFIG_DIR'),
            HERMES_HOME=os.environ.get('HERMES_HOME')))+'\n')
    if state.get('install_fails'):
        print(state.get('install_error', 'herdr refused'), file=sys.stderr)
        raise SystemExit(1)
    state['status'] = state['after']
    (root/'herdr.json').write_text(json.dumps(state))
else:
    raise SystemExit('unexpected herdr command '+str(args))
'''


class IdleWakeupPreflight(unittest.TestCase):
    def fixture(self, status, after=None, install_fails=False, install_error='herdr refused'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        herdr = root / 'herdr'
        herdr.write_text(HERDR)
        herdr.chmod(0o700)
        (root / 'herdr.json').write_text(json.dumps(
            dict(status=status, after=after if after is not None else status,
                 install_fails=install_fails, install_error=install_error)))
        return root, herdr

    def installs(self, root):
        path = root / 'installs.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def ensure(self, herdr, harness, settings, environment=None):
        with mock.patch.dict(os.environ, environment or {}):
            return installer.ensure_herdr_integration(herdr, harness, settings)

    def settings_file(self, root, *parts):
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_claude_second_account_home_is_covered(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        home = root / '.claude-harm'
        settings = self.settings_file(root, '.claude-harm', 'settings.json')
        hook = home / 'hooks' / 'herdr-agent-state.sh'
        root2, herdr = self.fixture([f'claude: not installed ({hook})'],
                                    after=[f'claude: current (v9) ({hook})'])
        self.assertEqual(self.ensure(herdr, 'claude', settings,
                                     environment={'HERDR_ENV': '1', 'HERDR_SOCKET_PATH': '/tmp/foreign'}),
                         'claude')
        recorded = self.installs(root2)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]['target'], 'claude')
        self.assertEqual(recorded[0]['CLAUDE_CONFIG_DIR'], str(home))
        self.assertIsNone(recorded[0]['herdr_env'])
        self.assertIsNone(recorded[0]['herdr_socket'])
        self.assertEqual(self.ensure(herdr, 'claude', settings), 'claude')
        self.assertEqual(len(self.installs(root2)), 1)  # current installs stay untouched

    def test_current_and_outdated_states(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        home = root / '.claude'
        settings = self.settings_file(root, '.claude', 'settings.json')
        hook = home / 'hooks' / 'herdr-agent-state.sh'
        root2, herdr = self.fixture([f'claude: current (v9) ({hook})'])
        self.assertEqual(self.ensure(herdr, 'claude', settings), 'claude')
        self.assertEqual(self.installs(root2), [])  # already current: no rewrite

        root3, herdr3 = self.fixture([f'claude: outdated (v8 < v9) ({hook})'],
                                     after=[f'claude: current (v9) ({hook})'])
        self.assertEqual(self.ensure(herdr3, 'claude', settings), 'claude')
        self.assertEqual([i['target'] for i in self.installs(root3)], ['claude'])

    def test_opencode_single_home_and_settings_mismatch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        home = root / '.config' / 'opencode'
        home.mkdir(parents=True)
        plugin = home / 'plugins' / 'herdr-agent-state.js'
        root2, herdr = self.fixture([f'opencode: current (v11) ({plugin})'])
        elsewhere = root / 'elsewhere'
        elsewhere.mkdir()
        with self.assertRaises(RuntimeError) as caught:
            self.ensure(herdr, 'opencode', elsewhere)
        self.assertIn(str(home), str(caught.exception))
        self.assertEqual(self.installs(root2), [])

        root3, herdr3 = self.fixture([f'opencode: not installed ({plugin})'],
                                     after=[f'opencode: current (v11) ({plugin})'])
        self.assertEqual(self.ensure(herdr3, 'opencode', home), 'opencode')
        recorded = self.installs(root3)
        self.assertEqual([i['target'] for i in recorded], ['opencode'])

    def test_hermes_and_agy_use_their_home_overrides(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        hermes = root / '.hermes'
        hermes.mkdir()
        herdr_file = hermes / 'plugins' / 'herdr-agent-state' / '__init__.py'
        root2, herdr = self.fixture([f'hermes: not installed ({herdr_file})'],
                                    after=[f'hermes: current (v5) ({herdr_file})'])
        self.assertEqual(self.ensure(herdr, 'hermes', hermes), 'hermes')
        recorded = self.installs(root2)
        self.assertEqual(recorded[0]['target'], 'hermes')
        self.assertEqual(recorded[0]['HERMES_HOME'], str(hermes))

        agy_home = root / '.gemini' / 'config'
        settings = self.settings_file(root, '.gemini', 'config', 'hooks.json')
        agy_hook = agy_home / 'hooks' / 'herdr-agent-state.sh'
        root3, herdr3 = self.fixture([f'antigravity-cli: not installed ({agy_hook})'],
                                     after=[f'antigravity-cli: current (v3) ({agy_hook})'])
        self.assertEqual(self.ensure(herdr3, 'agy', settings), 'antigravity-cli')
        recorded = self.installs(root3)
        self.assertEqual(recorded[0]['target'], 'antigravity-cli')
        self.assertEqual(recorded[0]['ANTIGRAVITY_CLI_CONFIG_DIR'], str(agy_home))

    def test_codex_needs_no_herdr_integration(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = self.settings_file(root, '.codex', 'hooks.json')
        root2, herdr = self.fixture(['claude: not installed (/nowhere/hooks/herdr-agent-state.sh)'])
        self.assertIsNone(self.ensure(herdr, 'codex', settings))
        self.assertEqual(self.installs(root2), [])

    def test_reported_integration_outside_the_selected_home_refuses(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = self.settings_file(root, '.claude-harm', 'settings.json')
        foreign = root / '.claude' / 'hooks' / 'herdr-agent-state.sh'
        root2, herdr = self.fixture([f'claude: current (v9) ({foreign})'])
        with self.assertRaises(RuntimeError) as caught:
            self.ensure(herdr, 'claude', settings)
        self.assertIn('outside the selected account home', str(caught.exception))
        self.assertEqual(self.installs(root2), [])

    def test_install_failure_and_persistent_gap_report_manual_repair(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = self.settings_file(root, '.claude-harm', 'settings.json')
        hook = root / '.claude-harm' / 'hooks' / 'herdr-agent-state.sh'
        root2, herdr = self.fixture([f'claude: not installed ({hook})'], install_fails=True,
                                    install_error='claude directory not found')
        with self.assertRaises(RuntimeError) as caught:
            self.ensure(herdr, 'claude', settings)
        self.assertIn('claude directory not found', str(caught.exception))
        self.assertIn(f'CLAUDE_CONFIG_DIR={settings.parent}', str(caught.exception))
        self.assertIn(f'{herdr} integration install claude', str(caught.exception))

        root3, herdr3 = self.fixture([f'claude: not installed ({hook})'])  # install leaves it missing
        with self.assertRaises(RuntimeError) as caught:
            self.ensure(herdr3, 'claude', settings)
        self.assertIn('after installation', str(caught.exception))
        self.assertIn('integration install claude', str(caught.exception))

    @unittest.skipUnless(shutil.which('herdr'), 'installed Herdr required for native configuration probe')
    def test_native_herdr_preserves_existing_hooks_in_an_isolated_account_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = home/'settings.json'
            unrelated = dict(permissions=dict(allow=['Read']), hooks=dict(UserPromptSubmit=[
                dict(hooks=[dict(type='command', command='echo retained-owner-hook')])]))
            settings.write_text(json.dumps(unrelated))
            installer.ensure_herdr_integration(shutil.which('herdr'), 'claude', settings)
            actual = json.loads(settings.read_text())
            self.assertEqual(actual['permissions'], unrelated['permissions'])
            self.assertIn(unrelated['hooks']['UserPromptSubmit'][0], actual['hooks']['UserPromptSubmit'])
            self.assertTrue((home/'hooks/herdr-agent-state.sh').is_file())
            before = settings.read_bytes()
            installer.ensure_herdr_integration(shutil.which('herdr'), 'claude', settings)
            self.assertEqual(settings.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
