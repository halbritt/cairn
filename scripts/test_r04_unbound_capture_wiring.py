"""Real R04 staged capture commands, retained Source, and confirmed API writes."""
import json
import shlex
import unittest
from pathlib import Path

from test_f02_bound_memory import BoundMemoryCase
from test_f03_checked_unbound_capture import UnboundSelection
import test_f03_checked_unbound_capture as unbound
import test_r04_bound_wiring as bound


class R04UnboundCaptureWiringTests(UnboundSelection, BoundMemoryCase):
    staged_process = bound.R04BoundWiringTests.staged_process
    source = bound.R04BoundWiringTests.source
    go = bound.R04BoundWiringTests.go
    prepare_provider = bound.R04BoundWiringTests.prepare_provider
    before_run = bound.R04BoundWiringTests.before_run
    create_reply = bound.R04BoundWiringTests.create_reply
    after_run = bound.R04BoundWiringTests.after_run
    capture_pairs = bound.R04BoundWiringTests.capture_pairs

    def stage_rows(self, harness='codex', mode='agent_tools', enabled=False, remove_package=True):
        metadata = bound.R04BoundWiringTests.stage_rows(
            self, harness, mode, enabled, remove_package)
        expected = {'PreCompact', 'Stop' if harness == 'codex' else 'SessionEnd'}
        actual = {name for name, row in self.rows.items()
                  if row['reason'] == 'receipt-checked unbound Go capture'}
        self.assertEqual(actual, expected)
        for event in expected:
            row = self.rows[event]
            self.assertEqual(row['implementation'], 'go')
            self.assertEqual(row['hook']['timeout'], 150)
            self.assertNotEqual(row['checked_launcher'], row['retained_launcher'])
            vector = shlex.split(Path(row['argv'][0]).read_text().splitlines()[1])
            self.assertEqual(vector[:3], ['exec', str(self.f.binary), 'memory-capture'])
            self.assertEqual(vector[vector.index('--role') + 1], 'memory')
            self.assertEqual(vector[vector.index('--hook-timeout') + 1], '150')
            self.assertEqual(vector[-2:], ['--', '$@'])
        if harness == 'codex':
            self.assertIs(self.rows['Stop']['hook']['async'], True)
        print('R04_UNBOUND_CAPTURE_SELECTION ' + json.dumps({
            'test': self.id(), 'harness': harness,
            'go_sha256': self.f.receipt['binary']['sha256'],
            'receipt_sha256': self.f.receipt_sha,
            'capture_events': sorted(expected),
        }, sort_keys=True), flush=True)
        return metadata

    def test_codex_staged_capture_writes_and_commits_after_package_removal(self):
        self.capture_pairs('codex')

    def test_claude_staged_capture_writes_and_commits_after_package_removal(self):
        self.capture_pairs('claude')

    def test_opencode_staged_capture_writes_and_commits_after_package_removal(self):
        self.capture_pairs('opencode')

    def test_changed_capture_selection_refuses_before_effects(self):
        bound.R04BoundWiringTests.refuse_changed_code(self, 'codex')

    def test_staged_capture_keeps_account_privacy_and_disable_guards(self):
        self.stage_rows()
        self.event('PreCompact')
        unbound.CheckedUnboundCaptureTests.test_account_privacy_and_disable_guards_have_no_body_effects(self)

    def test_opencode_capture_rejects_a_non_native_session_shape(self):
        self.stage_rows('opencode')
        self.event('PreCompact', messages=[{'role': 'user', 'text': 'Keep this decision.'}])
        self.assertFalse(self.f.event['session_id'].startswith('ses_'))
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, b'', b'Cairn lifecycle diagnostic: phase=handle error_class=HookError\n'
                          b'Cairn lifecycle: invalid OpenCode session identity; '
                          b'use native tools or an explicit handoff.\n'))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})


if __name__ == '__main__':
    unittest.main()
