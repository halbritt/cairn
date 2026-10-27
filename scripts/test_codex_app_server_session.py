"""Offline tests for the Codex app-server session module.

The FakeAppServer adapter speaks the same JSON-RPC subset as the real
app-server, so the ordering invariants that the 2026-10-25 fixture fix-ups
established (completed arrives before the Stop hook exits; only the matching
turn counts; interrupt yields an interrupted completion) are proven here
without Codex. A real subprocess test covers the stdio wire and process
ownership with a scripted Python child.
"""
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import codex_app_server_session as m  # noqa: E402

CHILD = r'''
import json, sys
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    rid, method = msg["id"], msg["method"]
    if method == "initialize":
        out(dict(id=rid, result=dict(framing="jsonrpc" if "jsonrpc" in msg else "plain")))
    elif method == "thread/start":
        out(dict(id=rid, result=dict(thread=dict(id="t1"))))
    elif method == "turn/start":
        out(dict(id=rid, result=dict(turn=dict(id="u1"))))
        out(dict(method="turn/started", params=dict(threadId="t1", turn=dict(id="u1"))))
        out(dict(method="turn/completed", params=dict(threadId="t1", turn=dict(id="u1", status="completed"))))
    elif method == "oversize":
        out(dict(id=rid, result="x" * 5000))
    elif method == "garbage":
        sys.stdout.write("not json\n"); sys.stdout.flush()
        out(dict(id=rid, result={}))
    elif method == "exit":
        break
'''


def _session(fake=None, **kwargs):
    fake = m.FakeAppServer() if fake is None else fake
    session = m.CodexAppServerSession.start(fake, client_name="test", **kwargs)
    return fake, session


class RefusalVocabularyTests(unittest.TestCase):
    def test_enum_matches_the_queue_trace_vocabulary(self):
        sys.path.insert(0, str(SCRIPTS / "fixtures" / "cairn220_queue_trace"))
        try:
            import trace_io
        finally:
            sys.path.pop(0)
        self.assertEqual(m.NATIVE_REFUSAL_CLASSES, trace_io.NATIVE_REFUSAL_CLASSES)

    def test_queue_context_classifies_by_code(self):
        cases = [
            (-32600, m.BUSY_MESSAGE, "busy"),
            (-32600, "queued submission not found: abc", "submission_not_found"),
            (-32600, "anything else", "invalid_request_other"),
            (-32601, m.BUSY_MESSAGE, "native_refusal_other"),
            (None, None, "native_refusal_other"),
        ]
        for code, message, expected in cases:
            with self.subTest(code=code, message=message):
                self.assertEqual(m.classify_refusal(code, message, context="queue"), expected)

    def test_resume_context_classifies_by_pinned_message(self):
        cases = [
            ("no rollout found for thread id abc", "thread_no_persisted_rollout"),
            ("thread abc is closing; retry thread/resume after the thread is closed", "thread_closing"),
            ("thread abc is closing; retry after the thread is closed", "thread_closing"),
            ("thread not found: abc", "thread_not_found"),
            ("cannot resume thread abc with history while it is already running", "history_already_running"),
            ("cannot resume running thread abc with stale path: requested `a`, active `b`", "stale_rollout_path"),
            ("cannot resume an unloaded multi-agent v2 sub-agent through its parent; x", "unloaded_v2_child"),
            ("failed to enqueue running thread resume for thread abc: thread listener is not running",
             "resume_listener_not_running"),
            ("failed to enqueue running thread resume for thread abc: thread listener command channel is closed",
             "resume_listener_channel_closed"),
            ("something private /home/x", "thread_resume_other"),
            (None, "thread_resume_other"),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                actual = m.classify_refusal(-32600, message, context="resume")
                self.assertEqual(actual, expected)
                self.assertIn(actual, m.NATIVE_REFUSAL_CLASSES)
        with self.assertRaises(ValueError):
            m.classify_refusal(0, "", context="other")

    def test_exception_attributes_are_classified(self):
        class Refused(Exception):
            code = -32600
            message = "thread not found: abc"
        self.assertEqual(m.classify_refusal_exception(Refused(), context="resume"), "thread_not_found")
        self.assertEqual(m.classify_refusal_exception(Refused(), context="queue"), "invalid_request_other")

    def test_refused_message_is_redacted_for_display(self):
        exc = m.AppServerRefused("turn/start", dict(code=1, message="token=abc path /home/x/secret 01234567-89ab-cdef-0123-456789abcdef"))
        self.assertEqual(exc.code, 1)
        self.assertIn("token=abc", exc.message)
        self.assertNotIn("abc", exc.safe_message)
        self.assertNotIn("/home", str(exc))
        self.assertIn("<redacted>", str(exc))


class WireCallerTests(unittest.TestCase):
    def test_handshake_sends_initialize_then_initialized(self):
        fake, session = _session()
        self.assertEqual(session.initialize_result, dict(userAgent="fake-app-server/1"))
        self.assertEqual([msg["method"] for msg in fake.sent], ["initialize", "initialized"])
        self.assertEqual(fake.client_info["name"], "test")
        session.close()

    def test_stale_responses_and_server_requests_do_not_confuse_matching(self):
        fake, session = _session()
        fake.inject(dict(id=999, result=dict(stale=True)))
        fake.server_request("item/commandExecution/requestApproval", dict(turnId="x"))
        fake.emit("turn/started", dict(threadId="t", turn=dict(id="u")))
        thread = session.thread("/w", "model")
        self.assertEqual(thread.id, "thread-1")
        request = session._caller.server_requests.get(timeout=1)
        self.assertEqual(request["method"], "item/commandExecution/requestApproval")
        self.assertEqual(session.next_notification(timeout=1)["method"], "turn/started")
        session.close()

    def test_refusal_raises_with_code_and_class(self):
        fake, session = _session()
        with self.assertRaises(m.AppServerRefused) as caught:
            session.call("turn/start", dict(threadId="missing"))
        self.assertEqual(caught.exception.code, -32600)
        self.assertEqual(caught.exception.method, "turn/start")
        session.close()

    def test_dropped_response_times_out(self):
        fake, session = _session(m.FakeAppServer(drop=("thread/start",)), default_timeout=0.3)
        started = time.monotonic()
        with self.assertRaises(m.AppServerTimeout):
            session.thread("/w")
        self.assertLess(time.monotonic() - started, 2)
        session.close()

    def test_session_deadline_bounds_every_wait(self):
        fake, session = _session(deadline=time.monotonic() + 0.2)
        time.sleep(0.25)
        with self.assertRaises(m.AppServerTimeout):
            session.call("initialize")
        with self.assertRaises(m.AppServerTimeout):
            session.next_notification(timeout=1)
        session.close()

    def test_transport_close_surfaces_on_both_sides(self):
        fake, session = _session()
        fake.close()
        with self.assertRaises(m.AppServerTransportError):
            session.call("thread/start", dict(cwd="/w"))
        with self.assertRaises(m.AppServerTransportError):
            session.next_notification(timeout=1)
        session.close()


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.session = _session()
        self.addCleanup(self.session.close)

    def test_wait_matches_only_the_awaited_turn(self):
        a = self.session.thread("/a", "model")
        b = self.session.thread("/b", "model")
        self.fake.script_turn(delay=0.3)
        slow = a.turn("slow")
        self.fake.script_turn(delay=0.0)
        quick = b.turn("quick")
        completion = slow.wait_completed(2)
        self.assertTrue(completion.completed)
        self.assertEqual(completion.params["turn"]["id"], slow.id)
        self.assertEqual(completion.turn_ids("turn/completed"), {quick.id, slow.id})
        self.assertEqual(completion.status, "completed")

    def test_completed_before_stop_marker_still_waits_for_the_marker(self):
        thread = self.session.thread("/w", "model")
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stop.json"

            def late_marker(turn_id):
                def write():
                    marker.write_text(json.dumps(dict(event="Stop", turn_id=turn_id, exit_code=0)))
                threading.Timer(0.15, write).start()

            self.fake.script_turn(after_completed=late_marker)
            turn = thread.turn("register")
            started = time.monotonic()
            completion = turn.wait_completed(2, stop_marker=marker, stop_timeout=2)
            self.assertTrue(completion.completed)
            self.assertEqual(completion.stop["turn_id"], turn.id)
            self.assertGreaterEqual(time.monotonic() - started, 0.14)

    def test_marker_mismatch_nonzero_and_stale_handling(self):
        thread = self.session.thread("/w", "model")
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stop.json"
            marker.write_text(json.dumps(dict(event="Stop", turn_id="other", exit_code=0)))
            turn = thread.turn("one")
            with self.assertRaisesRegex(AssertionError, "does not match"):
                turn.wait_completed(2, stop_marker=marker, stop_timeout=0.3)
            marker.write_text(json.dumps(dict(event="Stop", turn_id=turn.id, exit_code=1, stderr_code="X")))
            with self.assertRaisesRegex(AssertionError, "returned nonzero"):
                turn.wait_stop_marker(marker, 0.3)
            marker.write_text(json.dumps(dict(event="Stop", turn_id="other", exit_code=0)))
            threading.Timer(0.1, lambda: marker.write_text(json.dumps(
                dict(event="Stop", turn_id=turn.id, exit_code=0)))).start()
            self.assertEqual(turn.wait_stop_marker(marker, 2, allow_stale=True)["turn_id"], turn.id)
            marker.unlink()
            with self.assertRaises(m.AppServerTimeout):
                turn.wait_stop_marker(marker, 0.2)

    def test_until_drain_until_and_guard(self):
        thread = self.session.thread("/w", "model")
        self.fake.script_turn(no_completion=True)
        stuck = thread.turn("stuck")
        flag = threading.Event()
        threading.Timer(0.1, flag.set).start()
        completion = stuck.wait_completed(2, until=flag.is_set, poll=0.05)
        self.assertTrue(completion.stopped_early)
        self.assertFalse(completion.completed)

        self.fake.allow_concurrent_turns = True
        finished = threading.Event()
        turn = thread.turn("done")
        self.fake.emit("item/completed", dict(threadId=thread.id, turnId=turn.id, item=dict(type="late")), delay=0.1)
        threading.Timer(0.2, finished.set).start()
        completion = turn.wait_completed(2, drain_until=finished.is_set, poll=0.05)
        self.assertTrue(completion.completed)
        self.assertIn("item/completed", [row["method"] for row in completion.summary(items=True)])

        seen = []
        observed_turn = thread.turn("observed")
        completion = observed_turn.wait_completed(2, observe=lambda note: seen.append(note.get("method")), poll=0.05)
        self.assertTrue(completion.completed)
        self.assertEqual(seen, [note.get("method") for note in completion.observed])
        self.assertIn("turn/completed", seen)

        failures = ["boom"]
        with self.assertRaises(AssertionError):
            thread.turn("guarded").wait_completed(1, guard=lambda: self.assertFalse(failures, failures))

    def test_failed_turn_is_recorded_but_waiting_continues_unless_asked(self):
        thread = self.session.thread("/w", "model")
        self.fake.script_turn(no_completion=True)
        turn = thread.turn("fail")
        self.fake.emit("turn/failed", dict(threadId=thread.id, turn=dict(id=turn.id)))
        started = time.monotonic()
        completion = turn.wait_completed(0.4, poll=0.05)
        self.assertEqual(completion.terminal_class, "failed")
        self.assertFalse(completion.completed)
        self.assertGreaterEqual(time.monotonic() - started, 0.35)
        self.fake.emit("turn/cancelled", dict(threadId=thread.id, turn=dict(id=turn.id)))
        completion = turn.wait_completed(2, stop_on_failure=True, poll=0.05)
        self.assertEqual(completion.terminal_class, "cancelled")
        self.assertEqual(completion.notification["method"], "turn/cancelled")

    def test_interrupt_and_armed_deadline(self):
        thread = self.session.thread("/w", "model")
        self.fake.script_turn(delay=5)
        turn = thread.turn("long")
        turn.interrupt()
        completion = turn.wait_completed(2)
        self.assertEqual(completion.status, "interrupted")

        stopped = threading.Event()
        self.fake.script_turn(delay=5)
        turn = thread.turn("deadline")
        armed = turn.arm_deadline(0.1, stop_server=stopped.set)
        completion = turn.wait_completed(2, until=armed.expired.is_set, drain_until=lambda: False, poll=0.05)
        self.assertTrue(armed.wait_cleanup(2))
        self.assertTrue(stopped.is_set())
        self.assertIsNone(armed.cancel_error)
        self.assertTrue(completion.stopped_early or completion.status == "interrupted")
        self.fake.script_turn(delay=0)
        quick = thread.turn("quick")
        armed = quick.arm_deadline(5, stop_server=lambda: None)
        quick.wait_completed(2)
        armed.disarm()
        self.assertFalse(armed.expired.is_set())

    def test_compact_turn_is_awaited_through_turn_started(self):
        thread = self.session.thread("/w", "model")
        self.assertEqual(self.session.compact(thread), {})
        compact = self.session.await_turn_started(thread, 2)
        completion = compact.wait_completed(2)
        self.assertEqual(completion.status, "completed")
        rows = compact.observed_before_start + completion.observed
        self.assertIn("contextCompaction", [
            (row.get("params") or {}).get("item", {}).get("type") for row in rows])

    def test_resume_refusal_is_annotated_with_its_class(self):
        thread = self.session.thread("/w", "model")
        self.fake.resume_refusals[thread.id] = dict(code=-32600, message="no rollout found for thread id " + thread.id)
        with self.assertRaises(m.AppServerRefused) as caught:
            self.session.resume(thread.id)
        self.assertEqual(caught.exception.refusal_class, "thread_no_persisted_rollout")
        del self.fake.resume_refusals[thread.id]
        self.assertEqual(self.session.resume(thread.id)["thread"]["id"], thread.id)
        self.assertEqual(self.session.loaded_threads(), [thread.id])

    def test_drain_notifications_returns_queued_items(self):
        self.fake.emit("turn/started", dict(threadId="t", turn=dict(id="u")))
        time.sleep(0.05)
        self.assertEqual([row["method"] for row in self.session.drain_notifications()], ["turn/started"])
        self.assertEqual(self.session.drain_notifications(), [])


class RpcCallerTests(unittest.TestCase):
    def test_attach_installer_style_closure(self):
        calls = []
        notes = queue.Queue()

        def rpc(method, params, _timeout=None):
            calls.append((method, params, _timeout))
            if method == "thread/start":
                return dict(thread=dict(id="t1"))
            if method == "turn/start":
                notes.put(dict(method="turn/completed", params=dict(threadId="t1", turn=dict(id="u1", status="completed"))))
                return dict(turn=dict(id="u1"))
            return {}

        rpc.next_notification = lambda timeout=15: notes.get(timeout=timeout)
        rpc.arm_turn_deadline = lambda thread_id, turn_id, seconds: ("armed", thread_id, turn_id, seconds)
        session = m.CodexAppServerSession.attach(rpc)
        thread = session.thread("/w", "model", sandbox="read-only", ephemeral=True)
        turn = session.turn(thread, "hello", timeout=3)
        self.assertEqual(turn.wait_completed(1).status, "completed")
        self.assertEqual(turn.arm_deadline(7), ("armed", "t1", "u1", 7))
        self.assertEqual(calls[0], ("thread/start", dict(cwd="/w", model="model", approvalPolicy="never",
                                                         sandbox="read-only", ephemeral=True), None))
        self.assertEqual(calls[1][2], 3)
        with self.assertRaises(m.AppServerUnsupported):
            session.notify("initialized")
        session.close()

    def test_attach_minimal_callable(self):
        session = m.CodexAppServerSession.attach(lambda method, params: dict(thread=dict(id="t")))
        self.assertEqual(session.thread("/w").id, "t")
        with self.assertRaises(m.AppServerUnsupported):
            session.next_notification(timeout=0.1)
        self.assertIsNone(session.arm_turn_deadline("t", "u", 1))


class StdioWireProcessTests(unittest.TestCase):
    def _spawn(self, **kwargs):
        return m.CodexAppServerSession.spawn([sys.executable, "-c", CHILD], client_name="child",
                                             default_timeout=5, **kwargs)

    def test_plain_and_jsonrpc_framing_over_a_real_process(self):
        for jsonrpc, expected in ((False, "plain"), (True, "jsonrpc")):
            with self.subTest(jsonrpc=jsonrpc):
                session = self._spawn(jsonrpc=jsonrpc)
                try:
                    self.assertEqual(session.initialize_result["framing"], expected)
                    thread = session.thread("/w", "model")
                    turn = thread.turn("go")
                    completion = turn.wait_completed(5)
                    self.assertEqual(completion.status, "completed")
                    self.assertEqual(completion.turn_ids("turn/started"), {"u1"})
                finally:
                    session.close()
                self.assertIsNotNone(session.process.poll())

    def test_binary_pipes_line_bound_and_invalid_json(self):
        session = self._spawn(text=False, max_line_bytes=1000, stderr="digest")
        try:
            with self.assertRaises(m.AppServerTransportError):
                session.call("oversize")
        finally:
            session.close()
        session = self._spawn()
        try:
            with self.assertRaisesRegex(m.AppServerTransportError, "invalid JSON"):
                session.call("garbage")
        finally:
            session.close()

    def test_process_exit_surfaces_as_transport_error_and_stop_is_idempotent(self):
        session = self._spawn()
        try:
            with self.assertRaises(m.AppServerTransportError):
                session.call("exit")
        finally:
            session.close()
            session.close()
        self.assertIsNotNone(session.process.poll())
        self.assertIsNone(session.process.stop_error)
        self.assertFalse(session.process.wait_socket("/nonexistent/socket", timeout=0.05))


if __name__ == "__main__":
    unittest.main()
