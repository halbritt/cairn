"""Bootstrap digests preserve canonical bytes without a whole encoded copy."""
import hashlib
import json
import unittest
from unittest.mock import patch

import trial_native_bootstrap as bootstrap
import trial_sdk_incoming as incoming


class BootstrapHashTests(unittest.TestCase):
    def test_canonical_hash_matches_reference_with_bounded_encoded_pieces(self):
        documents = [
            {'a': [None, True, False, 2**63, -2**63], 'z': 'café 😀'},
            {str(n): '\x01' * 4096 for n in range(15)},
            ['--argument', '雪' * 4096, {'nested': ['quotes"', 'slash\\', '\n']}],
        ]
        original = incoming._pieces
        for document in documents:
            with self.subTest(size=len(str(document))):
                expected = hashlib.sha256(json.dumps(document, sort_keys=True,
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                offers = []
                def pieces(value):
                    for piece in original(value):
                        offers.append(len(piece.encode('utf-8')))
                        yield piece
                with patch.object(incoming, '_pieces', side_effect=pieces):
                    actual = bootstrap.canonical_sha(document)
                self.assertEqual(actual, expected)
                self.assertTrue(offers, 'digest must consume the canonical traversal')
                self.assertLessEqual(max(offers), 8190)


if __name__ == '__main__':
    unittest.main()
