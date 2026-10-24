import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("tiktoken"), "run with uv --with tiktoken==0.12.0")
class AccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("audit", Path(__file__).with_name("audit_model_overhead.py"))
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def run_records(self, records, harness):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"native.jsonl"
            path.write_text("".join(json.dumps(dict(timestamp="2026-10-22T01:00:00Z", **r))+"\n" for r in records))
            return getattr(self.audit, "analyze_"+harness)(path, 0, 2000000000)

    def test_codex_response_records_not_cumulative_or_duplicate(self):
        usage = dict(input_tokens=100, cached_input_tokens=80, output_tokens=12, reasoning_output_tokens=5)
        record = dict(type="token_usage_record", payload=dict(turn_id="t", response_id="r", usage=usage,
                      thread_token_usage=dict(input_tokens=99999)))
        result = self.run_records([
            dict(type="event_msg", payload=dict(type="task_started", turn_id="t")),
            record, record,
            dict(type="event_msg", payload=dict(type="token_count", info=dict(total_token_usage=usage))),
            dict(type="event_msg", payload=dict(type="task_complete", turn_id="t")),
        ], "codex")
        self.assertEqual(len(result["turns"]["t"]["usage"]), 1)
        self.assertEqual(result["turns"]["t"]["usage"][0], dict(input=100, cached=80, write=0, output=12, reasoning=5))
        self.assertEqual(result["errors"]["duplicate_usage_records"], 1)

    def test_claude_stream_blocks_charge_input_once_and_thinking_is_subset(self):
        messages = [dict(type="user", promptId="p", message=dict(content=self.audit.WAKE))]
        for output in [2, 9]:
            messages.append(dict(type="assistant", message=dict(id="m", model="fixture", content=[], usage=dict(
                input_tokens=3, cache_creation_input_tokens=7, cache_read_input_tokens=90,
                output_tokens=output, output_tokens_details=dict(thinking_tokens=4)))))
        result = self.run_records(messages, "claude")
        self.assertEqual(result["turns"]["p"]["usage"], [dict(input=100, cached=90, write=7, output=9, reasoning=4)])

    def test_claude_missing_thinking_stays_unknown(self):
        result = self.run_records([
            dict(type="user", promptId="p", message=dict(content="fixture")),
            dict(type="assistant", message=dict(id="m", content=[], usage=dict(input_tokens=1,
                 output_tokens=2, output_tokens_details=None)))], "claude")
        self.assertIsNone(result["turns"]["p"]["usage"][0]["reasoning"])

    def test_claude_request_reuse_and_missing_blob_are_separate(self):
        result = self.run_records([
            dict(type="api-request-blob", hash="a", message=dict(role="user", content="Cairn inbox: fixture")),
            dict(type="api-request", keep=0, tail=["a"], querySource="fixture"),
            dict(type="api-request", keep=1, tail=["unavailable"], querySource="fixture"),
        ], "claude")
        rows = result["request_metrics"]
        self.assertEqual([r["missing_blobs"] for r in rows], [0, 1])
        self.assertEqual(result["exposure_repetitions"]["sum"], 2)
        self.assertEqual(rows[0]["cairn_message_bytes"], rows[1]["cairn_message_bytes"])

    def test_journal_failure_does_not_become_handled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"binding/inbox").mkdir(parents=True)
            (root/"binding/intents").mkdir()
            (root/"binding/inbox/a.json").write_text(json.dumps(dict(native_turn_id="t", kind="notice")))
            (root/"binding/intents/a.json").write_text(json.dumps(dict(completion=dict(
                status="committed", kind="ack", argv=["ack", "--disposition", "failed"], code="OK"))))
            turn = self.audit.blank_turn()
            self.audit.join_journals([dict(turns=dict(t=turn))], root)
            self.assertTrue(turn["durable"][0]["committed"])
            self.assertEqual(turn["durable"][0]["disposition"], "failed")

    def test_partial_log_line_is_not_silently_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"log"
            path.write_text('{"type":"session_meta","payload":{}}\n{"type":')
            result = self.audit.analyze_codex(path, 0, 2000000000)
            self.assertEqual(result["errors"]["partial_lines"], 1)


if __name__ == "__main__":
    unittest.main()
