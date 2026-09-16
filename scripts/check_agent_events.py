"""Real CLI/API restart probe, only against the integration suite's disposable DB."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid


def check(binary, directory):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert os.environ["CAIRN_DATABASE_URL"] == os.environ["CAIRN_TEST_DATABASE_URL"]
    root = Path(directory)
    root.mkdir(mode=0o700)
    repo = "event-probe:" + str(uuid.uuid4())
    identities = []
    for name in ("alice", "bob", "carol"):
        token = secrets.token_urlsafe(32)
        path = root / (name + ".token")
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal="agent/" + name, repo=repo, role="agent",
                               destination="hosted"))
    config = root / "identities.json"
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    provision = Path(__file__).with_name("provision-event-profiles.py")
    provision_args = [sys.executable, str(provision), "--home", str(root), "--repo", repo, "probe"]
    subprocess.run(provision_args, check=True, capture_output=True, text=True, timeout=10)
    original_token = (root / "event-profiles/probe.token").read_bytes()
    subprocess.run(provision_args, check=True, capture_output=True, text=True, timeout=10)
    assert (root / "event-profiles/probe.token").read_bytes() == original_token
    env = dict(os.environ, CAIRN_HOME=str(root))
    process = None

    def call(name, command, *args, body=None, expected="OK", raw=False):
        connection = ["--token-file", str(root / (name + ".token"))]
        if raw:
            argv = [binary, "agent", *connection, command, *args]
        else:
            argv = [binary, command]
            if command == "inbox":
                argv.append("next")
            if command == "agents":
                argv.append(args[0])
                args = args[1:]
            argv += connection + list(args)
        result = subprocess.run(argv, input=body, env=env, text=True,
                                capture_output=True, timeout=15)
        output = json.loads(result.stdout)
        assert output["status"] == expected, (argv, output, result.stderr)
        assert (result.returncode == 0) == (expected == "OK"), output
        return output.get("data")

    def start():
        proc = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read())
            if (root / "api.sock").exists():
                call("alice", "version", raw=True)
                return proc
            time.sleep(0.02)
        proc.terminate()
        proc.wait(timeout=10)
        raise AssertionError("event probe API did not start")

    def stop(proc):
        proc.terminate()
        proc.communicate(timeout=10)
        assert proc.returncode == 0

    def publish(to=None, topic=None, request=None, source=None, cause=None):
        args = ["--request-id", request or str(uuid.uuid4()), "--kind", "request", "--version", "1"]
        args += ["--to", to] if to else ["--topic", topic]
        if cause:
            args += ["--causation-id", cause]
        return call("alice", "publish", *args, source or note["record_id"])

    try:
        process = start()
        profile = subprocess.run([binary, "event-stats", "--profile", "probe"], env=env,
                                 capture_output=True, text=True, timeout=10, check=True)
        assert json.loads(profile.stdout)["data"]["deliveries"] == 0
        note = call("alice", "remember", "--shareable", "--repo", repo,
                    "Event probe source", raw=True)
        request = str(uuid.uuid4())
        event = publish(to="agent/bob", request=request)
        assert publish(to="agent/bob", request=request) == event
        stop(process)
        process = start()
        delivery = call("bob", "inbox")["delivery"]
        assert delivery["event"]["event_id"] == event["event_id"]
        history = call("bob", "history", raw=True, body=json.dumps(delivery["event"]["ref"]))
        assert history["versions"][0]["body"] == "Event probe source"
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", delivery["lease_id"],
             delivery["delivery_id"], expected="NOT_FOUND")
        call("bob", "inbox", "--agent", "agent/alice", expected="AUTHORITY_DENIED")
        complete_args = ["--request-id", str(uuid.uuid4()), "--lease", delivery["lease_id"],
                         "--shareable", "--stdin", delivery["delivery_id"]]
        done = call("bob", "complete", *complete_args, body="Selected response")
        assert done["state"] == "handled" and done["result"]["version"] == 1
        assert call("bob", "complete", *complete_args, body="Selected response") == done
        assert call("bob", "inbox")["delivery"] is None
        status = call("alice", "event-status", event["event_id"])
        assert status["deliveries"][0]["result"] == done["result"]
        response = call("bob", "publish", "--request-id", str(uuid.uuid4()), "--to", "agent/alice",
                        "--kind", "response", "--version", "1", "--causation-id", event["event_id"],
                        done["result"]["record_id"])
        assert response["causation_id"] == event["event_id"]
        for name in ("bob", "carol"):
            call(name, "subscribe", "--request-id", str(uuid.uuid4()), "--topic", "reviews")
        topic_event = publish(topic="reviews")
        call("bob", "unsubscribe", "--request-id", str(uuid.uuid4()), "--topic", "reviews")
        for name in ("bob", "carol"):
            d = call(name, "inbox")["delivery"]
            assert d["event"]["event_id"] == topic_event["event_id"]
            call(name, "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"], d["delivery_id"])
        publish(topic="reviews")
        assert call("bob", "inbox")["delivery"] is None
        d = call("carol", "inbox", "--lease-seconds", "1")["delivery"]
        stop(process)
        process = start()
        time.sleep(1.05)
        again = call("carol", "inbox")["delivery"]
        assert again["event"]["event_id"] == d["event"]["event_id"]
        assert again["lease_id"] != d["lease_id"] and again["attempts"] == 2
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"],
             d["delivery_id"], expected="STALE_LEASE")
        call("carol", "renew", "--lease", again["lease_id"], again["delivery_id"])
        call("carol", "retry", "--lease", again["lease_id"], again["delivery_id"])
        d = call("carol", "inbox")["delivery"]
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"],
             "--disposition", "failed", "--code", "processing_failed", d["delivery_id"])
        assert call("carol", "event-stats")["redeliveries"] == 2
        first = call("bob", "events", "--limit", "1")
        assert first["more"] and first["events"][0]["event_id"] == event["event_id"]
        later = call("bob", "events", "--after", str(first["next_after"]), "--limit", "100")
        assert all(e["position"] > first["next_after"] for e in later["events"])
        raw = dict(request_id=str(uuid.uuid4()), kind="notice", ref=event["ref"],
                   destination=dict(type="agent", name="agent/bob"), **{"from": "agent/carol"})
        call("alice", "event-publish", raw=True, body=json.dumps(raw), expected="INVALID_REQUEST")
        registration = ["register", "--request-id", str(uuid.uuid4()), "--binding", "codex-default",
                        "--native-session", "one", "--harness", "codex", "--model", "configured-model",
                        "--project", "rhumb", "--workspace", str(root), "--state", "busy"]
        registered = call("bob", "agents", *registration)
        assert call("bob", "agents", *registration) == registered
        second_registration = list(registration)
        second_registration[2] = str(uuid.uuid4())
        second_registration[second_registration.index("--native-session") + 1] = "two"
        second_agent = call("bob", "agents", *second_registration)
        assert second_agent["agent_id"] != registered["agent_id"]
        listing = call("alice", "agents", "list", "--harness", "codex", "--project", "rhumb")
        assert len(listing["agents"]) == 2
        session = ["--agent-id", registered["agent_id"], "--execution-id", registered["execution_id"]]
        message = publish(to=registered["inbox"])
        assert call("bob", "inbox")["delivery"] is None
        stop(process)
        process = start()
        delivered = call("bob", "inbox", *session)["delivery"]
        assert delivered["event"]["event_id"] == message["event_id"]
        finished = call("bob", "complete", *session, "--request-id", str(uuid.uuid4()),
                        "--lease", delivered["lease_id"], "--shareable", "--stdin",
                        delivered["delivery_id"], body="Session handled selected request")
        assert finished["state"] == "handled"
        call("bob", "agents", "heartbeat", *session)
        registration[2] = str(uuid.uuid4())
        resumed = call("bob", "agents", *registration)
        assert resumed["agent_id"] == registered["agent_id"]
        assert resumed["execution_id"] != registered["execution_id"]
        call("bob", "inbox", *session, expected="STALE_SESSION")
        print("Agent sessions: stable identity, shared-profile inbox separation, completion and API restart passed")
        print("Agent events: CLI direct/offline delivery, replies, fanout, retry, leases and API restart passed")
    finally:
        if process is not None and process.poll() is None:
            stop(process)


if __name__ == "__main__":
    check(*sys.argv[1:])
