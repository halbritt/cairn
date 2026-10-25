"""Provider-free observer binding checks for the CAIRN-220 fixture."""

import unittest

from check_codex_app_server_wake_join import (
    _attach_item_started_observer,
    _classify_thread_resume_refusal,
    _classify_missing_queue_trace,
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

        def record(stage, outcome, error=None, code=None, flags=None):
            rows.append((stage, outcome, error, code, flags))

        with self.assertRaises(QueueRefused):
            _attach_item_started_observer(rpc, "thread-1", record)

        self.assertEqual(calls, ["thread/loaded/list", "thread/resume"])
        self.assertEqual(rows[-1], ("observer_resume", "failed", "QueueRefused", -32600,
                                    {"native_refusal_class": "thread_closing"}))
        self.assertNotIn("private message", repr(rows))


if __name__ == "__main__":
    unittest.main()
