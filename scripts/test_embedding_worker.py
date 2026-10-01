import unittest
import hashlib
import os
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from embedding_worker import Embeddings, source_passages
from semantic_rank import PREFIX


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(offsets=[(i, i + 1) for i in range(len(text))])


class PassageContractTest(unittest.TestCase):
    def test_long_query_embeds_one_original_unicode_prefix(self):
        class Tokenizer:
            def encode(self, text, **kwargs):
                return SimpleNamespace(ids=list(range(len(text) + 2)),
                                       offsets=[(0, 0)] + [(i, i + 1) for i in range(len(text))] + [(0, 0)])
        worker = Embeddings.__new__(Embeddings)
        worker.scorer = SimpleNamespace(tokenizer=Tokenizer())
        worker.identity = {}
        calls = []
        worker.vectors = lambda texts: calls.append(texts) or [[1.0] * 384]
        text = "é漢🙂 with source constraints " * 30
        result = worker.respond(dict(id="long", operation="query", text=text))
        self.assertEqual(len(calls), 1)
        question = calls[0][0]
        prefix = question[len(PREFIX):]
        self.assertTrue(text.startswith(prefix))
        self.assertLess(len(prefix), len(text))
        self.assertLessEqual(len(worker.scorer.tokenizer.encode(question).ids), 512)
        projection = result["query_projection"]
        self.assertTrue(projection["truncated"])
        self.assertEqual(projection["prefix_bytes"], len(prefix.encode()))
        self.assertEqual(projection["prefix_sha256"], hashlib.sha256(prefix.encode()).hexdigest())
        self.assertEqual(projection["original_tokens"], len(text) + len(PREFIX) + 2)
        self.assertEqual(projection["embedded_tokens"], len(question) + 2)

    @unittest.skipUnless(os.environ.get("CAIRN_EMBEDDING_TOKENIZER"), "requires prepared local tokenizer (no model inference)")
    def test_actual_tokenizer_long_and_short_queries(self):
        import time
        from tokenizers import Tokenizer, AddedToken
        path = Path(os.environ["CAIRN_EMBEDDING_TOKENIZER"])
        tokenizer = Tokenizer.from_file(str(path))
        tokenizer.no_truncation(); tokenizer.no_padding()
        for value in json.loads((path.parent / "special_tokens_map.json").read_text()).values():
            tokenizer.add_special_tokens([value if isinstance(value, str) else AddedToken(**value)])
        worker = Embeddings.__new__(Embeddings)
        worker.scorer = SimpleNamespace(tokenizer=tokenizer); worker.identity = {}
        calls = []
        worker.vectors = lambda texts: calls.append(texts) or [[1.0] * 384]
        started = time.monotonic()
        for text, truncated in (("database storage", False), ("é漢🙂 tokenize pathological constraints! " * 75, True)):
            self.assertLessEqual(len(text.encode()), 4096)
            result = worker.respond(dict(id="actual-tokenizer", operation="query", text=text))
            projection = result["query_projection"]
            self.assertEqual(projection["truncated"], truncated)
            question = calls[-1][0]
            prefix = text.encode()[:projection["prefix_bytes"]].decode()
            self.assertEqual(question, PREFIX + prefix)
            self.assertEqual(len(tokenizer.encode(question).ids), projection["embedded_tokens"])
            self.assertLessEqual(projection["embedded_tokens"], 512)
            if truncated:
                self.assertGreater(projection["original_tokens"], 512)
            else:
                self.assertEqual(question, PREFIX + text)
        self.assertEqual(len(calls), 2)
        print("Actual tokenizer: two single-embedding requests, %.6fs tokenization" % (time.monotonic() - started))

    def test_imported_scorer_change_invalidates_worker_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(Path(__file__).with_name("embedding_worker.py"), root / "embedding_worker.py")
            scorer = root / "semantic_rank.py"
            # A fixed model/package identity isolates the imported code change.
            scorer.write_text('PREFIX="query: "\nclass Scorer:\n def __init__(self,path): self.model_hash="a"*64\n')
            def identity():
                result = subprocess.run([sys.executable, "-B", str(root / "embedding_worker.py"), "--model-dir", directory],
                                        input=json.dumps(dict(id="identity", operation="identity", text="")) + "\n",
                                        capture_output=True, text=True, check=True)
                return json.loads(result.stdout)["identity"]["model_sha256"]
            first = identity()
            scorer.write_text(scorer.read_text() + "# revised scorer implementation\n")
            self.assertNotEqual(first, identity())

    def test_utf8_spans_address_original_bytes_and_cover_tail(self):
        body = "é漢🙂 before guidance. " * 25 + "Use the private directory."
        raw = body.encode()
        passages = list(source_passages(CharacterTokenizer(), body))
        covered = bytearray(len(raw))
        for text, span in passages:
            start, end = span["offset"], span["offset"] + span["length"]
            self.assertEqual(raw[start:end].decode(), text)
            covered[start:end] = b"\x01" * (end - start)
        self.assertTrue(all(covered))
        self.assertIn("Use the private directory.", passages[-1][0])

    def test_maximum_token_density_stays_bounded_without_truncating(self):
        body = "a" * 65536
        passages = list(source_passages(CharacterTokenizer(), body))
        self.assertLessEqual(len(passages), 256)
        self.assertEqual(passages[0][1]["offset"], 0)
        end = 0
        for text, span in passages:
            self.assertLessEqual(span["offset"], end)
            self.assertLessEqual(len(text), 510)
            end = span["offset"] + span["length"]
        self.assertEqual(end, len(body))


if __name__ == "__main__":
    unittest.main()
