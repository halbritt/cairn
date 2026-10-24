#!/usr/bin/env python3
"""Probe a committed native inbox result with a pending reply during API loss.

The hook owner and Store are real disposable processes. A synthetic host supplies
the turn boundary; no model actor handles the request. The API socket is hidden
before the first reply publication, so this checks pending intent replay rather
than an uncertain receipt after a remote commit.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import stat
import subprocess
import sys
import time
import uuid

import preflight_token_efficiency_panel as panel


OWNER = r'''
import json, os, subprocess, sys
script, config = sys.argv[1:3]
for line in sys.stdin:
    result = subprocess.run([sys.executable, script, 'hook', '--config', config],
        input=line, text=True, capture_output=True, timeout=15)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout,
                          stderr=result.stderr)), flush=True)
'''


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_completion_reply_lost sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                text=True, capture_output=True, timeout=10)
        require(result.returncode == 0, result.stderr)
        return result.stdout.strip()

    require(sql("SELECT count(*) FROM cairn.agent_event") == "0"
            and sql("SELECT count(*) FROM cairn.agent_session") == "0",
            "reply-loss database is not fresh")
    fixture, fixture_hash = panel.read_fixture()
    rows = [row for row in fixture["cases"] if row["id"] == "completion_reply_lost"]
    require(len(rows) == 1, "completion_reply_lost fixture missing")
    row = rows[0]
    require(row["setup"] == {"sources": [], "events": [{"id": "request_lost_reply",
            "kind": "request", "recipient": "worker", "body": "Report checksum CK-9."}],
            "transitions": [{"phase": "after_completion", "action": "drop_reply",
                             "target": "request_lost_reply"},
                            {"phase": "after_drop", "action": "retry_delivery",
                             "target": "request_lost_reply"}]}, "fixture setup changed")
    repo = fixture["event_source_defaults"]["scope"]["repo"]
    home = root / "panel-completion-reply-lost-home"
    home.mkdir(mode=0o700)
    identities = []
    for role in ("sender", "worker"):
        token = secrets.token_urlsafe(32)
        token_path = home / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": f"agent/panel-reply-loss-{role}-{uuid.uuid4()}",
                           "repo": repo, "role": "agent", "destination": "hosted"})
    identities_path = home / "identities.json"
    identities_path.write_text(json.dumps(identities), encoding="utf-8")
    identities_path.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    owner = None
    try:
        socket = home / "api.sock"
        deadline = time.monotonic() + 10
        while not socket.exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK(socket.stat().st_mode), "disposable API socket missing")

        def call(role, command, body, session=None):
            options = ["--socket", str(socket), "--token-file", str(home / f"{role}.token")]
            if session:
                options += ["--agent-id", session["agent_id"],
                            "--execution-id", session["execution_id"]]
            result = subprocess.run([str(binary), "agent", *options, command],
                    input=json.dumps(body), text=True, capture_output=True, env=env, timeout=15)
            envelope = json.loads(result.stdout)
            require(result.returncode == 0 and envelope["status"] == "OK",
                    f"{command}: {envelope['status']}")
            return envelope["data"]

        for role in ("sender", "worker"):
            call(role, "version", {})
        binding_root = home / "bindings"
        binding_root.mkdir(mode=0o700)
        state = home / "state" / "worker"
        state.mkdir(parents=True, mode=0o700)
        config = binding_root / "worker.json"
        config.write_text(json.dumps({"cairn": str(binary), "socket": str(socket),
            "token_file": str(home / "worker.token"), "repo": repo, "harness": "codex",
            "binding": "panel-reply-loss", "model": "synthetic/probe",
            "process_names": ["python3"], "state_dir": str(state),
            "native_delivery": True}), encoding="utf-8")
        config.chmod(0o600)
        script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
        owner = subprocess.Popen([sys.executable, "-u", "-c", OWNER, str(script), str(config)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True,
                                 env=dict(env, CAIRN_INBOX_ROOT=str(home)))

        def hook(name):
            owner.stdin.write(json.dumps({"session_id": "panel-reply-loss", "cwd": str(root),
                "hook_event_name": name, "model": "synthetic/probe",
                "prompt": "Synthetic host boundary only"}) + "\n")
            owner.stdin.flush()
            require(select.select([owner.stdout], [], [], 20)[0], "hook owner timed out")
            response = json.loads(owner.stdout.readline())
            require(response["code"] == 0, f"{name}: {response['stderr']}")
            return json.loads(response["stdout"])

        hook("UserPromptSubmit")
        result = subprocess.run([str(binary), "agents", "list", "--socket", str(socket),
            "--token-file", str(home / "worker.token"), "--harness", "codex"],
            text=True, capture_output=True, env=env, timeout=15)
        require(result.returncode == 0, f"worker session list failed: {result.stdout} {result.stderr}")
        agents = json.loads(result.stdout)["data"]["agents"]
        matched = [agent for agent in agents if agent["native_session_id"] == "panel-reply-loss"]
        require(len(matched) == 1, "native hook did not register one session")
        worker = matched[0]
        agent_id = str(uuid.UUID(worker["agent_id"]))
        require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{agent_id}'")
                == identities[1]["principal"], "Store worker owner mismatch")

        request_body = row["setup"]["events"][0]["body"]
        source = call("sender", "create", {"request_id": str(uuid.uuid4()), "draft": {
            "kind": "note", "body": request_body,
            "scope": fixture["event_source_defaults"]["scope"],
            "claim_type": "self", "sensitivity": "shareable"}})
        require(source["observed_writer"] == identities[0]["principal"]
                and source["body"] == request_body, "request source differs")
        ref = {"record_id": source["record_id"], "version": source["version"]}
        event = call("sender", "event-publish", {"request_id": str(uuid.uuid4()),
            "kind": "request", "ref": ref,
            "destination": {"type": "agent", "name": worker["inbox"]}})
        require(event["from"] == identities[0]["principal"] and event["ref"] == ref,
                "request publication differs")
        require(hook("Stop").get("decision") == "block", "hook did not claim request")
        contexts = [json.loads(path.read_text()) for path in (state / "inbox").glob("*.json")]
        matched = [context for context in contexts if context.get("event_id") == event["event_id"]]
        require(len(matched) == 1, "owner-only native context missing")
        context = matched[0]
        require(context["agent_id"] == worker["agent_id"]
                and context["execution_id"] == worker["execution_id"]
                and context["source"] == ref and context["sender"] == identities[0]["principal"],
                "native context does not bind original request")
        original = call("worker", "history", ref)
        require(len(original["versions"]) == 1
                and original["versions"][0]["body"] == request_body
                and original["versions"][0]["observed_writer"] == identities[0]["principal"],
                "worker could not read exact request source")
        context_path = state / "inbox" / f"{context['attempt_id']}.json"
        require(stat.S_IMODE(context_path.stat().st_mode) == 0o600,
                "native context is not owner-only")
        claim_poll_count = sql("SELECT count(*) FROM cairn.agent_session_poll")
        require(sql("SELECT count(*) FROM cairn.agent_session_attempt") == "1"
                and int(claim_poll_count) >= 1, "native hook did not retain one claim")
        result_body = "CK-9"
        def journaled(command, body=""):
            result = subprocess.run(command, input=body, text=True, capture_output=True,
                                    env=env, timeout=30)
            return result.returncode, json.loads(result.stdout)

        code, completed = journaled(context["completion"], result_body)
        require(code == 0 and completed["status"] == "OK"
                and completed["data"]["state"] == "handled", "completion failed")
        result_ref = completed["data"]["result"]
        require(result_ref and result_ref["version"] == 1, "committed result missing")
        result_history = call("worker", "history", result_ref)
        require(len(result_history["versions"]) == 1
                and result_history["versions"][0]["body"] == result_body
                and result_history["versions"][0]["observed_writer"] == worker["inbox"],
                "committed result content or Store writer differs")
        before = call("sender", "event-inspect", {"event_id": event["event_id"]})
        require(len(before["deliveries"]) == 1
                and before["deliveries"][0]["state"] == "handled"
                and before["deliveries"][0]["attempts"] == 1
                and before["deliveries"][0]["result"] == result_ref,
                "completion did not persist")
        require(sql("SELECT count(*) FROM cairn.memory_record") == "2"
                and sql("SELECT count(*) FROM cairn.agent_event") == "1",
                "result/source counts after completion")
        reply_command = [*context["response"], "--version", "1", result_ref["record_id"]]
        hidden_socket = home / "api.sock.unavailable"
        os.rename(socket, hidden_socket)
        try:
            code, lost = journaled(reply_command)
            require(code != 0 and lost["status"] == "API_CONNECTION_FAILED",
                    "outage did not leave reply pending")
        finally:
            os.rename(hidden_socket, socket)
        journal_path = state / "intents" / f"{context['attempt_id']}.json"
        pending = json.loads(journal_path.read_text())
        require(pending["completion"]["status"] == "committed"
                and pending["completion"]["result"] == result_ref
                and pending["response"]["status"] == "pending"
                and pending["response"]["argv"] == [*context["commands"]["respond"],
                    "--version", "1", result_ref["record_id"]]
                and pending["response"]["request_id"] == context["request_ids"]["response"],
                "durable completion or pending reply intent changed")
        require(sql("SELECT count(*) FROM cairn.agent_event") == "1",
                "unavailable reply unexpectedly published")
        code, replayed = journaled(reply_command)
        require(code == 0 and replayed["status"] == "OK", "pending reply replay failed")
        response = replayed["data"]
        require(response["kind"] == "response" and response["from"] == worker["inbox"]
                and response["causation_id"] == event["event_id"]
                and response["ref"] == result_ref
                and response["destination"] == {"type": "agent",
                                                 "name": identities[0]["principal"]},
                "replayed reply did not cite committed result and request")
        reply_status = call("worker", "event-inspect", {"event_id": response["event_id"]},
                            session=worker)
        require(reply_status["event"]["ref"] == result_ref
                and reply_status["event"]["causation_id"] == event["event_id"]
                and len(reply_status["deliveries"]) == 1
                and reply_status["deliveries"][0]["state"] == "pending",
                "durable reply or sender delivery differs")
        sender_result = call("sender", "history", result_ref)
        require(len(sender_result["versions"]) == 1
                and sender_result["versions"][0]["body"] == result_body,
                "sender could not read the exact reply result")
        code, completion_retry = journaled(context["completion"], result_body)
        require(code == 0 and completion_retry["data"]["result"] == result_ref,
                "same completion did not replay")
        code, reply_retry = journaled(reply_command)
        require(code == 0 and reply_retry["data"] == response, "same reply did not replay")
        final = json.loads(journal_path.read_text())
        require(final["completion"]["request_id"] == context["request_ids"]["completion"]
                and final["response"]["request_id"] == context["request_ids"]["response"]
                and final["completion"]["status"] == "committed"
                and final["response"]["status"] == "committed",
                "journal identities changed after replay")
        counts = [sql(f"SELECT count(*) FROM cairn.{table}") for table in
                  ("memory_record", "agent_event", "agent_delivery",
                   "agent_session_attempt", "agent_session_poll")]
        require(counts[:4] == ["2", "2", "2", "1"],
                f"completion, reply, or claim duplicated: {counts}")
        require(counts[4] == claim_poll_count, "replay made a second inbox poll")
        print(json.dumps({"fixture_sha256": fixture_hash,
                          "case": "completion_reply_lost",
                          "loss_boundary": "pre_publication_api_unavailable",
                          "completion_committed": True,
                          "reply_pending_before_retry": True,
                          "reply_replayed_with_same_intent": True,
                          "native_context": "hook_generated_synthetic_turn",
                          "actor_task": "unrun", "provider_called": False,
                          "task_outcome": "unassessed"}, sort_keys=True))
    finally:
        if owner is not None:
            if owner.poll() is None:
                owner.terminate()
            try:
                owner.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.communicate(timeout=5)
        if server.poll() is None:
            server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_completion_reply_lost.py BINARY")
    check(Path(sys.argv[1]).resolve())
