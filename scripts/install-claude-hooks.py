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


CLAUDE_CONTEXT_BYTES = 9500


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


def install_router(destination, config, enabled=True):
    """Copy the optional skill router beside the hook script; enable it in the engine config."""
    shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/lifecycle/skill_router.py",
                    destination / "skill_router.py")
    (destination / "skill_router.py").chmod(0o600)
    return dict(config, skill_router={"enabled": True}) if enabled else {k: v for k, v in config.items() if k != "skill_router"}


def install(settings_path, destination, config, skill_router=True):
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
                                 "timeout": 150 if event in ("PreCompact", "SessionEnd") else 13}]})
    original = settings_path.read_bytes() if settings_path.exists() else None
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py", script)
    script.chmod(0o700)
    config = install_router(destination, config, skill_router)
    # Claude Code keeps only about 10,000 characters of hook additionalContext
    # (measured 2026-09-24); keep injected memory and skills under it.
    write_json(config_path, dict(config, state_dir=str(destination / "state"),
                                 context_bytes=config.get("context_bytes", CLAUDE_CONTEXT_BYTES)))
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
    parser.add_argument("--no-skill-router", action="store_true", help="install without prompt-time skill routing")
    args = parser.parse_args()
    if not args.cairn or not args.claude:
        parser.error("installed cairn and claude executables are required")
    settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
    config = dict(cairn=str(Path(args.cairn).absolute()), claude=str(Path(args.claude).absolute()),
                  socket=str(args.socket.absolute()), token_file=str(args.token_file.absolute()), repo=args.repo)
    if settings.get("model"):
        config["model"] = settings["model"]
    install(args.settings.absolute(), args.destination.absolute(), config, skill_router=not args.no_skill_router)
    print(f"Installed Cairn lifecycle hooks in {args.settings}. Start a fresh Claude session.")


if __name__ == "__main__":
    main()
