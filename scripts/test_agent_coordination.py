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
        ref = coordination.owner_process(dict(process_names=[]), dict(host_pid=os.getppid()))
        self.assertEqual(ref["pid"], os.getppid())
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


if __name__ == "__main__":
    unittest.main()
