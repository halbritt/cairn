"""Exercise the existing disposable cluster wrapper with real local PostgreSQL."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
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

    def test_postmaster_death_preserves_command_status_and_removes_cluster(self):
        payload = r"""
import json, os, pathlib, signal, sys, time
root = pathlib.Path(os.environ['CAIRN_TASK_EVAL_PG']).parent
pid = int((root / 'data/postmaster.pid').read_text().splitlines()[0])
print(json.dumps({'root': str(root), 'pid': pid}), flush=True)
os.kill(pid, signal.SIGKILL)
time.sleep(0.3)
sys.exit(5)
"""
        done = self.invoke("--", sys.executable, "-c", payload)
        observed = json.loads(done.stdout)
        root = Path(observed["root"])
        # The failure case must not leave the disposable test data behind.
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(done.returncode, 5, done.stderr)
        self.assertFalse(root.exists(), "dead postmaster left its cluster directory")

    def test_process_group_sigterm_stops_command_and_cleans_cluster(self):
        payload = r"""
import json, os, pathlib, time
root = pathlib.Path(os.environ['CAIRN_TASK_EVAL_PG']).parent
print(json.dumps({'root': str(root), 'command_pid': os.getpid()}), flush=True)
time.sleep(60)
"""
        proc = subprocess.Popen(["bash", str(WRAPPER), "--", sys.executable, "-c", payload],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 20)[0], "command did not start")
            observed = json.loads(proc.stdout.readline())
            os.killpg(proc.pid, signal.SIGTERM)
            proc.communicate(timeout=15)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(Path(observed["root"]).exists(), "owned cluster directory remains")
            command = Path("/proc") / str(observed["command_pid"]) / "cmdline"
            if command.exists():
                self.assertEqual(command.read_bytes(), b"", "owned command is still running")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.communicate(timeout=15)

    def test_empty_explicit_command_is_rejected_before_database_setup(self):
        env = dict(os.environ, CAIRN_PG_BIN="/nonexistent-postgres")
        done = subprocess.run(["bash", str(WRAPPER), "--"], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(done.returncode, 2)
        self.assertIn("COMMAND", done.stderr)
        self.assertNotIn("initdb", done.stderr)

    def test_unknown_postgres_status_preserves_directory_and_command_failure(self):
        # Exercise pg_ctl's status contract without risking deletion of a live
        # cluster in the red regression. Normal cleanup uses real PG above.
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            initdb = bin_dir / "initdb"
            initdb.write_text("#!/bin/sh\nmkdir -p \"$2\"\n")
            initdb.chmod(0o700)
            ctl = bin_dir / "pg_ctl"
            ctl.write_text("#!" + sys.executable + "\n" + r'''
import json, os, pathlib, sys
args = sys.argv[1:]
data = pathlib.Path(args[args.index('-D') + 1])
if 'start' in args:
    (data / 'postmaster.pid').write_text('fixture-only-no-process\n')
    sys.exit(0)
if 'stop' in args:
    sys.exit(1)
counter = pathlib.Path(os.environ['FIXTURE_STATUS_COUNTER'])
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
codes = json.loads(os.environ['FIXTURE_STATUS_CODES'])
sys.exit(codes[min(n, len(codes)-1)])
''')
            ctl.chmod(0o700)
            for codes, command_status in (([4], 0), ([1], 37), ([0, 4], 0)):
                with self.subTest(codes=codes, command_status=command_status):
                    counter = bin_dir / "counter"
                    counter.unlink(missing_ok=True)
                    env = dict(os.environ, CAIRN_PG_BIN=str(bin_dir),
                               FIXTURE_STATUS_COUNTER=str(counter),
                               FIXTURE_STATUS_CODES=json.dumps(codes))
                    payload = "import os,pathlib,sys; print(pathlib.Path(os.environ['CAIRN_TASK_EVAL_PG']).parent); sys.exit(int(sys.argv[1]))"
                    done = subprocess.run(["bash", str(WRAPPER), "--", sys.executable,
                                           "-c", payload, str(command_status)], env=env,
                                          capture_output=True, text=True, timeout=10)
                    root = Path(done.stdout.strip())
                    self.addCleanup(shutil.rmtree, root, True)
                    self.assertTrue(root.is_dir(), "unknown server status deleted its directory")
                    self.assertEqual(done.returncode, command_status or 1, done.stderr)
                    self.assertIn("cleanup failed", done.stderr)


if __name__ == "__main__":
    unittest.main()
