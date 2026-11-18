"""Exercise installation and the installed CLI with disposable local files."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-development-checkpoint.py"
SOURCE = ROOT / "scripts/development_checkpoint.py"


class InstallDevelopmentCheckpointTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="checkpoint-install-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.target = self.root / "bin with spaces/cairn-development-checkpoint"

    def run_cli(self, *argv, expected=0):
        result = subprocess.run(argv, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def installer(self, *args, expected=0):
        return self.run_cli(sys.executable, "-B", str(INSTALLER), "--target", str(self.target),
                            *args, expected=expected)

    def test_installed_command_packs_and_inspects_and_reinstall_is_noop(self):
        self.installer("--check", expected=1)
        self.assertFalse(self.target.parent.exists())
        self.installer()
        self.assertEqual(self.target.read_bytes(), SOURCE.read_bytes())
        before = self.target.stat()
        self.installer()
        self.installer("--check")
        after = self.target.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.run_cli(str(self.target), "--help")

        source = self.root / "selected.txt"
        source.write_text("selected content\n")
        declaration = self.root / "declaration.json"
        declaration.write_text(json.dumps({
            "schema": "development-checkpoint/1", "project": "fixture", "goal": "inspect selected content",
            "owner": {"name": "fixture", "provenance": "test"}, "workspace": str(self.root),
            "next_action": "read the selected content", "references": [
                {"name": "selected", "kind": "file", "path": str(source)}]}))
        checkpoint = self.root / "checkpoint.json"
        self.run_cli(str(self.target), "pack", str(declaration), "--output", str(checkpoint))
        inspected = self.run_cli(str(self.target), "status", str(checkpoint))
        self.assertEqual(json.loads(inspected.stdout)["reference_status"], "current")
        source.write_text("changed content\n")
        stale = self.run_cli(str(self.target), "status", str(checkpoint), expected=1)
        self.assertEqual(json.loads(stale.stdout)["reference_status"], "stale")

    def test_changed_bytes_permission_and_symlink_are_detected_then_repaired(self):
        self.installer()
        self.target.write_text("changed executable\n")
        self.installer("--check", expected=1)
        self.assertEqual(self.target.read_text(), "changed executable\n")
        self.installer()
        self.target.chmod(0o644)
        self.installer("--check", expected=1)
        self.installer()
        self.installer("--check")

        referent = self.root / "leave-this-file"
        referent.write_text("unrelated\n")
        self.target.unlink()
        self.target.symlink_to(referent)
        self.installer("--check", expected=1)
        self.assertTrue(self.target.is_symlink())
        self.installer()
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(referent.read_text(), "unrelated\n")
        self.installer("--check")

    def test_failed_replacement_preserves_directory_and_removes_temporary_file(self):
        self.target.mkdir(parents=True)
        sentinel = self.target / "preserve"
        sentinel.write_text("unrelated\n")
        self.installer(expected=2)
        self.assertEqual(sentinel.read_text(), "unrelated\n")
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])


if __name__ == "__main__":
    unittest.main()
