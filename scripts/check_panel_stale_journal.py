#!/usr/bin/env python3
"""Fence a pending old-execution native journal after reviewed request reissue.

The host turn and result text are synthetic. All Store, CLI, API, and journal
operations run against one fresh disposable PostgreSQL database.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

import disposable_host

import preflight_token_efficiency_panel as panel


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def invoke(binary, env, argv, body=None, expected="OK"):
    result = disposable_host.run_envelope([binary, *argv], body, env=env, expected=expected,
                                          timeout=20, label=str(argv[-1]))
    return result if expected == "OK" else result.get("data")


def sql(dsn, statement):
    return disposable_host.run_sql(dsn, statement, limit=4096)


def journal_command(script, env, context, body, expected):
    result = subprocess.run([sys.executable, str(script), "inbox", "--context", str(context),
                             "complete"], input=body, text=True, capture_output=True,
                            env=env, timeout=30)
    require(len(result.stdout) < 100_000 and len(result.stderr) < 100_000,
            "native journal response too large")
    envelope = json.loads(result.stdout)
    require(envelope.get("status") == expected and (result.returncode == 0) == (expected == "OK"),
            f"native completion returned {envelope.get('status')}, expected {expected}")
    return envelope


def check(binary):
    cluster = disposable_host.ProvidedCluster.from_environment()
    root = cluster.root

    fixture, fixture_hash = panel.read_fixture()
    rows = [row for row in fixture["cases"] if row["id"] == "stale_execution"]
    require(len(rows) == 1 and rows[0]["setup"] == {
        "sources": [], "events": [{"id": "request_stale", "kind": "request",
                                 "recipient": "worker", "body": "Check marker ST-6."}],
        "transitions": [{"phase": "after_claim_before_settlement", "action": "rotate_execution",
                         "target": "request_stale"}]}, "stale_execution fixture changed")
    repo = fixture["event_source_defaults"]["scope"]["repo"]
    host = disposable_host.DisposableHost.start(
        cluster, binary=binary, repo=repo, database="cairn_panel_stale_journal",
        home=root / "panel-stale-journal-home", roles=("sender", "worker"),
        principals=lambda role: f"agent/panel-stale-{role}-{uuid.uuid4()}")
    home, env, socket, dsn = host.home, host.env, host.socket, host.dsn
    principals = host.principals
    host.require_fresh("agent_event", "agent_session", message="stale-journal database is not fresh")
    coordination_script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    module_spec = importlib.util.spec_from_file_location("panel_stale_coordination", coordination_script)
    coordination = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(coordination)
    try:

        def agent(role, operation, body, expected="OK", session=None):
            argv = ["agent", "--socket", str(socket), "--token-file", str(home / f"{role}.token")]
            if session:
                argv += ["--agent-id", session["agent_id"],
                         "--execution-id", session["execution_id"]]
            return invoke(binary, env, [*argv, operation], body, expected)

        def operator(operation, body):
            return invoke(binary, env, [operation], body)

        registration = {"request_id": str(uuid.uuid4()),
                        "binding": f"panel-stale-{uuid.uuid4()}",
                        "native_session_id": f"panel-stale-{uuid.uuid4()}",
                        "metadata": {"harness": "codex", "project": "fixture",
                                     "workspace": str(root), "state": "idle",
                                     "delivery_mode": "existing-session"}}
        old = agent("worker", "agent-register", registration)
        old_ref = {key: old[key] for key in ("agent_id", "execution_id")}
        source = agent("sender", "create", {"request_id": str(uuid.uuid4()),
            "draft": {"kind": "note", "body": rows[0]["setup"]["events"][0]["body"],
                      "scope": fixture["event_source_defaults"]["scope"],
                      "claim_type": "self", "sensitivity": "shareable"}})
        require(source["observed_writer"] == principals["sender"]
                and source["body"] == "Check marker ST-6.", "fixture source changed")
        ref = {"record_id": source["record_id"], "version": 1}
        event = agent("sender", "event-publish", {"request_id": str(uuid.uuid4()),
            "kind": "request", "ref": ref,
            "destination": {"type": "agent", "name": old["inbox"]}})
        original = agent("sender", "event-inspect", {"event_id": event["event_id"]})["deliveries"][0]
        claim_id = str(uuid.uuid4())
        turn_id = f"synthetic-stale-{uuid.uuid4()}"
        old_claim = agent("worker", "session-inbox-claim", {
            "request_id": claim_id, "session": old_ref,
            "delivery_id": original["delivery_id"], "native_turn_id": turn_id,
            "turn_exclusive": False})["attempt"]
        require(old_claim is not None and old_claim["delivery"]["event"]["event_id"] == event["event_id"],
                "old native claim differs from exact request")

        state_dir = home / "native-state"
        state_dir.mkdir(mode=0o700)
        state_path = state_dir / "session.json"
        config = {"cairn": str(binary), "socket": str(socket),
                  "token_file": str(home / "worker.token"), "state_dir": str(state_dir),
                  "binding": "panel-stale-journal", "harness": "codex", "repo": repo,
                  "native_delivery": True}
        state = {"agent": old, "process": coordination.process_reference(os.getpid()),
                 "inbox_intent": {"request_id": claim_id, "session": old_ref,
                                  "native_turn_id": turn_id},
                 "inbox_attempt": old_claim}
        coordination.write_state(state_path, state)
        coordination.inbox_context(config, state, state_path,
                                   {"event": "UserPromptSubmit", "phase": "busy",
                                    "native_turn_id": turn_id})
        context_path = state_dir / "inbox" / (old_claim["attempt_id"] + ".json")
        require(stat.S_IMODE(context_path.stat().st_mode) == 0o600,
                "native context is not owner-only")
        context = json.loads(context_path.read_text())
        require(context["delivery_id"] == original["delivery_id"]
                and context["source"] == ref
                and context["execution_id"] == old_ref["execution_id"],
                "native context not pinned to old execution and source")

        # The caller tries completion once while its local API socket is hidden.
        # Its private journal must retain the exact pending intent.
        hidden = home / "api.sock.hidden"
        os.rename(socket, hidden)
        try:
            journal_command(coordination_script, env, context_path, "ST-6",
                            "API_CONNECTION_FAILED")
        finally:
            os.rename(hidden, socket)
        journal_path = coordination.intent_path(str(state_dir), old_claim["attempt_id"])
        pending = json.loads(journal_path.read_text())
        require(stat.S_IMODE(journal_path.stat().st_mode) == 0o600
                and pending["completion"]["status"] == "pending"
                and pending["completion"]["request_id"] == context["request_ids"]["completion"]
                and pending["completion"]["input"] == "ST-6"
                and pending["completion"]["argv"] == context["commands"]["complete"],
                "old completion intent was not retained unchanged")
        require(agent("sender", "event-inspect", {"event_id": event["event_id"]})
                ["deliveries"][0]["state"] == "leased",
                "unavailable completion unexpectedly settled request")

        # The synthetic host goes offline. A local operator reviews and releases
        # this exact hold before reissuing; no automatic replay is inferred.
        original_id = str(uuid.UUID(original["delivery_id"]))
        reviewed = operator("coordination-review", {"repo": repo,
            "delivery_id": original_id})["deliveries"]["deliveries"]
        require(len(reviewed) == 1 and reviewed[0]["state"] == "leased"
                and reviewed[0]["held"] and reviewed[0].get("result") is None
                and reviewed[0]["latest_native"]["attempt_id"] == old_claim["attempt_id"],
                "operator review did not identify the unfinished exact hold")
        agent_id = str(uuid.UUID(old_ref["agent_id"]))
        require(sql(dsn, "UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' "
                    f"WHERE agent_id='{agent_id}'::uuid") == "UPDATE 1",
                "disposable old presence did not expire")
        released = operator("native-hold-release", {
            "request_id": str(uuid.uuid4()), "repo": repo,
            "attempt_id": old_claim["attempt_id"], "reason": "Reviewed synthetic host outage",
            "accept_uncertain_effects": True})
        require(released["code"] == "operator_released" and released["delivery_state"] == "failed",
                "old hold was not explicitly released")
        reissued = operator("event-reissue", {
            "request_id": str(uuid.uuid4()), "repo": repo,
            "delivery_id": original_id, "reason": "Reviewed exact pending completion",
            "accept_uncertain_effects": True})
        require(reissued["event_id"] != event["event_id"]
                and reissued["causation_id"] == event["event_id"]
                and reissued["ref"] == ref, "reviewed successor lost request identity")
        new_registration = dict(registration, request_id=str(uuid.uuid4()))
        current = agent("worker", "agent-register", new_registration)
        current_ref = {key: current[key] for key in ("agent_id", "execution_id")}
        require(current_ref["agent_id"] == old_ref["agent_id"]
                and current_ref["execution_id"] != old_ref["execution_id"],
                "reviewed recovery did not rotate execution")
        current_ready = operator("coordination-review", {
            "repo": repo, "consumer": old["inbox"], "state": "pending", "limit": 10})
        ready = current_ready["deliveries"]["deliveries"]
        require(len(ready) == 1 and ready[0]["event"]["event_id"] == reissued["event_id"]
                and ready[0]["state"] == "pending",
                "successor was not pending under current execution")
        current_delivery = ready[0]["delivery_id"]
        current_attempt = agent("worker", "session-inbox-claim", {
            "request_id": str(uuid.uuid4()), "session": current_ref,
            "delivery_id": current_delivery,
            "native_turn_id": f"synthetic-current-{uuid.uuid4()}",
            "turn_exclusive": False})["attempt"]
        require(current_attempt is not None
                and current_attempt["session"] == current_ref,
                "current execution did not retain successor")

        old_retry = journal_command(coordination_script, env, context_path, "ST-6", "STALE_SESSION")
        require(old_retry.get("data") is None or old_retry["data"].get("result") is None,
                "old execution returned a result")
        refused = json.loads(journal_path.read_text())["completion"]
        require(refused["status"] == "refused" and refused["code"] == "STALE_SESSION"
                and refused["request_id"] == context["request_ids"]["completion"]
                and refused["input"] == "ST-6"
                and refused["argv"] == context["commands"]["complete"],
                "old pending journal changed identity or was not fenced")
        require(coordination.flush_journal(config, old_claim["attempt_id"]) == set(),
                "refused old journal remained pending")
        original_final = agent("sender", "event-inspect", {"event_id": event["event_id"]})
        successor_final = operator("coordination-review", {"repo": repo,
            "delivery_id": current_delivery})["deliveries"]["deliveries"][0]
        require(original_final["deliveries"][0]["state"] == "failed"
                and original_final["deliveries"][0]["code"] == "operator_released"
                and original_final["deliveries"][0]["attempts"] == 1
                and original_final["deliveries"][0].get("result") is None
                and successor_final["state"] == "leased"
                and successor_final["attempts"] == 1
                and successor_final.get("result") is None
                and sql(dsn, "SELECT count(*) FROM cairn.memory_record") == "1",
                "old journal affected current successor or created a result")

        # Prove the current execution can settle that same reviewed successor.
        current_lease = current_attempt["delivery"]["lease_id"]
        # The CLI complete command reads result text from stdin; use the exact
        # current execution independently of the old journal helper.
        result = subprocess.run([str(binary), "complete", "--socket", str(socket),
            "--token-file", str(home / "worker.token"), "--agent-id", current_ref["agent_id"],
            "--execution-id", current_ref["execution_id"], "--request-id", str(uuid.uuid4()),
            "--lease", current_lease, "--shareable", "--stdin", current_delivery],
            input="Synthetic current-execution ST-6 result", text=True,
            capture_output=True, env=env, timeout=20)
        require(result.returncode == 0 and json.loads(result.stdout)["status"] == "OK",
                "current execution could not complete reviewed successor")
        current_done = json.loads(result.stdout)["data"]
        require(current_done["state"] == "handled" and current_done["result"] is not None,
                "current completion lacked result")
        closed = agent("worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": current_ref,
            "attempt_id": current_attempt["attempt_id"], "reason": "delivery_completed"})
        require(closed["finished_at"] is not None and closed["delivery"]["state"] == "handled",
                "current attempt did not reconcile")
        require(operator("coordination-review", {"repo": repo,
                "delivery_id": current_delivery})["deliveries"]["deliveries"][0]["result"] == current_done["result"]
                and sql(dsn, "SELECT count(*) FROM cairn.memory_record") == "2",
                "current result was not the only new memory")
        print(json.dumps({"case": "stale_execution", "fixture_sha256": fixture_hash,
                          "old_journal": "pending_then_STALE_SESSION",
                          "reviewed_reissue": reissued["event_id"] != event["event_id"],
                          "current_attempt": "handled_once", "actor_task": "unrun",
                          "provider_called": False, "task_outcome": "unassessed"}))
    finally:
        host.close()


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_stale_journal.py BINARY")
    check(Path(sys.argv[1]))
