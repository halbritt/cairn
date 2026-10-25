"""Controller deadline stops a native Codex app-server and its active turn."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest


SCRIPT = Path(__file__).with_name("install-agent-coordination.py")
SPEC = importlib.util.spec_from_file_location("coordination_installer_timeout", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class CodexTrustDeadlineTests(unittest.TestCase):
    def test_deadline_terminates_app_server_while_verifier_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "codex-home"
            home.mkdir(mode=0o700)
            (home / "config.toml").write_text("\n")
            settings = home / "hooks.json"
            settings.write_text("{}\n")
            fake_codex = root / "codex-fixture"
            fake_codex.write_text("""#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    request=json.loads(line)
    result={'data': []} if request['method']=='hooks/list' else {}
    if 'id' in request:
        print(json.dumps({'id': request['id'], 'result': result}), flush=True)
""")
            fake_codex.chmod(0o700)
            began = time.monotonic()
            release = threading.Event()
            try:
                with self.assertRaisesRegex(TimeoutError, "deadline"):
                    installer.trust_codex_hooks(
                        home, settings, "fixture-hook", codex_binary=str(fake_codex),
                        expected=0, timeout=0.2,
                        verify=lambda _rpc: release.wait(30),
                    )
            finally:
                release.set()
            self.assertLess(time.monotonic() - began, 3)


if __name__ == "__main__":
    unittest.main()
