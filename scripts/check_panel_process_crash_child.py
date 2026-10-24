#!/usr/bin/env python3
"""Kill a disposable native-claim child, then explicitly reconcile its attempt.

The child is a synthetic host adapter, not a model actor. The supervisor observes
SIGKILL before reporting process_exited; Cairn does not observe the OS process.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import subprocess
import sys
import time
import uuid

import preflight_token_efficiency_panel as panel


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def call(binary, home, env, role, operation, body):
    command = [str(binary), "agent", "--socket", str(home / "api.sock"),
               "--token-file", str(home / f"{role}.token"), operation]
    result = subprocess.run(command, input=json.dumps(body), text=True,
                            capture_output=True, env=env, timeout=15)
    require(len(result.stdout) < 1_000_000, "API response too large")
    response = json.loads(result.stdout)
    require(result.returncode == 0 and response.get("status") == "OK",
            f"{operation}: {response.get('status')}")
    return response["data"]


def child(binary, home, env, claim):
    result = call(binary, home, env, "worker", "session-inbox-claim", claim)
    attempt = result.get("attempt")
    require(attempt is not None, "child did not claim pinned delivery")
    print(json.dumps({"pid": os.getpid(), "attempt_id": attempt["attempt_id"],
                      "delivery_id": attempt["delivery"]["delivery_id"],
                      "event_id": attempt["delivery"]["event"]["event_id"],
                      "lease_id": attempt["delivery"]["lease_id"],
                      "session": attempt["session"]}), flush=True)
    while True:
        signal.pause()


def read_child_receipt(stream):
    deadline = time.monotonic() + 15
    line = bytearray()
    while len(line) < 4096:
        remaining = deadline - time.monotonic()
        require(remaining > 0 and select.select([stream], [], [], remaining)[0],
                "claim child did not emit receipt")
        part = os.read(stream.fileno(), 1)
        require(part, "claim child exited before receipt")
        if part == b"\n":
            return json.loads(line)
        line.extend(part)
    raise AssertionError("claim child receipt too large")


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_process_crash_child sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                text=True, capture_output=True, timeout=10)
        require(result.returncode == 0, "disposable SQL verification failed")
        return result.stdout.strip()
    require(sql("SELECT count(*) FROM cairn.agent_event") == "0"
            and sql("SELECT count(*) FROM cairn.agent_session") == "0",
            "process-crash database is not fresh")

    fixture, fixture_hash = panel.read_fixture()
    rows = [row for row in fixture["cases"] if row["id"] == "process_crash"]
    require(len(rows) == 1, "process_crash fixture missing")
    row = rows[0]
    require(row["setup"]["sources"] == []
            and row["setup"]["events"] == [{"id": "request_crash", "kind": "request",
                                                "recipient": "worker", "body": "Check marker CR-3."}]
            and row["setup"]["transitions"] == [{"phase": "after_claim_before_settlement",
                                                     "action": "crash_recipient",
                                                     "target": "request_crash"}],
            "process_crash setup changed")
    repo = fixture["event_source_defaults"]["scope"]["repo"]
    home = root / "panel-process-crash-child-home"
    home.mkdir(mode=0o700)
    identities = []
    for role in ("sender", "worker"):
        token = secrets.token_urlsafe(32)
        path = home / f"{role}.token"
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": f"agent/panel-crash-{role}-{uuid.uuid4()}",
                           "repo": repo, "role": "agent", "destination": "hosted"})
    config = home / "identities.json"
    config.write_text(json.dumps(identities), encoding="utf-8")
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    worker = None
    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode), "API socket missing")
        for role in ("sender", "worker"):
            call(binary, home, env, role, "version", {})
        registered = call(binary, home, env, "worker", "agent-register", {
            "request_id": str(uuid.uuid4()), "binding": f"panel-crash-{uuid.uuid4()}",
            "native_session_id": f"panel-crash-{uuid.uuid4()}",
            "metadata": {"harness": "codex", "project": "fixture",
                         "workspace": str(root), "state": "idle",
                         "delivery_mode": "existing-session"}})
        session = {key: registered[key] for key in ("agent_id", "execution_id")}
        source = call(binary, home, env, "sender", "create", {
            "request_id": str(uuid.uuid4()), "draft": {"kind": "note",
                "body": row["setup"]["events"][0]["body"],
                "scope": fixture["event_source_defaults"]["scope"],
                "claim_type": "self", "sensitivity": "shareable"}})
        require(source["observed_writer"] == identities[0]["principal"]
                and source["body"] == row["setup"]["events"][0]["body"],
                "source writer or body differs from fixture")
        ref = {"record_id": source["record_id"], "version": 1}
        history = call(binary, home, env, "worker", "history", ref)
        require(len(history["versions"]) == 1
                and history["versions"][0]["body"] == row["setup"]["events"][0]["body"],
                "worker could not read exact source")
        event = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "request", "ref": ref,
            "destination": {"type": "agent", "name": registered["inbox"]}})
        require(event["ref"] == ref and event["kind"] == "request"
                and event["destination"] == {"type": "agent", "name": registered["inbox"]}
                and event["from"] == identities[0]["principal"],
                "published request authority or source differs")
        status = call(binary, home, env, "sender", "event-inspect",
                      {"event_id": event["event_id"]})["deliveries"]
        require(len(status) == 1 and status[0]["state"] == "pending",
                "original delivery not pending")
        delivery_id = status[0]["delivery_id"]
        claim = {"request_id": str(uuid.uuid4()), "session": session,
                 "delivery_id": delivery_id, "native_turn_id": f"synthetic-child-{uuid.uuid4()}",
                 "turn_exclusive": False}
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child",
                                   str(binary), str(home), json.dumps(claim)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        receipt = read_child_receipt(worker.stdout)
        require(worker.poll() is None and receipt["pid"] == worker.pid
                and receipt["delivery_id"] == delivery_id
                and receipt["event_id"] == event["event_id"]
                and receipt["session"] == session,
                "child claim receipt differs from original")
        before = call(binary, home, env, "sender", "event-inspect",
                      {"event_id": event["event_id"]})["deliveries"][0]
        control = call(binary, home, env, "worker", "session-inbox-control", session)["attempt"]
        require(before["state"] == "leased" and before.get("result") is None
                and before["attempts"] == 1 and control["attempt_id"] == receipt["attempt_id"]
                and control.get("finished_at") is None
                and control["delivery"]["lease_id"] == receipt["lease_id"],
                "claim was not retained while child lived")
        worker.kill()
        require(worker.wait(timeout=5) == -signal.SIGKILL,
                "claim child did not terminate from SIGKILL")
        after_kill = call(binary, home, env, "sender", "event-inspect",
                          {"event_id": event["event_id"]})["deliveries"][0]
        held_after_kill = call(binary, home, env, "worker", "session-inbox-control", session)["attempt"]
        require(after_kill["state"] == "leased" and after_kill.get("result") is None
                and after_kill["delivery_id"] == delivery_id
                and held_after_kill["attempt_id"] == receipt["attempt_id"]
                and held_after_kill.get("finished_at") is None,
                "Store changed original without supervisor recovery")
        recovery = {"request_id": str(uuid.uuid4()), "session": session,
                    "attempt_id": receipt["attempt_id"], "reason": "process_exited"}
        closed = call(binary, home, env, "worker", "session-inbox-reconcile", recovery)
        require(closed["attempt_id"] == receipt["attempt_id"]
                and closed["reason"] == "process_exited"
                and closed.get("finished_at") is not None
                and closed["delivery"]["state"] == "failed"
                and closed["delivery"]["code"] == "processing_failed"
                and closed["delivery"].get("result") is None,
                "explicit recovery did not fail unfinished claim")
        repeated = call(binary, home, env, "worker", "session-inbox-reconcile", recovery)
        require(repeated == closed, "recovery retry changed durable attempt")
        final = call(binary, home, env, "sender", "event-inspect",
                     {"event_id": event["event_id"]})["deliveries"][0]
        require(final["delivery_id"] == delivery_id and final["state"] == "failed"
                and final["code"] == "processing_failed" and final["attempts"] == 1
                and final.get("result") is None,
                "failed original delivery or uncertainty was lost")
        next_claim = call(binary, home, env, "worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": session,
            "delivery_id": delivery_id,
            "native_turn_id": f"synthetic-next-{uuid.uuid4()}", "turn_exclusive": False})
        require(next_claim["attempt"] is None, "failed delivery was silently reclaimed")
        print(json.dumps({"fixture_sha256": fixture_hash, "case": "process_crash",
                          "child_exit": "SIGKILL_observed_by_test_supervisor",
                          "reconciliation": "process_exited_reported_by_test_supervisor",
                          "original_delivery": "failed_processing_failed",
                          "automatic_replay": False, "actor_task": "unrun",
                          "provider_called": False, "task_outcome": "unassessed"}))
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), Path(sys.argv[3]), os.environ,
              json.loads(sys.argv[4]))
    elif len(sys.argv) == 2:
        check(Path(sys.argv[1]))
    else:
        raise SystemExit("usage: check_panel_process_crash_child.py BINARY")
