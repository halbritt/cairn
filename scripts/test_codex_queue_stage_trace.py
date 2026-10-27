"""Regression checks for the opt-in CAIRN-220 private queue-stage trace."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integrations.lifecycle import lifecycle_bundle
from scripts import test_codex_queue as queue_fixture


SCRIPTS = Path(__file__).resolve().parent
OBSERVER = SCRIPTS / "fixtures" / "cairn220_queue_trace"
LIFECYCLE = SCRIPTS.parent / "integrations" / "lifecycle"


class CodexQueueStageTraceTests(unittest.TestCase):
    def test_scripted_continuation_trace_keeps_only_fixed_schema_tool_and_error_fields(self):
        sys.path.insert(0, str(OBSERVER))
        try:
            from trace_io import append_trace, create_trace
        finally:
            sys.path.remove(str(OBSERVER))

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            fd = create_trace(str(trace))
            try:
                append_trace(fd, "responses_request", "observed", flags={
                    "step": "request", "request_index": 2, "input_item_count": 4,
                    "tool_result_count": 1, "has_input": True, "has_tools": True,
                    "raw_prompt": "PRIVATE PROMPT"})
                append_trace(fd, "native_tool_call", "observed", flags={
                    "step": "acknowledgement", "tool_name": "exec",
                    "tool_namespace": "functions", "call_role": "acknowledgement",
                    "command": "PRIVATE COMMAND"})
                append_trace(fd, "native_tool_result", "refused", flags={
                    "result_role": "source", "result_class": "source_mismatch",
                    "source_record_match": True, "source_version_match": False,
                    "source_body_match": True, "result_transport": "matched",
                    "result_status": "completed", "result_output_type": "string",
                    "result_error_present": False, "tool_execution_observed": True,
                    "exit_class": "nonzero", "execution_error_class": "no_such_file",
                    "expected_read_command_requested": True,
                    "cairn_api_envelope_observed": False, "api_read_status": "not_observed",
                    "api_read_invocation": "not_observed",
                    "raw_output": "PRIVATE OUTPUT"})
                append_trace(fd, "responses_response", "failed", error="RuntimeError",
                             flags={"failure_code": "native_tool_result_missing"})
            finally:
                os.close(fd)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
            mode = trace.stat().st_mode & 0o777

        self.assertEqual(mode, 0o600)
        self.assertEqual(rows[0]["request_index"], 2)
        self.assertEqual(rows[1]["tool_name"], "exec")
        self.assertEqual(rows[2]["source_version_match"], False)
        self.assertEqual(rows[2]["execution_error_class"], "no_such_file")
        self.assertEqual(rows[2]["api_read_status"], "not_observed")
        self.assertEqual(rows[2]["api_read_invocation"], "not_observed")
        self.assertEqual(rows[3]["failure_code"], "native_tool_result_missing")
        self.assertNotIn("PRIVATE", json.dumps(rows))
        self.assertFalse(any(key in row for row in rows for key in
                             ("raw_prompt", "command", "output", "message", "body", "raw_output")))

    def test_private_resume_message_is_capped_and_confined_to_resume_failure_row(self):
        sys.path.insert(0, str(OBSERVER))
        try:
            from trace_io import append_trace, create_trace, MAX_PRIVATE_REFUSAL_MESSAGE_BYTES
        finally:
            sys.path.remove(str(OBSERVER))

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            fd = create_trace(str(trace))
            try:
                append_trace(fd, "observer_resume", "failed", error="QueueRefused",
                             code=-32600, private_message="x" * (MAX_PRIVATE_REFUSAL_MESSAGE_BYTES + 20))
                append_trace(fd, "observer_subscription", "failed",
                             private_message="MUST NOT BE RETAINED")
            finally:
                os.close(fd)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
            mode = trace.stat().st_mode & 0o777

        self.assertEqual(mode, 0o600)
        self.assertEqual(len(rows[0]["private_refusal_message"].encode("utf-8")),
                         MAX_PRIVATE_REFUSAL_MESSAGE_BYTES)
        self.assertTrue(rows[0]["private_refusal_message_truncated"])
        self.assertEqual(rows[1], {"schema": "cairn.codex-queue-stage/1",
                                  "stage": "observer_subscription", "outcome": "failed"})
        self.assertNotIn("MUST NOT BE RETAINED", json.dumps(rows))

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            fd = create_trace(str(trace))
            os.fchmod(fd, 0o640)
            try:
                with self.assertRaisesRegex(ValueError, "owner-only trace"):
                    append_trace(fd, "observer_resume", "failed",
                                 private_message="MUST NOT BE WRITTEN")
            finally:
                os.close(fd)
            self.assertEqual(trace.read_text(), "")

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

    def observer(self):
        sys.path.insert(0, str(OBSERVER))
        try:
            spec = importlib.util.spec_from_file_location("cairn220_observer", OBSERVER / "watcher.py")
            observer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(observer)
            return observer
        finally:
            sys.path.remove(str(OBSERVER))

    def peer(self):
        peer = queue_fixture.NativeQueue()
        peer.setUp()
        self.addCleanup(peer.doCleanups)
        return peer

    def test_installed_bundle_queue_trace_preserves_results_and_private_refusals(self):
        observer = self.observer()
        for outcome in ("confirm", "missing", "private_refusal"):
            with self.subTest(outcome=outcome):
                peer = self.peer()
                root = Path(peer.temp.name)
                installed = root / "installed"
                lifecycle_bundle.stage_runtime(LIFECYCLE, installed)
                trace = root / "trace.jsonl"
                if outcome == "private_refusal":
                    peer.serve(start_error={"code": -32600, "message": "PRIVATE NATIVE DETAIL"})
                else:
                    peer.serve(start=outcome)
                with observer.observed_watcher(installed / "coordination.py", trace) as coordination:
                    queue = coordination.queue_helper()
                    result = queue.enqueue(peer.path, peer.process, "native-one", "PRIVATE WAKE TEXT", "delivery-one")
                peer.join()
                self.assertEqual(result, ("queued-one", outcome == "confirm"))
                self.assertEqual(peer.methods(), ["initialize", "initialized", "thread/loaded/list",
                                                   "thread/queue/add", "thread/queue/start"])
                rows = [json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
                self.assertEqual([(row["stage"], row["outcome"]) for row in rows], [
                    ("trace", "started"), ("observer_attach", "after"),
                    ("_open", "before"), ("_open", "after"), ("_admit", "before"), ("_admit", "after"),
                    ("queue_add", "before"), ("queue_add", "after"),
                    ("queue_start", "before"), ("queue_start", "after" if outcome == "confirm" else "refused")])
                added, started = rows[-3], rows[-1]
                self.assertTrue(added["receipt_confirmed"])
                self.assertTrue(added["delivery_id_matches"])
                self.assertTrue(started["receipt_matches_start"])
                if outcome == "confirm":
                    self.assertTrue(started["turn_started"])
                else:
                    self.assertEqual(started["native_code"], -32600)
                    self.assertEqual(started["error_class"], "QueueRefused")
                    self.assertEqual(started["native_refusal_class"],
                                     "submission_not_found" if outcome == "missing" else "invalid_request_other")
                    self.assertNotIn("turn_started", started)
                self.assertNotIn("PRIVATE", json.dumps(rows))
                self.assertNotIn(peer.path, json.dumps(rows))
                self.assertNotIn("delivery-one", json.dumps(rows))

    def test_observer_does_not_trace_another_module_or_survive_its_scope(self):
        observer = self.observer()
        peer = self.peer()
        root = Path(peer.temp.name)
        installed = root / "installed"
        lifecycle_bundle.stage_runtime(LIFECYCLE, installed)
        trace = root / "trace.jsonl"
        with observer.observed_watcher(installed / "coordination.py", trace) as coordination:
            queue = coordination.queue_helper()
            attached = trace.read_bytes()
            # A normal imported queue has its own live connection; observing the
            # installed coordinator must not patch that unrelated module.
            peer.serve()
            self.assertEqual(peer.enqueue(), ("queued-one", True))
            peer.join()
            self.assertEqual(trace.read_bytes(), attached)
            peer.serve()
            self.assertEqual(queue.enqueue(peer.path, peer.process, "native-one", "wake", "delivery-one"),
                             ("queued-one", True))
            peer.join()
        recorded = trace.read_bytes()
        peer.serve()
        self.assertEqual(queue.enqueue(peer.path, peer.process, "native-one", "wake", "delivery-one"),
                         ("queued-one", True))
        peer.join()
        self.assertEqual(trace.read_bytes(), recorded)
        rows = [json.loads(line) for line in recorded.splitlines()]
        self.assertEqual(sum(row["stage"] == "observer_attach" for row in rows), 1)
        self.assertEqual(sum(row["stage"] == "queue_add" and row["outcome"] == "after" for row in rows), 1)

    def test_watcher_launcher_validates_bundle_before_attachment(self):
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installed = root / "installed"
                lifecycle_bundle.stage_runtime(LIFECYCLE, installed)
                bindings = root / "bindings"
                bindings.mkdir()
                trace = root / "trace.jsonl"
                if changed:
                    helper = installed / "codex_queue.py"
                    helper.write_bytes(helper.read_bytes() + b"\nCHANGED = True\n")
                done = subprocess.run([sys.executable, str(OBSERVER / "watcher.py"),
                                       str(installed / "coordination.py"), "watch", "--config-dir", str(bindings),
                                       "--once"], env=dict(os.environ, CAIRN220_QUEUE_TRACE=str(trace)),
                                      capture_output=True, text=True, timeout=5)
                self.assertEqual(done.returncode == 0, not changed, done.stderr)
                rows = [json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual([(row["stage"], row["outcome"]) for row in rows],
                                 [("trace", "started"), ("observer_attach", "failed" if changed else "after")])

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
