"""Pure evidence-report regression; no host fixture or process is launched."""
import hashlib
import json
import unittest

import test_trial_sdk_command_service_wire as transcript_fixture
from trial_sdk_command_service_host_cases import reported_cli_facts


class ReportTests(unittest.TestCase):
    def terminal(self):
        fixture = transcript_fixture.WireTests(
            'test_complete_transcript_preserves_nonzero_cli_report_and_release')
        fixture.setUp()
        fixture.terminal()
        fixture.emit(transcript_fixture.w.RELEASE, 1, 10)
        fixture.emit(transcript_fixture.w.RELEASED, 1, 10, 0)
        self.assertEqual(fixture.root.leased, 0)
        return fixture.state.snapshot()

    def test_real_terminal_transcript_is_json_and_preserves_reported_counts(self):
        state = self.terminal()
        with self.assertRaises(TypeError):
            json.dumps(state, allow_nan=False)
        report = reported_cli_facts(state)
        decoded = json.loads(json.dumps(report, allow_nan=False))
        self.assertEqual(decoded['returncode'], 17)
        self.assertEqual(decoded['cli_wait_status'], 17 << 8)
        self.assertEqual(decoded['phase'], 'idle')
        self.assertEqual(decoded['raw_counts'], [0, 2, 1])
        self.assertEqual(decoded['wire_counts'], [2, 3])
        sha = lambda body: hashlib.sha256(body).hexdigest()
        self.assertEqual(decoded['request_sha256'], sha(b'{}'))
        self.assertEqual(decoded['raw_sha256'], [sha(b''), sha(b'ok'), sha(b'?')])
        self.assertEqual(decoded['text_sha256'], [sha(b'ok'), sha('�'.encode())])
        self.assertIsNot(report, state)
        self.assertIs(type(state['request_sha256']), bytes)
        self.assertTrue(all(type(value) is bytes for value in state['raw_sha256']))
        self.assertTrue(all(type(value) is bytes for value in state['text_sha256']))

    def test_unpopulated_digests_remain_none(self):
        state = transcript_fixture.w.ServiceTranscript().snapshot()
        report = json.loads(json.dumps(reported_cli_facts(state), allow_nan=False))
        for key in ('request_sha256', 'raw_sha256', 'text_sha256'):
            self.assertIsNone(report[key])

    def test_malformed_known_digest_fields_refuse(self):
        original = self.terminal()
        invalid = {
            'request_sha256': (b'x', b'x' * 33, '0' * 64, bytearray(32)),
            'raw_sha256': ((b'x' * 32,), (b'x' * 32,) * 4,
                           (b'x' * 32, b'x', b'x' * 32), [b'x' * 32] * 3),
            'text_sha256': ((b'x' * 32,), (b'x' * 32, '0' * 64)),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value_type=type(value).__name__):
                    state = dict(original, **{key: value})
                    with self.assertRaisesRegex(ValueError, '^fixture_digest_shape$'):
                        reported_cli_facts(state)
                    self.assertIs(state[key], value)

    def test_unexpected_bytes_are_not_silently_coerced(self):
        state = self.terminal()
        state['unexpected'] = b'not a selected digest'
        with self.assertRaises(TypeError):
            json.dumps(reported_cli_facts(state), allow_nan=False)


if __name__ == '__main__':
    unittest.main()
