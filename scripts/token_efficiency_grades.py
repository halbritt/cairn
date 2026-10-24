#!/usr/bin/env python3
"""Freeze and validate external task grades for the CAIRN-227 panel.

This structural tool never grades task output and cannot authenticate a reviewer.
Its report is evidence organization, not independent acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import token_efficiency_gate as gate


HEX = re.compile(r"[0-9a-f]{64}\Z")
VERDICTS = {"accepted", "failed", "unknown", "unrun"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_private(path, label):
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_mode & 0o077 == 0 and info.st_size <= 5_000_000,
                    f"{label} must be an owner-only regular file under 5 MB")
            raw = os.read(fd, 5_000_001)
        finally:
            os.close(fd)
    except OSError:
        raise ValueError(f"{label} unavailable") from None
    require(len(raw) <= 5_000_000, f"{label} exceeds 5 MB")
    return raw


def read_json(path, label):
    raw = read_private(path, label)
    try:
        return json.loads(raw, object_pairs_hook=gate.unique_pairs,
                          parse_constant=gate.invalid_constant), raw
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f"{label} is not valid JSON") from None


def freeze(frozen, fixture, controller, controller_bytes=None):
    plan = gate.verify_frozen(frozen, fixture)
    require(controller.get("schema") == "cairn.token-efficiency-panel-manifest/1",
            "panel controller manifest schema")
    require(controller.get("fixture_sha256") == plan["fixture_sha256"],
            "controller and frozen plan fixture differ")
    rows = controller.get("prospective_runs")
    require(isinstance(rows, list) and len(rows) == 88, "controller must contain all 88 workflows")
    by_id = {row.get("workflow_id"): row for row in rows if isinstance(row, dict)}
    require(len(by_id) == 88, "controller workflow IDs must be unique")
    plan_rows = {row["workflow_id"]: row for row in plan["runs"]}
    require(set(by_id) == set(plan_rows), "controller workflow IDs do not match the frozen plan")
    grades = []
    for workflow_id, planned in plan_rows.items():
        source = by_id[workflow_id]
        require(source.get("case") == planned["case"] and source.get("arm") == planned["arm"],
                f"{workflow_id}: controller case/arm mismatch")
        require(source.get("acceptance_check_sha256") == plan["acceptance_checks"][planned["case"]],
                f"{workflow_id}: controller acceptance check mismatch")
        stage_contracts = []
        for stage in planned["stages"]:
            model = plan["capture_selector"] if stage["role"] == "capture" else plan["model"]
            stage_contracts.append({"stage_id": stage["stage_id"], "role": stage["role"],
                                    "provider": model,
                                    "pin_name": stage["pin"],
                                    "pin": plan["arms"][planned["arm"]]["pins"][stage["pin"]],
                                    "arm_pins": plan["arms"][planned["arm"]]["pins"]})
        grades.append({"workflow_id": workflow_id, "case": planned["case"], "arm": planned["arm"],
                       "acceptance_check_sha256": plan["acceptance_checks"][planned["case"]],
                       "stages": stage_contracts})
    return {"schema": "cairn.token-efficiency-grade-plan/1",
            "frozen_plan_sha256": frozen["plan_sha256"],
            "fixture_sha256": plan["fixture_sha256"],
            "controller_sha256": digest(controller_bytes if controller_bytes is not None else
                                         json.dumps(controller, sort_keys=True,
                                                    separators=(",", ":")).encode()),
            "workflows": grades,
            "independence_limit": "Reviewer identity and independence are external assertions; this plan cannot authenticate them.",
            "acceptance_limit": "Grades are reviewer judgments over cited artifacts; this tool cannot establish task correctness."}


def report(plan, ledger, evidence_root=None):
    require(plan.get("schema") == "cairn.token-efficiency-grade-plan/1", "grade plan schema")
    require(ledger.get("schema") == "cairn.token-efficiency-grade-ledger/1", "grade ledger schema")
    require(ledger.get("grade_plan_sha256") == gate.sha(plan), "grade plan hash mismatch")
    rows = ledger.get("grades")
    require(isinstance(rows, list) and len(rows) == 88, "exactly 88 workflow grade rows required")
    expected = {row["workflow_id"]: row for row in plan["workflows"]}
    actual = {row.get("workflow_id"): row for row in rows if isinstance(row, dict)}
    require(len(actual) == 88 and set(actual) == set(expected), "grade rows must cover each workflow exactly once")
    counts = {value: 0 for value in VERDICTS}
    reviewers = set()
    for workflow_id, row in actual.items():
        ref = expected[workflow_id]
        require(row.get("case") == ref["case"] and row.get("arm") == ref["arm"]
                and row.get("acceptance_check_sha256") == ref["acceptance_check_sha256"]
                and row.get("stages") == ref["stages"],
                f"{workflow_id}: grade does not match frozen case, arm, criterion, or stage identities")
        verdict = row.get("verdict")
        require(verdict in VERDICTS, f"{workflow_id}: invalid verdict")
        reviewer = row.get("reviewer")
        require(isinstance(reviewer, str) and reviewer.strip(), f"{workflow_id}: reviewer reference required")
        reviewers.add(reviewer)
        require(row.get("independence_assertion") is True,
                f"{workflow_id}: explicit external independence assertion required")
        require(isinstance(row.get("rationale"), str) and row["rationale"].strip(),
                f"{workflow_id}: reviewer rationale required, including for unknown/unrun")
        artifacts = row.get("artifacts", [])
        require(isinstance(artifacts, list), f"{workflow_id}: artifacts must be a list")
        if verdict in {"accepted", "failed"}:
            require(artifacts, f"{workflow_id}: accepted/failed grade requires evidence")
        for artifact in artifacts:
            require(isinstance(artifact, dict) and set(artifact) == {"path", "sha256"},
                    f"{workflow_id}: artifact reference shape")
            require(isinstance(artifact["sha256"], str) and HEX.fullmatch(artifact["sha256"]),
                    f"{workflow_id}: artifact hash")
            path = Path(artifact["path"])
            require(path.is_absolute(), f"{workflow_id}: artifact path must be absolute")
            if evidence_root is not None:
                try:
                    resolved_root = Path(evidence_root).resolve(strict=True)
                    resolved_path = path.resolve(strict=True)
                    resolved_path.relative_to(resolved_root)
                except (ValueError, OSError):
                    raise ValueError(f"{workflow_id}: artifact must be under the evidence root") from None
                path = resolved_path
            raw = read_private(path, f"{workflow_id} evidence")
            require(digest(raw) == artifact["sha256"], f"{workflow_id}: evidence hash mismatch")
        counts[verdict] += 1
    return {"schema": "cairn.token-efficiency-grade-report/1",
            "grade_plan_sha256": gate.sha(plan), "workflow_count": 88, "counts": counts,
            "reviewer_references": len(reviewers),
            "independent_task_acceptance": "externally_asserted_unverified",
            "grade_ledger_complete": True,
            "release_decision": "defer",
            "limits": ["Reviewer identity and independence were not authenticated.",
                       "Task correctness was not independently established by this validator.",
                       "The report does not replace native usage joins, capacity admission, or paired-run gates."]}


def write_new(path, value):
    path = Path(path)
    repo = Path(__file__).resolve().parents[1]
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ValueError("grade artifacts must be written outside the repository")
    require(not path.exists(), "output already exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    require(parent_stat.st_uid == os.getuid() and parent_stat.st_mode & 0o077 == 0,
            "output parent must be owner-only")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_cmd = commands.add_parser("freeze")
    for name in ("frozen", "fixture", "controller", "output"):
        freeze_cmd.add_argument("--" + name, type=Path, required=True)
    report_cmd = commands.add_parser("report")
    for name in ("plan", "ledger", "output"):
        report_cmd.add_argument("--" + name, type=Path, required=True)
    report_cmd.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        frozen, _ = read_json(args.frozen, "frozen plan")
        controller, controller_raw = read_json(args.controller, "panel controller")
        output = freeze(frozen, args.fixture, controller, controller_raw)
    else:
        plan, _ = read_json(args.plan, "grade plan")
        ledger, _ = read_json(args.ledger, "grade ledger")
        output = report(plan, ledger, args.evidence_root)
    write_new(args.output, output)
    print(json.dumps({"status": "ok", "schema": output["schema"], "output": str(args.output)}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc)) from None
