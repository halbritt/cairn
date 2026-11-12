"""Actual installed Source and eight-flag checked Go Claude wake callbacks.

This exercises the checked memory entry directly; it does not change or claim
the hook-settings producer's selected Claude prompt route.
"""
import fcntl
import json
from pathlib import Path
import unittest

from test_f02_bound_memory import BoundMemoryCase
from test_f04_activation import SESSION, TURN, sha, write


class ClaudeWakeStateTests(BoundMemoryCase):
    def setUp(self):
        super().setUp()
        for config in (self.f.memory, self.f.coord):
            config['harness'] = 'claude'
        self.f.memory['recall_mode'] = 'agent_tools'
        self.provider_log = self.root / 'unexpected-provider.calls'
        provider = self.root / 'provider'
        write(provider, b'#!/usr/bin/python3\nimport pathlib\n'
              b'pathlib.Path(__file__).with_name("unexpected-provider.calls").touch()\n'
              b'raise SystemExit(55)\n', 0o700)
        self.f.memory['claude'] = str(provider)
        self.f.publish_configuration(enabled=True)
        self.trace = self.f.memory_path.parent / 'invocation-trace' / 'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700)
        self.assertIs(json.loads(self.f.binding.read_bytes())['enabled'], True)
        self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                   prompt_id='native-claude-wake-prompt', prompt=self.f.wake())
        self.observed_side = None
        self.observations = {}

    def before_run(self, side):
        self.observed_side = side
        self.trace_offset = len(self.trace_rows())

    def trace_rows(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def after_run(self):
        directory = Path(self.f.memory['state_dir'])
        files = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
        locks = {}
        for name in files:
            if name.endswith('.lock'):
                with (directory / name).open('r+') as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locks[name] = True
                    fcntl.flock(stream, fcntl.LOCK_UN)
        self.observations[self.observed_side] = dict(
            files=files, locks=locks, trace=self.trace_rows()[self.trace_offset:])
        print('CLAUDE_WAKE_STATE ' + json.dumps(dict(
            side=self.observed_side, keys=sorted(self.state()),
            files=files, released_locks=locks,
            api=[row[0] for row in self.calls],
            provider_called=self.provider_log.exists())), flush=True)

    def paired_wake(self):
        # Keep the parent's raw state-key assertion and all stream/API checks.
        result = BoundMemoryCase.pair(self)
        source, go = (self.observations[side] for side in ('source', 'go'))
        self.assertEqual(go['files'], source['files'])
        self.assertEqual(go['locks'], source['locks'])
        for observation in (source, go):
            self.assertEqual([row['kind'] for row in observation['trace']], ['entry', 'parsed', 'exit'])

        def stable(row):
            row = dict(row)
            invocation = row.pop('inv')
            self.assertEqual(len(invocation), 32)
            self.assertGreater(int(invocation, 16), 0)
            for key in ('at', 'duration_ms', 'pid', 'ppid'):
                if key in row:
                    value = row.pop(key)
                    self.assertIs(type(value), int)
                    self.assertGreaterEqual(value, 0)
                    if key in ('pid', 'ppid'):
                        self.assertGreater(value, 1)
            return row

        self.assertEqual([stable(row) for row in go['trace']],
                         [stable(row) for row in source['trace']])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.provider_log.exists())
        return result

    def test_checked_reserved_wake_preserves_exact_source_parent_keys(self):
        print('CLAUDE_WAKE_PINS ' + json.dumps(dict(
            receipt_sha256=self.f.receipt_sha,
            image_sha256=sha(self.f.binary.read_bytes()),
            source_members=self.f.receipt['members'])), flush=True)
        result = self.paired_wake()
        self.assertEqual(self.state().keys(), {
            SESSION + '.inbox-recall.initialized', SESSION + '.inbox-recall.json'})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.provider_log.exists())
        first = self.state()
        result = self.paired_wake()
        self.assertEqual(self.state(), first)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.provider_log.exists())


    def test_privacy_and_account_exclusions_precede_binding_access(self):
        for kind in ('privacy', 'account'):
            with self.subTest(exclusion=kind):
                self.reset_state({})
                original_env = self.f.env
                marker = self.f.root / '.cairn-no-memory'
                parked = self.f.binding.with_name('parked-binding.json')
                if kind == 'privacy':
                    write(marker, b'')
                else:
                    self.f.env = lambda: dict(original_env(), CLAUDE_CONFIG_DIR=str(self.root / 'other-account'))
                self.f.binding.rename(parked)
                try:
                    self.paired_wake()
                    self.assertEqual(self.state(), {})
                finally:
                    parked.rename(self.f.binding)
                    marker.unlink(missing_ok=True)
                    self.f.env = original_env


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ClaudeWakeStateTests(name) for name in
                             ClaudeWakeStateTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
