"""Hermes-specific selection over the existing R-02 immutable installer."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('r03_hermes_installer', ROOT / 'scripts/install-hermes-integration.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SelectionInputTests(unittest.TestCase):
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
