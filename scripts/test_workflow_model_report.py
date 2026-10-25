import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("tiktoken"), "run with uv --with tiktoken==0.12.0")
class WorkflowReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import audit_model_overhead as audit
        import workflow_model_report as report
        cls.audit = audit
        cls.report = report

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "binding/inbox").mkdir(parents=True)
        (self.root / "binding/intents").mkdir()

    def context(self, stem, event, kind, turn="turn"):
        item = dict(event_id=event, delivery_id=f"delivery-{stem}", kind=kind,
                    attempt_id="44444444-4444-4444-8444-444444444444",
                    native_turn_id=turn, source=dict(record_id="source", version=2))
        (self.root / f"binding/inbox/{stem}.json").write_text(json.dumps(item))

    def journal(self, stem, action="complete", disposition="handled", reply=False):
        value = dict(completion=dict(kind=action, status="committed", code="OK",
                                     argv=[action, "--disposition", disposition]))
        if reply:
            value["response"] = dict(status="committed")
        (self.root / f"binding/intents/{stem}.json").write_text(json.dumps(value))

    def session(self, turns):
        return dict(harness="codex", home=".codex", binding="binding", pin="pin-1",
                    turns=turns, errors={})

    def test_request_result_reply_and_notice_join_once(self):
        request = "11111111-1111-4111-8111-111111111111"
        response = "22222222-2222-4222-8222-222222222222"
        notice = "33333333-3333-4333-8333-333333333333"
        self.context("request", request, "request")
        self.context("response", response, "response", "response-turn")
        self.context("notice", notice, "notice", "notice-turn")
        self.journal("request", reply=True)
        self.journal("response", action="ack")
        self.journal("notice", action="ack", disposition="failed")
        turn = self.audit.blank_turn()
        turn.update(responses=["provider-r1"], usage=[dict(input=100, cached=80,
                    write=0, output=10, reasoning=4)], models=["fixture"],
                    completed=True, tools=2, wake=True)
        links = {request: dict(publication_state="committed", causation_id=None, workload="small_request"),
                 response: dict(publication_state="committed", causation_id=request, workload=None)}
        result = self.report.compile_report([self.session(dict(turn=turn))], self.root,
                                            links, {request: "accepted"})
        rows = {row["event"]: row for row in result["workflows"]}
        request_row = rows[self.report.opaque(request)]
        self.assertEqual(request_row["assessment"], "accepted")
        self.assertEqual(request_row["response_events"], [self.report.opaque(response)])
        self.assertEqual(request_row["deliveries"][0]["provider"]["usage"]["tokens"]["input"]["total"], 100)
        self.assertEqual(request_row["deliveries"][0]["provider"]["response_refs"],
                         [self.report.opaque("response\0provider-r1")])
        self.assertNotIn("provider-r1", json.dumps(result))
        self.assertEqual(request_row["deliveries"][0]["settlement"]["reply_status"], "committed")
        recipient_groups = [group for group in result["coverage_by_dimension"] if group["stage"] == "recipient"]
        self.assertEqual(sum(group["joined_provider"] for group in recipient_groups), 1)
        notice_row = rows[self.report.opaque(notice)]
        self.assertEqual(notice_row["assessment"], "unknown")
        self.assertEqual(notice_row["deliveries"][0]["settlement"]["disposition"], "failed")
        self.assertNotIn(request, json.dumps(result))
        self.assertEqual(request_row["deliveries"][0]["attempt"],
                         self.report.opaque("44444444-4444-4444-8444-444444444444"))
        self.assertEqual(result["coverage"]["attempt_known"], 3)

    def test_shared_turn_and_partial_usage_do_not_claim_event_cost(self):
        one = "11111111-1111-4111-8111-111111111111"
        two = "22222222-2222-4222-8222-222222222222"
        self.context("one", one, "notice")
        self.context("two", two, "notice")
        turn = self.audit.blank_turn()
        turn.update(responses=["r1", "r2"], usage=[dict(input=10, cached=None,
                    write=None, output=2, reasoning=None)])
        result = self.report.compile_report([self.session(dict(turn=turn))], self.root)
        shared_refs = []
        for workflow in result["workflows"]:
            delivery = workflow["deliveries"][0]
            self.assertTrue(delivery["shared_turn"])
            shared_refs.append(delivery["provider"]["response_refs"])
            self.assertIsNone(delivery["provider"]["usage"]["tokens"]["input"]["total"])
            self.assertEqual(delivery["provider"]["usage"]["tokens"]["input"]["known_sum"], 10)
        self.assertEqual(result["coverage"]["shared_turns"], 1)
        self.assertEqual(shared_refs[0], shared_refs[1])
        self.assertEqual(sum(group["missing_usage_fields"]["input"] for group in
                             result["coverage_by_dimension"]), 2)

    def test_missing_turn_and_failed_journal_are_not_success(self):
        event = "11111111-1111-4111-8111-111111111111"
        self.context("missing", event, "request", turn="unretained")
        (self.root / "binding/intents/missing.json").write_text(json.dumps(
            dict(completion=dict(kind="complete", status="pending", code="API_CONNECTION_FAILED"))))
        result = self.report.compile_report([], self.root)
        delivery = result["workflows"][0]["deliveries"][0]
        self.assertEqual(delivery["turn_join"], "missing")
        self.assertIsNone(delivery["provider"])
        self.assertIsNone(delivery["settlement"]["disposition"])
        self.assertEqual(result["workflows"][0]["assessment"], "unknown")

    def test_absent_and_present_empty_intent_journals_are_distinguished(self):
        absent_event = "11111111-1111-4111-8111-111111111111"
        empty_event = "22222222-2222-4222-8222-222222222222"
        self.context("absent", absent_event, "notice", turn="unretained-absent")
        self.context("empty", empty_event, "notice", turn="unretained-empty")
        (self.root / "binding/intents/empty.json").write_text("{}")

        result = self.report.compile_report([], self.root)
        deliveries = {row["event"]: row["deliveries"][0] for row in result["workflows"]}
        absent = deliveries[self.report.opaque(absent_event)]
        empty = deliveries[self.report.opaque(empty_event)]
        self.assertEqual(absent["intent_journal"], "missing")
        self.assertEqual(empty["intent_journal"], "present")
        for delivery in (absent, empty):
            self.assertIsNone(delivery["provider"])
            self.assertIsNone(delivery["settlement"]["status"])
        self.assertEqual(result["coverage"]["intent_journal_missing"], 1)
        self.assertEqual(result["coverage"]["intent_journal_present"], 1)

    def test_selected_sender_metadata_joins_without_emitting_source_ids(self):
        event = "11111111-1111-4111-8111-111111111111"
        source = self.root / "links.json"
        source.write_text(json.dumps(dict(schema="cairn.workflow-links/1", events=[dict(
            event_id=event, kind="request", publication_state="committed",
            sender_turn_id="sender-turn", sender_binding="binding", workload="fixture",
            owner_input_joined=True, wake_state="started", retry_count=1)],
            assessments=[dict(event_id=event, outcome="unassessed")])))
        events, assessments = self.report.read_links(source)
        sender = self.audit.blank_turn()
        sender.update(responses=["provider-sender"], usage=[dict(input=20, cached=10,
                      write=0, output=3, reasoning=1)], models=["fixture-model"])
        result = self.report.compile_report([self.session({"sender-turn": sender})], self.root,
                                            events, assessments)
        workflow = result["workflows"][0]
        self.assertEqual(workflow["publication"], "committed")
        self.assertEqual(workflow["sender_provider"]["usage"]["tokens"]["input"]["total"], 20)
        self.assertEqual(workflow["sender_provider"]["response_refs"],
                         [self.report.opaque("response\0provider-sender")])
        self.assertEqual(workflow["deliveries"], [])
        self.assertEqual(workflow["assessment"], "unassessed")
        self.assertTrue(workflow["owner_input_joined"])
        self.assertEqual(workflow["wake_state"], "started")
        self.assertEqual(workflow["retry_count"], 1)
        self.assertEqual(result["coverage"]["owner_input_known"], 1)
        self.assertNotIn(event, json.dumps(result))

    def test_repeated_response_id_across_files_cannot_be_summed(self):
        event = "11111111-1111-4111-8111-111111111111"
        self.context("one", event, "notice", turn="turn-one")
        first = self.audit.blank_turn()
        second = self.audit.blank_turn()
        for turn in (first, second):
            turn.update(responses=["same-response"], usage=[dict(input=9, cached=4,
                        write=0, output=2, reasoning=1)])
        session_one = self.session({"turn-one": first})
        session_two = self.session({"turn-two": second})
        session_two["binding"] = "another-binding"
        result = self.report.compile_report([session_one, session_two], self.root)
        provider = result["workflows"][0]["deliveries"][0]["provider"]
        self.assertTrue(provider["duplicate_response_id"])
        self.assertIsNone(provider["usage"]["tokens"]["input"]["total"])
        self.assertEqual(result["coverage"]["duplicate_provider_response_ids"], 1)

    def test_attempt_reference_accepts_only_canonical_native_or_drain_identity(self):
        attempt = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertEqual(self.report.attempt_ref(attempt), self.report.opaque(attempt))
        self.assertEqual(self.report.attempt_ref("drain-" + attempt),
                         self.report.opaque("drain-" + attempt))
        self.assertIsNone(self.report.attempt_ref("owner prompt or raw payload"))
        self.assertIsNone(self.report.attempt_ref(attempt.upper()))


if __name__ == "__main__":
    unittest.main()
