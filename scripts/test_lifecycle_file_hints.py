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
                          "https://example.org:8080/docs/guide.md?next=core/store.go",
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

    def test_current_task_files_survive_full_recent_cache(self):
        recent = {f"old/file{i}.py": 1000 for i in range(15)}
        # Normalized duplicates consume only one slot, including cached copies.
        recent["src/active.py"] = 1000
        state = dict(hints=dict(files=dict(recent)))
        event = dict(self.event, prompt="Repair ./src/active.py src/active.py src/next.py")
        with patch.object(hook.time, "time", return_value=1100):
            intent = hook.retrieval_intent(event, state)
        self.assertEqual(intent["files"], ["src/active.py", "src/next.py"] +
                         [f"old/file{i}.py" for i in range(1, 15)])
        self.assertEqual(intent["phrases"][:2], ["src/active.py", "src/next.py"])
        self.assertIn('"src/active.py"', intent["query"])
        self.assertLessEqual(len(intent["query"].encode()), 4000)
        self.assertEqual(state["hints"]["files"], recent)
        self.assertTrue(hook.relevant(dict(summary="guidance", entities=[
            dict(kind="file", name="src/active.py")]), intent))
        self.assertFalse(hook.relevant(dict(summary="unrelated", entities=[
            dict(kind="file", name="elsewhere/other.py")]), intent))

    def test_recent_tail_order_and_expiry_without_task_files(self):
        recent = {f"old/file{i}.py": 1000 for i in range(18)}
        recent["old/expired.py"] = 200
        with patch.object(hook.time, "time", return_value=1100):
            intent = hook.retrieval_intent(self.event, dict(hints=dict(files=recent)))
        self.assertEqual(intent["files"], [f"old/file{i}.py" for i in range(2, 18)])

    def test_prompt_overflow_keeps_existing_tail_order(self):
        current = [f"src/task{i}.py" for i in range(18)]
        event = dict(self.event, prompt="Repair " + " ".join(current))
        with patch.object(hook.time, "time", return_value=1100):
            intent = hook.retrieval_intent(event, dict(hints=dict(files={"old/recent.py": 1000})))
        self.assertEqual(intent["files"], current[-16:])

    def test_saturated_cache_does_not_change_file_eligibility(self):
        (self.root / "settings.json").write_text("{}")
        (self.root / "outside").symlink_to(self.root.parent, target_is_directory=True)
        event = dict(self.event, prompt="Inspect settings.json src/new.py missing.py "
                     "../escape.py outside/escape.py https://example.org/src/remote.py "
                     "example.org/docs/page.md person@example.org")
        recent = {f"old/file{i}.py": 1000 for i in range(16)}
        with patch.object(hook.time, "time", return_value=1100):
            intent = hook.retrieval_intent(event, dict(hints=dict(files=recent)))
        self.assertEqual(intent["files"], ["settings.json", "src/new.py"] +
                         [f"old/file{i}.py" for i in range(2, 16)])

    def test_recall_forwards_current_task_file_under_cache_pressure(self):
        memory = hook.Memory(dict(cairn="unused", socket="unused", token_file="unused",
                                  repo="fixture", context_bytes=9500), "session")
        state = dict(hints=dict(files={f"old/file{i}.py": 1000 for i in range(16)}))
        event = dict(self.event, prompt="Inspect src/current.py")
        with patch.object(hook.time, "time", return_value=1100), \
                patch.object(memory, "search", return_value=dict(selected=[], index=[])) as search:
            hook.recall(memory, event, state)
        self.assertEqual(search.call_args.kwargs["entities"], ["src/current.py"] +
                         [f"old/file{i}.py" for i in range(1, 16)])
