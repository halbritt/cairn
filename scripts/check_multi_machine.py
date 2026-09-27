"""Exercise two real Cairn relays against one disposable central store."""

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid


def uid():
    return str(uuid.uuid4())


def native_coordination():
    source = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    spec = importlib.util.spec_from_file_location("multi_machine_coordination", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journal_command(argv, body, expected):
    result = subprocess.run(argv, input=body, text=True, capture_output=True,
                            timeout=40, env=client_env())
    envelope = json.loads(result.stdout)
    assert envelope["status"] == expected, (argv, envelope, result.stderr)
    assert (result.returncode == 0) == (expected == "OK"), envelope
    return envelope.get("data")


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

    def call(self, command, *args, body=None, expected="OK", not_contain=None):
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
        if not_contain is not None:
            assert not_contain not in result.stdout and not_contain not in result.stderr
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


def start_service(argv, socket_path, env, label):
    process = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(label + " exited: " + process.stderr.read())
        if socket_path.exists():
            # A killed relay leaves its socket pathname until the replacement
            # takes ownership. Existence alone would mistake that for readiness.
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(socket_path))
                return process
            except OSError:
                pass
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError(label + " did not create its socket")


def stop_service(process):
    process.terminate()
    _, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr


def kill_service(process, socket_path):
    process.kill()
    process.communicate(timeout=10)
    assert process.returncode == -signal.SIGKILL
    assert socket_path.is_socket(), "abrupt death did not leave a stale socket"


def certificate(root):
    cert, key = root / "remote-cert.pem", root / "remote-key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True, timeout=15)
    key.chmod(0o600)
    return cert, key


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def disposable_sql(statement):
    result = subprocess.run(
        ["psql", os.environ["CAIRN_TEST_DATABASE_URL"], "-X", "-v", "ON_ERROR_STOP=1",
         "-c", statement],
        check=True, capture_output=True, text=True, timeout=10)
    assert "UPDATE 1" in result.stdout, result.stdout


def disposable_count(statement):
    result = subprocess.run(
        ["psql", os.environ["CAIRN_TEST_DATABASE_URL"], "-X", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-c", statement],
        check=True, capture_output=True, text=True, timeout=10)
    return int(result.stdout.strip())


def expire_delivery(delivery_id):
    # Accelerate only the disposable database's clock-dependent lease state.
    canonical = str(uuid.UUID(delivery_id))
    disposable_sql("UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' "
                   "WHERE delivery_id='" + canonical + "'::uuid")


def expire_presence(agent_id):
    canonical = str(uuid.UUID(agent_id))
    disposable_sql("UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' "
                   "WHERE agent_id='" + canonical + "'::uuid")


def delivery_snapshot(delivery_id):
    canonical = str(uuid.UUID(delivery_id))
    result = subprocess.run(
        ["psql", os.environ["CAIRN_TEST_DATABASE_URL"], "-X", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-c",
         "SELECT state || '|' || COALESCE(result_id::text,'') FROM cairn.agent_delivery "
         "WHERE delivery_id='" + canonical + "'::uuid"],
        check=True, capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def operator_call(binary, command, request, expected="OK"):
    result = subprocess.run([binary, command], input=json.dumps(request), text=True,
                            capture_output=True, timeout=15)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == expected, (command, envelope, result.stderr)
    assert (result.returncode == 0) == (expected == "OK"), envelope
    return envelope.get("data")


def database_snapshot(root):
    snapshot = root / "before-restore.dump"
    subprocess.run(["pg_dump", "--format=custom", "--file", str(snapshot),
                    os.environ["CAIRN_TEST_DATABASE_URL"]],
                   check=True, capture_output=True, text=True, timeout=20)
    snapshot.chmod(0o600)
    return snapshot


def restore_database(snapshot):
    subprocess.run(["pg_restore", "--clean", "--if-exists", "--single-transaction",
                    "--dbname", os.environ["CAIRN_TEST_DATABASE_URL"], str(snapshot)],
                   check=True, capture_output=True, text=True, timeout=30)


def start_server(binary, root, env, port, cert, key):
    return start_service([binary, "serve", "--identities", str(root / "identities.json"),
                          "--socket", str(root / "api.sock"), "--machine-id", "central",
                          "--listen", "127.0.0.1:" + str(port), "--tls-cert", str(cert),
                          "--tls-key", str(key)], root / "api.sock", env, "central API")


def start_relay(binary, directory, port, cert):
    env = client_env()
    env.update(CAIRN_HOME=str(directory), SSL_CERT_FILE=str(cert))
    return start_service([binary, "relay", "--upstream", "https://127.0.0.1:" + str(port),
                          "--socket", str(directory / "api.sock")],
                         directory / "api.sock", env, "host relay")


class FaultHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        with self.server.fault_lock:
            fault = self.server.faults.pop(self.path, None)
        if fault == "reject":
            encoded = json.dumps(dict(schema="cairn.response/1", ok=False,
                                      status="UPSTREAM_UNAVAILABLE")).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        hop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
        hop.update(item.strip().lower() for item in self.headers.get("Connection", "").split(","))
        headers = {name: value for name, value in self.headers.items()
                   if name.lower() not in hop}
        upstream = http.client.HTTPSConnection(
            "127.0.0.1", self.server.central_port,
            context=ssl.create_default_context(cafile=str(self.server.cert)), timeout=15)
        try:
            upstream.request("POST", self.path, body=body, headers=headers)
            reply = upstream.getresponse()
            encoded = reply.read()
            status = reply.status
            content_type = reply.getheader("Content-Type", "application/json")
        finally:
            upstream.close()
        if fault == "drop":
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


class FaultProxy:
    def __init__(self, central_port, cert, key):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FaultHandler)
        self.server.central_port = central_port
        self.server.cert = cert
        self.server.fault_lock = threading.Lock()
        self.server.faults = {}
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certfile=str(cert), keyfile=str(key))
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def once(self, path, kind):
        assert kind in ("reject", "drop")
        with self.server.fault_lock:
            assert path not in self.server.faults
            self.server.faults[path] = kind

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


def check(binary, directory):
    disposable_root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"]).resolve(strict=True)
    assert disposable_root.parent == Path("/tmp")
    assert disposable_root.name.startswith("cairn-postgres.")
    test_dsn = ("host=" + str(disposable_root / "socket") +
                " dbname=cairn_multi_machine sslmode=disable")
    assert os.environ.get("CAIRN_TEST_DATABASE_URL") == test_dsn
    assert os.environ.get("CAIRN_DATABASE_URL") == test_dsn
    root = Path(directory)
    root.mkdir(mode=0o700)
    a_dir, b_dir = root / "host-a", root / "host-b"
    a_dir.mkdir(mode=0o700)
    b_dir.mkdir(mode=0o700)
    repo = "multi-machine-fixture:" + uid()
    a_token, b_token, observer_token, central_token, private_token = (
        secrets.token_urlsafe(32) for _ in range(5))

    def identity(name, role, token):
        return dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                    principal="machine:" + name + "/" + role, repo=repo,
                    role=role, destination="hosted", remote=True, machine_id=name)

    identities = [identity("host-a", "agent", a_token),
                  identity("host-a", "observer", observer_token),
                  identity("host-b", "agent", b_token),
                  dict(token_sha256=hashlib.sha256(central_token.encode()).hexdigest(),
                       principal="agent:central-fixture", repo=repo, role="agent",
                       destination="hosted"),
                  dict(token_sha256=hashlib.sha256(private_token.encode()).hexdigest(),
                       principal="agent:private-fixture", repo=repo, role="agent",
                       destination="local")]
    config = root / "identities.json"
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    cert, key = certificate(root)
    port = unused_port()
    process = a_relay = b_relay = None
    faults = None
    try:
        process = start_server(binary, root, env, port, cert, key)
        faults = FaultProxy(port, cert, key)
        a_relay = start_relay(binary, a_dir, port, cert)
        b_relay = start_relay(binary, b_dir, faults.port, cert)
    except Exception:
        for service in (b_relay, a_relay, process):
            if service is not None and service.poll() is None:
                stop_service(service)
        if faults is not None:
            faults.close()
        raise
    a = Host(binary, a_dir, a_dir / "api.sock", a_token)
    b = Host(binary, b_dir, b_dir / "api.sock", b_token)
    observer = Host(binary, a_dir, a_dir / "api.sock", observer_token, "observer.token")
    central = Host(binary, root, root / "api.sock", central_token, "central.token")
    # Remote identities are hosted-only; a local identity supplies an actual
    # private pull receipt for the cross-machine refusal checks.
    private_reader = Host(binary, a_dir, root / "api.sock", private_token, "private.token")
    try:
        a.call("version")
        b.call("version")
        central.call("version")
        Host(binary, a_dir, a_dir / "api.sock", central_token, "central-remote.token").call(
            "version", expected="AUTHORITY_DENIED")
        a.call("register-context", body="{}", expected="AUTHORITY_DENIED")
        observer.call("register-context", body="{}", expected="AUTHORITY_DENIED")
        shareable = a.call("remember", "--repo", repo, "--shareable", "--stdin",
                           body="Cross machine shared note " + uid())
        private_body = "Cross machine private note " + uid()
        private = private_reader.call("remember", "--repo", repo, "--stdin", body=private_body)
        search = b.call("search", "--repo", repo, "--task", "trial", "--run", uid(),
                        "Cross machine")
        ids = {item["record_id"] for item in search["index"]}
        assert shareable["record_id"] in ids and private["record_id"] not in ids
        own_private = private_reader.call("search", "--repo", repo, "--task", "trial", "--run", uid(),
                                          '"' + private_body + '"')
        private_entry = next(item for item in own_private["index"]
                             if item["record_id"] == private["record_id"])
        assert private_reader.call("pull", body=json.dumps(private_entry["pull_arguments"]))["selection"]["record"]["body"] == private_body
        b.call("history", body=json.dumps(dict(record_id=private["record_id"], version=1)),
               expected="NOT_FOUND", not_contain=private_body)
        b.call("pull", body=json.dumps(private_entry["pull_arguments"]),
               expected="AUTHORITY_DENIED", not_contain=private_body)

        registration = ("--binding", "same-binding", "--native-session", "same-session",
                        "--harness", "codex", "--project", "cairn", "--workspace")
        a_session = a.call("agents", "register", "--request-id", uid(), *registration,
                           str(a_dir), "--state", "busy")
        b_session = b.call("agents", "register", "--request-id", uid(), *registration,
                           str(b_dir), "--state", "busy")
        assert a_session["agent_id"] != b_session["agent_id"]
        assert a_session["execution_id"] != b_session["execution_id"]
        assert a_session["machine_id"] == "host-a" and b_session["machine_id"] == "host-b"
        listing = a.call("agent-directory", body=json.dumps(dict(machine_id="host-b")))
        assert [item["agent_id"] for item in listing["agents"]] == [b_session["agent_id"]]
        observer.call("agents", "register", "--request-id", uid(), *registration,
                      str(a_dir), expected="AUTHORITY_DENIED")
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
        a.event("complete", *complete_args, session=b_session,
                body="Cross machine result", expected="NOT_FOUND")
        a.event("ack", "--request-id", uid(), "--lease", delivered["lease_id"],
                delivered["delivery_id"], session=b_session, expected="NOT_FOUND")
        a.event("renew", "--lease", delivered["lease_id"], delivered["delivery_id"],
                session=b_session, expected="NOT_FOUND")
        a.event("retry", "--lease", delivered["lease_id"], delivered["delivery_id"],
                session=b_session, expected="AUTHORITY_DENIED")
        a.event("publish", "--request-id", uid(), "--to", a_session["inbox"],
                "--kind", "request", "--version", str(shareable["version"]),
                shareable["record_id"], session=b_session, expected="NOT_FOUND")
        a.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=claim["session"], attempt_id=attempt["attempt_id"],
            reason="turn_ended")), expected="NOT_FOUND")
        a.call("session-inbox-control", body=json.dumps(claim["session"]),
               expected="NOT_FOUND")
        unchanged = a.event("event-status", send["event_id"],
                            session=a_session)["deliveries"][0]
        assert unchanged["state"] == "leased" and unchanged.get("result") is None
        faults.once("/v1/event-complete", "drop")
        b.event("complete", *complete_args, session=b_session,
                body="Cross machine result", expected="UPSTREAM_UNCERTAIN")
        committed = a.event("event-status", send["event_id"], session=a_session)["deliveries"][0]
        assert committed["state"] == "handled" and committed["result"] is not None
        done = b.event("complete", *complete_args, session=b_session,
                       body="Cross machine result")
        assert done["state"] == "handled" and done["result"] == committed["result"]
        stop_service(process)
        process = start_server(binary, root, env, port, cert, key)
        assert b.event("complete", *complete_args, session=b_session,
                       body="Cross machine result") == done
        b.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=claim["session"], attempt_id=attempt["attempt_id"],
            reason="delivery_completed")))
        reply_args = ("--request-id", uid(), "--to", a_session["inbox"],
                      "--kind", "response", "--version", "1", "--causation-id",
                      send["event_id"], done["result"]["record_id"])
        faults.once("/v1/event-publish", "reject")
        b.event("publish", *reply_args, session=b_session, expected="UPSTREAM_UNAVAILABLE")
        faults.once("/v1/event-publish", "drop")
        b.event("publish", *reply_args, session=b_session, expected="UPSTREAM_UNCERTAIN")
        reply = b.event("publish", *reply_args, session=b_session)
        assert b.event("publish", *reply_args, session=b_session) == reply
        assert reply["causation_id"] == send["event_id"]
        assert disposable_count("SELECT count(*) FROM cairn.agent_event WHERE causation_id='" +
                                str(uuid.UUID(send["event_id"])) + "'::uuid") == 1
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

        # A host crash leaves the Unix socket pathname behind. The replacement
        # must reclaim it and restore access under the same registered session.
        kill_service(b_relay, b_dir / "api.sock")
        b_relay = start_relay(binary, b_dir, faults.port, cert)
        assert b.event("event-status", send["event_id"],
                       session=b_session)["deliveries"][0]["result"] == done["result"]

        # A partition after native delivery outlives the lease. The same
        # unreleased attempt must still accept its result when the relay returns.
        late_request = a.event("publish", "--request-id", uid(), "--to", b_session["inbox"],
                               "--kind", "request", "--version", "1",
                               shareable["record_id"], session=a_session)
        late_delivery = a.event("event-status", late_request["event_id"],
                                session=a_session)["deliveries"][0]
        late_claim = dict(request_id=uid(), session=claim["session"],
                          delivery_id=late_delivery["delivery_id"],
                          native_turn_id="two-host-late-turn")
        late_attempt = b.call("session-inbox-claim", body=json.dumps(late_claim))["attempt"]
        held = late_attempt["delivery"]
        late_args = ("--request-id", uid(), "--lease", held["lease_id"],
                     "--shareable", "--stdin", held["delivery_id"])
        stop_service(b_relay)
        b.event("complete", *late_args, session=b_session,
                body="Result produced during partition", expected="API_CONNECTION_FAILED")
        expire_delivery(held["delivery_id"])
        assert a.event("event-status", late_request["event_id"],
                       session=a_session)["deliveries"][0]["state"] == "leased"
        b_relay = start_relay(binary, b_dir, faults.port, cert)
        assert b.call("session-inbox-claim", body=json.dumps(dict(
            request_id=uid(), session=claim["session"])))["attempt"] is None
        # Remote manual inbox-next is denied for both the session and base
        # principal; neither can take the held delivery after lease expiry.
        b.event("inbox", session=b_session, expected="AUTHORITY_DENIED")
        b.event("inbox", expected="AUTHORITY_DENIED")
        a.call("session-inbox-claim", body=json.dumps(dict(
            request_id=uid(), session=claim["session"])), expected="NOT_FOUND")
        b.call("agents", "register", "--request-id", uid(), *registration,
               str(b_dir), "--state", "busy", expected="AGENT_BUSY")
        wrong = ("--request-id", uid(), "--lease", uid(), "--shareable", "--stdin",
                 held["delivery_id"])
        b.event("complete", *wrong, session=b_session,
                body="Wrong lease result", expected="STALE_LEASE")
        late_done = b.event("complete", *late_args, session=b_session,
                            body="Result produced during partition")
        assert late_done["state"] == "handled" and late_done["late"]
        assert b.event("complete", *late_args, session=b_session,
                       body="Result produced during partition") == late_done
        late_status = a.event("event-status", late_request["event_id"],
                              session=a_session)["deliveries"][0]
        assert late_status["state"] == "handled" and late_status["late"]
        assert late_status["result"] == late_done["result"]
        b.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=claim["session"],
            attempt_id=late_attempt["attempt_id"], reason="delivery_completed")))

        # Run the native lifecycle's own durable journal and watcher across
        # two relay outages. The first outage loses completion before send;
        # the second loses the response after the attempt was reconciled.
        coordination = native_coordination()
        journal_request = a.event("publish", "--request-id", uid(), "--to", b_session["inbox"],
                                  "--kind", "request", "--version", "1",
                                  shareable["record_id"], session=a_session)
        journal_delivery = a.event("event-status", journal_request["event_id"],
                                   session=a_session)["deliveries"][0]
        journal_claim = dict(request_id=uid(), session=claim["session"],
                             delivery_id=journal_delivery["delivery_id"],
                             native_turn_id="two-host-journal-turn")
        journal_attempt = b.call("session-inbox-claim", body=json.dumps(journal_claim))["attempt"]
        state_dir = b_dir / "native-state"
        state_dir.mkdir(mode=0o700)
        state_path = state_dir / "session.json"
        native_config = dict(cairn=binary, socket=str(b.socket), token_file=str(b.token_file),
                             state_dir=str(state_dir), binding="two-host-journal", harness="codex",
                             repo=repo, native_delivery=True)
        native_state = dict(agent=b_session, process=dict(pid=os.getpid()),
                            inbox_intent=dict(request_id=journal_claim["request_id"],
                                              session=journal_claim["session"],
                                              native_turn_id=journal_claim["native_turn_id"]),
                            inbox_attempt=journal_attempt)
        coordination.write_state(state_path, native_state)
        with patch.dict(os.environ, client_env(), clear=True):
            prompt = coordination.inbox_context(native_config, native_state, state_path,
                dict(event="UserPromptSubmit", phase="busy", native_turn_id="two-host-journal-turn"))
        assert "Cairn has a request" in prompt
        context_path = state_dir / "inbox" / (journal_attempt["attempt_id"] + ".json")
        context = json.loads(context_path.read_text())
        assert context["schema"] == "cairn.session-inbox/1"
        stop_service(b_relay)
        journal_command(context["completion"], "Journaled result across two relays",
                        "API_CONNECTION_FAILED")
        expire_delivery(journal_delivery["delivery_id"])
        b_relay = start_relay(binary, b_dir, faults.port, cert)
        with patch.dict(os.environ, client_env(), clear=True):
            coordination.watch_inbox(native_config, native_state, state_path)
        journal_status = a.event("event-status", journal_request["event_id"],
                                 session=a_session)["deliveries"][0]
        assert journal_status["state"] == "handled" and journal_status["late"]
        assert "inbox_intent" not in native_state
        intent = json.loads(coordination.intent_path(str(state_dir),
                            journal_attempt["attempt_id"]).read_text())
        assert intent["completion"]["status"] == "committed"
        result_ref = intent["completion"]["result"]
        assert journal_status["result"] == result_ref
        stop_service(b_relay)
        journal_command([*context["response"], "--version", str(result_ref["version"]),
                         result_ref["record_id"]], "", "API_CONNECTION_FAILED")
        b_relay = start_relay(binary, b_dir, faults.port, cert)
        with patch.dict(os.environ, client_env(), clear=True):
            coordination.watch_inbox(native_config, native_state, state_path)
        intent = json.loads(coordination.intent_path(str(state_dir),
                            journal_attempt["attempt_id"]).read_text())
        assert intent["response"]["status"] == "committed"
        assert disposable_count("SELECT count(*) FROM cairn.agent_event WHERE kind='response' "
                                "AND causation_id='" + str(uuid.UUID(journal_request["event_id"])) +
                                "'::uuid") == 1

        rotated_token = secrets.token_urlsafe(32)
        identities[2] = identity("host-b", "agent", rotated_token)
        config.write_text(json.dumps(identities))
        stop_service(process)
        process = start_server(binary, root, env, port, cert, key)
        b.call("version", expected="AUTHORITY_DENIED")
        b.token_file.write_text(rotated_token)
        b.call("agents", "heartbeat", "--agent-id", b_session["agent_id"],
               "--execution-id", b_session["execution_id"])

        lost_request = a.event("publish", "--request-id", uid(), "--to", b_session["inbox"],
                               "--kind", "request", "--version", "1",
                               shareable["record_id"], session=a_session)
        lost_delivery = a.event("event-status", lost_request["event_id"],
                                session=a_session)["deliveries"][0]
        lost_claim = dict(request_id=uid(), session=claim["session"],
                          delivery_id=lost_delivery["delivery_id"],
                          native_turn_id="lost-host-turn")
        lost_attempt = b.call("session-inbox-claim", body=json.dumps(lost_claim))["attempt"]
        release = dict(request_id=uid(), repo=repo, attempt_id=lost_attempt["attempt_id"],
                       reason="Disposable host disappeared after native delivery",
                       accept_uncertain_effects=True)
        operator_call(binary, "native-hold-release", release, expected="SESSION_LIVE")
        stop_service(b_relay)
        expire_presence(b_session["agent_id"])
        release["request_id"] = uid()
        released = operator_call(binary, "native-hold-release", release)
        assert released["delivery_state"] == "failed" and released["code"] == "operator_released"
        assert operator_call(binary, "native-hold-release", release) == released
        b_relay = start_relay(binary, b_dir, faults.port, cert)
        b.event("complete", "--request-id", uid(), "--lease",
                lost_attempt["delivery"]["lease_id"], "--shareable", "--stdin",
                lost_attempt["delivery"]["delivery_id"], session=b_session,
                body="Result returned after operator release", expected="HOLD_RELEASED")
        lost_status = a.event("event-status", lost_request["event_id"],
                              session=a_session)["deliveries"][0]
        assert lost_status["state"] == "failed" and lost_status["code"] == "operator_released"
        assert lost_status.get("result") is None

        identities = [entry for entry in identities if entry.get("machine_id") != "host-a"]
        config.write_text(json.dumps(identities))
        stop_service(process)
        process = start_server(binary, root, env, port, cert, key)
        a.call("version", expected="AUTHORITY_DENIED")
        observer.call("version", expected="AUTHORITY_DENIED")
        b.call("version")

        # Restore a real snapshot of the dedicated disposable database. A
        # completion recorded after the snapshot disappears; the restore fence
        # must reject the old execution before any retry can recreate it.
        before_restore = b.call("agents", "register", "--request-id", uid(),
                                *registration, str(b_dir), "--state", "busy")
        assert before_restore["agent_id"] == b_session["agent_id"]
        restore_event = central.event("publish", "--request-id", uid(),
                                      "--to", before_restore["inbox"], "--kind", "request",
                                      "--version", "1", shareable["record_id"])
        restore_delivery = central.event("event-status", restore_event["event_id"])["deliveries"][0]
        restore_claim = dict(request_id=uid(),
                             session={key: before_restore[key] for key in
                                      ("agent_id", "execution_id")},
                             delivery_id=restore_delivery["delivery_id"],
                             native_turn_id="pre-restore-turn")
        restore_attempt = b.call("session-inbox-claim", body=json.dumps(restore_claim))["attempt"]
        restore_held = restore_attempt["delivery"]
        restore_args = ("--request-id", uid(), "--lease", restore_held["lease_id"],
                        "--shareable", "--stdin", restore_held["delivery_id"])
        stop_service(process)
        snapshot = database_snapshot(root)
        process = start_server(binary, root, env, port, cert, key)
        recorded = b.event("complete", *restore_args, session=before_restore,
                           body="Result whose commit is later restored away")
        assert recorded["state"] == "handled" and recorded["result"] is not None
        stop_service(process)
        restore_database(snapshot)
        assert delivery_snapshot(restore_held["delivery_id"]) == "leased|"
        operator_call(binary, "fence-restore", dict(
            request_id=uid(), reason="Fence restored disposable multi-machine fixture"))
        assert delivery_snapshot(restore_held["delivery_id"]) == "pending|"
        process = start_server(binary, root, env, port, cert, key)
        b.call("version")  # The existing relay reconnects to the restored API.
        b.event("complete", *restore_args, session=before_restore,
                body="Result whose commit is later restored away", expected="STALE_SESSION")
        central.call("history", body=json.dumps(dict(
            record_id=recorded["result"]["record_id"],
            version=recorded["result"]["version"])), expected="NOT_FOUND",
            not_contain="Result whose commit is later restored away")
        b.call("agents", "register", "--request-id", uid(), *registration,
               str(b_dir), "--state", "busy", expected="AGENT_BUSY")
        b.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=restore_claim["session"],
            attempt_id=restore_attempt["attempt_id"], reason="process_exited")))
        resumed = b.call("agents", "register", "--request-id", uid(),
                         *registration, str(b_dir), "--state", "busy")
        assert resumed["agent_id"] == before_restore["agent_id"]
        assert resumed["execution_id"] != before_restore["execution_id"]
        fresh_event = central.event("publish", "--request-id", uid(),
                                    "--to", resumed["inbox"], "--kind", "request",
                                    "--version", "1", shareable["record_id"])
        fresh_delivery = central.event("event-status", fresh_event["event_id"])["deliveries"][0]
        fresh_claim = dict(request_id=uid(),
                           session={key: resumed[key] for key in ("agent_id", "execution_id")},
                           delivery_id=fresh_delivery["delivery_id"],
                           native_turn_id="post-restore-turn")
        fresh_attempt = b.call("session-inbox-claim", body=json.dumps(fresh_claim))["attempt"]
        fresh_held = fresh_attempt["delivery"]
        fresh_done = b.event("complete", "--request-id", uid(), "--lease",
                             fresh_held["lease_id"], "--shareable", "--stdin",
                             fresh_held["delivery_id"], session=resumed,
                             body="Fresh authorized result after restore")
        assert fresh_done["state"] == "handled"
        b.call("session-inbox-reconcile", body=json.dumps(dict(
            request_id=uid(), session=fresh_claim["session"],
            attempt_id=fresh_attempt["attempt_id"], reason="delivery_completed")))
        print("Two relays: shared memory, isolation, lost replies, late completion, operator recovery, rotation, revocation and restore passed")
    finally:
        if a_relay is not None and a_relay.poll() is None:
            stop_service(a_relay)
        if b_relay is not None and b_relay.poll() is None:
            stop_service(b_relay)
        if process is not None and process.poll() is None:
            stop_service(process)
        if faults is not None:
            faults.close()


if __name__ == "__main__":
    check(*sys.argv[1:])
