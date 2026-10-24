#!/usr/bin/env python3
"""Join routine notice policy to the real watcher and a disposable Cairn API.

The native owner is a synthetic live child with no Codex queue endpoint. A
watcher refusal proves eligibility was observed, never a model wake.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
import uuid


def require(value, message):
    if not value:
        raise AssertionError(message)


def coordinator():
    script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    spec = importlib.util.spec_from_file_location("disposable_notice_watcher", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


def call(binary, home, env, role, operation, body, expected="OK", session=None):
    command = [str(binary), "agent", "--socket", str(home / "api.sock"),
               "--token-file", str(home / f"{role}.token")]
    if session:
        command += ["--agent-id", session["agent_id"], "--execution-id", session["execution_id"]]
    command.append(operation)
    result = subprocess.run(command,
                            input=json.dumps(body), text=True, capture_output=True,
                            env=env, timeout=15)
    require(len(result.stdout) < 1_000_000, "API response too large")
    response = json.loads(result.stdout)
    require(response.get("status") == expected and (result.returncode == 0) == (expected == "OK"),
            f"{operation}: expected {expected}, got {response.get('status')}")
    return response.get("data")


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def empty_database(dsn):
    result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn,
                             "-c", "SELECT count(*) FROM cairn.agent_event"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == "0",
            "notice watcher database must be fresh")


def check(binary):
    cluster = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(cluster.parent == Path("/tmp") and cluster.name.startswith("cairn-postgres.")
            and cluster.stat().st_uid == os.getuid()
            and stat.S_IMODE(cluster.stat().st_mode) == 0o700
            and (cluster / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == cluster / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={cluster / 'socket'} dbname=cairn_notice_watcher_eligibility sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    empty_database(dsn)

    home = cluster / "notice-watcher-home"
    home.mkdir(mode=0o700)
    repo = "fixture:notice-watcher-eligibility"
    principals = {}
    identities = []
    for role in ("sender", "worker", "stranger"):
        token = secrets.token_urlsafe(32)
        token_path = home / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        principals[role] = f"agent/notice-watcher-{role}-{uuid.uuid4()}"
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": principals[role], "repo": repo,
                           "role": "agent", "destination": "hosted"})
    identities_path = home / "identities.json"
    identities_path.write_text(json.dumps(identities), encoding="utf-8")
    identities_path.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    api = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    owner = None
    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            require(api.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode), "local API socket missing")
        call(binary, home, env, "sender", "version", {})
        native_id = f"synthetic-notice-watcher-{uuid.uuid4()}"
        registered = call(binary, home, env, "worker", "agent-register", {
            "request_id": str(uuid.uuid4()), "binding": native_id,
            "native_session_id": native_id,
            "metadata": {"harness": "codex", "project": "fixture",
                         "workspace": str(cluster), "state": "idle",
                         "delivery_mode": "existing-session"}})
        session = {key: registered[key] for key in ("agent_id", "execution_id")}
        require(registered["inbox"] == "agent/" + session["agent_id"],
                "registered inbox differs")
        owner = subprocess.Popen(["sleep", "30"], env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        engine, script = coordinator()
        root = cluster / "notice-watcher-coordination"
        bindings = root / "bindings"
        state_dir = root / "state" / "worker"
        bindings.mkdir(parents=True, mode=0o700)
        state_dir.mkdir(parents=True, mode=0o700)
        config = {"cairn": str(binary), "socket": str(home / "api.sock"),
                  "token_file": str(home / "worker.token"), "state_dir": str(state_dir),
                  "binding": "worker", "harness": "codex", "repo": repo,
                  "native_delivery": True, "idle_wakeup": "/bin/false"}
        engine.write_state(bindings / "worker.json", config)
        state_path = state_dir / "session.json"
        engine.write_state(state_path, {"agent": registered,
                                        "workspace": str(cluster),
                                        "process": engine.process_reference(owner.pid)})

        def watch():
            result = subprocess.run([sys.executable, str(script), "watch", "--config-dir", str(bindings),
                                     "--once"], env=env, text=True, capture_output=True, timeout=10)
            require(result.returncode == 0 and "; presence may expire" not in result.stderr,
                    "disposable watcher failed")
            state = json.loads(state_path.read_text())
            require("idle_wake" not in state and "inbox_attempt" not in state,
                    "fixture unexpectedly submitted a native wake or claimed work")
            return state

        source = call(binary, home, env, "sender", "create", {
            "request_id": str(uuid.uuid4()),
            "draft": {"kind": "note", "body": "Synthetic notice eligibility source",
                      "scope": {"repo": repo, "task_id": "*", "run_id": "*"},
                      "claim_type": "self", "sensitivity": "shareable"}})
        ref = {"record_id": source["record_id"], "version": source["version"]}
        routine_request = {"request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
                           "destination": {"type": "agent", "name": registered["inbox"]},
                           "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 10}}
        routine = call(binary, home, env, "sender", "event-publish", routine_request)
        routine_status = call(binary, home, env, "sender", "event-inspect",
                              {"event_id": routine["event_id"]})
        routine_delivery = routine_status["deliveries"][0]
        fixed_deadline = parse_time(routine_delivery["available_at"])
        require(routine["notice_policy"] == {"owner": principals["sender"], "version": 1,
                                              "urgency": "routine", "defer_seconds": 10}
                and routine_delivery["state"] == "pending"
                and routine_delivery["attempts"] == 0
                and fixed_deadline - parse_time(routine["created_at"]) == timedelta(seconds=10),
                "routine policy or fixed deadline differs")
        require(datetime.now(timezone.utc) < fixed_deadline, "routine deadline elapsed before watcher probe")
        first = watch()
        require("wake_refusal" not in first
                and (first.get("delivery_observation") or {}).get("value", {}).get("condition") == "unknown"
                and (first.get("delivery_observation") or {}).get("value", {}).get("delivery_id") is None,
                "deferred routine notice was offered for wake before its deadline")
        require(call(binary, home, env, "worker", "session-inbox-ready", session).get("delivery_id") is None,
                "deferred routine notice became ready early")

        urgent = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "request", "ref": ref,
            "destination": {"type": "agent", "name": registered["inbox"]}})
        urgent_delivery = call(binary, home, env, "sender", "event-inspect",
                               {"event_id": urgent["event_id"]})["deliveries"][0]
        require(datetime.now(timezone.utc) < fixed_deadline,
                "routine deadline elapsed before urgent preemption probe")
        second = watch()
        refusal = second.get("wake_refusal") or {}
        observation = (second.get("delivery_observation") or {}).get("value") or {}
        require(refusal.get("delivery_id") == urgent_delivery["delivery_id"]
                and refusal.get("condition") == "native_transport_unavailable"
                and observation.get("delivery_id") == urgent_delivery["delivery_id"]
                and observation.get("condition") == "native_transport_unavailable",
                "watcher did not observe immediate request and coded transport refusal")
        require(call(binary, home, env, "worker", "session-inbox-ready", session)["event_id"]
                == urgent["event_id"], "urgent request did not preempt deferred routine notice")

        claimed = call(binary, home, env, "worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": session,
            "delivery_id": urgent_delivery["delivery_id"],
            "native_turn_id": "synthetic-urgent-observation", "turn_exclusive": True})["attempt"]
        require(claimed and claimed["delivery"]["event"]["event_id"] == urgent["event_id"],
                "urgent synthetic claim differs")
        settled = call(binary, home, env, "worker", "event-complete", {
            "request_id": str(uuid.uuid4()), "delivery_id": urgent_delivery["delivery_id"],
            "lease_id": claimed["delivery"]["lease_id"],
            "disposition": "failed", "code": "processing_failed"}, session=session)
        require(settled["state"] == "failed", "synthetic request settlement differs")
        call(binary, home, env, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": claimed["attempt_id"], "reason": "delivery_completed"})
        require(datetime.now(timezone.utc) < fixed_deadline,
                "routine deadline elapsed before publisher flush probe")
        flush_request = {"request_id": str(uuid.uuid4()), "event_id": routine["event_id"]}
        call(binary, home, env, "stranger", "event-notice-flush", flush_request,
             expected="NOT_FOUND")  # Hidden from an unrelated publisher profile.
        flushed = call(binary, home, env, "sender", "event-notice-flush", flush_request)
        require(flushed["released"] == 1 and flushed["flushed_by"] == principals["sender"]
                and parse_time(flushed["flushed_at"]) < fixed_deadline,
                "publisher did not release the original deferred notice")
        require(call(binary, home, env, "sender", "event-notice-flush", flush_request) == flushed,
                "publisher retry changed flush decision")
        final = watch()
        final_refusal = final.get("wake_refusal") or {}
        final_observation = (final.get("delivery_observation") or {}).get("value") or {}
        require(final_refusal.get("delivery_id") == routine_delivery["delivery_id"]
                and final_refusal.get("condition") == "native_transport_unavailable"
                and final_observation.get("delivery_id") == routine_delivery["delivery_id"]
                and final_observation.get("condition") == "native_transport_unavailable",
                "watcher did not observe released routine notice")
        after = call(binary, home, env, "sender", "event-inspect", {"event_id": routine["event_id"]})
        require(after["event"]["ref"] == ref
                and after["event"]["notice_policy"] == routine["notice_policy"]
                and after["deliveries"][0]["delivery_id"] == routine_delivery["delivery_id"]
                and after["deliveries"][0]["state"] == "pending"
                and after["deliveries"][0]["attempts"] == 0
                and after["deliveries"][0]["available_at"] == flushed["flushed_at"],
                "flush changed original event/delivery or failed to preserve pending state")
        print(json.dumps({"case": "routine_notice_watcher_eligibility", "store_api": "disposable",
                          "watcher_process": "real_once", "native_owner": "synthetic_live_child",
                          "defer_seconds": 10, "early_wake_attempt": False,
                          "urgent_request_observed": True, "flush_released_original": True,
                          "native_transport": "unavailable", "model_wake": False,
                          "task_outcome": "unassessed"}))
    finally:
        if owner is not None:
            owner.terminate()
            try:
                owner.wait(timeout=5)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.wait(timeout=5)
        api.terminate()
        try:
            api.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_notice_watcher_eligibility.py BINARY")
    check(Path(sys.argv[1]))
