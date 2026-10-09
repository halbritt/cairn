import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ..retention import Retention, Intent, ExitSnapshot, RetentionError, inspect_run, SELECTED_LIMIT
from ..frames import Frames
from ..evidence import PULL
from .test_selected_evidence import partial, search, BODY, RECEIPT, HANDLE, PULL_REQUEST
from .test_selected_frames import SESSION, MODEL, INIT, FINAL, wire, blocks, call, reply


INTENT = Intent(SESSION, 'a'*64, 'b'*64, 'c'*64, 'd'*64)
EXIT = ExitSnapshot(123, 456, '80000000-0000-4000-8000-000000000001', 0, False)


def completed():
    r = Frames(SESSION, MODEL, 'test-version')
    args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE,
            'span': {'offset': 14, 'length': 12}}
    for e in [INIT, blocks('assistant', 'RAW_PRIVATE_TRANSCRIPT_SENTINEL'),
              call(), reply(), call(PULL, args, 'p'), reply(partial(), 'p'), FINAL]:
        r.feed(wire(e))
    return r


class SelectedRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cairn267-selected-retention-')
        self.root = Path(self.tmp.name)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def tearDown(self):
        os.close(self.fd)
        self.tmp.cleanup()

    def runpath(self, name):
        return self.root / SESSION / name

    def test_selected_span_and_task_card_readback_without_raw_transcript_or_success_claim(self):
        with Retention.create(self.fd, INTENT) as store:
            receipt = store.prepare(completed(), EXIT)
            self.assertEqual(store.state, 'prepared')
        raw = self.runpath('SELECTED.json').read_bytes()
        selected = json.loads(raw)
        returned = selected['evidence']['responses']['p']['source']
        self.assertEqual(base64.b64decode(returned['span']['body_base64']), BODY.encode()[14:26])
        self.assertEqual(returned['coverage'], 'returned_byte_range')
        self.assertNotIn(b'RAW_PRIVATE_TRANSCRIPT_SENTINEL', raw)
        self.assertEqual(receipt.selected_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt.selected_bytes, len(raw))
        self.assertFalse(receipt.return_receipt)
        marker = json.loads(self.runpath('PREPARED.json').read_text())
        self.assertFalse(marker['exit_attested'])
        self.assertFalse(marker['task_success_claimed'])
        self.assertFalse(marker['standalone_terminal_is_success'])
        for f in (self.root / SESSION).iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / SESSION).stat().st_mode & 0o777, 0o700)

    def test_same_target_never_reopened_after_success_failure_or_symlink(self):
        for scenario in ['success', 'failure', 'symlink']:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                root = Path(d); fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    if scenario == 'symlink':
                        (root / SESSION).symlink_to(self.root, target_is_directory=True)
                    else:
                        with Retention.create(fd, INTENT) as store:
                            if scenario == 'success': store.prepare(completed(), EXIT)
                            else:
                                with self.assertRaisesRegex(RetentionError, '^native_exit_or_signal$'):
                                    store.prepare(completed(), dataclasses.replace(EXIT, exit_code=7))
                    before = list(root.iterdir())
                    with self.assertRaisesRegex(RetentionError, '^target_consumed$'):
                        Retention.create(fd, INTENT)
                    self.assertEqual(before, list(root.iterdir()))
                finally: os.close(fd)

    def test_bad_identity_root_mode_or_raw_dictionary_cannot_create_success(self):
        with self.assertRaisesRegex(RetentionError, '^identity_shape$'):
            Retention.create(self.fd, dataclasses.replace(INTENT, run_id='../escape'))
        os.chmod(self.root, 0o755)
        with self.assertRaisesRegex(RetentionError, '^root_custody$'):
            Retention.create(self.fd, INTENT)
        os.chmod(self.root, 0o700)
        self.assertFalse((self.root / SESSION).exists())
        with Retention.create(self.fd, INTENT) as store:
            with self.assertRaisesRegex(RetentionError, '^selected_input_type$'):
                store.prepare({'raw': 'PRIVATE'}, EXIT)
            self.assertFalse(self.runpath('SELECTED.json').exists())
            self.assertFalse(self.runpath('PREPARED.json').exists())

    def test_mismatched_session_signal_nonzero_and_incomplete_stream_poison_once(self):
        for scenario in ['session', 'signal', 'exit', 'incomplete']:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with Retention.create(fd, INTENT) as store:
                        r = completed(); exit_fact = EXIT
                        if scenario == 'session': r = Frames('different', MODEL, 'test-version')
                        elif scenario == 'signal': exit_fact = dataclasses.replace(EXIT, signal_observed=True)
                        elif scenario == 'exit': exit_fact = dataclasses.replace(EXIT, exit_code=1)
                        else: r = Frames(SESSION, MODEL, 'test-version'); r.feed(wire(INIT))
                        with self.assertRaises(RetentionError): store.prepare(r, exit_fact)
                        with self.assertRaisesRegex(RetentionError, '^writer_unavailable$'):
                            store.prepare(completed(), EXIT)
                        self.assertEqual(store.state, 'failed')
                        self.assertFalse((Path(d)/SESSION/'PREPARED.json').exists())
                finally: os.close(fd)

    def test_selected_leaf_symlink_refused_without_touching_its_target(self):
        untouched = self.root / 'do-not-touch'; untouched.write_bytes(b'KEEP')
        with Retention.create(self.fd, INTENT) as store:
            self.runpath('SELECTED.json').symlink_to(untouched)
            with self.assertRaisesRegex(RetentionError, '^retention_io$'): store.prepare(completed(), EXIT)
            self.assertFalse(self.runpath('PREPARED.json').exists())
        self.assertEqual(untouched.read_bytes(), b'KEEP')

    def test_fsync_failure_consumes_target_and_diagnostic_failure_does_not_replace_primary(self):
        with patch('selected_native.retention.os.fsync', side_effect=OSError('PRIVATE_STORAGE_SENTINEL')):
            with self.assertRaisesRegex(RetentionError, '^intent_io_after_consume$') as caught:
                Retention.create(self.fd, INTENT)
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        with self.assertRaisesRegex(RetentionError, '^target_consumed$'): Retention.create(self.fd, INTENT)
        new = dataclasses.replace(INTENT, run_id='70000000-0000-4000-8000-000000000002')
        with Retention.create(self.fd, new) as store:
            with patch('selected_native.retention.os.fsync', side_effect=OSError('PRIVATE_STORAGE_SENTINEL')):
                with self.assertRaisesRegex(RetentionError, '^run_mismatch$') as caught:
                    store.prepare(completed(), EXIT)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual(store.state, 'failed')

    def test_readback_io_error_never_returns_prepared_and_retains_selected_partial(self):
        actual_open = os.open
        def fail_readback(name, flags, *args, **kw):
            if name == 'SELECTED.json' and not flags & os.O_CREAT:
                raise OSError('PRIVATE_READBACK_SENTINEL')
            return actual_open(name, flags, *args, **kw)
        with Retention.create(self.fd, INTENT) as store:
            with patch('selected_native.retention.os.open', side_effect=fail_readback):
                with self.assertRaisesRegex(RetentionError, '^retention_io$'): store.prepare(completed(), EXIT)
        self.assertTrue(self.runpath('SELECTED.json').exists())
        self.assertFalse(self.runpath('PREPARED.json').exists())
        self.assertEqual(json.loads(self.runpath('FAILURE.json').read_text())['code'], 'retention_io')

    def test_interrupt_preserved_after_poison_and_owner_fd_closed(self):
        with Retention.create(self.fd, INTENT) as store:
            with patch('selected_native.retention.os.fsync', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): store.prepare(completed(), EXIT)
            self.assertEqual(store.state, 'failed')
            with self.assertRaisesRegex(RetentionError, '^writer_unavailable$'): store.prepare(completed(), EXIT)
        self.assertIsNone(store.fd)

    def test_secondary_interrupt_and_close_failure_keep_original_failure(self):
        original = KeyboardInterrupt('ORIGINAL_PRIVATE_SENTINEL')
        secondary = KeyboardInterrupt('SECONDARY_PRIVATE_SENTINEL')
        with Retention.create(self.fd, INTENT) as store:
            with patch('selected_native.retention.os.fsync', side_effect=[original, secondary]):
                with self.assertRaises(KeyboardInterrupt) as caught: store.prepare(completed(), EXIT)
            self.assertIs(caught.exception, original)
        new = dataclasses.replace(INTENT, run_id='70000000-0000-4000-8000-000000000002')
        store = Retention.create(self.fd, new); owned_fd = store.fd
        try:
            with patch('selected_native.retention.os.close', side_effect=OSError('CLOSE_PRIVATE_SENTINEL')):
                with self.assertRaisesRegex(RetentionError, '^run_mismatch$'):
                    with store: store.prepare(completed(), EXIT)
            self.assertTrue(store.close_unknown)
        finally:
            os.close(owned_fd)  # Test owns the fault-injected unclosed descriptor.

    def test_parser_valid_large_selected_envelope_fits_derived_storage_bound(self):
        r = Frames(SESSION, MODEL, 'test-version'); r.feed(wire(INIT))
        for i in range(16):
            request_id = f'40000000-0000-4000-8000-{100+i:012d}'
            response = search(); response['request_id'] = request_id
            r.feed(wire(call(args={'request_id': request_id, 'query': 'x'*4000}, key=f's{i}')))
            r.feed(wire(reply(response, key=f's{i}')))
        r.feed(wire(FINAL))
        with Retention.create(self.fd, INTENT) as store:
            receipt = store.prepare(r, EXIT)
        self.assertGreater(receipt.selected_bytes, 65536)
        self.assertLess(receipt.selected_bytes, SELECTED_LIMIT)
        self.assertEqual(len(json.loads(self.runpath('SELECTED.json').read_bytes())['evidence']['calls']), 16)

    def test_direct_constructor_refused_before_it_can_write_into_root(self):
        with self.assertRaisesRegex(RetentionError, '^factory_required$'): Retention(self.fd, INTENT)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_prepared_alone_unknown_failure_wins_and_intent_hash_mismatch_refused(self):
        with Retention.create(self.fd, INTENT) as store: store.prepare(completed(), EXIT)
        status = inspect_run(self.fd, SESSION)
        self.assertEqual(status.status, 'unknown')
        self.assertFalse(status.return_receipt)
        self.assertFalse(status.cleanup_authorized)
        marker = self.runpath('PREPARED.json'); terminal = json.loads(marker.read_text())
        terminal['intent_sha256'] = '0'*64; marker.write_text(json.dumps(terminal))
        with self.assertRaisesRegex(RetentionError, '^prepared_marker_shape$'): inspect_run(self.fd, SESSION)
        f = self.runpath('FAILURE.json'); f.write_text(json.dumps({'schema': 'cairn.selected-retention-failure/1',
            'run_id': SESSION, 'code': 'retention_io', 'success_claimed': False, 'return_receipt': False}))
        f.chmod(0o600)
        self.assertEqual(inspect_run(self.fd, SESSION).status, 'retained_failure')

    def test_prepared_directory_fsync_failure_and_missing_diagnostic_cannot_become_success(self):
        original_fsync = os.fsync
        with Retention.create(self.fd, INTENT) as store:
            def fail_after_terminal(fd):
                if self.runpath('PREPARED.json').exists(): raise OSError('NO_DIAGNOSTIC')
                original_fsync(fd)
            with patch('selected_native.retention.os.fsync', side_effect=fail_after_terminal):
                with self.assertRaisesRegex(RetentionError, '^retention_io$'): store.prepare(completed(), EXIT)
        # Diagnostic may exist as partial/notfsynced, so no durability asserted.
        if self.runpath('FAILURE.json').exists(): self.runpath('FAILURE.json').unlink()
        self.assertEqual(inspect_run(self.fd, SESSION).status, 'unknown')

    def test_replaced_selected_inode_or_modified_mode_bytes_size_link_refused(self):
        original_open = os.open
        for scenario, code in [('inode', 'readback_identity'), ('mode', 'readback_custody'),
                               ('size', 'readback_custody'), ('bytes', 'readback_mismatch'),
                               ('link', 'readback_custody')]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                root = Path(d); root_fd = original_open(d, os.O_RDONLY | os.O_DIRECTORY)
                target = root/SESSION/'SELECTED.json'
                def intercept(name, flags, *args, **kw):
                    if name == 'SELECTED.json' and not flags & os.O_CREAT:
                        if scenario == 'inode':
                            other = root/'replacement'; other.write_bytes(target.read_bytes()); other.chmod(0o600)
                            os.replace(other, target)
                        elif scenario == 'mode': target.chmod(0o644)
                        elif scenario == 'size': target.write_bytes(target.read_bytes()+b'x')
                        elif scenario == 'bytes': target.write_bytes(b'x'+target.read_bytes()[1:])
                        else: os.link(target, root/'hardlink')
                    return original_open(name, flags, *args, **kw)
                try:
                    with Retention.create(root_fd, INTENT) as store:
                        with patch('selected_native.retention.os.open', side_effect=intercept):
                            with self.assertRaisesRegex(RetentionError, '^'+code+'$'): store.prepare(completed(), EXIT)
                    self.assertFalse((root/SESSION/'PREPARED.json').exists())
                finally: os.close(root_fd)

    def test_before_consume_io_and_unexpected_diagnostic_error_keep_truthful_primary(self):
        with patch('selected_native.retention.os.mkdir', side_effect=OSError('BEFORE')):
            with self.assertRaisesRegex(RetentionError, '^intent_io_before_consume$'): Retention.create(self.fd, INTENT)
        self.assertFalse((self.root/SESSION).exists())
        with Retention.create(self.fd, INTENT) as store:
            with patch('selected_native.retention.os.fsync', side_effect=MemoryError('DIAGNOSTIC_PRIVATE')):
                with self.assertRaisesRegex(RetentionError, '^native_exit_or_signal$'):
                    store.prepare(completed(), dataclasses.replace(EXIT, exit_code=1))

    def test_malformed_marker_error_has_no_private_json_chain(self):
        with Retention.create(self.fd, INTENT): pass
        marker = self.runpath('PREPARED.json'); marker.write_bytes(b'{PRIVATE_MARKER_SENTINEL'); marker.chmod(0o600)
        with self.assertRaisesRegex(RetentionError, '^marker_shape$') as caught: inspect_run(self.fd, SESSION)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


if __name__ == '__main__': unittest.main()
