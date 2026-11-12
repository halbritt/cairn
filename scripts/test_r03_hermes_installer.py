"""Hermes-specific selection over the existing R-02 immutable installer."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('r03_hermes_installer', ROOT / 'scripts/install-hermes-integration.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SelectionInputTests(unittest.TestCase):
    def test_selected_cli_does_not_require_an_unselected_global_claude(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            native, selection, skill = home / 'native.json', home / 'selected.json', home / 'skill.md'
            native.write_text('{}')
            selected = dict(path='/selected/manifest.json', sha256='a' * 64)
            selection.write_text(json.dumps(selected))
            skill.write_text('Fixture skill.')
            argv = ['install-hermes-integration.py', '--hermes-home', str(home), '--native-config', str(native),
                    '--skill', str(skill), '--lifecycle-selection', str(selection), '--stager', '/selected/stager']
            with patch.object(installer.sys, 'argv', argv), patch.object(installer.shutil, 'which', return_value=None), \
                    patch.object(installer, 'install') as install, patch('builtins.print'):
                installer.main()
            self.assertEqual(install.call_args.kwargs['lifecycle_selection'], selected)
            self.assertIsNone(install.call_args.args[2])

    def test_incomplete_selected_input_refuses_before_profile_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / 'config.yaml'
            config.write_bytes(b'{"unrelated":"retained"}\n')
            original = config.read_bytes()
            for selection, stager in (({}, None), (None, '/selected/stager')):
                with self.subTest(selection=selection, stager=stager):
                    with self.assertRaises(ValueError):
                        installer.install(home, {}, '', home / 'absent-skill',
                                          lifecycle_selection=selection, stager=stager)
                    self.assertEqual(config.read_bytes(), original)
                    self.assertEqual(sorted(p.name for p in home.iterdir()), ['config.yaml'])


if __name__ == '__main__':
    unittest.main()
