"""Offline decision table for the shared lifecycle hook admission."""
import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hook_admission_tests", HERE / "hook_admission.py")
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)

HOME = "/home/owner"
WORK = "/home/owner/git/project/src"
GIT_ROOT = "/home/owner/git/project"
PROJECT = "/home/owner/elsewhere"
UUID = "0b63c5e2-0c6e-4a3f-9f4f-1f1d4f4a7a11"


def probe(files=(), directories=()):
    base = {HOME, "/home/owner/git", GIT_ROOT, WORK, PROJECT, "/home/owner/.codex", "/home/owner/.claude",
            "/home/owner/accounts/second", "/tmp"}
    return admission.TableProbe(directories=base | set(directories), files=set(files))


def event(name="UserPromptSubmit", **fields):
    base = dict(hook_event_name=name, session_id=UUID, cwd=WORK, model="m")
    base.update(fields)
    return base


class DecisionTableTests(unittest.TestCase):
    def check(self, kind, config, hook, environ, fs, verdict, reason=None, **expect):
        result = admission.decide(kind, config, hook, environ=environ, probe=fs)
        self.assertEqual((result.verdict, result.reason), (verdict, reason), (kind, environ, hook))
        for key, value in expect.items():
            self.assertEqual(getattr(result, key), value, key)
        return result

    def test_environment_guards_per_kind(self):
        cases = [
            ({}, {"memory": "run", "coordination": "run", "inbox_recall": "run"}),
            ({"CAIRN_LIFECYCLE_DISABLED": "1"}, {"memory": "disabled_env", "coordination": "disabled_env", "inbox_recall": "disabled_env"}),
            ({"CAIRN_LIFECYCLE_CHILD": "1"}, {"memory": "disabled_env", "coordination": "disabled_env", "inbox_recall": "disabled_env"}),
            ({"CAIRN_COORDINATION_DISABLED": "1"}, {"memory": "run", "coordination": "coordination_disabled", "inbox_recall": "disabled_env"}),
            ({"CAIRN_WAKE_CONTEXT": "/tmp/wake.json"}, {"memory": "run", "coordination": "wake_context", "inbox_recall": "wake_context"}),
        ]
        for environ, expected in cases:
            for kind, outcome in expected.items():
                with self.subTest(kind=kind, environ=environ):
                    config = dict(harness="codex")
                    result = admission.decide(kind, config, event(), environ=environ, probe=probe())
                    self.assertEqual(result.reason if not result.run else "run", outcome)

    def test_accepted_wake_overrides_child_and_wake_guards_for_coordination_only(self):
        environ = {"CAIRN_LIFECYCLE_CHILD": "1", "CAIRN_WAKE_CONTEXT": "/tmp/wake.json"}
        consulted = []
        def accept():
            consulted.append(True)
            return True
        result = admission.guard("coordination", dict(harness="codex"), event(), environ=environ, wake_accepted=accept)
        self.assertTrue(result.run)
        self.assertEqual(consulted, [True])
        self.assertEqual(admission.guard("coordination", dict(harness="codex"), event(), environ=environ,
                                         wake_accepted=lambda: False).reason, "disabled_env")
        self.assertEqual(admission.guard("coordination", dict(harness="codex"), event(), environ={}).run, True)
        # Coordination disabled wins before the wake is even consulted.
        environ = {"CAIRN_COORDINATION_DISABLED": "1", "CAIRN_WAKE_CONTEXT": "/tmp/wake.json"}
        self.assertEqual(admission.guard("coordination", dict(harness="codex"), event(), environ=environ,
                                         wake_accepted=lambda: (_ for _ in ()).throw(AssertionError("consulted"))).reason,
                         "coordination_disabled")
        # The tool cue never consults a wake file.
        self.assertEqual(admission.guard("coordination", dict(harness="codex"), event("PostToolUse"),
                                         environ={"CAIRN_WAKE_CONTEXT": "/tmp/wake.json"},
                                         wake_accepted=lambda: True).reason, "wake_context")

    def test_other_account_home_skips_per_account_hooks(self):
        config = dict(harness="codex", config_home="/home/owner/accounts/second")
        for kind in ("coordination", "inbox_recall", "memory"):
            with self.subTest(kind=kind):
                self.check(kind, config, event(), {"HOME": HOME}, probe(), "skip", "other_account_home")
                self.check(kind, config, event(), {"CODEX_HOME": "/home/owner/accounts/second"}, probe(), "run")
        claude = dict(harness="claude", config_home="/home/owner/.claude")
        self.check("coordination", claude, event(), {"CLAUDE_CONFIG_DIR": "/home/owner/accounts/second"}, probe(),
                   "skip", "other_account_home")
        self.assertFalse(admission.other_account_home(dict(harness="hermes", config_home="/x"), {}))

    def test_one_privacy_rule(self):
        cases = [
            # (marker path, kind, explicit project_path?) -> excluded?
            (f"{GIT_ROOT}/.cairn-no-memory", "memory", False, True),
            (f"{HOME}/.cairn-no-memory", "memory", False, True),             # any ancestor of cwd
            (f"{PROJECT}/.cairn-no-memory", "memory", True, True),            # explicit project directory
            (f"/home/owner/.cairn-no-memory", "memory", True, True),          # ancestor of the explicit project
            (f"{PROJECT}/.cairn-no-memory", "memory", False, False),          # unrelated directory
            (f"{GIT_ROOT}/.cairn-no-coordination", "memory", False, False),  # never silences memory
            (f"{GIT_ROOT}/.cairn-no-coordination", "coordination", False, True),
            (f"{HOME}/.cairn-no-memory", "coordination", False, True),
            (f"{PROJECT}/.cairn-no-memory", "coordination", True, False),     # presence follows the workspace only
            (f"{GIT_ROOT}/.cairn-no-coordination", "inbox_recall", False, True),
            (f"{PROJECT}/.cairn-no-memory", "inbox_recall", True, True),
            (f"{PROJECT}/.cairn-no-coordination", "inbox_recall", True, True),
            (f"{WORK}/.cairn-no-memory", "inbox_recall", False, True),
        ]
        for marker, kind, explicit, excluded in cases:
            with self.subTest(marker=marker, kind=kind, explicit=explicit):
                hook = event(project_path=PROJECT) if explicit else event()
                config = dict(harness="codex", inbox_recall_binding="/x" if kind == "inbox_recall" else None)
                result = admission.decide(kind, config, hook, environ={}, probe=probe(files=[marker]))
                self.assertEqual(result.reason == "privacy_marker", excluded)
                if excluded:
                    self.assertEqual(result.exclusions, (marker,))

    def test_memory_identity_shapes_are_data(self):
        cases = [("codex", UUID, None), ("claude", UUID, None), ("codex", "thread-one", "host session identity must be a UUID"),
                 ("opencode", "ses_abc123", None), ("opencode", UUID, "invalid OpenCode session identity"),
                 ("hermes", "any printable label", None), ("hermes", "bad\x01", "invalid Hermes session identity"),
                 ("codex", "", "missing or invalid host session identity"), ("codex", "x" * 129, "missing or invalid host session identity")]
        for harness, value, message in cases:
            with self.subTest(harness=harness, value=value[:12]):
                self.assertEqual(admission.shaped_identity_error(harness, value), message)
                result = admission.decide("memory", dict(harness=harness), event(session_id=value), environ={}, probe=probe())
                self.assertEqual(result.message, message)
                self.assertEqual(result.verdict, "invalid" if message else "run")
        self.assertTrue(admission.IDENTITY_MESSAGES >= {m for _, _, m in cases if m})

    def test_coordination_identity_is_any_printable_native_id(self):
        for value, ok in [("thread-one", True), ("é" * 128, True), ("", False), ("x\x00", False), ("a" * 257, False), (None, False)]:
            with self.subTest(value=repr(value)[:12]):
                self.assertEqual(admission.valid_native_session_id(value), ok)
                result = admission.observe(dict(harness="codex"), event(session_id=value), probe=probe())
                self.assertEqual(result.run, ok)
                if not ok:
                    self.assertEqual(result.message, "native conversation ID required")

    def test_event_vocabularies(self):
        memory = dict(harness="codex")
        self.check("memory", memory, event("Notification"), {}, probe(), "skip", "unsupported_event")
        self.check("memory", dict(harness="claude"), event("Stop"), {}, probe(), "skip", "stop_not_boundary")
        self.check("memory", memory, event("Stop", stop_hook_active=True), {}, probe(), "skip", "stop_not_boundary")
        self.check("memory", memory, event("Stop"), {}, probe(), "run")
        self.check("inbox_recall", dict(harness="codex"), event("Stop"), {}, probe(), "skip", "unsupported_event")
        self.check("inbox_recall", dict(harness="codex"), None, {}, probe(), "run")
        obs = admission.observe(dict(harness="codex"), event("Stop"), probe=probe())
        self.assertEqual((obs.phase, obs.project, obs.workspace), ("idle", "src", WORK))
        self.assertEqual(admission.observe(dict(harness="codex"), event("Notification"), probe=probe()).message,
                         "unsupported coordination hook event")
        self.assertEqual(admission.observe(dict(harness="codex"), event("ToolStart"), probe=probe()).message,
                         "tool capture hooks are OpenCode-only")
        self.assertEqual(admission.observe(dict(harness="claude"), event("Interrupt"), probe=probe()).message,
                         "interrupt observation is a Codex-only hook")
        agy = dict(hook_event_name="Stop", conversationId="c1", workspacePaths=[WORK], modelName="m", fullyIdle=False)
        self.assertEqual(admission.observe(dict(harness="agy"), agy, probe=probe()).phase, "busy")
        self.assertEqual(admission.guard("coordination", dict(harness="hermes"), event("PostToolUse"), environ={}).reason,
                         "unsupported_event")
        self.assertEqual(admission.guard("coordination", dict(harness="codex"), event("PostToolUse"), environ={}).phase, "cue")

    def test_workspace_and_ownership_validation(self):
        self.check("memory", dict(harness="codex"), event(cwd="/missing"), {}, probe(), "invalid", "invalid_host",
                   message="host working directory is unavailable")
        self.check("memory", dict(harness="codex"), event(project_path="relative"), {}, probe(), "invalid", "invalid_host",
                   message="explicit project directory is unavailable")
        self.assertEqual(admission.observe(dict(harness="codex"), event(cwd="relative"), probe=probe()).message,
                         "native workspace must be an existing absolute directory")
        self.assertEqual(admission.observe(dict(harness="codex"), event(model="m" * 257), probe=probe()).message,
                         "invalid observed model")
        self.assertEqual(admission.observe(dict(harness="claude"), event(prompt_id="p-1"), probe=probe()).native_turn_id,
                         "claude-channel:p-1")
        self.assertEqual(admission.observe(dict(harness="codex"), event(turn_id="t-1"), probe=probe()).native_turn_id, "t-1")
        self.assertEqual(admission.observe(dict(harness="codex"), event(turn_id="bad\x01"), probe=probe()).message,
                         "invalid native ownership key")
        observation = admission.observe(dict(harness="codex"), event(), probe=probe(files=[f"{GIT_ROOT}/.git"])).observation()
        self.assertEqual(observation["project"], "project")
        self.assertEqual(set(observation), {"native_id", "workspace", "project", "observed_model", "phase", "event", "native_turn_id"})


if __name__ == "__main__":
    unittest.main()
