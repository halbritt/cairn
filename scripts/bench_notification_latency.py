"""Bounded notification timings against one disposable PostgreSQL cluster.

The shell runner owns the database. This driver creates fixture credentials and
processes only under that root; no credential or raw output is saved in the repo.
"""

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import time
from unittest.mock import patch
import uuid


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=15)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def api(socket_path, token, operation, request, session=None):
    connection = UnixHTTPConnection(socket_path)
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if session:
        headers.update({"Cairn-Agent-ID": session["agent_id"],
                        "Cairn-Execution-ID": session["execution_id"]})
    start = time.monotonic_ns()
    try:
        connection.request("POST", "/v1/" + operation, json.dumps(request).encode(), headers)
        response = connection.getresponse()
        envelope = json.loads(response.read())
    finally:
        connection.close()
    elapsed = time.monotonic_ns() - start
    assert response.status == 200 and envelope["status"] == "OK", (operation, envelope)
    return envelope["data"], elapsed


def start_service(command, socket_path, env, log_path, token):
    log = log_path.open("w")
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=log)
    log.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("service exited: " + log_path.read_text()[-2000:])
        if socket_path.exists():
            try:
                api(socket_path, token, "version", {})
                return process
            except (OSError, AssertionError):
                pass
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("service readiness timed out: " + log_path.read_text()[-2000:])


def stop_service(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    assert process.returncode == 0, process.returncode


def token_file(root, name, token):
    path = root / (name + ".token")
    path.write_text(token)
    path.chmod(0o600)
    return path


def distribution(values_ns):
    values = sorted(value / 1_000_000 for value in values_ns)
    def rank(p):
        return values[max(0, (len(values) * p + 99) // 100 - 1)]
    return dict(n=len(values), min_ms=round(values[0], 3),
                p50_ms=round(rank(50), 3), p95_ms=round(rank(95), 3),
                p99_ms=round(rank(99), 3), max_ms=round(values[-1], 3),
                mean_ms=round(statistics.mean(values), 3),
                samples_ms=[round(value, 3) for value in values])


def drain(socket_path, token):
    cursor = ""
    while True:
        page, _ = api(socket_path, token, "event-watch", {"cursor": cursor, "limit": 100})
        cursor = page["cursor"]
        if not page["more"]:
            return cursor


def publish(socket_path, token, source, destination):
    request = dict(request_id=str(uuid.uuid4()), kind="notice",
                   ref=dict(record_id=source, version=1),
                   destination=dict(type="agent", name=destination))
    return api(socket_path, token, "event-publish", request)


def coordination_module():
    path = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    spec = importlib.util.spec_from_file_location("notification_bench_coordination", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def no_client_database():
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("CAIRN_DATABASE_URL", "CAIRN_TEST_DATABASE_URL")}
    environment["CAIRN_DATABASE_URL"] = "host=/nonexistent-notification-bench-db dbname=denied"
    with patch.dict(os.environ, environment, clear=True):
        yield


def check_root(root):
    root = root.resolve(strict=True)
    assert root.parent == Path("/tmp") and root.name.startswith("cairn-notification-bench.")
    assert os.environ.get("CAIRN_DISPOSABLE_BENCH_ROOT") == str(root)
    dsn = "host=" + str(root / "socket") + " dbname=cairn_notification_bench sslmode=disable"
    assert os.environ.get("CAIRN_DATABASE_URL") == dsn
    assert os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
    return root


def run(args):
    root = check_root(args.root)
    repo = "notification-bench:" + str(uuid.uuid4())
    publisher = "agent:bench-publisher"
    receiver = "machine:bench-relay/agent"
    pub_token, recv_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_file(root, "publisher", pub_token)
    recv_file = token_file(root, "receiver", recv_token)
    identities = [dict(token_sha256=hashlib.sha256(pub_token.encode()).hexdigest(),
                       principal=publisher, repo=repo, role="agent", destination="hosted"),
                  dict(token_sha256=hashlib.sha256(recv_token.encode()).hexdigest(),
                       principal=receiver, repo=repo, role="agent", destination="hosted",
                       remote=True, machine_id="bench-relay")]
    config = root / "identities.json"
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True, timeout=15)
    key.chmod(0o600)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    central, relay = root / "central.sock", root / "relay.sock"
    env = dict(os.environ, CAIRN_HOME=str(root / "home"))
    relay_env = dict(env, SSL_CERT_FILE=str(cert))
    server = bridge = None
    samples = {}
    try:
        server = start_service([str(args.binary), "serve", "--identities", str(config),
            "--socket", str(central), "--machine-id", "bench-central",
            "--listen", "127.0.0.1:" + str(port), "--tls-cert", str(cert),
            "--tls-key", str(key)], central, env, root / "server.log", pub_token)
        bridge = start_service([str(args.binary), "relay", "--socket", str(relay),
            "--upstream", "https://127.0.0.1:" + str(port)],
            relay, relay_env, root / "relay.log", recv_token)
        draft = dict(kind="note", body="Synthetic notification benchmark source",
                     scope=dict(repo=repo, task_id="fixture", run_id="fixture"),
                     sensitivity="shareable", claim_type="self")
        source, _ = api(central, pub_token, "create",
                        dict(request_id=str(uuid.uuid4()), draft=draft))
        source_id = source["record_id"]
        probe = subprocess.run([str(args.store_probe), "--samples", str(args.samples),
            "--warmup", str(args.warmup), "--repo", repo, "--source", source_id,
            "--receiver", receiver], capture_output=True, text=True, check=True,
            timeout=90, env=env)
        samples["store_publish_commit"] = [json.loads(line)["ns"]
            for line in probe.stdout.splitlines() if line]
        assert len(samples["store_publish_commit"]) == args.samples

        for label, endpoint in (("central_unix", central), ("relay_tls", relay)):
            cursor = drain(endpoint, recv_token)
            empty, publish_ack, watch_fresh, visibility = [], [], [], []
            for i in range(args.warmup + args.samples):
                page, elapsed = api(endpoint, recv_token, "event-watch",
                                    {"cursor": cursor, "limit": 100})
                assert not page["deliveries"] and not page["more"]
                if i >= args.warmup:
                    empty.append(elapsed)
                event, publish_elapsed = publish(central, pub_token, source_id, receiver)
                published_at = time.monotonic_ns()
                page, watched = api(endpoint, recv_token, "event-watch",
                                    {"cursor": cursor, "limit": 100})
                assert len(page["deliveries"]) == 1
                assert page["deliveries"][0]["event"]["event_id"] == event["event_id"]
                cursor = page["cursor"]
                if i >= args.warmup:
                    publish_ack.append(publish_elapsed)
                    watch_fresh.append(watched)
                    visibility.append(time.monotonic_ns() - published_at)
            samples[label + "_watch_empty"] = empty
            if label == "central_unix":
                samples["central_unix_publish_ack"] = publish_ack
            samples[label + "_watch_fresh"] = watch_fresh
            samples[label + "_ack_to_watch_return"] = visibility

        coordination = coordination_module()
        state_dir = root / "native-state"
        state_dir.mkdir(mode=0o700)
        state_path = state_dir / "session.json"
        registration = dict(request_id=str(uuid.uuid4()), binding="bench-native",
            native_session_id="notification-bench-native", metadata=dict(harness="codex",
            project="cairn", workspace=str(root), state="busy",
            delivery_mode="existing-session"))
        session, _ = api(relay, recv_token, "agent-register", registration)
        native_config = dict(cairn=str(args.binary), socket=str(relay),
            token_file=str(recv_file), state_dir=str(state_dir), binding="bench-native",
            harness="codex", repo=repo, native_delivery=True)
        state = dict(agent=session, process=coordination.process_reference(os.getpid()))
        coordination.write_state(state_path, state)
        presence = []
        with no_client_database():
            for i in range(args.warmup + args.samples):
                start = time.monotonic_ns()
                coordination.watch_once(native_config)
                elapsed = time.monotonic_ns() - start
                if i >= args.warmup:
                    presence.append(elapsed)
        samples["presence_watch_once"] = presence
        state = json.loads(state_path.read_text())
        native_boundary = []
        with no_client_database():
            for i in range(args.warmup + args.samples):
                event, _ = publish(central, pub_token, source_id, session["inbox"])
                turn = "bench-turn-" + str(i)
                start = time.monotonic_ns()
                prompt = coordination.inbox_context(native_config, state, state_path,
                    dict(event="UserPromptSubmit", phase="busy", native_turn_id=turn))
                elapsed = time.monotonic_ns() - start
                assert "Cairn has a notice" in prompt
                attempt = state["inbox_attempt"]
                context_path = state_dir / "inbox" / (attempt["attempt_id"] + ".json")
                context = json.loads(context_path.read_text())
                assert context["event_id"] == event["event_id"]
                ack = subprocess.run(context["acknowledgement"], capture_output=True,
                                     text=True, timeout=30, check=True)
                assert json.loads(ack.stdout)["status"] == "OK"
                coordination.watch_inbox(native_config, state, state_path)
                assert "inbox_intent" not in state
                coordination.inbox_context(native_config, state, state_path,
                    dict(event="Stop", phase="idle", native_turn_id=turn))
                if i >= args.warmup:
                    native_boundary.append(elapsed)
        samples["native_context_boundary"] = native_boundary
    finally:
        stop_service(bridge)
        stop_service(server)

    # These are controlled phase fixtures, not observed notifications or model wake.
    for label, period_ms in (("simulated_watch_cli_phase", 1000),
                             ("simulated_wake_serve_phase", 2000),
                             ("simulated_presence_scheduler_phase", 30000)):
        samples[label] = [int(period_ms * 1_000_000 * (i + 0.5) / args.samples)
                          for i in range(args.samples)]
    report = dict(schema="cairn.notification-latency-benchmark/1",
        revision=subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip(),
        parameters=dict(samples=args.samples, warmup=args.warmup,
            transport="new Unix client connection per API call; relay opens verified TLS upstream",
            workload="sequential one-recipient notices, one source version, idle disposable database",
            postgres="disposable local socket, fsync=on, synchronous_commit=on",
            presence_period_ms=30000, watch_cli_period_ms=1000,
            wake_serve_period_ms=2000,
            scheduler_fixture="evenly spaced midpoint phases, no OS or model scheduling"),
        measured={key: distribution(values) for key, values in samples.items()
                  if not key.startswith("simulated_")},
        simulated={key: distribution(values) for key, values in samples.items()
                   if key.startswith("simulated_")},
        not_measured=["actual provider/model wake", "cross-machine tailnet scheduling",
                      "concurrent producer load", "persistence-to-client commit instant"])
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--store-probe", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    assert 1 <= options.samples <= 200 and 0 <= options.warmup <= 50
    run(options)
