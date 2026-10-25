"""Provider-free isolation proof against the installed Codex code-mode host."""
import importlib.util
from pathlib import Path
import re
import shutil
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "trial_codex_remote_code_mode_host", HERE / "trial_codex_remote_code_mode_host.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def installed_paths():
    launcher = Path(shutil.which("codex") or "").resolve()
    if not launcher.is_file():
        raise unittest.SkipTest("installed Codex CLI is unavailable")
    text = launcher.read_text(encoding="utf-8")
    match = re.search(r'^REAL="([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise unittest.SkipTest("Codex launcher does not expose its installed package entry")
    entry = Path(match.group(1))
    root = entry.parent.parent / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin"
    host = root / "codex-code-mode-host"
    if not entry.is_file() or not host.is_file():
        raise unittest.SkipTest("installed Codex package lacks app-server or code-mode host")
    return entry, host


class RemoteCodeModeHostTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap") and shutil.which("node"),
                         "bwrap and node are required")
    def test_remote_code_mode_host_does_not_isolate_shell_tool_execution(self):
        entry, host = installed_paths()
        cache = Path.home() / ".cache/cairn-token-efficiency-panels"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="test-remote-code-mode-", dir=cache) as name:
            result = probe.run_probe(Path(name) / "probe", entry, host)
        self.assertEqual(result["result"], "refused")
        self.assertEqual(result["reason"],
                         "remote_code_mode_host_did_not_isolate_functions_exec")
        self.assertEqual(result["provider_call"], False)
        self.assertEqual(result["benign_exec"], "passed")
        self.assertEqual(result["bwrap_direct_read"], "denied")
        self.assertEqual(result["marker_read"], "leaked")
        self.assertEqual(result["auth_marker_leaked"], True)
        self.assertEqual(result["turn_interrupt_status"], "interrupted")
        self.assertLessEqual(result["interrupt_elapsed_seconds"], 2.5)
        self.assertTrue(result["owned_process_groups_cleaned"])


if __name__ == "__main__":
    unittest.main()
