import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_task_evidence as evidence


def text(value):
    return [{"type": "text", "text": json.dumps(value)}]


class DeliveryEvidenceTest(unittest.TestCase):
    def test_report_uses_pinned_observed_corpus_after_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus.json"
            corpus.write_text(json.dumps(dict(notes=[dict(id="T", body="old guidance") ])))
            observed = root / "observed-corpus.json"
            observed.write_text(json.dumps(dict(notes=[dict(id="T", body="corrected α guidance")])))
            metadata = dict(path=observed.name, sha256=hashlib.sha256(observed.read_bytes()).hexdigest())
            record = dict(run_id="case.candidate.s0", case="case", arm="candidate", seed=0, outcome="correct", harness="codex")
            report = dict(frozen=dict(corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest()),
                          observed_corpus=metadata, records=[record])
            source = root / "agent.json"
            source.write_text(json.dumps(report))
            stream = root / "runs" / record["run_id"] / "stream.jsonl"
            stream.parent.mkdir(parents=True)
            payload = dict(selection=dict(record=dict(record_id="r", version=1,
                body="corrected α guidance", scope=dict(repo="trial:task-eval"))))
            stream.write_text(json.dumps(dict(type="item.completed", item=dict(id="p", type="mcp_tool_call",
                server="cairn", tool="cairn_pull", status="completed", result=dict(content=text(payload))))))
            result = evidence.analyze_report(source, corpus)
            self.assertEqual(result["records"][0]["body_notes"], ["T"])
            self.assertEqual(result["observed_corpus_sha256"], metadata["sha256"])
            observed.write_text('{"notes":[]}')
            with self.assertRaisesRegex(ValueError, "observed corpus hash"):
                evidence.analyze_report(source, corpus)
            metadata["path"] = "../outside.json"
            source.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "outside"):
                evidence.analyze_report(source, corpus)

    def test_retained_report_enforces_frozen_source_and_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus.json"
            corpus.write_text(json.dumps(dict(notes=[dict(id="T", body="guidance")])))
            source = root / "agent.json"
            record = dict(run_id="case.baseline.s0", case="case", arm="baseline", seed=0, outcome="C")
            report = dict(frozen=dict(corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest()), records=[record])
            source.write_text(json.dumps(report))
            result = evidence.analyze_report(source, corpus)
            self.assertTrue(result["records"][0]["missing_stream"])
            self.assertEqual(result["records"][0]["original_outcome"], "C")
            stream = root / "runs" / record["run_id"] / "stream.jsonl"
            stream.parent.mkdir(parents=True)
            stream.write_text("{\"type\":\"result\"}\n")
            result = evidence.analyze_report(source, corpus)
            self.assertEqual(result["records"][0]["source_sha256"], hashlib.sha256(stream.read_bytes()).hexdigest())
            self.assertEqual(result["records"][0]["calls"], [])
            record["run_id"] = "../../outside"
            source.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "outside"):
                evidence.analyze_report(source, corpus)
            corpus.write_text('{"notes":[]}')
            with self.assertRaisesRegex(ValueError, "frozen hash"):
                evidence.analyze_report(source, corpus)

    def test_unknown_results_remain_unaccounted_and_wrong_scope_fails(self):
        def event(content):
            return json.dumps(dict(type="item.completed", item=dict(id="a", type="mcp_tool_call", server="cairn",
                tool="cairn_search", status="completed", result=dict(content=content))))
        payload = dict(schema="cairn.mcp-search/1", scope=dict(repo="trial:task-eval"),
                       index=[dict(body_sha256="unknown", summary="Unmapped preview")])
        content = text(payload) + text(dict(history=[])) + [dict(type="text", text="unparseable")]
        result = evidence.analyze_stream(event(content), "codex", [dict(id="T", body="guidance")])
        self.assertEqual(result["unmapped_deliveries"], 1)
        self.assertEqual(result["preview_notes"], [])
        self.assertEqual(result["calls"][0]["unparsed_blocks"], 1)
        self.assertEqual(result["calls"][0]["unrecognized_payloads"], 1)
        payload["scope"]["repo"] = "another-collection"
        with self.assertRaisesRegex(ValueError, "outside the trial collection"):
            evidence.analyze_stream(event(text(payload)), "codex", [dict(id="T", body="guidance")])

    def test_codex_only_counts_successful_verified_span_and_keeps_pending_calls(self):
        body = "α first. Useful passage. End."
        excerpt = "Useful passage."
        offset = len("α first. ".encode())
        payload = dict(selection=dict(record=dict(record_id="r", version=1, body="", scope=dict(repo="trial:task-eval"))),
                       span=dict(body=excerpt, offset=offset, end=offset + len(excerpt.encode()), total_bytes=len(body.encode()),
                                 sha256=evidence.sha256(excerpt), source_sha256=evidence.sha256(body)))
        def item(identifier, status, result=None):
            return dict(id=identifier, type="mcp_tool_call", server="cairn", tool="cairn_pull", status=status, result=result)
        events = [dict(type="item.started", item=item("a", "in_progress")),
                  dict(type="item.completed", item=item("a", "completed", dict(content=text(payload)))),
                  dict(type="item.completed", item=item("bad", "completed", dict(isError=True, content=text(payload)))),
                  dict(type="item.started", item=item("pending", "in_progress"))]
        report = evidence.analyze_stream("\n".join(map(json.dumps, events)), "codex", [dict(id="T", body=body)])
        self.assertEqual([c["status"] for c in report["calls"]], ["completed", "failed", "incomplete"])
        self.assertEqual(report["body_notes"], ["T"])
        self.assertEqual(report["body_bytes"], len(excerpt.encode()))
        self.assertEqual(report["calls"][0]["deliveries"][0]["extent"], "partial_span")
        payload["span"]["offset"] += 1
        with self.assertRaisesRegex(ValueError, "span"):
            evidence.observed_deliveries(payload, [dict(id="T", body=body)])

    def test_claude_matches_results_to_calls_and_separates_preview_from_body(self):
        body = "Project choice: use local checks."
        digest = hashlib.sha256(body.encode()).hexdigest()
        search = dict(schema="cairn.mcp-search/1", scope=dict(repo="trial:task-eval"), selected=[],
                      index=[dict(record_id="r1", version=1, body_sha256=digest, summary="Project choice")])
        pull = dict(selection=dict(record=dict(record_id="r1", version=1, body=body,
                                               scope=dict(repo="trial:task-eval"))))
        events = [
            dict(type="assistant", message=dict(content=[dict(type="tool_use", id="bad", name="mcp__cairn__cairn_search", input={}),
                                                         dict(type="tool_use", id="search", name="mcp__cairn__cairn_search", input={})])),
            dict(type="user", message=dict(content=[dict(type="tool_result", tool_use_id="search", content=text(search)),
                                                    dict(type="tool_result", tool_use_id="bad", is_error=True, content="invalid request")])),
            dict(type="assistant", message=dict(content=[dict(type="tool_use", id="pull", name="mcp__cairn__cairn_pull", input={})])),
            dict(type="user", message=dict(content=[dict(type="tool_result", tool_use_id="pull", content=text(pull))])),
        ]
        report = evidence.analyze_stream("\n".join(map(json.dumps, events)), "claude", [dict(id="T", body=body)])
        self.assertEqual([c["status"] for c in report["calls"]], ["failed", "completed", "completed"])
        self.assertEqual(report["preview_notes"], ["T"])
        self.assertEqual(report["body_notes"], ["T"])
        self.assertEqual(report["body_bytes"], len(body.encode()))
        self.assertNotIn(body, json.dumps(report))
        self.assertGreater(report["response_text_bytes"], report["body_bytes"])


if __name__ == "__main__":
    unittest.main()
