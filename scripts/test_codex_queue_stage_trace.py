"""Regression checks for the opt-in CAIRN-220 private queue-stage trace."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
OBSERVER = SCRIPTS / "fixtures" / "cairn220_queue_trace"
LIFECYCLE = SCRIPTS.parent / "integrations" / "lifecycle"


class CodexQueueStageTraceTests(unittest.TestCase):
    def test_postgres_log_classifier_retains_only_fixed_category(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            from check_codex_app_server_wake_join import _classify_pg_start_failure
        finally:
            sys.path.remove(str(SCRIPTS))

        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "postgres.log"
            log.write_text("could not create Unix-domain socket \"/private/path\": path too long\n")
            category = _classify_pg_start_failure(b"waiting failed", b"", log)
        self.assertEqual(category, "socket_path_too_long")
        self.assertNotIn("private/path", category)

    def test_trace_is_private_fixed_code_and_excludes_refusal_text(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            env = dict(os.environ)
            env.update(
                CAIRN220_QUEUE_TRACE=str(trace),
                PYTHONPATH=os.pathsep.join((str(OBSERVER), str(LIFECYCLE))),
            )
            code = r"""
import json, time
import codex_queue

class Peer:
    def settimeout(self, _value): pass
    def send(self, _value): pass
    def recv(self):
        return json.dumps({"id": 1, "error": {"code": -32600, "message": "PRIVATE WAKE TEXT"}})

rpc = codex_queue._Rpc(Peer(), time.monotonic() + 5)
try:
    rpc("thread/queue/add", {})
except codex_queue.QueueRefused:
    pass
else:
    raise AssertionError("the synthetic native refusal was not preserved")
"""
            completed = subprocess.run(
                [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]

        self.assertEqual(
            [(row["stage"], row["outcome"]) for row in rows],
            [("trace", "started"), ("queue_add", "before"), ("queue_add", "refused")],
        )
        self.assertEqual(rows[-1]["error_class"], "QueueRefused")
        self.assertEqual(rows[-1]["native_code"], -32600)
        self.assertNotIn("PRIVATE WAKE TEXT", json.dumps(rows))
        self.assertFalse(any("message" in row for row in rows))

    def test_queue_start_refusal_records_fixed_class_and_receipt_match(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            env = dict(os.environ)
            env.update(
                CAIRN220_QUEUE_TRACE=str(trace),
                PYTHONPATH=os.pathsep.join((str(OBSERVER), str(LIFECYCLE))),
            )
            code = r"""
import json, time
import codex_queue
class Peer:
    def settimeout(self, _value): pass
    def send(self, _value): pass
    def recv(self):
        return json.dumps({"id": 1, "error": {"code": -32600, "message": "PRIVATE NATIVE DETAIL"}})
rpc = codex_queue._Rpc(Peer(), time.monotonic() + 5)
try:
    rpc("thread/queue/start", {"threadId": "thread", "queuedSubmissionId": "receipt"}, busy_ok=True)
except codex_queue.QueueRefused:
    pass
else:
    raise AssertionError("the synthetic start refusal was not preserved")
"""
            completed = subprocess.run([sys.executable, "-c", code], env=env,
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
        refusal = rows[-1]
        self.assertEqual((refusal["stage"], refusal["outcome"]), ("queue_start", "refused"))
        self.assertEqual(refusal["native_refusal_class"], "invalid_request_other")
        self.assertFalse(refusal["receipt_matches_start"])
        self.assertNotIn("PRIVATE NATIVE DETAIL", json.dumps(rows))

    def test_api_start_classifier_uses_only_fixed_path_category(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            from check_codex_app_server_wake_join import _classify_api_start_output
        finally:
            sys.path.remove(str(SCRIPTS))
        long_socket = Path("/fixture/" + "x" * 120 + "/api.sock")
        envelope = b'{"ok":false,"status":"STORE_ERROR","message":"operation failed"}'
        self.assertEqual(_classify_api_start_output(envelope, b"", truncated=False,
                                                    socket_path=long_socket),
                         "api_socket_path_too_long")

    def test_trace_creation_rejects_symlink_paths_and_existing_targets(self):
        sys.path.insert(0, str(OBSERVER))
        try:
            from trace_io import create_trace
        finally:
            sys.path.remove(str(OBSERVER))

        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private"
            private.mkdir(mode=0o700)
            private.chmod(0o700)
            target = private / "trace.jsonl"
            descriptor = create_trace(str(target))
            os.close(descriptor)
            with self.assertRaises(FileExistsError):
                create_trace(str(target))
            linked_parent = Path(directory) / "linked"
            linked_parent.symlink_to(private, target_is_directory=True)
            with self.assertRaises(OSError):
                create_trace(str(linked_parent / "other.jsonl"))
            linked_target = private / "linked.jsonl"
            linked_target.symlink_to(target)
            with self.assertRaises(OSError):
                create_trace(str(linked_target))


if __name__ == "__main__":
    unittest.main()
