"""Protect task vocabulary at the lifecycle search boundary, without a store."""
import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location(
    "query_hook", Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class LifecycleQueryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".git").mkdir()
        self.event = dict(hook_event_name="UserPromptSubmit", cwd=str(self.root))

    def query(self, prompt):
        return hook.retrieval_intent(dict(self.event, prompt=prompt), {})["query"]

    def test_task_vocabulary_survives_varied_background_when_query_fits(self):
        background = " ".join("background%02d" % i for i in range(65))
        request = "Implement memory_budget_bytes for OpenCode search and preserve zstd compression."
        for prompt in (background + "\n" + request, request + "\n" + background):
            with self.subTest(request_first=prompt.startswith(request)):
                words = set(shlex.split(self.query(prompt)))
                self.assertTrue({"memory_budget_bytes", "opencode", "search", "zstd"} <= words)

    def test_unquoted_identifier_and_unicode_terms_are_not_discarded(self):
        identifier = "70d91493-0c6e-4a18-a829-f6443030791b"
        prompt = "Investigate incident " + identifier + " with café 日本語 and UTF_8 decoding."
        words = set(shlex.split(self.query(prompt)))
        self.assertTrue({identifier, "café", "日本語", "utf_8", "decoding"} <= words)

    def test_large_query_keeps_late_exact_anchor_and_whole_tokens_within_limit(self):
        background = " ".join("背景%04d" % i for i in range(1200))
        prompt = background + '\nRepair "src/final.py" after "ZSTD_frameHeader" fails.'
        query = self.query(prompt)
        self.assertLessEqual(len(query.encode()), 4000)
        self.assertIn('"src/final.py"', query)
        self.assertIn('"ZSTD_frameHeader"', query)
        self.assertEqual(query, self.query(prompt))
        allowed = hook.terms(prompt) | {self.root.name, "src/final.py", "ZSTD_frameHeader"}
        self.assertTrue(set(shlex.split(query)) <= allowed)

    def test_short_query_preserves_eligible_vocabulary_without_new_stopword_policy(self):
        prompt = "Repair café socket_timeout in worker-42; memory should remain bounded."
        words = set(shlex.split(self.query(prompt)))
        self.assertEqual(words, {self.root.name} | hook.terms(prompt))
        self.assertNotIn("memory", words)

    def test_capture_related_searches_keep_vocabulary_and_candidate_guards(self):
        prompt = " ".join("background%02d" % i for i in range(65)) + " Repair OpenCode memory_budget_bytes."
        messages = [dict(role="user", text=prompt)]
        for kind in ("handoff", "durable"):
            with self.subTest(kind=kind):
                records = [dict(record_id=str(i), version=1, kind="decision", **{"class": "A"},
                                body="Use the declared memory allowance.") for i in range(4)]
                records[0]["body"] = hook.workstream_prefix(self.event) + "Budget repair\nPrior decision."
                records[1]["class"] = "B"
                records[2]["body"] = "z" * 65536
                memory = Mock()
                memory.search.return_value = dict(index=[dict(record_id=str(i),
                    summary="OpenCode memory_budget_bytes", pull_arguments=dict(handle=str(i))) for i in range(4)])
                memory.call.side_effect = [dict(selection=dict(record=r)) for r in records]
                if kind == "handoff":
                    result = hook.handoff_candidates(memory, self.event, messages, {})
                    self.assertEqual(memory.search.call_args.kwargs["kinds"], ["note"])
                    self.assertEqual(memory.call.call_count, 2)
                else:
                    result = hook.durable_candidates(memory, self.event, messages)
                    self.assertEqual(memory.call.call_count, 3)
                self.assertEqual(result, [records[0]])
                self.assertIn("memory_budget_bytes", memory.search.call_args.args[0].split())
                self.assertIn("opencode", memory.search.call_args.args[0].split())


if __name__ == "__main__":
    unittest.main()
