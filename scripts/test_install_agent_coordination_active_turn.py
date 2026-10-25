"""A stalled local app-server is cancelled and its sandbox descendants are killed."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


HERE = Path(__file__).resolve().parent
INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "coordination_installer_active_deadline", HERE / "install-agent-coordination.py")
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)
SANDBOX_SPEC = importlib.util.spec_from_file_location(
    "trial_codex_app_server_sandbox", HERE / "trial_codex_app_server_sandbox.py")
sandbox = importlib.util.module_from_spec(SANDBOX_SPEC)
SANDBOX_SPEC.loader.exec_module(sandbox)


def alive(pid):
    try:
        stat_fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        return stat_fields[0] not in ("Z", "X") and b"/bin/sleep 60" in command
    except FileNotFoundError:
        return False


class ActiveTurnIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap") and Path("/usr/bin/node").exists(),
                         "bwrap and node are required")
    def test_stalled_turn_cancel_and_namespace_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "actor"
            profile = workspace / ".codex"
            package = workspace / "codex-package"
            profile.mkdir(parents=True)
            package.mkdir()
            auth = root / "synthetic-auth.json"
            auth.write_text("{}\n")
            cancel_marker = workspace / "cancel-received"
            child_pid_file = workspace / "child-host-pid"
            entry = package / "codex.js"
            entry.write_text("""const fs = require('fs');
const {spawn} = require('child_process');
const child = spawn('/bin/sleep', ['60'], {detached: true, stdio: 'ignore'});
const status = fs.readFileSync('/proc/' + child.pid + '/status', 'utf8');
const ids = status.match(/^NSpid:\\s+([0-9 ]+)/m)[1].trim().split(/\\s+/);
fs.writeFileSync('""" + str(child_pid_file) + """', ids[0]);
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  let end;
  while ((end = input.indexOf('\\n')) >= 0) {
    const line = input.slice(0, end); input = input.slice(end + 1);
    const req = JSON.parse(line);
    let result = {};
    if (req.method === 'hooks/list') result = {data: []};
    else if (req.method === 'thread/start') result = {thread: {id: 'thread_fixture'}};
    else if (req.method === 'turn/start') result = {turn: {id: 'turn_fixture'}};
    else if (req.method === 'turn/interrupt') fs.writeFileSync('""" + str(cancel_marker) + """', JSON.stringify(req.params));
    if (req.id !== undefined) console.log(JSON.stringify({id: req.id, result}));
  }
});
""")
            launcher = sandbox.write_launcher(root / "codex-launcher", workspace=workspace,
                profile=profile, codex_entry=entry, codex_root=package, selected_auth=auth)
            turn_result = {}

            def verify(rpc):
                thread = rpc("thread/start", {})["thread"]["id"]
                turn = rpc("turn/start", {"threadId": thread})["turn"]["id"]
                deadline = rpc.arm_turn_deadline(thread, turn, .3)
                self.assertTrue(deadline.expired.wait(3))
                self.assertTrue(deadline.wait_cleanup(4))
                turn_result.update(expired=deadline.expired.is_set(),
                                   cancel_error=deadline.cancel_error,
                                   stop_error=deadline.stop_error)
                raise TimeoutError("active native turn deadline expired")

            began = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "active native turn"):
                installer.trust_codex_hooks(profile, profile / "hooks.json", "fixture-hook",
                    verify=verify, codex_binary=str(launcher), expected=0)
            self.assertLess(time.monotonic() - began, 5)
            self.assertEqual(turn_result, dict(expired=True, cancel_error=None, stop_error=None))
            self.assertEqual(json.loads(cancel_marker.read_text()),
                {"threadId": "thread_fixture", "turnId": "turn_fixture"})
            child_pid = int(child_pid_file.read_text())
            deadline = time.monotonic() + 2
            while alive(child_pid) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(alive(child_pid),
                "sandbox descendant remained after the app-server was stopped: " +
                Path(f"/proc/{child_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                if Path(f"/proc/{child_pid}/cmdline").exists() else "process remained")
            self.assertFalse((workspace / "false-settlement").exists())


if __name__ == "__main__":
    unittest.main()
