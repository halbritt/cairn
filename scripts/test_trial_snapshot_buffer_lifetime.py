"""Synthetic file validation must not overlap consecutive raw read buffers."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from trial_bootstrap_snapshot import Snapshot


class SnapshotBufferLifetime(unittest.TestCase):
    def test_copy_and_revalidation_drop_old_block_before_next_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'synthetic.bin'
            size = 131089  # Independent of the implementation's read granularity.
            source.write_bytes(b'a' * size)
            source.chmod(0o600)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            snapshot = Snapshot(str(root / 'snapshot'))
            actual_read = os.read
            observations = []
            offers = []
            def read(fd, count):
                frame = sys._getframe(1)
                while frame is not None and frame.f_code not in (
                        Snapshot.copy.__code__, Snapshot._readback.__code__):
                    frame = frame.f_back
                if frame is not None:
                    offers.append(count)
                    old = frame.f_locals.get('block')
                    observations.append((frame.f_code.co_name, len(old) if old else 0))
                return actual_read(fd, count)
            try:
                with patch('os.read', side_effect=read):
                    snapshot.copy('synthetic.bin', {'path': str(source), 'sha256': digest}, size)
                    bindings = snapshot.bindings()
                self.assertEqual(bindings['synthetic.bin']['bytes'], size)
                self.assertEqual(bindings['synthetic.bin']['sha256'], digest)
                self.assertTrue(any(name == 'copy' for name, size in observations))
                self.assertTrue(any(name == '_readback' for name, size in observations))
                self.assertGreaterEqual(len(observations), 12)
                self.assertEqual([row for row in observations if row[1]], [])
                self.assertLessEqual(max(offers), 8192)
            finally:
                self.assertEqual(snapshot.close(), ())


if __name__ == '__main__':
    unittest.main()
