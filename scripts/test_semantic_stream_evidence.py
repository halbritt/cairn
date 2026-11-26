import json
from pathlib import Path
import subprocess
import sys
import unittest

from semantic_stream_evidence import cache_handoff, canonical, compare_scores, digest, mapped_reference, stream_plan, trace_frames


def call(kind, fd, data, count=None):
    encoded = "".join(f"\\x{x:02x}" for x in data)
    return f'{kind}({fd}, "{encoded}", {len(data)}) = {len(data) if count is None else count}\n'


class StreamEvidenceTest(unittest.TestCase):
    def test_cli_entry_point_loads_only_its_sibling_under_isolated_python(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("semantic_stream_evidence.py")), "--help"],
                              check=True, capture_output=True, text=True)
        self.assertIn("--retained-stream-manifest", done.stdout)

    def test_phase_names_do_not_invent_nineteen_cold_processes(self):
        ids = [str(i) for i in range(19)]
        inventory = dict(rank_ids=ids, expected_counts=dict(stream_replies=38),
                         required_reference_cells=[f"stream/{x}/{p}" for x in ids for p in ("cold", "warm")])
        requests = [dict(id=x, request=dict(query=x)) for x in ids]
        inventory["fixture_sha256"] = dict(rank=digest(canonical([dict(id=x["id"], **x["request"]) for x in requests])))
        plan = stream_plan(inventory, requests)
        self.assertEqual(38, len(plan))
        self.assertEqual(1, sum(x["actual_phase"] == "initial_cold" for x in plan))
        self.assertEqual(37, sum(x["actual_phase"] == "warm_same_child" for x in plan))
        requests[0]["request"]["query"] = "changed"
        with self.assertRaisesRegex(ValueError, "fixture digest"):
            stream_plan(inventory, requests)
        requests.reverse()
        with self.assertRaisesRegex(ValueError, "frozen 19-case order"):
            stream_plan(inventory, requests)

    def test_reassembles_split_reads_and_partial_writes(self):
        raw = b'{"id":"1"}\n'
        text = call("read", 0, raw[:4]) + call("read", 0, raw[4:])
        text += call("write", 1, raw, 4) + call("write", 1, raw[4:])
        self.assertEqual(dict(read=[dict(id="1")], write=[dict(id="1")]), trace_frames(text))

    def test_reassembles_unfinished_read(self):
        raw = b'{"id":"2"}\n'
        resumed = call("read", 0, raw).replace("read(0, ", "<... read resumed>")
        got = trace_frames("read(0,  <unfinished ...>\n" + resumed)
        self.assertEqual([dict(id="2")], got["read"])

    def test_refuses_truncated_trace(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            trace_frames('read(0, "\\x7b"..., 4096) = 20\n')

    def test_refuses_lost_bytes_and_incomplete_frame(self):
        with self.assertRaisesRegex(ValueError, "lost bytes"):
            trace_frames(call("read", 0, b"{}\n", 4))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            trace_frames(call("read", 0, b"{}"))

    def test_cache_handoff_observes_state_not_only_equal_scores(self):
        old = dict(cache=dict(model="one", rows=[dict(hash="a", vector="AAAA")]))
        copied = json.loads(json.dumps(old))
        proof = cache_handoff(old, copied)
        self.assertTrue(proof["exact_json_state_handoff"])
        copied["cache"]["rows"][0]["vector"] = "AAAB"
        with self.assertRaisesRegex(ValueError, "retained snapshot"):
            cache_handoff(old, copied)

    def test_missing_cache_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "did not supply"):
            cache_handoff({}, {})

    def test_rule_c_and_source_binding(self):
        score = dict(record_id="id", version=2, body_sha256="a" * 64, score=100)
        reference = dict(algorithm="bge-max-chunk/1", scores=[score])
        result = json.loads(json.dumps(reference))
        result["scores"][0]["score"] = 600
        self.assertEqual([500], compare_scores(result, reference))
        result["scores"][0]["score"] = 601
        with self.assertRaisesRegex(ValueError, "rule C"):
            compare_scores(result, reference)
        result["scores"][0]["record_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "metadata"):
            compare_scores(result, reference)

    def test_retained_alias_adapter_preserves_numeric_and_source_bytes(self):
        body = "abc"
        note = dict(record_id="storage", version=1, body=body, body_sha256=digest(body.encode()))
        score = {k: note[k] for k in ("record_id", "version", "body_sha256")}
        score["score"] = 123
        request, result, aliases = mapped_reference(dict(query="q", notes=[note]), dict(result=dict(scores=[score])))
        self.assertEqual(123, result["scores"][0]["score"])
        self.assertEqual(body, request["notes"][0]["body"])
        self.assertEqual(aliases["storage"], result["scores"][0]["record_id"])
        note["version"] = 2
        with self.assertRaisesRegex(ValueError, "beyond the ID adapter"):
            mapped_reference(dict(query="q", notes=[note]), dict(result=dict(scores=[score])))


if __name__ == "__main__":
    unittest.main()
