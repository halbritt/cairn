"""Optional numerical checks; run with the prepared semantic worker environment."""
import hashlib
import unittest

import semantic_rank

try:
    import numpy as np
except ImportError:
    np = None


class Tokenizer:
    def encode(self, text, **kwargs):
        class Encoded:
            ids = text.split()
        return Encoded()

    def decode(self, tokens, **kwargs):
        return " ".join(tokens)


class Model:
    def __init__(self):
        self.inputs = []

    def embed(self, texts, batch_size):
        self.inputs.append(list(texts))
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            yield np.array(list(digest) * 12, dtype=np.float32)


@unittest.skipIf(np is None, "requires optional semantic-worker NumPy")
class SemanticCacheTest(unittest.TestCase):
    def scorer(self):
        scorer = object.__new__(semantic_rank.Scorer)
        scorer.model = Model()
        scorer.tokenizer = Tokenizer()
        scorer.np = np
        scorer.model_hash = "fixture"
        scorer.chunk_vectors = {}
        return scorer

    def request(self, *bodies, query="selected question"):
        return dict(query=query, notes=[dict(record_id=str(i), version=1, body=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest())
                    for i, body in enumerate(bodies)])

    def test_repeated_notes_only_embed_the_new_query(self):
        scorer = self.scorer()
        req = self.request("first note", "second note")
        first = scorer.score(req)
        self.assertEqual(first, scorer.score(req))
        self.assertEqual(scorer.model.inputs[-1], [semantic_rank.PREFIX + req["query"]])
        changed_query = dict(req, query="another question")
        self.assertEqual(scorer.score(changed_query), self.scorer().score(changed_query))
        self.assertEqual(scorer.model.inputs[-1], [semantic_rank.PREFIX + changed_query["query"]])

    def test_changed_body_reembeds_but_metadata_does_not(self):
        scorer = self.scorer()
        req = self.request("first note", "second note")
        scorer.score(req)
        req["notes"][0].update(record_id="another-record", version=2)
        self.assertEqual(scorer.score(req), self.scorer().score(req))
        self.assertEqual(len(scorer.model.inputs[-1]), 1)
        changed = self.request("changed body", "second note")
        self.assertEqual(scorer.score(changed), self.scorer().score(changed))
        self.assertEqual(scorer.model.inputs[-1],
                         [semantic_rank.PREFIX + changed["query"], "changed body"])

    def test_appending_to_long_note_reuses_unchanged_passages(self):
        scorer = self.scorer()
        body = " ".join("word" + str(i) for i in range(1000))
        scorer.score(self.request(body))
        changed = self.request(body + " an appended verification step")
        self.assertEqual(scorer.score(changed), self.scorer().score(changed))
        self.assertEqual(scorer.model.inputs[-1], [
            semantic_rank.PREFIX + changed["query"],
            " ".join(body.split()[640:]) + " an appended verification step",
        ])

    def test_removed_sources_are_not_retained_for_later_requests(self):
        scorer = self.scorer()
        both = self.request("first note", "second note")
        scorer.score(both)
        one = self.request("second note")
        self.assertEqual(scorer.score(one), self.scorer().score(one))
        self.assertEqual(len(scorer.model.inputs[-1]), 1)
        self.assertEqual(scorer.score(both), self.scorer().score(both))
        self.assertEqual(scorer.model.inputs[-1],
                         [semantic_rank.PREFIX + both["query"], "first note"])

    def test_mixed_hits_misses_duplicates_and_long_notes_preserve_scores(self):
        scorer = self.scorer()
        long = " ".join("word" + str(i) for i in range(800))
        scorer.score(self.request("first", long))
        for req in (self.request(long, "new", "first", long),
                    self.request("new", long, "new", query="different query")):
            self.assertEqual(scorer.score(req), self.scorer().score(req))

    def test_cache_hits_do_not_bypass_chunk_budget(self):
        scorer = self.scorer()
        long = "word " * (semantic_rank.STRIDE * 127 + semantic_rank.WINDOW)
        scorer.score(self.request(long))
        before = len(scorer.model.inputs)
        for req in (self.request(long, "extra"), self.request("extra", long)):
            with self.assertRaisesRegex(ValueError, "chunk budget exceeded"):
                scorer.score(req)
        self.assertEqual(len(scorer.model.inputs), before)

    def test_duplicate_passages_share_inference_without_retaining_text_or_views(self):
        scorer = self.scorer()
        req = self.request("shared passage", "shared passage")
        result = scorer.score(req)
        self.assertEqual(len(result["scores"]), 2)
        self.assertEqual(result["scores"][0]["score"], result["scores"][1]["score"])
        self.assertEqual(scorer.model.inputs[-1],
                         [semantic_rank.PREFIX + req["query"], "shared passage"])
        self.assertEqual(list(scorer.chunk_vectors),
                         [hashlib.sha256(b"shared passage").hexdigest()])
        vector = next(iter(scorer.chunk_vectors.values()))
        self.assertIsNone(vector.base)
        self.assertEqual(vector.nbytes, 384 * 4)

    def test_unique_chunk_retention_is_bounded_and_replaced_after_success(self):
        scorer = self.scorer()
        body = " ".join("w" + str(i) for i in range(320 * 127 + 384))
        scorer.score(self.request(body))
        self.assertEqual(len(scorer.chunk_vectors), 128)
        self.assertEqual(sum(v.nbytes for v in scorer.chunk_vectors.values()),
                         128 * 384 * 4)
        scorer.score(self.request("fresh note"))
        self.assertEqual(list(scorer.chunk_vectors),
                         [hashlib.sha256(b"fresh note").hexdigest()])

    def test_invalid_embedding_does_not_publish_a_partial_cache(self):
        scorer = self.scorer()
        first = self.request("first")
        scorer.score(first)
        good = scorer.model
        class InvalidModel:
            def embed(self, texts, batch_size):
                return [np.zeros(384) for _ in texts]
        scorer.model = InvalidModel()
        with self.assertRaisesRegex(ValueError, "empty model embedding"):
            scorer.score(self.request("changed"))
        scorer.model = good
        self.assertEqual(scorer.score(first), self.scorer().score(first))
        self.assertEqual(good.inputs[-1], [semantic_rank.PREFIX + first["query"]])
