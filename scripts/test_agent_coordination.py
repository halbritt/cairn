"""Coordination hooks retain identities and selected context, never transcripts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coordination", ROOT / "integrations/lifecycle/coordination.py")
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)


class CoordinationNormalization(unittest.TestCase):
    def test_claude_channel_session_selector_validation_and_defaults(self):
        config = dict(harness='claude', binding='account-one', repo='fixture',
                      cairn='/fixture/cairn', socket='/fixture/api.sock', token_file='/fixture/token',
                      state_dir='/fixture/state', claude_channel_dir='/fixture/channels')
        flagged = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', '--dangerously-load-development-channels', 'server:cairn-events'])
        self.addCleanup(lambda: (flagged.terminate(), flagged.wait(timeout=5)))
        enabled = coordination.process_reference(flagged.pid)
        plain = coordination.process_reference(os.getpid())
        self.assertIs(coordination.claude_session_config(config, 'native-one', enabled), config)
        effective, reason = coordination.claude_channel_admission(config, 'native-one', plain)
        self.assertNotIn('claude_channel_dir', effective)
        self.assertIn('not launched with --dangerously-load-development-channels=server:cairn-events', reason)
        self.assertEqual(coordination.claude_channel_admission(dict(config, harness='codex'), 'native-one', plain),
                         (dict(config, harness='codex'), None))
        unconfigured = dict(config)
        unconfigured.pop('claude_channel_dir')
        self.assertEqual(coordination.claude_channel_admission(unconfigured, 'native-one', enabled),
                         (unconfigured, 'binding has no claude_channel_dir'))
        for sessions in ([], ['native-one']):
            selected = dict(config, claude_channel_sessions=sessions)
            self.assertEqual(coordination.validate_config(selected), selected)
            effective = coordination.claude_session_config(selected, 'native-one', enabled)
            self.assertEqual('claude_channel_dir' in effective, bool(sessions))
            self.assertNotIn('claude_channel_dir', coordination.claude_session_config(selected, 'native-one', plain))
            self.assertIn('claude_channel_dir', selected)
        for server in ('', 'a,b', 'a:b', 'bad name', 'x' * 129, 5, None):
            with self.subTest(server=server), self.assertRaisesRegex(coordination.CoordinationError, 'INVALID_CONFIG'):
                coordination.validate_config(dict(config, claude_channel_server=server))
        with self.assertRaisesRegex(coordination.CoordinationError, 'INVALID_CONFIG'):
            coordination.validate_config(dict(config, harness='codex', claude_channel_server='cairn-events'))
        named = dict(config, claude_channel_server='cairn-events-two')
        self.assertEqual(coordination.validate_config(named), named)
        self.assertNotIn('claude_channel_dir', coordination.claude_session_config(named, 'native-one', enabled))
        for sessions in (None, True, 'native-one', {}, [None], [1], [''], ['  '],
                         ['bad\nname'], ['x' * 257], ['native-one', 'native-one']):
            with self.subTest(sessions=sessions), self.assertRaisesRegex(coordination.CoordinationError, 'INVALID_CONFIG'):
                coordination.validate_config(dict(config, claude_channel_sessions=sessions))
        with self.assertRaisesRegex(coordination.CoordinationError, 'INVALID_CONFIG'):
            coordination.validate_config(dict(config, harness='codex', claude_channel_sessions=[]))

    def test_claude_selector_missing_or_invalid_directory_refuses_hook_before_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = dict(harness='claude', binding='account-one', repo='fixture',
                          cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
                          state_dir=str(root/'state'), native_delivery=True, idle_wakeup='/absent/herdr')
            path = root/'config.json'
            for sessions in ([], ['native-one']):
                for channel in ('missing', None, '', False, True, 12, [], {}, 'relative/channel'):
                    with self.subTest(sessions=sessions, directory=channel):
                        invalid = dict(config, claude_channel_sessions=sessions)
                        if channel != 'missing':
                            invalid['claude_channel_dir'] = channel
                        path.write_text(json.dumps(invalid))
                        result = subprocess.run([sys.executable, str(ROOT/'integrations/lifecycle/coordination.py'),
                            'hook', '--config', str(path)], input=json.dumps(dict(session_id='native-one',
                            cwd=str(root), hook_event_name='UserPromptSubmit', prompt_id='owner-prompt')),
                            text=True, capture_output=True, timeout=5)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn('INVALID_CONFIG', result.stderr)
                        self.assertIn('absolute claude_channel_dir', result.stderr)
                        self.assertFalse((root/'state').exists(), 'invalid configuration reached native admission')

    def test_provider_observation_survives_api_outage_without_registering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = 'a424caa4-1a11-4c59-b8db-d05d926fc4aa'
            path = root / (attempt + '.context.json')
            target = root / (attempt + '.provider.json')
            config = dict(cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
                repo='fixture', harness='hermes', binding='hermes', state_dir=str(root/'state'))
            wake = dict(schema='cairn.wake-context/1', native_registration=True, attempt_id=attempt,
                collection='fixture', socket=config['socket'], token_file=config['token_file'],
                native_session_id='native-one', provider_harness='hermes', provider_observation_file=str(target))
            path.write_text(json.dumps(wake))
            config_path = root/'config.json'
            config_path.write_text(json.dumps(config))
            failure = dict(harness='hermes', source='native-hook', kind='rate_limit', code='rate_limit', status=429)
            def invoke(native, value):
                return subprocess.run([sys.executable, str(ROOT/'integrations/lifecycle/coordination.py'),
                    'hook', '--config', str(config_path)], input=json.dumps(dict(session_id=native,
                    cwd=directory, host_pid=os.getpid(), hook_event_name='ProviderObservation',
                    provider_failure=value)), env=dict(os.environ, CAIRN_WAKE_CONTEXT=str(path),
                    CAIRN_COORDINATION_DISABLED='0'), text=True, capture_output=True, timeout=5)
            result = invoke('native-one',failure)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(target.read_text())['failure'],failure)
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            self.assertFalse((root/'state').exists())
            result = invoke('child-session',None)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(target.read_text())['failure'],failure)
            result = invoke('native-one',None)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIsNone(json.loads(target.read_text())['failure'])

    def test_malformed_wake_context_is_reported_without_traceback_or_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text(json.dumps(dict(cairn='/absent/cairn', socket='/absent/api.sock',
                token_file='/absent/token', repo='fixture', harness='codex', binding='codex', state_dir=str(root/'state'))))
            wake = root / 'wake.json'
            for body in ('[]', 'null', '{', 'x' * 32769):
                wake.write_text(body)
                result = subprocess.run([sys.executable, str(ROOT/'integrations/lifecycle/coordination.py'),
                    'hook', '--config', str(config)], input='{}', text=True, capture_output=True, timeout=5,
                    env=dict(os.environ, CAIRN_WAKE_CONTEXT=str(wake), CAIRN_COORDINATION_DISABLED='0'))
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('Traceback', result.stderr)
                self.assertIn('Cairn coordination:', result.stderr)
                self.assertFalse((root/'state').exists())

    def test_native_ids_models_and_no_prompt_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            for harness, event in (
                ("codex", dict(session_id="thread-one", cwd=directory, hook_event_name="UserPromptSubmit", prompt="PRIVATE PROMPT", model="native-model")),
                ("claude", dict(session_id="thread-two", cwd=directory, hook_event_name="SessionStart", transcript_path="PRIVATE TRANSCRIPT")),
                ("agy", dict(conversationId="thread-three", workspacePaths=[directory], modelName="native-model", transcriptPath="PRIVATE TRANSCRIPT")),
            ):
                observed = coordination.normalize(dict(harness=harness, model="configured"), event, "PreInvocation" if harness == "agy" else None)
                self.assertTrue(observed["native_id"].startswith("thread-"))
                self.assertNotIn("PRIVATE", json.dumps(observed))
                self.assertEqual(observed["workspace"], directory)
                if harness != "claude":
                    self.assertEqual(observed["observed_model"], "native-model")

    def test_process_reference_does_not_survive_exit_or_pid_reuse(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            ref = coordination.process_reference(child.pid)
            self.assertTrue(coordination.process_alive(ref))
            self.assertFalse(coordination.process_alive(dict(ref, start=ref["start"] + 1)))
            self.assertFalse(coordination.process_alive(dict(ref, boot="different-boot")))
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(coordination.process_alive(ref))

    def test_declared_process_must_be_an_ancestor(self):
        # In a container the test runner's parent can be PID 1, which is
        # deliberately not an eligible native host. Give the check a real
        # process parent by running it in a child of this test process.
        check = subprocess.run([
            sys.executable, '-c',
            'import os; from integrations.lifecycle import coordination; '
            'parent = os.getppid(); '
            'ref = coordination.owner_process(dict(process_names=[]), dict(host_pid=parent)); '
            'assert ref["pid"] == parent',
        ], capture_output=True, text=True, timeout=5)
        self.assertEqual(check.returncode, 0, check.stderr)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with self.assertRaises(coordination.CoordinationError):
                coordination.owner_process(dict(process_names=[]), dict(host_pid=child.pid))
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_other_account_hook_layer_cannot_register_the_active_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for harness, variable in (('codex', 'CODEX_HOME'), ('claude', 'CLAUDE_CONFIG_DIR')):
                config = dict(cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
                    repo='fixture', harness=harness, binding=harness, state_dir=str(root / 'state'),
                    config_home=str(root / 'other-account'))
                path = root / (harness + '.json')
                path.write_text(json.dumps(config))
                result = subprocess.run([sys.executable, str(ROOT / 'integrations/lifecycle/coordination.py'),
                    'hook', '--config', str(path)], input=json.dumps(dict(session_id='native', cwd=directory,
                    hook_event_name='SessionStart')), env=dict(os.environ, **{variable: str(root / 'active-account')}),
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
                self.assertFalse((root / 'state').exists())


class CoordinationInstall(unittest.TestCase):
    def test_reinstall_preserves_other_hooks_and_settings(self):
        installer_spec = importlib.util.spec_from_file_location("coordination_installer", ROOT / "scripts/install-agent-coordination.py")
        installer = importlib.util.module_from_spec(installer_spec)
        installer_spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = dict(model="owner-model", hooks={"SessionStart": [{"hooks": [{"type": "command", "command": "owner-existing-hook"}]}]})
            for harness in ("codex", "claude"):
                settings = root / (harness + ".json")
                settings.write_text(json.dumps(original))
                config = dict(cairn="/fixture/cairn", socket="/fixture/api.sock", token_file="/fixture/existing.token",
                    repo="collection", harness=harness, binding=harness + "-two", process_names=[harness])
                installer.install(root / "coordinator", settings, config)
                first = settings.read_text()
                installer.install(root / "coordinator", settings, config)
                self.assertEqual(first, settings.read_text())
                installed = json.loads(first)
                self.assertEqual(installed["model"], "owner-model")
                self.assertEqual(installed["hooks"]["SessionStart"][0], original["hooks"]["SessionStart"][0])
                self.assertEqual(len(installed["hooks"]["SessionStart"]), 2)
                self.assertEqual(json.loads(settings.with_name(settings.name + ".before-cairn-coordination").read_text()), original)
                # Only Codex observes Interrupt; its natively interrupted turns
                # are actually idle while their tools may still be running.
                events = set(installed["hooks"])
                if harness == "codex":
                    self.assertIn("Interrupt", events)
                    interrupt = installed["hooks"]["Interrupt"][0]["hooks"][0]
                    self.assertEqual(interrupt["timeout"], 2)
                else:
                    self.assertNotIn("Interrupt", events)


if __name__ == "__main__":
    unittest.main()
