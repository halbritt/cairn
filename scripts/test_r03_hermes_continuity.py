"""Safety boundaries for the separately run, held-runtime continuity prep."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import check_r03_hermes_continuity as continuity


class ContinuityBoundary(unittest.TestCase):
    def setUp(self):
        cache = Path.home() / '.cache'
        self.temp = tempfile.TemporaryDirectory(prefix='r03-continuity-test-', dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_native_phase_refuses_before_invalid_paths_or_any_effect(self):
        output = self.root / 'untouched'
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = continuity.main(['--phase', 'native', '--output', str(output),
                '--native-root', '/nonexistent/hermes', '--manifest', '/nonexistent/pins'])
        report = json.loads(stream.getvalue())
        self.assertEqual(status, 75)
        self.assertEqual(report['status'], 'NOTRUN')
        self.assertFalse(report['ac4_closed'])
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_public_evidence_ancestor_refused_before_output_creation(self):
        parent = self.root / 'public'
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        with self.assertRaisesRegex(continuity.PrerequisiteError, '0700'):
            continuity.private_output(self.root, parent / 'case')
        self.assertFalse((parent / 'case').exists())

    def test_symlink_cannot_redirect_application_state(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'symlink'):
            continuity.private_output(self.root, link / 'case')
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_evidence_is_never_reused_or_erased(self):
        prior = self.root / 'prior'
        prior.mkdir(mode=0o700)
        sentinel = prior / 'failure.json'
        sentinel.write_text('retained')
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'fresh output'):
            continuity.private_output(self.root, prior)
        self.assertEqual(sentinel.read_text(), 'retained')

    def test_wrong_inventory_stops_before_runtime_access(self):
        manifest = self.root / 'pins.json'
        manifest.write_text('{}')
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'held inventory changed'):
            continuity.verify_inputs(self.root / 'absent-runtime', manifest)

    def test_session_api_requires_disposable_home_before_import(self):
        home = self.root / 'home'
        home.mkdir(mode=0o700)
        # The normal host interpreter cannot enter the held API phase merely
        # because a caller supplies a valid-looking application home.
        previous = os.environ.pop('HERMES_HOME', None)
        try:
            with self.assertRaisesRegex(continuity.PrerequisiteError, 'disposable HERMES_HOME'):
                continuity.session_api(home, self.root / 'absent-runtime', self.root / 'report.json')
        finally:
            if previous is not None:
                os.environ['HERMES_HOME'] = previous
        self.assertFalse((home / 'state.db').exists())


if __name__ == '__main__':
    unittest.main()
