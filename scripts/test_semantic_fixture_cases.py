"""Input-contract tests; these execute no tokenizer or model."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
import ast
import uuid
from pathlib import Path

import semantic_fixture_cases


class SemanticFixtureCasesTest(unittest.TestCase):
    def test_rank_inputs_match_frozen_nineteen_case_oracle(self):
        cases = semantic_fixture_cases.ranking_cases()
        self.assertEqual(len(cases), 19)
        self.assertEqual(sum(len(case['notes']) for case in cases), 269)
        self.assertEqual([case['id'] for case in cases[-2:]], ['long-tail', '64-short'])
        canonical = json.dumps(cases, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), 'ebeecaa4a508ec4cd735ae6f6844f3c96dd30f4d48ef85e7caadbc4cdc9420ba')
        # Execute only the frozen comparator's pure case-building statements,
        # before repeat-storage, argparse, subprocesses or worker calls.
        root = Path(semantic_fixture_cases.__file__).resolve().parents[1]
        source = subprocess.check_output(['git', '-C', str(root), 'show',
            'd3c255a4bb75438e93527eaa1d83da780fd11eea:scripts/compare_semantic_workers.py'])
        body = ast.parse(source).body
        start = next(index for index, node in enumerate(body) if isinstance(node, ast.FunctionDef) and node.name == 'note')
        stop = next(index for index, node in enumerate(body) if isinstance(node, ast.AugAssign)
                    and isinstance(node.target, ast.Name) and node.target.id == 'cases')
        namespace = {'uuid': uuid, 'hashlib': hashlib,
                     'workload': json.loads((root / 'core/testdata/retrieval-quality.json').read_bytes())}
        exec(compile(ast.Module(body=body[start:stop], type_ignores=[]), 'frozen-comparator-case-builder', 'exec'), namespace)
        self.assertEqual(cases, namespace['cases'])

    def test_embedding_inventory_keeps_nine_queries_six_documents_and_exact_byte_limits(self):
        cases = semantic_fixture_cases.embedding_cases()
        self.assertEqual(len(cases), 15)
        self.assertEqual(sum(case['operation'] == 'query' for case in cases), 9)
        self.assertEqual(sum(case['operation'] == 'document' for case in cases), 6)
        self.assertTrue(all(set(case) == {'id', 'operation', 'text', 'expected'} for case in cases))
        self.assertTrue(all(case['expected'] == 'accepted' for case in cases))
        canonical = json.dumps(cases, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         'feb24e50fd6b37b7405afd8f972ec6bf57c086820db22d8ee96381c4f26e44a2')
        wire = [{key: case[key] for key in ('id', 'operation', 'text')} for case in cases]
        wire_bytes = json.dumps(wire, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        self.assertEqual(hashlib.sha256(wire_bytes).hexdigest(),
                         '6e1bafddc5a00b061ca56281845a09b58766cf4f97e53bcc0da15a2f350062cf')
        by_id = {case['id']: case for case in cases}
        self.assertEqual(len(by_id['query-4096-bytes']['text'].encode('utf-8')), 4096)
        for case_id in ('document-maximum-latin', 'document-maximum-cjk'):
            self.assertEqual(len(by_id[case_id]['text'].encode('utf-8')), 65536)
        self.assertEqual(by_id['query-exact-512']['text'], 'a ' * 502)
        self.assertEqual(by_id['query-over-512']['text'], 'a ' * 503)
        self.assertEqual(by_id['query-token-empty']['text'], '\u200b\u200d\ufeff')

    def test_case_mutation_cannot_change_other_requests_or_a_later_inventory(self):
        cases = semantic_fixture_cases.ranking_cases()
        first_body = cases[1]['notes'][0]['body']
        cases[0]['notes'][0]['body'] = 'changed by a consumer'
        self.assertEqual(cases[1]['notes'][0]['body'], first_body)
        self.assertEqual(semantic_fixture_cases.ranking_cases()[0]['notes'][0]['body'], first_body)

    def test_module_import_does_not_require_model_or_tokenizer_dependencies(self):
        script = """
import sys
class BlockModelImports:
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'numpy','fastembed','onnxruntime','tokenizers'}:
   raise ImportError('forbidden model dependency: '+fullname)
sys.meta_path.insert(0,BlockModelImports())
import semantic_fixture_cases
import capture_semantic_fixtures
assert len(semantic_fixture_cases.ranking_cases())==19
assert len(semantic_fixture_cases.embedding_cases())==15
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', script], capture_output=True,
                                text=True, env=dict(os.environ, PYTHONPATH=os.path.dirname(__file__)), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blank_and_overflow_failures_do_not_enter_the_positive_inventory(self):
        failures = semantic_fixture_cases.embedding_failure_cases()
        self.assertTrue(all(set(case) == {'id', 'operation', 'text', 'expected'} for case in failures))
        self.assertTrue(all(case['expected'] == 'refused' for case in failures))
        for codepoint in semantic_fixture_cases.PYTHON_WHITESPACE_CODEPOINTS:
            for operation in ('query', 'document'):
                self.assertTrue(any(case['operation'] == operation and case['text'] == chr(codepoint) for case in failures))
        by_id = {case['id']: case for case in failures}
        self.assertEqual(len(by_id['query-byte-overflow']['text'].encode()), 4097)
        self.assertEqual(len(by_id['document-byte-overflow']['text'].encode()), 65537)
        punctuation = semantic_fixture_cases.consumed_punctuation_case()
        self.assertEqual(punctuation['observed_direct_supervisor_disposition'], 'timeout')
        self.assertEqual(punctuation['real_go_disposition'], 'unmeasured')
        self.assertNotIn('expected', punctuation)
        self.assertNotIn('expected_disposition', punctuation)

    def test_wordpiece_boundary_requires_an_explicit_tokenizer_without_a_fallback(self):
        with self.assertRaisesRegex(ValueError, 'prepared tokenizer'):
            semantic_fixture_cases.wordpiece_boundary_case(None)
        # Manufactured tokenizer mechanics only. Actual WordPiece evidence must
        # come from the prepared tokenizer inside the reviewed isolation boundary.
        class Encoding:
            def __init__(self, tokens):
                self.tokens, self.ids = tokens, list(range(len(tokens)))

        class SyntheticTokenizer:
            def encode(self, text, add_special_tokens):
                return Encoding([piece for word in text.split()
                    for piece in (['un', '##affordable'] if word == 'unaffordable' else [word])])

        case = semantic_fixture_cases.wordpiece_boundary_case(SyntheticTokenizer())
        tokens = SyntheticTokenizer().encode(case['notes'][0]['body'], False).tokens
        self.assertEqual(tokens[320], '##affordable')
        self.assertGreater(len(tokens), 384)
        self.assertEqual(case['id'], 'wordpiece-continuation-at-320')
        self.assertEqual(len(semantic_fixture_cases.ranking_cases()), 19)


if __name__ == '__main__':
    unittest.main()
