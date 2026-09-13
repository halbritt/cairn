#!/usr/bin/env python3
"""Install Cairn lifecycle hooks alongside existing Claude Code settings."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile


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


def install(settings_path, destination, config):
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    script = destination / "lifecycle.py"
    config_path = destination / "config.json"
    command = shlex.join([sys.executable, str(script), "--config", str(config_path)])
    for event in ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd", "PostToolUse", "PostToolUseFailure"):
        groups = hooks.setdefault(event, [])
        # Replace only this installation's exact command; retain every other hook.
        for group in groups:
            group["hooks"] = [hook for hook in group["hooks"] if hook.get("command") != command]
        groups[:] = [group for group in groups if group["hooks"]]
        groups.append({"hooks": [{"type": "command", "command": command,
                                 "timeout": 55 if event in ("PreCompact", "SessionEnd") else 13}]})
    original = settings_path.read_bytes() if settings_path.exists() else None
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/claude/lifecycle.py", script)
    script.chmod(0o700)
    write_json(config_path, dict(config, state_dir=str(destination / "state")))
    backup = settings_path.with_name(settings_path.name + ".before-cairn-lifecycle")
    if original is not None and not backup.exists():
        backup.write_bytes(original)
        backup.chmod(0o600)
    write_json(settings_path, settings)
    return command


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "settings.json")
    parser.add_argument("--destination", type=Path, default=home / ".local/share/cairn/claude-hooks")
    parser.add_argument("--cairn", default=shutil.which("cairn"))
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--socket", type=Path, default=home / ".local/share/cairn/api.sock")
    parser.add_argument("--token-file", type=Path, default=home / ".local/share/cairn/hosted-agent.token")
    parser.add_argument("--repo", default=str(home / "git/cairn"))
    args = parser.parse_args()
    if not args.cairn or not args.claude:
        parser.error("installed cairn and claude executables are required")
    settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
    config = dict(cairn=str(Path(args.cairn).absolute()), claude=str(Path(args.claude).absolute()),
                  socket=str(args.socket.absolute()), token_file=str(args.token_file.absolute()), repo=args.repo)
    if settings.get("model"):
        config["model"] = settings["model"]
    install(args.settings.absolute(), args.destination.absolute(), config)
    print(f"Installed Cairn lifecycle hooks in {args.settings}. Start a fresh Claude session.")


if __name__ == "__main__":
    main()
