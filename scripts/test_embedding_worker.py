import unittest
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from embedding_worker import source_passages


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(offsets=[(i, i + 1) for i in range(len(text))])


class PassageContractTest(unittest.TestCase):
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
