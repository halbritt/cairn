"""Strict end/source/file binding using constructed process declarations only."""
import dataclasses
import hashlib
import json
import unittest
from unittest.mock import patch

from selected_native.retention import ExitSnapshot, RetentionError
from trial_worker_protocol import encoded
import test_trial_attempt_retention as fixture
from test_trial_coding_stream import composer
from test_trial_attempt_actions import SESSION, init, event, wire


class AttemptEndTests(unittest.TestCase):
    setUp = fixture.AttemptRetentionTests.setUp
    stream = fixture.AttemptRetentionTests.stream
    def end(self, source, code=17):
        wait = dict(pid=1, start_ticks=1, boot_id=SESSION, raw_status=code<<8, returncode=code)
        capture = dict(wait, raw_status=0, returncode=0)
        return dict(schema='cairn.worker-end-observation/1', run_id=SESSION,
                    source_manifest_sha256='d'*64, stream_sha256=source.hash.hexdigest(),
                    stream_bytes=source.total, observed_stream_sha256=source.hash.hexdigest(),
                    observed_stream_bytes=source.total, actor_wait=wait, capture_wait=capture,
                    capture_released=True, input_bytes=7, input_bytes_written=7,
                    observations=[], capture_cleanup_observations=[])

    def success(self):
        source = composer()
        final = dict.fromkeys(source.profile.final_required_keys, None)
        source.feed(wire(init())+wire(event('result', subtype='success', is_error=False,
            result=json.dumps(final), permission_denials=[], num_turns=1, total_cost_usd=.1,
            modelUsage={'fixture-model':dict(provider='firstParty', inputTokens=1,
                outputTokens=2, cacheReadInputTokens=0, cacheCreationInputTokens=0)})), 0)
        # Fixture shape testimony; the fixed worker must use its actual E observation.
        receipt = self.retention.prepare(source.reader, ExitSnapshot(1, 1, SESSION, 0, False))
        return source, receipt

    def test_end_and_failed_stream_are_retained_with_distinct_actuality_basis(self):
        source = self.stream();end = self.end(source)
        receipt = self.writer.prepare_with_end(source, [], encoded(end))
        saved = json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertEqual(saved['end_observation'], end)
        self.assertIsNone(saved['selected_success'])
        self.assertFalse(saved['standalone_file_is_closure'])
        self.assertEqual(receipt.manifest_sha256,
                         hashlib.sha256((self.run/'ATTEMPT-EVIDENCE.json').read_bytes()).hexdigest())

    def test_end_binding_fault_prevents_output_and_consumes_writer(self):
        for field, value in (('source_manifest_sha256', 'e'*64), ('stream_sha256', 'e'*64),
                             ('private_payload', 'private'), ('stream_bytes', 0)):
            with self.subTest(field=field):
                source = self.stream();end = {**self.end(source), field:value}
                from trial_attempt_retention import AttemptRetention
                writer = AttemptRetention(self.retention)
                try:
                    with self.assertRaises(RetentionError):
                        writer.prepare_with_end(source, [], encoded(end))
                    self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
                    with self.assertRaisesRegex(RetentionError, 'attempt_writer_unavailable'):
                        writer.prepare_with_end(self.stream(), [], encoded(self.end(source)))
                finally:
                    writer.close()

    def test_success_observations_bind_prepared_file_without_repeating_reader_finish(self):
        source, selected = self.success();end = self.end(source, code=0)
        receipt = self.writer.prepare_success_observations(source, [], encoded(end), selected)
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        saved = json.loads((self.run/'OBSERVATIONS.json').read_bytes())
        self.assertEqual(saved['selected_success'], dataclasses.asdict(selected))
        self.assertEqual(saved['end_observation'], end)
        self.assertFalse(saved['task_acceptance'])
        self.assertFalse(receipt.return_receipt)

    def test_forged_success_receipt_cannot_replace_actual_selected_file_hash(self):
        source, selected = self.success()
        forged = dataclasses.replace(selected, selected_sha256='0'*64)
        with self.assertRaisesRegex(RetentionError, 'attempt_success_readback'):
            self.writer.prepare_success_observations(source, [], encoded(self.end(source, 0)), forged)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())

    def test_nonzero_end_cannot_be_attached_as_consistent_success(self):
        source, selected = self.success()
        with self.assertRaisesRegex(RetentionError, 'attempt_success_exit_binding'):
            self.writer.prepare_success_observations(source, [], encoded(self.end(source, 17)), selected)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())

    def test_zero_exit_identity_must_match_the_actual_prepared_snapshot(self):
        source, selected = self.success(); end = self.end(source, 0)
        end['actor_wait']['pid'] = 2
        with self.assertRaisesRegex(RetentionError, 'attempt_success_exit_binding'):
            self.writer.prepare_success_observations(source, [], encoded(end), selected)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())

    def test_prepared_marker_receipt_drift_refuses_observation_output(self):
        source, selected = self.success()
        marker = self.run/'PREPARED.json'
        value = json.loads(marker.read_bytes())
        value['selected_sha256'] = '0'*64
        marker.write_bytes(encoded(value))
        with self.assertRaisesRegex(RetentionError, 'attempt_success_terminal_binding'):
            self.writer.prepare_success_observations(source, [], encoded(self.end(source, 0)), selected)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())

    def test_success_observation_fsync_fault_preserves_selected_and_consumes_writer(self):
        source, selected = self.success()
        original_selected = (self.run/'SELECTED.json').read_bytes()
        with patch('trial_attempt_retention.os.fsync', side_effect=OSError('private fixture error')):
            with self.assertRaisesRegex(RetentionError, '^attempt_retention_io$'):
                self.writer.prepare_success_observations(source, [], encoded(self.end(source, 0)), selected)
        self.assertEqual((self.run/'SELECTED.json').read_bytes(), original_selected)
        self.assertTrue((self.run/'OBSERVATIONS.json').exists())
        self.assertEqual(self.writer.state, 'failed')
        self.assertTrue(source.closed)
        with self.assertRaisesRegex(RetentionError, 'attempt_writer_unavailable'):
            self.writer.prepare_with_end(self.stream(), [], encoded(self.end(source)))
        with self.assertRaises(RetentionError):
            self.retention.prepare(source.reader, ExitSnapshot(1, 1, SESSION, 0, False))

    def test_selected_symlink_is_refused_before_observation_output(self):
        source, selected = self.success()
        stored = self.run/'SELECTED.json'
        outside = self.root/'outside-selected'
        stored.rename(outside)
        stored.symlink_to(outside)
        with self.assertRaisesRegex(RetentionError, '^attempt_retention_io$'):
            self.writer.prepare_success_observations(source, [], encoded(self.end(source, 0)), selected)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())
        self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), selected.selected_sha256)

    def test_failure_retains_explicit_unknown_end_without_promoting_success(self):
        source = self.stream(); end = self.end(source)
        end.update(actor_wait=None, input_bytes_written=None,
                   observations=['actor_wait_unknown', 'input_delivery_incomplete'])
        self.writer.prepare_with_end(source, [], encoded(end))
        saved = json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertEqual(saved['end_observation'], end)
        self.assertFalse(saved['validity_decided'])
        self.assertFalse(saved['success_claimed'])

    def test_incomplete_delivery_refuses_success_even_with_known_zero_waits(self):
        source, selected = self.success(); end = self.end(source, 0)
        end.update(input_bytes_written=6, observations=['input_delivery_incomplete'])
        with self.assertRaisesRegex(RetentionError, 'attempt_success_end_conflict'):
            self.writer.prepare_success_observations(source, [], encoded(end), selected)
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())


if __name__ == '__main__':
    unittest.main()
