"""PostgreSQL smoke-test cleanup uncertainty with inert subprocess results."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import trial_candidate_request as candidate


class SmokeCleanupTests(unittest.TestCase):
    def exercise(self, *, start_timeout=False, stop_error=None, existing=False):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory)/'entry'
            entry.mkdir()
            socket = Path(directory)/'socket'
            socket.mkdir()
            root = entry/'pg-smoke'
            if existing:
                root.mkdir()
                (root/'owned-by-other').write_text('preserve')
            commands = []
            def run(argv, **options):
                commands.append(argv)
                if argv[-1] == 'start' and start_timeout:
                    raise subprocess.TimeoutExpired(argv, 120)
                if argv[-1] == 'stop':
                    if stop_error == 'timeout':
                        raise subprocess.TimeoutExpired(argv, 120)
                    return subprocess.CompletedProcess(argv, 1 if stop_error else 0, b'', b'')
                return subprocess.CompletedProcess(argv, 0, b'1\n', b'')
            with patch.object(candidate.tempfile, 'mkdtemp', return_value=str(socket)), \
                    patch.object(candidate.subprocess, 'run', side_effect=run):
                if stop_error:
                    with self.assertRaisesRegex(candidate.paired.Refusal, '^postgres_smoke_cleanup_unknown$'):
                        candidate.smoke_postgres(entry, Path('/inert/postgres'))
                    self.assertTrue(root.is_dir())
                    self.assertTrue(socket.is_dir())
                elif start_timeout or existing:
                    with self.assertRaisesRegex(candidate.paired.Refusal, '^postgres_smoke_failed$'):
                        candidate.smoke_postgres(entry, Path('/inert/postgres'))
                    self.assertEqual(root.exists(), existing)
                    self.assertFalse(socket.exists())
                else:
                    self.assertEqual(candidate.smoke_postgres(entry, Path('/inert/postgres')),
                                     dict(status='passed', query='select 1'))
                    self.assertFalse(root.exists())
                    self.assertFalse(socket.exists())
            self.assertEqual(sum(argv[-1] == 'stop' for argv in commands), 0 if existing else 1)
            if existing:
                self.assertEqual((root/'owned-by-other').read_text(), 'preserve')
                self.assertEqual(commands, [])

    def test_known_stop_cleans_both_directories(self):
        self.exercise()

    def test_failed_stop_preserves_both_directories_and_refuses_success(self):
        self.exercise(stop_error='nonzero')

    def test_start_timeout_still_attempts_stop(self):
        self.exercise(start_timeout=True)

    def test_stop_timeout_preserves_recovery_paths(self):
        self.exercise(start_timeout=True, stop_error='timeout')

    def test_existing_cluster_directory_is_not_removed(self):
        self.exercise(existing=True)
