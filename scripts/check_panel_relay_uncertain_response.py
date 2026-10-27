#!/usr/bin/env python3
"""Lose one relay response receipt, then retry its native journaled intent.

All services and identities belong to this invocation's disposable database.
The fixed completion is test setup, not an actor response or task outcome.
"""

import hashlib
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
from unittest.mock import patch

import check_multi_machine as relay_probe
import preflight_token_efficiency_panel as panel
import disposable_host


def require(value, message):
    if not value:
        raise AssertionError(message)


def identity(machine, principal, token, repo):
    return {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "principal": principal, "repo": repo, "role": "agent",
            "destination": "hosted", "remote": True, "machine_id": machine}


def sql_rows(dsn, statement):
    result = subprocess.run(["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                             "-d", dsn, "-c", statement],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0 and len(result.stdout) < 4096,
            "disposable SQL inspection failed")
    return result.stdout.strip().splitlines() if result.stdout.strip() else []


def journal_command(context_path, body):
    script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    command = [sys.executable, str(script), "inbox", "--context", str(context_path),
               "finish-reply"]
    result = subprocess.run(command, input=body, text=True, capture_output=True,
                            env=relay_probe.client_env(), timeout=45)
    require(len(result.stdout) < 100_000, "native journal response too large")
    return result.returncode, json.loads(result.stdout)


def start_central(binary, root, env, port, cert, key):
    command = [str(binary), "serve", "--identities", str(root / "identities.json"),
               "--socket", str(root / "api.sock"), "--machine-id", "central",
               "--listen", "127.0.0.1:" + str(port), "--tls-cert", str(cert),
               "--tls-key", str(key)]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(f"central API exited: {stdout[:500]} {stderr[:500]}")
        if (root / "api.sock").exists():
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(root / "api.sock"))
                return process
            except OSError:
                pass
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("central API did not create its socket")


def check(binary):
    cluster = disposable_host.ProvidedCluster.from_environment()
    disposable = cluster.root
    cluster.binary(binary)
    # This fixture drives its own central server and relays, so it owns only the
    # database here and publishes it to those services through the environment.
    dsn = cluster.create_database("cairn_panel_relay_uncertain_response")
    os.environ["CAIRN_TEST_DATABASE_URL"] = os.environ["CAIRN_DATABASE_URL"] = dsn
    disposable_host.checked([binary, "migrate"], "migrate", env=dict(os.environ), timeout=300)
    require(sql_rows(dsn, "SELECT count(*) FROM cairn.agent_event") == ["0"]
            and sql_rows(dsn, "SELECT count(*) FROM cairn.agent_session") == ["0"],
            "relay-response database is not fresh")

    fixture, fixture_hash = panel.read_fixture()
    cases = [item for item in fixture["cases"] if item["id"] == "relay_uncertain_response"]
    require(len(cases) == 1, "relay_uncertain_response fixture missing")
    row = cases[0]
    require(row["setup"] == {
        "sources": [], "events": [{"id": "request_relay_uncertain", "kind": "request",
                                 "recipient": "worker", "body": "Return marker RY-5."}],
        "transitions": [{"phase": "after_send_before_receipt", "action": "hide_receipt",
                         "target": "request_relay_uncertain"},
                        {"phase": "after_uncertain", "action": "retry_same_response",
                         "target": "request_relay_uncertain"}]},
        "relay-response fixture setup changed")
    repo = fixture["event_source_defaults"]["scope"]["repo"]
    root = disposable / "panel-relay-uncertain-home"
    sender_dir, worker_dir = root / "sender", root / "worker"
    for directory in (root, sender_dir, worker_dir):
        directory.mkdir(mode=0o700)
    sender_token, worker_token, central_token = (secrets.token_urlsafe(32) for _ in range(3))
    sender_principal = f"machine:panel-sender/agent"
    worker_principal = f"machine:panel-worker/agent"
    identities = [identity("panel-sender", sender_principal, sender_token, repo),
                  identity("panel-worker", worker_principal, worker_token, repo),
                  {"token_sha256": hashlib.sha256(central_token.encode()).hexdigest(),
                   "principal": f"agent/panel-relay-central-{uuid.uuid4()}", "repo": repo,
                   "role": "agent", "destination": "hosted"}]
    config = root / "identities.json"
    config.write_text(json.dumps(identities), encoding="utf-8")
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    cert, key = relay_probe.certificate(root)
    central_port = relay_probe.unused_port()
    central = sender_relay = worker_relay = faults = None
    try:
        central = start_central(binary, root, env, central_port, cert, key)
        faults = relay_probe.FaultProxy(central_port, cert, key)
        sender_relay = relay_probe.start_relay(str(binary), sender_dir, central_port, cert)
        worker_relay = relay_probe.start_relay(str(binary), worker_dir, faults.port, cert)
        sender = relay_probe.Host(str(binary), sender_dir, sender_dir / "api.sock",
                                  sender_token)
        worker = relay_probe.Host(str(binary), worker_dir, worker_dir / "api.sock",
                                  worker_token)
        sender.call("version")
        worker.call("version")
        def register(host, name, directory):
            session = host.call("agents", "register", "--request-id", str(uuid.uuid4()),
                                "--binding", f"panel-relay-{name}-{uuid.uuid4()}",
                                "--native-session", f"panel-relay-{name}-{uuid.uuid4()}",
                                "--harness", "codex", "--project", "fixture",
                                "--workspace", str(directory), "--state", "idle",
                                "--delivery-mode", "existing-session", "--repo", repo)
            require(session["inbox"] == "agent/" + session["agent_id"],
                    "registered inbox identity changed")
            return session
        sender_session = register(sender, "sender", sender_dir)
        worker_session = register(worker, "worker", worker_dir)
        require(sender_session["agent_id"] != worker_session["agent_id"],
                "sender and worker sessions collapsed")
        source = sender.call("remember", "--request-id", str(uuid.uuid4()),
                             "--repo", repo, "--task", "*", "--run", "*",
                             "--kind", "note", "--shareable", "--stdin",
                             body=row["setup"]["events"][0]["body"])
        require(source["body"] == "Return marker RY-5."
                and source["observed_writer"] == sender_principal,
                "fixture request source differs")
        request = sender.event("publish", "--request-id", str(uuid.uuid4()),
                               "--to", worker_session["inbox"], "--kind", "request",
                               "--version", str(source["version"]), source["record_id"],
                               session=sender_session)
        require(request["kind"] == "request"
                and request["from"] == sender_session["inbox"],
                "request publisher or kind differs")
        incoming = sender.event("event-status", request["event_id"],
                                session=sender_session)["deliveries"]
        require(len(incoming) == 1 and incoming[0]["state"] == "pending",
                "fixture request was not pending")
        ref = {key: worker_session[key] for key in ("agent_id", "execution_id")}
        claim = {"request_id": str(uuid.uuid4()), "session": ref,
                 "delivery_id": incoming[0]["delivery_id"],
                 "native_turn_id": f"synthetic-relay-{uuid.uuid4()}",
                 "turn_exclusive": False}
        attempt = worker.call("session-inbox-claim", body=json.dumps(claim))["attempt"]
        require(attempt is not None and attempt["delivery"]["event"]["event_id"] == request["event_id"],
                "native fixture claim differs")

        native = relay_probe.native_coordination()
        state_dir = worker_dir / "native-state"
        state_dir.mkdir(mode=0o700)
        state_path = state_dir / "session.json"
        native_config = {"cairn": str(binary), "socket": str(worker.socket),
                         "token_file": str(worker.token_file), "state_dir": str(state_dir),
                         "binding": "panel-relay-uncertain", "harness": "codex",
                         "repo": repo, "native_delivery": True}
        native_state = {"agent": worker_session,
                        "process": native.process_reference(os.getpid()),
                        "inbox_intent": {"request_id": claim["request_id"],
                                         "session": ref,
                                         "native_turn_id": claim["native_turn_id"]},
                        "inbox_attempt": attempt}
        native.write_state(state_path, native_state)
        with patch.dict(os.environ, relay_probe.client_env(), clear=True):
            native.inbox_context(native_config, native_state, state_path,
                                 {"event": "UserPromptSubmit", "phase": "busy",
                                  "native_turn_id": claim["native_turn_id"]})
        context_path = state_dir / "inbox" / (attempt["attempt_id"] + ".json")
        require(context_path.is_file() and stat.S_IMODE(context_path.stat().st_mode) == 0o600,
                "native context missing or not private")
        context = json.loads(context_path.read_text())
        require(context["schema"] == "cairn.session-inbox/1"
                and context["delivery_id"] == incoming[0]["delivery_id"]
                and context["source"] == {"record_id": source["record_id"],
                                          "version": source["version"]},
                "native context not bound to exact fixture source version")
        # This is a synthetic result solely to make a causal response available
        # for the transport test. No actor/model has executed the fixture task.
        synthetic_result = "Synthetic transport result only; actor task unrun."
        faults.once("/v1/event-publish", "drop")
        first_code, first = journal_command(context_path, synthetic_result)
        require(first_code != 0 and first["status"] == "UPSTREAM_UNCERTAIN"
                and first["data"]["completion_committed"] is True
                and first["data"]["reply_pending"] is True,
                "relay did not lose committed publication receipt")
        journal_path = native.intent_path(str(state_dir), attempt["attempt_id"])
        journal = json.loads(journal_path.read_text())
        completion, response = journal["completion"], journal["response"]
        require(completion["status"] == "committed" and response["status"] == "pending"
                and response["code"] == "UPSTREAM_UNCERTAIN"
                and response["request_id"] == context["request_ids"]["response"]
                and response["expected_ref"] == completion["result"],
                "native journal lost immutable response intent")
        result_ref = completion["result"]
        response_rows = sql_rows(dsn,
            "SELECT event_id::text FROM cairn.agent_event WHERE kind='response' AND "
            f"causation_id='{uuid.UUID(request['event_id'])}'::uuid")
        require(len(response_rows) == 1, "dropped receipt did not leave one durable response")
        response_id = response_rows[0]
        durable = worker.event("event-status", response_id,
                               session=worker_session)
        require(durable["event"]["event_id"] == response_id
                and durable["event"]["ref"] == result_ref
                and durable["event"]["causation_id"] == request["event_id"]
                and durable["event"]["from"] == worker_session["inbox"]
                and durable["event"]["destination"] == {"type": "agent",
                                                      "name": sender_session["inbox"]}
                and len(durable["deliveries"]) == 1
                and durable["deliveries"][0]["consumer"] == sender_session["inbox"],
                "durable response differs from journaled result/cause")
        before_retry = json.loads(journal_path.read_text())["response"]
        second_code, second = journal_command(context_path, synthetic_result)
        require(second_code == 0 and second["status"] == "OK"
                and second["data"]["reply_committed"] is True,
                "same journaled response retry did not resolve uncertainty")
        after_retry = json.loads(journal_path.read_text())["response"]
        require(after_retry["request_id"] == before_retry["request_id"]
                and after_retry["argv"] == before_retry["argv"]
                and after_retry["input"] == before_retry["input"]
                and after_retry["expected_ref"] == result_ref
                and after_retry["status"] == "committed",
                "retry changed the response identity or result")
        require(sql_rows(dsn,
            "SELECT event_id::text FROM cairn.agent_event WHERE kind='response' AND "
            f"causation_id='{uuid.UUID(request['event_id'])}'::uuid") == [response_id],
            "response retry published a duplicate event")
        final = worker.event("event-status", response_id,
                             session=worker_session)
        require(final == durable, "durable response receipt changed on retry")
        print(json.dumps({"case": "relay_uncertain_response", "fixture_sha256": fixture_hash,
                          "transport": "relay_forwarded_commit_then_dropped_reply",
                          "journal_first": "UPSTREAM_UNCERTAIN",
                          "journal_retry": "committed_same_response_identity",
                          "durable_response_count": 1, "synthetic_completion": True,
                          "actor_task": "unrun", "provider_called": False,
                          "task_outcome": "unassessed"}))
    finally:
        for service in (worker_relay, sender_relay, central):
            if service is not None and service.poll() is None:
                relay_probe.stop_service(service)
        if faults is not None:
            faults.close()


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_relay_uncertain_response.py BINARY")
    check(Path(sys.argv[1]))
