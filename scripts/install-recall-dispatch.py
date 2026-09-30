#!/usr/bin/env python3
"""Wrap or unwrap a Claude memory hook's recall events with recall_dispatch.py.

install: copy the dispatcher into DEST and rewrite only the SessionStart and
UserPromptSubmit hooks whose command equals --original exactly, so they run
`recall_dispatch.py --route ROUTE -- ORIGINAL...`. Timeouts and every other hook,
event and setting are left untouched. Capture events keep the original command.

rollback: restore the exact original command in every hook that runs this
dispatcher, leaving everything else (including edits made since install) alone.

Deleting the route file alone disables routing without touching settings.
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
spec = importlib.util.spec_from_file_location("recall_dispatch", ROOT / "integrations/lifecycle/recall_dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


def write_json(path, value):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as file:
        json.dump(value, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.chmod(file.name, path.stat().st_mode & 0o777 if path.exists() else 0o600)
    Path(file.name).replace(path)


def wrapped(python, dispatcher, route, original):
    return shlex.join([python, str(dispatcher), "--route", str(route), "--", *shlex.split(original)])


def unwrap(command, dispatcher):
    """The original command if this hook runs the given dispatcher, else None."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) >= 6 and argv[1] == str(dispatcher) and argv[2] == "--route" and argv[4] == "--":
        return shlex.join(argv[5:])
    return None


def install(settings, dest, route, original, python=sys.executable):
    if shlex.join(shlex.split(original)) != original:
        raise SystemExit("--original must be in shlex.join form so rollback restores it byte for byte")
    if route.exists():
        dispatch.load_route(route)  # refuse to wire an invalid route file
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    dispatcher = dest / "recall_dispatch.py"
    shutil.copyfile(ROOT / "integrations/lifecycle/recall_dispatch.py", dispatcher)
    dispatcher.chmod(0o700)
    data = json.loads(settings.read_text())  # read immediately before writing
    command = wrapped(python, dispatcher, route, original)
    changed = found = 0
    for event in dispatch.RECALL_EVENTS:
        for group in data.get("hooks", {}).get(event, []):
            for hook in group.get("hooks", []):
                if hook.get("command") == original:
                    hook["command"] = command
                    changed += 1
                    found += 1
                elif hook.get("command") == command:
                    found += 1
    if found != len(dispatch.RECALL_EVENTS):
        raise SystemExit(f"expected the original command once on each of {dispatch.RECALL_EVENTS}; found {found}")
    if changed:
        backup = settings.with_name(settings.name + ".before-recall-dispatch")
        if not backup.exists():
            shutil.copy2(settings, backup)
        write_json(settings, data)
    return changed


def rollback(settings, dest):
    dispatcher = dest / "recall_dispatch.py"
    data = json.loads(settings.read_text())
    restored = 0
    for groups in data.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                original = unwrap(hook.get("command", ""), dispatcher)
                if original is not None:
                    hook["command"] = original
                    restored += 1
    if restored:
        write_json(settings, data)
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("install", "rollback"))
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    parser.add_argument("--dest", type=Path, required=True, help="directory for the installed dispatcher")
    parser.add_argument("--route", type=Path, help="route file (install)")
    parser.add_argument("--original", help="exact existing recall hook command (install)")
    args = parser.parse_args()
    if args.action == "install":
        if not args.route or not args.original:
            parser.error("install requires --route and --original")
        print(f"wrapped {install(args.settings, args.dest.resolve(), args.route.resolve(), args.original)} hook(s)")
    else:
        print(f"restored {rollback(args.settings, args.dest.resolve())} hook(s)")


if __name__ == "__main__":
    main()
