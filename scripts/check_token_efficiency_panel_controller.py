#!/usr/bin/env python3
"""Exercise one panel materialization in this invocation's disposable cluster."""

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


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."), "fresh cluster root")
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "private cluster root")
    require((root / "data" / "PG_VERSION").is_file(), "cluster not initialized")
    require(binary == root / "cairn" and binary.is_file(), "disposable binary")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_controller sslmode=disable"
    require(os.environ["CAIRN_TEST_DATABASE_URL"] == os.environ["CAIRN_DATABASE_URL"] == dsn,
            "dedicated database")

    export_parent = root / "panel-export"
    export_parent.mkdir(mode=0o700)
    panel.PRIVATE_PARENT = export_parent
    export = export_parent / "frozen"
    fixture, fixture_hash = panel.read_fixture()
    panel.export(fixture, fixture_hash, export)
    home = root / "panel-controller-home"
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

    def cli(role, *args, body=None):
        result = subprocess.run([str(binary), args[0], "--socket", str(home / "api.sock"),
                                 "--token-file", str(home / f"{role}.token"), *args[1:]],
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
                   "--export", str(export), "--workflow-id", "baseline.single_notice",
                   "--binary", str(binary), "--home", str(home),
                   "--sender-token", str(home / "sender.token"),
                   "--worker-token", str(home / "worker.token"),
                   "--sender-principal", principals["sender"],
                   "--run-dir", str(root / "panel-run")]
        swapped = list(command)
        swapped[swapped.index("--worker-token") + 1] = str(home / "sender.token")
        rejected = subprocess.run(swapped, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "token identities must differ" in rejected.stderr,
                "same-token identity was accepted")
        require(not (root / "panel-run").exists(), "same-token request mutated state")
        actor_path = export / "actor" / "single_notice.json"
        actor_bytes = actor_path.read_bytes()
        actor_path.write_bytes(actor_bytes.replace(b"Process", b"Dispose"))
        rejected = subprocess.run(command, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "actor export differs" in rejected.stderr,
                "tampered actor was accepted")
        require(not (root / "panel-run").exists(), "tampered actor mutated state")
        actor_path.write_bytes(actor_bytes)

        first = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
        require(first.returncode == 0, first.stderr)
        receipt = json.loads(first.stdout)
        require(receipt["schema"] == "cairn.panel-materialization/1", "receipt schema")
        require(receipt["fixture_sha256"] == fixture_hash, "fixture binding")
        require(receipt["delivery_state"] == "pending" and receipt["attempts"] == 0,
                "pending delivery")
        require(receipt["provider_called"] is False and receipt["native_turn_join"] == "unimplemented",
                "no actor turn")
        require(receipt["task_outcome"] == "unassessed", "no task outcome")
        agent_id = str(uuid.UUID(receipt["agent_id"]))
        require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{agent_id}'")
                == receipt["observed_worker_owner"] == principals["worker"], "worker owner")
        require(principals["worker"] != receipt["observed_writer"] == principals["sender"],
                "separate source writer")
        require(set(json.loads(actor_path.read_text())) == {"schema", "case", "task"},
                "actor export isolation")
        second = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
        require(second.returncode == 0 and json.loads(second.stdout) == receipt, second.stderr)
        changed = list(command)
        changed[changed.index("--worker-token") + 1] = str(home / "other.token")
        rejected = subprocess.run(changed, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "prior intent differs" in rejected.stderr,
                "changed worker identity accepted on retry")
        require(sql("SELECT count(*) FROM cairn.agent_session") == "1",
                "changed worker identity registered a session")
        status = cli("sender", "event-status", receipt["event_id"])
        require(status["event"]["ref"] == receipt["source_ref"], "event source ref")
        require(len(status["deliveries"]) == 1, "one delivery")
        require(status["deliveries"][0]["delivery_id"] == receipt["delivery_id"], "delivery ref")
        sender_stats = cli("sender", "event-stats")
        require(sender_stats["published"] == 1, sender_stats)
        require(sql("SELECT count(*) FROM cairn.agent_event") == "1", "retry duplicated event")
        require(sql("SELECT count(*) FROM cairn.agent_delivery") == "1", "retry duplicated delivery")
        print(json.dumps({"workflow_id": receipt["workflow_id"], "event_id": receipt["event_id"],
                          "delivery_id": receipt["delivery_id"], "provider_called": False}, sort_keys=True))
    finally:
        server.terminate()
        server.communicate(timeout=10)
        require(server.returncode == 0, "API server did not stop cleanly")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
