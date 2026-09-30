"""Idle-wakeup installation covers the selected account home via the Herdr CLI, or skips Herdr for OpenCode."""
import importlib.util
import io
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


INSTALLER = ROOT / 'scripts/install-agent-coordination.py'
TRAP_HERDR = '#!/bin/sh\necho "$0 $@" >> "$(dirname "$0")/herdr-was-run"\nexit 1\n'


class ScratchOpenCodeBinding(unittest.TestCase):
    """A scratch OpenCode directory wakes through the native bridge without Herdr (CAIRN-42)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.home = self.temp / 'home'
        self.bin = self.temp / 'bin'
        self.scratch = self.temp / 'scratch'
        for directory in (self.home / '.config/opencode/plugins', self.bin, self.scratch):
            directory.mkdir(parents=True)
        # Stand-ins for an unrelated installed OpenCode profile and for Herdr.
        (self.home / '.config/opencode/opencode.json').write_text('{"owner": "profile"}')
        (self.home / '.config/opencode/plugins/herdr-agent-state.js').write_text('// herdr plugin v11')
        self.trap = self.bin / 'herdr'
        self.trap.write_text(TRAP_HERDR)
        self.trap.chmod(0o700)
        self.cairn = self.bin / 'cairn'
        self.cairn.write_text('#!/bin/sh\nexit 0\n')
        self.cairn.chmod(0o700)
        self.token = self.scratch / 'agent.token'
        self.token.write_text('scratch-token')
        self.settings = self.scratch / 'opencode'
        self.root = self.scratch / 'root'

    def snapshot(self):
        return {str(path.relative_to(self.home)): path.read_bytes()
                for path in sorted(self.home.rglob('*')) if path.is_file()}

    def install(self, *extra, harness='opencode'):
        command = [sys.executable, '-B', str(INSTALLER), '--harness', harness, '--binding', 'scratch',
                   '--settings', str(self.settings), '--root', str(self.root), '--cairn', str(self.cairn),
                   '--socket', str(self.scratch / 'api.sock'), '--token-file', str(self.token),
                   '--repo', 'fixture:scratch', '--no-service', *extra]
        environment = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:/usr/bin:/bin')
        for name in ('HERDR_ENV', 'HERDR_SOCKET_PATH', 'CAIRN_COORDINATION_CONFIG'):
            environment.pop(name, None)
        return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)

    def binding(self):
        return json.loads((self.root / 'bindings/scratch.json').read_text())

    def test_no_herdr_installs_a_usable_scratch_binding_and_touches_nothing_else(self):
        before = self.snapshot()
        result = self.install('--native-delivery', '--idle-wakeup', '--opencode-cancel-trial', '--no-herdr')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Herdr was not consulted', result.stdout)
        self.assertFalse((self.bin / 'herdr-was-run').exists(), 'Herdr was run')
        self.assertEqual(self.snapshot(), before)  # the unrelated installed profile and HOME are unchanged
        self.assertTrue((self.settings / 'plugins/cairn-coordination.ts').is_file())
        self.assertTrue((self.settings / 'cairn-coordination.json').is_file())
        binding = self.binding()
        self.assertEqual((binding['harness'], binding['native_delivery'], binding['idle_wakeup'],
                          binding['opencode_cancel_enabled']), ('opencode', True, True, True))
        self.assertEqual(installer.engine.load_config(self.root / 'bindings/scratch.json')['idle_wakeup'], True)

    def test_no_herdr_without_no_service_refuses_before_install_or_shared_watcher(self):
        # The normal service installer targets a fixed shared unit even when
        # --root points at scratch. Never reach it through this isolated mode.
        argv = ['install-agent-coordination.py', '--harness', 'opencode', '--binding', 'scratch',
                '--settings', str(self.settings), '--root', str(self.root), '--cairn', str(self.cairn),
                '--socket', str(self.scratch / 'api.sock'), '--token-file', str(self.token),
                '--repo', 'fixture:scratch', '--native-delivery', '--idle-wakeup', '--no-herdr']
        before = self.snapshot()
        error = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(sys, 'stderr', error), \
                mock.patch.object(sys, 'stdout', io.StringIO()), \
                mock.patch.object(installer, 'install_service') as shared_service, \
                self.assertRaises(SystemExit) as caught:
            installer.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('--no-herdr requires --no-service', error.getvalue())
        shared_service.assert_not_called()
        self.assertFalse((self.root / 'bindings').exists())
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.snapshot(), before)

    def test_installed_scratch_binding_selects_the_bridge_wake_without_herdr(self):
        self.assertEqual(self.install('--native-delivery', '--idle-wakeup', '--opencode-cancel-trial',
                                      '--no-herdr').returncode, 0)
        engine = installer.engine
        config = engine.load_config(self.root / 'bindings/scratch.json')
        session = dict(agent_id='fixture-agent', execution_id='fixture-execution')
        state = dict(workspace=str(self.scratch), process=dict(pid=os.getpid(), start=1, boot='boot'),
                     agent=dict(**session, native_session_id='ses_scratch',
                                metadata=dict(workspace=str(self.scratch), state='idle',
                                              delivery_mode='existing-session')))
        path = Path(config['state_dir']) / 'session.json'
        engine.write_state(path, state)
        delivery = '00000000-0000-4000-8000-000000000001'

        def store(_config, operation, _request, **_kwargs):
            self.assertEqual(operation, 'session-inbox-ready')
            return dict(delivery_id=delivery)

        with mock.patch.object(engine, 'call', side_effect=store), \
                mock.patch.object(engine, 'opencode_idle_endpoint', return_value='/tmp/cairn-opencode-fixture.sock'), \
                mock.patch.object(engine, 'opencode_capture_available', return_value=False), \
                mock.patch.object(engine, 'herdr_environment', side_effect=AssertionError('Herdr environment read')), \
                mock.patch.object(engine, 'bounded_herdr_command', side_effect=AssertionError('Herdr run')), \
                engine.session_lock(path):
            prepared = engine.prepare_idle_wake(config, state, path)
        self.assertEqual(prepared['wake']['transport'], 'opencode-queue')
        self.assertEqual((prepared['wake']['delivery_id'], prepared['wake']['native_id']), (delivery, 'ses_scratch'))

    def test_without_the_flag_a_scratch_directory_is_refused_and_points_to_it(self):
        state = dict(status=[f'opencode: outdated (v10 < v11) ({self.home}/.config/opencode/plugins/herdr-agent-state.js)'],
                     after=[], install_fails=False)
        herdr = self.bin / 'herdr'
        herdr.write_text(HERDR)
        (self.bin / 'herdr.json').write_text(json.dumps(state))
        before = self.snapshot()
        result = self.install('--native-delivery', '--idle-wakeup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot be woken through Herdr', result.stderr)
        self.assertIn('--no-herdr', result.stderr)
        self.assertFalse((self.bin / 'installs.jsonl').exists(), 'Herdr install ran for an unrelated home')
        self.assertFalse((self.root / 'bindings').exists())
        self.assertEqual(self.snapshot(), before)

    def test_no_herdr_is_limited_to_opencode_idle_wake_without_a_herdr_choice(self):
        for arguments, harness, message in (
                (('--native-delivery', '--idle-wakeup', '--no-herdr'), 'claude', '--no-herdr requires'),
                (('--native-delivery', '--no-herdr'), 'opencode', '--no-herdr requires'),
                (('--native-delivery', '--idle-wakeup', '--no-herdr', '--herdr', str(self.trap)), 'opencode', 'cannot be combined'),
                (('--idle-wakeup', '--no-herdr'), 'opencode', '--idle-wakeup requires --native-delivery')):
            with self.subTest(arguments=arguments, harness=harness):
                result = self.install(*arguments, harness=harness)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.root / 'bindings').exists())
        self.assertFalse((self.bin / 'herdr-was-run').exists())

    def test_default_herdr_install_is_unchanged_for_the_owner_home(self):
        # Herdr's own OpenCode home still gets the existing coverage, with the Herdr path recorded.
        home = self.home / '.config/opencode'
        herdr = self.bin / 'herdr'
        herdr.write_text(HERDR)
        (self.bin / 'herdr.json').write_text(json.dumps(dict(
            status=[f'opencode: current (v11) ({home}/plugins/herdr-agent-state.js)'], after=[], install_fails=False)))
        self.settings = home
        result = self.install('--native-delivery', '--idle-wakeup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Herdr opencode integration is current', result.stdout)
        self.assertEqual(self.binding()['idle_wakeup'], str(herdr.resolve()))


class BridgeOnlyIdleWakeConfig(unittest.TestCase):
    """`idle_wakeup: true` is the OpenCode bridge's switch, never a way to run Herdr."""

    def config(self, root, **override):
        values = dict(cairn=str(root / 'cairn'), socket=str(root / 'api.sock'), token_file=str(root / 'token'),
                      state_dir=str(root / 'state'), repo='fixture', binding='fixture', harness='opencode',
                      native_delivery=True, idle_wakeup=True)
        values.update(override)
        return values

    def test_only_opencode_with_native_delivery_accepts_true(self):
        engine = installer.engine
        root = Path('/absolute')
        self.assertEqual(engine.validate_config(self.config(root))['idle_wakeup'], True)
        self.assertEqual(engine.validate_config(self.config(root, idle_wakeup='/usr/bin/herdr'))['idle_wakeup'], '/usr/bin/herdr')
        for override in (dict(harness='claude'), dict(harness='hermes'), dict(harness='codex'), dict(native_delivery=False),
                         dict(idle_wakeup=False), dict(idle_wakeup='herdr'), dict(idle_wakeup=1)):
            with self.subTest(override=override), self.assertRaises(engine.CoordinationError) as caught:
                engine.validate_config(self.config(root, **override))
            self.assertEqual(caught.exception.code, 'INVALID_CONFIG')

    def test_a_bridge_only_binding_cannot_be_run_as_a_herdr_command(self):
        engine = installer.engine
        with mock.patch.object(engine, 'bounded_herdr_command', side_effect=AssertionError('ran a command')):
            with self.assertRaises(engine.CoordinationError) as caught:
                engine.herdr_call(dict(idle_wakeup=True), {}, 'agent', 'list')
        self.assertEqual(caught.exception.code, 'HOST_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
