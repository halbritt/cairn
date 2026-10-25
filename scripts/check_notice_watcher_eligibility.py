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
import socket
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


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def empty_database(dsn):
    result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn,
                             "-c", "SELECT count(*) FROM cairn.agent_event"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == "0",
            "notice watcher database must be fresh")


def expire_disposable_deadline(dsn, delivery_id):
    canonical = str(uuid.UUID(delivery_id))
    result = subprocess.run(["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                             "-d", dsn, "-c", "UPDATE cairn.agent_delivery SET "
                             "available_at=clock_timestamp()-interval '1 second' "
                             f"WHERE delivery_id='{canonical}'::uuid"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == "UPDATE 1",
            "disposable deadline did not advance")


def check(binary, real_clock_pilot=False):
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
        owner = subprocess.Popen(["sleep", "90"], env=env, stdout=subprocess.DEVNULL,
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
            started_at = utc_now()
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(script), "watch", "--config-dir", str(bindings),
                                     "--once"], env=env, text=True, capture_output=True, timeout=10)
            require(result.returncode == 0 and "; presence may expire" not in result.stderr,
                    "disposable watcher failed")
            state = json.loads(state_path.read_text())
            require("idle_wake" not in state and "inbox_attempt" not in state,
                    "fixture unexpectedly submitted a native wake or claimed work")
            state["_pilot_watch_timing"] = {"started_at": started_at,
                                             "finished_at": utc_now(),
                                             "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            return state

        source = call(binary, home, env, "sender", "create", {
            "request_id": str(uuid.uuid4()),
            "draft": {"kind": "note", "body": "Synthetic notice eligibility source",
                      "scope": {"repo": repo, "task_id": "*", "run_id": "*"},
                      "claim_type": "self", "sensitivity": "shareable"}})
        ref = {"record_id": source["record_id"], "version": source["version"]}
        routine_request = {"request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
                           "destination": {"type": "agent", "name": registered["inbox"]},
                           "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 30}}
        routine = call(binary, home, env, "sender", "event-publish", routine_request)
        routine_status = call(binary, home, env, "sender", "event-inspect",
                              {"event_id": routine["event_id"]})
        routine_delivery = routine_status["deliveries"][0]
        fixed_deadline = parse_time(routine_delivery["available_at"])
        require(routine["notice_policy"] == {"owner": principals["sender"], "version": 1,
                                              "urgency": "routine", "defer_seconds": 30}
                and routine_delivery["state"] == "pending"
                and routine_delivery["attempts"] == 0
                and fixed_deadline - parse_time(routine["created_at"]) == timedelta(seconds=30),
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
        # Explicitly urgent notices have the same immediate wake eligibility
        # as requests. Keep the routine event pending so this checks priority
        # and identity across two notices, not only request-over-notice order.
        urgent_notice = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
            "destination": {"type": "agent", "name": registered["inbox"]},
            "notice_policy": {"version": 1, "urgency": "urgent", "defer_seconds": 0}})
        urgent_notice_delivery = call(binary, home, env, "sender", "event-inspect",
                                      {"event_id": urgent_notice["event_id"]})["deliveries"][0]
        require(urgent_notice["notice_policy"] == {"owner": principals["sender"], "version": 1,
                                                    "urgency": "urgent", "defer_seconds": 0}
                and urgent_notice_delivery["state"] == "pending"
                and urgent_notice_delivery["attempts"] == 0,
                "urgent notice policy or initial delivery differs")
        require(datetime.now(timezone.utc) < fixed_deadline,
                "routine deadline elapsed before urgent-notice bypass probe")
        third = watch()
        refusal = third.get("wake_refusal") or {}
        observation = (third.get("delivery_observation") or {}).get("value") or {}
        require(refusal.get("delivery_id") == urgent_notice_delivery["delivery_id"]
                and refusal.get("condition") == "native_transport_unavailable"
                and observation.get("delivery_id") == urgent_notice_delivery["delivery_id"]
                and observation.get("condition") == "native_transport_unavailable"
                and call(binary, home, env, "worker", "session-inbox-ready", session)["event_id"]
                == urgent_notice["event_id"],
                "explicitly urgent notice did not bypass the routine deadline")
        urgent_notice_claim = call(binary, home, env, "worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": session,
            "delivery_id": urgent_notice_delivery["delivery_id"],
            "native_turn_id": "synthetic-urgent-notice-observation", "turn_exclusive": True})["attempt"]
        require(urgent_notice_claim
                and urgent_notice_claim["delivery"]["event"]["event_id"] == urgent_notice["event_id"]
                and urgent_notice_claim["delivery"]["delivery_id"] == urgent_notice_delivery["delivery_id"],
                "urgent notice claim changed its event or delivery identity")
        urgent_notice_settled = call(binary, home, env, "worker", "event-complete", {
            "request_id": str(uuid.uuid4()), "delivery_id": urgent_notice_delivery["delivery_id"],
            "lease_id": urgent_notice_claim["delivery"]["lease_id"],
            "disposition": "failed", "code": "processing_failed"}, session=session)
        require(urgent_notice_settled["state"] == "failed", "urgent notice fixture settlement differs")
        call(binary, home, env, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": urgent_notice_claim["attempt_id"], "reason": "delivery_completed"})
        require(call(binary, home, env, "worker", "session-inbox-ready", session).get("delivery_id") is None,
                "settled urgent notice exposed the still-deferred routine notice early")
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

        # Finish fixture cleanup, then probe a new routine notice across a real
        # local API restart. A busy owner must remain a distinct wake barrier
        # even when the notice's fixed deadline has passed.
        routine_claim = call(binary, home, env, "worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": session,
            "delivery_id": routine_delivery["delivery_id"],
            "native_turn_id": "synthetic-flushed-notice", "turn_exclusive": True})["attempt"]
        require(routine_claim and routine_claim["delivery"]["event"]["event_id"] == routine["event_id"],
                "flushed routine cleanup claimed another event")
        call(binary, home, env, "worker", "event-complete", {
            "request_id": str(uuid.uuid4()), "delivery_id": routine_delivery["delivery_id"],
            "lease_id": routine_claim["delivery"]["lease_id"],
            "disposition": "failed", "code": "processing_failed"}, session=session)
        call(binary, home, env, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": routine_claim["attempt_id"], "reason": "delivery_completed"})

        busy = call(binary, home, env, "worker", "agent-context", {
            "request_id": str(uuid.uuid4()), "session": session,
            "expected_revision": registered["context_revision"],
            "metadata": dict(registered["metadata"], state="busy")})
        require(busy["metadata"]["state"] == "busy", "owner did not report busy")
        delayed = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
            "destination": {"type": "agent", "name": registered["inbox"]},
            "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 60}})
        delayed_before = call(binary, home, env, "sender", "event-inspect",
                              {"event_id": delayed["event_id"]})["deliveries"][0]
        delayed_id = delayed_before["delivery_id"]
        delayed_deadline = delayed_before["available_at"]
        require(parse_time(delayed_deadline) - parse_time(delayed["created_at"])
                == timedelta(seconds=60), "second fixed deadline differs")
        api.terminate()
        api.wait(timeout=5)
        api = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        restart_deadline = time.monotonic() + 10
        while True:
            require(api.poll() is None and time.monotonic() < restart_deadline,
                    "disposable API did not restart")
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(home / "api.sock"))
                break
            except OSError:
                time.sleep(0.02)
        delayed_after = call(binary, home, env, "sender", "event-inspect",
                             {"event_id": delayed["event_id"]})["deliveries"][0]
        require(delayed_after["delivery_id"] == delayed_id
                and delayed_after["available_at"] == delayed_deadline
                and delayed_after["attempts"] == 0,
                "API restart changed the fixed routine deadline or delivery")
        before_busy = watch()
        require((before_busy.get("delivery_observation") or {}).get("value", {}).get("condition") == "busy"
                and (before_busy.get("delivery_observation") or {}).get("value", {}).get("delivery_id") is None,
                "busy owner was offered deferred notice")

        expire_disposable_deadline(dsn, delayed_id)
        expired = call(binary, home, env, "sender", "event-inspect",
                       {"event_id": delayed["event_id"]})["deliveries"][0]
        require(expired["delivery_id"] == delayed_id and expired["state"] == "pending"
                and expired["attempts"] == 0
                and parse_time(expired["available_at"]) < datetime.now(timezone.utc),
                "expired original notice was not durably pending")
        require(call(binary, home, env, "worker", "session-inbox-ready", session)
                .get("delivery_id") is None,
                "busy session exposed a ready head")
        still_busy = watch()
        require((still_busy.get("delivery_observation") or {}).get("value", {}).get("condition") == "busy"
                and "idle_wake" not in still_busy,
                "busy owner was woken after routine deadline")
        idle = call(binary, home, env, "worker", "agent-context", {
            "request_id": str(uuid.uuid4()), "session": session,
            "expected_revision": busy["context_revision"],
            "metadata": dict(busy["metadata"], state="idle")})
        require(idle["metadata"]["state"] == "idle", "owner did not report idle")
        require(call(binary, home, env, "worker", "session-inbox-ready", session)["delivery_id"]
                == delayed_id, "expired original notice was not ready after idle report")
        ready_after_idle = watch()
        ready_observation = (ready_after_idle.get("delivery_observation") or {}).get("value") or {}
        require(ready_observation.get("condition") == "native_transport_unavailable"
                and ready_observation.get("delivery_id") == delayed_id
                and (ready_after_idle.get("wake_refusal") or {}).get("delivery_id") == delayed_id,
                "eligible original notice was not observed after busy owner became idle")
        require(call(binary, home, env, "sender", "event-inspect",
                     {"event_id": delayed["event_id"]})["deliveries"][0]["attempts"] == 0,
                "watcher claimed a notice without a native transport")

        natural_report = None
        if real_clock_pilot:
            # This opt-in leg is separate from the default deterministic
            # integration path. It measures one natural deadline and cannot
            # establish fewer native wakes because the synthetic owner has no
            # queue endpoint.
            delayed_claim = call(binary, home, env, "worker", "session-inbox-claim", {
                "request_id": str(uuid.uuid4()), "session": session,
                "delivery_id": delayed_id, "native_turn_id": "synthetic-expired-cleanup",
                "turn_exclusive": True})["attempt"]
            require(delayed_claim is not None, "expired fixture notice could not be cleaned up")
            call(binary, home, env, "worker", "event-complete", {
                "request_id": str(uuid.uuid4()), "delivery_id": delayed_id,
                "lease_id": delayed_claim["delivery"]["lease_id"],
                "disposition": "failed", "code": "processing_failed"}, session=session)
            call(binary, home, env, "worker", "session-inbox-reconcile", {
                "request_id": str(uuid.uuid4()), "session": session,
                "attempt_id": delayed_claim["attempt_id"], "reason": "delivery_completed"})
            engine.write_state(state_path, {"agent": registered, "workspace": str(cluster),
                                            "process": engine.process_reference(owner.pid)})
            natural = call(binary, home, env, "sender", "event-publish", {
                "request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
                "destination": {"type": "agent", "name": registered["inbox"]},
                "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 60}})
            natural_created = parse_time(natural["created_at"])
            natural_delivery = call(binary, home, env, "sender", "event-inspect",
                                    {"event_id": natural["event_id"]})["deliveries"][0]
            natural_deadline = parse_time(natural_delivery["available_at"])
            require(natural_deadline - natural_created == timedelta(seconds=60),
                    "real-clock pilot did not retain its 60-second deadline")
            cycles = []
            natural_observation = None
            while datetime.now(timezone.utc) < natural_deadline:
                cycle = watch()
                observation = (cycle.get("delivery_observation") or {}).get("value") or {}
                cycles.append({"timing": cycle["_pilot_watch_timing"],
                               "condition": observation.get("condition"),
                               "delivery_id": observation.get("delivery_id")})
                require(observation.get("delivery_id") is None,
                        "routine notice became eligible before its real deadline")
                time.sleep(min(5, max(0.05, (natural_deadline - datetime.now(timezone.utc)).total_seconds())))
            cycle = watch()
            observation = (cycle.get("delivery_observation") or {}).get("value") or {}
            cycles.append({"timing": cycle["_pilot_watch_timing"],
                           "condition": observation.get("condition"),
                           "delivery_id": observation.get("delivery_id")})
            if observation.get("delivery_id") == natural_delivery["delivery_id"]:
                natural_observation = cycle["_pilot_watch_timing"]
            require(natural_observation is not None
                    and observation.get("condition") == "native_transport_unavailable",
                    "real-clock watcher did not observe the original notice after its deadline")
            natural_elapsed_ms = round((parse_time(natural_observation["finished_at"])
                                        - natural_created).total_seconds() * 1000, 3)
            natural_report = {
                "event_id": natural["event_id"], "delivery_id": natural_delivery["delivery_id"],
                "actionability": "unassessed_no_actor_or_task_grade",
                "created_at": natural["created_at"], "deadline_at": natural_delivery["available_at"],
                "eligible_at": natural_delivery["available_at"],
                "observed_eligible_at": natural_observation["finished_at"],
                "arrival_to_observed_eligibility_ms": natural_elapsed_ms,
                "deadline_to_observed_eligibility_ms": round(
                    (parse_time(natural_observation["finished_at"]) - natural_deadline).total_seconds() * 1000, 3),
                "watch_cycles": cycles, "eligible_wake_decisions": 1,
                "native_wakes_accepted": 0, "native_transport": "unavailable",
                "provider_calls": 0,
            }
        result = {"case": "routine_notice_watcher_eligibility", "store_api": "disposable",
                  "watcher_process": "real_once", "native_owner": "synthetic_live_child",
                  "defer_seconds": 30, "early_wake_attempt": False,
                  "urgent_request_observed": True, "urgent_notice_observed": True,
                  "flush_released_original": True,
                  "restart_preserved_deadline": True, "busy_owner_wake": False,
                  "expired_notice_observed_after_idle": True,
                  "native_transport": "unavailable", "model_wake": False,
                  "task_outcome": "unassessed"}
        if natural_report is not None:
            result["real_clock_60_second_pilot"] = natural_report
        print(json.dumps(result))
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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-clock-pilot", action="store_true",
                        help="also wait for and measure one natural 60-second notice deadline")
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    check(args.binary, real_clock_pilot=args.real_clock_pilot)
