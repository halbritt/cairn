"""Installed and pinned lifecycle execution owns one complete code snapshot."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bundle_tests", HERE / "lifecycle_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class LifecycleBundleTests(unittest.TestCase):
    def test_every_lifecycle_source_is_in_the_installed_snapshot(self):
        runtime = {path.name for path in HERE.glob('*.py') if not path.name.startswith('test_')}
        self.assertEqual(set(bundle.MODULE_FILES), runtime)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def stage(self, name):
        root = self.root / name
        bundle.stage_runtime(HERE, root, {"memory.py": "lifecycle.py"})
        return root

    def pinned(self, root):
        path = root / "lifecycle.py"
        return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    bundle=bundle.bundle_descriptor(root))

    def run_entry(self, root):
        return subprocess.run([sys.executable, "-I", str(root / "lifecycle.py"), "--help"],
                              capture_output=True, text=True, timeout=3)

    def test_installed_entry_refuses_incomplete_changed_and_nonregular_code(self):
        root = self.stage("complete")
        self.assertEqual(self.run_entry(root).returncode, 0)
        for damage in ("changed", "missing", "manifest", "fifo", "loader_fifo", "alias"):
            with self.subTest(damage=damage):
                root = self.stage(damage)
                member = root / "grant_ledger.py"
                if damage == "changed":
                    member.write_bytes(member.read_bytes() + b"\nCHANGED = True\n")
                elif damage == "missing":
                    member.unlink()
                elif damage == "manifest":
                    (root / bundle.MANIFEST).unlink()
                elif damage in ("fifo", "loader_fifo"):
                    if damage == "loader_fifo":
                        member = root / "lifecycle_bundle.py"
                    member.unlink()
                    os.mkfifo(member)
                else:
                    member = root / "lifecycle.py"
                    member.write_bytes(member.read_bytes() + b"\nCHANGED = True\n")
                result = self.run_entry(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("usage:", result.stdout)

    def test_two_bundles_ignore_global_modules_and_keep_their_verified_helpers(self):
        first, second = self.stage("first"), self.stage("second")
        loader = bundle.Bundle(HERE)
        first_pin, second_pin = self.pinned(first), self.pinned(second)
        foreign = types.ModuleType("grant_ledger")
        foreign.GrantLedger = object()
        with patch.dict(sys.modules, {"grant_ledger": foreign,
                                      "integrations.lifecycle.grant_ledger": foreign}):
            a = loader.load_pinned(first_pin, "memory")
            b = loader.load_pinned(second_pin, "memory")
        self.assertIsNot(a.GrantLedger, b.GrantLedger)
        self.assertIsNot(a.GrantLedger, foreign.GrantLedger)
        self.assertEqual(a._grant_ledger.parse_wake_envelope("ordinary owner prompt"), None)
        helper = first / "grant_ledger.py"
        helper.write_text("raise AssertionError('unverified helper executed')\n")
        self.assertEqual(a._grant_ledger.parse_wake_envelope("ordinary owner prompt"), None)
        with self.assertRaises(ValueError):
            loader.load_pinned(first_pin, "memory")
        expected = hashlib.sha256((HERE / "memory.py").read_bytes()).hexdigest()
        self.assertEqual(json.loads(a.CALLER_DECLARATION)["implementation_id"], expected)
        self.assertEqual(a.__file__, str(first / "lifecycle.py"))

    def test_installed_coordination_loads_every_native_queue_adapter(self):
        root = self.stage("adapters")
        spec = importlib.util.spec_from_file_location("installed_coordination", root / "coordination.py")
        coordination = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(coordination)
        for name in ("codex_queue", "claude_channel", "opencode_queue", "hermes_queue"):
            with self.subTest(adapter=name):
                adapter = coordination._BUNDLE.load(name)
                self.assertEqual(adapter._transport.process_reference(os.getpid())["pid"], os.getpid())

    def test_installed_entry_points_share_the_staged_hook_admission(self):
        root = self.stage("admission")
        modules = {}
        for name in ("coordination", "memory"):
            spec = importlib.util.spec_from_file_location("installed_" + name, root / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
        admission = modules["coordination"]._hook_admission
        self.assertIs(admission, modules["coordination"]._BUNDLE.load("hook_admission"))
        self.assertEqual(Path(admission.__file__), root / "hook_admission.py")
        self.assertEqual(Path(modules["memory"]._hook_admission.__file__), root / "hook_admission.py")
        event = dict(hook_event_name="UserPromptSubmit", session_id="thread-one", cwd=str(root), model="m")
        self.assertEqual(modules["coordination"].normalize(dict(harness="codex"), event)["phase"], "busy")
        with patch.dict(os.environ, {"CAIRN_LIFECYCLE_DISABLED": "1"}):
            self.assertEqual(admission.decide("memory", dict(harness="codex"), event).reason, "disabled_env")
        (root / ".cairn-no-coordination").touch()
        self.assertTrue(modules["coordination"].coordination_excluded(root / "deeper"))
        native = modules["coordination"]._native_session
        self.assertEqual(Path(native.__file__), root / "native_session.py")
        self.assertIs(modules["coordination"].NativeSession, native.NativeSession)
        record = {}
        modules["coordination"].clear_inbox(record, root / "unused.json")
        self.assertEqual(record, {})
        self.assertTrue(admission.decide("memory", dict(harness="codex"), dict(event, session_id=str(__import__("uuid").uuid4()))).run)

    def test_copying_installed_bundle_preserves_checked_bytes_and_refuses_drift(self):
        original = self.stage("original")
        copied = self.root / "copied"
        bundle.stage_runtime(original, copied, {"memory.py": "lifecycle.py"})
        self.assertEqual(bundle.bundle_descriptor(original), bundle.bundle_descriptor(copied))
        self.assertEqual(self.run_entry(copied).returncode, 0)
        (original / "grant_ledger.py").write_text("changed\n")
        with self.assertRaises(ValueError):
            bundle.stage_runtime(original, self.root / "refused")
        self.assertFalse((self.root / "refused").exists())


if __name__ == "__main__":
    unittest.main()
