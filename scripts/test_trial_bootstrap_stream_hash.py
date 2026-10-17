"""Bootstrap digests preserve canonical bytes without a whole encoded copy."""
import hashlib
import json
import unittest
from unittest.mock import patch

import trial_native_bootstrap as bootstrap
import trial_sdk_incoming as incoming


class BootstrapHashTests(unittest.TestCase):
    def test_canonical_hash_matches_reference_with_bounded_chunks(self):
        documents = [
            {'a': [None, True, False, 2**63, -2**63], 'z': 'café 😀'},
            {str(n): '\x01' * 4096 for n in range(15)},
            ['--argument', '雪' * 4096, {'nested': ['quotes"', 'slash\\', '\n']}],
        ]
        original = incoming.canonical_chunks
        for document in documents:
            with self.subTest(size=len(str(document))):
                expected = hashlib.sha256(json.dumps(document, sort_keys=True,
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                offers = []
                def chunks(value):
                    for chunk in original(value):
                        offers.append(len(chunk))
                        yield chunk
                with patch.object(incoming, 'canonical_chunks', side_effect=chunks):
                    actual = bootstrap.canonical_sha(document)
                self.assertEqual(actual, expected)
                self.assertTrue(offers, 'digest must use the bounded encoder')
                self.assertLessEqual(max(offers), 8192)


if __name__ == '__main__':
    unittest.main()
