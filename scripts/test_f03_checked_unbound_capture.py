"""Checked unbound H150 consumers against the complete installed Source."""
import json
from pathlib import Path
import shutil
import unittest

from test_f02_bound_memory import BoundMemoryCase
import test_f03_bound_capture as bound_capture
import test_f03_capture_boundary as capture_fixture
from test_f04_activation import SESSION, encoded, sha, write
from test_r04_host_wiring import UnboundFixture


class UnboundSelection:
    def setUp(self):
        super().setUp()
        previous = self.f
        self.f = UnboundFixture(self.root / 'unbound-project', self.binary,
                                stager=self.stager, harness='codex', enabled=False)
        for config in (self.f.memory, self.f.coord):
            for key in ('cairn', 'socket', 'token_file'):
                config[key] = previous.memory[key]
        for key in ('claude', 'model', 'capture_observations', 'recall_observations'):
            if key in previous.memory:
                self.f.memory[key] = previous.memory[key]
        self.f.memory['capture_observations'] = True
        self.f.publish_configuration(enabled=False)
        self.f.event = dict(previous.event, cwd=str(self.f.root))
        self.f.raw = encoded(self.f.event)

    def go_vector(self, timeout=None):
        vector = super().go_vector(timeout)
        vector[1] = 'memory-capture'
        return vector


class CheckedUnboundCaptureTests(UnboundSelection, BoundMemoryCase):
    def setUp(self):
        super().setUp()
        self.event('PreCompact')

    rollout = bound_capture.BoundCaptureTests.rollout
    session_key = bound_capture.BoundCaptureTests.session_key
    after_run = bound_capture.BoundCaptureTests.after_run
    scheduled = bound_capture.BoundCaptureTests.scheduled

    def before_run(self, side):
        super().before_run(side)
        capture_fixture.CaptureBoundaryTests.start_scheduler(self, side)

    test_missing_transcript_commits_pending_without_native_activation = bound_capture.BoundCaptureTests.test_missing_transcript_commits_pending_without_native_activation
    test_empty_courtesy_and_duplicate_reuse_existing_capture_body = bound_capture.BoundCaptureTests.test_empty_courtesy_and_duplicate_reuse_existing_capture_body
    test_short_and_continuation_stop_preserve_source_early_returns = bound_capture.BoundCaptureTests.test_short_and_continuation_stop_preserve_source_early_returns
    test_capture_lock_refuses_without_committing_a_status = bound_capture.BoundCaptureTests.test_capture_lock_refuses_without_committing_a_status
    test_claude_capture_rows_keep_source_eligibility_and_no_activation = bound_capture.BoundCaptureTests.test_claude_capture_rows_keep_source_eligibility_and_no_activation
    test_changed_capture_fields_and_binding_refuse_cas_before_marker = bound_capture.BoundCaptureTests.test_changed_capture_fields_and_binding_refuse_cas_before_marker
    test_concurrent_recall_and_hints_survive_bound_capture_commit = bound_capture.BoundCaptureTests.test_concurrent_recall_and_hints_survive_bound_capture_commit
    test_failed_replace_cannot_advance_marker_and_next_capture_can_retry = bound_capture.BoundCaptureTests.test_failed_replace_cannot_advance_marker_and_next_capture_can_retry
    test_incorrect_capture_budget_refuses_before_body_or_reports = bound_capture.BoundCaptureTests.test_incorrect_capture_budget_refuses_before_body_or_reports

    def test_changed_receipt_refuses_before_body(self):
        self.overwrite(self.f.receipt_path, self.f.receipt_path.read_bytes() + b' ')
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_repinned_bound_profile_cannot_downgrade(self):
        config = dict(self.f.coord, inbox_recall_binding=str(self.f.binding))
        self.overwrite(self.f.coord_path, encoded(config))
        self.repin_profiles()
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})


class CheckedUnboundProviderCaptureTests(UnboundSelection, bound_capture.BoundProviderCaptureTests):
    pass


if __name__ == '__main__':
    unittest.main()
