"""Protocol skew between independently built Cairn binaries.

Runs real `cairn serve`, `cairn relay` and CLI clients over HTTPS against a
disposable PostgreSQL database. The binaries are:

- legacy: the deployed b46a0e1 release (protocol 1), built from a clean clone,
  or CAIRN_LEGACY_BINARY when root supplies an independently retained one;
- current: the binary under test (protocols 1 to 2);
- raised: the binary under test built with the test-only cairn_protocol_min2
  tag, standing in for a future server that has raised its minimum.

Each combination is a separate case. Refusals must happen before any effect,
which is checked by counting records in the disposable store.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import http.client
import time
import uuid

LEGACY_REVISION = "b46a0e1"


def uid():
    return str(uuid.uuid4())


def client_env(home):
    env = {key: value for key, value in os.environ.items()
           if key not in ("CAIRN_DATABASE_URL", "CAIRN_TEST_DATABASE_URL")}
    env["CAIRN_DATABASE_URL"] = "host=/nonexistent-protocol-skew-db dbname=denied"
    env["CAIRN_HOME"] = str(home)
    return env


def legacy_binary(root, source):
    supplied = os.environ.get("CAIRN_LEGACY_BINARY")
    if supplied:
        binary = Path(supplied).resolve(strict=True)
    else:
        clone = root / "legacy-source"
        subprocess.run(["git", "clone", "--quiet", "--no-local", str(source), str(clone)],
                       check=True, capture_output=True, timeout=120)
        subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", LEGACY_REVISION],
                       check=True, capture_output=True, timeout=30)
        binary = root / "cairn-legacy"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/cairn"], cwd=clone,
                       check=True, capture_output=True, timeout=600)
    info = json.loads(subprocess.run([str(binary), "version"], capture_output=True,
                                     text=True, check=True, timeout=15).stdout)["data"]
    assert info["vcs_revision"].startswith(LEGACY_REVISION) and info["vcs_modified"] is False, info
    return str(binary)


def raised_binary(root, source):
    binary = root / "cairn-min2"
    subprocess.run(["go", "build", "-tags", "cairn_protocol_min2", "-o", str(binary), "./cmd/cairn"],
                   cwd=source, check=True, capture_output=True, timeout=600)
    return str(binary)


def certificate(root):
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True, timeout=30)
    key.chmod(0o600)
    return cert, key


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_socket(process, path, label):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(label + " exited: " + process.stderr.read())
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.2)
                probe.connect(str(path))
            return process
        except OSError:
            time.sleep(0.05)
    process.terminate()
    raise AssertionError(label + " did not listen")


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        process.communicate(timeout=10)


def record_count(repo):
    result = subprocess.run(["psql", os.environ["CAIRN_TEST_DATABASE_URL"], "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                             "-c", "SELECT count(*) FROM cairn.record_version WHERE repo='" + repo + "'"],
                            check=True, capture_output=True, text=True, timeout=10)
    return int(result.stdout.strip())


class Host:
    def __init__(self, directory, token):
        directory.mkdir(mode=0o700)
        self.directory = directory
        self.socket = directory / "api.sock"
        self.token_file = directory / "hosted-agent.token"
        self.token_file.write_text(token)
        self.token_file.chmod(0o600)

    def cli(self, binary, *args, body=None, expected="OK"):
        argv = [binary, "agent", "--socket", str(self.socket), "--token-file", str(self.token_file), *args]
        result = subprocess.run(argv, input=body, text=True, capture_output=True, timeout=30,
                                env=client_env(self.directory))
        envelope = json.loads(result.stdout)
        assert envelope["status"] == expected, (binary, args, envelope, result.stderr)
        return envelope


def check(current, directory):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    source = Path(__file__).resolve().parents[1]
    root = Path(directory)
    root.mkdir(mode=0o700)
    legacy = legacy_binary(root, source)
    raised = raised_binary(root, source)
    repo = "protocol-skew:" + uid()
    token = secrets.token_urlsafe(32)
    identities = root / "identities.json"
    identities.write_text(json.dumps([dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                                           principal="machine:skew-host/agent", repo=repo, role="agent",
                                           destination="hosted", remote=True, machine_id="skew-host")]))
    identities.chmod(0o600)
    cert, key = certificate(root)
    host = Host(root / "host", token)
    port = unused_port()
    server = relay = None

    def start_server(binary):
        env = dict(os.environ, CAIRN_HOME=str(root))
        process = subprocess.Popen([binary, "serve", "--identities", str(identities), "--socket", str(root / "api.sock"),
                                    "--machine-id", "central", "--listen", "127.0.0.1:" + str(port),
                                    "--tls-cert", str(cert), "--tls-key", str(key)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        wait_socket(process, root / "api.sock", "server")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return process
            except OSError:
                time.sleep(0.05)
        raise AssertionError("server TLS listener did not open")

    def start_relay(binary):
        env = client_env(host.directory)
        env["SSL_CERT_FILE"] = str(cert)
        process = subprocess.Popen([binary, "relay", "--upstream", "https://127.0.0.1:" + str(port),
                                    "--socket", str(host.socket)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return wait_socket(process, host.socket, "relay")

    def remember(client, expected="OK"):
        before = record_count(repo)
        envelope = host.cli(client, "remember", "--repo", repo, "--shareable", "--request-id", uid(), "--stdin",
                            body="Protocol skew note " + uid(), expected=expected)
        assert record_count(repo) == before + (1 if expected == "OK" else 0), "refused call changed the store"
        return envelope

    def raw(headers):
        """One HTTPS call straight to the central listener, as a relay would make."""
        headers = dict(headers)
        request = dict(request_id=uid(), draft=dict(
            kind="note", body="raw " + uid(), sensitivity="shareable", claim_type="self",
            scope=dict(repo=repo, task_id="*", run_id="*")))
        guard = headers.pop("X-Body-Guard", None)
        if guard is not None:
            request["cairn_protocol"] = int(guard)
        context = ssl.create_default_context(cafile=str(cert))
        connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=10)
        try:
            connection.request("POST", "/v1/create", body=json.dumps(request),
                headers=dict({"Authorization": "Bearer " + token, "Content-Type": "application/json"}, **headers))
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()

    results = []

    def case(name, server_binary, relay_binary, client_binary, expected, detail=None):
        nonlocal server, relay
        stop(relay)
        stop(server)
        server = start_server(server_binary)
        relay = start_relay(relay_binary)
        envelope = remember(client_binary, expected)
        if detail and client_binary != legacy:
            # The deployed CLI masks messages of statuses it does not know;
            # a legacy client still receives the distinct status code.
            assert detail in envelope.get("message", ""), (name, envelope)
        results.append((name, expected))
        return envelope

    try:
        # Baseline and both directions of permitted skew while min is 1.
        case("legacy client, legacy relay, legacy server", legacy, legacy, legacy, "OK")
        case("legacy client, legacy relay, current server", current, legacy, legacy, "OK")
        ok = case("current client, legacy relay, current server (header stripped)", current, legacy, current, "OK")
        assert ok["ok"]
        case("current client, current relay, current server", current, current, current, "OK")
        version = host.cli(current, "version")["data"]
        assert version["protocol"] == 2 and version["server_protocol"] == {"min": 1, "current": 2}, version
        case("legacy client, current relay, current server", current, current, legacy, "OK")
        case("current client, current relay, legacy server", legacy, current, current, "OK")
        version = host.cli(current, "version")["data"]
        assert version["server_protocol"] == {"min": 1, "current": 1} and version["protocol"] == 1, version
        case("current client, legacy relay, legacy server", legacy, legacy, current, "OK")

        # A server that raised its minimum refuses undeclared protocol 1 before
        # any effect, and says whether the relay is the likely cause.
        case("current client, current relay, raised server", raised, current, current, "OK")
        refused = case("legacy client, current relay, raised server", raised, current, legacy,
                       "PROTOCOL_UNSUPPORTED", "upgrade the client")
        assert "relay" not in refused["message"], refused
        case("current client, legacy relay, raised server", raised, legacy, current,
             "PROTOCOL_UNSUPPORTED", "legacy relay that drops Cairn-Protocol")
        case("legacy client, legacy relay, raised server", raised, legacy, legacy,
             "PROTOCOL_UNSUPPORTED", "legacy relay")

        # The other incompatible direction: a client whose minimum is 2. Its body
        # guard makes any server that cannot honor the minimum refuse before an
        # effect, including a legacy server that ignores Cairn-Protocol, a legacy
        # relay that strips it, and a server swapped after a successful preflight.
        case("raised client, current relay, legacy server", legacy, current, raised,
             "PROTOCOL_UNSUPPORTED", "predates protocol 2")
        case("raised client, legacy relay, legacy server", legacy, legacy, raised,
             "PROTOCOL_UNSUPPORTED", "predates protocol 2")
        case("raised client, legacy relay, current server (guard survives)", current, legacy, raised, "OK")
        case("raised client, legacy relay, raised server (guard survives)", raised, legacy, raised, "OK")
        case("raised client, current relay, current server", current, current, raised, "OK")
        preflight = host.cli(raised, "version")["data"]
        assert preflight["protocol"] == 2 and preflight["server_protocol"] == {"min": 1, "current": 2}, preflight
        stop(server)
        server = start_server(legacy)  # Rolled back behind the same relay after the preflight.
        before = record_count(repo)
        swapped = host.cli(raised, "remember", "--repo", repo, "--shareable", "--request-id", uid(), "--stdin",
                           body="Written only if the rollback were missed", expected="PROTOCOL_UNSUPPORTED")
        assert "predates protocol 2" in swapped["message"] and record_count(repo) == before, swapped
        results.append(("raised client after preflight, server rolled back to legacy", "PROTOCOL_UNSUPPORTED"))

        # Malformed and out-of-range declarations on the wire, current server.
        stop(relay)
        stop(server)
        server, relay = start_server(current), None
        for headers, status, code in (
                ({"Cairn-Protocol": "3"}, 426, "PROTOCOL_UNSUPPORTED"),
                ({"Cairn-Protocol": "02"}, 400, "INVALID_REQUEST"),
                ({"Cairn-Protocol": "two"}, 400, "INVALID_REQUEST"),
                ({"Cairn-Protocol": "2", "Cairn-Relay-Protocol": "-1"}, 400, "INVALID_REQUEST"),
                ({"X-Body-Guard": "3"}, 426, "PROTOCOL_UNSUPPORTED"),
                ({"Cairn-Protocol": "2", "X-Body-Guard": "3"}, 400, "INVALID_REQUEST")):
            before = record_count(repo)
            got, envelope = raw(headers)
            assert (got, envelope["status"]) == (status, code), (headers, got, envelope)
            assert envelope["protocol"] == {"min": 1, "current": 2}, envelope
            assert record_count(repo) == before, "refused declaration changed the store"
            results.append(("wire " + json.dumps(headers), code))
        got, envelope = raw({"Cairn-Protocol": "2", "Cairn-Relay-Protocol": "2"})
        assert got == 200 and envelope["ok"] and envelope["protocol"] == {"min": 1, "current": 2}, envelope

        # machine status on the joining host decides by protocol, not build.
        (host.directory / "machine.json").write_text(json.dumps(dict(
            schema="cairn.machine/1", machine_id="skew-host", upstream="https://127.0.0.1:" + str(port),
            collection=repo, principals=["machine:skew-host/agent"], enrolled_at="2026-09-26T00:00:00Z")))
        (host.directory / "machine.json").chmod(0o600)
        for server_binary, relay_binary, want, exit_ok in (
                (current, current, "compatible", True),
                (legacy, current, "compatible", True),
                (raised, legacy, "incompatible", False)):
            stop(relay)
            stop(server)
            server, relay = start_server(server_binary), start_relay(relay_binary)
            result = subprocess.run([current, "machine", "status"], capture_output=True, text=True, timeout=30,
                                    env=client_env(host.directory))
            status = json.loads(result.stdout)
            data = status.get("data") or {}
            assert (result.returncode == 0) == exit_ok and data.get("protocol") == want, (server_binary, status)
            results.append(("machine status " + Path(server_binary).name + " via " + Path(relay_binary).name, want))
        print("Protocol skew: " + str(len(results)) + " combinations passed against legacy " + LEGACY_REVISION +
              ", current and raised-minimum binaries")
    finally:
        stop(relay)
        stop(server)


if __name__ == "__main__":
    check(*sys.argv[1:])
