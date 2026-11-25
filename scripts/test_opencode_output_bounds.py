"""Verify OpenCode TypeScript adapter bounded outputs, spooling, stderr/exit-code capture, and opt-outs."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]

FAKE_CLI = r'''#!/usr/bin/python3
import json, pathlib, sys

state = json.loads(pathlib.Path(sys.argv[0]).with_name("state.json").read_text(encoding="utf-8"))
argv = sys.argv[1:]
idx = 1
while idx < len(argv) and argv[idx].startswith("--"):
    idx += 2
op = argv[idx]

if state.get("raw_fail"):
    sys.stderr.write(state["raw_fail_stderr"])
    sys.exit(int(state.get("raw_fail_code", 2)))

def ok(data):
    print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": data}))
    sys.exit(0)

if op == "assessments":
    ok(state["assessments"])
elif op == "expand-evidence":
    ok(state["evidence"])
elif op == "agent-directory":
    ok({"agents": state["agents"], "more": False})
elif op == "event-inspect":
    ok(state["event_status"])
elif op == "history":
    ok(state["history"])
elif op == "index":
    ok(state["index"])

sys.stderr.write(f"unexpected op: {op}\n")
sys.exit(3)
'''


class OpenCodeOutputBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opencode = shutil.which("opencode")
        if not cls.opencode:
            raise unittest.SkipTest("opencode binary is not installed on PATH")
        cls._tmp = tempfile.TemporaryDirectory(prefix="cairn-opencode-bounds-")
        cls.root = Path(cls._tmp.name)
        cls.cairn_bin = cls.root / "cairn"
        subprocess.run(
            ["go", "build", "-o", str(cls.cairn_bin), "./cmd/cairn"],
            cwd=REPO_ROOT, check=True, timeout=60,
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def test_bounded_outputs_spooling_stderr_and_opt_outs(self):
        work = self.root / "work"
        work.mkdir()
        subprocess.run(
            [str(self.cairn_bin), "opencode-install", "--project", str(work),
             "--socket", str(self.root / "api.sock"),
             "--token-file", str(self.root / "agent.token"),
             "--repo", "fixture:opencode-bounds", "--messaging"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        settings_path = work / ".opencode/cairn.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["tokens"], 16000)
        self.assertEqual(settings["max_tokens"], 160000)
        self.assertEqual(settings["output_max_bytes"], 12000)

        fake_cli = self.root / "fake-cairn"
        fake_cli.write_text(FAKE_CLI, encoding="utf-8")
        fake_cli.chmod(0o700)
        settings["executable"] = str(fake_cli)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        agent_id = str(uuid.uuid4())
        exec_id = str(uuid.uuid4())
        receipt_id = str(uuid.uuid4())
        handle_id = str(uuid.uuid4())
        evidence_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        record_id = str(uuid.uuid4())

        large_reason = (
            "ASSESS_HEAD: initial observation\n"
            + ("filler assessment detail line for size inflation\n" * 400)
            + "ERROR: assertion failed in verification pass\n"
            + ("trailing assessment context line\n" * 100)
            + "ASSESS_TAIL: end of review"
        )
        large_evidence = (
            "EVIDENCE_HEAD: begin log\n"
            + ("ordinary evidence output line\n" * 550)
            + "panic: fatal evidence failure detected\n"
            + ("trailing evidence padding line\n" * 100)
            + "EVIDENCE_TAIL: end log"
        )
        agents = [
            {
                "agent_id": agent_id,
                "ordinal": i + 1,
                "display_name": f"agent-{i + 1}",
                "inbox": f"agent/{agent_id}",
                "execution_id": exec_id,
                "online": True,
                "last_seen": "2026-11-25T00:00:00Z",
                "metadata": {"harness": "opencode", "project": "cairn", "task_summary": "x" * 350},
            }
            for i in range(60)
        ]
        deliveries = [
            {
                "delivery_id": str(uuid.uuid4()),
                "consumer": f"agent/{agent_id}",
                "state": "completed",
                "diagnosis": {"error": "delivery diag", "detail": "d" * 300},
            }
            for _ in range(50)
        ]
        state = {
            "assessments": [{
                "receipt_id": receipt_id, "version": 1, "task_outcome": "unknown",
                "witness": "testimony", "observer": "agent:test", "method": "test/1",
                "reason": large_reason,
            }],
            "evidence": {
                "receipt_id": receipt_id,
                "credits_remaining": 1,
                "bytes_remaining": 1000,
                "evidence": {"evidence_id": evidence_id, "sha256": "a" * 64, "body": large_evidence},
            },
            "agents": agents,
            "event_status": {
                "event": {
                    "event_id": event_id, "kind": "request", "from": f"agent/{agent_id}",
                    "created_at": "2026-11-25T00:00:00Z",
                    "ref": {"record_id": record_id, "version": 1},
                    "destination": {"type": "agent", "name": f"agent/{agent_id}"},
                },
                "deliveries": deliveries,
                "more": False,
            },
            "history": {
                "historical": True, "record_id": record_id, "current_version": 1,
                "versions": [{"version": 1, "body": "h" * 20000, "payload_available": True}],
            },
            "index": {"schema": "cairn.agent-search/1", "selected": [], "index": [], "bytes_remaining": 0},
        }
        state_path = self.root / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        config = {
            "model": "fixture/probe",
            "provider": {"fixture": {
                "npm": "@ai-sdk/openai-compatible", "name": "Bounds fixture",
                "options": {"baseURL": "http://127.0.0.1:1/v1", "apiKey": "unused"},
                "models": {"probe": {"name": "Probe"}},
            }},
            "permission": {
                "*": "deny",
                **{f"cairn_{n}": "allow" for n in (
                    "assessments", "pull_evidence", "agents", "event_status", "history", "search"
                )},
            },
        }
        config_path = work / "opencode.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        for key, folder in [("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                            ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")]:
            d = work / folder
            d.mkdir(exist_ok=True)
            env[key] = str(d)
        env.update(
            OPENCODE_CONFIG=str(config_path),
            OPENCODE_DISABLE_AUTOUPDATE="true",
            OPENCODE_DISABLE_MODELS_FETCH="true",
            OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
        )

        def invoke(name, args, expect_error=None, extra_env=None):
            run_env = dict(env)
            if extra_env:
                run_env.update(extra_env)
            proc = subprocess.run(
                [self.opencode, "debug", "agent", "build", "--pure", "--tool", "cairn_" + name,
                 "--params", json.dumps(args)],
                cwd=work, env=run_env, capture_output=True, text=True, timeout=60,
            )
            if expect_error is not None:
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(expect_error, proc.stderr + proc.stdout)
                return proc.stderr + proc.stdout
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(json.loads(proc.stdout)["result"]["output"])

        # 1. Bounded tools return excerpt + 0600 full_output_path under XDG_STATE_HOME.
        for name, args, snippet in [
            ("assessments", {"receipt_id": receipt_id}, "ASSESS_TAIL: end of review"),
            ("pull_evidence", {"request_id": str(uuid.uuid4()), "receipt_id": receipt_id, "handle": handle_id,
                               "evidence_id": evidence_id, "expected_sha256": "a" * 64}, "EVIDENCE_TAIL: end log"),
            ("agents", {"limit": 60}, "agent-60"),
            ("event_status", {"event_id": event_id, "agent_id": agent_id, "execution_id": exec_id}, "delivery diag"),
        ]:
            out = invoke(name, args)
            self.assertTrue(out.get("truncated"), (name, out))
            self.assertGreater(out["total_bytes"], 12000)
            self.assertGreater(out["omitted_bytes"], 0)
            self.assertLessEqual(len(json.dumps(out).encode("utf-8")), 12000)
            spool_path = Path(out["full_output_path"])
            self.assertTrue(spool_path.is_relative_to(Path(env["XDG_STATE_HOME"])))
            self.assertEqual(stat.S_IMODE(spool_path.stat().st_mode), 0o600)
            self.assertIn(snippet, spool_path.read_text(encoding="utf-8"))

        # 2. pull_evidence binds to min(tokens, output_max_bytes) by default.
        settings_path.write_text(json.dumps(dict(settings, tokens=4096, output_max_bytes=12000)))
        pe = invoke("pull_evidence", {
            "request_id": str(uuid.uuid4()), "receipt_id": receipt_id, "handle": handle_id,
            "evidence_id": evidence_id, "expected_sha256": "a" * 64,
        })
        self.assertTrue(pe["truncated"])
        self.assertLessEqual(len(json.dumps(pe).encode("utf-8")), 4096)
        settings_path.write_text(json.dumps(settings))

        # 3. Strict retrieval (history) keeps BUDGET_REFUSED.
        invoke("history", {"record_id": record_id, "version": 1}, expect_error="BUDGET_REFUSED")

        # 4. Non-JSON CLI failure preserves exit_code + stderr diagnostics (and spools if oversized).
        state_path.write_text(json.dumps(dict(state, raw_fail=True, raw_fail_code=2,
                                              raw_fail_stderr="fatal stderr diagnostic: socket missing\n")))
        err_out = invoke("assessments", {"receipt_id": receipt_id}, expect_error="exit_code=2")
        self.assertIn("fatal stderr diagnostic: socket missing", err_out)

        huge_err = "HEAD_ERR\n" + ("noise stderr line\n" * 900) + "ERROR: middle failure cause\n" + "TAIL_ERR\n"
        state_path.write_text(json.dumps(dict(state, raw_fail=True, raw_fail_code=7, raw_fail_stderr=huge_err)))
        bounded_err = invoke("assessments", {"receipt_id": receipt_id}, expect_error="exit_code=7")
        self.assertIn("ERROR: middle failure cause", bounded_err)
        self.assertIn(env["XDG_STATE_HOME"], bounded_err)
        state_path.write_text(json.dumps(state))

        # 5. Opt-outs: CAIRN_UNBOUNDED_TOOL_OUTPUT=1, CAIRN_OPENCODE_OUTPUT_MAX_BYTES=0, and output_max_bytes: 0.
        full_env = invoke("assessments", {"receipt_id": receipt_id}, extra_env={"CAIRN_UNBOUNDED_TOOL_OUTPUT": "1"})
        self.assertEqual(full_env[0]["reason"], large_reason)
        full_op = invoke("assessments", {"receipt_id": receipt_id}, extra_env={"CAIRN_OPENCODE_OUTPUT_MAX_BYTES": "0"})
        self.assertEqual(full_op[0]["reason"], large_reason)

        settings_path.write_text(json.dumps(dict(settings, output_max_bytes=0)))
        full_cfg = invoke("pull_evidence", {
            "request_id": str(uuid.uuid4()), "receipt_id": receipt_id, "handle": handle_id,
            "evidence_id": evidence_id, "expected_sha256": "a" * 64,
        })
        self.assertEqual(full_cfg["evidence"]["body"], large_evidence)


if __name__ == "__main__":
    unittest.main()
