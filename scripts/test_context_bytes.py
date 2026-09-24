"""The lifecycle context budget is per installation (CAIRN-35).

Claude Code delivers about 10,000 characters of hook additionalContext and
drops the rest, so its installations cap the budget below that; other
harnesses keep the 12,000-byte default.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hook = module("cairn_lifecycle_budget", ROOT / "integrations/lifecycle/memory.py")
SESSION = "caed9473-b01a-41e7-95ce-c3c1f28d66b3"


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        self.config = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared",
                           state_dir=str(self.root / "state"))
        self.event = dict(hook_event_name="UserPromptSubmit", session_id=SESSION, cwd=str(self.root),
                          prompt="fix the retry policy for the uploader")

    def recall(self, config, body_bytes, summary="retry policy uploader fix"):
        memory = hook.Memory(config, SESSION)
        search = dict(status="READY", destination=dict(name="hosted"), selected=[],
                      index=[dict(record_id="r1", version=1, summary=summary, pull_arguments={"handle": "h"})])
        pulled = {"selection": {"record": {"body": "x" * body_bytes}}}
        with patch.object(memory, "call", side_effect=[search, pulled]):
            return hook.recall(memory, dict(self.event), {})

    def test_default_budget_and_validation(self):
        self.assertEqual(hook.context_budget({}), hook.CONTEXT_BYTES)
        self.assertEqual(hook.context_budget(dict(context_bytes=9500)), 9500)
        for invalid in (0, 999, 70000, "9500", 9.5, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(hook.HookError):
                hook.context_budget(dict(context_bytes=invalid))

    def test_configured_budget_bounds_the_expanded_body(self):
        default = self.recall(self.config, 10000)["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"expanded"', default, "the default budget should fit a 10 KB body")
        capped = self.recall(dict(self.config, context_bytes=9500), 10000)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn('"expanded"', capped, "a body past the configured budget was delivered")
        self.assertLessEqual(len(capped.encode()), 9500)
        self.assertIn('"handle":"h"', capped, "the index must still offer the pull handle")

    def test_index_beyond_the_configured_budget_refuses_instead_of_truncating(self):
        with self.assertRaises(hook.HookError):
            self.recall(dict(self.config, context_bytes=1000), 100, summary="retry policy uploader fix " + "y" * 2000)


class InstallerTests(unittest.TestCase):
    def test_claude_installer_caps_the_budget_below_claude_codes_limit(self):
        claude = module("install_claude_budget", ROOT / "scripts/install-claude-hooks.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = dict(cairn="/bin/cairn", claude="/bin/claude", socket="/s", token_file="/t", repo="shared")
            claude.install(root / "settings.json", root / "dest", base)
            installed = json.loads((root / "dest" / "config.json").read_text())
            self.assertEqual(installed["context_bytes"], 9500)
            self.assertEqual(hook.context_budget(installed), 9500)


if __name__ == "__main__":
    unittest.main()
