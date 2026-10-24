#!/usr/bin/env python3
"""Build an offline, disabled, two-account Cairn canary candidate.

This stages candidate files in a new directory and records the selected-account
hook and MCP edits. It never edits account settings, grants, or the running service.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import tomllib


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    ".codex/hooks.json": "02d086bdd332739d7985d8583e5c8c7e63e40fb5e64c5b83d81f7faf3aa78333",
    ".codex-harm/hooks.json": "c880e3668e767ab4181f3ee7494cd25fc8d99e9c96253d74c863308af3d49c26",
    ".claude/settings.json": "1654d27f5da01c0be4ece3cb23c457bf2b696262aff14f20ad80b18c26f1e7dc",
    ".claude-harm/settings.json": "c802af22005f1b1a0f127bfadf7c365c2e9f4c65756a3b2e5d2e8a1fd3d38858",
    ".codex/config.toml": "098ae337713b9052837b834c4a413420fb7ea1042a20128be4ba82505144b6aa",
    ".codex-harm/config.toml": "eac9d873dbe6655bd83d5c6b8014c815e4849a907aa7928fea78295ea5851403",
    # Reobserved 2026-10-24 after preflight drift; Cairn MCP entry remained unchanged.
    ".claude.json": "8e3a2a4d4db56f452ec35d73280444cc43cf066f8c2aaaafd4d2df50490cfb7b",
    ".claude-harm/.claude.json": "e8e4a04f4cac39745937facdf9070f7de1658df8f6df1d99d173e0675884805c",
    ".local/bin/cairn-shared-mcp": "21d8d12e13976031be8bc8c7f1c7f14e95ad2f47c6968370d7c484b24d52fe01",
    ".local/share/cairn/codex-hooks/config.json": "e3d21c2f334bada43918f2d839b1f92e660fb6ac7b419327749fa000acf249d2",
    ".local/share/cairn/claude-hooks/config.json": "62677d97808bfccd8c929318844a7233303d58a28340db54f0b5da4c33823ae4",
    ".local/share/cairn/coordination/bindings/codex-one.json": "1c1e1468bfb8d94d0e219ac8be3560271bbbc324541fbd3a00fba66ab2f907d5",
    ".local/share/cairn/coordination/bindings/claude-one.json": "286ba2ecb76a61b762a9f1ddce8e5d6d064baf4a7d51d3e0bcd14ebe827f3579",
    ".local/share/cairn/deployments/inbox-recall-codex-4b0757a-20260930/binding.json": "8b1f0670fb6f71b8fdfefa49595b7f3b2d6bfb0bb63771fd2f8341ee1af8d120",
    ".local/share/cairn/deployments/inbox-recall-claude-one-2306e44-20260930/binding.json": "d40a15f6cc90933f6b36c509b86daa611b74cbef48aa931fdf21779b3070ead1",
}
COORD_SIBLINGS = ("codex_queue.py", "claude_channel.py", "opencode_queue.py",
                  "hermes_queue.py", "process_scan.py")
EVENTS = {
    "codex": {"memory": ("SessionStart", "UserPromptSubmit", "PreCompact", "Stop"),
              "coordination": ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "Interrupt", "PostToolUse")},
    "claude": {"memory": ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd", "PostToolUse", "PostToolUseFailure"),
               "coordination": ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PostToolUse")},
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def pinned(path):
    return {"path": str(path), "sha256": sha256(path)}


def check_snapshot(home, expected):
    for relative, digest in expected.items():
        path = home / relative
        info = path.stat()
        if (path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o022 or
                sha256(path) != digest):
            raise ValueError(f"installed snapshot drift: {path}")


def replace_exact_hooks(settings, old_memory, old_coordination, new_memory, new_coordination, harness):
    result = copy.deepcopy(settings)
    for kind, old, new in (("memory", old_memory, new_memory),
                           ("coordination", old_coordination, new_coordination)):
        for event in EVENTS[harness][kind]:
            matches = [hook for group in result["hooks"][event]
                       for hook in group["hooks"] if hook.get("command") == old]
            if len(matches) != 1:
                raise ValueError(f"expected one selected {harness} {kind} hook at {event}, got {len(matches)}")
            matches[0]["command"] = new
    remaining = {hook.get("command") for groups in result["hooks"].values()
                 for group in groups for hook in group["hooks"]}
    if old_memory in remaining or old_coordination in remaining:
        raise ValueError(f"unmapped selected {harness} hook remains")
    return result


def selected_command(settings, config_path, kind):
    commands = {hook.get("command") for groups in settings["hooks"].values()
                for group in groups for hook in group["hooks"]
                if hook.get("command") and str(config_path) in shlex.split(hook["command"])}
    if len(commands) != 1:
        raise ValueError(f"expected one selected {kind} command for {config_path}")
    return commands.pop()


def staged_command(old, script, config):
    argv = shlex.split(old)
    if (len(argv) < 4 or not Path(argv[0]).is_absolute() or
            argv.count("--config") != 1 or not Path(argv[1]).is_absolute()):
        raise ValueError("unexpected installed hook command")
    index = argv.index("--config")
    if index != len(argv) - 2:
        raise ValueError("unexpected installed hook arguments")
    argv[1] = str(script)
    argv[index + 1] = str(config)
    return shlex.join(argv)


def stage_account(home, stage, harness):
    account = "codex" if harness == "codex" else "claude"
    settings_path = home / (".codex/hooks.json" if harness == "codex" else ".claude/settings.json")
    old_memory_path = home / f".local/share/cairn/{account}-hooks/config.json"
    old_coord_path = home / f".local/share/cairn/coordination/bindings/{account}-one.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    memory = json.loads(old_memory_path.read_text(encoding="utf-8"))
    coordination = json.loads(old_coord_path.read_text(encoding="utf-8"))
    target = stage / account
    ordinary = target / "ordinary"
    native = target / "coordination"
    bridge_path = target / "inbox_recall.py"
    manifest_path = target / "binding.json"
    memory_script = ordinary / "memory.py"
    coordination_script = native / "coordination.py"
    memory_path = ordinary / "config.json"
    coordination_path = native / "bindings" / (coordination["binding"] + ".json")
    for source, destination in ((ROOT / "integrations/lifecycle/memory.py", memory_script),
                                (ROOT / "integrations/lifecycle/inbox_recall.py", bridge_path),
                                (ROOT / "integrations/lifecycle/coordination.py", coordination_script)):
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        destination.chmod(0o700)
    for sibling in COORD_SIBLINGS:
        destination = native / sibling
        shutil.copyfile(ROOT / "integrations/lifecycle" / sibling, destination)
        destination.chmod(0o600)

    candidate_binary = stage / "bin/cairn"
    memory.update(cairn=str(candidate_binary), state_dir=str(ordinary / "state"), inbox_recall_binding=str(manifest_path),
                  capture_observations=True, recall_view="compact/1")
    if harness == "codex":
        memory["context_lifecycle"] = "compact/1"
    coordination.update(cairn=str(candidate_binary),
                        state_dir=str(native / "state" / coordination["binding"]),
                        inbox_recall_binding=str(manifest_path),
                        compact_inbox_version=1, notice_batch_version=1)
    write_json(memory_path, memory)
    write_json(coordination_path, coordination)
    write_json(manifest_path, {"schema": "cairn.inbox-recall-binding/1", "enabled": False,
                               "bridge": pinned(bridge_path), "engine": pinned(memory_script),
                               "memory_config": pinned(memory_path),
                               "coordination_configs": [pinned(coordination_path)]})
    spec = importlib.util.spec_from_file_location("staged_inbox_recall", bridge_path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    bridge.validate_binding(memory, require_enabled=False)
    bridge.validate_binding(coordination, require_enabled=False)

    old_memory = selected_command(settings, old_memory_path, "memory")
    old_coordination = selected_command(settings, old_coord_path, "coordination")
    candidate = replace_exact_hooks(settings, old_memory, old_coordination,
                                    staged_command(old_memory, memory_script, memory_path),
                                    staged_command(old_coordination, coordination_script, coordination_path), harness)
    candidate_path = target / settings_path.name
    write_json(candidate_path, candidate)
    launcher = target / "bin/cairn-inbox"
    launcher.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    launcher.write_text("#!/bin/sh\nexec env CAIRN_INBOX_ROOT=" + shlex.quote(str(native)) + " " +
                        shlex.quote(shlex.split(old_coordination)[0]) + " " +
                        shlex.quote(str(coordination_script)) + ' inbox compact "$@"\n', encoding="utf-8")
    launcher.chmod(0o700)
    return {"account_settings": str(settings_path), "expected_sha256": sha256(settings_path),
            "candidate_settings": str(candidate_path), "candidate_sha256": sha256(candidate_path),
            "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_path),
            "manifest_enabled": False, "coordination_count": 1,
            "compact_launcher": str(launcher), "compact_launcher_sha256": sha256(launcher),
            "required_path_prefix": str(launcher.parent)}


def stage_mcp(home, destination):
    binary = destination / "bin/cairn"
    codex_path = home / ".codex/config.toml"
    codex_text = codex_path.read_text(encoding="utf-8")
    codex_data = tomllib.loads(codex_text)
    old_codex = codex_data["mcp_servers"]["cairn"]
    if old_codex["command"] != str(home / ".local/bin/cairn") or old_codex["args"][-1] != "--codex-thread":
        raise ValueError("unexpected selected Codex MCP command")
    start = codex_text.index("[mcp_servers.cairn]\n")
    end = codex_text.find("\n[", start + 1)
    if end < 0:
        end = len(codex_text)
    section = codex_text[start:end]
    old_command_line = "command = " + json.dumps(old_codex["command"])
    old_args_line = "args = " + json.dumps(old_codex["args"])
    if section.count(old_command_line) != 1 or section.count(old_args_line) != 1:
        raise ValueError("Codex Cairn MCP section differs from reviewed layout")
    new_args = [*old_codex["args"], "--search-view", "compact/1", "--tool-profile", "compact/1"]
    section = section.replace(old_command_line, "command = " + json.dumps(str(binary)))
    section = section.replace(old_args_line, "args = " + json.dumps(new_args))
    candidate_codex = codex_text[:start] + section + codex_text[end:]
    parsed_codex = tomllib.loads(candidate_codex)
    expected_codex = copy.deepcopy(codex_data)
    expected_codex["mcp_servers"]["cairn"] = dict(old_codex, command=str(binary), args=new_args)
    if parsed_codex != expected_codex:
        raise ValueError("Codex candidate changes another MCP setting")
    codex_candidate = destination / "codex/config.toml"
    codex_candidate.write_text(candidate_codex, encoding="utf-8")
    codex_candidate.chmod(0o600)

    wrapper = home / ".local/bin/cairn-shared-mcp"
    wrapper_text = wrapper.read_text(encoding="utf-8")
    old_exec = f"exec {home}/.local/bin/cairn mcp \\\n"
    old_tail = '  --task "${1:-shared}/$cairn_session_id" --run "$cairn_session_id"\n'
    if wrapper_text.count(old_exec) != 1 or wrapper_text.count(old_tail) != 1:
        raise ValueError("shared Claude MCP wrapper differs from reviewed layout")
    candidate_wrapper = destination / "claude/cairn-canary-mcp"
    candidate_wrapper.write_text(wrapper_text.replace(old_exec, f"exec {shlex.quote(str(binary))} mcp \\\n")
                                 .replace(old_tail, old_tail.rstrip("\n") +
                                          ' \\\n  --search-view compact/1 --tool-profile compact/1\n'), encoding="utf-8")
    candidate_wrapper.chmod(0o700)
    claude_path = home / ".claude.json"
    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    old_claude = claude_data["mcpServers"]["cairn"]
    if old_claude.get("command") != str(wrapper) or old_claude.get("args") != ["claude"]:
        raise ValueError("unexpected selected Claude MCP command")
    claude_data["mcpServers"]["cairn"]["command"] = str(candidate_wrapper)
    claude_candidate = destination / "claude/.claude.json"
    write_json(claude_candidate, claude_data)
    return {"candidate_binary": str(binary), "candidate_binary_present": False,
            "codex": {"account_mcp": str(codex_path), "expected_sha256": sha256(codex_path),
                      "candidate_mcp": str(codex_candidate), "candidate_sha256": sha256(codex_candidate)},
            "claude": {"account_mcp": str(claude_path), "expected_sha256": sha256(claude_path),
                       "candidate_mcp": str(claude_candidate), "candidate_sha256": sha256(claude_candidate),
                       "account_wrapper": str(candidate_wrapper), "wrapper_sha256": sha256(candidate_wrapper)}}


def private_stage_root(home):
    if not home.is_absolute() or home.is_symlink():
        raise ValueError("home must be an absolute, non-symlink directory")
    cache = home / ".cache"
    if not cache.is_dir() or cache.is_symlink():
        raise ValueError("private home cache is required")
    for path in (cache, *cache.parents):
        info = path.stat()
        if (path.is_symlink() or info.st_uid not in (os.getuid(), 0) or info.st_mode & 0o022
                or (path / ".git").exists() or (path / ".git").is_symlink()):
            raise ValueError(f"unsafe staging ancestor: {path}")
    info = cache.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("home cache must be private and owner-owned")
    root = cache / "cairn-token-efficiency-stages"
    if root.is_symlink():
        raise ValueError("stage root must not be a symlink")
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.stat()
    if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or (root / ".git").exists() or (root / ".git").is_symlink()):
        raise ValueError("stage root must be private, owner-owned and outside a repository")
    return root


def stage(home, destination, expected=EXPECTED):
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("stage destination must be a new absolute directory")
    check_snapshot(home, expected)
    root = private_stage_root(home)
    if destination.parent != root or destination.resolve(strict=False).parent != root:
        raise ValueError(f"stage destination must be a direct child of {root}")
    destination.mkdir(parents=True, mode=0o700)
    report = {"schema": "cairn.token-efficiency-canary-stage/1", "activation": "none",
              "accounts": {}}
    try:
        if stat.S_IMODE(destination.stat().st_mode) != 0o700:
            raise ValueError("stage destination must be owner-only")
        for harness in ("codex", "claude"):
            report["accounts"][harness] = stage_account(home, destination, harness)
        report["mcp"] = stage_mcp(home, destination)
        report["source_hashes"] = {name: sha256(ROOT / "integrations/lifecycle" / name)
                                   for name in ("memory.py", "coordination.py", "inbox_recall.py")}
        report["unchanged_account_hashes"] = {
            str(home / relative): sha256(home / relative) for relative in
            (".codex-harm/hooks.json", ".codex-harm/config.toml",
             ".claude-harm/settings.json", ".claude-harm/.claude.json")}
        write_json(destination / "stage.json", report)
    except Exception:
        shutil.rmtree(destination)
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path,
                        help="new offline directory, outside installed account and Cairn paths")
    args = parser.parse_args()
    report = stage(Path.home(), args.destination)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
