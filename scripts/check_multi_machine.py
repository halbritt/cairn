"""Exercise independent machine principals against a disposable Cairn store.

The host sockets are supplied by the topology fixture. The initial fixture uses
the central Unix API; the remote fixture will attach one relay per host.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid


def uid():
    return str(uuid.uuid4())


def client_env():
    env = {key: value for key, value in os.environ.items()
           if key not in ("CAIRN_DATABASE_URL", "CAIRN_TEST_DATABASE_URL")}
    env["CAIRN_DATABASE_URL"] = "host=/nonexistent-multi-machine-db dbname=denied"
    return env


class Host:
    def __init__(self, binary, directory, socket, token, token_name="agent.token"):
        self.binary = binary
        self.directory = directory
        self.socket = socket
        self.token_file = directory / token_name
        self.token_file.write_text(token)
        self.token_file.chmod(0o600)

    def call(self, command, *args, body=None, expected="OK"):
        if command == "agents":
            argv = [self.binary, "agents", args[0], "--socket", str(self.socket),
                    "--token-file", str(self.token_file), *args[1:]]
        else:
            argv = [self.binary, "agent", "--socket", str(self.socket),
                    "--token-file", str(self.token_file), command, *args]
        result = subprocess.run(argv, input=body, text=True, capture_output=True,
                                timeout=15, env=client_env())
        envelope = json.loads(result.stdout)
        assert envelope["status"] == expected, (command, args, envelope, result.stderr)
        assert (result.returncode == 0) == (expected == "OK"), envelope
        return envelope.get("data")

    def event(self, command, *args, body=None, expected="OK", session=None):
        argv = [self.binary, command]
        if command == "inbox":
            argv.append("next")
        argv.extend(("--socket", str(self.socket),
                     "--token-file", str(self.token_file)))
        if session:
            argv.extend(("--agent-id", session["agent_id"],
                         "--execution-id", session["execution_id"]))
        argv.extend(args)
        result = subprocess.run(argv, input=body, text=True, capture_output=True,
                                timeout=15, env=client_env())
        envelope = json.loads(result.stdout)
        assert envelope["status"] == expected, (command, args, envelope, result.stderr)
        assert (result.returncode == 0) == (expected == "OK"), envelope
        return envelope.get("data")


def start_server(binary, root, env):
    process = subprocess.Popen([binary, "serve", "--identities", str(root / "identities.json"),
                                "--socket", str(root / "api.sock")], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("central API exited: " + process.stderr.read())
        if (root / "api.sock").exists():
            return process
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("central API did not create its socket")


def stop_server(process):
    process.terminate()
    _, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr


def check(binary, directory):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert os.environ.get("CAIRN_DATABASE_URL") == os.environ["CAIRN_TEST_DATABASE_URL"]
    root = Path(directory)
    root.mkdir(mode=0o700)
    a_dir, b_dir = root / "host-a", root / "host-b"
    a_dir.mkdir(mode=0o700)
    b_dir.mkdir(mode=0o700)
    repo = "multi-machine-fixture:" + uid()
    a_token, b_token, observer_token = (secrets.token_urlsafe(32) for _ in range(3))

    def identity(name, role, token):
        return dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                    principal="machine:" + name + "/" + role, repo=repo,
                    role=role, destination="hosted")

    identities = [identity("host-a", "agent", a_token),
                  identity("host-a", "observer", observer_token),
                  identity("host-b", "agent", b_token)]
    config = root / "identities.json"
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    process = start_server(binary, root, env)
    a = Host(binary, a_dir, root / "api.sock", a_token)
    b = Host(binary, b_dir, root / "api.sock", b_token)
    observer = Host(binary, a_dir, root / "api.sock", observer_token, "observer.token")
    try:
        a.call("version")
        b.call("version")
        a.call("register-context", body="{}", expected="AUTHORITY_DENIED")
        observer.call("register-context", body="{}", expected="INVALID_REQUEST")
        shareable = a.call("remember", "--repo", repo, "--shareable", "--stdin",
                           body="Cross machine shared note " + uid())
        private = a.call("remember", "--repo", repo, "--stdin",
                         body="Cross machine private note " + uid())
        search = b.call("search", "--repo", repo, "--task", "trial", "--run", uid(),
                        "Cross machine")
        ids = {item["record_id"] for item in search["index"]}
        assert shareable["record_id"] in ids and private["record_id"] not in ids

        registration = ("--binding", "same-binding", "--native-session", "same-session",
                        "--harness", "codex", "--project", "cairn", "--workspace")
        a_session = a.call("agents", "register", "--request-id", uid(), *registration,
                           str(a_dir), "--state", "busy")
        b_session = b.call("agents", "register", "--request-id", uid(), *registration,
                           str(b_dir), "--state", "busy")
        assert a_session["agent_id"] != b_session["agent_id"]
        assert a_session["execution_id"] != b_session["execution_id"]
        request_id = uid()
        send = a.event("publish", "--request-id", request_id, "--to", b_session["inbox"],
                       "--kind", "request", "--version", str(shareable["version"]),
                       shareable["record_id"], session=a_session)
        assert a.event("publish", "--request-id", request_id, "--to", b_session["inbox"],
                       "--kind", "request", "--version", str(shareable["version"]),
                       shareable["record_id"], session=a_session) == send
        delivery = a.event("event-status", send["event_id"], session=a_session)["deliveries"][0]
        claim = dict(request_id=uid(), session={key: b_session[key] for key in
                                                ("agent_id", "execution_id")},
                     delivery_id=delivery["delivery_id"], native_turn_id="two-host-turn")
        attempt = b.call("session-inbox-claim", body=json.dumps(claim))["attempt"]
        assert b.call("session-inbox-claim", body=json.dumps(claim))["attempt"] == attempt
        alien_claim = dict(claim, request_id=uid())
        a.call("session-inbox-claim", body=json.dumps(alien_claim), expected="NOT_FOUND")
        held_claim = dict(request_id=uid(), session=claim["session"])
        assert b.call("session-inbox-claim", body=json.dumps(held_claim))["attempt"] is None
        delivered = attempt["delivery"]
        source = b.call("history", body=json.dumps(delivered["event"]["ref"]))
        assert source["versions"][0]["body"].startswith("Cross machine shared note ")
        completion_id = uid()
        complete_args = ("--request-id", completion_id, "--lease", delivered["lease_id"],
                         "--shareable", "--stdin", delivered["delivery_id"])
        a.event("complete", *complete_args, body="Cross machine result", expected="NOT_FOUND")
        done = b.event("complete", *complete_args, session=b_session,
                       body="Cross machine result")
        assert done["state"] == "handled" and done["result"]["version"] == 1
        stop_server(process)
        process = start_server(binary, root, env)
        assert b.event("complete", *complete_args, session=b_session,
                       body="Cross machine result") == done
        b.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=claim["session"], attempt_id=attempt["attempt_id"],
            reason="delivery_completed")))
        reply = b.event("publish", "--request-id", uid(), "--to", a_session["inbox"],
                        "--kind", "response", "--version", "1", "--causation-id",
                        send["event_id"], done["result"]["record_id"], session=b_session)
        assert reply["causation_id"] == send["event_id"]
        reply_delivery = b.event("event-status", reply["event_id"], session=b_session)["deliveries"][0]
        reply_claim = dict(request_id=uid(), session={key: a_session[key] for key in
                                                  ("agent_id", "execution_id")},
                           delivery_id=reply_delivery["delivery_id"],
                           native_turn_id="two-host-response-turn")
        reply_attempt = a.call("session-inbox-claim", body=json.dumps(reply_claim))["attempt"]
        received = reply_attempt["delivery"]
        assert received["event"]["event_id"] == reply["event_id"]
        a.event("ack", "--request-id", uid(), "--lease", received["lease_id"],
                received["delivery_id"], session=a_session)
        a.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=reply_claim["session"],
            attempt_id=reply_attempt["attempt_id"], reason="delivery_completed")))
        assert a.event("event-status", send["event_id"], session=a_session)["deliveries"][0]["result"] == done["result"]
        rotated_token = secrets.token_urlsafe(32)
        identities[2] = identity("host-b", "agent", rotated_token)
        config.write_text(json.dumps(identities))
        stop_server(process)
        process = start_server(binary, root, env)
        b.call("version", expected="AUTHORITY_DENIED")
        b.token_file.write_text(rotated_token)
        b.call("agents", "heartbeat", "--agent-id", b_session["agent_id"],
               "--execution-id", b_session["execution_id"])
        identities = identities[1:]
        config.write_text(json.dumps(identities))
        stop_server(process)
        process = start_server(binary, root, env)
        a.call("version", expected="AUTHORITY_DENIED")
        b.call("version")
        print("Two principals: hosted retrieval, role isolation, durable completion/reply, rotation and revocation passed")
    finally:
        if process.poll() is None:
            stop_server(process)


if __name__ == "__main__":
    check(*sys.argv[1:])
