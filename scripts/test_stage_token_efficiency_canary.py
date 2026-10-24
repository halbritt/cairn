"""Offline account-isolation checks for the token-efficiency canary candidate."""
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("canary_stage", ROOT / "scripts/stage_token_efficiency_canary.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class CanaryStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cairn-stage-test-", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(mode=0o700)
        (self.home / ".cache").mkdir(mode=0o700)
        self.stage = self.home / ".cache/cairn-token-efficiency-stages/candidate with space"
        self.expected = {}
        common = {"cairn": str(self.home / ".local/bin/cairn"),
                  "socket": str(self.home / "api.sock"),
                  "token_file": str(self.home / "token"), "repo": str(self.home / "repo")}
        for harness in ("codex", "claude"):
            account = self.home / (".codex" if harness == "codex" else ".claude")
            account.mkdir(mode=0o700)
            memory = self.home / f".local/share/cairn/{harness}-hooks/config.json"
            coordination = self.home / f".local/share/cairn/coordination/bindings/{harness}-one.json"
            self.write(memory, dict(common, harness=harness, recall_mode="agent_tools", context_bytes=9500,
                                    state_dir=str(self.home / "old-state/memory"),
                                    inbox_recall_binding=str(self.home / f"old-{harness}-binding.json")))
            self.write(coordination, dict(common, harness=harness, binding=harness + "-one",
                                          config_home=str(account), native_delivery=True,
                                          state_dir=str(self.home / "old-state/coordination"),
                                          inbox_recall_binding=str(self.home / f"old-{harness}-binding.json")))
            hooks = {}
            for event in set(stage.EVENTS[harness]["memory"] + stage.EVENTS[harness]["coordination"]):
                entries = [{"hooks": [{"type": "command", "command": "other-tool --safe"}]}]
                if event in stage.EVENTS[harness]["memory"]:
                    entries.append({"hooks": [{"type": "command", "command": shlex.join(
                        [sys.executable, str(self.home / "old-memory.py"), "--config", str(memory)])}]})
                if event in stage.EVENTS[harness]["coordination"]:
                    entries.append({"hooks": [{"type": "command", "command": shlex.join(
                        [sys.executable, str(self.home / "old-coordination.py"), "hook", "--config", str(coordination)])}]})
                hooks[event] = entries
            settings = account / ("hooks.json" if harness == "codex" else "settings.json")
            self.write(settings, {"hooks": hooks, "unrelated": {"keep": True}})
        self.write(self.home / ".codex-harm/hooks.json", {"hooks": {"Stop": [{"hooks": [{"command": "unchanged"}]}]}})
        self.write(self.home / ".claude-harm/settings.json", {"hooks": {"Stop": [{"hooks": [{"command": "unchanged"}]}]}})
        self.write(self.home / ".claude-harm/.claude.json", {"mcpServers": {"other": {"command": "/bin/true"}}})
        self.write(self.home / ".codex-3/hooks.json", {"hooks": {"Stop": [{"hooks": [{"command": "unchanged"}]}]}})
        self.write_text(self.home / ".codex-3/config.toml", "[mcp_servers.other]\ncommand = \"/bin/true\"\n")
        for name in ("codex-two", "codex-three"):
            self.write(self.home / f".local/share/cairn/coordination/bindings/{name}.json",
                       {"binding": name, "harness": "codex"})
        self.write(self.home / ".local/share/cairn/deployments/inbox-recall-codex-4b0757a-20260930/binding.json",
                   {"enabled": True, "coordination_configs": ["codex-one", "codex-two", "codex-three"]})
        self.write_text(self.home / ".codex/config.toml", """[mcp_servers.cairn]
command = "{binary}"
args = ["mcp", "--socket", "{socket}", "--token-file", "{token}", "--repo", "{repo}", "--codex-thread"]

[mcp_servers.other]
command = "/bin/true"
""".format(binary=common["cairn"], socket=common["socket"], token=common["token_file"], repo=common["repo"]))
        self.write_text(self.home / ".codex-harm/config.toml", "[mcp_servers.other]\ncommand = \"/bin/true\"\n")
        self.write(self.home / ".claude.json", {"mcpServers": {
            "cairn": {"command": str(self.home / ".local/bin/cairn-shared-mcp"), "args": ["claude"]},
            "other": {"command": "/bin/true"}}, "unrelated": {"keep": True},
            "cachedGrowthBookFeaturesAt": 1700000000000})
        self.write_text(self.home / ".local/bin/cairn-shared-mcp", """#!/bin/bash
set -euo pipefail
read -r cairn_session_id < /proc/sys/kernel/random/uuid
exec {binary} mcp \\
  --socket {socket} \\
  --token-file {token} \\
  --repo {repo} \\
  --task "${{1:-shared}}/$cairn_session_id" --run "$cairn_session_id"
""".format(binary=common["cairn"], socket=common["socket"],
           token=common["token_file"], repo=common["repo"]))
        for path in self.home.rglob("*"):
            if path.is_file():
                relative = str(path.relative_to(self.home))
                self.expected[relative] = stage.snapshot_sha256(relative, path.read_bytes())

    def write(self, path, value):
        self.write_text(path, json.dumps(value))

    def write_text(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)

    def test_candidate_is_disabled_and_other_accounts_are_unchanged(self):
        before = {relative: (self.home / relative).read_bytes() for relative in self.expected}
        report = stage.stage(self.home, self.stage, self.expected)
        self.assertEqual(report["activation"], "none")
        for harness in ("codex", "claude"):
            account = self.stage / harness
            binding = json.loads((account / "binding.json").read_text())
            self.assertFalse(binding["enabled"])
            self.assertEqual(len(binding["coordination_configs"]), 1)
            for config in (account / "ordinary/config.json",
                           account / "coordination/bindings" / (harness + "-one.json")):
                self.assertEqual(json.loads(config.read_text())["cairn"], str(self.stage / "bin/cairn"))
            original = json.loads((self.home / (".codex/hooks.json" if harness == "codex" else ".claude/settings.json")).read_text())
            candidate = json.loads((account / ("hooks.json" if harness == "codex" else "settings.json")).read_text())
            self.assertEqual(candidate["unrelated"], original["unrelated"])
            for groups in candidate["hooks"].values():
                self.assertEqual(sum(h["command"] == "other-tool --safe" for group in groups for h in group["hooks"]), 1)
            self.assertNotIn("old-memory.py", json.dumps(candidate))
            self.assertNotIn("old-coordination.py", json.dumps(candidate))
            launcher = account / "bin/cairn-inbox"
            self.assertEqual(report["accounts"][harness]["required_path_prefix"], str(launcher.parent))
            subprocess.run(["sh", "-n", str(launcher)], check=True)
        self.assertEqual(before, {relative: (self.home / relative).read_bytes() for relative in self.expected})
        codex_mcp = (self.stage / "codex/config.toml").read_text()
        self.assertIn("--tool-profile", codex_mcp)
        claude_mcp = json.loads((self.stage / "claude/.claude.json").read_text())
        self.assertEqual(claude_mcp["mcpServers"]["other"], {"command": "/bin/true"})
        self.assertEqual(claude_mcp["mcpServers"]["cairn"]["command"], str(self.stage / "claude/cairn-canary-mcp"))
        self.assertIn(shlex.quote(str(self.stage / "bin/cairn")),
                      (self.stage / "claude/cairn-canary-mcp").read_text())
        subprocess.run(["bash", "-n", str(self.stage / "claude/cairn-canary-mcp")], check=True)

    def test_snapshot_drift_stops_before_stage_is_written(self):
        self.expected[".codex-harm/hooks.json"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "snapshot drift"):
            stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

    def test_codex_only_stages_only_selected_codex_and_reports_unresolved_limits(self):
        before = {relative: (self.home / relative).read_bytes() for relative in self.expected}
        # Claude account drift must not gate or enter a Codex-only candidate.
        self.write(self.home / ".claude.json", {"mcpServers": {"changed": {"command": "/bin/false"}}})
        with mock.patch.dict(stage.os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            report = stage.stage(self.home, self.stage, self.expected, codex_only=True)
        self.assertEqual(set(report["accounts"]), {"codex"})
        self.assertFalse((self.stage / "claude").exists())
        self.assertEqual(report["mcp"]["claude"], {"staged": False})
        self.assertFalse(report["mcp"]["candidate_binary_present"])
        self.assertFalse(report["deployment_limits"]["provider_requests_made"])
        self.assertIn("not verified", report["deployment_limits"]["provider_route"])
        manifest = json.loads((self.stage / "codex/binding.json").read_text())
        self.assertFalse(manifest["enabled"])
        self.assertEqual(len(manifest["coordination_configs"]), 1)
        self.assertIn(str(self.home / ".codex-3/config.toml"), report["unchanged_account_hashes"])
        self.assertIn(str(self.home / ".codex-harm/config.toml"), report["unchanged_account_hashes"])
        self.assertTrue(report["account_selection"]["matches_invocation_home"])
        before[".claude.json"] = (self.home / ".claude.json").read_bytes()
        self.assertEqual(before, {relative: (self.home / relative).read_bytes() for relative in self.expected})

    def test_capture_only_profile_opts_in_only_capture_observations(self):
        with mock.patch.dict(stage.os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            report = stage.stage(self.home, self.stage, self.expected, codex_only=True,
                                 profile="capture-only")

        self.assertEqual(report["profile"], "capture-only")
        self.assertEqual(report["opt_ins"], ["capture_observations"])
        self.assertEqual(set(report["accounts"]), {"codex"})
        self.assertEqual(report["activation"], "none")
        self.assertFalse(report["deployment_limits"]["provider_requests_made"])

        memory = json.loads((self.stage / "codex/ordinary/config.json").read_text())
        self.assertIs(memory["capture_observations"], True)
        self.assertNotIn("recall_view", memory)
        self.assertNotIn("context_lifecycle", memory)
        coordination = json.loads((self.stage / "codex/coordination/bindings/codex-one.json").read_text())
        self.assertNotIn("compact_inbox_version", coordination)
        self.assertNotIn("notice_batch_version", coordination)
        candidate_mcp = stage.tomllib.loads((self.stage / "codex/config.toml").read_text())
        args = candidate_mcp["mcp_servers"]["cairn"]["args"]
        self.assertNotIn("--search-view", args)
        self.assertNotIn("--tool-profile", args)

        claude_stage = self.home / ".cache/cairn-token-efficiency-stages/claude-candidate"
        claude_report = stage.stage(self.home, claude_stage, self.expected, profile="capture-only")
        self.assertEqual(claude_report["opt_ins"], ["capture_observations"])
        claude_config = json.loads((claude_stage / "claude/ordinary/config.json").read_text())
        self.assertIs(claude_config["capture_observations"], True)
        self.assertNotIn("recall_view", claude_config)
        wrapper = (claude_stage / "claude/cairn-canary-mcp").read_text()
        self.assertNotIn("--search-view", wrapper)
        self.assertNotIn("--tool-profile", wrapper)

    def test_capture_only_profile_refuses_an_existing_recall_opt_in(self):
        path = self.home / ".local/share/cairn/codex-hooks/config.json"
        memory = json.loads(path.read_text())
        memory["recall_view"] = "compact/1"
        self.write(path, memory)
        relative = ".local/share/cairn/codex-hooks/config.json"
        self.expected[relative] = stage.sha256(path)

        with mock.patch.dict(stage.os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            with self.assertRaisesRegex(ValueError, "capture-only profile requires baseline without recall_view"):
                stage.stage(self.home, self.stage, self.expected, codex_only=True,
                            profile="capture-only")
        self.assertFalse(self.stage.exists())

    def test_compact_inbox_profile_pairs_capture_with_only_inbox_opt_in(self):
        report = stage.stage(self.home, self.stage, self.expected, profile="compact-inbox")
        self.assertEqual(report["profile"], "compact-inbox")
        self.assertEqual(report["opt_ins"], ["capture_observations", "compact_inbox_version=1"])
        for harness in ("codex", "claude"):
            memory = json.loads((self.stage / harness / "ordinary/config.json").read_text())
            self.assertIs(memory["capture_observations"], True)
            self.assertNotIn("recall_view", memory)
            self.assertNotIn("context_lifecycle", memory)
            coordination = json.loads((self.stage / harness / "coordination/bindings" /
                                       f"{harness}-one.json").read_text())
            self.assertEqual(coordination["compact_inbox_version"], 1)
            self.assertNotIn("notice_batch_version", coordination)
        codex_mcp = stage.tomllib.loads((self.stage / "codex/config.toml").read_text())
        codex_args = codex_mcp["mcp_servers"]["cairn"]["args"]
        self.assertNotIn("--search-view", codex_args)
        self.assertNotIn("--tool-profile", codex_args)
        claude_wrapper = (self.stage / "claude/cairn-canary-mcp").read_text()
        self.assertNotIn("--search-view", claude_wrapper)
        self.assertNotIn("--tool-profile", claude_wrapper)

    def test_two_account_compact_candidate_accepts_explicit_codex_config_baseline(self):
        config = self.home / ".codex/config.toml"
        original = config.read_bytes()
        config.write_bytes(original + b"\n[unrelated]\nkeep = true\n")
        baseline = config.read_bytes()
        report = stage.stage(self.home, self.stage, self.expected,
                             snapshot_codex_config_baseline=True,
                             profile="compact-inbox")
        self.assertEqual(set(report["accounts"]), {"codex", "claude"})
        codex = report["mcp"]["codex"]
        self.assertEqual(Path(codex["baseline_snapshot"]).read_bytes(), baseline)
        candidate_text = Path(codex["candidate_mcp"]).read_text()
        self.assertIn("[unrelated]\nkeep = true", candidate_text)
        parsed = stage.tomllib.loads(candidate_text)
        args = parsed["mcp_servers"]["cairn"]["args"]
        self.assertNotIn("--search-view", args)
        self.assertNotIn("--tool-profile", args)
        self.assertEqual(config.read_bytes(), baseline)

    def test_current_codex_config_baseline_preserves_unrelated_toml_and_reports_exact_hooks(self):
        config_path = self.home / ".codex/config.toml"
        original = config_path.read_bytes()
        # A selected-account local setting changes the whole-file pin while the
        # Cairn table stays structurally valid. Keep this value out of reports.
        original_text = original.decode()
        parsed = stage.tomllib.loads(original_text)
        old_args_line = "args = " + json.dumps(parsed["mcp_servers"]["cairn"]["args"])
        opted_args = parsed["mcp_servers"]["cairn"]["args"] + [
            "--search-view", "compact/1", "--tool-profile", "compact/1"]
        opted_args_line = "args = " + json.dumps(opted_args)
        config_path.write_text(original_text.replace(old_args_line, opted_args_line) +
                               "\n[notice]\nopt_in = true\n")
        baseline = config_path.read_bytes()
        self.expected[".codex/config.toml"] = "0" * 64
        with mock.patch.dict(stage.os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            report = stage.stage(self.home, self.stage, self.expected, codex_only=True,
                                 snapshot_codex_config_baseline=True)
        mcp = report["mcp"]["codex"]
        baseline_path = Path(mcp["baseline_snapshot"])
        self.assertEqual(baseline_path.read_bytes(), baseline)
        self.assertEqual(baseline_path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(mcp["baseline_sha256"], stage.hashlib.sha256(baseline).hexdigest())
        candidate_text = Path(mcp["candidate_mcp"]).read_text()
        candidate = stage.tomllib.loads(candidate_text)
        original_values = stage.tomllib.loads(baseline.decode())
        self.assertEqual(candidate["notice"], original_values["notice"])
        self.assertEqual(candidate["mcp_servers"]["other"], original_values["mcp_servers"]["other"])
        selected_args = candidate["mcp_servers"]["cairn"]["args"]
        self.assertEqual(selected_args.count("--search-view"), 1)
        self.assertEqual(selected_args.count("--tool-profile"), 1)
        start = baseline.decode().index("[mcp_servers.cairn]\n")
        end = baseline.decode().find("\n[", start + 1)
        candidate_end = candidate_text.find("\n[", candidate_text.index("[mcp_servers.cairn]\n") + 1)
        self.assertEqual(candidate_text[:start], baseline.decode()[:start])
        self.assertEqual(candidate_text[candidate_end:], baseline.decode()[end:])
        changes = report["accounts"]["codex"]["hook_changes"]
        self.assertEqual({change["kind"] for change in changes}, {"memory", "coordination"})
        self.assertEqual({change["event"] for change in changes},
                         set(stage.EVENTS["codex"]["memory"] + stage.EVENTS["codex"]["coordination"]))
        closure_path = Path(report["accounts"]["codex"]["pin_closure"])
        self.assertEqual(closure_path.stat().st_mode & 0o777, 0o400)
        closure = json.loads(closure_path.read_text())
        self.assertTrue({"ordinary/memory.py", "coordination/coordination.py", "inbox_recall.py"}
                        <= set(closure["files"]))
        self.assertEqual(stage.sha256(closure_path), report["accounts"]["codex"]["pin_closure_sha256"])
        self.assertEqual(config_path.read_bytes(), baseline)

    def test_current_codex_config_change_during_staging_removes_candidate(self):
        config_path = self.home / ".codex/config.toml"
        real_stage_mcp = stage.stage_mcp

        def mutate_after_read(home, destination, expected, *, codex_only=False,
                              codex_config_baseline=None, profile="full"):
            result = real_stage_mcp(home, destination, expected, codex_only=codex_only,
                                    codex_config_baseline=codex_config_baseline, profile=profile)
            config_path.write_text(config_path.read_text() + "\n[notice]\nother = true\n")
            return result

        with mock.patch.dict(stage.os.environ, {"CODEX_HOME": str(self.home / ".codex")}), \
             mock.patch.object(stage, "stage_mcp", side_effect=mutate_after_read):
            with self.assertRaisesRegex(ValueError, "changed during staging"):
                stage.stage(self.home, self.stage, self.expected, codex_only=True,
                            snapshot_codex_config_baseline=True)
        self.assertFalse(self.stage.exists())

    def test_current_codex_config_baseline_rejects_symlink(self):
        config_path = self.home / ".codex/config.toml"
        contents = config_path.read_bytes()
        config_path.unlink()
        target = self.home / "external-config.toml"
        target.write_bytes(contents)
        config_path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "unsafe selected Codex config baseline"):
            stage.stage(self.home, self.stage, self.expected, codex_only=True,
                        snapshot_codex_config_baseline=True)
        self.assertFalse(self.stage.exists())

    def test_claude_cache_timestamp_may_advance_but_mcp_drift_refuses(self):
        path = self.home / ".claude.json"
        data = json.loads(path.read_text())
        data["cachedGrowthBookFeaturesAt"] += 1
        self.write(path, data)
        stage.check_snapshot(self.home, self.expected)
        report = stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(report["accounts"]["claude"]["manifest_enabled"])
        shutil.rmtree(self.stage)

        data["mcpServers"]["cairn"]["args"] = ["other-account"]
        self.write(path, data)
        with self.assertRaisesRegex(ValueError, "snapshot drift"):
            stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

        data["mcpServers"]["cairn"]["args"] = ["claude"]
        data["cachedGrowthBookFeaturesAt"] = "1700000000002"
        self.write(path, data)
        with self.assertRaisesRegex(ValueError, "timestamp layout changed"):
            stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

    def test_claude_mcp_change_during_staging_refuses_candidate(self):
        path = self.home / ".claude.json"
        real_check = stage.check_snapshot

        def change_after_check(home, expected, relative_paths=None):
            real_check(home, expected, relative_paths)
            data = json.loads(path.read_text())
            data["mcpServers"]["cairn"]["args"] = ["other-account"]
            self.write(path, data)

        with mock.patch.object(stage, "check_snapshot", side_effect=change_after_check):
            with self.assertRaisesRegex(ValueError, "snapshot drift"):
                stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

    def test_selected_hook_change_during_staging_refuses_candidate(self):
        path = self.home / ".codex/hooks.json"
        real_stage_mcp = stage.stage_mcp

        def change_after_copy(home, destination, expected, *, codex_only=False,
                              codex_config_baseline=None, profile="full"):
            result = real_stage_mcp(home, destination, expected, codex_only=codex_only,
                                    codex_config_baseline=codex_config_baseline, profile=profile)
            data = json.loads(path.read_text())
            data["hooks"]["Stop"].append({"hooks": [{"command": "new-unreviewed-hook"}]})
            self.write(path, data)
            return result

        with mock.patch.object(stage, "stage_mcp", side_effect=change_after_copy):
            with self.assertRaisesRegex(ValueError, "snapshot drift"):
                stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

    def test_duplicate_selected_hook_is_rejected_without_candidate(self):
        path = self.home / ".codex/hooks.json"
        data = json.loads(path.read_text())
        data["hooks"]["SessionStart"].append(copy.deepcopy(data["hooks"]["SessionStart"][1]))
        self.write(path, data)
        self.expected[".codex/hooks.json"] = stage.sha256(path)
        root = stage.private_stage_root(self.home)
        marker = root / "prior-candidate"
        marker.write_text("retain")
        with self.assertRaisesRegex(ValueError, "expected one selected codex memory hook"):
            stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())
        self.assertEqual(marker.read_text(), "retain")

    def test_rejects_repository_and_shared_parent_destinations(self):
        repository = self.home / "checkout"
        repository.mkdir()
        (repository / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "direct child"):
            stage.stage(self.home, repository / "candidate", self.expected)
        self.assertFalse((repository / "candidate").exists())
        root = stage.private_stage_root(self.home)
        (root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "outside a repository"):
            stage.stage(self.home, self.stage, self.expected)
        (root / ".git").rmdir()
        (self.home / ".cache").chmod(0o775)
        with self.assertRaisesRegex(ValueError, "unsafe staging ancestor"):
            stage.stage(self.home, self.stage, self.expected)
        self.assertFalse(self.stage.exists())

    def test_rejects_tmp_destination(self):
        destination = Path(tempfile.gettempdir()) / "cairn-stage-unsafe-destination"
        with self.assertRaisesRegex(ValueError, "direct child"):
            stage.stage(self.home, destination, self.expected)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
