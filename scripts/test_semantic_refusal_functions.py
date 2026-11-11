"""Source-function controls; no actual model initialization or inference."""
import unittest

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


if __name__=='__main__':
    unittest.main()
