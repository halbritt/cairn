#!/usr/bin/env python3
"""Materialize selected frozen panel setups through a disposable Cairn API.

This prepares native setup state, including one synthetic Store settlement.
It never launches an actor, observes a live turn, grades an answer, or emits
gate observations.
"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

import ordinary_task_receipt as receipt_binding
import preflight_token_efficiency_panel as panel


# Adapters bind fixture recipients and publishers to authenticated profiles,
# and guard the exact supported event and transition descriptors.
CASE_ADAPTERS = {
    "single_notice": {
        "events": [{"id": "notice_a", "kind": "notice", "recipient": "worker"}],
        "transitions": [], "publishers": {"notice_a": "sender"},
        "sessions": {"worker": "worker"},
    },
    "actionable_response": {
        "events": [{"id": "request_decision", "kind": "request", "recipient": "worker_a"},
                   {"id": "response_a", "kind": "response", "recipient": "worker",
                    "causation": "request_decision"}],
        "transitions": [],
        "publishers": {"request_decision": "sender", "response_a": "worker"},
        "sessions": {"worker_a": "worker", "worker": "sender"},
    },
    "duplicate_send": {
        "events": [{"id": "duplicate_a", "kind": "request", "recipient": "worker",
                    "idempotency_key": "ck8"}],
        "transitions": [{"phase": "after_first_publish", "action": "duplicate_publish",
                         "target": "duplicate_a"}],
        "publishers": {"duplicate_a": "sender"},
        "sessions": {"worker": "worker"},
    },
    "concurrent_receivers": {
        "events": [{"id": "request_a", "kind": "request", "recipient": "worker_a"},
                   {"id": "request_b", "kind": "request", "recipient": "worker_b"}],
        "transitions": [],
        "publishers": {"request_a": "sender", "request_b": "sender"},
        "sessions": {"worker_a": "worker", "worker_b": "other"},
    },
    "small_request_result_reply": {
        "events": [{"id": "request_hash", "kind": "request", "recipient": "worker"}],
        "transitions": [], "publishers": {"request_hash": "sender"},
        "sessions": {"worker": "worker", "sender": "sender"},
    },
    "routine_burst_urgent_request": {
        "events": [{"id": event_id, "kind": "notice", "recipient": "worker"}
                   for event_id in ("routine_1", "routine_2", "routine_3")]
                  + [{"id": "urgent_1", "kind": "request", "recipient": "worker"}],
        "transitions": [],
        "publishers": {event_id: "sender" for event_id in
                       ("routine_1", "routine_2", "routine_3", "urgent_1")},
        "sessions": {"worker": "worker"},
        "interventions": {
            "baseline": {"routine_notice_policy": None},
            "envelope": {"routine_notice_policy": None},
            "batch": {"routine_notice_policy": {"version": 1, "urgency": "routine",
                                                 "defer_seconds": 30}},
            "combined": {"routine_notice_policy": {"version": 1, "urgency": "routine",
                                                    "defer_seconds": 30}},
        },
    },
}


def case_name(workflow_id):
    parts = workflow_id.split(".")
    require(len(parts) == 2 and parts[1] in CASE_ADAPTERS
            and (parts[0] == "baseline" or parts[1] == "routine_burst_urgent_request"
                 and parts[0] in ("envelope", "batch", "combined")),
            "unsupported workflow")
    return parts[1]


def database_name(workflow_id):
    case = case_name(workflow_id)
    return f"cairn_panel_{workflow_id.replace('.', '_')}" if case == "routine_burst_urgent_request" else f"cairn_panel_{case}"


def require(value, message):
    if not value:
        raise ValueError(message)


def private_dir(path):
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700, f"owner-only directory required: {path}")


def private_file(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o600, f"owner-only file required: {path}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=panel.pairs_unique)


def write_new(path, value):
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def disposable_inputs(args):
    case = case_name(args.workflow_id)
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."),
            "disposable integration root required")
    private_dir(root)
    require((root / "data" / "PG_VERSION").is_file(), "disposable PostgreSQL cluster required")
    dsn = f"host={root / 'socket'} dbname={database_name(args.workflow_id)} sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "controller requires the dedicated disposable database")
    require((root / "socket").is_dir() and not (root / "socket").is_symlink(),
            "disposable socket directory required")
    binary = Path(args.binary)
    require(binary.is_file() and not binary.is_symlink() and binary.parent == root,
            "disposable Cairn binary required")
    home = Path(args.home)
    require(home.parent == root, "controller home must be under disposable root")
    private_dir(home)
    socket = home / "api.sock"
    require(socket.exists() and not socket.is_symlink() and stat.S_ISSOCK(socket.stat().st_mode),
            "disposable Cairn API socket required")
    export = Path(args.export)
    require(export.parent.parent == root, "private panel export must be under disposable root")
    private_dir(export.parent)
    private_dir(export)
    private_dir(export / "actor")
    private_file(export / "controller.json")
    private_file(export / "actor" / f"{case}.json")
    run_dir = Path(args.run_dir)
    require(run_dir.parent == root and run_dir.name not in ("", ".", ".."),
            "run directory must be a direct child of disposable root")
    token_paths = [args.sender_token, args.worker_token]
    if case == "concurrent_receivers":
        require(args.other_token is not None, "second worker token required")
        token_paths.append(args.other_token)
    for token in token_paths:
        path = Path(token)
        require(path.parent == home, "test token path must be in disposable home")
        private_file(path)
    return root, socket, export, run_dir


def frozen_case(export, fixture, workflow_id):
    case = case_name(workflow_id)
    adapter = CASE_ADAPTERS[case]
    require(Path(fixture).resolve(strict=True) == panel.FIXTURE.resolve(strict=True),
            "canonical frozen fixture required")
    data, fixture_hash = panel.read_fixture(fixture)
    expected = panel.manifest(data, fixture_hash)
    actual = read_json(export / "controller.json")
    require(actual == expected, "exported controller differs from frozen fixture")
    binding = receipt_binding.frozen_binding(actual, workflow_id)
    row = next(item for item in actual["cases"] if item["id"] == case)
    actor = read_json(export / "actor" / f"{case}.json")
    require(actor == row["actor"] and set(actor) == {"schema", "case", "task"}
            and panel.sha(actor) == binding["actor_sha256"], "actor export differs from frozen task")
    setup = row["setup"]
    descriptors = [{key: value for key, value in event.items() if key != "body"}
                   for event in setup["events"]]
    require(setup["sources"] == [] and descriptors == adapter["events"]
            and setup["transitions"] == adapter["transitions"], "unsupported panel setup")
    run = next(item for item in actual["prospective_runs"] if item["workflow_id"] == workflow_id)
    expected_intervention = (adapter["interventions"][run["arm"]]
                             if "interventions" in adapter else None)
    require(run["intervention"] == expected_intervention, "unsupported arm intervention")
    return binding, setup, actual["event_source_defaults"], adapter, expected_intervention, actor


def database_now(dsn):
    process = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                              "SELECT clock_timestamp()"], text=True, capture_output=True, timeout=10)
    require(process.returncode == 0 and len(process.stdout) < 128,
            "cannot verify disposable database clock")
    value = datetime.fromisoformat(process.stdout.strip())
    require(value.tzinfo is not None, "database clock lacks timezone")
    return value


def instant(value):
    require(isinstance(value, str), "native event timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "native event timestamp lacks timezone")
    return parsed


def call(binary, socket, token, operation, args=(), body=None, session=None):
    connection = ["--socket", str(socket), "--token-file", str(token)]
    if session is not None:
        connection += ["--agent-id", session["agent_id"],
                       "--execution-id", session["execution_id"]]
    if operation[0] == "agent":
        command = [str(binary), "agent", *connection, *operation[1:], *args]
    else:
        command = [str(binary), *operation, *connection, *args]
    process = subprocess.run(command, input=body, text=True, capture_output=True, timeout=20)
    require(len(process.stdout) <= 1_000_000, "Cairn API reply exceeds bound")
    result = json.loads(process.stdout, object_pairs_hook=panel.pairs_unique)
    require(process.returncode == 0 and result.get("schema") == "cairn.response/1"
            and result.get("ok") is True and result.get("status") == "OK"
            and isinstance(result.get("data"), dict),
            f"Cairn {operation[-1]} refused: {result.get('status')}")
    return result["data"]


def refused_readiness(binary, socket, token, session):
    command = [str(binary), "agent", "--socket", str(socket), "--token-file", str(token),
               "session-inbox-ready"]
    process = subprocess.run(command, input=json.dumps({"agent_id": session["agent_id"],
                                                         "execution_id": session["execution_id"]}),
                             text=True, capture_output=True, timeout=20)
    require(len(process.stdout) <= 1_000_000, "Cairn refusal exceeds bound")
    result = json.loads(process.stdout, object_pairs_hook=panel.pairs_unique)
    require(process.returncode != 0 and result.get("schema") == "cairn.response/1"
            and result.get("ok") is False and result.get("status") == "NOT_FOUND",
            "other worker session was visible to first worker profile")
    return "NOT_FOUND"


def registered_owner(dsn, agent_id):
    valid_id = str(uuid.UUID(agent_id))
    process = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                              f"SELECT owner FROM cairn.agent_session WHERE agent_id='{valid_id}'"],
                             text=True, capture_output=True, timeout=10)
    require(process.returncode == 0 and len(process.stdout) < 256,
            "cannot verify registered session owner")
    owner = process.stdout.strip()
    require(owner.startswith("agent/") and "\n" not in owner, "registered owner missing")
    return owner


def settle_small_request(binary, socket, tokens, repo, actor, setup, intent, sessions, events):
    request_spec = setup["events"][0]
    match = re.fullmatch(r"Return SHA-256 of ([a-z]+)\.", request_spec["body"])
    require(match is not None and match.group(1) == "cedar"
            and actor["task"] ==
            f"Answer the Cairn request with the lowercase hexadecimal SHA-256 of the UTF-8 bytes {match.group(1)}, then settle the request.",
            "unsupported frozen request/result contract")
    digest = hashlib.sha256(match.group(1).encode("utf-8")).hexdigest()
    require(digest == "0c274639936717793d7695ef0373039e397acdabc247d9953d3c9209a95044cd",
            "exact cedar digest differs")
    request = events["request_hash"]
    worker = sessions["worker"]
    sender = sessions["sender"]
    worker_ref = {key: worker[key] for key in ("agent_id", "execution_id")}
    if request["delivery_state"] == "pending":
        ready = call(binary, socket, tokens["worker"], ("agent", "session-inbox-ready"),
                     body=json.dumps(worker_ref))
        require(ready == {"event_id": request["event_id"],
                          "delivery_id": request["delivery_id"]},
                "worker readiness differs from exact request")
    claim_body = {"request_id": intent["settlement_request_ids"]["claim"],
                  "session": worker_ref, "delivery_id": request["delivery_id"],
                  "native_turn_id": intent["synthetic_turn_id"], "turn_exclusive": False}
    claimed = call(binary, socket, tokens["worker"], ("agent", "session-inbox-claim"),
                   body=json.dumps(claim_body))
    attempt = claimed.get("attempt") or {}
    delivered = attempt.get("delivery") or {}
    lease_id = delivered.get("lease_id")
    require(attempt.get("attempt_id") == claim_body["request_id"],
            "synthetic claim attempt ID differs from journal")
    require(attempt.get("native_turn_id") == claim_body["native_turn_id"]
            and attempt.get("turn_exclusive", False) is False,
            "synthetic claim turn boundary differs")
    require(delivered.get("delivery_id") == request["delivery_id"]
            and (delivered.get("event") or {}).get("event_id") == request["event_id"],
            "synthetic claim delivery differs from request")
    require(isinstance(lease_id, str) and str(uuid.UUID(lease_id)) == lease_id,
            "synthetic claim lease missing")
    source_history = call(binary, socket, tokens["worker"], ("agent", "history"),
                          body=json.dumps(request["source_ref"]), session=worker)
    source_versions = source_history.get("versions") or []
    require(len(source_versions) == 1
            and source_versions[0].get("body") == request_spec["body"]
            and source_versions[0].get("observed_writer") == request["observed_writer"],
            "claimed session cannot read exact request source")
    result_task = "coordination-result/" + request["delivery_id"]
    done = call(binary, socket, tokens["worker"], ("complete",),
                ("--request-id", intent["settlement_request_ids"]["completion"],
                 "--lease", lease_id, "--shareable", "--stdin", "--task", result_task,
                 "--run", "*", request["delivery_id"]),
                body=digest, session=worker)
    result_ref = done.get("result") or {}
    require(done.get("state") == "handled" and done.get("delivery_id") == request["delivery_id"]
            and result_ref.get("version") == 1 and done.get("completed_at"),
            "synthetic completion did not durably create one result")
    result_history = call(binary, socket, tokens["worker"], ("agent", "history"),
                          body=json.dumps(result_ref), session=worker)
    result_versions = result_history.get("versions") or []
    require(len(result_versions) == 1 and result_versions[0].get("body") == digest
            and result_versions[0].get("observed_writer") == worker["inbox"]
            and result_versions[0].get("kind") == "note"
            and result_versions[0].get("scope") ==
            {"repo": repo, "task_id": result_task, "run_id": "*"},
            "Store result differs from exact synthetic digest")
    request_status = call(binary, socket, tokens["sender"], ("event-status",),
                          (request["event_id"],))
    request_deliveries = request_status.get("deliveries") or []
    require(len(request_deliveries) == 1
            and request_deliveries[0].get("delivery_id") == request["delivery_id"]
            and request_deliveries[0].get("state") == "handled"
            and request_deliveries[0].get("attempts") == 1
            and request_deliveries[0].get("result") == result_ref,
            "request completion receipt differs from exact result")
    response_body = json.dumps({
        "request_id": intent["settlement_request_ids"]["response_publication"],
        "repo": repo, "kind": "response", "ref": result_ref,
        "destination": {"type": "agent", "name": sender["inbox"]},
        "causation_id": request["event_id"],
    }, sort_keys=True, separators=(",", ":"))
    reply = call(binary, socket, tokens["worker"], ("agent", "event-publish"),
                 body=response_body, session=worker)
    require(reply.get("kind") == "response" and reply.get("ref") == result_ref
            and reply.get("from") == worker["inbox"]
            and reply.get("destination") == {"type": "agent", "name": sender["inbox"]}
            and reply.get("causation_id") == request["event_id"],
            "synthetic response differs from exact result or cause")
    reply_status = call(binary, socket, tokens["worker"], ("event-status",),
                        (reply["event_id"],), session=worker)
    reply_deliveries = reply_status.get("deliveries") or []
    require(reply_status.get("event") == reply and len(reply_deliveries) == 1
            and reply_deliveries[0].get("consumer") == sender["inbox"]
            and reply_deliveries[0].get("state") == "pending"
            and reply_deliveries[0].get("attempts") == 0,
            "synthetic reply is not one pending sender delivery")
    sender_history = call(binary, socket, tokens["sender"], ("agent", "history"),
                          body=json.dumps(result_ref), session=sender)
    sender_versions = sender_history.get("versions") or []
    require(len(sender_versions) == 1 and sender_versions[0].get("body") == digest
            and sender_versions[0].get("observed_writer") == worker["inbox"],
            "sender session cannot read exact shareable result")
    request["delivery_state"] = "handled"
    request["attempts"] = 1
    return {"schema": "cairn.panel-synthetic-settlement/1",
            "synthetic_store_settlement": True, "actor_result": "unassessed",
            "claim_request_id": claim_body["request_id"],
            "claim_attempt_id": attempt["attempt_id"], "claim_lease_id": lease_id,
            "synthetic_turn_id": claim_body["native_turn_id"],
            "turn_exclusive": False, "attempt_reconciled": False,
            "completion_request_id": intent["settlement_request_ids"]["completion"],
            "request_event_id": request["event_id"],
            "request_delivery_id": request["delivery_id"], "request_state": "handled",
            "request_attempts": 1, "result_ref": result_ref,
            "result_body_sha256": hashlib.sha256(digest.encode("utf-8")).hexdigest(),
            "result_observed_writer": worker["inbox"],
            "response_publication_request_id": intent["settlement_request_ids"]["response_publication"],
            "response_event_id": reply["event_id"],
            "response_delivery_id": reply_deliveries[0]["delivery_id"],
            "response_causation_id": reply["causation_id"],
            "response_state": "pending", "response_attempts": 0}


def materialize(args):
    root, socket, export, run_dir = disposable_inputs(args)
    case = case_name(args.workflow_id)
    sender_token = Path(args.sender_token).read_bytes()
    worker_token = Path(args.worker_token).read_bytes()
    require(sender_token != worker_token,
            "sender and worker token identities must differ")
    other_token = None
    if case == "concurrent_receivers":
        other_token = Path(args.other_token).read_bytes()
        require(len({sender_token, worker_token, other_token}) == 3,
                "sender and both worker token identities must differ")
    binding, setup, defaults, adapter, intervention, actor = frozen_case(
        export, args.fixture, args.workflow_id)
    require(defaults == {"kind": "note", "record_class": "A", "operation": "create",
                         "sensitivity": "shareable", "claim_type": "self",
                         "scope": {"repo": "fixture:cairn-token-efficiency", "task_id": "*", "run_id": "*"}},
            "unsupported event source defaults")
    if not run_dir.exists():
        run_dir.mkdir(mode=0o700)
    private_dir(run_dir)
    intent_path = run_dir / "intent.json"
    binary_hash = hashlib.sha256(Path(args.binary).read_bytes()).hexdigest()
    immutable = {"schema": "cairn.panel-controller-intent/2", "workflow_id": args.workflow_id,
                 "fixture_sha256": binding["fixture_sha256"],
                 "actor_sha256": binding["actor_sha256"], "setup_sha256": binding["setup_sha256"],
                 "intervention_sha256": binding["intervention_sha256"],
                 "adapter_sha256": panel.sha(adapter), "binary_sha256": binary_hash,
                 "sender_principal": args.sender_principal,
                 "sender_token_sha256": hashlib.sha256(sender_token).hexdigest(),
                 "worker_token_sha256": hashlib.sha256(worker_token).hexdigest()}
    if other_token is not None:
        immutable["other_token_sha256"] = hashlib.sha256(other_token).hexdigest()
    event_ids = [event["id"] for event in setup["events"]]
    dynamic_keys = {"run_id", "register_request_ids", "source_request_ids",
                    "publication_request_ids"}
    if case == "small_request_result_reply":
        dynamic_keys |= {"settlement_request_ids", "synthetic_turn_id"}
    if intent_path.exists():
        private_file(intent_path)
        intent = read_json(intent_path)
        require({key: intent.get(key) for key in immutable} == immutable
                and set(intent) == set(immutable) | dynamic_keys
                and set(intent["register_request_ids"]) == set(adapter["sessions"])
                and set(intent["source_request_ids"]) == set(event_ids)
                and set(intent["publication_request_ids"]) == set(event_ids)
                and (case != "small_request_result_reply" or
                     set(intent["settlement_request_ids"]) ==
                     {"claim", "completion", "response_publication"}),
                "prior intent differs from frozen setup")
    else:
        run_id = str(uuid.uuid4())
        publication_ids = {event_id: str(uuid.uuid4()) for event_id in event_ids}
        for event in setup["events"]:
            if "idempotency_key" in event:
                publication_ids[event["id"]] = str(uuid.uuid5(
                    uuid.UUID(run_id), f'{case}/{event["idempotency_key"]}'))
        intent = immutable | {
            "run_id": run_id,
            "register_request_ids": {role: str(uuid.uuid4()) for role in adapter["sessions"]},
            "source_request_ids": {event_id: str(uuid.uuid4()) for event_id in event_ids},
            "publication_request_ids": publication_ids,
        }
        if case == "small_request_result_reply":
            intent["settlement_request_ids"] = {
                name: str(uuid.uuid4()) for name in
                ("claim", "completion", "response_publication")}
            intent["synthetic_turn_id"] = f"panel-synthetic/{run_id}"
        write_new(intent_path, intent)
    for value in (intent["run_id"], *intent["register_request_ids"].values(),
                  *intent["source_request_ids"].values(),
                  *intent["publication_request_ids"].values(),
                  *intent.get("settlement_request_ids", {}).values()):
        require(str(uuid.UUID(value)) == value, "invalid intent UUID")
    if case == "small_request_result_reply":
        require(intent["synthetic_turn_id"] == f'panel-synthetic/{intent["run_id"]}',
                "synthetic turn label differs from journal")
    for event in setup["events"]:
        if "idempotency_key" in event:
            expected_id = str(uuid.uuid5(uuid.UUID(intent["run_id"]),
                                         f'{case}/{event["idempotency_key"]}'))
            require(intent["publication_request_ids"][event["id"]] == expected_id,
                    "idempotency key differs from journaled native UUID")
    binary = Path(args.binary)
    tokens = {"sender": Path(args.sender_token), "worker": Path(args.worker_token)}
    if other_token is not None:
        tokens["other"] = Path(args.other_token)
    repo = defaults["scope"]["repo"]
    dsn = os.environ["CAIRN_DATABASE_URL"]
    sessions = {}
    owners = {"sender": args.sender_principal}
    for recipient, token_role in adapter["sessions"].items():
        session = call(binary, socket, tokens[token_role], ("agents", "register"),
                       ("--request-id", intent["register_request_ids"][recipient],
                        "--binding", f'panel-{intent["run_id"]}-{recipient}',
                        "--native-session", f'{intent["run_id"]}/{recipient}',
                        "--harness", "codex", "--project", "Cairn", "--workspace", str(root),
                        "--state", "idle", "--delivery-mode", "existing-session", "--repo", repo))
        metadata = session.get("metadata") or {}
        require(session.get("inbox") == "agent/" + session.get("agent_id", "")
                and session.get("execution_id") and session.get("agent_id")
                and session.get("repo") == repo
                and session.get("native_session_id") == f'{intent["run_id"]}/{recipient}'
                and metadata.get("harness") == "codex"
                and metadata.get("delivery_mode") == "existing-session",
                "registered session identity mismatch")
        owner = registered_owner(dsn, session["agent_id"])
        if token_role == "sender":
            require(owner == args.sender_principal, "sender session owner differs")
        else:
            require(owner != args.sender_principal, "sender cannot own worker session")
        if token_role in owners:
            require(owners[token_role] == owner, "profile owns inconsistent sessions")
        owners[token_role] = owner
        sessions[recipient] = {"agent_id": session["agent_id"],
                               "execution_id": session["execution_id"],
                               "inbox": session["inbox"], "observed_owner": owner}
    expected_roles = ({"sender", "worker", "other"} if case == "concurrent_receivers"
                      else {"sender", "worker"})
    require(set(owners) == expected_roles, "authenticated profiles differ from adapter")
    if case == "concurrent_receivers":
        require(len(set(owners.values())) == 3 and
                sessions["worker_a"]["inbox"] != sessions["worker_b"]["inbox"],
                "distinct worker owners and inboxes required")
    events = {}
    transitions = []
    last_position = 0
    delivery_ids = set()
    for event_spec in setup["events"]:
        event_id = event_spec["id"]
        publisher_role = adapter["publishers"][event_id]
        publisher_token = tokens[publisher_role]
        recipient = event_spec["recipient"]
        recipient_session = sessions[recipient]
        source = call(binary, socket, publisher_token, ("agent", "remember"),
                      ("--request-id", intent["source_request_ids"][event_id], "--repo", repo,
                       "--task", "*", "--run", "*", "--kind", "note", "--shareable", "--stdin"),
                      event_spec["body"])
        ref = {"record_id": source.get("record_id"), "version": source.get("version")}
        require(ref["version"] == 1 and source.get("body") == event_spec["body"]
                and source.get("scope") == defaults["scope"] and source.get("kind") == "note"
                and source.get("sensitivity") == "shareable" and source.get("claim_type") == "self"
                and source.get("observed_writer") == owners[publisher_role],
                "stored source does not match fixture or publisher")
        recipient_token = tokens[adapter["sessions"][recipient]]
        history = call(binary, socket, recipient_token, ("agent", "history"),
                       body=json.dumps(ref))
        versions = history.get("versions") or []
        require(len(versions) == 1 and versions[0].get("body") == event_spec["body"]
                and versions[0].get("observed_writer") == owners[publisher_role],
                "recipient cannot read exact source version")
        causation = ""
        publisher_session = None
        if "causation" in event_spec:
            prior = events.get(event_spec["causation"])
            require(prior is not None, "causal parent was not published first")
            causation = prior["event_id"]
            publisher_session = sessions[prior["recipient"]]
            require(publisher_session["observed_owner"] == owners[publisher_role],
                    "causal publisher does not own parent recipient session")
        destination = {"type": "agent", "name": recipient_session["inbox"]}
        payload = {"request_id": intent["publication_request_ids"][event_id],
                   "repo": repo, "kind": event_spec["kind"], "ref": ref,
                   "destination": destination}
        if causation:
            payload["causation_id"] = causation
        notice_policy = (intervention["routine_notice_policy"]
                         if intervention is not None and event_spec["kind"] == "notice" else None)
        if notice_policy is not None:
            payload["notice_policy"] = notice_policy
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        published = call(binary, socket, publisher_token, ("agent", "event-publish"),
                         body=body, session=publisher_session)
        event_publisher = (publisher_session["inbox"] if publisher_session else owners[publisher_role])
        require(published.get("ref") == ref and published.get("kind") == event_spec["kind"]
                and published.get("from") == event_publisher
                and published.get("destination") == destination
                and published.get("causation_id", "") == causation,
                "published event differs from source, publisher, recipient or cause")
        if case == "routine_burst_urgent_request":
            require(type(published.get("position")) is int
                    and published["position"] > last_position,
                    "burst event publication order changed")
            last_position = published["position"]
            require("task_deadline" not in published and "admission_expires_at" not in published
                    and "response_group" not in published,
                    "burst fixture does not authorize an event deadline")
            expected_policy = ({**notice_policy, "owner": owners[publisher_role]}
                               if notice_policy is not None else None)
            require(published.get("notice_policy") == expected_policy,
                    "publisher-owned routine notice policy differs")
        status = call(binary, socket, publisher_token, ("event-status",),
                      (published["event_id"],), session=publisher_session)
        deliveries = status.get("deliveries") or []
        require(status.get("event") == published and len(deliveries) == 1
                and deliveries[0].get("consumer") == recipient_session["inbox"],
                "native delivery differs from publication")
        if case == "small_request_result_reply":
            require(deliveries[0].get("state") in ("pending", "leased", "handled")
                    and deliveries[0].get("attempts") in (0, 1),
                    "request state cannot be recovered from journal")
        else:
            require(deliveries[0].get("state") == "pending"
                    and deliveries[0].get("attempts") == 0,
                    "pending native delivery differs from publication")
        available_at = deliveries[0].get("available_at")
        if case == "routine_burst_urgent_request":
            require(deliveries[0]["delivery_id"] not in delivery_ids,
                    "distinct burst events share a delivery")
            delivery_ids.add(deliveries[0]["delivery_id"])
            if notice_policy is None:
                require(available_at is None, "immediate burst event has deferred availability")
            else:
                require(instant(available_at) == instant(published["created_at"])
                        + timedelta(seconds=notice_policy["defer_seconds"]),
                        "routine notice availability differs from fixed policy deadline")
        events[event_id] = {"event_spec_sha256": panel.sha(event_spec),
                            "source_body_sha256": hashlib.sha256(event_spec["body"].encode()).hexdigest(),
                            "source_ref": ref, "observed_writer": source["observed_writer"],
                            "event_publisher": published["from"],
                            "publication_request_id": payload["request_id"],
                            "event_id": published["event_id"], "kind": published["kind"],
                            "causation_id": causation, "recipient": recipient,
                            "recipient_inbox": recipient_session["inbox"],
                            "delivery_id": deliveries[0]["delivery_id"],
                            "delivery_state": deliveries[0]["state"],
                            "attempts": deliveries[0]["attempts"]}
        if case == "routine_burst_urgent_request":
            events[event_id]["position"] = published["position"]
            events[event_id]["notice_policy"] = published.get("notice_policy")
            events[event_id]["available_at"] = available_at
            events[event_id]["created_at"] = published["created_at"]
        for transition in setup["transitions"]:
            if transition["phase"] == "after_first_publish" and transition["target"] == event_id:
                repeated = call(binary, socket, publisher_token, ("agent", "event-publish"),
                                body=body, session=publisher_session)
                require(repeated == published, "identical publish retry changed the event")
                repeated_status = call(binary, socket, publisher_token,
                                       ("event-status",), (published["event_id"],),
                                       session=publisher_session)
                retry_deliveries = repeated_status.get("deliveries") or []
                require(repeated_status.get("event") == published and len(retry_deliveries) == 1
                        and retry_deliveries[0].get("delivery_id") == deliveries[0]["delivery_id"]
                        and retry_deliveries[0].get("state") == "pending"
                        and retry_deliveries[0].get("attempts") == 0,
                        "identical publish retry changed delivery identity or state")
                transitions.append({"action": transition["action"], "target": event_id,
                                    "publication_request_id": payload["request_id"],
                                    "first_event_id": published["event_id"],
                                    "retry_event_id": repeated["event_id"],
                                    "delivery_id": deliveries[0]["delivery_id"]})
    readiness_snapshot = None
    if case == "routine_burst_urgent_request":
        worker_session = sessions["worker"]
        selected = events["urgent_1" if intervention["routine_notice_policy"] else "routine_1"]
        deadline = (min(instant(events[event_id]["available_at"]) for event_id in
                        ("routine_1", "routine_2", "routine_3"))
                    if intervention["routine_notice_policy"] else None)
        snapshot_path = run_dir / "readiness.json"
        if snapshot_path.exists():
            private_file(snapshot_path)
            readiness_snapshot = read_json(snapshot_path)
        else:
            ready = call(binary, socket, tokens["worker"], ("agent", "session-inbox-ready"),
                         body=json.dumps({"agent_id": worker_session["agent_id"],
                                          "execution_id": worker_session["execution_id"]}))
            observed_at = database_now(dsn)
            require(deadline is None or observed_at < deadline,
                    "routine notice window expired before priority readiness proof")
            require(ready == {"event_id": selected["event_id"],
                              "delivery_id": selected["delivery_id"]},
                    "native readiness differs from frozen burst order")
            readiness_snapshot = {"basis": "selected_native_ready_hint",
                                  "event_id": ready["event_id"],
                                  "delivery_id": ready["delivery_id"],
                                  "observed_at": observed_at.isoformat(),
                                  "earliest_routine_available_at": deadline.isoformat() if deadline else None}
            write_new(snapshot_path, readiness_snapshot)
        require(set(readiness_snapshot) == {"basis", "event_id", "delivery_id", "observed_at",
                                            "earliest_routine_available_at"}
                and readiness_snapshot["basis"] == "selected_native_ready_hint"
                and readiness_snapshot["event_id"] == selected["event_id"]
                and readiness_snapshot["delivery_id"] == selected["delivery_id"]
                and readiness_snapshot["earliest_routine_available_at"] ==
                    (deadline.isoformat() if deadline else None)
                and (deadline is None or instant(readiness_snapshot["observed_at"]) < deadline),
                "stored readiness snapshot differs from durable burst setup")
    synthetic_settlement = None
    recipient_isolation = None
    if case == "concurrent_receivers":
        for recipient, event_id, token_role in (("worker_a", "request_a", "worker"),
                                                ("worker_b", "request_b", "other")):
            session = sessions[recipient]
            ready = call(binary, socket, tokens[token_role], ("agent", "session-inbox-ready"),
                         body=json.dumps({"agent_id": session["agent_id"],
                                          "execution_id": session["execution_id"]}))
            require(ready == {"event_id": events[event_id]["event_id"],
                              "delivery_id": events[event_id]["delivery_id"]},
                    "native readiness differs from exact recipient delivery")
        b = events["request_b"]
        refusal = refused_readiness(binary, socket, tokens["worker"], sessions["worker_b"])
        b_status = call(binary, socket, tokens["sender"], ("event-status",),
                        (b["event_id"],))
        b_deliveries = b_status.get("deliveries") or []
        require(len(b_deliveries) == 1 and
                b_deliveries[0].get("delivery_id") == b["delivery_id"] and
                b_deliveries[0].get("consumer") == sessions["worker_b"]["inbox"] and
                b_deliveries[0].get("state") == "pending" and
                b_deliveries[0].get("attempts") == 0,
                "other worker delivery changed after cross-session refusal")
        recipient_isolation = {
            "basis": "native_readiness_and_cross_session_refusal",
            "worker_a": {"session_agent_id": sessions["worker_a"]["agent_id"],
                         "event_id": events["request_a"]["event_id"],
                         "delivery_id": events["request_a"]["delivery_id"]},
            "worker_b": {"session_agent_id": sessions["worker_b"]["agent_id"],
                         "event_id": b["event_id"], "delivery_id": b["delivery_id"]},
            "worker_a_to_worker_b_status": refusal,
            "worker_b_after_refusal": {"delivery_id": b["delivery_id"],
                                       "state": "pending", "attempts": 0},
        }
    if case == "small_request_result_reply":
        synthetic_settlement = settle_small_request(binary, socket, tokens, repo, actor, setup,
                                                    intent, sessions, events)
    receipt = {"schema": "cairn.panel-materialization/2", "workflow_id": args.workflow_id,
               "run_id": intent["run_id"], "fixture_sha256": binding["fixture_sha256"],
               "actor_sha256": binding["actor_sha256"], "setup_sha256": binding["setup_sha256"],
               "acceptance_check_sha256": binding["acceptance_check_sha256"],
               "intervention_sha256": binding["intervention_sha256"],
               "adapter_sha256": immutable["adapter_sha256"], "binary_sha256": binary_hash,
               "setup_harness": "codex",
               "sessions": sessions, "events": events, "transitions": transitions,
               "provider_called": False, "native_turn_join": "unimplemented",
               "task_outcome": "unassessed"}
    if readiness_snapshot is not None:
        receipt["readiness_snapshot"] = readiness_snapshot
        receipt["native_batch_transfer"] = "unrun"
    if synthetic_settlement is not None:
        receipt["synthetic_store_settlement"] = synthetic_settlement
    if recipient_isolation is not None:
        receipt["recipient_isolation"] = recipient_isolation
        receipt["native_actor_concurrency"] = "unrun"
    output = run_dir / "materialization.json"
    if output.exists():
        private_file(output)
        require(read_json(output) == receipt, "prior materialization receipt differs from durable store")
    else:
        write_new(output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=panel.FIXTURE)
    parser.add_argument("--export", type=Path, required=True)
    choices = tuple(f"baseline.{case}" for case in CASE_ADAPTERS)
    choices += tuple(f"{arm}.routine_burst_urgent_request" for arm in
                     ("envelope", "batch", "combined"))
    parser.add_argument("--workflow-id", choices=choices,
                        required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--sender-token", type=Path, required=True)
    parser.add_argument("--worker-token", type=Path, required=True)
    parser.add_argument("--other-token", type=Path)
    parser.add_argument("--sender-principal", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    case_name(args.workflow_id)
    result = materialize(args)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
