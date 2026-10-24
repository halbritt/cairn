#!/usr/bin/env python3
"""Forget one admitted panel source through the supported operator workflow.

Only a fresh disposable database is used. A synthetic native host records the
source-unavailable failure; no model reads the source or explains it to a user.
"""

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


def invoke(binary, env, command, body=None, args=(), *, status="OK", token=None,
           socket=None, session=None, forbidden=None):
    argv = [str(binary), command]
    if token is not None:
        argv += ["--socket", str(socket), "--token-file", str(token)]
        if session is not None:
            argv += ["--agent-id", session["agent_id"],
                     "--execution-id", session["execution_id"]]
    argv += list(args)
    result = subprocess.run(argv, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, env=env, timeout=20)
    require(len(result.stdout) < 1_000_000 and len(result.stderr) < 100_000,
            "API response too large")
    if forbidden is not None:
        require(forbidden not in result.stdout and forbidden not in result.stderr,
                "forgotten body disclosed in response")
    envelope = json.loads(result.stdout)
    require(envelope.get("status") == status and (result.returncode == 0) == (status == "OK"),
            f"{command} returned {envelope.get('status')}, expected {status}")
    return envelope.get("data")


def agent(binary, env, home, role, operation, body, *, status="OK", forbidden=None):
    return invoke(binary, env, "agent", body, (operation,), status=status,
                  token=home / f"{role}.token", socket=home / "api.sock",
                  forbidden=forbidden)


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_source_forgotten sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    def count(table):
        require(table in ("agent_event", "agent_session", "memory_record"), "invalid count table")
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                                 f"SELECT count(*) FROM cairn.{table}"],
                                text=True, capture_output=True, timeout=10)
        require(result.returncode == 0, "disposable SQL count failed")
        return result.stdout.strip()
    require(all(count(table) == "0" for table in
                ("agent_event", "agent_session", "memory_record")),
            "source-forget database is not fresh")

    fixture, fixture_hash = panel.read_fixture()
    cases = [row for row in fixture["cases"] if row["id"] == "source_forgotten_before_read"]
    require(len(cases) == 1, "source_forgotten_before_read fixture missing")
    row = cases[0]
    require(row["setup"] == {
        "sources": [], "events": [{"id": "request_missing_source", "kind": "request",
                                 "recipient": "worker",
                                 "body": "Read source SRC-7 and summarize it."}],
        "transitions": [{"phase": "after_admission_before_read",
                         "action": "remove_source", "target": "request_missing_source"}]},
        "source-loss fixture setup changed")
    source_body = row["setup"]["events"][0]["body"]
    repo = fixture["event_source_defaults"]["scope"]["repo"]
    home = root / "panel-source-forgotten-home"
    home.mkdir(mode=0o700)
    identities = []
    for role in ("sender", "worker"):
        token = secrets.token_urlsafe(32)
        path = home / f"{role}.token"
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": f"agent/panel-forget-{role}-{uuid.uuid4()}",
                           "repo": repo, "role": "agent", "destination": "hosted"})
    identities_path = home / "identities.json"
    identities_path.write_text(json.dumps(identities), encoding="utf-8")
    identities_path.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode),
                "local API socket missing")
        for role in ("sender", "worker"):
            agent(binary, env, home, role, "version", {})
        registration = agent(binary, env, home, "worker", "agent-register", {
            "request_id": str(uuid.uuid4()), "binding": f"panel-forget-{uuid.uuid4()}",
            "native_session_id": f"panel-forget-{uuid.uuid4()}",
            "metadata": {"harness": "codex", "project": "fixture",
                         "workspace": str(root), "state": "idle",
                         "delivery_mode": "existing-session"}})
        session = {key: registration[key] for key in ("agent_id", "execution_id")}
        source = agent(binary, env, home, "sender", "create", {
            "request_id": str(uuid.uuid4()), "draft": {"kind": "note", "body": source_body,
                "scope": fixture["event_source_defaults"]["scope"],
                "claim_type": "self", "sensitivity": "shareable"}})
        require(source["version"] == 1 and source["body"] == source_body
                and source["observed_writer"] == identities[0]["principal"],
                "source differs from frozen fixture or sender")
        ref = {"record_id": source["record_id"], "version": source["version"]}
        published = agent(binary, env, home, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "request", "ref": ref,
            "destination": {"type": "agent", "name": registration["inbox"]}})
        require(published["ref"] == ref and published["kind"] == "request"
                and published["from"] == identities[0]["principal"],
                "admitted event differs from exact source")
        status = agent(binary, env, home, "sender", "event-inspect",
                       {"event_id": published["event_id"]})
        require(len(status["deliveries"]) == 1
                and status["deliveries"][0]["state"] == "pending",
                "request not pending before native admission")
        delivery_id = status["deliveries"][0]["delivery_id"]
        attempt = agent(binary, env, home, "worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": session,
            "delivery_id": delivery_id,
            "native_turn_id": f"synthetic-source-loss-{uuid.uuid4()}",
            "turn_exclusive": False})["attempt"]
        require(attempt is not None and attempt["delivery"]["event"]["ref"] == ref
                and attempt["delivery"]["delivery_id"] == delivery_id,
                "native admission did not pin exact source")
        require(source_body not in json.dumps(attempt),
                "claim disclosed source before scoped read")

        # The operator grant and deletion preview are scoped to this new DB.
        root_grant = invoke(binary, env, "bootstrap", {
            "request_id": str(uuid.uuid4()),
            "reason": "Authorize disposable panel source forgetting probe"})
        preview = invoke(binary, env, "preview-delete", args=(source["record_id"],))
        require(preview["record_id"] == source["record_id"],
                "operator preview targeted another source")
        deleted = invoke(binary, env, "forget", {
            "request_id": str(uuid.uuid4()), "record_id": source["record_id"],
            "expected_version": source["version"],
            "grant_id": root_grant["grant_id"], "preview_id": preview["preview_id"]})
        require(deleted["record_id"] == source["record_id"],
                "authorized forgetting targeted another source")

        # This is the first exact scoped receiver read after admission.
        agent(binary, env, home, "worker", "history",
              {"repo": repo, **ref}, status="PAYLOAD_UNAVAILABLE",
              forbidden=source_body)
        after_forget = agent(binary, env, home, "sender", "event-inspect",
                             {"event_id": published["event_id"]}, forbidden=source_body)
        require(after_forget["event"]["ref"] == ref
                and after_forget["deliveries"][0]["state"] == "leased"
                and after_forget["deliveries"][0].get("result") is None,
                "forgotten source removed admitted communication")

        ack_id = str(uuid.uuid4())
        ack_args = ("--request-id", ack_id, "--lease", attempt["delivery"]["lease_id"],
                    "--disposition", "failed", "--code", "source_unavailable", delivery_id)
        failed = invoke(binary, env, "ack", args=ack_args, token=home / "worker.token",
                        socket=home / "api.sock", session=session, forbidden=source_body)
        repeated = invoke(binary, env, "ack", args=ack_args, token=home / "worker.token",
                          socket=home / "api.sock", session=session, forbidden=source_body)
        require(repeated == failed and failed["state"] == "failed"
                and failed["code"] == "source_unavailable"
                and failed.get("result") is None,
                "source-unavailable settlement was not stable and result-free")
        closed = agent(binary, env, home, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": attempt["attempt_id"], "reason": "delivery_completed"})
        require(closed["finished_at"] is not None and closed["delivery"]["state"] == "failed",
                "native source-loss attempt did not close")
        final = agent(binary, env, home, "sender", "event-inspect",
                      {"event_id": published["event_id"]}, forbidden=source_body)
        require(final["event"]["ref"] == ref and len(final["deliveries"]) == 1
                and final["deliveries"][0]["delivery_id"] == delivery_id
                and final["deliveries"][0]["state"] == "failed"
                and final["deliveries"][0]["code"] == "source_unavailable"
                and final["deliveries"][0]["attempts"] == 1
                and final["deliveries"][0].get("result") is None,
                "durable source-loss outcome differs")
        print(json.dumps({"case": "source_forgotten_before_read",
                          "fixture_sha256": fixture_hash,
                          "source_transition": "authorized_operator_forget_after_claim",
                          "exact_read": "PAYLOAD_UNAVAILABLE",
                          "settlement": "failed_source_unavailable_once",
                          "requester_explanation": "unrun", "actor_task": "unrun",
                          "provider_called": False, "task_outcome": "unassessed"}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_source_forgotten.py BINARY")
    check(Path(sys.argv[1]))
