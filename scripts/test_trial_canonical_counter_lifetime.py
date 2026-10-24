"""Observe actual counting-consumer aliases during canonical generator yields."""
import hashlib
import json
import sys
import unittest

from trial_sdk_incoming import _pieces, canonical_size_sha256


class CanonicalCounterLifetime(unittest.TestCase):
    def test_previous_encoded_piece_is_gone_when_traversal_yields_successor(self):
        value = {'x': 'a' * 81900, 'unicode': 'é漢😀'}
        expected = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
        observations = []
        def trace(frame, event, arg):
            if frame.f_code is _pieces.__code__ and event == 'return' and type(arg) is str:
                consumer = frame.f_back
                if consumer is not None and consumer.f_code is canonical_size_sha256.__code__:
                    previous = consumer.f_locals.get('encoded')
                    observations.append(previous)
            return trace
        previous_trace = sys.gettrace()
        try:
            sys.settrace(trace)
            result = canonical_size_sha256(value)
        finally:
            sys.settrace(previous_trace)
        self.assertEqual(result, (len(expected), hashlib.sha256(expected).hexdigest()))
        self.assertGreater(len(observations), 8)
        self.assertEqual(observations, [None] * len(observations))


if __name__ == '__main__':
    unittest.main()
