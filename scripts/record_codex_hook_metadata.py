#!/usr/bin/env python3
"""Forward a Codex hook while retaining only event and turn identifiers."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from codex_fixture_diagnostic import update as update_fixture_diagnostic


HOOK_ERROR_CODES = frozenset({
    "API_UNAVAILABLE", "HANDLE_EXPIRED", "HOST_TARGET_GONE", "IDEMPOTENCY_CONFLICT",
    "INBOX_ATTEMPT_MISMATCH", "INTENT_PENDING", "INVALID_CONFIG", "INVALID_HOST",
    "INVALID_REQUEST", "NATIVE_ACTIVE", "NATIVE_PROMPT_REFUSED", "NATIVE_TURN_MISMATCH",
    "NOT_FOUND", "NOT_THIS_SESSION", "REQUEST_CANCELLED", "SESSION_BUSY", "SOURCE_UNAVAILABLE",
    "STALE_LEASE", "STALE_SESSION", "UNSUPPORTED_CONTROL", "WAKE_UNAVAILABLE", "WAKE_UNCERTAIN",
})
HOOK_ERROR_CLASSES = frozenset({
    "CoordinationError", "ConnectionError", "FileNotFoundError", "KeyError", "OSError",
    "PermissionError", "RuntimeError", "SubprocessError", "TimeoutError", "TypeError", "ValueError",
})


def safe_stderr_tags(stderr):
    """Return only allowlisted error identifiers; never retain stderr text."""
    tokens = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", stderr)
    match = re.search(r"Cairn coordination: ([A-Za-z][A-Za-z0-9_]+)(?::|;)", stderr)
    return dict(
        stderr_code=next((token for token in tokens if token in HOOK_ERROR_CODES), None),
        stderr_class=(match.group(1) if match and match.group(1) in HOOK_ERROR_CLASSES else None))


def owner_process_class(command, event):
    """Mirror owner lookup but retain only a fixed classification."""
    if event.get("host_pid") is not None:
        return "not_applicable"
    try:
        config_index = command.index("--config")
        config = json.loads(Path(command[config_index + 1]).read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location("codex_fixture_owner_probe", Path(command[1]))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return "not_evaluated"
    try:
        module.owner_process(config, event)
    except module.CoordinationError:
        return "none"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "not_evaluated"
    return "configured_name_match"


def safe_output_tags(stdout):
    try:
        output = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return dict(output_status=None, output_code=None)
    if not isinstance(output, dict):
        return dict(output_status=None, output_code=None)
    status, code = output.get("status"), output.get("code")
    return dict(output_status=status if isinstance(status, str) and
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", status) else None,
                output_code=code if isinstance(code, str) and
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--stop-complete", required=True, type=Path)
    parser.add_argument("--diagnostic-summary", type=Path)
    parser.add_argument("--hook-kind", choices=("coordination", "memory"))
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
    if args.hook_kind:
        row["hook_kind"] = args.hook_kind
    summary_eligible = (args.diagnostic_summary is not None and args.hook_kind == "coordination"
                        and row["event"] == "UserPromptSubmit")
    if summary_eligible:
        update_fixture_diagnostic(args.diagnostic_summary, "user_prompt_submit", dict(
            entry_seen=True, exit_seen=False, exit_code=None, stderr_code=None, stderr_class=None,
            output_status=None, output_code=None,
            owner_process_class=owner_process_class(command, event)), increment=("observation_count",))
    args.metadata.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.metadata, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=14)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    row.update(exit_code=result.returncode, **safe_stderr_tags(result.stderr))
    if summary_eligible:
        update_fixture_diagnostic(args.diagnostic_summary, "user_prompt_submit", dict(
            exit_seen=True, exit_code=result.returncode,
            stderr_code=row.get("stderr_code"), stderr_class=row.get("stderr_class"),
            **safe_output_tags(result.stdout)))
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
