"""Loopback-only regression probe for Codex app-server sockets under bwrap."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_socket_namespace_probe", ROOT / "scripts/check_codex_app_server_socket_namespace.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class CodexAppServerSocketNamespaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not probe.NATIVE.is_file() or shutil.which("bwrap") is None:
            raise unittest.SkipTest("pinned Codex binary and bwrap are required")
        version = subprocess.run([str(probe.NATIVE), "--version"], capture_output=True,
                                 text=True, timeout=5, check=True).stdout.strip()
        if version != "codex-cli 0.164.0":
            raise unittest.SkipTest("probe is pinned to Codex 0.164.0")

    def test_same_namespace_watcher_verifies_peer_and_exact_queue_add(self):
        report = probe.run_probe()
        self.assertEqual(report.get("queue_add"), "accepted", report)
        self.assertTrue(report.get("thread_loaded"), report)
        self.assertTrue(report.get("socket_peer", {}).get("pid_matches_app_server"), report)
        self.assertTrue(report.get("socket_peer", {}).get("uid_matches_watcher"), report)
        self.assertTrue(report.get("client_user_message_id_matches"), report)
        self.assertTrue(report.get("queued_submission_id_present"), report)
        self.assertEqual(report.get("queue_method"), "thread/queue/add", report)
        self.assertEqual(
            report.get("observer"), "isolated probe using production codex_queue client", report)
        self.assertTrue(report.get("local_response_stub_replied"), report)
        self.assertEqual(report.get("local_responses_requests"), 1, report)
        self.assertTrue(report.get("network", "").startswith("bwrap --unshare-net"), report)
        self.assertFalse(report.get("unexpected_local_paths"), report)
        outside = report.get("socket_outside", {})
        self.assertTrue(outside.get("entry_is_symlink"), report)
        self.assertTrue(outside.get("target_under_private_tmp"), report)
        self.assertFalse(outside.get("target_exists"), report)
        self.assertFalse(outside.get("target_is_socket"), report)
        self.assertEqual(report.get("native_item_started"), "not_observed", report)
        self.assertFalse(report.get("explicit_queue_start_called"), report)
        self.assertFalse(report.get("production_watcher"), report)
        self.assertFalse(report.get("cairn_api_mounted"), report)
        self.assertTrue(report.get("output_redacted"), report)
        self.assertTrue(report.get("scratch_removed"), report)
        self.assertNotIn("sk-cairn220-fake", str(report))
        self.assertFalse(report.get("stderr_truncated"), report)
        self.assertLessEqual(len(report.get("stderr", "").encode()), probe.MAX_STDERR)
        self.assertLessEqual(len(report.get("outer_bwrap_stderr", "").encode()), probe.MAX_STDERR)
        self.assertNotIn(str(Path.home()), str(report))

class ProbeFailureRedactionTests(unittest.TestCase):
    def test_failure_codes_do_not_retain_exception_or_refusal_text(self):
        self.assertEqual(probe._failure_code("app_server_socket_not_ready"),
                         "app_server_socket_not_ready")
        self.assertEqual(probe._failure_code("/private/path sk-secret-value"), "other")
        self.assertEqual(probe._native_refusal_code("STALE_SESSION"), "STALE_SESSION")
        self.assertEqual(probe._native_refusal_code("Bearer sk-secret-value"), "other")


if __name__ == "__main__":
    unittest.main()
