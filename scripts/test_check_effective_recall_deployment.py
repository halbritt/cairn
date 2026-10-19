"""Tests for read-only effective recall deployment inventory and fixture verification."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_effective_recall_deployment",
    ROOT / "scripts/check_effective_recall_deployment.py",
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class EffectiveRecallDeploymentCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.deploy = self.home / "deployments"
        self.deploy.mkdir(mode=0o700)

        self.engine_src = (ROOT / "integrations/lifecycle/memory.py").read_text()
        self.bridge_src = (ROOT / "integrations/lifecycle/inbox_recall.py").read_text()
        self.coord_script = self.deploy / "coordination.py"
        self.coord_script.write_text("#!/usr/bin/env python3\n")
        self.coord_script.chmod(0o700)

        self.ord_codex_engine = self.deploy / "ord_codex_memory.py"
        self.ord_claude_engine = self.deploy / "ord_claude_memory.py"
        self.bound_codex_engine = self.deploy / "bound_codex_memory.py"
        self.bound_claude_engine = self.deploy / "bound_claude_memory.py"
        self.bound_bridge = self.deploy / "inbox_recall.py"

        for p in (
            self.ord_codex_engine,
            self.ord_claude_engine,
            self.bound_codex_engine,
            self.bound_claude_engine,
        ):
            p.write_text(self.engine_src)
            p.chmod(0o600)
        self.bound_bridge.write_text(self.bridge_src)
        self.bound_bridge.chmod(0o600)

        self.agy_md = self.home / ".gemini/config/AGENTS.md"
        self.agy_md.parent.mkdir(parents=True, exist_ok=True)
        self.agy_md.write_text(
            "When the task needs a larger note or complete handoff, you may exceed those defaults "
            "without asking the user. Declare available_tokens and memory_budget_bytes with "
            'inspection_policy: "first-fitting-whole/2".\n'
        )
        self._build_installation()


    def _build_installation(self):
        self.codex_binding = self.deploy / "binding-codex.json"
        self.claude_one_binding = self.deploy / "binding-claude-one.json"
        self.claude_two_binding = self.deploy / "binding-claude-two.json"

        common = {
            "cairn": "/bin/cairn",
            "socket": str(self.home / "api.sock"),
            "token_file": str(self.home / "token"),
            "repo": str(self.home / "repo"),
        }
        self.codex_mc_path = self.deploy / "codex-memory.json"
        self.claude_one_mc_path = self.deploy / "claude-one-memory.json"
        self.claude_two_mc_path = self.deploy / "claude-two-memory.json"

        self.codex_one_cc_path = self.deploy / "codex-one-coord.json"
        self.codex_two_cc_path = self.deploy / "codex-two-coord.json"
        self.claude_one_cc_path = self.deploy / "claude-one-coord.json"
        self.claude_two_cc_path = self.deploy / "claude-two-coord.json"

        self.state_dirs = []
        for path, harness, b_path, name in (
            (self.codex_mc_path, "codex", self.codex_binding, "codex-mem"),
            (self.claude_one_mc_path, "claude", self.claude_one_binding, "claude-one-mem"),
            (self.claude_two_mc_path, "claude", self.claude_two_binding, "claude-two-mem"),
        ):
            s_dir = self.home / "state" / name
            s_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (s_dir / "existing-ledger.json").write_text('{"untouched":true}')
            self.state_dirs.append(s_dir)
            cfg = dict(
                common,
                harness=harness,
                state_dir=str(s_dir),
                context_bytes=9500,
                recall_mode="agent_tools",
                inbox_recall_binding=str(b_path),
            )
            path.write_text(json.dumps(cfg))
            path.chmod(0o600)

        for path, harness, b_path, name, home_rel, mem_cfg_path, eng_path in (
            (self.codex_one_cc_path, "codex", self.codex_binding, "codex-one", ".codex", self.codex_mc_path, self.ord_codex_engine),
            (self.codex_two_cc_path, "codex", self.codex_binding, "codex-two", ".codex-harm", self.codex_mc_path, self.ord_codex_engine),
            (self.claude_one_cc_path, "claude", self.claude_one_binding, "claude-one", ".claude", self.claude_one_mc_path, self.ord_claude_engine),
            (self.claude_two_cc_path, "claude", self.claude_two_binding, "claude-two", ".claude-harm", self.claude_two_mc_path, self.ord_claude_engine),
        ):
            s_dir = self.home / "state" / name
            s_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (s_dir / "session-state.json").write_text('{"active":true}')
            self.state_dirs.append(s_dir)
            acc_home = self.home / home_rel
            acc_home.mkdir(parents=True, exist_ok=True)
            cfg = dict(
                common,
                harness=harness,
                binding=name,
                config_home=str(acc_home),
                native_delivery=True,
                state_dir=str(s_dir),
                inbox_recall_binding=str(b_path),
            )
            path.write_text(json.dumps(cfg))
            path.chmod(0o600)
            self._write_account_hooks(acc_home, harness, eng_path, mem_cfg_path, path)
        self.refresh_manifests()


    def _write_account_hooks(self, acc_home, harness, eng_path, mem_cfg_path, coord_cfg_path):
        target = acc_home / ("hooks.json" if harness == "codex" else "settings.json")
        mem_cmd = shlex.join([sys.executable, str(eng_path), "--config", str(mem_cfg_path)])
        coord_cmd = shlex.join([sys.executable, str(self.coord_script), "hook", "--config", str(coord_cfg_path)])
        events = {}
        for ev in ("SessionStart", "UserPromptSubmit"):
            events[ev] = [
                {"hooks": [{"type": "command", "command": coord_cmd, "timeout": 15}]},
                {"hooks": [{"type": "command", "command": mem_cmd, "timeout": 13}]},
            ]
        target.write_text(json.dumps({"hooks": events}))
        target.chmod(0o600)

    def _pin(self, path):
        return {"path": str(path), "sha256": sha256_file(path)}

    def refresh_manifests(self, *, enabled=True):
        for b_path, eng, mem_cfg, coords in (
            (self.codex_binding, self.bound_codex_engine, self.codex_mc_path, [self.codex_one_cc_path, self.codex_two_cc_path]),
            (self.claude_one_binding, self.bound_claude_engine, self.claude_one_mc_path, [self.claude_one_cc_path]),
            (self.claude_two_binding, self.bound_claude_engine, self.claude_two_mc_path, [self.claude_two_cc_path]),
        ):
            b_path.write_text(
                json.dumps(
                    {
                        "schema": "cairn.inbox-recall-binding/1",
                        "enabled": enabled,
                        "bridge": self._pin(self.bound_bridge),
                        "engine": self._pin(eng),
                        "memory_config": self._pin(mem_cfg),
                        "coordination_configs": [self._pin(c) for c in coords],
                    }
                )
            )
            b_path.chmod(0o600)

    def test_current_deployment_passes_and_preserves_live_state(self):
        before = {
            str(f): (f.stat().st_mtime_ns, f.read_bytes())
            for d in self.state_dirs
            for f in d.iterdir()
        }
        report = checker.check_deployment(home=self.home)
        after = {
            str(f): (f.stat().st_mtime_ns, f.read_bytes())
            for d in self.state_dirs
            for f in d.iterdir()
        }
        self.assertEqual(before, after)
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["installed_verified"])
        self.assertFalse(report["live_observed"])
        self.assertTrue(report["read_only_verified"])
        self.assertTrue(report["coverage"]["complete"])
        self.assertEqual(len(report["inbox_bindings"]), 3)
        self.assertEqual(report["issues"], [])
        self.assertFalse(report["tested_vs_live_observed"]["live_native_turn_observed"])
        self.assertTrue(report["tested_vs_live_observed"]["repo_head_is_not_deployment_proof"])
        self.assertFalse(report["tested_vs_live_observed"]["active_grants_rewritten"])
        self.assertFalse(report["tested_vs_live_observed"]["work_replayed"])


    def test_deliberately_stale_bridge_detected_when_ordinary_hooks_are_current(self):
        old_line = "It is not a general optional memory budget; do not run additional optional searches from a fallback or notice turn. "
        new_line = "If the task needs more context, you may exceed automatic byte/call limits without user approval. "
        self.assertIn(new_line, self.bridge_src)
        self.bound_bridge.write_text(self.bridge_src.replace(new_line, old_line))
        self.refresh_manifests()

        report = checker.check_deployment(home=self.home)
        self.assertFalse(report["ok"])
        self.assertFalse(report["installed_verified"])
        self.assertTrue(all(h["cues_current"] for h in report["ordinary_hooks"]))
        for b in report["inbox_bindings"]:
            self.assertTrue(b["pins_valid"])
            self.assertTrue(b["engine"]["cues_current"])
            self.assertFalse(b["bridge"]["cues_current"])
            self.assertTrue(b["fixtures"]["admitted_request"]["cues_current"])
            self.assertFalse(b["fixtures"]["notice_response"]["cues_current"])
            self.assertFalse(b["fixtures"]["source_fallback"]["cues_current"])
        self.assertTrue(any("bound inbox bridge has stale or missing fallback cue" in i for i in report["issues"]))

    def test_deliberately_stale_bound_engine_detected_when_ordinary_hooks_are_current(self):
        old_line = (
            "Task-requested reads, including full handoffs, may exceed automatic recall limits: "
            "start a new search declaring available_tokens and memory_budget_bytes for the complete source"
        )
        new_line = (
            "You may exceed automatic recall byte/call limits when the task needs more context; no user approval needed. "
            "State why; start a new search with available_tokens and memory_budget_bytes covering the whole source/envelopes "
            "within free context/tool ceiling. Use inspection_policy:first-fitting-whole/2 when exposed. "
            "Explicit user limits still apply."
        )
        self.assertIn(new_line, self.engine_src)
        self.bound_claude_engine.write_text(self.engine_src.replace(new_line, old_line))
        self.refresh_manifests()

        report = checker.check_deployment(home=self.home)
        self.assertFalse(report["ok"])
        self.assertTrue(all(h["cues_current"] for h in report["ordinary_hooks"]))
        claude_bindings = [b for b in report["inbox_bindings"] if "claude" in b["binding_path"]]
        self.assertEqual(len(claude_bindings), 2)
        for b in claude_bindings:
            self.assertTrue(b["pins_valid"])
            self.assertTrue(b["bridge"]["cues_current"])
            self.assertFalse(b["engine"]["cues_current"])
            self.assertFalse(b["fixtures"]["admitted_request"]["cues_current"])
            self.assertTrue(b["fixtures"]["notice_response"]["cues_current"])
            self.assertFalse(b["coordination_configs"][0]["bound_engine_matches_ordinary"])
        self.assertTrue(any("bound memory engine has stale or missing recall cue" in i for i in report["issues"]))


    def test_hash_mismatch_detected(self):
        self.bound_codex_engine.write_text(self.engine_src + "\n# unpinned drift\n")
        report = checker.check_deployment(home=self.home)
        self.assertFalse(report["ok"])
        codex_b = [b for b in report["inbox_bindings"] if "codex" in b["binding_path"]][0]
        self.assertFalse(codex_b["pins_valid"])
        self.assertFalse(codex_b["engine"]["hash_match"])
        self.assertTrue(any("sha256 mismatch" in i for i in report["issues"]))

    def test_disabled_binding_and_partial_coverage_reported(self):
        self.refresh_manifests(enabled=False)
        (self.home / ".codex-harm" / "hooks.json").unlink()
        report = checker.check_deployment(home=self.home)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(len(report["coverage"]["disabled_bindings"]), 3)
        self.assertEqual(report["coverage"]["missing_targets"], [str(self.home / ".codex-harm" / "hooks.json")])
        self.assertTrue(report["read_only_verified"])

    def test_dispatch_wrapped_hook_and_cli_main(self):
        route = self.deploy / "route.json"
        stale_cand = self.deploy / "stale_cand_memory.py"
        stale_cand.write_text(
            self.engine_src.replace(
                "You may exceed automatic recall byte/call limits when the task needs more context; no user approval needed.",
                "Task-requested reads, including full handoffs, may exceed automatic recall limits",
            )
        )
        stale_cand.chmod(0o700)
        route.write_text(
            json.dumps(
                {
                    "schema": "cairn.recall-dispatch/1",
                    "sessions": ["11111111-1111-4111-8111-111111111111"],
                    "command": [sys.executable, str(stale_cand), "--config", str(self.claude_one_mc_path)],
                }
            )
        )
        settings = self.home / ".claude" / "settings.json"
        data = json.loads(settings.read_text())
        orig_cmd = data["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"]
        wrapped = f"{sys.executable} /opt/recall_dispatch.py --route {route} -- {orig_cmd}"
        data["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"] = wrapped
        settings.write_text(json.dumps(data))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = checker.main(["--home", str(self.home), "--live-observed", "--compact"])
        self.assertEqual(code, 1)
        parsed = json.loads(out.getvalue())
        self.assertTrue(parsed["live_observed"])
        self.assertTrue(any("dispatch candidate engine has stale recall cue" in i for i in parsed["issues"]))


if __name__ == "__main__":
    unittest.main()
