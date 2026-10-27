import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

@unittest.skipUnless(importlib.util.find_spec("tiktoken"), "run with uv --with tiktoken==0.12.0")
class SchemaExposureAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import audit_schema_exposure
        cls.audit = audit_schema_exposure

    def test_overlapping_request_phases_and_final_streaming_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            rows = [
                dict(type="api-request-shape", shapeHash="shape-a", shape=dict(tools=[
                    dict(name="cairn_search", description="Search selected source", input_schema=dict(type="object")),
                    dict(name="other_tool", description="Unrelated tool")]), timestamp="2026-10-24T00:00:00Z"),
                dict(type="api-request", shapeHash="shape-a", requestDigest="digest-a", timestamp="2026-10-24T00:01:00Z"),
                dict(type="assistant", message=dict(id="response-a", usage=dict(input_tokens=5,
                     cache_creation_input_tokens=7, cache_read_input_tokens=11), content=[
                         dict(type="tool_use", name="cairn_pull")]), timestamp="2026-10-24T00:01:01Z"),
                dict(type="assistant", message=dict(id="response-a", usage=dict(input_tokens=6,
                     cache_creation_input_tokens=8, cache_read_input_tokens=12), content=[]), timestamp="2026-10-24T00:01:02Z"),
                dict(type="api-request", shapeHash="shape-a", requestDigest="digest-a", timestamp="2026-10-24T00:02:00Z"),
                dict(type="system", subtype="compact_boundary", timestamp="2026-10-24T00:03:00Z"),
                dict(type="api-request", shapeHash="shape-a", requestDigest="digest-b", timestamp="2026-10-24T00:04:00Z"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            requests, errors = self.audit.inspect_claude(path, dt.datetime.fromisoformat("2026-10-24T00:00:00+00:00"),
                                                     dt.datetime.fromisoformat("2026-10-25T00:00:00+00:00"))
            result = self.audit.aggregate(requests, errors)
            self.assertEqual(result["total_requests"], 3)
            self.assertEqual(result["requests_by_cairn_tool_count"], {"1": 3})
            self.assertEqual(result["phases"]["file_first"]["requests"], 1)
            self.assertEqual(result["phases"]["first_cairn_use"]["requests"], 1)
            self.assertEqual(result["phases"]["first_pull"]["requests"], 1)
            self.assertEqual(result["phases"]["first_write"]["requests"], 0)
            self.assertEqual(result["phases"]["same_digest_repeat"]["requests"], 1)
            self.assertEqual(result["phases"]["post_compaction"]["requests"], 1)
            self.assertEqual(result["phases"]["after_first_cairn_use"]["requests"], 2)
            self.assertEqual(result["phases"]["file_first"]["responses_with_usage"], 1)
            self.assertEqual(result["phases"]["file_first"]["requests_with_usage"], 1)
            self.assertEqual(result["phases"]["file_first"]["response_input_tokens"], 26)
            self.assertEqual(result["phases"]["file_first"]["response_cache_read_tokens"], 12)
            self.assertEqual(result["phases"]["file_first"]["cairn_tool_count"], 1)

    def test_pre_window_request_does_not_become_cold_and_unknown_shape_stays_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            rows = [dict(type="api-request", shapeHash="missing", requestDigest="old",
                         timestamp="2026-10-23T23:59:00Z"),
                    dict(type="api-request", shapeHash="missing", requestDigest="old",
                         timestamp="2026-10-24T00:01:00Z")]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            requests, errors = self.audit.inspect_claude(path, dt.datetime.fromisoformat("2026-10-24T00:00:00+00:00"),
                                                     dt.datetime.fromisoformat("2026-10-25T00:00:00+00:00"))
            result = self.audit.aggregate(requests, errors)
            self.assertEqual(result["phases"]["file_first"]["requests"], 0)
            self.assertEqual(result["phases"]["same_digest_repeat"]["requests"], 1)
            self.assertEqual(result["requests_without_shape"], 1)


if __name__ == "__main__":
    unittest.main()
