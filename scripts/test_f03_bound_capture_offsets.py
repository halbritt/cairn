"""Proposed bound Codex H150 transcript-offset corpus; preparation only."""
import unittest

import test_f03_bound_capture as bound
import test_f03_capture_boundary as boundary


TEST_NAMES = (
    'test_short_stop_and_repeated_append_truncate_keep_exact_offsets',
    'test_append_after_snapshot_remains_for_later_stop',
)


class BoundCaptureOffsetTests(bound.BoundCaptureTests):
    # Keep the original default-spaced JSONL bytes, event construction,
    # courtesy dialogue, assertions and external syscall schedule unchanged.
    configure = boundary.CaptureBoundaryTests.configure
    rollout = boundary.CaptureBoundaryTests.rollout
    courtesy_messages = boundary.CaptureBoundaryTests.courtesy_messages
    outcome = boundary.CaptureBoundaryTests.outcome
    scheduled = boundary.CaptureBoundaryTests.scheduled
    state = boundary.CaptureBoundaryTests.state

    def before_run(self, side):
        if side == 'go':
            if getattr(self, 'before_candidate', None):
                self.before_candidate()
            # The original oracle calls this candidate_prefix. The existing
            # bound launcher calls the same external prefix go_prefix.
            self.go_prefix = getattr(self, 'candidate_prefix', ())
        super().before_run(side)

    test_short_stop_and_repeated_append_truncate_keep_exact_offsets = boundary.CaptureBoundaryTests.test_short_stop_and_repeated_append_truncate_keep_exact_offsets
    test_append_after_snapshot_remains_for_later_stop = boundary.CaptureBoundaryTests.test_append_after_snapshot_remains_for_later_stop


def load_tests(loader, tests, pattern):
    # Inherited old methods remain available as helpers, without rerunning them.
    return unittest.TestSuite(BoundCaptureOffsetTests(name) for name in TEST_NAMES)


if __name__ == '__main__':
    unittest.main()
