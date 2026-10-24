#!/usr/bin/env python3
"""Mechanical CAIRN-227 paired-panel gate; never grades task usefulness.

Freeze a reviewed plan before trials. The report joins its exact workflow and
stage IDs to selected, opaque native workflow evidence. Caller observations
can establish coverage for review, but cannot establish independent acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


CASES = (
    "single_notice", "actionable_response", "small_request_result_reply", "long_source",
    "routine_burst_urgent_request", "busy_owner_task", "concurrent_receivers", "duplicate_send",
    "completion_reply_lost", "source_forgotten_before_read", "lease_expiry", "process_crash",
    "relay_uncertain_response", "stale_execution", "required_conflict_group", "semantic_unavailable",
    "wrong_project_memory", "corrected_note", "compaction_before_after_settlement",
    "unchanged_capture", "meaningful_owner_correction", "unfinished_handoff",
)
ARMS = ("baseline", "envelope", "batch", "combined")
ROLES = ("sender", "recipient", "coordinator", "capture", "recovery")
TOKEN_FIELDS = ("input", "cached", "write", "output", "reasoning")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
WORKFLOW_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")  # workflow_model_report workload label
HEX = re.compile(r"[0-9a-f]{64}\Z")
OPAQUE = re.compile(r"[0-9a-f]{24}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(rows, key, label):
    values = [row[key] for row in rows]
    require(len(values) == len(set(values)), f"duplicate {label}")


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_plan(plan):
    require(isinstance(plan, dict), "plan object")
    require(plan.get("schema") == "cairn.token-efficiency-plan/1", "plan schema")
    require(plan.get("cases") == list(CASES), "plan must contain the 22 ordered case IDs")
    checks = plan.get("acceptance_checks") or {}
    require(set(checks) == set(CASES) and all(isinstance(value, str) and HEX.fullmatch(value)
                                             for value in checks.values()),
            "one frozen acceptance-check hash per case is required")
    require(isinstance(plan.get("fixture_sha256"), str) and HEX.fullmatch(plan["fixture_sha256"]),
            "fixture sha256")
    model = plan.get("model") or {}
    require(isinstance(model, dict), "model object")
    for key in ("harness", "account", "id", "effort"):
        require(isinstance(model.get(key), str) and ID.fullmatch(model[key]), f"model {key}")
    require(model.get("route") == "subscription", "model route must be subscription")
    limits = plan.get("limits") or {}
    require(isinstance(limits, dict), "limits object")
    for key in ("elapsed_seconds", "input_tokens", "output_tokens"):
        require(type(limits.get(key)) is int and limits[key] > 0, f"positive {key} limit")
    arms = plan.get("arms") or {}
    require(isinstance(arms, dict), "arms object")
    require(list(arms) == list(ARMS), "plan needs baseline, envelope, batch, combined arms in order")
    for arm, spec in arms.items():
        require(isinstance(spec, dict), f"{arm}: arm spec")
        require(isinstance(spec.get("commit"), str) and re.fullmatch(r"[0-9a-f]{7,40}", spec["commit"]),
                f"{arm}: commit")
        pins = spec.get("pins") or {}
        for key in ("api", "ordinary", "inbox", "client"):
            require(isinstance(pins.get(key), str) and ID.fullmatch(pins[key]), f"{arm}: {key} pin")
    runs = plan.get("runs") or []
    require(len(runs) == len(CASES) * len(ARMS), "one planned workflow per case and arm is required")
    unique(runs, "workflow_id", "workflow ID")
    pairs = []
    stages = []
    for run in runs:
        require(isinstance(run, dict), "run object")
        require(isinstance(run.get("workflow_id"), str) and WORKFLOW_ID.fullmatch(run["workflow_id"]),
                "workflow ID must fit the native workload label")
        require(run.get("case") in CASES and run.get("arm") in ARMS, "unknown case or arm")
        pairs.append((run["case"], run["arm"]))
        slots = run.get("stages") or []
        require(slots, f"{run['workflow_id']}: no stages")
        for slot in slots:
            require(isinstance(slot.get("stage_id"), str) and ID.fullmatch(slot["stage_id"]), "stage ID")
            require(slot.get("role") in ROLES, "stage role")
            require(slot.get("pin") in ("api", "ordinary", "inbox", "client"), "stage pin kind")
            stages.append(slot["stage_id"])
    require(len(pairs) == len(set(pairs)), "duplicate case/arm pair")
    require(set(pairs) == {(case, arm) for case in CASES for arm in ARMS}, "missing case/arm pair")
    require(len(stages) == len(set(stages)), "stage IDs must be globally disjoint")
    return plan


def verify_fixture(plan, fixture):
    require(hashlib.sha256(fixture.read_bytes()).hexdigest() == plan["fixture_sha256"],
            "fixture hash mismatch")
    source = json.loads(fixture.read_text())
    require(source.get("schema") == "cairn.token-efficiency-fixture/1", "fixture schema")
    rows = source.get("cases") or []
    require([row.get("id") for row in rows] == list(CASES), "fixture case IDs")
    for row in rows:
        check = row.get("acceptance_check")
        require(isinstance(check, (str, dict, list)) and check, f"{row['id']}: acceptance check")
        require(plan["acceptance_checks"][row["id"]] == sha(check),
                f"{row['id']}: acceptance check changed")


def freeze(plan, fixture):
    validate_plan(plan)
    verify_fixture(plan, fixture)
    return dict(schema="cairn.token-efficiency-frozen/1", plan_sha256=sha(plan), plan=plan)


def verify_frozen(frozen, fixture):
    require(frozen.get("schema") == "cairn.token-efficiency-frozen/1", "frozen schema")
    plan = validate_plan(frozen.get("plan") or {})
    require(frozen.get("plan_sha256") == sha(plan), "frozen plan hash mismatch")
    verify_fixture(plan, fixture)
    return plan


def provider_ok(provider, model, pin):
    return (isinstance(provider, dict) and provider.get("duplicate_response_id") is False
            and provider.get("harness") == model["harness"]
            and provider.get("account") == model["account"]
            and provider.get("models") == [model["id"]]
            and provider.get("efforts") == [model["effort"]]
            and provider.get("pin") == pin)


def report(frozen, fixture, observations, native):
    plan = verify_frozen(frozen, fixture)
    require(isinstance(observations, dict) and isinstance(native, dict), "report input objects")
    require(observations.get("schema") == "cairn.token-efficiency-observations/1", "observations schema")
    require(observations.get("plan_sha256") == frozen["plan_sha256"], "observation plan hash mismatch")
    require(native.get("schema") == "cairn.workflow-model-report/1", "native workflow report schema")
    workflows = native.get("workflows") or []
    unique(workflows, "event", "native event")
    require(all(isinstance(row["event"], str) and OPAQUE.fullmatch(row["event"]) for row in workflows),
            "native event must be opaque")
    native_by_event = {row["event"]: row for row in workflows}
    observed = observations.get("runs") or []
    unique(observed, "workflow_id", "observed workflow ID")
    planned_ids = {row["workflow_id"] for row in plan["runs"]}
    require(all(row["workflow_id"] in planned_ids for row in observed), "unplanned workflow ID")
    by_id = {row["workflow_id"]: row for row in observed}
    counts = dict(planned_runs=len(plan["runs"]), missing_runs=0, unrun_runs=0,
                  failed_runs=0, observed_runs=0, planned_stages=0, missing_stages=0,
                  unknown_stages=0, failed_stages=0, not_reached_stages=0, observed_stages=0,
                  unknown_provider_stages=0, unknown_usage_fields={field: 0 for field in TOKEN_FIELDS})
    problems = []
    rows = []
    seen_events = set()
    seen_attempts = set()
    for expected in plan["runs"]:
        workflow_id, arm, case = expected["workflow_id"], expected["arm"], expected["case"]
        actual = by_id.get(workflow_id)
        counts["planned_stages"] += len(expected["stages"])
        if actual is None:
            counts["missing_runs"] += 1
            counts["missing_stages"] += len(expected["stages"])
            rows.append(dict(workflow_id=workflow_id, case=case, arm=arm, state="missing"))
            continue
        require(actual.get("case") == case and actual.get("arm") == arm,
                f"{workflow_id}: case/arm join mismatch")
        state = actual.get("state")
        require(state in ("observed", "failed", "unrun"), f"{workflow_id}: state")
        counts[state + "_runs"] += 1
        event = actual.get("event")
        if event is None and state in ("failed", "unrun"):
            native_row = None
        else:
            require(isinstance(event, str) and OPAQUE.fullmatch(event), f"{workflow_id}: opaque event")
            require(event not in seen_events, "one native event cannot serve two planned workflows")
            seen_events.add(event)
            native_row = native_by_event.get(event)
            if native_row is None:
                problems.append(f"{workflow_id}: native workflow event missing")
            elif native_row.get("workload") != workflow_id:
                problems.append(f"{workflow_id}: native workload does not match frozen workflow ID")
        if state == "failed":
            require(isinstance(actual.get("failure_disposition"), str)
                    and ID.fullmatch(actual["failure_disposition"]),
                    f"{workflow_id}: explicit failure disposition required")
        if state == "unrun":
            problems.append(f"{workflow_id}: unrun")
        stages = actual.get("stages") or []
        unique(stages, "stage_id", "observed stage ID")
        expected_stages = {slot["stage_id"]: slot for slot in expected["stages"]}
        require(all(stage["stage_id"] in expected_stages for stage in stages),
                f"{workflow_id}: unplanned stage ID")
        actual_stages = {stage["stage_id"]: stage for stage in stages}
        for stage_id, slot in expected_stages.items():
            stage = actual_stages.get(stage_id)
            if stage is None:
                counts["missing_stages"] += 1
                continue
            status = stage.get("state")
            require(status in ("observed", "failed", "not_reached", "unknown"), f"{stage_id}: state")
            counts[status + "_stages"] += 1
            if status == "unknown":
                problems.append(f"{stage_id}: stage evidence unknown")
            if status == "failed":
                require(isinstance(stage.get("failure_disposition"), str)
                        and ID.fullmatch(stage["failure_disposition"]),
                        f"{stage_id}: explicit failure disposition required")
            if status != "observed":
                continue
            if native_row is None and slot["role"] in ("sender", "recipient"):
                problems.append(f"{stage_id}: observed stage has no native workflow")
                continue
            require(stage.get("route") == plan["model"]["route"], f"{stage_id}: route drift")
            require(stage.get("model") == plan["model"]["id"]
                    and stage.get("effort") == plan["model"]["effort"], f"{stage_id}: model/effort drift")
            require(stage.get("pins") == plan["arms"][arm]["pins"], f"{stage_id}: pin drift")
            role = slot["role"]
            provider = None
            if native_row and role == "sender":
                provider = native_row.get("sender_provider")
            elif native_row and role == "recipient":
                attempt = stage.get("attempt")
                require(isinstance(attempt, str) and OPAQUE.fullmatch(attempt), f"{stage_id}: attempt")
                require(attempt not in seen_attempts, "one native attempt cannot serve two planned stages")
                seen_attempts.add(attempt)
                matches = [row for row in native_row.get("deliveries", []) if row.get("attempt") == attempt]
                require(len(matches) <= 1, f"{stage_id}: ambiguous native attempt")
                provider = matches[0].get("provider") if matches else None
            elif role in ("coordinator", "capture", "recovery"):
                # This selected evidence remains reviewable, not a native usage join.
                require(isinstance(stage.get("evidence_ref"), str) and ID.fullmatch(stage["evidence_ref"]),
                        f"{stage_id}: selected evidence reference")
                provider = stage.get("provider")
            if provider is None:
                counts["unknown_provider_stages"] += 1
                problems.append(f"{stage_id}: provider join missing")
                continue
            if not provider_ok(provider, plan["model"], plan["arms"][arm]["pins"][slot["pin"]]):
                problems.append(f"{stage_id}: provider model/effort/pin mismatch or duplicate response")
                continue
            usage = (provider.get("usage") or {}).get("tokens") or {}
            for field in TOKEN_FIELDS:
                value = usage.get(field)
                if not isinstance(value, dict) or type(value.get("total")) is not int or value["total"] < 0:
                    counts["unknown_usage_fields"][field] += 1
        rows.append(dict(workflow_id=workflow_id, case=case, arm=arm, state=state))
        if state == "observed" and not any(stage.get("state") == "observed" for stage in stages):
            problems.append(f"{workflow_id}: observed workflow has no observed stage")
    if counts["missing_runs"] or counts["missing_stages"]:
        problems.append("planned workflow or stage missing")
    return dict(schema="cairn.token-efficiency-gate-report/1",
                state="incomplete" if problems else "ready_for_human_grade",
                independent_task_acceptance="unassessed", release_decision="defer",
                counts=counts, problems=problems, runs=rows,
                limitations=["Caller observations and selected metadata do not independently grade useful tasks.",
                             "Native provider usage is whole-turn evidence, not Cairn marginal cost.",
                             "Route and installed pins require separate effective deployment verification."])


def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_cmd = commands.add_parser("freeze")
    freeze_cmd.add_argument("--plan", type=Path, required=True)
    freeze_cmd.add_argument("--fixture", type=Path, required=True)
    freeze_cmd.add_argument("--output", type=Path, required=True)
    report_cmd = commands.add_parser("report")
    for flag in ("frozen", "fixture", "observations", "workflow-report", "output"):
        report_cmd.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            value = freeze(json.loads(args.plan.read_text()), args.fixture)
        else:
            value = report(json.loads(args.frozen.read_text()), args.fixture,
                           json.loads(args.observations.read_text()), json.loads(args.workflow_report.read_text()))
        write_new(args.output, value)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "state": value.get("state", "frozen")}))


if __name__ == "__main__":
    main()
