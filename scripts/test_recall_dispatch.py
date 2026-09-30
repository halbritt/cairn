"""recall_dispatch routes only allowlisted recall events and otherwise execs the original."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "integrations/lifecycle/recall_dispatch.py"
spec = importlib.util.spec_from_file_location("install_recall_dispatch", ROOT / "scripts/install-recall-dispatch.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

# A fake engine: records its identity, argv, config and exact stdin, then exits
# with FAKE_EXIT or waits for a signal.
ENGINE = r'''import hashlib, json, os, sys, time
data = sys.stdin.buffer.read()
config = sys.argv[sys.argv.index("--config") + 1]
print(json.dumps({"engine": os.path.basename(os.path.dirname(os.path.abspath(__file__))), "pid": os.getpid(),
    "config": config, "stdin_sha256": hashlib.sha256(data).hexdigest(), "stdin_len": len(data)}), flush=True)
sys.stderr.write("engine stderr\n")
if os.environ.get("FAKE_SLEEP"):
    time.sleep(30)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''
SHARED = dict(repo="collection", socket="/fixture/api.sock", token_file="/fixture/token", state_dir="/fixture/state")


class RecallDispatch(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, extra in (("original", dict(model="claude-haiku")), ("candidate", dict(model="claude-haiku", recall_mode="agent_tools"))):
            (self.root / name).mkdir()
            (self.root / name / "lifecycle.py").write_text(ENGINE)
            (self.root / name / "config.json").write_text(json.dumps(dict(SHARED, **extra)))
        self.original = [sys.executable, str(self.root / "original/lifecycle.py"), "--config", str(self.root / "original/config.json")]
        self.candidate = [sys.executable, str(self.root / "candidate/lifecycle.py"), "--config", str(self.root / "candidate/config.json")]
        self.route = self.root / "route.json"
        self.write_route(["ses-target"])

    def write_route(self, sessions, command=None, **extra):
        self.route.write_text(json.dumps(dict(schema="cairn.recall-dispatch/1", sessions=sessions,
                                              command=command or self.candidate, **extra)))

    def run_hook(self, event, session="ses-other", payload=None, env=None):
        body = payload if payload is not None else json.dumps(dict(hook_event_name=event, session_id=session, cwd="/tmp", prompt="p")).encode()
        process = subprocess.Popen([sys.executable, str(DISPATCH), "--route", str(self.route), "--", *self.original],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=dict(os.environ, **(env or {})))
        out, err = process.communicate(body, timeout=20)
        record = json.loads(out) if out.strip() else None
        return process, record, err.decode(), body

    def test_only_allowlisted_recall_events_reach_the_candidate(self):
        for event in ("SessionStart", "UserPromptSubmit"):
            _, record, _, _ = self.run_hook(event, "ses-target")
            self.assertEqual(record["engine"], "candidate", event)
        for event, session in (("UserPromptSubmit", "ses-other"), ("PostToolUse", "ses-target"), ("PreCompact", "ses-target"),
                               ("SessionEnd", "ses-target"), ("Stop", "ses-target")):
            _, record, _, _ = self.run_hook(event, session)
            self.assertEqual(record["engine"], "original", (event, session))
        # Exact IDs only: a prefix or differently cased ID is not targeted.
        for session in ("ses-targe", "SES-TARGET", "ses-target "):
            self.assertEqual(self.run_hook("UserPromptSubmit", session)[1]["engine"], "original", session)

    def test_payload_output_exit_and_process_identity_pass_through(self):
        big = json.dumps(dict(hook_event_name="UserPromptSubmit", session_id="ses-target", prompt="x" * 300_000)).encode()
        for payload, engine in ((big, "candidate"), (big.replace(b"ses-target", b"ses-other"), "original")):
            process, record, err, body = self.run_hook(None, payload=payload, env=dict(FAKE_EXIT="2"))
            self.assertEqual(record["engine"], engine)
            self.assertEqual(record["stdin_len"], len(body))
            self.assertEqual(record["stdin_sha256"], __import__("hashlib").sha256(body).hexdigest())
            self.assertEqual(process.returncode, 2)  # exit status reaches the host unchanged
            self.assertEqual(err, "engine stderr\n")
            self.assertEqual(record["pid"], process.pid)  # exec: the engine is the hook process

    def test_signal_reaches_the_engine_directly(self):
        process = subprocess.Popen([sys.executable, str(DISPATCH), "--route", str(self.route), "--", *self.original],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=dict(os.environ, FAKE_SLEEP="1"))
        process.stdin.write(json.dumps(dict(hook_event_name="UserPromptSubmit", session_id="ses-target")).encode())
        process.stdin.close()
        line = process.stdout.readline()
        self.assertEqual(json.loads(line)["pid"], process.pid)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=10), -signal.SIGTERM)
        process.stdout.close()
        process.stderr.close()

    def test_missing_route_and_malformed_payload_run_the_original(self):
        for payload in (b"not json", b"[]", b""):
            self.assertEqual(self.run_hook(None, payload=payload)[1]["engine"], "original", payload)
        self.route.unlink()
        self.assertEqual(self.run_hook("UserPromptSubmit", "ses-target")[1]["engine"], "original")

    def test_invalid_route_fails_visibly(self):
        bad = [dict(schema="other", sessions=["ses-target"], command=self.candidate),
               dict(schema="cairn.recall-dispatch/1", sessions=["ses-*"], command=self.candidate),
               dict(schema="cairn.recall-dispatch/1", sessions=[], command=self.candidate),
               dict(schema="cairn.recall-dispatch/1", sessions=["a", "a"], command=self.candidate),
               dict(schema="cairn.recall-dispatch/1", sessions=["ses-target"], command=[]),
               dict(schema="cairn.recall-dispatch/1", sessions=["ses-target"], command=self.candidate, events=["Stop"])]
        for route in bad:
            self.route.write_text(json.dumps(route))
            process, record, err, _ = self.run_hook("UserPromptSubmit", "ses-other")
            self.assertEqual((process.returncode, record), (1, None), route)
            self.assertIn("Cairn recall dispatch:", err)
        self.route.write_text("{broken")
        self.assertEqual(self.run_hook("SessionStart")[0].returncode, 1)

    def test_invalid_candidate_fails_only_the_targeted_session(self):
        drifted = dict(SHARED, state_dir="/elsewhere")
        (self.root / "candidate/config.json").write_text(json.dumps(drifted))
        process, record, err, _ = self.run_hook("UserPromptSubmit", "ses-target")
        self.assertEqual((process.returncode, record), (1, None))
        self.assertIn("state_dir", err)
        self.assertEqual(self.run_hook("UserPromptSubmit", "ses-other")[1]["engine"], "original")
        (self.root / "candidate/config.json").write_text(json.dumps(SHARED))
        for command in ([sys.executable, str(self.root / "missing.py"), "--config", str(self.root / "candidate/config.json")],
                        ["python3", str(self.root / "candidate/lifecycle.py"), "--config", str(self.root / "candidate/config.json")],
                        [sys.executable, str(self.root / "candidate/lifecycle.py")]):
            self.write_route(["ses-target"], command)
            process, record, err, _ = self.run_hook("UserPromptSubmit", "ses-target")
            self.assertEqual((process.returncode, record), (1, None), command)
            self.assertEqual(self.run_hook("UserPromptSubmit", "ses-other")[1]["engine"], "original")


class InstallRollback(unittest.TestCase):
    def test_install_wraps_only_recall_hooks_and_rollback_restores_exact_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = shlex.join([sys.executable, "/opt/claude-hooks/lifecycle.py", "--config", "/opt/claude-hooks/config.json"])
            other = {"hooks": [{"type": "command", "command": "owner-hook", "timeout": 5}]}
            hooks = {event: [other, {"hooks": [{"type": "command", "command": original, "timeout": 150 if event in ("PreCompact", "SessionEnd") else 13}]}]
                     for event in ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd", "PostToolUse", "PostToolUseFailure")}
            settings = root / "settings.json"
            settings.write_text(json.dumps({"model": "owner", "hooks": hooks}))
            before = json.loads(settings.read_text())
            route = root / "route.json"
            route.write_text(json.dumps(dict(schema="cairn.recall-dispatch/1", sessions=["ses-target"], command=[sys.executable, "x.py"])))
            self.assertEqual(installer.install(settings, root / "dest", route, original), 2)
            self.assertEqual(installer.install(settings, root / "dest", route, original), 0)  # idempotent
            after = json.loads(settings.read_text())
            for event, groups in after["hooks"].items():
                hook = groups[1]["hooks"][0]
                self.assertEqual(groups[0], other)
                self.assertEqual(hook["timeout"], before["hooks"][event][1]["hooks"][0]["timeout"])
                if event in ("SessionStart", "UserPromptSubmit"):
                    self.assertIn("recall_dispatch.py", hook["command"])
                    self.assertTrue(hook["command"].endswith("-- " + original))
                else:
                    self.assertEqual(hook["command"], original)
            # A concurrent unrelated edit survives rollback.
            after["model"] = "changed-meanwhile"
            after["hooks"]["Stop"] = [other]
            settings.write_text(json.dumps(after))
            self.assertEqual(installer.rollback(settings, root / "dest"), 2)
            final = json.loads(settings.read_text())
            self.assertEqual(final["model"], "changed-meanwhile")
            self.assertEqual(final["hooks"]["Stop"], [other])
            final["model"] = before["model"]
            del final["hooks"]["Stop"]
            self.assertEqual(final, before)
            self.assertTrue((root / "settings.json.before-recall-dispatch").exists())

    def test_install_refuses_invalid_route_or_missing_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text(json.dumps({"hooks": {}}))
            route = root / "route.json"
            route.write_text(json.dumps(dict(schema="cairn.recall-dispatch/1", sessions=["ses-*"], command=["x"])))
            with self.assertRaises(installer.dispatch.DispatchError):
                installer.install(settings, root / "dest", route, "python3 /x.py --config /c.json")
            route.unlink()
            with self.assertRaises(SystemExit):
                installer.install(settings, root / "dest", route, "python3 /x.py --config /c.json")
            self.assertEqual(json.loads(settings.read_text()), {"hooks": {}})


if __name__ == "__main__":
    unittest.main()
