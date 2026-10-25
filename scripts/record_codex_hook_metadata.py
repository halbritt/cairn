#!/usr/bin/env python3
"""Forward a Codex hook while retaining only event and turn identifiers."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--stop-complete", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("hook command is required")
    payload = sys.stdin.read(1_000_001)
    if len(payload.encode("utf-8")) > 1_000_000:
        raise SystemExit("hook payload exceeds fixture limit")
    event = json.loads(payload)
    row = dict(event=event.get("hook_event_name"),
               turn_id=event.get("turn_id"),
               source=event.get("source"),
               keys=sorted(event.keys()))
    args.metadata.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.metadata, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=14)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    row.update(exit_code=result.returncode)
    fd = os.open(args.metadata, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(**row, phase="exit"), sort_keys=True) + "\n")
    if row["event"] == "Stop":
        try:
            output = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            output = {}
        row.update(exit_code=result.returncode, output_status=output.get("status"),
                   output_code=output.get("code"), stderr_bytes=len(result.stderr.encode("utf-8")),
                   stderr_code=(re.search(r"\b[A-Z][A-Z0-9_]{2,}\b", result.stderr).group(0)
                                if re.search(r"\b[A-Z][A-Z0-9_]{2,}\b", result.stderr) else None))
        pending = args.stop_complete.with_suffix(args.stop_complete.suffix + ".tmp")
        pending.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
        pending.chmod(0o600)
        pending.replace(args.stop_complete)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
