#!/usr/bin/env python3
"""Route recall hook events for an explicit allowlist of native sessions.

Usage (as a SessionStart or UserPromptSubmit hook command):

    recall_dispatch.py --route ROUTE.json -- ORIGINAL_COMMAND...

The original command runs unchanged for every session and event except an
allowlisted native session ID on SessionStart or UserPromptSubmit, which runs
the route's candidate command instead. Either way this process replaces itself
with the selected command (exec), so the hook payload bytes, stdout, stderr,
exit status, signals and the host's timeout reach the engine exactly as before.

The session ID is routing metadata from the host, never authentication: routing
grants nothing beyond selecting which already-installed engine handles recall.

A missing route file disables routing (every event runs the original command).
A route file that exists but cannot be validated fails the hook visibly (exit 1)
rather than guessing which sessions it meant. A targeted session whose candidate
command is invalid also fails visibly; other sessions are unaffected.
"""
import argparse
import json
import os
from pathlib import Path
import sys

SCHEMA = "cairn.recall-dispatch/1"
RECALL_EVENTS = ("SessionStart", "UserPromptSubmit")
# Settings the candidate must share with the original so memory identity,
# state files and their locks stay continuous across engines.
SHARED_CONFIG = ("repo", "socket", "token_file", "state_dir", "harness", "task_id", "run_id")
INPUT_LIMIT = 1024 * 1024  # same host-event ceiling as memory.py


class DispatchError(Exception):
    pass


def valid_session_id(value):
    # Exact IDs only: no wildcard or pattern characters, bounded, printable.
    return (isinstance(value, str) and value.strip() == value and 0 < len(value.encode()) <= 256 and
            not any(c in value for c in "*?[]") and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def config_path(command):
    """The --config argument of a lifecycle engine command."""
    for i, arg in enumerate(command[:-1]):
        if arg == "--config":
            return Path(command[i + 1])
    raise DispatchError("command has no --config argument: " + " ".join(command[:3]))


def load_route(path):
    try:
        route = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise DispatchError(f"route file {path} is unreadable: {type(exc).__name__}") from exc
    if not isinstance(route, dict) or route.get("schema") != SCHEMA or set(route) - {"schema", "sessions", "command", "note"}:
        raise DispatchError(f"route file {path} must be a {SCHEMA} object with sessions and command")
    sessions, command = route.get("sessions"), route.get("command")
    if (not isinstance(sessions, list) or not sessions or len(sessions) > 64 or
            not all(valid_session_id(s) for s in sessions) or len(set(sessions)) != len(sessions)):
        raise DispatchError(f"route file {path}: sessions must be 1-64 distinct exact native session IDs")
    if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
        raise DispatchError(f"route file {path}: command must be a non-empty argv list")
    return route


def check_candidate(candidate, original):
    """Refuse a candidate that could not continue this session's memory state."""
    if not os.path.isabs(candidate[0]) or not os.access(candidate[0], os.X_OK):
        raise DispatchError(f"candidate interpreter {candidate[0]} is not an absolute executable")
    for arg in candidate[1:]:
        if arg.endswith(".py") and not Path(arg).is_file():
            raise DispatchError(f"candidate engine {arg} does not exist")
    try:
        mine = json.loads(config_path(candidate).read_text())
        theirs = json.loads(config_path(original).read_text())
    except (OSError, ValueError) as exc:
        raise DispatchError(f"candidate or original config is unreadable: {type(exc).__name__}") from exc
    if not isinstance(mine, dict) or not isinstance(theirs, dict):
        raise DispatchError("candidate and original configs must be objects")
    differing = [k for k in SHARED_CONFIG if mine.get(k) != theirs.get(k)]
    if differing:
        raise DispatchError("candidate config differs from the original in " + ", ".join(differing))


def select(route_path, original, payload, session=None):
    """Return the argv to exec. Raises DispatchError for a visible failure."""
    if not route_path.exists():
        return original
    try:
        event = json.loads(payload)
    except ValueError:
        return original  # The original engine reports malformed input itself.
    if not isinstance(event, dict) or event.get("hook_event_name") not in RECALL_EVENTS:
        return original
    # A fixed deployment gate protects unrelated sessions even when the mutable
    # route file is corrupt. It is routing metadata, not authentication.
    if session is not None and event.get("session_id") != session:
        return original
    route = load_route(route_path)
    if event.get("session_id") not in route["sessions"]:
        return original
    check_candidate(route["command"], original)
    return route["command"]


def exec_with_payload(argv, payload):
    # Hand the exact bytes to the engine's stdin through an in-memory file.
    fd = os.memfd_create("cairn-hook-payload")
    remaining = memoryview(payload)
    while remaining:
        written = os.write(fd, remaining)
        if written == 0:
            raise OSError("hook payload write made no progress")
        remaining = remaining[written:]
    os.lseek(fd, 0, os.SEEK_SET)
    os.dup2(fd, 0)
    os.close(fd)
    os.execv(argv[0], argv)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--session", help="fixed exact session gate checked before reading the route")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv[1:])
    original = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not original or (args.session is not None and not valid_session_id(args.session)):
        parser.error("an original command and a valid exact session gate are required")
    payload = sys.stdin.buffer.read(INPUT_LIMIT + 1)
    try:
        if len(payload) > INPUT_LIMIT:
            raise DispatchError("host event exceeds input limit")
        selected = select(args.route, original, payload, args.session)
    except DispatchError as exc:
        print(f"Cairn recall dispatch: {exc}; recall skipped for this event", file=sys.stderr)
        return 1
    sys.stdout.flush()
    exec_with_payload(selected, payload)
    return 1  # exec does not return


if __name__ == "__main__":
    sys.exit(main(sys.argv))
