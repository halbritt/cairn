"""Exercise the existing disposable cluster wrapper with real local PostgreSQL."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


WRAPPER = Path(__file__).with_name("trial-task-eval.sh")
PAYLOAD = r"""
import json, os, pathlib, subprocess, sys
socket = pathlib.Path(os.environ['CAIRN_TASK_EVAL_PG'])
pg_bin = pathlib.Path(os.environ['CAIRN_TASK_EVAL_PG_BIN'])
result = subprocess.run([str(pg_bin / 'psql'), '-h', str(socket), '-d', 'postgres',
                         '-Atc', 'select current_database(), inet_server_addr() is null'],
                        check=True, capture_output=True, text=True)
root = socket.parent
print(json.dumps({'root': str(root), 'pid': int((root / 'data/postmaster.pid').read_text().splitlines()[0]),
                  'database': result.stdout.strip(), 'argument': sys.argv[1]}), flush=True)
sys.exit(int(sys.argv[2]))
"""


@unittest.skipUnless(shutil.which("pg_config"), "local PostgreSQL tools required")
class TrialTaskWrapperTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(["bash", str(WRAPPER), *args], capture_output=True, text=True, timeout=45)

    def test_explicit_command_preserves_arguments_status_and_cleans_cluster(self):
        for status in (0, 37):
            with self.subTest(status=status):
                argument = "spaces ; literal $HOME and $(not-a-command)"
                done = self.invoke("--", sys.executable, "-c", PAYLOAD, argument, str(status))
                self.assertEqual(done.returncode, status, done.stderr)
                observed = json.loads(done.stdout)
                self.assertEqual(observed["argument"], argument)
                self.assertEqual(observed["database"], "postgres|t")
                root = Path(observed["root"])
                self.assertEqual(root.parent, Path("/tmp"))
                self.assertTrue(root.name.startswith("cairn-task-eval-pg."))
                self.assertFalse(root.exists(), "owned cluster directory remains")
                process = Path("/proc") / str(observed["pid"]) / "cmdline"
                if process.exists():
                    self.assertNotIn(str(root).encode(), process.read_bytes(), "owned PostgreSQL remains")

    def test_default_command_still_accepts_native_evaluator_arguments(self):
        done = self.invoke("--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("retrieval", done.stdout)
        self.assertIn("agent", done.stdout)

    def test_empty_explicit_command_is_rejected_before_database_setup(self):
        env = dict(os.environ, CAIRN_PG_BIN="/nonexistent-postgres")
        done = subprocess.run(["bash", str(WRAPPER), "--"], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(done.returncode, 2)
        self.assertIn("COMMAND", done.stderr)
        self.assertNotIn("initdb", done.stderr)


if __name__ == "__main__":
    unittest.main()
