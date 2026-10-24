#!/usr/bin/env python3
"""Materialize one frozen panel setup through a disposable Cairn API.

This prepares a pending native inbox delivery. It never launches an actor,
claims a turn, grades an answer, or emits gate observations.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

import ordinary_task_receipt as receipt_binding
import preflight_token_efficiency_panel as panel


DATABASE = "cairn_panel_controller"
WORKFLOW = "baseline.single_notice"


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
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."),
            "disposable integration root required")
    private_dir(root)
    require((root / "data" / "PG_VERSION").is_file(), "disposable PostgreSQL cluster required")
    dsn = f"host={root / 'socket'} dbname={DATABASE} sslmode=disable"
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
    private_file(export / "actor" / "single_notice.json")
    run_dir = Path(args.run_dir)
    require(run_dir.parent == root and run_dir.name not in ("", ".", ".."),
            "run directory must be a direct child of disposable root")
    for token in (args.sender_token, args.worker_token):
        path = Path(token)
        require(path.parent == home, "test token path must be in disposable home")
        private_file(path)
    return root, socket, export, run_dir


def frozen_case(export, fixture):
    require(Path(fixture).resolve(strict=True) == panel.FIXTURE.resolve(strict=True),
            "canonical frozen fixture required")
    data, fixture_hash = panel.read_fixture(fixture)
    expected = panel.manifest(data, fixture_hash)
    actual = read_json(export / "controller.json")
    require(actual == expected, "exported controller differs from frozen fixture")
    binding = receipt_binding.frozen_binding(actual, WORKFLOW)
    row = next(item for item in actual["cases"] if item["id"] == "single_notice")
    actor = read_json(export / "actor" / "single_notice.json")
    require(actor == row["actor"] and set(actor) == {"schema", "case", "task"}
            and panel.sha(actor) == binding["actor_sha256"], "actor export differs from frozen task")
    setup = row["setup"]
    require(setup["sources"] == [] and len(setup["events"]) == 1
            and setup["transitions"] == [] and setup["events"][0]["id"] == "notice_a"
            and setup["events"][0]["kind"] == "notice"
            and setup["events"][0]["recipient"] == "worker", "unsupported panel setup")
    run = next(item for item in actual["prospective_runs"] if item["workflow_id"] == WORKFLOW)
    require(run["intervention"] is None, "baseline notice must not add arm policy")
    return binding, setup["events"][0], actual["event_source_defaults"]


def call(binary, socket, token, operation, args=(), body=None):
    connection = ["--socket", str(socket), "--token-file", str(token)]
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


def materialize(args):
    root, socket, export, run_dir = disposable_inputs(args)
    sender_token = Path(args.sender_token).read_bytes()
    worker_token = Path(args.worker_token).read_bytes()
    require(sender_token != worker_token,
            "sender and worker token identities must differ")
    binding, event_spec, defaults = frozen_case(export, args.fixture)
    require(defaults == {"kind": "note", "record_class": "A", "operation": "create",
                         "sensitivity": "shareable", "claim_type": "self",
                         "scope": {"repo": "fixture:cairn-token-efficiency", "task_id": "*", "run_id": "*"}},
            "unsupported event source defaults")
    if not run_dir.exists():
        run_dir.mkdir(mode=0o700)
    private_dir(run_dir)
    intent_path = run_dir / "intent.json"
    event_hash = panel.sha(event_spec)
    binary_hash = hashlib.sha256(Path(args.binary).read_bytes()).hexdigest()
    immutable = {"schema": "cairn.panel-controller-intent/1", "workflow_id": WORKFLOW,
                 "fixture_sha256": binding["fixture_sha256"],
                 "actor_sha256": binding["actor_sha256"], "setup_sha256": binding["setup_sha256"],
                 "event_sha256": event_hash, "binary_sha256": binary_hash,
                 "sender_principal": args.sender_principal,
                 "sender_token_sha256": hashlib.sha256(sender_token).hexdigest(),
                 "worker_token_sha256": hashlib.sha256(worker_token).hexdigest()}
    if intent_path.exists():
        private_file(intent_path)
        intent = read_json(intent_path)
        require({key: intent.get(key) for key in immutable} == immutable
                and set(intent) == set(immutable) | {"run_id", "register_request_id",
                                                       "source_request_id", "publication_request_id"},
                "prior intent differs from frozen setup")
    else:
        intent = immutable | {key: str(uuid.uuid4()) for key in
                              ("run_id", "register_request_id", "source_request_id",
                               "publication_request_id")}
        write_new(intent_path, intent)
    binary = Path(args.binary)
    sender = Path(args.sender_token)
    worker = Path(args.worker_token)
    repo = defaults["scope"]["repo"]
    session = call(binary, socket, worker, ("agents", "register"),
                   ("--request-id", intent["register_request_id"], "--binding", "panel-" + intent["run_id"],
                    "--native-session", intent["run_id"], "--harness", "codex", "--project", "Cairn",
                    "--workspace", str(root), "--state", "idle", "--delivery-mode", "existing-session",
                    "--repo", repo))
    require(session.get("inbox") == "agent/" + session.get("agent_id", "")
            and session.get("execution_id") and session.get("agent_id"),
            "registered session identity mismatch")
    dsn = os.environ["CAIRN_DATABASE_URL"]
    worker_owner = registered_owner(dsn, session["agent_id"])
    require(worker_owner != args.sender_principal, "sender cannot own worker session")
    source = call(binary, socket, sender, ("agent", "remember"),
                  ("--request-id", intent["source_request_id"], "--repo", repo, "--task", "*",
                   "--run", "*", "--kind", "note", "--shareable", "--stdin"), event_spec["body"])
    ref = {"record_id": source.get("record_id"), "version": source.get("version")}
    require(ref["version"] == 1 and source.get("body") == event_spec["body"]
            and source.get("scope") == defaults["scope"] and source.get("kind") == "note"
            and source.get("sensitivity") == "shareable" and source.get("claim_type") == "self"
            and source.get("observed_writer") == args.sender_principal,
            "stored source does not match fixture")
    history = call(binary, socket, worker, ("agent", "history"),
                   body=json.dumps({"record_id": ref["record_id"], "version": ref["version"]}))
    versions = history.get("versions") or []
    require(len(versions) == 1 and versions[0].get("body") == event_spec["body"]
            and versions[0].get("observed_writer") == args.sender_principal,
            "recipient cannot read exact source version")
    published = call(binary, socket, sender, ("agent", "event-publish"),
                     body=json.dumps({"request_id": intent["publication_request_id"], "repo": repo,
                                      "kind": "notice", "ref": ref,
                                      "destination": {"type": "agent", "name": session["inbox"]}}))
    require(published.get("ref") == ref and published.get("kind") == "notice"
            and published.get("from") == args.sender_principal
            and published.get("destination") == {"type": "agent", "name": session["inbox"]},
            "published event does not match source and recipient")
    status = call(binary, socket, sender, ("event-status",), (published["event_id"],))
    deliveries = status.get("deliveries") or []
    require(status.get("event") == published and len(deliveries) == 1
            and deliveries[0].get("state") == "pending" and deliveries[0].get("attempts") == 0
            and deliveries[0].get("consumer") == session["inbox"],
            "pending native delivery differs from publication")
    receipt = {"schema": "cairn.panel-materialization/1", "workflow_id": WORKFLOW,
               "run_id": intent["run_id"], "fixture_sha256": binding["fixture_sha256"],
               "actor_sha256": binding["actor_sha256"], "setup_sha256": binding["setup_sha256"],
               "acceptance_check_sha256": binding["acceptance_check_sha256"],
               "intervention_sha256": binding["intervention_sha256"],
               "event_sha256": event_hash,
               "binary_sha256": binary_hash,
               "source_body_sha256": hashlib.sha256(event_spec["body"].encode()).hexdigest(),
               "source_ref": ref, "observed_writer": source["observed_writer"],
               "event_id": published["event_id"], "delivery_id": deliveries[0]["delivery_id"],
               "agent_id": session["agent_id"], "execution_id": session["execution_id"],
               "observed_worker_owner": worker_owner,
               "recipient_inbox": session["inbox"], "delivery_state": "pending", "attempts": 0,
               "provider_called": False, "native_turn_join": "unimplemented",
               "task_outcome": "unassessed"}
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
    parser.add_argument("--workflow-id", choices=(WORKFLOW,), required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--sender-token", type=Path, required=True)
    parser.add_argument("--worker-token", type=Path, required=True)
    parser.add_argument("--sender-principal", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    require(args.workflow_id == WORKFLOW, "unsupported workflow")
    result = materialize(args)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
