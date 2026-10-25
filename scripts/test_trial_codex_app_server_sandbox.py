"""Provider-free inventory proof for the Codex app-server sandbox launcher."""
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "trial_codex_app_server_sandbox", HERE / "trial_codex_app_server_sandbox.py")
sandbox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sandbox)


class AppServerSandboxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap") and Path("/usr/bin/node").exists(),
                         "bwrap and node are required")
    def test_only_actor_workspace_profile_auth_and_explicit_ro_mounts_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "actor"
            profile = workspace / "codex-home"
            profile.mkdir(parents=True)
            (workspace / "task.txt").write_text("one synthetic notice\n")
            selected_auth = base / "selected-auth.json"
            selected_auth.write_text('{"fixture":"selected account"}')
            controller = base / "hidden-rubric.json"
            controller.write_text('{"grade":"hidden"}')
            production_db = base / "production.db"
            production_db.write_text("synthetic marker only")
            production_config = base / "production.toml"
            production_config.write_text("synthetic marker only")
            production_socket = base / "production.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(production_socket))
            listener.close()
            codex_package = base / "codex-package"
            codex_package.mkdir()
            codex_entry = codex_package / "codex.js"
            codex_entry.write_text('''const fs = require('fs');
const auth = fs.readFileSync(process.env.CODEX_HOME + '/auth.json', 'utf8');
let authWriteDenied = false;
try { fs.writeFileSync(process.env.CODEX_HOME + '/auth.json', 'changed'); }
catch (_) { authWriteDenied = true; }
const report = {
  taskVisible: fs.existsSync(process.cwd() + '/task.txt'),
  authReadable: auth.includes('selected account'),
  authWriteDenied,
  hiddenRubricAbsent: !fs.existsSync(''' + json.dumps(str(controller)) + '''),
  productionDbAbsent: !fs.existsSync(''' + json.dumps(str(production_db)) + '''),
  productionConfigAbsent: !fs.existsSync(''' + json.dumps(str(production_config)) + '''),
  productionSocketAbsent: !fs.existsSync(''' + json.dumps(str(production_socket)) + '''),
  codexHarmAbsent: !fs.existsSync('/home/halbritt/.codex-harm'),
  codexHomeIsIsolated: process.env.CODEX_HOME !== '/home/halbritt/.codex'
};
console.log(JSON.stringify(report));
''')
            launcher = sandbox.write_launcher(
                base / "codex-launcher", workspace=workspace, profile=profile,
                codex_entry=codex_entry, selected_auth=selected_auth)
            result = subprocess.run([str(launcher), "app-server", "--stdio"],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout.strip())
            self.assertEqual(report, dict(taskVisible=True, authReadable=True,
                authWriteDenied=True, hiddenRubricAbsent=True, productionDbAbsent=True,
                productionConfigAbsent=True, productionSocketAbsent=True,
                codexHarmAbsent=True,
                codexHomeIsIsolated=True))

    def test_profile_must_be_inside_actor_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "actor"
            workspace.mkdir()
            profile = root / "profile"
            profile.mkdir()
            auth = root / "auth"
            auth.write_text("fixture")
            codex = root / "codex.js"
            codex.write_text("")
            with self.assertRaisesRegex(ValueError, "inside the actor workspace"):
                sandbox.write_launcher(root / "launcher", workspace=workspace,
                    profile=profile, codex_entry=codex, selected_auth=auth)

    def test_extra_directory_mount_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "actor"
            profile = workspace / ".codex"
            profile.mkdir(parents=True)
            auth = root / "auth"
            auth.write_text("synthetic")
            package = root / "package"
            package.mkdir()
            codex = package / "codex.js"
            codex.write_text("")
            hidden_dir = root / "controller"
            hidden_dir.mkdir()
            with self.assertRaisesRegex(ValueError, "individual files or Unix sockets"):
                sandbox.write_launcher(root / "launcher", workspace=workspace,
                    profile=profile, codex_entry=codex, selected_auth=auth,
                    extra_binds=[(hidden_dir, "/tmp/controller", "ro")])


if __name__ == "__main__":
    unittest.main()
