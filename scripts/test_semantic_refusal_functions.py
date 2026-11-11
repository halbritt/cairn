"""Source-function controls; no actual model initialization or inference."""
import unittest
import tempfile
from pathlib import Path

import semantic_refusal_functions as functions


class ResidualFunctionTest(unittest.TestCase):
    def test_rank_and_retreat_guards_have_actual_exceptions_and_no_result(self):
        rows=functions.rank_and_retreat()
        expected={
            'F-RANK/decoded-chunk-token-overflow/boundary':'decoded chunk exceeds model input limit',
            'F-RANK/nonfinite-inference/NaN':'invalid model embeddings',
            'F-RANK/nonfinite-inference/positive-infinity':'invalid model embeddings',
            'F-RANK/nonfinite-inference/negative-infinity':'invalid model embeddings',
            'F-RANK/zero-inference/boundary':'empty model embedding',
            'F-EMBED/long-query-retreat-refusal/boundary':'query prefix exceeds token bound',
        }
        self.assertEqual({r['id'] for r in rows},set(expected))
        for row in rows:
            self.assertEqual(row['exception'],dict(type='ValueError',message=expected[row['id']]))
            self.assertIsNone(row['result'])
            self.assertFalse(row['model_initialized'])
            self.assertFalse(row['real_model_inference'])
        self.assertEqual(rows[0]['model_double_calls'],0)
        self.assertEqual(rows[-1]['tokenizer_double_calls'],9)
        self.assertEqual(rows[-1]['model_double_calls'],0)

    def test_failed_rank_inference_cannot_publish_partial_cache(self):
        for row in functions.rank_and_retreat()[:-1]:
            self.assertEqual(row['cache_before'],row['cache_after'])
            if 'nonfinite-inference/' in row['id'] or 'zero-inference/' in row['id']:
                self.assertEqual(row['model_double_calls'],1)

    def test_source_derivation_changes_and_stable_boundaries_keep_model_stub_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);assets=root/'assets';assets.mkdir()
            for name in functions.ASSETS:
                (assets/name).write_bytes(('hash-only unit asset '+name).encode())
                (assets/name).chmod(0o400)
            rows=functions.identity_cases(root/'controls',assets)
            self.assertEqual(len(rows),18)
            for row in rows:
                self.assertFalse(row['model_initialized'])
                self.assertFalse(row['real_model_inference'])
                before=row['before']['identities'];after=row['after']['identities']
                if row['id'].endswith('/fastembed-version'):
                    self.assertIsNone(after)
                    self.assertEqual(row['after']['exception'],dict(type='ValueError',message='worker requires fastembed==0.8.0'))
                    self.assertEqual(row['after']['external_constructor_stub_calls'],[])
                elif '/path-move-stable/' in row['id'] or '/timestamp-stable/' in row['id']:
                    self.assertEqual(before,after)
                    for step in row['steps']:self.assertEqual(before,step['result']['identities'])
                elif '/each-implementation-source/' in row['id']:
                    self.assertEqual(before['rank'],after['rank'])
                    self.assertNotEqual(before['embed'],after['embed'])
                else:
                    self.assertNotEqual(before['rank'],after['rank'])
                    self.assertNotEqual(before['embed'],after['embed'])
                if row['input']['mutation']['kind'] in ('one-byte-asset','one-source-comment'):
                    change=row['input']['mutation']
                    self.assertEqual([name for name in change['before'] if change['before'][name]!=change['after'][name]],
                                     [change['file']])
                    self.assertTrue((root/'controls'/change['retained_before']).is_file())
                    self.assertTrue((root/'controls'/change['retained_after']).is_file())
                if '/timestamp-stable/' in row['id']:
                    self.assertEqual(len(row['steps']),7)
                    for step in row['steps']:
                        self.assertEqual(step['before_sha256'],step['after_sha256'])
                        self.assertNotEqual(step['before_mtime_ns'],step['after_mtime_ns'])


if __name__=='__main__':
    unittest.main()
