"""Bound capture through the actual checked Source and Go process boundaries."""
import fcntl
import json
from pathlib import Path
import unittest

from test_f02_bound_memory import BoundMemoryCase
from test_f04_activation import SESSION, encoded, write


class BoundCaptureTests(BoundMemoryCase):
    def setUp(self):
        super().setUp()
        self.f.memory['capture_observations'] = True
        self.f.publish_configuration()
        self.event('PreCompact')

    def rollout(self, count=0):
        path = self.root / 'rollout.jsonl'
        rows = []
        for i in range(count):
            role = 'user' if i % 2 == 0 else 'assistant'
            rows.append(dict(type='response_item', payload=dict(type='message', role=role,
                        content=[dict(type='input_text' if role == 'user' else 'output_text',
                                      text='thanks' if role == 'user' else 'no problem')])))
        write(path, b''.join(encoded(row) + b'\n' for row in rows))
        return path

    def test_missing_transcript_commits_pending_without_native_activation(self):
        result = self.pair()
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'Cairn capture is pending', result.stdout)
        state = self.state()
        self.assertEqual(set(state), {SESSION + '.json'})
        self.assertEqual(state[SESSION + '.json']['last_capture']['outcome'], 'transcript_unavailable')
        self.assertEqual([row[0] for row in self.calls], ['capture-observation'])

    def test_empty_courtesy_and_duplicate_reuse_existing_capture_body(self):
        for count, outcome in ((0, 'empty'), (6, 'courtesy')):
            with self.subTest(messages=count):
                self.reset_state({})
                self.event('PreCompact', transcript_path=str(self.rollout(count)))
                self.assertEqual(self.pair().returncode, 0)
                self.assertEqual(self.calls[-1][1]['outcome'], outcome)
                if count:
                    self.assertEqual(self.pair().returncode, 0)
                    self.assertEqual(self.calls[-1][1]['outcome'], 'unchanged')

    def test_short_and_continuation_stop_preserve_source_early_returns(self):
        path = self.rollout(2)
        self.event('Stop', transcript_path=str(path))
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls[-1][1]['outcome'], 'too_little_dialogue')
        self.reset_state({})
        self.event('Stop', transcript_path=str(path), stop_hook_active=True)
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_capture_exclusion_does_not_read_missing_manifest(self):
        # F04 admission excludes capture before binding reads. This is a Source
        # boundary, rather than permission to reinterpret an enabled generation.
        parked = self.f.binding.with_name('unavailable-binding.json')
        self.f.binding.rename(parked)
        try:
            result = self.pair()
            self.assertEqual(result.returncode, 0)
            self.assertEqual([row[0] for row in self.calls], ['capture-observation'])
            self.assertNotIn(SESSION + '.memory-budget.json', self.state())
        finally:
            parked.rename(self.f.binding)

    def test_capture_lock_refuses_without_committing_a_status(self):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        lock = directory / (SESSION + '.capture.lock')
        for side in (self.source, self.go):
            with lock.open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = side()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'a capture is already running for this session', result.stderr)
                self.assertEqual(self.state(), {})
        self.assertEqual(self.report_locks, [{'.lock': True, '.capture.lock': False}] * 2)


if __name__ == '__main__':
    unittest.main()
