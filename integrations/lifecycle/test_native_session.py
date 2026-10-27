"""Offline transition table for the native session record."""
import importlib.util
from pathlib import Path
import unittest
import uuid


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_session_tests", HERE / "native_session.py")
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)

AGENT = dict(agent_id=str(uuid.UUID(int=1)), execution_id=str(uuid.UUID(int=2)), native_session_id="thread-one")
SESSION = dict(agent_id=AGENT["agent_id"], execution_id=AGENT["execution_id"])
CONFIG = dict(binding="test", harness="codex")


def attempt(attempt_id="a1", **fields):
    base = dict(attempt_id=attempt_id, session=SESSION, native_turn_id="turn-1",
                delivery=dict(delivery_id="d1", lease_id="l1", event=dict(kind="notice", event_id="e1", from_="x")))
    base.update(fields)
    return base


def held(**extra):
    record = dict(schema=ns.SCHEMA, agent=dict(AGENT), process=dict(pid=1, start=2, boot=3),
                  inbox_intent=dict(request_id="a1", session=SESSION, native_turn_id="turn-1"),
                  inbox_attempt=attempt(), inbox_result_policy=dict(claim_request_id="a1", version=1))
    record.update(extra)
    return record


class TransitionTableTests(unittest.TestCase):
    def session(self, record, host):
        return ns.NativeSession(CONFIG, Path("/unused"), record, host)

    def test_admit_intends_then_claims_or_forgets(self):
        for claimed in (True, False):
            with self.subTest(claimed=claimed):
                host = ns.MemoryHost({"session-inbox-claim": dict(attempt=attempt("fresh") if claimed else None)})
                record = dict(agent=dict(AGENT))
                result = self.session(record, host).admit_inbox(dict(native_turn_id="turn-9"))
                operation, body = host.calls[0]
                self.assertEqual(operation, "session-inbox-claim")
                self.assertEqual((body["session"], body["native_turn_id"]), (SESSION, "turn-9"))
                self.assertEqual(record["inbox_result_policy"], dict(claim_request_id=body["request_id"], version=1))
                if claimed:
                    self.assertEqual(result["attempt_id"], "fresh")
                    self.assertEqual(record["inbox_attempt"]["attempt_id"], "fresh")
                else:
                    self.assertIsNone(result)
                    self.assertNotIn("inbox_intent", record)
                self.assertEqual(len(host.persisted), 2)  # intent, then the claim outcome

    def test_recover_replays_batch_advance_before_claiming(self):
        nxt = attempt("b2", batch_root="a1", batch_index=2, batch_limit=3)
        host = ns.MemoryHost({"session-inbox-advance": dict(attempt=nxt, remainder="")})
        record = held(batch_advance_intent=dict(request_id="b2", session=SESSION, attempt_id="a1", version=1),
                      inbox_close=dict(x=1), inbox_completion="c", inbox_response="r")
        self.session(record, host).recover()
        self.assertEqual(record["inbox_attempt"]["attempt_id"], "b2")
        self.assertEqual(record["inbox_intent"], dict(request_id="b2", session=SESSION, native_turn_id="turn-1"))
        self.assertEqual(record["inbox_result_policy"]["claim_request_id"], "b2")
        for key in ("batch_advance_intent", "inbox_close", "inbox_completion", "inbox_response"):
            self.assertNotIn(key, record)
        self.assertEqual([op for op, _ in host.calls], ["session-inbox-advance"])

    def test_batch_advance_final_refusal_drops_intent_and_transport_doubt_keeps_it(self):
        for code, kept in (("STALE_SESSION", False), ("TIMEOUT", True)):
            with self.subTest(code=code):
                host = ns.MemoryHost({"session-inbox-advance": ns.MemoryHost.Refusal(code)})
                record = held(batch_advance_intent=dict(request_id="b2", session=SESSION, attempt_id="a1", version=1))
                with self.assertRaises(ns.MemoryHost.Refusal):
                    self.session(record, host).advance_batch()
                self.assertEqual("batch_advance_intent" in record, kept)
        host = ns.MemoryHost()
        with self.assertRaises(ns.MemoryHost.Refusal) as caught:
            self.session(held(batch_advance_intent=dict(request_id="b2")), host).advance_batch()
        self.assertEqual(caught.exception.code, "INVALID_HOST")

    def test_renew_marks_a_lapsed_lease_and_keeps_the_hold(self):
        host = ns.MemoryHost({"event-renew": ns.MemoryHost.Refusal("STALE_LEASE")})
        record = held()
        delivery = self.session(record, host).renew(record["inbox_attempt"])
        self.assertTrue(record["inbox_attempt"]["lease_lapsed"])
        self.assertEqual(delivery["lease_id"], "l1")
        self.assertIn("lease lapsed for attempt a1", host.logged[0])
        host = ns.MemoryHost({"event-renew": dict(delivery_id="d1", lease_id="l2", event={})})
        record = held()
        self.session(record, host).renew(record["inbox_attempt"])
        self.assertEqual(record["inbox_attempt"]["delivery"]["lease_id"], "l2")
        self.assertEqual(host.calls[0][1]["lease_seconds"], ns.LEASE_SECONDS)

    def test_release_reconciles_under_one_saved_close_then_clears(self):
        host = ns.MemoryHost({"session-inbox-claim": dict(attempt=None), "session-inbox-reconcile": dict(ok=True)})
        record = held(tool_calls={"c": {}}, inbox_turn_ended=True)
        self.assertTrue(self.session(record, host).release("turn_ended"))
        reconcile = [body for op, body in host.calls if op == "session-inbox-reconcile"]
        self.assertEqual((reconcile[0]["attempt_id"], reconcile[0]["reason"]), ("a1", "turn_ended"))
        for key in ns.ATTEMPT_KEYS:
            self.assertNotIn(key, record)
        self.assertIn("agent", record)
        # A saved close with another reason is finished under its own UUID first.
        host = ns.MemoryHost({"session-inbox-reconcile": dict(ok=True)})
        saved = dict(request_id="old", session=SESSION, attempt_id="a1", reason="delivery_completed")
        record = held(inbox_close=dict(saved))
        self.assertTrue(self.session(record, host).release("turn_ended", fenced=True))
        self.assertEqual(host.calls[0], ("session-inbox-reconcile", saved))
        # DELIVERY_ACTIVE against delivery_completed commits nothing: a later reason may use a new UUID.
        host = ns.MemoryHost({"session-inbox-reconcile": ns.MemoryHost.Refusal("DELIVERY_ACTIVE")})
        record = held()
        self.assertFalse(self.session(record, host).release("delivery_completed", fenced=True))
        self.assertNotIn("inbox_close", record)
        self.assertIn("inbox_attempt", record)
        # Without an intent there is nothing to reconcile.
        host = ns.MemoryHost()
        self.assertTrue(self.session(dict(agent=dict(AGENT)), host).release("turn_ended", fenced=True))
        self.assertEqual(host.calls, [])

    def test_end_turn_names_completion_only_when_the_journal_proves_it(self):
        for committed, reason in ((True, "delivery_completed"), (False, "turn_ended")):
            with self.subTest(committed=committed):
                host = ns.MemoryHost({"session-inbox-claim": dict(attempt=None), "session-inbox-reconcile": {}},
                                     journal_committed=committed)
                record = held()
                self.assertTrue(self.session(record, host).end_turn())
                reconcile = [body for op, body in host.calls if op == "session-inbox-reconcile"]
                self.assertEqual(reconcile[-1]["reason"], reason)

    def test_settle_records_the_outcome_for_every_recovery_shape(self):
        cases = [
            # (evidence settlement, flush, raises, strict) -> recorded settlement
            ("pending", True, False, True, "turn_ended"),
            ("pending", False, False, True, "turn_ended_pending"),
            ("pending", True, True, True, "turn_ended_pending"),
            ("turn_ended", True, False, True, "turn_ended"),
            ("turn_ended", False, False, True, "turn_ended_pending"),
            ("turn_ended", False, False, False, "turn_ended"),  # the Claude hook leaves it to the watcher
            ("turn_ended", True, True, False, "turn_ended_pending"),
        ]
        for settlement, flush, raises, strict, expected in cases:
            with self.subTest(settlement=settlement, flush=flush, raises=raises, strict=strict):
                responses = {"session-inbox-claim": dict(attempt=None),
                             "session-inbox-reconcile": ns.MemoryHost.Refusal("INVALID_REQUEST") if raises else {}}
                host = ns.MemoryHost(responses, flush=flush)
                record = held(active_prompt=dict(id="turn-1"), delivered_since_idle=True)
                outcome = self.session(record, host).settle("codex_ended_turn", dict(proof=1, settlement=settlement),
                                                            prompt="turn-1", strict=strict)
                self.assertEqual(outcome["settlement"], expected)
                self.assertIs(record["codex_ended_turn"], outcome)
                self.assertNotIn("active_prompt", record)
                self.assertFalse(record["delivered_since_idle"])
                self.assertEqual(host.persisted[0]["codex_ended_turn"]["settlement"], settlement)
                self.assertEqual(host.persisted[0]["inbox_turn_ended"], True)
                if expected == "turn_ended" and settlement == "pending":
                    self.assertNotIn("inbox_attempt", record)  # reconciled and cleared

    def test_retire_leaves_releases_and_retires_or_refuses_while_uncertain(self):
        host = ns.MemoryHost({"agent-leave": {}, "session-inbox-reconcile": {}})
        record = held()
        self.session(record, host).retire("turn_ended", timeout=1)
        self.assertEqual([op for op, _ in host.calls], ["agent-leave", "session-inbox-reconcile"])
        self.assertEqual(host.calls[1][1]["reason"], "turn_ended")
        self.assertTrue(record["retired"] and record["ending"])
        self.assertEqual(record["ending_reason"], "turn_ended")
        self.assertNotIn("inbox_attempt", record)
        host = ns.MemoryHost(flush=False)
        record = held()
        with self.assertRaises(ns.MemoryHost.Refusal) as caught:
            self.session(record, host).retire()
        self.assertEqual(caught.exception.code, "INTENT_PENDING")
        self.assertTrue(record["ending"])
        self.assertNotIn("retired", record)
        host = ns.MemoryHost({"agent-leave": ns.MemoryHost.Refusal("STALE_SESSION"), "session-inbox-reconcile": {}})
        record = held()
        self.session(record, host).retire()
        self.assertTrue(record["retired"])

    def test_latches_and_attempt_refresh_persist_once(self):
        host = ns.MemoryHost()
        record = held()
        session = self.session(record, host)
        session.mark_turn_ended()
        session.mark_turn_ended()
        self.assertEqual(len(host.persisted), 1)
        session.mark_delivered(True)
        self.assertTrue(record["delivered_since_idle"])
        session.refresh_attempt(attempt("a1", cancel=dict(at=1)))
        self.assertEqual(record["inbox_attempt"]["cancel"], dict(at=1))
        self.assertEqual(len(host.persisted), 3)

    def test_open_session_locks_and_loads_the_record(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            path.write_text(json.dumps(dict(agent=dict(AGENT))))
            events = []
            class Lock:
                def __enter__(self): events.append("lock")
                def __exit__(self, *a): events.append("unlock")
            host = ns.MemoryHost()
            host.locate, host.lock = (lambda config, native: path), (lambda p: Lock())
            with ns.open_session(CONFIG, "thread-one", host) as session:
                self.assertEqual(session.record["agent"]["agent_id"], AGENT["agent_id"])
                self.assertEqual(session.path, path)
            self.assertEqual(events, ["lock", "unlock"])
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                with ns.open_session(CONFIG, "thread-one", host):
                    pass
            with ns.open_session(CONFIG, "thread-one", host, create=True) as session:
                self.assertEqual(session.record, {})


if __name__ == "__main__":
    unittest.main()
