#!/usr/bin/env python3
"""Exercise one selected panel setup in this invocation's disposable database."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
import uuid

import preflight_token_efficiency_panel as panel


def require(value, message):
    if not value:
        raise AssertionError(message)


def check(binary, workflow_id):
    require(workflow_id in ("baseline.single_notice", "baseline.actionable_response",
                            "baseline.duplicate_send"), "unsupported workflow")
    case = workflow_id.split(".", 1)[1]
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."), "fresh cluster root")
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "private cluster root")
    require((root / "data" / "PG_VERSION").is_file(), "cluster not initialized")
    require(binary == root / "cairn" and binary.is_file(), "disposable binary")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_{case} sslmode=disable"
    require(os.environ["CAIRN_TEST_DATABASE_URL"] == os.environ["CAIRN_DATABASE_URL"] == dsn,
            "dedicated database")

    export_parent = root / f"panel-export-{case}"
    export_parent.mkdir(mode=0o700)
    panel.PRIVATE_PARENT = export_parent
    export = export_parent / "frozen"
    fixture, fixture_hash = panel.read_fixture()
    panel.export(fixture, fixture_hash, export)
    home = root / f"panel-controller-home-{case}"
    home.mkdir(mode=0o700)
    principals = {role: f"agent/panel-{role}-{uuid.uuid4()}"
                  for role in ("sender", "worker", "other")}
    identities = []
    for role, principal in principals.items():
        token = secrets.token_urlsafe(32)
        token_path = home / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": principal, "repo": "fixture:cairn-token-efficiency",
                           "role": "agent", "destination": "hosted"})
    config = home / "identities.json"
    config.write_text(json.dumps(identities), encoding="utf-8")
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                text=True, capture_output=True, env=env, timeout=10)
        require(result.returncode == 0, result.stderr)
        return result.stdout.strip()

    require(sql("SELECT count(*) FROM cairn.agent_event") == "0", "event table not fresh")
    require(sql("SELECT count(*) FROM cairn.agent_session") == "0", "session table not fresh")
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True)

    def cli(role, *args, body=None, session=None):
        connection = ["--socket", str(home / "api.sock"),
                      "--token-file", str(home / f"{role}.token")]
        if session is not None:
            connection += ["--agent-id", session["agent_id"],
                           "--execution-id", session["execution_id"]]
        result = subprocess.run([str(binary), args[0], *connection, *args[1:]],
                                input=body, text=True, capture_output=True, env=env, timeout=20)
        envelope = json.loads(result.stdout)
        require(result.returncode == 0 and envelope["status"] == "OK", (args, envelope, result.stderr))
        return envelope["data"]

    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            if server.poll() is not None:
                raise AssertionError(server.stderr.read())
            require(time.monotonic() < deadline, "panel API startup timed out")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode), "API socket required")
        for role in principals:
            result = subprocess.run([str(binary), "agent", "--socket", str(home / "api.sock"),
                                     "--token-file", str(home / f"{role}.token"), "version"],
                                    text=True, capture_output=True, env=env, timeout=10)
            require(result.returncode == 0 and json.loads(result.stdout)["status"] == "OK",
                    "token authentication failed")

        controller = Path(__file__).with_name("token_efficiency_panel_controller.py")
        command = [sys.executable, str(controller), "--fixture", str(panel.FIXTURE),
                   "--export", str(export), "--workflow-id", workflow_id,
                   "--binary", str(binary), "--home", str(home),
                   "--sender-token", str(home / "sender.token"),
                   "--worker-token", str(home / "worker.token"),
                   "--sender-principal", principals["sender"],
                   "--run-dir", str(root / f"panel-run-{case}")]
        swapped = list(command)
        swapped[swapped.index("--worker-token") + 1] = str(home / "sender.token")
        rejected = subprocess.run(swapped, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "token identities must differ" in rejected.stderr,
                "same-token identity was accepted")
        require(not (root / f"panel-run-{case}").exists(), "same-token request mutated state")
        actor_path = export / "actor" / f"{case}.json"
        actor_bytes = actor_path.read_bytes()
        actor = json.loads(actor_bytes)
        actor["task"] += " Changed."
        actor_path.write_text(json.dumps(actor), encoding="utf-8")
        rejected = subprocess.run(command, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "actor export differs" in rejected.stderr,
                "tampered actor was accepted")
        require(not (root / f"panel-run-{case}").exists(), "tampered actor mutated state")
        actor_path.write_bytes(actor_bytes)

        first = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
        require(first.returncode == 0, first.stderr)
        receipt = json.loads(first.stdout)
        require(receipt["schema"] == "cairn.panel-materialization/2", "receipt schema")
        require(receipt["workflow_id"] == workflow_id, "workflow identity")
        require(receipt["setup_harness"] == "codex", "synthetic session harness")
        require(receipt["fixture_sha256"] == fixture_hash, "fixture binding")
        require(receipt["provider_called"] is False and receipt["native_turn_join"] == "unimplemented",
                "no actor turn")
        require(receipt["task_outcome"] == "unassessed", "no task outcome")
        expected_sessions = ({"worker_a": "worker", "worker": "sender"}
                             if case == "actionable_response" else {"worker": "worker"})
        require(set(receipt["sessions"]) == set(expected_sessions), "session roles")
        for label, token_role in expected_sessions.items():
            session = receipt["sessions"][label]
            agent_id = str(uuid.UUID(session["agent_id"]))
            require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{agent_id}'")
                    == session["observed_owner"] == principals[token_role], "session owner")
            require(session["inbox"] == f"agent/{agent_id}", "session inbox")
        require(principals["sender"] != principals["worker"], "separate profiles")
        require(set(json.loads(actor_path.read_text())) == {"schema", "case", "task"},
                "actor export isolation")
        second = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
        require(second.returncode == 0 and json.loads(second.stdout) == receipt, second.stderr)
        changed = list(command)
        changed[changed.index("--worker-token") + 1] = str(home / "other.token")
        rejected = subprocess.run(changed, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "prior intent differs" in rejected.stderr,
                "changed worker identity accepted on retry")
        require(sql("SELECT count(*) FROM cairn.agent_session") == str(len(expected_sessions)),
                "changed worker identity registered a session")
        fixture_case = next(row for row in fixture["cases"] if row["id"] == case)
        expected_publishers = ({"request_decision": "sender", "response_a": "worker"}
                               if case == "actionable_response" else
                               {event["id"]: "sender" for event in fixture_case["setup"]["events"]})
        require(set(receipt["events"]) == set(expected_publishers), "event IDs")
        for spec in fixture_case["setup"]["events"]:
            event = receipt["events"][spec["id"]]
            publisher = expected_publishers[spec["id"]]
            require(event["observed_writer"] == principals[publisher], "source writer")
            require(event["source_body_sha256"] == hashlib.sha256(spec["body"].encode()).hexdigest(),
                    "source body hash")
            require(event["delivery_state"] == "pending" and event["attempts"] == 0,
                    "pending delivery")
            require(event["recipient_inbox"] == receipt["sessions"][spec["recipient"]]["inbox"],
                    "recipient session")
            cause = (receipt["events"][spec["causation"]]["event_id"]
                     if "causation" in spec else "")
            require(event["causation_id"] == cause, "exact causal parent")
            publisher_session = (receipt["sessions"]["worker_a"]
                                 if case == "actionable_response" and spec["id"] == "response_a"
                                 else None)
            expected_publisher = (publisher_session["inbox"] if publisher_session
                                  else principals[publisher])
            status = cli(publisher, "event-status", event["event_id"],
                         session=publisher_session)
            require(status["event"]["ref"] == event["source_ref"], "event source ref")
            require(status["event"]["from"] == event["event_publisher"]
                    == expected_publisher, "event publisher")
            require(status["event"].get("causation_id", "") == cause, "native causation")
            require(len(status["deliveries"]) == 1
                    and status["deliveries"][0]["delivery_id"] == event["delivery_id"]
                    and status["deliveries"][0]["state"] == "pending"
                    and status["deliveries"][0]["attempts"] == 0,
                    "exact delivery")
        sender_stats = cli("sender", "event-stats")
        expected_events = len(fixture_case["setup"]["events"])
        require(sender_stats["published"] == 1, sender_stats)
        require(sql("SELECT count(*) FROM cairn.agent_event") == str(expected_events),
                "unexpected event count")
        require(sql("SELECT count(*) FROM cairn.agent_delivery") == str(expected_events),
                "unexpected delivery count")
        if case == "duplicate_send":
            require(len(receipt["transitions"]) == 1, "missing duplicate transition")
            transition = receipt["transitions"][0]
            event = receipt["events"]["duplicate_a"]
            expected_request = str(uuid.uuid5(uuid.UUID(receipt["run_id"]), "duplicate_send/ck8"))
            require(transition["publication_request_id"] == event["publication_request_id"]
                    == expected_request
                    and transition["first_event_id"] == transition["retry_event_id"]
                    == event["event_id"] and transition["delivery_id"] == event["delivery_id"],
                    "same native publication identity")
        else:
            require(receipt["transitions"] == [], "unexpected transition")
        print(json.dumps({"workflow_id": workflow_id, "events": expected_events,
                          "provider_called": False}, sort_keys=True))
    finally:
        server.terminate()
        server.communicate(timeout=10)
        require(server.returncode == 0, "API server did not stop cleanly")


if __name__ == "__main__":
    check(Path(sys.argv[1]), sys.argv[2])
