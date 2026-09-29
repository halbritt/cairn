import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "file_hint_hook", Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class FileHintRecallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".git").mkdir()
        self.event = dict(hook_event_name="UserPromptSubmit", cwd=str(self.root))

    def test_quota_resume_does_not_inject_domain_only_match(self):
        event = dict(self.event, prompt="Your claude.ai usage limit has reset. Continue the task "
                     "you were working on when the limit was reached; do not repeat work that is already complete.")
        memory = hook.Memory(dict(cairn="unused", socket="unused", token_file="unused",
                                  repo="fixture", context_bytes=9500), "session")
        entry = dict(record_id="oauth", version=3,
                     summary='Claude Code warned "signed-in claude.ai account or organization changed on this machine".',
                     pull_arguments={})
        required = dict(record=dict(record_id="required", body="Keep mandatory guidance."), mandatory=True)
        pulled = dict(selection=dict(record=dict(record_id="oauth", version=3,
                      body=entry["summary"], **{"class": "A"})))
        with patch.object(memory, "search", return_value=dict(selected=[required], index=[entry])), \
                patch.object(memory, "call", return_value=pulled):
            result = hook.recall(memory, event, {})
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Keep mandatory guidance.", text)
        self.assertNotIn("oauth", text)

    def test_web_and_email_references_are_not_file_entities(self):
        for reference in ("claude.ai", "https://example.org/docs/guide.md",
                          "example.org/docs/guide.md", "person@example.org"):
            with self.subTest(reference=reference):
                intent = hook.retrieval_intent(dict(self.event, prompt="Inspect " + reference), {})
                self.assertEqual(intent["files"], [])
                self.assertFalse(intent["precise"])

    def test_existing_bare_files_and_explicit_paths_keep_file_recall(self):
        (self.root / "settings.json").write_text("{}")
        for reference in ("settings.json", "core/new.go", "./new.py"):
            with self.subTest(reference=reference):
                intent = hook.retrieval_intent(dict(self.event, prompt="Repair " + reference), {})
                self.assertEqual(intent["files"], [reference.removeprefix("./")])
                self.assertTrue(hook.relevant(dict(summary="prior guidance",
                    entities=[dict(kind="file", name=intent["files"][0])]), intent))

    def test_missing_bare_name_stays_lexical_and_explicit_quotes_stay_exact(self):
        intent = hook.retrieval_intent(dict(self.event, prompt="Repair missing.py"), {})
        self.assertEqual(intent["files"], [])
        self.assertIn("missing.py", intent["words"])
        quoted = hook.retrieval_intent(dict(self.event, prompt='Repair "missing.py"'), {})
        self.assertIn('"missing.py"', quoted["query"])
        self.assertTrue(hook.relevant(dict(summary="missing.py repair guidance"), quoted))
