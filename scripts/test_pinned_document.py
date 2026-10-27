"""pinned_document rules, each through the module's own interface, on disposable files."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import pinned_document as pinned
import trial_paired_request as paired


RE_EXPORTED = ('FILE_LIMIT', 'PIN_LIMIT', 'Refusal', 'canonical_sha', 'decode_json', 'need',
               'pin', 'pin_shape', 'plain_path', 'read_bytes', 'read_json', 'read_json_pin',
               'read_pin_bytes', 'reject_nonfinite', 'scan_refusal', 'selected_bytes', 'shape',
               'unique_object', 'utc', 'write_selected', 'write_selected_bytes')


class PinnedDocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        os.chmod(self.root, 0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, content=b'{"a": 1}\n', mode=0o600):
        path = self.root / name
        path.write_bytes(content)
        os.chmod(path, mode)
        return path

    def refuses(self, expected, function, *args, **kwargs):
        with self.assertRaises(pinned.Refusal) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), expected)

    # --- refusals, shapes, timestamps ---------------------------------------------
    def test_need_and_shape_raise_the_fixed_code_only(self):
        self.assertIsNone(pinned.need(True, 'unused'))
        self.refuses('bad', pinned.need, False, 'bad')
        self.assertTrue(issubclass(pinned.Refusal, ValueError))
        pinned.shape({'a': 1, 'b': 2}, 'a b', 'shape_code')
        self.refuses('shape_code', pinned.shape, {'a': 1}, 'a b', 'shape_code')
        self.refuses('shape_code', pinned.shape, ['a', 'b'], 'a b', 'shape_code')

    def test_utc_accepts_only_aware_zero_offset_timestamps(self):
        parsed = pinned.utc('2026-10-27T01:02:03Z')
        self.assertEqual(parsed, dt.datetime(2026, 10, 27, 1, 2, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(pinned.utc('2026-10-27T01:02:03+00:00'), parsed)
        for bad in ('2026-10-27T01:02:03', '2026-10-27T01:02:03+01:00', 'yesterday', 7, None):
            self.refuses('utc_timestamp', pinned.utc, bad)

    # --- digests ---------------------------------------------------------------------
    def test_canonical_sha_sorts_keys_keeps_unicode_and_refuses_nonfinite(self):
        document = {'b': [1, 2], 'a': 'é'}
        expected = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(pinned.canonical_sha(document), expected)
        self.assertEqual(pinned.canonical_sha({'a': 'é', 'b': [1, 2]}), expected)
        with self.assertRaises(ValueError):
            pinned.canonical_sha({'a': float('nan')})

    def test_digest_helpers_agree_with_hashlib(self):
        content = b'abc'
        self.assertEqual(pinned.sha256_hex(content), hashlib.sha256(content).hexdigest())
        self.assertEqual(pinned.digest(content), dict(bytes=3, sha256=hashlib.sha256(content).hexdigest()))
        self.assertEqual(pinned.hex_digest('a' * 64), 'a' * 64)
        for bad in ('A' * 64, 'a' * 63, 'g' * 64, b'a' * 64, None):
            self.refuses('digest_shape', pinned.hex_digest, bad)
        self.refuses('own_code', pinned.hex_digest, 'nope', 'own_code')

    # --- paths and reads ---------------------------------------------------------------
    def test_plain_path_refuses_relative_parent_and_symlinked_paths(self):
        regular = self.file('doc.json')
        self.assertEqual(pinned.plain_path(str(regular)), regular)
        self.refuses('absolute_path_required', pinned.plain_path, 'doc.json')
        self.refuses('absolute_path_required', pinned.plain_path, None)
        self.refuses('path_indirection', pinned.plain_path, str(self.root / '..' / self.root.name / 'doc.json'))
        link = self.root / 'link.json'
        link.symlink_to(regular)
        self.refuses('path_indirection', pinned.plain_path, str(link))
        linked_dir = self.root / 'dir-link'
        linked_dir.symlink_to(self.root)
        self.refuses('path_indirection', pinned.plain_path, str(linked_dir / 'doc.json'))

    def test_read_bytes_bounds_size_and_requires_a_regular_file(self):
        regular = self.file('doc.json', b'12345')
        self.assertEqual(pinned.read_bytes(regular), b'12345')
        self.assertEqual(pinned.read_bytes(str(regular), limit=5), b'12345')
        self.refuses('file_limit', pinned.read_bytes, regular, limit=4)
        self.refuses('own_limit', pinned.read_bytes, regular, limit=4, code='own_limit')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo, 0o600)
        self.refuses('regular_file_required', pinned.read_bytes, fifo)
        with self.assertRaises(IsADirectoryError):
            pinned.read_bytes(self.root)
        with self.assertRaises(FileNotFoundError):
            pinned.read_bytes(self.root / 'missing.json')
        self.assertEqual(pinned.FILE_LIMIT, 8 * 1024 * 1024)
        self.assertEqual(pinned.PIN_LIMIT, 512 * 1024 * 1024)

    def test_decode_json_is_strict(self):
        self.assertEqual(pinned.decode_json(b'{"a": [1, "\\u00e9"]}'), {'a': [1, 'é']})
        self.assertEqual(pinned.read_json(self.file('doc.json', b'{"k": true}')), {'k': True})
        self.refuses('duplicate_json_key', pinned.decode_json, b'{"a": 1, "a": 2}')
        self.refuses('nonfinite_json', pinned.decode_json, b'{"a": NaN}')
        self.refuses('nonfinite_json', pinned.decode_json, b'[Infinity]')
        self.refuses('invalid_json', pinned.decode_json, b'{"a": }')
        self.refuses('invalid_json', pinned.decode_json, b'\xff\xfe')

    # --- pins ---------------------------------------------------------------------------
    def test_pin_reference_round_trips_through_pin_and_read_pin_bytes(self):
        regular = self.file('doc.json', b'{"a": 1}\n')
        reference = pinned.pin_reference(regular)
        self.assertEqual(reference, dict(path=str(regular), sha256=hashlib.sha256(b'{"a": 1}\n').hexdigest()))
        self.assertIsNone(pinned.pin(reference))
        self.assertEqual(pinned.read_pin_bytes(reference), b'{"a": 1}\n')
        self.assertEqual(pinned.read_json_pin(reference), {'a': 1})
        self.assertEqual(pinned.file_sha256(regular), reference['sha256'])
        regular.write_bytes(b'{"a": 2}\n')
        self.refuses('pin_mismatch', pinned.pin, reference)
        self.refuses('pin_mismatch', pinned.read_pin_bytes, reference)

    def test_pin_shape_and_limits(self):
        regular = self.file('doc.json', b'0123456789')
        good = pinned.pin_reference(regular)
        self.refuses('pin_shape', pinned.pin, dict(good, bytes=10))
        self.refuses('pin_shape', pinned.pin, 'not a reference')
        self.refuses('pin_digest', pinned.pin, dict(path=good['path'], sha256='Z' * 64))
        self.refuses('pin_byte_limit', pinned.file_sha256, regular, limit=9)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo, 0o600)
        self.refuses('regular_file_required', pinned.file_sha256, fifo)
        with self.assertRaises(IsADirectoryError):
            pinned.file_sha256(self.root)
        self.refuses('path_indirection', pinned.pin, dict(path=str(self.root / '..' / self.root.name / 'doc.json'),
                                                           sha256=good['sha256']))

    def test_read_sized_pin_keeps_each_callers_differences_explicit(self):
        regular = self.file('doc.json', b'0123456789')
        reference = dict(path=str(regular), **pinned.digest(b'0123456789'))
        self.assertEqual(pinned.read_sized_pin(reference), b'0123456789')
        self.assertEqual(pinned.read_sized_pin(dict(reference, extra=1), 'c', exact=False), b'0123456789')
        self.refuses('c', pinned.read_sized_pin, dict(reference, extra=1), 'c')
        self.refuses('shape', pinned.read_sized_pin, dict(path=reference['path']), 'c', shape_code='shape')
        self.refuses('c', pinned.read_sized_pin, dict(reference, bytes=9), 'c')
        self.refuses('c', pinned.read_sized_pin, dict(reference, sha256='a' * 64), 'c')
        self.refuses('small', pinned.read_sized_pin, reference, 'c', limit=4, limit_code='small')
        missing = dict(path=str(self.root / 'missing.json'), bytes=1, sha256='a' * 64)
        with self.assertRaises(FileNotFoundError):
            pinned.read_sized_pin(missing, 'c')
        self.refuses('c', pinned.read_sized_pin, missing, 'c', os_error=True)

    # --- writes ---------------------------------------------------------------------
    def test_write_selected_creates_owner_only_sorted_json_once(self):
        path = self.root / 'selected.json'
        pinned.write_selected(path, {'b': 1, 'a': 'é'})
        self.assertEqual(path.read_bytes(), b'{"a": "\xc3\xa9", "b": 1}\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(pinned.selected_bytes({'b': 1, 'a': 'é'}), path.read_bytes())
        with self.assertRaises(FileExistsError):
            pinned.write_selected(path, {'a': 1})

    def test_write_once_tolerates_identical_reruns_only(self):
        path = self.root / 'once.json'
        first = pinned.write_once(path, b'{"a": 1}\n', 'changed')
        self.assertEqual(first, dict(path=str(path), bytes=9, sha256=hashlib.sha256(b'{"a": 1}\n').hexdigest()))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(pinned.write_once(path, b'{"a": 1}\n', 'changed'), first)
        self.refuses('changed', pinned.write_once, path, b'{"a": 2}\n', 'changed')
        self.assertEqual(path.read_bytes(), b'{"a": 1}\n')

    # --- re-export ------------------------------------------------------------------
    def test_trial_paired_request_re_exports_the_same_objects(self):
        for name in RE_EXPORTED:
            self.assertIs(getattr(paired, name), getattr(pinned, name), name)
        self.assertIs(paired.Refusal, pinned.Refusal)
        self.assertTrue(callable(paired.check_input_files))
        self.assertTrue(callable(paired.identifier))


if __name__ == '__main__':
    unittest.main()
