"""Provider-free observer binding checks for the CAIRN-220 fixture."""

import json
import unittest

from check_codex_app_server_wake_join import (
    _ItemStartedObserver,
    _scripted_inbox_tool,
    _is_expected_source_read_tool_call,
    _source_result_matches,
    _tool_result_diagnostics,
    _attach_item_started_observer,
    _classify_thread_resume_refusal,
    _classify_missing_queue_trace,
    _is_missing_rollout_resume_refusal,
)


class ItemStartedObserverSubscriptionTests(unittest.TestCase):
    def test_observer_checks_loaded_metadata_then_resumes_its_connection(self):
        calls = []

        def rpc(method, params):
            calls.append((method, params))
            if method == "thread/loaded/list":
                return {"data": ["thread-1"]}
            if method == "thread/resume":
                return {"thread": {"id": "thread-1"}}
            raise AssertionError("unexpected RPC method")

        rows = []
        _attach_item_started_observer(
            rpc, "thread-1", lambda stage, outcome, error=None, **extra:
            rows.append((stage, outcome, error)))

        self.assertEqual(calls, [
            ("thread/loaded/list", {"limit": 100}),
            ("thread/resume", {"threadId": "thread-1"}),
        ])
        self.assertEqual(rows, [
            ("observer_loaded_list", "before", None),
            ("observer_loaded_list", "after", None),
            ("observer_resume", "before", None),
            ("observer_resume", "after", None),
        ])


class ScriptedHandledContinuationTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "source": {"record_id": "record-1", "version": 3},
            "read_input": {"record_id": "record-1", "version": 3},
            "read": ["cairn", "agent", "history"],
            "acknowledgement": ["coordination.py", "inbox", "--context", "private.json", "ack"],
        }

    def test_reads_exact_source_then_uses_native_acknowledgement_argv(self):
        stage, tool = _scripted_inbox_tool({}, self.context, "source body")
        self.assertEqual(stage, "source_read")
        self.assertIn("history", tool["input"])
        self.assertIn("record-1", tool["input"])

        stage, tool = _scripted_inbox_tool({"source": {"call_id": "source",
            "output": 'record-1 version 3: source body'}}, self.context, "source body")
        self.assertEqual(stage, "acknowledgement")
        self.assertIn("coordination.py", tool["input"])
        self.assertIn("private.json", tool["input"])

        stage, tool = _scripted_inbox_tool({"source": {"output": "record-1 version 3: source body"},
            "acknowledgement": {"output": "handled"}}, self.context, "source body")
        self.assertEqual(stage, "store_verification")
        self.assertIsNone(tool)

    def test_source_read_tool_call_matches_exact_native_history_command(self):
        _stage, item = _scripted_inbox_tool({}, self.context, "source body")
        self.assertTrue(_is_expected_source_read_tool_call(item, self.context, "source body"))
        changed = dict(item, input=item["input"].replace("history", "search"))
        self.assertFalse(_is_expected_source_read_tool_call(changed, self.context, "source body"))

    def test_source_result_diagnostic_separates_identity_version_and_body(self):
        expected = {"record_id": "record-1", "version": 3, "body": "source body"}
        self.assertEqual(_source_result_matches(expected, self.context, "source body"), {
            "source_record_match": True, "source_version_match": True, "source_body_match": True})
        self.assertEqual(_source_result_matches({"record_id": "record-1", "version": 2,
            "body": "source body"}, self.context, "source body"), {
            "source_record_match": True, "source_version_match": False, "source_body_match": True})
        self.assertEqual(_source_result_matches({"record_id": "other", "version": 3,
            "body": "source body"}, self.context, "source body"), {
            "source_record_match": False, "source_version_match": True, "source_body_match": True})
        self.assertEqual(_source_result_matches({"record_id": "record-1", "version": 3,
            "body": "different"}, self.context, "source body"), {
            "source_record_match": True, "source_version_match": True, "source_body_match": False})

    def test_tool_result_diagnostics_distinguish_api_response_and_shell_failure(self):
        api_envelope = json.dumps({"schema": "cairn.response/1", "ok": True,
                                   "status": "OK", "data": {"versions": []}})
        success = _tool_result_diagnostics({"type": "custom_tool_call_output", "status": "completed",
            "output": json.dumps({"exit_code": 0, "output": api_envelope})})
        self.assertEqual(success["result_status"], "completed")
        self.assertEqual(success["result_output_type"], "string")
        self.assertEqual(success["exit_class"], "zero")
        self.assertEqual(success["execution_error_class"], "none")
        self.assertTrue(success["cairn_api_envelope_observed"])
        self.assertEqual(success["api_read_status"], "success")
        self.assertEqual(success["api_read_invocation"], "confirmed")

        nested_exec_output = _tool_result_diagnostics({"type": "custom_tool_call_output",
            "output": [{"type": "input_text", "text": json.dumps({
                "output": "Chunk ID: 1\nProcess exited with code 0\nOutput:\n" + api_envelope,
                "metadata": {"exit_code": 0}})}]})
        self.assertEqual(nested_exec_output["result_output_type"], "array")
        self.assertEqual(nested_exec_output["exit_class"], "zero")
        self.assertEqual(nested_exec_output["execution_error_class"], "none")
        self.assertTrue(nested_exec_output["cairn_api_envelope_observed"])
        self.assertEqual(nested_exec_output["api_read_status"], "success")
        self.assertEqual(nested_exec_output["api_read_invocation"], "confirmed")

        command_failure = _tool_result_diagnostics({"type": "custom_tool_call_output",
            "output": json.dumps({"exit_code": 127,
                                   "output": "/fixture/cairn: No such file or directory"})})
        self.assertEqual(command_failure["exit_class"], "nonzero")
        self.assertEqual(command_failure["execution_error_class"], "no_such_file")
        self.assertEqual(command_failure["api_read_status"], "not_observed")
        self.assertEqual(command_failure["api_read_invocation"], "not_observed")
        self.assertFalse(command_failure["cairn_api_envelope_observed"])

        tool_error = _tool_result_diagnostics({"type": "custom_tool_call_output", "isError": True,
                                                "content": [{"type": "text", "text": "PRIVATE"}]})
        self.assertEqual(tool_error["result_status"], "failed")
        self.assertTrue(tool_error["result_error_present"])
        self.assertEqual(tool_error["execution_error_class"], "tool_error")
        self.assertNotIn("PRIVATE", json.dumps((success, command_failure, tool_error)))

    def test_does_not_ack_missing_or_different_source(self):
        with self.assertRaisesRegex(RuntimeError, "exact_source_read_not_observed"):
            _scripted_inbox_tool({"source": {"output": "different record"}},
                                 self.context, "source body")

    def test_does_not_accept_non_handled_ack_result(self):
        with self.assertRaisesRegex(RuntimeError, "native_acknowledgement_not_confirmed"):
            _scripted_inbox_tool({"source": {"output": "record-1 version 3 source body"},
                "acknowledgement": {"output": "pending"}}, self.context, "source body")

    def test_resume_is_not_attempted_when_metadata_check_fails(self):
        calls = []
        rows = []

        def rpc(method, params):
            calls.append(method)
            return {"data": []}

        with self.assertRaisesRegex(RuntimeError, "notification_observer_thread_not_loaded"):
            _attach_item_started_observer(
                rpc, "thread-1", lambda stage, outcome, error=None, **extra:
                rows.append((stage, outcome, error)))

        self.assertEqual(calls, ["thread/loaded/list"])
        self.assertEqual(rows, [("observer_loaded_list", "before", None),
                                ("observer_loaded_list", "after", None),
                                ("observer_loaded_list", "failed", "RuntimeError")])

    def test_missing_queue_trace_is_classified_from_the_last_fixed_stage(self):
        self.assertEqual(_classify_missing_queue_trace([
            {"stage": "observer_subscription", "outcome": "before"},
            {"stage": "observer_loaded_list", "outcome": "after"},
            {"stage": "observer_resume", "outcome": "failed"},
        ]), "observer_resume_failed")
        self.assertEqual(_classify_missing_queue_trace([
            {"stage": "observer_subscription", "outcome": "after"},
            {"stage": "watcher_start", "outcome": "before"},
        ]), "watcher_start_incomplete")
        self.assertEqual(_classify_missing_queue_trace([
            {"stage": "observer_subscription", "outcome": "before"},
            {"stage": "observer_subscription", "outcome": "failed",
             "error_class": "TypeError"},
        ]), "observer_subscription_failed")
        self.assertEqual(_classify_missing_queue_trace([
            {"stage": "observer_initialize", "outcome": "failed",
             "error_class": "TimeoutExpired"},
        ]), "observer_initialize_failed")

    def test_resume_refusal_classifier_keeps_only_source_known_reason_codes(self):
        cases = {
            "thread 01234567-89ab-cdef-0123-456789abcdef is closing; retry thread/resume after the thread is closed":
                "thread_closing",
            "thread not found: /private/path/thread.jsonl": "thread_not_found",
            "cannot resume thread 01234567-89ab-cdef-0123-456789abcdef with history while it is already running":
                "history_already_running",
            "cannot resume running thread 01234567-89ab-cdef-0123-456789abcdef with stale path: requested `/private/a`, active `/private/b`":
                "stale_rollout_path",
            "cannot resume an unloaded multi-agent v2 sub-agent through its parent; resume the parent first, or use thread/read to inspect it":
                "unloaded_v2_child",
            "no rollout found for thread id fixture-thread": "thread_no_persisted_rollout",
            "failed to enqueue running thread resume for thread 01234567-89ab-cdef-0123-456789abcdef: thread listener is not running":
                "resume_listener_not_running",
            "failed to enqueue running thread resume for thread 01234567-89ab-cdef-0123-456789abcdef: thread listener command channel is closed":
                "resume_listener_channel_closed",
            "unrecognized detail with /private/path": "thread_resume_other",
            None: "thread_resume_other",
        }
        for message, expected in cases.items():
            with self.subTest(expected=expected):
                actual = _classify_thread_resume_refusal(message)
                self.assertEqual(actual, expected)
                self.assertNotIn("/private", actual)

    def test_resume_refusal_trace_carries_code_and_fixed_enum_only(self):
        class QueueRefused(Exception):
            code = -32600
            message = "thread 01234567-89ab-cdef-0123-456789abcdef is closing; retry after the thread is closed"

        calls = []
        rows = []

        def rpc(method, params):
            calls.append(method)
            if method == "thread/loaded/list":
                return {"data": ["thread-1"]}
            raise QueueRefused("private message is never emitted")

        def record(stage, outcome, error=None, code=None, flags=None, private_message=None):
            rows.append((stage, outcome, error, code, flags, private_message))

        with self.assertRaises(QueueRefused):
            _attach_item_started_observer(rpc, "thread-1", record)

        self.assertEqual(calls, ["thread/loaded/list", "thread/resume"])
        self.assertEqual(rows[-1], ("observer_resume", "failed", "QueueRefused", -32600,
                                    {"native_refusal_class": "thread_closing"},
                                    QueueRefused.message))
        self.assertNotIn("private message", repr(rows))

    def test_only_missing_rollout_refusal_selects_native_thread_start_connection(self):
        class QueueRefused(Exception):
            def __init__(self, message):
                self.message = message

        known = QueueRefused("no rollout found for thread id fixture-thread")
        other = QueueRefused("different refusal")
        self.assertTrue(_is_missing_rollout_resume_refusal(known))
        self.assertFalse(_is_missing_rollout_resume_refusal(other))
        self.assertFalse(_is_missing_rollout_resume_refusal(RuntimeError("not a JSON-RPC refusal")))

    def test_observer_uses_thread_start_connection_for_missing_rollout_only(self):
        import json
        import tempfile
        from pathlib import Path

        class Connection:
            def __init__(self):
                self.closed = False

            def close(self, timeout=0):
                self.closed = True

            def settimeout(self, _value):
                pass

            def send(self, _value):
                pass

            def recv(self):
                raise OSError("test observer shutdown")

        class QueueRefused(Exception):
            code = -32600
            message = "no rollout found for thread id fixture-thread"

        class QueueModule:
            @staticmethod
            def _open(_endpoint, _process, _mode):
                return observer_connection, None, None

            class _Rpc:
                def __init__(self, _connection, _deadline):
                    pass

                def __call__(self, method, _params):
                    if method == "initialize":
                        return {}
                    if method == "thread/loaded/list":
                        return {"data": ["fixture-thread"]}
                    if method == "thread/resume":
                        raise QueueRefused("synthetic refusal")
                    raise AssertionError(method)

        observer_connection = Connection()
        start_connection = Connection()
        with tempfile.TemporaryDirectory() as directory:
            trace_path = str(Path(directory) / "item.jsonl")
            observer = _ItemStartedObserver(
                QueueModule, "fixture-endpoint", {}, "fixture-thread", trace_path,
                fallback_connection=start_connection)
            observer.close()
            rows = [json.loads(line) for line in Path(trace_path).read_text().splitlines()]

        self.assertTrue(observer_connection.closed)
        self.assertTrue(start_connection.closed)
        self.assertEqual(next(row["native_refusal_class"] for row in rows
                              if row["stage"] == "observer_resume" and
                              "native_refusal_class" in row),
                         "thread_no_persisted_rollout")
        self.assertTrue(any(row["stage"] == "observer_fallback" and
                            row["outcome"] == "observed" for row in rows))
        self.assertEqual(next(row["private_refusal_message"] for row in rows
                              if "private_refusal_message" in row), QueueRefused.message)


if __name__ == "__main__":
    unittest.main()
