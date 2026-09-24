"""Skill routing at the lifecycle boundary, with fake TypeSafe and Kev servers and no model."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SESSION = "caed9473-b01a-41e7-95ce-c3c1f28d66b3"
KEY = "test-key-not-a-secret-0001"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


router = module("skill_router_under_test", ROOT / "integrations/lifecycle/skill_router.py")
hook = module("cairn_lifecycle_for_router", ROOT / "integrations/lifecycle/memory.py")


class FakeLeg:
    """A System One endpoint answering with a fixed Choice, an error, or a stall."""

    def __init__(self):
        self.mode, self.answer, self.requests = "ok", None, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((dict(self.headers), body))
                if outer.mode == "stall":
                    time.sleep(1.0)  # past the client's timeout; the client has gone
                    return
                if outer.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                payload = json.dumps({"answers": {"skill": outer.answer}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def choose(self, name, confidence, runner_up="none"):
        self.answer = {"type": "choice", "choice": name, "confidence": confidence,
                       "probabilities": {name: confidence, runner_up: round(1 - confidence, 4)}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def skill(root, name, description, body, hidden, extra=None):
    directory = root / name
    directory.mkdir(parents=True)
    flag = "disable-model-invocation: true\n" if hidden else ""
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n{flag}---\n{body}\n")
    for relative, text in (extra or {}).items():
        (directory / relative).parent.mkdir(parents=True, exist_ok=True)
        (directory / relative).write_text(text)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skills = self.root / "skills"
        skill(self.skills, "hidden-a", "Use when the database must be force dropped.", "HIDDEN A BODY", True,
              {"references/steps.md": "steps", "__pycache__/x.pyc": "", ".secret/y": ""})
        skill(self.skills, "visible-b", "Use for merge conflicts.", "VISIBLE B BODY", False)
        skill(self.skills, "big-c", "Use for very long procedures.", "BIG " + "x" * 20000, True)
        (self.skills / "no-description").mkdir()
        (self.skills / "no-description" / "SKILL.md").write_text("---\nname: no-description\n---\nbody\n")
        self.env = self.root / "typesafe.env"
        self.env.write_text(f"export TYPESAFE_API_KEY='{KEY}'\n")
        self.jev, self.kev = FakeLeg(), FakeLeg()
        self.addCleanup(self.jev.close)
        self.addCleanup(self.kev.close)
        self.work = self.root / "work"
        self.work.mkdir()
        self.config = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared",
                           state_dir=str(self.root / "state"),
                           skill_router=dict(skills_dir=str(self.skills), jev_env_file=str(self.env), jev_url=self.jev.url,
                                             kev_url=self.kev.url, timeout=0.5, exclude_paths=[str(self.root / "council")]))
        Path(self.config["state_dir"]).mkdir()
        self.event = dict(hook_event_name="UserPromptSubmit", session_id=SESSION, cwd=str(self.work),
                          prompt="drop the test database, postgres says it's in use")
        self.memory = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": 'Cairn lifecycle memory:\n{"selected":[],"index":[]}'}}

    def route(self, event=None, state=None, result=None, config=None):
        state = {} if state is None else state
        job = router.start(config or self.config, event or self.event, state)
        return (job.merge(result if result is not None else {}, state) if job else None), state

    def log_lines(self):
        path = Path(self.config["state_dir"]) / "skill-router.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_catalog_reads_hidden_flag_skips_undescribed_and_caches_on_mtime(self):
        cache = self.root / "cache.json"
        first = {s["name"]: s for s in router.catalog(self.skills, cache)}
        self.assertEqual(set(first), {"hidden-a", "visible-b", "big-c"})
        self.assertTrue(first["hidden-a"]["hidden"])
        self.assertFalse(first["visible-b"]["hidden"])
        with patch.object(router, "parse_frontmatter", side_effect=AssertionError("cache not used")):
            router.catalog(self.skills, cache)
        time.sleep(0.01)
        (self.skills / "visible-b" / "SKILL.md").write_text("---\nname: visible-b\ndescription: Changed.\n---\nx\n")
        changed = {s["name"]: s for s in router.catalog(self.skills, cache)}
        self.assertEqual(changed["visible-b"]["description"], "Changed.")

    def test_wrapped_and_block_descriptions_fold_and_nested_mappings_do_not(self):
        fields, body = router.parse_frontmatter(
            "---\nname: wrapped\ndescription: Use for samtools failures; see body for SIGPIPE\n"
            "  and newline fixes.\nmetadata:\n  trigger: samtools depth\n  nested: value\n"
            "other: >\n  folded block\n  text\nquoted: \"Use it\"\n---\nBODY\n")
        self.assertEqual(fields["description"], "Use for samtools failures; see body for SIGPIPE and newline fixes.")
        self.assertEqual(fields["metadata"], "")
        self.assertEqual(fields["other"], "folded block text")
        self.assertEqual(fields["quoted"], "Use it")
        self.assertEqual(body, "BODY")
        self.assertEqual(router.parse_frontmatter("no header\n"), (None, "no header\n"))

    def test_only_a_bounded_prompt_and_the_directory_name_leave_the_box(self):
        self.jev.choose("hidden-a", 0.95)
        long_prompt = "drop it " + "p" * 5000
        self.route(dict(self.event, prompt=long_prompt, cwd=str(self.work)))
        headers, body = self.jev.requests[0]
        self.assertEqual(set(body["state"]), {"prompt", "cwd"})
        self.assertEqual(body["state"]["prompt"], long_prompt[:1500])
        self.assertEqual(body["state"]["cwd"], "work")
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertIn("none", body["questions"]["skill"]["criteria"])
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertTrue(headers["User-Agent"].startswith("cairn-skill-router"))

    def test_refusals_happen_before_any_request(self):
        council = self.root / "council" / "deep"
        council.mkdir(parents=True)
        link = self.root / "via-link"
        link.symlink_to(self.root / "council")
        cases = [
            (self.config, dict(self.event, cwd=str(council))),
            (self.config, dict(self.event, cwd=str(link))),
            (self.config, dict(self.event, project_path=str(self.root / "council"))),
            (self.config, dict(self.event, prompt="/marker-convert file.pdf")),
            (self.config, dict(self.event, prompt="   ")),
            (self.config, dict(self.event, hook_event_name="PreCompact")),
            (dict(self.config, skill_router=dict(self.config["skill_router"], enabled=False)), self.event),
            ({k: v for k, v in self.config.items() if k != "skill_router"}, self.event),
        ]
        for config, event in cases:
            self.assertIsNone(router.start(config, event, {}), event)
        with patch.dict(os.environ, {"CAIRN_SKILL_ROUTER": "0"}):
            self.assertIsNone(router.start(self.config, self.event, {}))
        self.assertEqual(self.jev.requests + self.kev.requests, [])

    def test_hidden_winner_is_injected_before_memory_with_its_directory_and_files(self):
        self.jev.choose("hidden-a", 0.93)
        result, state = self.route(result=self.memory)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith('Cairn skill router: the owner\'s prompt matches the skill "hidden-a" (p=0.93)'))
        self.assertIn("HIDDEN A BODY", context)
        self.assertNotIn("disable-model-invocation", context)
        self.assertIn("Base directory for this skill: " + str((self.skills / "hidden-a").resolve()), context)
        self.assertIn(str((self.skills / "hidden-a" / "references/steps.md").resolve()), context)
        self.assertNotIn("__pycache__", context)
        self.assertNotIn(".secret", context)
        self.assertTrue(context.endswith(self.memory["hookSpecificOutput"]["additionalContext"]))
        self.assertIn("hidden-a", state["skills_loaded"])
        self.assertEqual(self.log_lines()[-1]["mode"], "inline")

    def test_visible_none_low_confidence_and_repeat_do_not_fire(self):
        for name, confidence, reason in (("visible-b", 0.99, "visible skill"), ("none", 0.99, "none"),
                                         ("hidden-a", 0.69, "below threshold"), ("ghost", 0.99, "unknown choice")):
            self.jev.choose(name, confidence)
            result, state = self.route(result=self.memory)
            self.assertEqual(result, self.memory)
            self.assertNotIn("skills_loaded", state)
            self.assertEqual(self.log_lines()[-1]["reason"], reason)
        self.jev.choose("hidden-a", 0.95)
        state = {}
        first, _ = self.route(state=state)
        again, _ = self.route(state=state)
        self.assertIn("HIDDEN A BODY", first["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(again, {})
        self.assertEqual(self.log_lines()[-1]["reason"], "already loaded")

    def test_kev_answers_when_typesafe_fails_or_has_no_key_and_failure_keeps_memory(self):
        self.kev.choose("hidden-a", 0.9)
        for mode in ("error", "stall"):
            self.jev.mode = mode
            result, _ = self.route(result=self.memory)
            self.assertIn("HIDDEN A BODY", result["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(self.log_lines()[-1]["leg"], "kev")
        headers, body = self.kev.requests[-1]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("model", body)
        self.env.write_text("# no key here\n")
        self.jev.requests.clear()
        result, _ = self.route()
        self.assertEqual(self.jev.requests, [])
        self.assertIn("jev: no key", self.log_lines()[-1]["errors"])
        self.kev.mode = "error"
        result, state = self.route(result=self.memory)
        self.assertEqual(result, self.memory)
        self.assertEqual(self.log_lines()[-1]["reason"], "no leg")

    def test_malformed_answers_fall_through_and_keep_memory(self):
        malformed = [
            {"type": "choice", "choice": "hidden-a", "confidence": 0.95, "probabilities": [0.95]},
            {"type": "choice", "choice": "hidden-a", "confidence": "high", "probabilities": {}},
            {"type": "choice", "choice": ["hidden-a"], "confidence": 0.95},
            {"type": "choice", "choice": "hidden-a", "confidence": 0.95, "probabilities": {"hidden-a": "x"}},
            ["not", "a", "mapping"],
        ]
        self.kev.mode = "error"
        for answer in malformed:
            self.jev.answer = answer
            with patch.object(hook, "recall", return_value=self.memory), patch.object(hook, "Memory"):
                (Path(self.config["state_dir"]) / (SESSION + ".json")).unlink(missing_ok=True)
                self.assertEqual(hook.handle(self.config, self.event), self.memory, answer)
            self.assertEqual(self.log_lines()[-1]["reason"], "no leg", answer)
            self.assertIn("jev: ValueError", self.log_lines()[-1]["errors"], answer)

    def test_any_merge_failure_keeps_memory(self):
        self.jev.choose("hidden-a", 0.95)
        with patch.object(router, "skill_text", side_effect=RuntimeError("boom")):
            result, state = self.route(result=self.memory)
        self.assertEqual(result, self.memory)
        self.assertEqual(state["last_route"]["reason"], "error: RuntimeError")

    def test_oversized_skill_becomes_a_pointer(self):
        self.jev.choose("big-c", 0.97)
        result, _ = self.route(result=self.memory)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Read " + str((self.skills / "big-c").resolve() / "SKILL.md"), context)
        self.assertNotIn("BIG xxx", context)
        self.assertLessEqual(len(context), 9500)
        self.assertEqual(self.log_lines()[-1]["mode"], "pointer")

    def test_skill_that_cannot_fit_beside_memory_is_not_injected(self):
        self.jev.choose("big-c", 0.97)
        crowded = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "m" * 11900}}
        result, state = self.route(result=crowded)
        self.assertEqual(result, crowded)
        self.assertNotIn("skills_loaded", state)
        self.assertEqual(self.log_lines()[-1]["reason"], "no room")

    def test_stalled_legs_bound_the_wait(self):
        self.jev.mode = self.kev.mode = "stall"
        started = time.monotonic()
        result, _ = self.route(result=self.memory)
        self.assertEqual(result, self.memory)
        self.assertLess(time.monotonic() - started, 2 * 0.5 + 0.5 + 0.5)

    def test_missing_router_file_is_recorded_in_session_state(self):
        destination = self.root / "partial"
        destination.mkdir()
        shutil.copyfile(ROOT / "integrations/lifecycle/memory.py", destination / "lifecycle.py")
        installed = module("installed_partial", destination / "lifecycle.py")
        config = dict(self.config, state_dir=str(destination / "state"))
        with patch.object(installed, "recall", return_value=self.memory), patch.object(installed, "Memory"):
            self.assertEqual(installed.handle(config, self.event), self.memory)
        state = json.loads((destination / "state" / (SESSION + ".json")).read_text())
        self.assertTrue(state["last_route"]["reason"].startswith("router unavailable"))

    def test_opencode_gets_the_skill_apart_from_memory(self):
        self.jev.choose("hidden-a", 0.95)
        config = dict(self.config, harness="opencode")
        result, _ = self.route(event=dict(self.event, session_id="ses_abc"), result=self.memory, config=config)
        self.assertEqual(result["hookSpecificOutput"], self.memory["hookSpecificOutput"])
        self.assertIn("HIDDEN A BODY", result["cairn_skill"]["text"])
        self.assertEqual(result["cairn_skill"]["name"], "hidden-a")

    def test_log_holds_no_prompt_and_no_key(self):
        self.jev.choose("hidden-a", 0.95)
        self.route(dict(self.event, prompt="SENSITIVE-PROMPT-TEXT please drop the database"))
        raw = (Path(self.config["state_dir"]) / "skill-router.jsonl").read_text()
        self.assertNotIn("SENSITIVE-PROMPT-TEXT", raw)
        self.assertNotIn(KEY, raw)
        self.assertEqual(self.log_lines()[-1]["choice"], "hidden-a")

    def test_handle_merges_route_with_recall_and_resets_on_session_start(self):
        self.jev.choose("hidden-a", 0.95)
        with patch.object(hook, "recall", return_value=self.memory), patch.object(hook, "Memory"):
            first = hook.handle(self.config, self.event)
            self.assertIn("HIDDEN A BODY", first["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(hook.handle(self.config, self.event), self.memory)
            hook.handle(self.config, dict(self.event, hook_event_name="SessionStart", source="compact", prompt=""))
            again = hook.handle(self.config, self.event)
            self.assertIn("HIDDEN A BODY", again["hookSpecificOutput"]["additionalContext"])
            self.jev.mode = self.kev.mode = "error"
            state_file = Path(self.config["state_dir"]) / (SESSION + ".json")
            state_file.unlink()
            self.assertEqual(hook.handle(self.config, self.event), self.memory)

    def test_router_absent_or_broken_leaves_memory_untouched(self):
        with patch.object(hook, "recall", return_value=self.memory), patch.object(hook, "Memory"):
            plain = {k: v for k, v in self.config.items() if k != "skill_router"}
            self.assertEqual(hook.handle(plain, self.event), self.memory)
            broken = dict(self.config, skill_router=dict(self.config["skill_router"], skills_dir=str(self.root / "missing")))
            self.assertEqual(hook.handle(broken, self.event), self.memory)

    def test_both_installed_file_names_find_the_router(self):
        self.jev.choose("hidden-a", 0.95)
        for installed_name in ("lifecycle.py", "memory.py"):
            destination = self.root / ("install-" + installed_name)
            destination.mkdir()
            shutil.copyfile(ROOT / "integrations/lifecycle/memory.py", destination / installed_name)
            shutil.copyfile(ROOT / "integrations/lifecycle/skill_router.py", destination / "skill_router.py")
            installed = module("installed_" + installed_name.replace(".", "_"), destination / installed_name)
            config = dict(self.config, state_dir=str(destination / "state"))
            with patch.object(installed, "recall", return_value=self.memory), patch.object(installed, "Memory"):
                result = installed.handle(config, self.event)
            self.assertIn("HIDDEN A BODY", result["hookSpecificOutput"]["additionalContext"], installed_name)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = dict(cairn="cairn", claude="claude", socket="socket", token_file="token", repo="shared")

    def assert_installed(self, destination, enabled):
        self.assertEqual((destination / "skill_router.py").read_bytes(),
                         (ROOT / "integrations/lifecycle/skill_router.py").read_bytes())
        config = json.loads((destination / "config.json").read_text())
        self.assertEqual(config.get("skill_router"), {"enabled": True} if enabled else None)

    def test_claude_installer_copies_and_enables_the_router(self):
        claude = module("install_claude_for_router", ROOT / "scripts/install-claude-hooks.py")
        for enabled in (True, False):
            destination = self.root / f"claude-{enabled}"
            claude.install(self.root / f"settings-{enabled}.json", destination, self.base, skill_router=enabled)
            self.assert_installed(destination, enabled)

    def test_opencode_installer_copies_and_enables_the_router(self):
        opencode = module("install_opencode_for_router", ROOT / "scripts/install-opencode-hooks.py")
        config_dir = self.root / "opencode"
        config_dir.mkdir()
        (config_dir / "cairn.json").write_text(json.dumps(dict(executable="cairn", socket="socket", token_file="token", repo="shared")))
        opencode.install(config_dir, self.root / "opencode-hooks", "claude")
        self.assert_installed(self.root / "opencode-hooks", True)
        self.assertEqual(json.loads((self.root / "opencode-hooks/config.json").read_text())["harness"], "opencode")

    def test_codex_installer_copies_and_enables_the_router(self):
        codex = module("install_codex_for_router", ROOT / "scripts/install-codex-hooks.py")
        with patch.object(codex, "trust"):
            codex.install(self.root / "hooks.json", self.root / "codex-hooks", self.base)
        self.assert_installed(self.root / "codex-hooks", True)


if __name__ == "__main__":
    unittest.main()
