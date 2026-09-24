#!/usr/bin/env python3
"""Live check of the installed skill router in one harness.

Sends one owner prompt that belongs to a hidden skill through the harness's real
CLI, then reads that installation's router log for a new line. Passes when the
router fired for the expected skill. Model output is printed for inspection but
is not the pass criterion: Codex, for one, can fail after its hooks ran.

Usage: check_skill_router.py claude|claude-harm|opencode|codex [--prompt TEXT --skill NAME]
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HOME = Path.home()
STATE = HOME / ".local/share/cairn"
HARNESSES = {
    "claude": dict(log=STATE / "claude-hooks/state/skill-router.jsonl", env={"CLAUDE_CONFIG_DIR": str(HOME / ".claude")},
                   argv=["claude", "--print", "--no-session-persistence", "--tools", "", "--max-turns", "1"]),
    "claude-harm": dict(log=STATE / "claude-harm-hooks/state/skill-router.jsonl",
                        env={"CLAUDE_CONFIG_DIR": str(HOME / ".claude-harm")},
                        argv=["claude", "--print", "--no-session-persistence", "--tools", "", "--max-turns", "1"]),
    "opencode": dict(log=STATE / "opencode-hooks/state/skill-router.jsonl", env={}, argv=["opencode", "run"]),
    "codex": dict(log=STATE / "codex-hooks/state/skill-router.jsonl", env={},
                  argv=["codex", "exec", "--skip-git-repo-check", "-s", "read-only"]),
}


def lines(path):
    return path.read_text().splitlines() if path.exists() else []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("harness", choices=sorted(HARNESSES))
    parser.add_argument("--prompt", default="samtools depth exits 141 in the pipeline")
    parser.add_argument("--skill", default="samtools-sigpipe-and-depth-fix")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--cwd", type=Path, default=Path("/tmp"))
    args = parser.parse_args()
    spec = HARNESSES[args.harness]
    before = len(lines(spec["log"]))
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update(spec["env"])
    started = time.monotonic()
    run = subprocess.run(spec["argv"] + [args.prompt], cwd=args.cwd, env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=args.timeout)
    seconds = round(time.monotonic() - started, 1)
    new = [json.loads(line) for line in lines(spec["log"])[before:]]
    print(json.dumps(dict(harness=args.harness, exit=run.returncode, seconds=seconds, routes=[
        {k: r.get(k) for k in ("event", "fired", "reason", "leg", "choice", "confidence", "mode", "ms")} for r in new]), indent=1))
    print("--- model output (first 1200 chars) ---")
    print((run.stdout or run.stderr)[:1200])
    fired = [r for r in new if r.get("fired") and r.get("choice") == args.skill]
    print("PASS" if fired else "FAIL: no fired route for " + args.skill)
    return 0 if fired else 1


if __name__ == "__main__":
    sys.exit(main())
