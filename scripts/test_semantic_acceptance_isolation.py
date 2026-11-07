import json
import os
from pathlib import Path
import pwd
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch

from semantic_acceptance_isolation import (
    DisposableBacking,
    IsolationError,
    SemanticIsolation,
    augment_sealed_prefix,
    derive_w_prefix,
)


class SemanticIsolationTest(unittest.TestCase):
    def test_owned_backing_and_sealed_prefix_refuse_reuse_or_unsealed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backing = DisposableBacking(root / "disposable")
            self.assertEqual(backing.path.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(IsolationError):
                DisposableBacking(backing.path)
            with self.assertRaisesRegex(IsolationError, "seal"):
                SemanticIsolation.sealed_oracle(
                    sealed_root=root,
                    backing=backing,
                    read_only_paths=[],
                    output_dir=root / "capture",
                )
            self.assertTrue(backing.path.exists())
            backing.remove()
            self.assertFalse(backing.path.exists())

    def test_sealed_prefix_adds_only_read_only_binds_without_rewriting_environment(
        self,
    ):
        prefix = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--clearenv",
            "--setenv",
            "HOME",
            "/sandbox/home",
            "--chdir",
            "/oracle",
            "--remount-ro",
            "/",
        ]
        actual = augment_sealed_prefix(
            prefix, [("/capture/tool.py", "/capture/tool.py")]
        )
        self.assertEqual(
            actual,
            prefix[:-2]
            + ["--ro-bind", "/capture/tool.py", "/capture/tool.py"]
            + prefix[-2:],
        )
        with self.assertRaises(IsolationError):
            augment_sealed_prefix(prefix + ["--", "worker"], [])
        w = ["original"] * 33 + [
            "--bind",
            "/frozen/disposable",
            "/sandbox",
            "--remount-ro",
            "/",
        ]
        derived = derive_w_prefix(w, "/frozen/disposable", "/fresh/private")
        self.assertEqual(
            [i for i, (old, new) in enumerate(zip(w, derived)) if old != new], [34]
        )
        with self.assertRaises(IsolationError):
            derive_w_prefix(w, "/wrong-source", "/fresh/private")

    def test_host_proc_cannot_be_added_as_a_read_only_runtime_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(IsolationError):
                with SemanticIsolation(
                    read_only_paths=["/proc"], output_dir=Path(directory) / "out"
                ):
                    self.fail("host proc exposed")

    def test_already_exited_namespace_leader_leaves_no_detached_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            with SemanticIsolation(
                read_only_paths=[], output_dir=Path(directory) / "out"
            ) as isolation:
                process = isolation.popen(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                        "import os,time;pid=os.fork();"
                        "(os.setsid(),print('child-ready',flush=True),time.sleep(10)) if pid==0 else time.sleep(.2)",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(process.stdout.readline(), b"child-ready\n")
                time.sleep(0.1)
                observed = isolation.runtime_evidence()["processes"]
                child_pids = {p["pid"] for p in observed if "python" in p["executable"]}
                self.assertGreaterEqual(len(child_pids), 2, observed)
                process.wait(timeout=3)
                # Cleanup must run even after wait() has already reaped the launcher.
            self.assertTrue(
                all(not Path(f"/proc/{pid}").exists() for pid in child_pids)
            )
            cleanup = json.loads((Path(directory) / "out/cleanup.json").read_text())
            self.assertTrue(cleanup["all_observed_processes_exited"])

    def test_existing_output_is_preserved_and_preflight_failure_removes_private_home(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            existing.mkdir()
            marker = existing / "history"
            marker.write_text("preserve")
            with self.assertRaises(IsolationError):
                with SemanticIsolation(read_only_paths=[], output_dir=existing):
                    self.fail("existing output accepted")
            self.assertEqual(marker.read_text(), "preserve")
            failed = root / "failed"
            with patch(
                "semantic_acceptance_isolation.shutil.which", return_value="/bin/false"
            ):
                with self.assertRaises(IsolationError):
                    with SemanticIsolation(read_only_paths=[], output_dir=failed):
                        self.fail("failed namespace accepted")
            self.assertFalse((failed / "mapped-home").exists())

    def test_home_lookup_without_home_env_is_disposable_and_runtime_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            marker = runtime / "marker"
            marker.write_text("unchanged")
            with SemanticIsolation(
                read_only_paths=[runtime], output_dir=root / "output"
            ) as isolation:
                result = isolation.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                        "import os,pwd,pathlib,json; "
                        "home=pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir); "
                        "(home/'captured').write_text('private'); "
                        "print(json.dumps(dict(home=str(home),env_home=os.getenv('HOME'))))",
                    ],
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                reply = json.loads(result.stdout)
                self.assertEqual(reply["home"], pwd.getpwuid(os.getuid()).pw_dir)
                self.assertIsNone(reply["env_home"])
                self.assertEqual(
                    (isolation.home_dir / "captured").read_text(), "private"
                )
                self.assertNotEqual(
                    isolation.proof["network_namespace"],
                    os.readlink("/proc/self/ns/net"),
                )
                write = isolation.run(
                    ["/bin/sh", "-c", 'printf changed > "$1"', "sh", str(marker)],
                    timeout=5,
                )
                self.assertNotEqual(write.returncode, 0)
            self.assertEqual(marker.read_text(), "unchanged")

    def test_output_limit_stops_a_worker_before_its_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            with SemanticIsolation(
                read_only_paths=[], output_dir=Path(directory) / "out"
            ) as isolation:
                with self.assertRaisesRegex(IsolationError, "stdout exceeds"):
                    isolation.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-S",
                            "-c",
                            "import sys,time;sys.stdout.write('x'*2048);sys.stdout.flush();time.sleep(10)",
                        ],
                        timeout=1,
                        stdout_limit=1024,
                    )

    def test_runtime_mount_cannot_follow_a_link_outside_its_selected_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (root / "unselected").write_text("private")
            (runtime / "escape").symlink_to(root / "unselected")
            with self.assertRaisesRegex(IsolationError, "symlink"):
                with SemanticIsolation(
                    read_only_paths=[runtime], output_dir=root / "out"
                ):
                    self.fail("unsafe runtime closure accepted")

    def test_observer_identifies_the_actual_child_and_requires_ort_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with SemanticIsolation(
                read_only_paths=[], output_dir=Path(directory) / "out"
            ) as isolation:
                process = isolation.popen(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                        "import math,time;print('ready',flush=True);time.sleep(10)",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(process.stdout.readline(), b"ready\n")
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    evidence = isolation.runtime_evidence()
                    children = [
                        p for p in evidence["processes"] if "python" in p["executable"]
                    ]
                    if children and children[0]["libraries"]:
                        break
                    time.sleep(0.02)
                self.assertTrue(children, evidence)
                self.assertNotEqual(children[0]["pid"], process.pid)
                self.assertTrue(
                    all(
                        lib["inode"] > 0 and len(lib["sha256"]) == 64
                        for lib in children[0]["libraries"]
                    )
                )
                with self.assertRaisesRegex(IsolationError, "ORT"):
                    isolation.runtime_evidence(require_ort=True)
            self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
