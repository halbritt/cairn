#!/usr/bin/env python3
"""Install Cairn lifecycle memory hooks alongside existing Codex hooks.

Retrieval runs at SessionStart and UserPromptSubmit. Capture runs at PreCompact
and, in the background, at Stop once enough new dialogue has accumulated:
Codex SessionEnd is synchronous with a 1 s default and 3 s maximum, too short
for checkpoint selection, so exit capture is not guaranteed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_LIMIT = 12000  # matches the engine's CONTEXT_BYTES retrieval budget
EVENTS = {
    "SessionStart": dict(timeout=13, additionalContextLimit=CONTEXT_LIMIT),
    "UserPromptSubmit": dict(timeout=13, additionalContextLimit=CONTEXT_LIMIT),
    "PreCompact": dict(timeout=150),
    "Stop": dict(timeout=150, **{"async": True}),
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as out:
        temporary = Path(out.name)
        try:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.write("\n")
            out.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def trust(config_home, hooks_path, command):
    """Trust exactly this installation's reviewed hook definitions through Codex."""
    spec = importlib.util.spec_from_file_location("install_agent_coordination", ROOT / "scripts/install-agent-coordination.py")
    coordination = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coordination)
    coordination.trust_codex_hooks(config_home, hooks_path, command, expected=len(EVENTS))


def install(hooks_path, destination, config):
    data = json.loads(hooks_path.read_text()) if hooks_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    script = destination / "lifecycle.py"
    config_path = destination / "config.json"
    command = shlex.join([sys.executable, str(script), "--config", str(config_path)])
    for event, settings in EVENTS.items():
        groups = hooks.setdefault(event, [])
        # Replace only this installation's exact command; retain every other hook.
        for group in groups:
            group["hooks"] = [hook for hook in group["hooks"] if hook.get("command") != command]
        groups[:] = [group for group in groups if group["hooks"]]
        groups.append({"hooks": [dict(type="command", command=command, **settings)]})
    original = hooks_path.read_bytes() if hooks_path.exists() else None
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(ROOT / "integrations/lifecycle/memory.py", script)
    script.chmod(0o700)
    write_json(config_path, dict(config, harness="codex", state_dir=str(destination / "state")))
    backup = hooks_path.with_name(hooks_path.name + ".before-cairn-lifecycle")
    if original is not None and not backup.exists():
        backup.write_bytes(original)
        backup.chmod(0o600)
    write_json(hooks_path, data)
    trust(hooks_path.parent, hooks_path, command)
    return command


def main():
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hooks", type=Path, default=codex_home / "hooks.json",
                        help="Codex hooks.json of the account to install into (default: $CODEX_HOME/hooks.json)")
    parser.add_argument("--destination", type=Path, default=home / ".local/share/cairn/codex-hooks")
    parser.add_argument("--cairn", default=shutil.which("cairn"))
    parser.add_argument("--claude", default=shutil.which("claude"),
                        help="Claude Code CLI used for checkpoint selection, as in the other lifecycle adapters")
    parser.add_argument("--model", default="claude-sonnet-5", help="selection model")
    parser.add_argument("--socket", type=Path, default=home / ".local/share/cairn/api.sock")
    parser.add_argument("--token-file", type=Path, default=home / ".local/share/cairn/hosted-agent.token")
    parser.add_argument("--repo", default=str(home / "git/cairn"))
    args = parser.parse_args()
    if not args.cairn or not args.claude:
        parser.error("installed cairn and claude executables are required")
    config = dict(cairn=str(Path(args.cairn).absolute()), claude=str(Path(args.claude).absolute()), model=args.model,
                  socket=str(args.socket.absolute()), token_file=str(args.token_file.absolute()), repo=args.repo)
    install(args.hooks.absolute(), args.destination.absolute(), config)
    print(f"Installed and trusted Cairn lifecycle hooks in {args.hooks}. Start a fresh Codex session.")


if __name__ == "__main__":
    main()
