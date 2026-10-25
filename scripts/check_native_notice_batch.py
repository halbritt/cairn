#!/usr/bin/env python3
"""Exercise a real compact notice batch through a disposable Cairn API.

This invokes the native coordinator helper in a synthetic owner process. It
proves per-item Store transitions, not a provider/model turn or task outcome.
"""

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


def native_coordinator():
    path = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    spec = importlib.util.spec_from_file_location("disposable_notice_batch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def call(binary, home, env, role, operation, body):
    result = subprocess.run([str(binary), "agent", "--socket", str(home / "api.sock"),
                             "--token-file", str(home / f"{role}.token"), operation],
                            input=json.dumps(body), text=True, capture_output=True,
                            env=env, timeout=15)
    require(len(result.stdout) < 1_000_000, "API response too large")
    envelope = json.loads(result.stdout)
    require(result.returncode == 0 and envelope.get("status") == "OK",
            f"{operation}: {envelope.get('status')}")
    return envelope["data"]


def operator(binary, env, operation, body=None, args=()):
    result = subprocess.run([str(binary), operation, *args],
                            input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, env=env, timeout=15)
    require(len(result.stdout) < 100_000 and len(result.stderr) < 100_000,
            "operator response too large")
    envelope = json.loads(result.stdout)
    require(result.returncode == 0 and envelope.get("status") == "OK",
            f"operator {operation}: {envelope.get('status')}")
    return envelope["data"]


def compact(script, root, env, native_session_id, operation, handle, disposition=None, code=None):
    command = [sys.executable, str(script), "inbox", "compact", operation, handle]
    if disposition:
        command.append(disposition)
    if code:
        command += ["--code", code]
    # The helper is a child of this synthetic native owner, not the outer
    # Codex session running the test harness.
    helper_env = dict(env, CAIRN_INBOX_ROOT=str(root))
    helper_env.pop("CLAUDE_SESSION_ID", None)
    helper_env["CODEX_THREAD_ID"] = native_session_id
    result = subprocess.run(command, env=helper_env,
                            text=True, capture_output=True, timeout=30)
    require(len(result.stdout) < 100_000 and len(result.stderr) < 100_000,
            "compact response too large")
    response = json.loads(result.stdout)
    require(result.returncode == 0 and response.get("ok") is True,
            f"compact {operation}: {response.get('status')}: {response.get('message')}")
    return response


def sql_count(dsn, table):
    require(table in ("agent_event", "agent_session", "agent_session_attempt"),
            "unsupported disposable count table")
    result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn,
                             "-c", f"SELECT count(*) FROM cairn.{table}"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0, "disposable SQL count failed")
    return int(result.stdout.strip())


def check(binary):
    cluster = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(cluster.parent == Path("/tmp") and cluster.name.startswith("cairn-postgres.")
            and cluster.stat().st_uid == os.getuid()
            and stat.S_IMODE(cluster.stat().st_mode) == 0o700
            and (cluster / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == cluster / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={cluster / 'socket'} dbname=cairn_native_notice_batch sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    require(sql_count(dsn, "agent_event") == 0 and sql_count(dsn, "agent_session") == 0,
            "notice-batch database is not fresh")

    home = cluster / "native-notice-batch-home"
    home.mkdir(mode=0o700)
    repo = "fixture:native-notice-batch"
    principals = {}
    identities = []
    for role in ("sender", "worker"):
        token = secrets.token_urlsafe(32)
        token_path = home / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        principals[role] = f"agent/native-batch-{role}-{uuid.uuid4()}"
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": principals[role], "repo": repo,
                           "role": "agent", "destination": "hosted"})
    config_file = home / "identities.json"
    config_file.write_text(json.dumps(identities), encoding="utf-8")
    config_file.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode), "local API socket missing")
        for role in ("sender", "worker"):
            call(binary, home, env, role, "version", {})
        forget_grant = operator(binary, env, "bootstrap", {
            "request_id": str(uuid.uuid4()),
            "reason": "Authorize forgetting one source in a disposable native batch fixture"})
        session_id = f"synthetic-native-batch-{uuid.uuid4()}"
        registered = call(binary, home, env, "worker", "agent-register", {
            "request_id": str(uuid.uuid4()), "binding": session_id,
            "native_session_id": session_id,
            "metadata": {"harness": "codex", "project": "fixture",
                         "workspace": str(cluster), "state": "idle",
                         "delivery_mode": "existing-session"}})
        session = {key: registered[key] for key in ("agent_id", "execution_id")}
        require(registered["inbox"] == "agent/" + session["agent_id"],
                "registered worker inbox differs")
        events = []
        for index in range(4):
            body = ("Synthetic oversized middle source. " + "qualification " * 2500
                    if index == 1 else f"Synthetic routine notice {index + 1}; exact source.")
            source = call(binary, home, env, "sender", "create", {
                "request_id": str(uuid.uuid4()),
                "draft": {"kind": "note", "body": body,
                          "scope": {"repo": repo, "task_id": "*", "run_id": "*"},
                          "claim_type": "self", "sensitivity": "shareable"}})
            require(source["body"] == body
                    and source["observed_writer"] == principals["sender"],
                    "routine source writer/body differs")
            ref = {"record_id": source["record_id"], "version": source["version"]}
            event = call(binary, home, env, "sender", "event-publish", {
                "request_id": str(uuid.uuid4()), "kind": "notice", "ref": ref,
                "destination": {"type": "agent", "name": registered["inbox"]},
                "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 0}})
            require(event["kind"] == "notice" and event["ref"] == ref
                    and event["notice_policy"]["owner"] == principals["sender"],
                    "routine notice policy or ref differs")
            delivery = call(binary, home, env, "sender", "event-inspect",
                            {"event_id": event["event_id"]})["deliveries"]
            require(len(delivery) == 1 and delivery[0]["state"] == "pending"
                    and delivery[0]["consumer"] == registered["inbox"],
                    "routine delivery not pending")
            events.append({"event_id": event["event_id"], "delivery_id": delivery[0]["delivery_id"],
                           "ref": ref, "body": body})
        require(sql_count(dsn, "agent_event") == 4, "expected four waiting notices")

        turn_id = f"synthetic-native-turn-{uuid.uuid4()}"
        claim = {"request_id": str(uuid.uuid4()), "session": session,
                 "delivery_id": events[0]["delivery_id"], "native_turn_id": turn_id,
                 "turn_exclusive": False}
        first = call(binary, home, env, "worker", "session-inbox-claim", claim)["attempt"]
        require(first is not None and first["delivery"]["event"]["event_id"] == events[0]["event_id"],
                "initial native claim differs")
        coordinator, script = native_coordinator()
        root = cluster / "native-notice-batch-coordination"
        state_dir = root / "state" / "worker"
        state_dir.mkdir(parents=True, mode=0o700)
        binding_dir = root / "bindings"
        binding_dir.mkdir(mode=0o700)
        config = {"cairn": str(binary), "socket": str(home / "api.sock"),
                  "token_file": str(home / "worker.token"),
                  "state_dir": str(state_dir), "binding": "worker", "harness": "codex",
                  "repo": repo, "native_delivery": True,
                  "compact_inbox_version": 1, "notice_batch_version": 1}
        coordinator.write_state(binding_dir / "worker.json", config)
        state_path = state_dir / "session.json"
        state = {"agent": registered, "process": coordinator.process_reference(os.getpid()),
                 "inbox_intent": {"request_id": claim["request_id"], "session": session,
                                  "native_turn_id": turn_id}, "inbox_attempt": first}
        coordinator.write_state(state_path, state)
        # The synthetic owner is this script's live process. Helper subprocesses
        # must descend from it, and all Store calls use the dedicated API socket.
        cue = coordinator.inbox_context(config, state, state_path,
                                        {"event": "UserPromptSubmit", "phase": "busy",
                                         "native_turn_id": turn_id})
        require("cairn-inbox" in cue and events[0]["body"] in cue,
                "first compact notice did not expose exact short source")
        attempts = []
        leases = set()
        handles = set()
        for index, (disposition, code) in enumerate((("handled", None),
                                                      ("failed", "source_unavailable"),
                                                      ("ignored", "unsupported_kind"))):
            current = json.loads(state_path.read_text())["inbox_attempt"]
            context_path = state_dir / "inbox" / (current["attempt_id"] + ".json")
            context = json.loads(context_path.read_text())
            handle = context["compact_handle"]
            require(handle not in handles and current["delivery"]["delivery_id"] == events[index]["delivery_id"]
                    and current["delivery"]["event"]["ref"] == events[index]["ref"]
                    and current["native_turn_id"] == turn_id
                    and (current.get("batch_index") or 1) == index + 1
                    and (current.get("batch_root") or first["attempt_id"]) == first["attempt_id"],
                    "batch item changed turn/root/index/source")
            require(current["delivery"]["lease_id"] not in leases,
                    "two batch items shared a lease")
            handles.add(handle)
            leases.add(current["delivery"]["lease_id"])
            if index == 1:
                # The second delivery is admitted with its own lease before
                # this authorized source removal. Its compact context is
                # oversized, so the cue contains only the scoped handle.
                require(events[index]["body"] not in handoff["next"],
                        "oversized forgotten source leaked into the next cue")
                preview = operator(binary, env, "preview-delete",
                                   args=(events[index]["ref"]["record_id"],))
                forgotten = operator(binary, env, "forget", {
                    "request_id": str(uuid.uuid4()),
                    "record_id": events[index]["ref"]["record_id"],
                    "expected_version": events[index]["ref"]["version"],
                    "grant_id": forget_grant["grant_id"],
                    "preview_id": preview["preview_id"]})
                require(forgotten["record_id"] == events[index]["ref"]["record_id"],
                        "operator forgot a different batch source")
                read_env = dict(env, CAIRN_INBOX_ROOT=str(root), CODEX_THREAD_ID=session_id)
                read_env.pop("CLAUDE_SESSION_ID", None)
                unavailable = subprocess.run(
                    [sys.executable, str(script), "inbox", "compact", "read", handle],
                    env=read_env, text=True, capture_output=True, timeout=30)
                response = json.loads(unavailable.stdout)
                require(unavailable.returncode != 0
                        and response.get("status") == "PAYLOAD_UNAVAILABLE"
                        and events[index]["body"] not in unavailable.stdout
                        and events[index]["body"] not in unavailable.stderr,
                        "forgotten source did not remain private and explicitly unavailable")
            else:
                exact = compact(script, root, env, session_id, "read", handle)
                require(exact["body"] == events[index]["body"]
                        and exact["source"] == events[index]["ref"],
                        "scoped helper read changed exact source")
            settled = compact(script, root, env, session_id, "settle", handle, disposition, code)
            require(settled["status"] == disposition and settled["attempts"] == 1,
                    "per-item settlement not confirmed")
            attempts.append(current)
            handoff = compact(script, root, env, session_id, "advance", handle)
            if index < 2:
                require(handoff["batch_index"] == index + 2 and handoff["batch_limit"] == 3
                        and isinstance(handoff["next"], str),
                        "next notice was not transferred under batch cap")
                next_attempt = json.loads(state_path.read_text())["inbox_attempt"]
                replay = call(binary, home, env, "worker", "session-inbox-advance", {
                    "request_id": context["batch_advance_request_id"], "session": session,
                    "attempt_id": current["attempt_id"], "version": 1})
                require(replay["attempt"]["attempt_id"] == next_attempt["attempt_id"]
                        and replay["attempt"]["delivery"]["delivery_id"]
                        == events[index + 1]["delivery_id"],
                        "same handoff request produced duplicate or different successor")
            else:
                require(handoff["next"] is None and handoff["remainder"] == "batch_limit",
                        "third item failed to enforce batch cap")
        require(sql_count(dsn, "agent_session_attempt") == 3,
                "handoff retry created duplicate attempts")
        expected_codes = (None, "source_unavailable", "unsupported_kind")
        for index, disposition in enumerate(("handled", "failed", "ignored")):
            expected_code = expected_codes[index]
            delivery = call(binary, home, env, "sender", "event-inspect",
                            {"event_id": events[index]["event_id"]})["deliveries"][0]
            require(delivery["delivery_id"] == events[index]["delivery_id"]
                    and delivery["state"] == disposition and delivery["attempts"] == 1
                    and (delivery.get("code") == expected_code if expected_code
                         else delivery.get("code") is None)
                    and delivery.get("result") is None,
                    "per-item durable delivery outcome differs")
        fourth = call(binary, home, env, "sender", "event-inspect",
                      {"event_id": events[3]["event_id"]})["deliveries"][0]
        require(fourth["state"] == "pending" and fourth["attempts"] == 0,
                "fourth notice was consumed by bounded batch")
        final_attempt = attempts[-1]
        closed = call(binary, home, env, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": final_attempt["attempt_id"], "reason": "delivery_completed"})
        require(closed["finished_at"] is not None,
                "final native hold not reconciled")
        ready = call(binary, home, env, "worker", "session-inbox-ready", session)
        require(ready["delivery_id"] == events[3]["delivery_id"],
                "fourth notice not available for later turn")

        # A priority delivery can arrive after a later turn has claimed its
        # first routine notice. The batch must stop before transferring the
        # already-waiting routine neighbor; neither pending delivery is settled.
        later_source = call(binary, home, env, "sender", "create", {
            "request_id": str(uuid.uuid4()),
            "draft": {"kind": "note", "body": "Synthetic mid-batch priority fixture source.",
                      "scope": {"repo": repo, "task_id": "*", "run_id": "*"},
                      "claim_type": "self", "sensitivity": "shareable"}})
        later_ref = {"record_id": later_source["record_id"], "version": later_source["version"]}
        routine_event = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "notice", "ref": later_ref,
            "destination": {"type": "agent", "name": registered["inbox"]},
            "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 0}})
        next_turn = f"synthetic-native-priority-turn-{uuid.uuid4()}"
        priority_claim = {"request_id": str(uuid.uuid4()), "session": session,
                          "delivery_id": ready["delivery_id"], "native_turn_id": next_turn,
                          "turn_exclusive": False}
        first_priority_batch = call(binary, home, env, "worker", "session-inbox-claim", {
            **priority_claim})["attempt"]
        require(first_priority_batch is not None
                and first_priority_batch["delivery"]["event"]["event_id"] == events[3]["event_id"],
                "later turn did not claim the exact remaining routine notice")
        urgent_event = call(binary, home, env, "sender", "event-publish", {
            "request_id": str(uuid.uuid4()), "kind": "notice", "ref": later_ref,
            "destination": {"type": "agent", "name": registered["inbox"]},
            "notice_policy": {"version": 1, "urgency": "urgent", "defer_seconds": 0}})
        priority_state = {"agent": registered, "process": coordinator.process_reference(os.getpid()),
                          "inbox_intent": {"request_id": first_priority_batch["attempt_id"],
                                           "session": session, "native_turn_id": next_turn},
                          "inbox_attempt": first_priority_batch,
                          "inbox_journals": [first_priority_batch["attempt_id"]],
                          "inbox_result_policy": {"claim_request_id": first_priority_batch["attempt_id"],
                                                   "version": 1}}
        coordinator.write_state(state_path, priority_state)
        priority_cue = coordinator.inbox_context(config, priority_state, state_path,
            {"event": "UserPromptSubmit", "phase": "busy", "native_turn_id": next_turn})
        require("cairn-inbox" in priority_cue, "new routine item did not receive its own compact context")
        priority_context = json.loads((state_dir / "inbox" /
                                       (first_priority_batch["attempt_id"] + ".json")).read_text())
        priority_handle = priority_context["compact_handle"]
        settled_priority = compact(script, root, env, session_id, "settle", priority_handle, "handled")
        require(settled_priority["status"] == "handled" and settled_priority["attempts"] == 1,
                "claimed routine item was not explicitly settled")
        stopped = compact(script, root, env, session_id, "advance", priority_handle)
        require(stopped["next"] is None and stopped["remainder"] == "priority_waiting",
                "urgent arrival failed to stop the native batch")
        for event in (routine_event, urgent_event):
            delivery = call(binary, home, env, "sender", "event-inspect",
                            {"event_id": event["event_id"]})["deliveries"][0]
            require(delivery["state"] == "pending" and delivery["attempts"] == 0,
                    "batch stop settled or claimed a neighboring delivery")
        reconciled = call(binary, home, env, "worker", "session-inbox-reconcile", {
            "request_id": str(uuid.uuid4()), "session": session,
            "attempt_id": first_priority_batch["attempt_id"], "reason": "delivery_completed"})
        require(reconciled["finished_at"] is not None,
                "priority boundary did not retain explicit turn settlement")
        print(json.dumps({"case": "native_notice_batch", "store_api": "disposable",
                          "source_helper": "real", "items_in_one_synthetic_turn": 3,
                          "outcomes": ["handled", "failed", "ignored"],
                          "forgotten_middle_source": "explicit_source_unavailable_neighbors_preserved",
                          "remainder": "batch_limit_fourth_pending",
                          "urgent_arrival": "stops_batch_without_claiming_neighbor",
                          "handoff_retry": "same_attempt_no_duplicate",
                          "provider_called": False, "task_outcome": "unassessed"}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_native_notice_batch.py BINARY")
    check(Path(sys.argv[1]))
