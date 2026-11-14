"""Focused privacy guard for the maintained unbound process fixture."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_r06_unbound_roundtrip as fixture


class SnapshotPrivacyTests(unittest.TestCase):
    def test_credential_and_hardlink_alias_are_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root/'token'
            credential.write_bytes(b'fixture-value-that-must-not-be-read')
            credential.chmod(0o600)
            alias = root/'unlabelled-alias.json'
            os.link(credential, alias)
            regular = root/'ordinary.json'
            regular.write_bytes(b'{}')
            called = []
            original = fixture.sha
            def checked(path):
                self.assertFalse(os.path.samefile(path, credential))
                called.append(path)
                return original(path)
            with patch.object(fixture, 'sha', checked):
                rows = fixture.snapshot_root(root, credential)
            self.assertEqual(called, [regular])
            for path in (credential, alias):
                self.assertFalse(rows[str(path)]['content_read'])
                self.assertNotIn('sha256', rows[str(path)])


if __name__ == '__main__':
    unittest.main()
