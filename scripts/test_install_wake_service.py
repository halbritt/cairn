"""Tests for install-wake-service.py validating the exact bytes persisted."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_wake_service", ROOT / "scripts/install-wake-service.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallWakeServiceTests(unittest.TestCase):
    def test_validates_exact_persisted_temporary_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "cairn"
            home.mkdir()
            unit_dir = tmp_path / "systemd"
            unit_dir.mkdir()
            source = tmp_path / "valid.json"
            source.write_text(json.dumps({"name": "worker1", "cmd": "echo"}))

            validated_files = []

            def fake_run(cmd, check=True):
                if cmd[1:3] == ["wake", "check"]:
                    validated_config = Path(cmd[4])
                    # Simulate mutating or deleting the source file while check is running
                    source.write_text(json.dumps({"name": "invalid_name", "cmd": "echo"}))
                    self.assertTrue(validated_config.exists())
                    self.assertEqual(validated_config.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(validated_config.parent, home / "wake")
                    validated_files.append(validated_config.read_text())
                    return mock.Mock(returncode=0)
                return mock.Mock(returncode=0)

            with mock.patch("subprocess.run", side_effect=fake_run):
                installer.install(Path("/bin/cairn"), source, home, unit_dir)

            dest = home / "wake/worker1.json"
            self.assertTrue(dest.exists())
            self.assertEqual(len(validated_files), 1)
            # The persisted file must match exactly what was checked, not the mutated source
            self.assertEqual(dest.read_text(), validated_files[0])
            self.assertIn("worker1", dest.read_text())
            self.assertNotIn("invalid_name", dest.read_text())

    def test_validation_failure_cleans_up_and_does_not_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "cairn"
            home.mkdir()
            unit_dir = tmp_path / "systemd"
            unit_dir.mkdir()
            source = tmp_path / "invalid.json"
            source.write_text(json.dumps({"name": "worker-fail", "cmd": "echo"}))

            def fake_run(cmd, check=True):
                if cmd[1:3] == ["wake", "check"]:
                    raise subprocess.CalledProcessError(1, cmd)
                return mock.Mock(returncode=0)

            with mock.patch("subprocess.run", side_effect=fake_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(Path("/bin/cairn"), source, home, unit_dir)

            dest = home / "wake/worker-fail.json"
            self.assertFalse(dest.exists())
            # Ensure no stray temp files left
            stray = list(home.glob("**/.install-*"))
            self.assertEqual(stray, [])

    def test_stop_and_replace_failures_clean_up_and_preserve_config(self):
        for failure in ("stop", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                home = root / "cairn"
                directory = home / "wake"
                directory.mkdir(parents=True)
                unit_dir = root / "systemd"
                unit_dir.mkdir()
                destination = directory / "worker1.json"
                destination.write_text('{"name":"worker1","old":true}')
                original = destination.read_bytes()
                unit_path = unit_dir / "cairn-wake-worker1.service"
                unit_path.write_text("old unit")
                source = root / "source.json"
                source.write_text('{"name":"worker1"}')

                def fake_run(cmd, check=True):
                    if failure == "stop" and cmd[2] == "stop":
                        raise subprocess.CalledProcessError(1, cmd)
                    return mock.Mock(returncode=0)

                original_replace = Path.replace

                def replace(path, target):
                    if failure == "replace" and target == destination:
                        raise OSError("replace failed")
                    return original_replace(path, target)

                with mock.patch.object(installer.subprocess, "run", side_effect=fake_run), mock.patch.object(Path, "replace", replace):
                    with self.assertRaises((subprocess.CalledProcessError, OSError)):
                        installer.install(Path("/bin/cairn"), source, home, unit_dir)
                self.assertEqual(list(directory.glob(".install-*")), [])
                self.assertEqual(destination.read_bytes(), original)
                self.assertEqual(unit_path.read_text(), "old unit")

    def test_invalid_name_traversal_rejected_without_external_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "cairn"
            home.mkdir()
            unit_dir = tmp_path / "systemd"
            unit_dir.mkdir()
            source = tmp_path / "traversal.json"
            # Sentinel with directory traversal in name
            source.write_text(json.dumps({"name": "../../etc/sentinel", "cmd": "echo"}))

            def fake_run(cmd, check=True):
                if cmd[1:3] == ["wake", "check"]:
                    # cairn wake check rejects traversal in name
                    raise subprocess.CalledProcessError(1, cmd)
                return mock.Mock(returncode=0)

            with mock.patch("subprocess.run", side_effect=fake_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(Path("/bin/cairn"), source, home, unit_dir)

            # Ensure nothing was written outside wake or to sentinel path
            self.assertFalse((tmp_path / "etc/sentinel").exists())
            self.assertFalse((home / "wake/../../etc/sentinel.json").exists())
            stray = list(home.glob("**/.install-*"))
            self.assertEqual(stray, [])

    def test_installed_name_matches_go_case_and_duplicate_field_selection(self):
        for body in (
            '{"name":"../../outside","Name":"worker1"}',
            '{"Name":"worker1","name":null}',
            '{"name":"old","Name":"other","name":"worker1"}',
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "source.json"
                source.write_text(body)
                with mock.patch.object(installer.subprocess, "run"):
                    installer.install(Path("/bin/cairn"), source, root / "cairn", root / "systemd")
                self.assertEqual((root / "cairn/wake/worker1.json").read_text(), body)
                self.assertIn("worker1.json", (root / "systemd/cairn-wake-worker1.service").read_text())
                self.assertEqual(list((root / "cairn/wake").glob(".install-*")), [])

    def test_same_unit_activation_is_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "cairn"
            unit_dir = root / "systemd"
            source = root / "source.json"
            source.write_text('{"name":"worker1"}')
            first_active = threading.Event()
            second_waiting = threading.Event()
            release_first = threading.Event()
            second_active = threading.Event()
            real_flock = installer.fcntl.flock
            acquisitions = []

            def flock(stream, operation):
                acquisitions.append(stream)
                if len(acquisitions) == 2:
                    with self.assertRaises(BlockingIOError):
                        real_flock(stream, operation | installer.fcntl.LOCK_NB)
                    second_waiting.set()
                return real_flock(stream, operation)

            def run(cmd, check=True):
                if cmd[2] == "is-active":
                    if not first_active.is_set():
                        first_active.set()
                        if not release_first.wait(5):
                            raise AssertionError("first activation not released")
                    else:
                        second_active.set()
                return mock.Mock(returncode=0)

            with mock.patch.object(installer.subprocess, "run", side_effect=run), mock.patch.object(installer.fcntl, "flock", side_effect=flock):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(installer.install, Path("/bin/cairn"), source, home, unit_dir)
                    try:
                        self.assertTrue(first_active.wait(5))
                        second = executor.submit(installer.install, Path("/bin/cairn"), source, home, unit_dir)
                        self.assertTrue(second_waiting.wait(5))
                        self.assertFalse(second_active.is_set())
                    finally:
                        release_first.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
            self.assertTrue(second_active.is_set())
            self.assertEqual(list((home / "wake").glob(".install-*")), [])

    def test_concurrent_installs_do_not_collide_on_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "cairn"
            home.mkdir()
            unit_dir = tmp_path / "systemd"
            unit_dir.mkdir()

            def fake_run(cmd, check=True):
                return mock.Mock(returncode=0)

            def do_install(i):
                source = tmp_path / f"worker_{i}.json"
                source.write_text(json.dumps({"name": f"worker-{i}", "cmd": "echo"}))
                installer.install(Path("/bin/cairn"), source, home, unit_dir)

            with mock.patch("subprocess.run", side_effect=fake_run):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    list(executor.map(do_install, range(8)))

            for i in range(8):
                self.assertTrue((home / f"wake/worker-{i}.json").exists())
                self.assertTrue((unit_dir / f"cairn-wake-worker-{i}.service").exists())
            stray = list(home.glob("**/.install-*"))
            self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main()
