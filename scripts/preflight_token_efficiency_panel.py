#!/usr/bin/env python3
"""Validate the synthetic CAIRN-227 task panel and export separate trial inputs.

This validates declarative setup data. It does not execute setup, use Cairn,
call a provider, or establish a native workflow join or task acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from token_efficiency_gate import ARMS, CASES, sha


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "trials/token-efficiency/FIXTURE.json"
PRIVATE_PARENT = Path.home() / ".cache/cairn-token-efficiency-panels"
EVENT_KINDS = {"notice", "request", "response"}
ACTIONS = {
    "repeat_source", "start_task", "drop_reply", "retry_delivery", "remove_source",
    "expire_lease", "crash_recipient", "hide_receipt", "retry_same_response",
    "rotate_execution", "disable_semantic", "compact_context", "seed_capture",
    "repeat_capture", "apply_correction", "pause_after_source_check", "duplicate_publish",
}
EXPECTED = {
    "single_notice": (1, "notice", ()),
    "actionable_response": (2, "response", ()),
    "small_request_result_reply": (1, "request", ()),
    "long_source": (1, "request", ("repeat_source",)),
    "routine_burst_urgent_request": (4, "request", ()),
    "busy_owner_task": (1, "notice", ("start_task",)),
    "concurrent_receivers": (2, "request", ()),
    "duplicate_send": (1, "request", ("duplicate_publish",)),
    "completion_reply_lost": (1, "request", ("drop_reply", "retry_delivery")),
    "source_forgotten_before_read": (1, "request", ("remove_source",)),
    "lease_expiry": (1, "request", ("expire_lease",)),
    "process_crash": (1, "request", ("crash_recipient",)),
    "relay_uncertain_response": (1, "request", ("hide_receipt", "retry_same_response")),
    "stale_execution": (1, "request", ("rotate_execution",)),
    "required_conflict_group": (0, None, ()),
    "semantic_unavailable": (0, None, ("disable_semantic",)),
    "wrong_project_memory": (0, None, ()),
    "corrected_note": (0, None, ()),
    "compaction_before_after_settlement": (2, "notice", ("compact_context", "compact_context")),
    "unchanged_capture": (0, None, ("seed_capture", "repeat_capture")),
    "meaningful_owner_correction": (0, None, ("apply_correction",)),
    "unfinished_handoff": (0, None, ("pause_after_source_check",)),
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def exact_keys(value, keys, label, optional=()):
    require(isinstance(value, dict), f"{label}: object required")
    require(set(keys) <= set(value) and set(value) <= set(keys) | set(optional),
            f"{label}: unexpected or missing keys")


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_fixture(path=FIXTURE):
    raw = Path(path).read_bytes()
    require(len(raw) <= 128_000, "fixture too large")
    data = json.loads(raw, object_pairs_hook=pairs_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    validate(data)
    return data, hashlib.sha256(raw).hexdigest()


def capture_binding(capture, plan):
    """Resolve hook harness and auxiliary selector model from one frozen plan."""
    require(isinstance(capture, dict) and isinstance(plan, dict),
            "capture binding input")
    require("harness_ref" in capture and "model_ref" in capture,
            "capture model references")
    require(capture["harness_ref"] == "plan.model.harness"
            and capture["model_ref"] == "plan.capture_selector.id", "capture model references")
    model = plan.get("model")
    selector = plan.get("capture_selector")
    require(isinstance(model, dict) and isinstance(model.get("harness"), str)
            and model["harness"], "capture frozen hook harness")
    require(isinstance(selector, dict) and selector.get("harness") == "claude"
            and isinstance(selector.get("id"), str) and selector["id"]
            and selector.get("route") == "subscription"
            and selector.get("effort_configuration") == "unset",
            "capture frozen selector identity")
    return {"harness": model["harness"], "model": selector["id"]}


def validate(data):
    exact_keys(data, ("schema", "purpose", "sources", "event_source_defaults",
                      "arm_interventions", "cases"),
               "fixture")
    require(data["schema"] == "cairn.token-efficiency-fixture/1", "fixture schema")
    require(data["purpose"] == "Prospective synthetic paired tasks; no trial outcomes", "fixture purpose")
    require(data["event_source_defaults"] == {
        "kind": "note", "record_class": "A", "operation": "create",
        "sensitivity": "shareable", "claim_type": "self",
        "scope": {"repo": "fixture:cairn-token-efficiency", "task_id": "*", "run_id": "*"}},
        "event source defaults")
    interventions = data["arm_interventions"]
    policy = {"version": 1, "urgency": "routine", "defer_seconds": 30}
    require(interventions == {"routine_burst_urgent_request": {
        "baseline": {"routine_notice_policy": None},
        "envelope": {"routine_notice_policy": None},
        "batch": {"routine_notice_policy": policy},
        "combined": {"routine_notice_policy": policy}}},
        "arm-specific routine notice policy")
    sources = data["sources"]
    require(isinstance(sources, dict) and 1 <= len(sources) <= 32, "source catalog")
    for name, source in sources.items():
        exact_keys(source, ("project", "body", "kind", "record_class", "sensitivity",
                            "claim_type",
                            "scope", "operation", "record_family", "logical_version",
                            "supersedes", "conflicts_with"), f"source {name}", ("authority",))
        require(isinstance(name, str) and name.isidentifier(), "source ID")
        require(source["project"] in ("Cairn", "Trail"), f"{name}: project")
        require(source["record_class"] in ("A", "C"), f"{name}: class")
        if source["record_class"] == "A":
            require(source["kind"] in ("note", "procedure", "preference")
                    and source["operation"] in ("create", "edit")
                    and "authority" not in source, f"{name}: ordinary record")
        else:
            require(source["kind"] == "instruction" and source["operation"] == "issue",
                    f"{name}: required instruction")
            require(source.get("authority") == {
                "grant": "disposable_operator_root", "mandatory": True,
                "requires_runtime": False, "category": "workflow",
                "policy_key": "synthetic-evacuation-threshold"}, f"{name}: issue authority")
        require(source["sensitivity"] == "shareable", f"{name}: sensitivity")
        require(source["claim_type"] == "self", f"{name}: claim type")
        require(source["scope"] == {"repo": "fixture:cairn-token-efficiency",
                                    "task_id": "*", "run_id": "*"}, f"{name}: shared scope")
        require(isinstance(source["record_family"], str)
                and source["record_family"].isidentifier(), f"{name}: record family")
        require(type(source["logical_version"]) is int and source["logical_version"] > 0,
                f"{name}: logical version")
        for relation in ("supersedes", "conflicts_with"):
            refs = source[relation]
            require(isinstance(refs, list) and all(isinstance(ref, str) for ref in refs)
                    and len(refs) == len(set(refs)) and name not in refs
                    and all(ref in sources for ref in refs), f"{name}: {relation}")
        require(isinstance(source["body"], str) and 20 <= len(source["body"]) <= 2000,
                f"{name}: source body")
    for name, source in sources.items():
        for previous in source["supersedes"]:
            require(sources[previous]["scope"] == source["scope"]
                    and sources[previous]["kind"] == source["kind"]
                    and sources[previous]["record_family"] == source["record_family"]
                    and sources[previous]["logical_version"] + 1 == source["logical_version"]
                    and source["operation"] == "edit"
                    and sources[previous]["operation"] == "create",
                    f"{name}: invalid supersession")
        for other in source["conflicts_with"]:
            require(name in sources[other]["conflicts_with"]
                    and source["record_class"] == sources[other]["record_class"] == "C"
                    and source["scope"] == sources[other]["scope"]
                    and source["authority"]["policy_key"] == sources[other]["authority"]["policy_key"],
                    f"{name}: invalid required conflict")
    rows = data["cases"]
    require(isinstance(rows, list) and len(rows) == len(CASES)
            and all(isinstance(row, dict) for row in rows)
            and [row.get("id") for row in rows] == list(CASES),
            "exact ordered 22 case IDs")
    for row in rows:
        case = row["id"]
        exact_keys(row, ("id", "actor_task", "setup", "acceptance_check"), case)
        task = row["actor_task"]
        require(isinstance(task, str) and 30 <= len(task) <= 500, f"{case}: actor task")
        setup = row["setup"]
        exact_keys(setup, ("sources", "events", "transitions"), f"{case}: setup",
                   ("capture",))
        source_ids = setup["sources"]
        require(isinstance(source_ids, list) and len(source_ids) == len(set(source_ids))
                and all(isinstance(name, str) and name in sources for name in source_ids),
                f"{case}: source order")
        events = setup["events"]
        require(isinstance(events, list), f"{case}: events")
        ids = set()
        for position, event in enumerate(events):
            exact_keys(event, ("id", "kind", "recipient", "body"), f"{case}: event",
                       ("idempotency_key", "causation"))
            require(all(isinstance(event[key], str) and 1 <= len(event[key]) <= 500
                        for key in ("id", "kind", "recipient", "body")), f"{case}: event text")
            require(event["id"].isidentifier() and event["id"] not in ids, f"{case}: event ID")
            require(event["kind"] in EVENT_KINDS, f"{case}: event kind")
            require(event["recipient"] in ("worker", "worker_a", "worker_b"),
                    f"{case}: recipient")
            if event["kind"] == "response":
                cause = event.get("causation")
                require(isinstance(cause, str)
                        and any(prior["id"] == cause and prior["kind"] == "request"
                                for prior in events[:position]),
                        f"{case}: response causation")
            else:
                require("causation" not in event, f"{case}: non-response causation")
            if "idempotency_key" in event:
                require(isinstance(event["idempotency_key"], str)
                        and event["idempotency_key"].isidentifier(), f"{case}: idempotency key")
            ids.add(event["id"])
        capture = setup.get("capture")
        if case == "unchanged_capture":
            # Controller-only synthetic selector output. It is not a provider
            # observation, actor input, or permission to seed a digest directly.
            exact_keys(capture, ("target", "harness_ref", "hook_event", "model_ref",
                                 "dialogue", "seed_selection"), f"{case}: capture")
            require(capture["target"] == "capture_same"
                    and capture["harness_ref"] == "plan.model.harness"
                    and capture["hook_event"] == "PreCompact"
                    and capture["model_ref"] == "plan.capture_selector.id",
                    f"{case}: capture identity")
            dialogue = capture["dialogue"]
            require(isinstance(dialogue, list) and len(dialogue) == 2,
                    f"{case}: dialogue")
            for role, message in zip(("user", "assistant"), dialogue):
                exact_keys(message, ("role", "text"), f"{case}: message")
                require(message["role"] == role and isinstance(message["text"], str)
                        and 20 <= len(message["text"]) <= 500,
                        f"{case}: message content")
            selection = capture["seed_selection"]
            exact_keys(selection, ("checkpoint", "workstream", "memories"),
                       f"{case}: seed selection")
            require(selection["checkpoint"] is None and selection["workstream"] is None
                    and isinstance(selection["memories"], list)
                    and len(selection["memories"]) == 1,
                    f"{case}: one synthetic selected memory")
            selected = selection["memories"][0]
            exact_keys(selected, ("record_id", "kind", "title", "body"),
                       f"{case}: selected memory")
            require(selected["record_id"] is None and selected["kind"] == "procedure"
                    and isinstance(selected["title"], str)
                    and 5 <= len(selected["title"]) <= 90
                    and isinstance(selected["body"], str)
                    and 20 <= len(selected["body"]) <= 2000,
                    f"{case}: selected memory content")
        else:
            require("capture" not in setup, f"{case}: unexpected capture setup")
        transitions = setup["transitions"]
        require(isinstance(transitions, list), f"{case}: transitions")
        for transition in transitions:
            exact_keys(transition, ("phase", "action", "target"), f"{case}: transition", ("count",))
            require(all(isinstance(transition[key], str) and transition[key]
                        for key in ("phase", "action", "target")), f"{case}: transition text")
            require(transition["action"] in ACTIONS, f"{case}: transition action")
            targets = ids | set(source_ids) | {"parser_review"}
            if capture is not None:
                targets.add(capture["target"])
            require(transition["target"] in targets,
                    f"{case}: undeclared transition target")
            require("count" not in transition or transition["action"] == "repeat_source"
                    and type(transition["count"]) is int and 2 <= transition["count"] <= 200,
                    f"{case}: repeat count")
        expected_count, expected_kind, actions = EXPECTED[case]
        require(len(events) == expected_count, f"{case}: event count")
        if expected_kind:
            require(expected_kind in {event["kind"] for event in events}, f"{case}: event kind")
        require(tuple(item["action"] for item in transitions) == actions,
                f"{case}: transition sequence")
        if case == "unchanged_capture":
            require([(item["phase"], item["action"], item["target"])
                     for item in transitions] == [
                         ("before_capture", "seed_capture", capture["target"]),
                         ("at_capture", "repeat_capture", capture["target"])],
                    f"{case}: capture transition binding")
        check = row["acceptance_check"]
        exact_keys(check, ("must", "must_not"), f"{case}: acceptance check")
        for key in ("must", "must_not"):
            require(isinstance(check[key], list) and len(check[key]) >= 2
                    and all(isinstance(item, str) and 12 <= len(item) <= 300 for item in check[key]),
                    f"{case}: {key}")
    require([event.get("idempotency_key") for event in rows[7]["setup"]["events"]]
            == ["ck8"], "duplicate-send identity")
    require(rows[6]["setup"]["events"][0]["recipient"] == "worker_a"
            and rows[6]["setup"]["events"][1]["recipient"] == "worker_b",
            "concurrent receiver custody")
    require([sources[name]["record_class"] for name in rows[14]["setup"]["sources"]]
            == ["C", "C"], "required conflict setup")
    require([sources[name]["project"] for name in rows[16]["setup"]["sources"]]
            == ["Trail", "Cairn"], "cross-project setup")
    require(rows[17]["setup"]["sources"] == ["old_export", "export_correction"]
            and sources["export_correction"]["supersedes"] == ["old_export"],
            "corrected note setup")


def manifest(data, fixture_hash):
    cases = []
    runs = []
    for row in data["cases"]:
        actor = {"schema": "cairn.token-efficiency-actor-task/1", "case": row["id"],
                 "task": row["actor_task"]}
        cases.append({"id": row["id"], "actor_sha256": sha(actor),
                      "setup_sha256": sha(row["setup"]),
                      "acceptance_check_sha256": sha(row["acceptance_check"]),
                      "actor": actor, "setup": row["setup"],
                      "acceptance_check": row["acceptance_check"]})
        for arm in ARMS:
            intervention = data["arm_interventions"].get(row["id"], {}).get(arm)
            runs.append({"workflow_id": f"{arm}.{row['id']}", "case": row["id"],
                         "arm": arm, "actor_sha256": sha(actor),
                         "setup_sha256": sha(row["setup"]),
                         "acceptance_check_sha256": sha(row["acceptance_check"]),
                         "intervention_sha256": sha(intervention),
                         "intervention": intervention})
    source_hashes = {name: hashlib.sha256(source["body"].encode()).hexdigest()
                     for name, source in data["sources"].items()}
    source_record_hashes = {name: sha(source) for name, source in data["sources"].items()}
    return {"schema": "cairn.token-efficiency-panel-manifest/1",
            "fixture_sha256": fixture_hash, "sources": data["sources"],
            "event_source_defaults": data["event_source_defaults"],
            "arm_interventions": data["arm_interventions"],
            "source_body_sha256": source_hashes,
            "source_record_sha256": source_record_hashes, "cases": cases,
            "prospective_runs": runs, "setup_executed": False, "provider_called": False,
            "native_join": "unimplemented", "independent_task_acceptance": "unassessed",
            "release": "defer"}


def write_new(path, value):
    payload = json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def export(data, fixture_hash, output):
    parent = PRIVATE_PARENT
    require(parent.exists() and parent.is_dir() and not parent.is_symlink(),
            f"create private parent first: {parent}")
    require(parent.stat().st_uid == os.getuid() and stat.S_IMODE(parent.stat().st_mode) == 0o700,
            "output parent must be owner-only 0700")
    output = Path(output)
    require(output.parent == parent and output.name not in ("", ".", ".."),
            "output must be a new direct child of the private parent")
    output.mkdir(mode=0o700)
    try:
        actor_dir = output / "actor"
        actor_dir.mkdir(mode=0o700)
        for row in data["cases"]:
            write_new(actor_dir / f"{row['id']}.json",
                      {"schema": "cairn.token-efficiency-actor-task/1",
                       "case": row["id"], "task": row["actor_task"]})
        write_new(output / "controller.json", manifest(data, fixture_hash))
    except BaseException:
        for path in (output / "actor").glob("*.json"):
            path.unlink()
        (output / "actor").rmdir() if (output / "actor").exists() else None
        (output / "controller.json").unlink(missing_ok=True)
        output.rmdir()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path, help="new direct child of private 0700 cache parent")
    args = parser.parse_args()
    data, fixture_hash = read_fixture(args.fixture)
    result = manifest(data, fixture_hash)
    if args.output:
        export(data, fixture_hash, args.output)
    print(json.dumps({key: result[key] for key in
                      ("fixture_sha256", "setup_executed", "provider_called", "native_join",
                       "independent_task_acceptance", "release")}
                     | {"cases": len(result["cases"]), "prospective_runs": len(result["prospective_runs"])},
                     sort_keys=True))


if __name__ == "__main__":
    main()
