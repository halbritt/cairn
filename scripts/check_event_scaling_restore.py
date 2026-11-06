#!/usr/bin/env python3
"""Event-scaling state across an actual older physical restore (ES-7).

Runs scripts/check_work_coordination_restore.py with the event-scaling
extension in scripts/event_scaling_restore_case.py. The runner creates its own
disposable PostgreSQL cluster and uses a local scripted Responses endpoint; it
needs an explicit Cairn executable and a complete native Codex package
manifest. Nothing is built, installed or provisioned and no production store
is opened.

--admission-mode enforce (default) is the proof. --admission-mode observe runs
only the mode-independent closure and policy checks and exits 3 with status
observe_subset_passed: it is a development subset, not ES-7 evidence.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
OBSERVE_SUBSET_EXIT = 3


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    parser.add_argument("--admission-mode", choices=("enforce", "observe"), default="enforce")
    args = parser.parse_args()
    sys.path.insert(0, str(HERE))
    runner = load("event_scaling_restore_runner", HERE / "check_work_coordination_restore.py")
    case = load("event_scaling_restore_case", HERE / "event_scaling_restore_case.py").EventScalingRestoreCase(
        args.admission_mode)
    case.extra_inputs = [Path(__file__).resolve()]
    sys.argv = [str(HERE / "check_work_coordination_restore.py"), str(args.binary),
                "--native-manifest", str(args.native_manifest), "--report-directory", str(args.report_directory)]
    runner.main(extension_factory=lambda: case)
    status = case.report["status"]
    print(json.dumps({"event_scaling_restore": status, "admission_mode": args.admission_mode,
                      "stages": case.report["stages"]}))
    if args.admission_mode == "observe":
        sys.exit(OBSERVE_SUBSET_EXIT)
    if status != "passed":
        raise SystemExit("event-scaling restore extension did not pass: " + status)


if __name__ == "__main__":
    main()
