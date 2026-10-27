"""Provider-free observer binding checks for the CAIRN-220 fixture."""

import hashlib
import json
import io
import os
from pathlib import Path
import tempfile
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch
import check_codex_app_server_wake_join as wake

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


def history_result(record_id='record-1', version=3, body='source body'):
    row = dict(version=version, payload_available=True, body=body, body_bytes=len(body.encode()),
               body_sha256=hashlib.sha256(body.encode()).hexdigest())
    return {'output': json.dumps(dict(exit_code=0, output=json.dumps(dict(schema='cairn.response/1',
        ok=True, status='OK', data=dict(historical=True, record_id=record_id, versions=[row])))))}


def handled_result():
    return {'output': json.dumps(dict(exit_code=0, output=json.dumps(dict(schema='cairn.response/1',
        ok=True, status='OK', data=dict(delivery_id='delivery-1', state='handled', attempts=1,
                                       event=dict(event_id='event-1'))))))}


class ScriptedHandledContinuationTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "source": {"record_id": "record-1", "version": 3},
            "delivery_id": "delivery-1", "event_id": "event-1",
            "read_input": {"record_id": "record-1", "version": 3},
            "read": ["cairn", "agent", "history"],
            "acknowledgement": ["coordination.py", "inbox", "--context", "private.json", "ack"],
        }

    def test_reads_exact_source_then_uses_native_acknowledgement_argv(self):
        stage, tool = _scripted_inbox_tool({}, self.context, "source body")
        self.assertEqual(stage, "source_read")
        self.assertIn("history", tool["input"])
        self.assertIn("record-1", tool["input"])

        stage, tool = _scripted_inbox_tool({"source": history_result()}, self.context, "source body")
        self.assertEqual(stage, "acknowledgement")
        self.assertIn("coordination.py", tool["input"])
        self.assertIn("private.json", tool["input"])

        stage, tool = _scripted_inbox_tool({"source": history_result(),
            "acknowledgement": handled_result()}, self.context, "source body")
        self.assertEqual(stage, "store_verification")
        self.assertIsNone(tool)

    def test_source_read_tool_call_matches_exact_native_history_command(self):
        _stage, item = _scripted_inbox_tool({}, self.context, "source body")
        self.assertTrue(_is_expected_source_read_tool_call(item, self.context, "source body"))
        changed = dict(item, input=item["input"].replace("history", "search"))
        self.assertFalse(_is_expected_source_read_tool_call(changed, self.context, "source body"))

    def test_source_result_diagnostic_separates_identity_version_and_body(self):
        expected = history_result()
        self.assertEqual(_source_result_matches(expected, self.context, "source body"), {
            "source_record_match": True, "source_version_match": True, "source_body_match": True})
        self.assertEqual(_source_result_matches(history_result(version=2), self.context, "source body"), {
            "source_record_match": True, "source_version_match": False, "source_body_match": True})
        self.assertEqual(_source_result_matches(history_result(record_id="other"), self.context, "source body"), {
            "source_record_match": False, "source_version_match": True, "source_body_match": True})
        self.assertEqual(_source_result_matches(history_result(body="different"), self.context, "source body"), {
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
            _scripted_inbox_tool({"source": history_result(),
                "acknowledgement": {"output": "pending"}}, self.context, "source body")

    def test_token_mentions_and_failed_wrappers_do_not_advance_to_acknowledgement(self):
        success = history_result()
        failed = dict(success, isError=True)
        for result in ({'output': 'record-1 version 3 source body'}, failed, history_result(version=2)):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'exact_source_read_not_observed'):
                _scripted_inbox_tool({'source': result}, self.context, 'source body')

    def test_unhandled_or_wrong_delivery_does_not_advance_to_store_verification(self):
        for replacement in ('unhandled', 'other-delivery'):
            result = handled_result()
            before = 'handled' if replacement == 'unhandled' else 'delivery-1'
            result['output'] = result['output'].replace(before, replacement)
            with self.subTest(replacement=replacement), self.assertRaisesRegex(RuntimeError, 'native_acknowledgement_not_confirmed'):
                _scripted_inbox_tool({'source': history_result(), 'acknowledgement': result}, self.context, 'source body')

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
        self.assertEqual(_classify_missing_queue_trace([
            {"stage": "watcher_start", "outcome": "before"},
            {"stage": "watcher_start", "outcome": "after", "exit_zero": True},
        ]), "queue_observer_trace_missing")

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


class QueueJoinEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.delivery_digest = hashlib.sha256(b"delivery-1").hexdigest()
        self.turn_digest = hashlib.sha256(b"turn-1").hexdigest()
        self.queue_rows = [
            dict(stage="trace", outcome="started"),
            dict(stage="observer_attach", outcome="after"),
            dict(stage="queue_add", outcome="before"),
            dict(stage="queue_add", outcome="after", receipt_confirmed=True,
                 delivery_id_matches=True, delivery_id_sha256=self.delivery_digest,
                 receipt_id_sha256=hashlib.sha256(b"receipt-1").hexdigest()),
            dict(stage="queue_start", outcome="before"),
            dict(stage="queue_start", outcome="after", receipt_matches_start=True, turn_started=True),
        ]
        self.item_rows = [dict(stage="native_item_started", outcome="observed",
            delivery_id_matches=True, item_thread_matches=True, item_is_user_message=True,
            delivery_id_sha256=self.delivery_digest, turn_id_sha256=self.turn_digest,
            native_item_id_sha256=hashlib.sha256(b"item-1").hexdigest())]

    def evidence(self, queue_rows=None, item_rows=None, hook_join=True):
        return dict(hook_join=hook_join, watcher_exit=0, **wake._queue_join_evidence(
            self.queue_rows if queue_rows is None else queue_rows,
            self.item_rows if item_rows is None else item_rows,
            "delivery-1", "turn-1", hook_join))

    def test_auto_consumed_queue_still_joins_exact_receipt_item_hook_and_store(self):
        self.queue_rows[-1] = dict(stage="queue_start", outcome="refused",
            error_class="QueueRefused", receipt_matches_start=True,
            native_refusal_class="submission_not_found")
        report = self.evidence()
        self.assertTrue(report["exact_queue_hook_store_join"])
        self.assertTrue(wake._queue_join_succeeded(report, inside=True))
        self.assertEqual(report["queue_start_rpc"], "refused")
        self.assertEqual(report["queue_start_refusal_class"], "submission_not_found")
        self.assertTrue(report["start_matches_receipt"])

    def test_trace_file_or_attachment_without_a_queue_hit_cannot_prove_the_join(self):
        cases = [
            ([self.queue_rows[0]], "queue_observer_not_attached"),
            ([self.queue_rows[0], dict(stage="observer_attach", outcome="failed")],
             "queue_observer_not_attached"),
            (self.queue_rows[:2], "queue_observer_not_hit"),
            ([row for row in self.queue_rows if row["stage"] != "observer_attach"],
             "queue_observer_not_attached"),
            ([self.queue_rows[2], self.queue_rows[1], self.queue_rows[3]],
             "queue_observer_not_hit"),
        ]
        for rows, stage in cases:
            with self.subTest(stage=stage, rows=rows):
                report = self.evidence(queue_rows=rows)
                self.assertFalse(report["exact_queue_hook_store_join"])
                self.assertEqual(wake._failure_stage(report, rows), stage)

    def test_observed_join_is_retained_but_failed_watcher_cannot_pass_inside_gate(self):
        good = self.evidence()
        cases = [dict(good, watcher_exit=value) for value in (1, -15, False, None, "0")]
        cases.append({key: value for key, value in good.items() if key != "watcher_exit"})
        for report in cases:
            with self.subTest(report=report):
                self.assertTrue(report["exact_queue_hook_store_join"])
                self.assertFalse(wake._queue_join_succeeded(report, inside=True))

    def test_receipt_must_be_successful_and_match_expected_delivery(self):
        for changes in (dict(receipt_confirmed=False), dict(delivery_id_matches=False),
                        dict(delivery_id_sha256="0" * 64), dict(receipt_id_sha256=None),
                        dict(outcome="before")):
            rows = [dict(row) for row in self.queue_rows]
            rows[3].update(changes)
            with self.subTest(changes=changes):
                report = self.evidence(queue_rows=rows)
                self.assertFalse(report["exact_queue_hook_store_join"])

    def test_native_item_must_match_expected_delivery_thread_and_store_turn(self):
        for changes in (dict(delivery_id_sha256="0" * 64), dict(turn_id_sha256="0" * 64),
                        dict(item_thread_matches=False), dict(item_is_user_message=False),
                        dict(delivery_id_matches=False)):
            with self.subTest(changes=changes):
                report = self.evidence(item_rows=[dict(self.item_rows[0], **changes)])
                self.assertFalse(report["exact_queue_hook_store_join"])
                self.assertNotEqual(wake._failure_stage(report, self.queue_rows), "joined")
        report = self.evidence(hook_join=False)
        self.assertFalse(report["exact_queue_hook_store_join"])
        self.assertEqual(wake._failure_stage(report, self.queue_rows), "hook_to_store_attempt")


class ExplicitTracePreflightTests(unittest.TestCase):
    def test_preflight_creates_private_trace_without_interpreter_startup_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with patch.object(wake, "__file__", "/repo/scripts/check_codex_app_server_wake_join.py"), \
                    patch.dict(os.environ, {"CAIRN220_QUEUE_TRACE": str(trace)}), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(wake._preflight_inner(), 0)
            self.assertEqual(json.loads(output.getvalue())["trace_created"], True)
            self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
            self.assertEqual([json.loads(line) for line in trace.read_text().splitlines()],
                [dict(schema="cairn.codex-queue-stage/1", stage="trace", outcome="started")])


class ScriptedRunnerExitTests(unittest.TestCase):
    def test_scripted_runner_requires_observed_full_join_read_and_store_settlement(self):
        good = dict(hook_join=True, exact_source_read=True, acknowledgement='handled_confirmed',
                    settlement_exactly_once=True, queue_observer_attached=True,
                    queue_observer_hit=True, exact_queue_hook_store_join=True, outer_exit=0,
                    watcher_exit=0)
        cases = [(good, 0), (dict(hook_join=True), 1), ({}, 1)]
        for field in good:
            cases.append((dict(good, **{field: False}), 1))
        cases.extend((dict(good, outer_exit=value), 1) for value in (1, -15, False, None, "0"))
        cases.append(({key: value for key, value in good.items() if key != "outer_exit"}, 1))
        cases.extend((dict(good, watcher_exit=value), 1) for value in (1, -15, False, None, "0"))
        cases.append(({key: value for key, value in good.items() if key != "watcher_exit"}, 1))
        for report, expected in cases:
            with self.subTest(report=report), patch.object(wake.sys, 'argv', ['fixture', '--scripted-handled']), \
                    patch.object(wake, 'run_once', return_value=report), redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(), expected)

    def test_default_runner_rejects_hook_only_and_missing_observer_evidence(self):
        good = dict(hook_join=True, queue_observer_attached=True,
                    queue_observer_hit=True, exact_queue_hook_store_join=True, outer_exit=0,
                    watcher_exit=0)
        cases = [(good, 0), (dict(hook_join=True), 1), ({}, 1)]
        for field in good:
            cases.append((dict(good, **{field: False}), 1))
        cases.extend((dict(good, outer_exit=value), 1) for value in (1, -15, False, None, "0"))
        cases.append(({key: value for key, value in good.items() if key != "outer_exit"}, 1))
        cases.extend((dict(good, watcher_exit=value), 1) for value in (1, -15, False, None, "0"))
        cases.append(({key: value for key, value in good.items() if key != "watcher_exit"}, 1))
        for report, expected in cases:
            with self.subTest(report=report), patch.object(wake.sys, 'argv', ['fixture']), \
                    patch.object(wake, 'run_once', return_value=report), redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(), expected)


if __name__ == "__main__":
    unittest.main()
