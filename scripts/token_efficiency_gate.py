#!/usr/bin/env python3
"""Mechanical CAIRN-227 paired-panel gate; never grades task usefulness.

Freeze a reviewed plan before trials. The report joins its exact workflow and
stage IDs to selected, opaque native workflow evidence. Caller observations
can establish coverage for review, but cannot establish independent acceptance.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat


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
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(rows, key, label):
    values = [row[key] for row in rows]
    require(len(values) == len(set(values)), f"duplicate {label}")


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError(f"non-JSON number: {value}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                      parse_constant=invalid_constant)


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
    selector = plan.get("capture_selector")
    if selector is not None:
        require(isinstance(selector, dict) and set(selector) == {
            "harness", "account", "id", "route", "effort_configuration"},
            "capture selector shape")
        require(selector["harness"] == "claude", "capture selector uses Claude CLI")
        for key in ("account", "id"):
            require(isinstance(selector[key], str) and ID.fullmatch(selector[key]),
                    f"capture selector {key}")
        require(selector["route"] == "subscription", "capture selector route must be subscription")
        require(selector["effort_configuration"] == "unset",
                "capture selector effort is not configured by the hook")
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
        if selector is not None:
            require(isinstance(pins.get("capture"), str) and ID.fullmatch(pins["capture"]),
                    f"{arm}: capture selector client/config pin")
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
            if slot["role"] == "capture":
                require(selector is not None and slot.get("pin") == "capture",
                        "capture stage requires frozen selector and capture pin")
                require(slot.get("capture_phase") in ("seed", "repeat"),
                        "capture stage phase")
            else:
                require(slot.get("pin") in ("api", "ordinary", "inbox", "client"),
                        "stage pin kind")
                require("capture_phase" not in slot, "non-capture stage phase")
            stages.append(slot["stage_id"])
    require(len(pairs) == len(set(pairs)), "duplicate case/arm pair")
    require(set(pairs) == {(case, arm) for case in CASES for arm in ARMS}, "missing case/arm pair")
    require(len(stages) == len(set(stages)), "stage IDs must be globally disjoint")
    frozen_at = _utc_timestamp(plan["frozen_at"], "plan: frozen_at") if "frozen_at" in plan else None
    if frozen_at is not None:
        require(frozen_at <= datetime.now(timezone.utc), "plan: frozen_at is in the future")
    validate_capacity_admission(plan, checked_at=frozen_at)
    return plan


def _utc_timestamp(value, label):
    require(isinstance(value, str), f"{label}: timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label}: invalid timestamp") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label}: timezone required")
    return parsed.astimezone(timezone.utc)


def validate_capacity_admission(plan, *, checked_at=None):
    """Require a fresh utilization stop policy for every subscription account."""
    admission = plan.get("capacity_admission")
    require(isinstance(admission, dict)
            and set(admission) == {"max_concurrent_workflows", "accounts"},
            "capacity admission evidence required")
    require(type(admission["max_concurrent_workflows"]) is int
            and admission["max_concurrent_workflows"] == 1,
            "panel provider admission must be sequential")
    accounts = admission["accounts"]
    require(isinstance(accounts, list) and accounts, "capacity admission accounts required")

    expected = [(plan["model"]["harness"], plan["model"]["account"], "task")]
    selector = plan.get("capture_selector")
    if selector is not None:
        expected.append((selector["harness"], selector["account"], "capture"))

    require(len(accounts) == len(expected), "one capacity evidence record per provider account required")
    seen = set()
    for row, (harness, account, purpose) in zip(accounts, expected):
        required = {"harness", "account", "purpose", "ceiling_used_percent",
                    "reserve_percentage_points", "evidence_path", "evidence_sha256"}
        require(isinstance(row, dict) and set(row) == required,
                f"{purpose}: exact capacity evidence shape required")
        require(row["harness"] == harness and row["account"] == account
                and row["purpose"] == purpose,
                f"{purpose}: capacity evidence account mismatch")
        identity = (harness, account)
        require(identity not in seen, "duplicate capacity evidence account")
        seen.add(identity)
        evidence = load_capacity_evidence(
            dict(evidence_path=row["evidence_path"], evidence_sha256=row["evidence_sha256"]), purpose=purpose)
        require(type(row["ceiling_used_percent"]) is int
                and 1 <= row["ceiling_used_percent"] <= 100,
                f"{purpose}: utilization ceiling must be from 1 to 100 percent")
        require(type(row["reserve_percentage_points"]) is int
                and 1 <= row["reserve_percentage_points"] <= 100,
                f"{purpose}: positive utilization reserve in percentage points required")
        validate_utilization_evidence(evidence, harness=harness, account=account, row=row,
                                      checked_at=checked_at)


def load_capacity_evidence(reference, *, purpose):
    require(isinstance(reference, dict)
            and set(reference) == {"evidence_path", "evidence_sha256"},
            f"{purpose}: capacity evidence reference required")
    path_value, expected_hash = reference["evidence_path"], reference["evidence_sha256"]
    require(isinstance(path_value, str) and os.path.isabs(path_value),
            f"{purpose}: absolute private capacity evidence path required")
    require(isinstance(expected_hash, str) and HEX.fullmatch(expected_hash),
            f"{purpose}: hash of private provider capacity evidence required")
    try:
        descriptor = os.open(path_value, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            evidence_stat = os.fstat(descriptor)
            evidence_mode = evidence_stat.st_mode
            require(stat.S_ISREG(evidence_mode) and evidence_stat.st_uid == os.getuid()
                    and evidence_mode & 0o077 == 0 and evidence_stat.st_size <= 5_000_000,
                    f"{purpose}: capacity evidence file must be owner-only regular file under 5 MB")
            content = os.read(descriptor, 5_000_001)
            require(len(content) <= 5_000_000,
                    f"{purpose}: capacity evidence file must be owner-only regular file under 5 MB")
        finally:
            os.close(descriptor)
    except OSError:
        raise ValueError(f"{purpose}: capacity evidence file unavailable") from None
    require(hashlib.sha256(content).hexdigest() == expected_hash,
            f"{purpose}: provider capacity evidence hash mismatch")
    try:
        return json.loads(content, object_pairs_hook=unique_pairs,
                          parse_constant=invalid_constant)
    except (ValueError, json.JSONDecodeError):
        raise ValueError(f"{purpose}: invalid capacity evidence document") from None


def validate_utilization_evidence(evidence, *, harness, account, row, started_at=None,
                                  workflow_id=None, stage_id=None, checked_at=None):
    """Check one owner-only subscription status sample against its frozen stop policy."""
    purpose = row["purpose"]
    evidence_keys = {"schema", "provider", "route", "harness", "account", "source_type", "measurement",
                     "ordinary_usage_allowed", "used_percent", "observed_at", "valid_until"}
    if workflow_id is not None or stage_id is not None:
        require(workflow_id is not None and stage_id is not None,
                f"{purpose}: complete workflow/stage identity required")
        evidence_keys |= {"workflow_id", "stage_id"}
    require(isinstance(evidence, dict) and set(evidence) == evidence_keys,
            f"{purpose}: exact provider utilization evidence schema required")
    expected_provider = {"codex": "openai", "claude": "anthropic"}.get(harness)
    require(evidence.get("schema") == "cairn.provider-capacity-evidence/1"
            and evidence.get("provider") == expected_provider
            and evidence.get("route") == "subscription"
            and evidence.get("harness") == harness
            and evidence.get("account") == account
            and evidence.get("source_type") == "caller_recorded"
            and evidence.get("measurement") == "utilization_status",
            f"{purpose}: provider utilization evidence identity mismatch")
    if workflow_id is not None:
        require(evidence.get("workflow_id") == workflow_id and evidence.get("stage_id") == stage_id,
                f"{purpose}: capacity sample does not identify this workflow and stage")
    observed_at = _utc_timestamp(evidence["observed_at"], f"{purpose}: observed_at")
    valid_until = _utc_timestamp(evidence["valid_until"], f"{purpose}: valid_until")
    cutoff = started_at or checked_at or datetime.now(timezone.utc)
    require(observed_at <= cutoff, f"{purpose}: utilization was not checked before provider turn")
    require((cutoff - observed_at).total_seconds() <= 60,
            f"{purpose}: utilization precheck older than 60 seconds")
    require(valid_until > cutoff and valid_until > observed_at,
            f"{purpose}: utilization evidence stale at provider turn")
    require((valid_until - observed_at).total_seconds() <= 60,
            f"{purpose}: utilization evidence validity window exceeds 60 seconds")
    require(evidence.get("ordinary_usage_allowed") is True,
            f"{purpose}: provider ordinary usage admission is false")
    require(type(evidence.get("used_percent")) is int
            and 0 <= evidence["used_percent"] <= 100,
            f"{purpose}: invalid utilization percentage")
    require(evidence["used_percent"] + row["reserve_percentage_points"]
            <= row["ceiling_used_percent"],
            f"{purpose}: utilization stop ceiling or per-run reserve reached")


def overlapping_provider_turns(intervals):
    """Return same-account turn overlaps, including non-adjacent interleavings."""
    latest_end_by_account = {}
    overlaps = []
    for started_at, ended_at, account, stage_id in sorted(intervals):
        previous = latest_end_by_account.get(account)
        if previous and started_at < previous[0]:
            overlaps.append((stage_id, previous[1]))
        if previous is None or ended_at > previous[0]:
            latest_end_by_account[account] = (ended_at, stage_id)
    return overlaps


def verify_fixture(plan, fixture):
    require(hashlib.sha256(fixture.read_bytes()).hexdigest() == plan["fixture_sha256"],
            "fixture hash mismatch")
    source = read_json(fixture)
    require(source.get("schema") == "cairn.token-efficiency-fixture/1", "fixture schema")
    rows = source.get("cases") or []
    require([row.get("id") for row in rows] == list(CASES), "fixture case IDs")
    for row in rows:
        check = row.get("acceptance_check")
        require(isinstance(check, (str, dict, list)) and check, f"{row['id']}: acceptance check")
        require(plan["acceptance_checks"][row["id"]] == sha(check),
                f"{row['id']}: acceptance check changed")
        capture = (row.get("setup") or {}).get("capture")
        if capture is not None:
            require(isinstance(capture, dict)
                    and capture.get("harness_ref") == "plan.model.harness"
                    and capture.get("model_ref") == "plan.capture_selector.id"
                    and plan.get("capture_selector") is not None,
                    f"{row['id']}: frozen capture selector required")
            if row["id"] == "unchanged_capture":
                runs = [run for run in plan.get("runs", []) if run.get("case") == row["id"]]
                require(len(runs) == len(ARMS), "unchanged_capture: four planned arms")
                for run in runs:
                    slots = [slot for slot in run.get("stages", []) if slot.get("role") == "capture"]
                    require([(slot.get("capture_phase"), slot.get("pin")) for slot in slots]
                            == [("seed", "capture"), ("repeat", "capture")],
                            f"{run['workflow_id']}: seed and repeat capture stages required")


def freeze(plan, fixture):
    require(isinstance(plan, dict) and "frozen_at" not in plan, "frozen_at is gate-owned")
    frozen_plan = copy.deepcopy(plan)
    frozen_plan["frozen_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    validate_plan(frozen_plan)
    verify_fixture(frozen_plan, fixture)
    return dict(schema="cairn.token-efficiency-frozen/1", plan_sha256=sha(frozen_plan),
                plan=frozen_plan)


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


def capture_provider_ok(provider, selector, pin):
    if not isinstance(provider, dict):
        return False
    efforts = provider.get("efforts")
    return (provider.get("duplicate_response_id") is False
            and provider.get("harness") == selector["harness"]
            and provider.get("account") == selector["account"]
            and provider.get("models") == [selector["id"]]
            and provider.get("pin") == pin
            and (efforts is None or isinstance(efforts, list)
                 and len(efforts) <= 1
                 and all(isinstance(value, str) and ID.fullmatch(value) for value in efforts)))


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
                  failed_runs=0, observed_runs=0, unjoined_observed_runs=0,
                  planned_stages=0, missing_stages=0,
                  unknown_stages=0, failed_stages=0, not_reached_stages=0, observed_stages=0,
                  skipped_capture_stages=0, unknown_effort_stages=0,
                  unknown_provider_stages=0, unknown_usage_fields={field: 0 for field in TOKEN_FIELDS})
    problems = []
    rows = []
    seen_events = set()
    seen_attempts = set()
    seen_capacity_samples = set()
    provider_intervals = []
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
        if (case == "unchanged_capture" and state == "observed"
                and any(slot["role"] == "capture" for slot in expected["stages"])):
            capture_stages = {slot["capture_phase"]: actual_stages.get(slot["stage_id"])
                              for slot in expected["stages"] if slot["role"] == "capture"}
            if (capture_stages.get("seed") or {}).get("state") != "observed":
                problems.append(f"{workflow_id}: observed capture requires provider-backed seed stage")
            if (capture_stages.get("repeat") or {}).get("state") != "skipped":
                problems.append(f"{workflow_id}: observed capture requires selected repeat skip stage")
        native_joined = False
        for stage_id, slot in expected_stages.items():
            stage = actual_stages.get(stage_id)
            if stage is None:
                counts["missing_stages"] += 1
                continue
            role = slot["role"]
            status = stage.get("state")
            require(status in ("observed", "failed", "not_reached", "unknown", "skipped"),
                    f"{stage_id}: state")
            if status == "skipped":
                require(role == "capture", f"{stage_id}: only capture may skip")
                require(slot["capture_phase"] == "repeat", f"{stage_id}: only repeat capture may skip")
                selector = plan["capture_selector"]
                require(stage.get("route") == selector["route"] and stage.get("model") == selector["id"]
                        and stage.get("effort") is None, f"{stage_id}: capture selector drift")
                require(stage.get("pins") == plan["arms"][arm]["pins"], f"{stage_id}: pin drift")
                require(stage.get("skip_reason") == "unchanged_confirmed_snapshot"
                        and isinstance(stage.get("capture_observation_ref"), str)
                        and UUID.fullmatch(stage["capture_observation_ref"])
                        and isinstance(stage.get("snapshot_sha256"), str)
                        and HEX.fullmatch(stage["snapshot_sha256"])
                        and type(stage.get("selector_calls")) is int and stage["selector_calls"] == 0
                        and type(stage.get("new_records")) is int and stage["new_records"] == 0
                        and stage.get("provider") is None,
                        f"{stage_id}: selected capture skip shape")
                counts["skipped_capture_stages"] += 1
                counts["unknown_effort_stages"] += 1
                problems.append(f"{stage_id}: capture skip is selected hook testimony; native observation join unavailable")
                continue
            counts[status + "_stages"] += 1
            if status == "unknown":
                problems.append(f"{stage_id}: stage evidence unknown")
            if status == "failed":
                require(isinstance(stage.get("failure_disposition"), str)
                        and ID.fullmatch(stage["failure_disposition"]),
                        f"{stage_id}: explicit failure disposition required")
            if status != "observed":
                continue
            if role == "capture" and slot["capture_phase"] == "repeat":
                problems.append(f"{stage_id}: repeated capture invoked selector instead of skipping")
            if native_row is None and slot["role"] in ("sender", "recipient"):
                problems.append(f"{stage_id}: observed stage has no native workflow")
                continue
            stage_model = plan["capture_selector"] if role == "capture" else plan["model"]
            require(stage.get("route") == stage_model["route"], f"{stage_id}: route drift")
            if role == "capture":
                require(stage.get("model") == stage_model["id"] and stage.get("effort") is None,
                        f"{stage_id}: capture selector model/effort drift")
            else:
                require(stage.get("model") == stage_model["id"]
                        and stage.get("effort") == stage_model["effort"], f"{stage_id}: model/effort drift")
            require(stage.get("pins") == plan["arms"][arm]["pins"], f"{stage_id}: pin drift")
            provider = None
            native_basis = None
            if native_row and role == "sender":
                provider = native_row.get("sender_provider")
                native_basis = provider.get("turn_join") if isinstance(provider, dict) else None
                if native_row.get("publication") == "failed":
                    problems.append(f"{stage_id}: native publication not committed")
            elif native_row and role == "recipient":
                attempt = stage.get("attempt")
                require(isinstance(attempt, str) and OPAQUE.fullmatch(attempt), f"{stage_id}: attempt")
                require(attempt not in seen_attempts, "one native attempt cannot serve two planned stages")
                seen_attempts.add(attempt)
                matches = [row for row in native_row.get("deliveries", []) if row.get("attempt") == attempt]
                require(len(matches) <= 1, f"{stage_id}: ambiguous native attempt")
                provider = matches[0].get("provider") if matches else None
                native_basis = matches[0].get("turn_join") if matches else None
                if matches and (matches[0].get("settlement") or {}).get("status") == "failed":
                    problems.append(f"{stage_id}: native attempt not settled")
            elif role in ("coordinator", "capture", "recovery"):
                # This selected evidence remains reviewable, not a native usage join.
                require(isinstance(stage.get("evidence_ref"), str) and ID.fullmatch(stage["evidence_ref"]),
                        f"{stage_id}: selected evidence reference")
                provider = stage.get("provider")
            if provider is None:
                counts["unknown_provider_stages"] += 1
                if role == "capture":
                    counts["unknown_effort_stages"] += 1
                problems.append(f"{stage_id}: provider join missing")
                continue
            pin = plan["arms"][arm]["pins"][slot["pin"]]
            if role == "capture":
                if not isinstance(provider, dict) or not provider.get("efforts"):
                    counts["unknown_effort_stages"] += 1
                provider_valid = capture_provider_ok(provider, stage_model, pin)
            else:
                provider_valid = provider_ok(provider, stage_model, pin)
            if not provider_valid:
                problems.append(f"{stage_id}: provider model/account/effort/pin mismatch or duplicate response")
                continue
            account_row = next((item for item in plan["capacity_admission"]["accounts"]
                                if item["harness"] == stage_model["harness"]
                                and item["account"] == stage_model["account"]), None)
            require(account_row is not None, f"{stage_id}: capacity policy for provider account missing")
            capacity_document = load_capacity_evidence(
                stage.get("capacity_check"), purpose=account_row["purpose"])
            capacity_identity = (stage["capacity_check"]["evidence_path"],
                                 stage["capacity_check"]["evidence_sha256"])
            require(capacity_identity not in seen_capacity_samples,
                    f"{stage_id}: capacity sample reused across provider stages")
            seen_capacity_samples.add(capacity_identity)
            require(isinstance(provider.get("started_at"), str),
                    f"{stage_id}: native provider start time required for admission join")
            started_at = _utc_timestamp(provider["started_at"], f"{stage_id}: provider started_at")
            if role in ("sender", "recipient"):
                if not isinstance(provider.get("ended_at"), str):
                    problems.append(f"{stage_id}: native provider end time missing for sequential admission check")
                else:
                    ended_at = _utc_timestamp(provider["ended_at"], f"{stage_id}: provider ended_at")
                    require(ended_at >= started_at, f"{stage_id}: provider end precedes start")
                    provider_intervals.append((started_at, ended_at, stage_model["account"], stage_id))
            validate_utilization_evidence(capacity_document, harness=stage_model["harness"],
                                          account=stage_model["account"], row=account_row,
                                          started_at=started_at, workflow_id=workflow_id,
                                          stage_id=stage_id)
            provider_usage = provider.get("usage") or {}
            if role == "capture":
                response_refs = provider.get("response_refs")
                observed_responses = provider_usage.get("observed_responses")
                response_ok = (type(observed_responses) is int and observed_responses > 0
                               and isinstance(response_refs, list)
                               and len(response_refs) == observed_responses
                               and all(isinstance(value, str) and OPAQUE.fullmatch(value)
                                       for value in response_refs)
                               and len(response_refs) == len(set(response_refs)))
                if not response_ok:
                    counts["unknown_provider_stages"] += 1
                    problems.append(f"{stage_id}: selected capture provider response missing")
            if role in ("sender", "recipient"):
                basis_ok = native_basis in ("binding", "unique_turn_id")
                completed = provider.get("native_completed") is True
                response_refs = provider.get("response_refs")
                response_ok = (type(provider_usage.get("observed_responses")) is int
                               and provider_usage["observed_responses"] > 0
                               and isinstance(response_refs, list)
                               and len(response_refs) == provider_usage["observed_responses"]
                               and all(isinstance(value, str) and OPAQUE.fullmatch(value)
                                       for value in response_refs)
                               and len(response_refs) == len(set(response_refs)))
                if not basis_ok:
                    problems.append(f"{stage_id}: native turn join missing")
                if not completed:
                    problems.append(f"{stage_id}: native turn incomplete")
                if not response_ok:
                    problems.append(f"{stage_id}: native response missing")
                if basis_ok and completed and response_ok:
                    native_joined = True
            usage = provider_usage.get("tokens") or {}
            for field in TOKEN_FIELDS:
                value = usage.get(field)
                if not isinstance(value, dict) or type(value.get("total")) is not int or value["total"] < 0:
                    counts["unknown_usage_fields"][field] += 1
        rows.append(dict(workflow_id=workflow_id, case=case, arm=arm, state=state))
        if state == "observed" and not any(stage.get("state") == "observed" for stage in stages):
            problems.append(f"{workflow_id}: observed workflow has no observed stage")
        if state == "observed" and not native_joined:
            counts["unjoined_observed_runs"] += 1
            problems.append(f"{workflow_id}: observed workflow has no native joined stage")
    problems.extend(f"{stage_id}: provider turn overlaps {previous} under sequential admission"
                    for stage_id, previous in overlapping_provider_turns(provider_intervals))
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
            value = freeze(read_json(args.plan), args.fixture)
        else:
            value = report(read_json(args.frozen), args.fixture,
                           read_json(args.observations), read_json(args.workflow_report))
        write_new(args.output, value)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "state": value.get("state", "frozen")}))


if __name__ == "__main__":
    main()
