import json
import hashlib
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
    stop_process,
)


class SemanticIsolationTest(unittest.TestCase):
    def synthetic_w(self, root, *, bad_preflight=False):
        frozen = root / "frozen"
        frozen.mkdir()
        for name in ("inventory", "preflight", "etc", "reference", "runtime", "assets"):
            (frozen / name).mkdir()
        (frozen / "etc/passwd").write_text(
            f"fixture:x:{os.getuid()}:{os.getgid()}:fixture:/sandbox/home:/bin/sh\n"
        )
        (frozen / "etc/group").write_text(f"fixture:x:{os.getgid()}:\n")
        env = dict(
            PATH="/usr/bin:/bin",
            HF_HUB_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            TOKENIZERS_PARALLELISM="false",
            HOME="/sandbox/home",
            XDG_CACHE_HOME="/sandbox/cache",
            XDG_CONFIG_HOME="/sandbox/config",
            XDG_DATA_HOME="/sandbox/data",
            XDG_STATE_HOME="/sandbox/state",
            TMPDIR="/sandbox/tmp",
            FASTEMBED_CACHE_PATH="/sandbox/fastembed",
            PYTHONDONTWRITEBYTECODE="1",
        )
        prefix = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--clearenv",
            "--hostname",
            "fixture",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            str(frozen / "runtime"),
            "/runtime/venv",
            "--ro-bind",
            str(frozen / "runtime"),
            "/runtime/base",
            "--ro-bind",
            str(frozen / "runtime"),
            "/runtime/alias",
            "--ro-bind",
            str(frozen / "reference"),
            "/oracle-1276-180s",
            "--ro-bind",
            str(frozen / "assets"),
            "/model",
            "--bind",
            str(frozen / "disposable"),
            "/sandbox",
            "--proc",
            "/proc",
            "--tmpfs",
            "/proc/sys",
            "--remount-ro",
            "/proc/sys",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/etc",
            "--ro-bind",
            str(frozen / "etc/passwd"),
            "/etc/passwd",
            "--ro-bind",
            str(frozen / "etc/group"),
            "/etc/group",
            "--ro-bind",
            "/etc/ld.so.cache",
            "/etc/ld.so.cache",
        ]
        for key, value in env.items():
            prefix += ["--setenv", key, value]
        prefix += ["--chdir", "/oracle-1276-180s", "--remount-ro", "/"]
        self.assertEqual(len(prefix), 97)
        (frozen / "inventory/experiment-manifest.json").write_text(
            json.dumps(dict(sandbox_prefix=prefix, sandbox_environment=env))
        )
        (frozen / "oracle-attempt.json").write_text(
            json.dumps(
                dict(command=prefix + ["--", "/usr/bin/true"], sandbox_environment=env)
            )
        )
        preflight = list(prefix)
        if bad_preflight:
            preflight[5] = "wrong-hostname"
        (frozen / "preflight/full-isolation-command.json").write_text(
            json.dumps(dict(command=preflight + ["--", "/usr/bin/true"]))
        )
        for number in range(26):
            (frozen / f"fixture-{number:02d}").write_text("unchanged")
        files = sorted(p for p in frozen.rglob("*") if p.is_file())
        self.assertEqual(len(files), 31)
        seal = "".join(
            hashlib.sha256(p.read_bytes()).hexdigest()
            + "  "
            + str(p.relative_to(frozen))
            + "\n"
            for p in files
        )
        (frozen / "SHA256SUMS").write_text(seal)
        task = root / "task"
        task.mkdir(mode=0o700)
        return frozen, task, hashlib.sha256(seal.encode()).hexdigest()

    def test_derived_w_runs_nonmodel_preflight_and_checks_cleanup_and_all_three_records(
        self,
    ):
        for fault in ("none", "tampered-after", "third-prefix"):
            with (
                self.subTest(fault=fault),
                tempfile.TemporaryDirectory(prefix="cairn-semantic-") as directory,
            ):
                root = Path(directory)
                frozen, task, pin = self.synthetic_w(
                    root, bad_preflight=fault == "third-prefix"
                )
                backing = DisposableBacking(task / "backing")
                tool = root / "tool"
                tool.write_text("read-only")
                with patch("semantic_acceptance_isolation.SEALED_ORACLE_SHA256", pin):
                    if fault == "third-prefix":
                        with self.assertRaisesRegex(IsolationError, "prefix"):
                            SemanticIsolation.sealed_oracle(
                                sealed_root=frozen,
                                backing=backing,
                                task_scratch_roots=[task],
                                read_only_paths=[tool],
                                output_dir=root / "out",
                            )
                        backing.remove()
                        continue
                    boundary = SemanticIsolation.sealed_oracle(
                        sealed_root=frozen,
                        backing=backing,
                        task_scratch_roots=[task],
                        read_only_paths=[tool],
                        output_dir=root / "out",
                    )

                    def execute():
                        with boundary:
                            self.assertEqual(
                                set(p.name for p in backing.path.iterdir()),
                                {
                                    "home",
                                    "cache",
                                    "config",
                                    "data",
                                    "state",
                                    "tmp",
                                    "fastembed",
                                },
                            )
                            proof = boundary.proof
                            self.assertEqual(proof["profile"], "derived-W")
                            self.assertEqual(
                                proof["backing_argument_diff"][0]["index"], 34
                            )
                            self.assertTrue(
                                all(
                                    item["read_only"]
                                    for item in proof["read_only_mounts"]
                                )
                            )
                            observed = boundary.run(
                                [
                                    "/usr/bin/python3",
                                    "-I",
                                    "-S",
                                    "-c",
                                    "import os,json;print(json.dumps(dict(os.environ)))",
                                ],
                                timeout=5,
                            )
                            self.assertEqual(
                                json.loads(observed.stdout),
                                boundary.command_contract()["cpython_environment"],
                            )
                            with self.assertRaises(IsolationError):
                                boundary.command_contract(env={})
                            if fault == "tampered-after":
                                (frozen / "fixture-00").write_text("tampered")

                    if fault == "tampered-after":
                        with self.assertRaisesRegex(IsolationError, "sealed"):
                            execute()
                    else:
                        execute()
                    self.assertFalse(backing.path.exists())
                    cleanup = json.loads((root / "out/cleanup.json").read_text())
                    self.assertTrue(cleanup["backing_removed"])
                    self.assertTrue(cleanup["all_observed_processes_exited"])
                    self.assertEqual(bool(cleanup["errors"]), fault == "tampered-after")
                    self.assertFalse((frozen / "disposable").exists())

    def test_command_environment_is_declared_exactly_including_custom_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with SemanticIsolation(
                read_only_paths=[], output_dir=Path(directory) / "out"
            ) as boundary:
                for env in (
                    None,
                    dict(PATH="/usr/bin:/bin", CAIRN_SEMANTIC_STREAM_CACHE="1"),
                ):
                    expected = boundary.command_contract(env=env)
                    result = boundary.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-S",
                            "-c",
                            "import json,os;print(json.dumps(dict(os.environ)))",
                        ],
                        env=env,
                        timeout=5,
                    )
                    self.assertEqual(
                        json.loads(result.stdout), expected["cpython_environment"]
                    )
                    self.assertEqual(result.invocation["contract"], expected)
                    self.assertNotIn("model_environment", boundary.proof)

    def test_timeout_has_machine_reason_partial_bytes_and_joined_process(self):
        with tempfile.TemporaryDirectory() as directory:
            with SemanticIsolation(
                read_only_paths=[], output_dir=Path(directory) / "out"
            ) as boundary:
                with self.assertRaises(IsolationError) as caught:
                    boundary.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-S",
                            "-c",
                            "import sys,time;print('partial',flush=True);print('detail',file=sys.stderr,flush=True);time.sleep(10)",
                        ],
                        timeout=1,
                    )
                error = caught.exception
                self.assertEqual(error.code, "timeout")
                self.assertEqual(error.evidence["stdout"], b"partial\n")
                self.assertEqual(error.evidence["stderr"], b"detail\n")
                self.assertIsNotNone(error.evidence["exit_code"])
                self.assertTrue(error.evidence["processes_joined"])
                self.assertEqual(error.evidence["timeout_seconds"], 1)

    def test_each_invocation_needs_its_own_pinned_runtime_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A marker .so from the protocol fixture is not an ORT/model runtime.
            source = (
                Path(__file__).resolve().parents[1]
                / "semantic/testdata/executable-harness/protocol-worker.c"
            )
            library = root / "libonnxruntime.so"
            subprocess.run(
                ["gcc", "-shared", "-fPIC", str(source), "-o", str(library)],
                check=True,
                capture_output=True,
            )
            with SemanticIsolation(
                read_only_paths=[root], output_dir=root / "out"
            ) as boundary:
                first = boundary.popen(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                        "import ctypes,sys;handle=ctypes.CDLL(sys.argv[1]);print('ready',flush=True);sys.stdin.read()",
                        str(library),
                    ],
                    role="reference",
                    mode="rank",
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(first.stdout.readline(), b"ready\n")
                first_invocation = first._semantic_invocation
                second = boundary.run(
                    ["/usr/bin/true"], role="candidate", mode="rank", timeout=5
                )
                pins = [
                    dict(
                        path=str(library),
                        sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                    )
                ]
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    observed = boundary.runtime_evidence()["model_workers"]
                    if any(
                        any(
                            lib["sha256"] == pins[0]["sha256"] for lib in p["libraries"]
                        )
                        for p in observed
                    ):
                        break
                    time.sleep(0.02)
                own = boundary.invocation_runtime(first_invocation, pins)
                self.assertEqual(own["launcher_pid"], first_invocation["pid"])
                for changed in (
                    dict(first_invocation, role="candidate"),
                    dict(first_invocation, mode="embed"),
                ):
                    with self.assertRaises(IsolationError):
                        boundary.invocation_runtime(changed, pins)
                stop_process(first)
                with self.assertRaises(IsolationError):
                    boundary.invocation_runtime(second.invocation, pins)
                with self.assertRaises(IsolationError):
                    boundary.runtime_evidence(require_ort=True)
                with self.assertRaises(IsolationError):
                    boundary.invocation_runtime(
                        first_invocation, [dict(path=str(library), sha256="0" * 64)]
                    )

    def test_task_allowlist_and_proof_tampering_refuse_without_leaking_backing(self):
        from semantic_acceptance_isolation import task_scratch_identity

        for broad in (Path.home() / ".cache", Path.home() / ".local/share/cairn"):
            with self.subTest(broad=broad), self.assertRaises(IsolationError):
                task_scratch_identity(broad)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = SemanticIsolation(read_only_paths=[], output_dir=root / "out")
            with self.assertRaisesRegex(IsolationError, "proof"):
                with boundary:
                    boundary.run(
                        [
                            "/bin/sh",
                            "-c",
                            'chmod u+w "$1"; printf tampered > "$1"',
                            "sh",
                            str(root / "out/isolation-proof.json"),
                        ],
                        timeout=5,
                    )
            self.assertFalse((root / "out/mapped-home").exists())

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

    def test_declared_read_only_mounts_refuse_host_proc_or_writable_shadow(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(IsolationError):
                with SemanticIsolation(
                    read_only_paths=["/proc"], output_dir=Path(directory) / "out"
                ):
                    self.fail("host proc exposed")
            output = Path(directory) / "shadow"
            with self.assertRaises(IsolationError):
                with SemanticIsolation(read_only_paths=[output], output_dir=output):
                    self.fail("writable output claimed as a read-only mount")

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
