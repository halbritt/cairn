#!/usr/bin/env python3
"""Install authorized OpenCode lifecycle memory beside existing Cairn tools."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("claude_installer", ROOT / "scripts/install-claude-hooks.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def install(config_dir, destination, claude, model=None):
    native = json.loads((config_dir / "cairn.json").read_text())
    config = {"cairn": native["executable"], "socket": native["socket"], "token_file": native["token_file"],
              "repo": native["repo"], "harness": "opencode", "claude": claude,
              "state_dir": str(destination / "state")}
    for key in ("task_id", "run_id", "context"):
        if key in native:
            config[key] = native[key]
    if model:
        config["model"] = model
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    script = destination / "memory.py"
    shutil.copyfile(ROOT / "integrations/lifecycle/memory.py", script)
    script.chmod(0o700)
    engine_config = destination / "config.json"
    installer.write_json(engine_config, config)
    installer.write_json(config_dir / "cairn-lifecycle.json", dict(
        python=sys.executable, script=str(script), engine_config=str(engine_config)))
    plugin = config_dir / "plugins/cairn-lifecycle.ts"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "integrations/opencode/lifecycle.ts", plugin)


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=home / ".config/opencode")
    parser.add_argument("--destination", type=Path, default=home / ".local/share/cairn/opencode-hooks")
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--model")
    args = parser.parse_args()
    if not args.claude:
        parser.error("installed Claude is required for the tool-free selector")
    model = args.model
    if model is None:
        profile = home / ".claude/settings.json"
        model = json.loads(profile.read_text()).get("model") if profile.exists() else None
    install(args.config_dir.absolute(), args.destination.absolute(), str(Path(args.claude).absolute()), model)
    print(f"Installed Cairn lifecycle plugin in {args.config_dir}. Start a fresh OpenCode process.")


if __name__ == "__main__":
    main()
