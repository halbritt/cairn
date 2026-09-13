"""Snapshot transfer uses the normal stream, with deterministic numerical fixtures."""
import io
import json
import unittest
from unittest.mock import patch

import semantic_rank
import test_semantic_cache as fixtures


@unittest.skipIf(fixtures.np is None, "requires optional semantic-worker NumPy")
class SemanticResumeTest(unittest.TestCase):
    def scorer(self):
        return fixtures.SemanticCacheTest().scorer()

    def request(self, *bodies):
        return fixtures.SemanticCacheTest().request(*bodies)

    def exchange(self, scorer, request, cache=None):
        envelope = dict(id="1", request=request)
        if cache is not None:
            envelope["cache"] = cache
        output = io.BytesIO()
        with patch.object(semantic_rank, "Scorer", return_value=scorer):
            semantic_rank.serve_stream("model", io.BytesIO(json.dumps(envelope).encode()+b"\n"),
                                       output, cache_enabled=True)
        return json.loads(output.getvalue())

    def test_fresh_scorer_restores_exact_passages_through_stream(self):
        request = self.request("first note", "second note")
        first = self.exchange(self.scorer(), request)
        resumed = self.scorer()
        second = self.exchange(resumed, request, first["cache"])
        self.assertEqual(first, second)
        self.assertEqual(resumed.model.inputs, [[semantic_rank.PREFIX + request["query"]]])
        self.assertEqual(set(first["cache"]), {"format", "model_sha256", "vectors"})
        self.assertNotIn("first note", json.dumps(first["cache"]))

    def test_changed_removed_and_mismatched_model_recompute_only_current_inputs(self):
        both = self.request("first note", "second note")
        snapshot = self.exchange(self.scorer(), both)["cache"]
        for request in [self.request("changed note", "second note"), self.request("second note")]:
            fresh = self.scorer()
            response = self.exchange(fresh, request, snapshot)
            self.assertEqual(response["result"], self.scorer().score(request))
            self.assertEqual(len(response["cache"]["vectors"]), len(request["notes"]))
            self.assertNotIn("first note", fresh.model.inputs[0])
        fresh = self.scorer()
        response = self.exchange(fresh, both, dict(snapshot, model_sha256="different-model"))
        self.assertEqual(response["result"], self.scorer().score(both))
        self.assertEqual(len(fresh.model.inputs[0]), 3)

    def test_bad_snapshot_refuses_without_scoring_or_output(self):
        import base64
        import copy
        import hashlib
        request = self.request("known note")
        state = self.exchange(self.scorer(), request)["cache"]
        key = next(iter(state["vectors"]))
        cases = [[], dict(state, format="unknown"), dict(state, surprise=True),
                 dict(state, vectors={key: 7}), dict(state, vectors={key: "bad"}),
                 dict(state, vectors={key: state["vectors"][key], "f" * 64: "bad"}),
                 dict(state, vectors={key: "!" * 2048}),
                 dict(state, vectors={"bad-hash": state["vectors"][key]}),
                 dict(state, vectors={hashlib.sha256(str(i).encode()).hexdigest(): state["vectors"][key]
                                      for i in range(129)}),
                 dict(state, vectors={key: "x" * semantic_rank.CACHE_LIMIT})]
        for value in [0.0, float("nan"), float("inf")]:
            vector = fixtures.np.full(384, value, dtype="<f4")
            cases.append(dict(state, vectors={key: base64.b64encode(vector.tobytes()).decode()}))
        for bad in cases:
            fresh = self.scorer()
            with self.subTest(cache_type=type(bad)), self.assertRaises(ValueError):
                self.exchange(fresh, request, copy.deepcopy(bad))
            self.assertEqual(fresh.model.inputs, [])
            self.assertEqual(fresh.chunk_vectors, {})

    def test_full_capacity_round_trip_preserves_vector_ownership(self):
        body = " ".join("word" + str(i) for i in range(320 * 127 + 384))
        request = self.request(body)
        first = self.exchange(self.scorer(), request)
        self.assertEqual(len(first["cache"]["vectors"]), 128)
        self.assertLess(len(json.dumps(first["cache"]).encode()), semantic_rank.CACHE_LIMIT)
        resumed = self.scorer()
        self.assertEqual(first, self.exchange(resumed, request, first["cache"]))
        self.assertEqual(len(resumed.model.inputs[0]), 1)
        self.assertTrue(all(v.base is None for v in resumed.chunk_vectors.values()))


    def test_old_host_shape_and_late_restore_refusal(self):
        request = self.request("known note")
        envelope = dict(id="1", request=request)
        old_output = io.BytesIO()
        with patch.object(semantic_rank, "Scorer", return_value=self.scorer()):
            semantic_rank.serve_stream("model", io.BytesIO(json.dumps(envelope).encode()+b"\n"), old_output)
        self.assertEqual(set(json.loads(old_output.getvalue())), {"id", "result"})
        first = self.exchange(self.scorer(), request)
        late = dict(id="2", request=request, cache=first["cache"])
        output = io.BytesIO()
        scorer = self.scorer()
        with patch.object(semantic_rank, "Scorer", return_value=scorer), self.assertRaisesRegex(ValueError, "fresh worker"):
            semantic_rank.serve_stream("model", io.BytesIO(b"".join(json.dumps(x).encode()+b"\n" for x in [envelope, late])),
                                       output, cache_enabled=True)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(len(scorer.model.inputs), 1)
