"""Real disposable local file effects; constructed streams, no provider/campaign."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from selected_native.retention import Intent, Retention, RetentionError, ExitSnapshot
from trial_attempt_retention import AttemptRetention
from test_trial_coding_stream import composer
from test_trial_attempt_actions import SESSION, PRIVATE, init, result, event, wire


class AttemptRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.addCleanup(os.close, self.root_fd)
        self.retention = Retention.create(self.root_fd, Intent(SESSION, 'a'*64, 'b'*64, 'c'*64, 'd'*64))
        self.addCleanup(self.retention.close)
        self.writer = AttemptRetention(self.retention)
        self.addCleanup(self.writer.close)
        self.run = self.root/SESSION

    def stream(self):
        stream = composer()
        stream.feed(wire(init())+wire(result()), 1)
        return stream

    def test_selected_failed_attempt_is_private_read_back_and_not_success_receipt(self):
        receipt = self.writer.prepare(self.stream(), [])
        self.writer.close()
        self.retention.close()
        saved = self.run/'ATTEMPT-EVIDENCE.json'
        raw = saved.read_bytes()
        selected = json.loads(raw)
        self.assertEqual(receipt.manifest_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt.selected_bytes, len(raw))
        self.assertEqual(receipt.intent_sha256, hashlib.sha256((self.run/'INTENT.json').read_bytes()).hexdigest())
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertEqual(selected['stream']['success_reader']['refusal'], 'native_result_failed')
        self.assertNotIn(PRIVATE.encode(), raw)
        self.assertFalse(receipt.return_receipt)
        self.assertFalse(selected['standalone_file_is_closure'])
        self.assertFalse(selected['task_acceptance'])
        self.assertEqual(sorted(p.name for p in self.run.iterdir()), ['ATTEMPT-EVIDENCE.json', 'INTENT.json'])

    def test_duplicate_descriptor_survives_original_close_without_reopening_path(self):
        self.retention.close()
        self.writer.prepare(self.stream(), [])
        self.assertTrue((self.run/'ATTEMPT-EVIDENCE.json').is_file())

    def test_moved_directory_keeps_owned_identity(self):
        moved = self.root/'moved'
        self.run.rename(moved)
        self.writer.prepare(self.stream(), [])
        self.assertTrue((moved/'ATTEMPT-EVIDENCE.json').is_file())
        self.assertFalse(self.run.exists())

    def test_wrong_run_or_raw_dictionary_refuses_without_output(self):
        for source in ({'private':PRIVATE}, composer(session_id='00000000-0000-4000-8000-000000000002')):
            with self.subTest(source_type=type(source).__name__):
                writer = AttemptRetention(self.retention)
                try:
                    with self.assertRaises(RetentionError):
                        writer.prepare(source, [])
                    self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
                    with self.assertRaisesRegex(RetentionError, 'attempt_writer_unavailable'):
                        writer.prepare(self.stream(), [])
                finally:
                    writer.close()

    def test_existing_leaf_or_symlink_is_consumed_never_overwritten(self):
        destination = self.run/'ATTEMPT-EVIDENCE.json'
        private = self.root/'private'; private.write_text(PRIVATE)
        destination.symlink_to(private)
        with self.assertRaisesRegex(RetentionError, 'attempt_retention_io'):
            self.writer.prepare(self.stream(), [])
        self.assertTrue(destination.is_symlink())
        self.assertEqual(private.read_text(), PRIVATE)
        with self.assertRaisesRegex(RetentionError, 'attempt_writer_unavailable'):
            self.writer.prepare(self.stream(), [])

    def test_fsync_failure_leaves_consumed_file_and_no_success_claim(self):
        with patch('trial_attempt_retention.os.fsync', side_effect=OSError(PRIVATE)):
            with self.assertRaisesRegex(RetentionError, '^attempt_retention_io$'):
                self.writer.prepare(self.stream(), [])
        self.assertTrue((self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertEqual(self.writer.state, 'failed')
        with self.assertRaisesRegex(RetentionError, 'attempt_writer_unavailable'):
            self.writer.prepare(self.stream(), [])

    def test_directory_privacy_loss_refuses(self):
        self.run.chmod(0o755)
        with self.assertRaisesRegex(RetentionError, 'attempt_directory_custody'):
            self.writer.prepare(self.stream(), [])
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())

    def test_readback_detects_link_added_after_write(self):
        original = os.fsync
        linked = False
        def tamper(fd):
            nonlocal linked
            original(fd)
            destination = self.run/'ATTEMPT-EVIDENCE.json'
            if destination.exists() and not linked:
                os.link(destination, self.root/'alias')
                linked = True
        with patch('trial_attempt_retention.os.fsync', side_effect=tamper):
            with self.assertRaisesRegex(RetentionError, 'attempt_readback_custody'):
                self.writer.prepare(self.stream(), [])
        self.assertEqual(self.writer.state, 'failed')

    def test_readback_detects_replacement_inode_with_equal_bytes(self):
        original = os.fsync
        replaced = False
        def tamper(fd):
            nonlocal replaced
            original(fd)
            destination = self.run/'ATTEMPT-EVIDENCE.json'
            if destination.exists() and not replaced:
                replacement = self.run/'replacement'
                replacement.write_bytes(destination.read_bytes()); replacement.chmod(0o600)
                replacement.replace(destination)
                replaced = True
        with patch('trial_attempt_retention.os.fsync', side_effect=tamper):
            with self.assertRaisesRegex(RetentionError, 'attempt_readback_identity'):
                self.writer.prepare(self.stream(), [])

    def test_close_fault_does_not_claim_closed_and_never_retries_descriptor(self):
        descriptor = self.writer.fd
        original = os.close
        def fail(fd):
            if fd == descriptor:
                original(fd)
                raise OSError(PRIVATE)
            return original(fd)
        with patch('trial_attempt_retention.os.close', side_effect=fail):
            with self.assertRaisesRegex(RetentionError, '^attempt_close_io$'):
                self.writer.close()
        self.assertTrue(self.writer.close_unknown)
        self.assertIsNone(self.writer.fd)
        self.writer.close()
        os.fstat(self.retention.fd)

    def test_interrupt_poisons_writer_and_is_reraised(self):
        with patch('trial_attempt_retention.encode', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.writer.prepare(self.stream(), [])
        self.assertEqual(self.writer.state, 'failed')
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())

    def test_constructor_interrupt_closes_only_its_new_duplicate(self):
        original = os.dup
        duplicates = []
        def duplicate(fd):
            value = original(fd); duplicates.append(value); return value
        with patch('trial_attempt_retention.os.dup', side_effect=duplicate), \
             patch.object(AttemptRetention, '_check_directory', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                AttemptRetention(self.retention)
        self.assertEqual(len(duplicates), 1)
        with self.assertRaises(OSError):
            os.fstat(duplicates[0])
        os.fstat(self.retention.fd)
        os.fstat(self.writer.fd)

    def test_equal_size_readback_tampering_refuses(self):
        original = os.fsync
        tampered = False
        def tamper(fd):
            nonlocal tampered
            original(fd)
            destination = self.run/'ATTEMPT-EVIDENCE.json'
            if destination.exists() and not tampered:
                raw = destination.read_bytes()
                destination.write_bytes(raw[:-1]+b'!')
                tampered = True
        with patch('trial_attempt_retention.os.fsync', side_effect=tamper):
            with self.assertRaisesRegex(RetentionError, 'attempt_readback_mismatch'):
                self.writer.prepare(self.stream(), [])

    def test_constructed_success_retention_prevents_failure_channel_preparation(self):
        source = composer()
        final = dict.fromkeys(source.profile.final_required_keys, None)
        terminal = event('result', subtype='success', is_error=False, result=json.dumps(final),
                         permission_denials=[], num_turns=1, total_cost_usd=.1,
                         modelUsage={'fixture-model':dict(provider='firstParty', inputTokens=1,
                             outputTokens=2, cacheReadInputTokens=0, cacheCreationInputTokens=0)})
        source.feed(wire(init())+wire(terminal), 0)
        # Shape-only fixture testimony; no process exit observation is claimed.
        self.retention.prepare(source.reader, ExitSnapshot(1, 1, SESSION, 0, False))
        with self.assertRaisesRegex(RetentionError, 'attempt_success_already_prepared'):
            self.writer.prepare(source, [])
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertTrue((self.run/'SELECTED.json').is_file())
        self.assertTrue((self.run/'PREPARED.json').is_file())


if __name__ == '__main__':
    unittest.main()
