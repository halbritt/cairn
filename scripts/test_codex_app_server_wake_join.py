"""Provider-free observer binding checks for the CAIRN-220 fixture."""

import unittest

from check_codex_app_server_wake_join import (
    _attach_item_started_observer,
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
            rpc, "thread-1", lambda stage, outcome, error=None: rows.append((stage, outcome, error)))

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
                rpc, "thread-1", lambda stage, outcome, error=None: rows.append((stage, outcome, error)))

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


if __name__ == "__main__":
    unittest.main()
