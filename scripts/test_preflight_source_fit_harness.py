import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from preflight_source_fit import hook_configuration
from trial_task_eval import TRIAL_REPO


class SourceFitHarnessConfigTests(unittest.TestCase):
    def test_harness_is_explicit_and_defaults_remain_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SimpleNamespace(binary=root / 'cairn', socket=root / 'api.sock',
                                    token_file=root / 'agent.token')
            state = root / 'state'
            self.assertEqual(hook_configuration(store, state)['harness'], 'claude')
            for harness in ('claude', 'codex'):
                with self.subTest(harness=harness):
                    config = hook_configuration(store, state, harness)
                    self.assertEqual(config['harness'], harness)
                    self.assertEqual(config['recall_mode'], 'agent_tools')
                    self.assertEqual(config['repo'], TRIAL_REPO)
                    self.assertEqual(config['state_dir'], str(state))


if __name__ == '__main__':
    unittest.main()
