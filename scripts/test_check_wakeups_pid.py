"""Test process_exited_or_zombie in check_wakeups.py."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_wakeups", ROOT / "scripts/check_wakeups.py")
check_wakeups = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_wakeups)


class ProcessExitedOrZombieTests(unittest.TestCase):
    def test_handles_file_not_found_cleanly(self):
        with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError("No such process")):
            self.assertTrue(check_wakeups.process_exited_or_zombie(999999))

    def test_handles_process_lookup_error_cleanly(self):
        with mock.patch.object(Path, "read_text", side_effect=ProcessLookupError("No such process")):
            self.assertTrue(check_wakeups.process_exited_or_zombie(999999))

    def test_detects_zombie(self):
        with mock.patch.object(Path, "read_text", return_value="1234 (sleep) Z 1 1234 ..."):
            self.assertTrue(check_wakeups.process_exited_or_zombie(1234))

    def test_detects_running_process(self):
        with mock.patch.object(Path, "read_text", return_value="1234 (sleep) S 1 1234 ..."):
            self.assertFalse(check_wakeups.process_exited_or_zombie(1234))


if __name__ == "__main__":
    unittest.main()
