import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('recurrence_trial', Path(__file__).with_name('trial-opencode-recurrence.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class ExplanationTest(unittest.TestCase):
    def test_selected_text_is_private_bounded_and_excludes_other_event_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = [{'type': 'text', 'part': {'text': 'earlier prose'}},
                     {'type': 'reasoning', 'part': {'text': 'private reasoning'}},
                     {'type': 'tool_use', 'part': {'text': 'private tool output'}},
                     {'type': 'text', 'part': {'text': 'x' + 'é' * 5000}}]
            result = trial.retain_last_explanation(root, 'repo_only', trace)
            path = Path(result['path'])
            raw = path.read_bytes()
            self.assertEqual(raw.decode(), 'x' + 'é' * 4095)
            self.assertLessEqual(len(raw), 8192)
            self.assertTrue(result['truncated'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_no_model_text_creates_no_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = trial.retain_last_explanation(root, 'repo_only', [
                {'type': 'tool_use', 'part': {'text': 'private tool output'}},
                {'type': 'reasoning', 'part': {'text': 'private reasoning'}}])
            self.assertIsNone(result)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
