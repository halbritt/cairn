"""Real-filesystem tests for the watch driver's early evidence-path diagnostic."""
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import check_r06_watch_unit_roundtrip as driver
from disposable_host import HostRefusal


class EvidencePathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='r06-evidence-test-', dir=Path.home() / '.cache')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_private_0700_preflight_preserves_existing_metadata_and_bytes(self):
        parent = self.root / 'private'
        parent.mkdir(mode=0o700)
        sentinel = parent / 'sentinel'
        sentinel.write_bytes(b'preserve')
        before = {path: path.lstat() for path in (self.root, parent, sentinel)}
        evidence = parent / 'fresh'

        driver.preflight_evidence_path(evidence)

        self.assertEqual(before, {path: path.lstat() for path in before})
        self.assertEqual(sentinel.read_bytes(), b'preserve')
        self.assertFalse(evidence.exists())

    def test_private_entry_creates_only_fresh_leaf_before_runtime(self):
        evidence = self.root / 'fresh'
        original_mode = self.root.lstat().st_mode
        # The full nine-case workload is outside this entry-path unit test.
        with mock.patch.object(driver, 'check', side_effect=RuntimeError('runtime boundary')):
            with self.assertRaisesRegex(RuntimeError, '^runtime boundary$'):
                driver.main(['--evidence', str(evidence)])
        self.assertEqual(evidence.lstat().st_mode & 0o777, 0o700)
        self.assertEqual(self.root.lstat().st_mode, original_mode)
        self.assertEqual(list(self.root.iterdir()), [evidence])

    def test_0775_ancestor_refuses_before_build_or_service_without_chmod(self):
        unsafe = self.root / 'unsafe'
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o775)
        evidence = unsafe / 'fresh'
        before = unsafe.lstat()
        with mock.patch.object(driver.subprocess, 'Popen', side_effect=AssertionError('process boundary reached')) as process:
            with self.assertRaisesRegex(HostRefusal, 'writable evidence ancestor: ' + re.escape(str(unsafe))):
                driver.main(['--evidence', str(evidence)])
        process.assert_not_called()
        self.assertEqual(unsafe.lstat(), before)
        self.assertFalse(evidence.exists())

    def test_symlink_ancestor_refuses_without_following_or_starting_processes(self):
        target = self.root / 'private-target'
        target.mkdir(mode=0o700)
        alias = self.root / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        before = target.lstat()
        with mock.patch.object(driver.subprocess, 'Popen', side_effect=AssertionError('process boundary reached')) as process:
            with self.assertRaisesRegex(HostRefusal, 'symlink evidence ancestor: ' + re.escape(str(alias))):
                driver.main(['--evidence', str(alias / 'fresh')])
        process.assert_not_called()
        self.assertEqual(target.lstat(), before)
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue(alias.is_symlink())


if __name__ == '__main__':
    unittest.main()
