"""App-server setup RPC deadlines are groundwork, not native turn cancellation."""
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("install-agent-coordination.py")
SPEC = importlib.util.spec_from_file_location("coordination_installer_timeout", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class CodexSetupDeadlineTests(unittest.TestCase):
    def test_blocked_setup_rpc_terminates_app_server_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "codex-home"
            home.mkdir(mode=0o700)
            (home / "config.toml").write_text("\n")
            settings = home / "hooks.json"
            settings.write_text("{}\n")
            pid_file = root / "pid"
            fake_codex = root / "codex-fixture"
            fake_codex.write_text(f"""#!/usr/bin/env python3
import json, os, sys, time
open({str(pid_file)!r}, 'w').write(str(os.getpid()))
for line in sys.stdin:
    request=json.loads(line)
    if request.get('method') == 'hooks/list':
        time.sleep(30)
""")
            fake_codex.chmod(0o700)
            began = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                installer.trust_codex_hooks(
                    home, settings, "fixture-hook", codex_binary=str(fake_codex),
                    expected=0, timeout=0.2,
                )
            self.assertLess(time.monotonic() - began, 3)
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError):
                __import__("os").kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
