"""Framing and early refusal checks require no embedding dependencies."""
import hashlib
import io
import json
import unittest
from unittest.mock import patch

import semantic_rank


class SemanticStreamTest(unittest.TestCase):
    def request(self, body="first body", request_id="1"):
        note = dict(record_id="record", version=1, body=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest())
        return dict(id=request_id, request=dict(query="query", notes=[note]))

    def test_reuses_scorer_but_passes_current_request_each_time(self):
        first, second = self.request(), self.request("changed 日本語 body", "2")
        raw = b"".join(json.dumps(r).encode() + b"\n" for r in [first, second])
        output = io.BytesIO()
        with patch.object(semantic_rank, "Scorer") as factory:
            factory.return_value.score.side_effect = [dict(score=1), dict(score=2)]
            semantic_rank.serve_stream("model", io.BytesIO(raw), output)
            factory.assert_called_once_with("model")
            self.assertEqual([c.args[0] for c in factory.return_value.score.call_args_list],
                             [first["request"], second["request"]])
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [dict(id="1", result=dict(score=1)), dict(id="2", result=dict(score=2))])

    def test_bad_framing_or_hash_refuses_before_loading_model(self):
        bad_hash = self.request()
        bad_hash["request"]["notes"][0]["body"] = "changed without matching hash"
        cases = [b"{}", b"{}\n", b"x" * (8*1024*1024+2),
                 json.dumps(bad_hash).encode()+b"\n",
                 json.dumps(self.request(request_id=False)).encode()+b"\n"]
        for raw in cases:
            with self.subTest(length=len(raw)), patch.object(semantic_rank,"Scorer") as factory:
                with self.assertRaises(ValueError):
                    semantic_rank.serve_stream("model",io.BytesIO(raw),io.BytesIO())
                factory.assert_not_called()

    def test_eof_without_request_does_not_initialize(self):
        with patch.object(semantic_rank,"Scorer") as factory:
            semantic_rank.serve_stream("model",io.BytesIO(),io.BytesIO())
            factory.assert_not_called()

    def test_second_invalid_request_does_not_score_or_emit_result(self):
        first = self.request()
        bad = self.request("second", "2")
        bad["request"]["notes"][0]["body_sha256"] = "wrong"
        output = io.BytesIO()
        with patch.object(semantic_rank,"Scorer") as factory:
            factory.return_value.score.return_value = dict(score=1)
            with self.assertRaises(ValueError):
                semantic_rank.serve_stream("model",io.BytesIO(b"".join(
                    json.dumps(r).encode()+b"\n" for r in [first,bad])),output)
            self.assertEqual(factory.return_value.score.call_count,1)
        self.assertEqual(len(output.getvalue().splitlines()),1)
