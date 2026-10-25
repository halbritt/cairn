"""The deadline requests protocol cancellation, then stops only its owned server."""
import importlib.util
from pathlib import Path
import threading
import unittest


SPEC = importlib.util.spec_from_file_location(
    "trial_codex_turn_deadline", Path(__file__).with_name("trial_codex_turn_deadline.py"))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "codex_cancel_error_installer", Path(__file__).with_name("install-agent-coordination.py"))
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


class ActiveTurnDeadlineTests(unittest.TestCase):
    def test_expiry_cancels_turn_then_stops_owned_server(self):
        calls = []
        deadline = module.ActiveTurnDeadline(.02,
            lambda: calls.append("cancel"), lambda: calls.append("stop")).start()
        self.assertTrue(deadline.expired.wait(1))
        self.assertTrue(deadline.wait_cleanup(1))
        self.assertEqual(calls, ["cancel", "stop"])

    def test_successful_turn_disarms_deadline_without_cancel(self):
        calls = []
        deadline = module.ActiveTurnDeadline(.2,
            lambda: calls.append("cancel"), lambda: calls.append("stop")).start()
        deadline.disarm()
        self.assertFalse(deadline.expired.is_set())
        self.assertEqual(calls, [])

    def test_expiry_still_stops_server_if_cancel_rpc_fails(self):
        calls = []
        def unavailable():
            calls.append("cancel")
            raise TimeoutError("unresponsive")
        deadline = module.ActiveTurnDeadline(.02, unavailable,
            lambda: calls.append("stop")).start()
        self.assertTrue(deadline.wait_cleanup(1))
        self.assertEqual(calls, ["cancel", "stop"])
        self.assertEqual(deadline.cancel_error, "TimeoutError")

    def test_cancel_rpc_error_retains_only_redacted_code_and_message(self):
        error = installer.CodexRPCError("turn/cancel", dict(code=-32602,
            message="invalid turn abcdef01-2345-6789-abcd-0123456789ab token=secret-value at /home/owner/private"))
        def unavailable():
            raise error
        deadline = module.ActiveTurnDeadline(.01, unavailable, lambda: None).start()
        self.assertTrue(deadline.wait_cleanup(1))
        self.assertEqual(deadline.cancel_error_code, -32602)
        self.assertIn("invalid turn <uuid>", deadline.cancel_error_message)
        self.assertIn("token=<redacted>", deadline.cancel_error_message)
        self.assertNotIn("secret-value", deadline.cancel_error_message)
        self.assertNotIn("/home/owner", deadline.cancel_error_message)


if __name__ == "__main__":
    unittest.main()
