"""Exercise actual historical/candidate clients, relays and APIs on disposable DBs.

The shell runner owns the immutable baseline checkout and PostgreSQL lifecycle.
This probe never enrolls machines or loads an installed credential. Expectations
are explicit input while the compatibility policy is developed; the acceptance
invocation must use the reviewed policy fixture rather than inferred outcomes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid

from check_multi_machine import certificate, start_service, stop_service, unused_port


def invoke(binary, home, operation, request, *, token_file, expected="OK"):
    env = dict(os.environ, CAIRN_HOME=str(home),
               CAIRN_DATABASE_URL="host=/nonexistent-api-skew-db dbname=denied")
    env.pop("CAIRN_TEST_DATABASE_URL", None)
    command = [str(binary), "agent", "--socket", str(home / "api.sock"),
               "--token-file", str(token_file), operation]
    result = subprocess.run(command, input=json.dumps(request), text=True,
                            capture_output=True, timeout=40, env=env)
    try:
        reply = json.loads(result.stdout)
    except ValueError as exc:
        raise AssertionError(f"{operation}: no JSON envelope: {result.stderr}") from exc
    assert reply.get("schema") == "cairn.response/1", reply
    assert reply.get("status") == expected, (operation, expected, reply, result.stderr)
    assert reply.get("ok") is (expected == "OK"), reply
    assert (result.returncode == 0) is (expected == "OK"), reply
    return reply.get("data")


def write_private(path, body):
    with path.open("x") as stream:
        stream.write(body)
    path.chmod(0o600)


def exercise(binary, home, repo, token_file, expected):
    request = dict(request_id=str(uuid.uuid4()), draft=dict(
        kind="note", body="Historical API compatibility fixture", claim_type="self",
        sensitivity="shareable", scope=dict(repo=repo, task_id="*", run_id="*")))
    first = invoke(binary, home, "create", request, token_file=token_file, expected=expected)
    if expected != "OK":
        return dict(expected=expected, supported=False, request_id=request["request_id"])
    assert first["observed_writer"] == "machine:skew/agent", first
    assert first["witness"] == "testimony", first
    assert first["version"] == 1, first
    replay = invoke(binary, home, "create", request, token_file=token_file)
    assert replay == first, (first, replay)
    changed = dict(request, draft=dict(request["draft"], body="Changed intent"))
    invoke(binary, home, "create", changed, token_file=token_file,
           expected="IDEMPOTENCY_CONFLICT")
    update = dict(request_id=str(uuid.uuid4()), repo=repo, record_id=first["record_id"],
                  expected_version=1, body="; appended exactly once")
    revised = invoke(binary, home, "append", update, token_file=token_file)
    assert revised["version"] == 2, revised
    assert invoke(binary, home, "append", update, token_file=token_file) == revised
    current = invoke(binary, home, "get", dict(record_id=first["record_id"]),
                     token_file=token_file)
    assert current["version"] == 2, current
    assert current["body"] == request["draft"]["body"] + update["body"], current
    return dict(expected=expected, supported=True, request_id=request["request_id"],
                record_id=first["record_id"], final_version=current["version"],
                same_request_replayed=True, changed_intent_refused=True)


def check(args):
    root = args.root.resolve()
    assert str(root) == os.environ.get("CAIRN_DISPOSABLE_TEST_ROOT"), "runner-owned root required"
    assert root.parent == Path("/tmp") and root.name.startswith("cairn-api-skew."), root
    assert (root / "data/postmaster.pid").is_file(), "disposable cluster is not running"
    expected = json.loads(args.expectations.read_text())
    peers = {"legacy": args.legacy.resolve(), "candidate": args.candidate.resolve()}
    cases = {f"{client}/{relay}/{server}" for client in peers for relay in peers for server in peers}
    assert set(expected) == cases, "expectations must explicitly cover exactly the eight peer combinations"
    assert all(isinstance(status, str) and status for status in expected.values()), expected
    versions = {}
    for name, binary in peers.items():
        result = subprocess.run([str(binary), "version"], capture_output=True,
                                text=True, check=True, timeout=10)
        versions[name] = json.loads(result.stdout)["data"]
    assert versions["legacy"]["vcs_revision"] == "0fb09c3c6698d96ae62921d546e3263000e59732"
    assert versions["legacy"]["vcs_modified"] is False
    cert, key = certificate(root)
    rows = []
    for server_name, server_binary in peers.items():
        home = root / ("server-" + server_name)
        home.mkdir(mode=0o700)
        token = secrets.token_urlsafe(32)
        repo = "fixture:api-skew-" + server_name
        identity = dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                        principal="machine:skew/agent", repo=repo, role="agent",
                        destination="hosted", remote=True, machine_id="skew")
        write_private(home / "identities.json", json.dumps([identity]))
        write_private(home / "agent.token", token)
        dsn = f"host={root / 'socket'} dbname=cairn_{server_name} sslmode=disable"
        env = dict(os.environ, CAIRN_HOME=str(home), CAIRN_DATABASE_URL=dsn,
                   CAIRN_TEST_DATABASE_URL=dsn)
        subprocess.run([str(server_binary), "migrate"], env=env, check=True,
                       capture_output=True, timeout=30)
        port = unused_port()
        server = start_service([str(server_binary), "serve", "--listen", f"127.0.0.1:{port}",
                                "--tls-cert", str(cert), "--tls-key", str(key)],
                               home / "api.sock", env, server_name)
        try:
            for relay_name, relay_binary in peers.items():
                relay_home = root / (server_name + "-" + relay_name)
                relay_home.mkdir(mode=0o700)
                relay_env = dict(os.environ, CAIRN_HOME=str(relay_home), SSL_CERT_FILE=str(cert),
                                 CAIRN_DATABASE_URL="host=/nonexistent-api-skew-db dbname=denied")
                relay_env.pop("CAIRN_TEST_DATABASE_URL", None)
                relay = start_service([str(relay_binary), "relay", "--upstream", f"https://127.0.0.1:{port}"],
                                      relay_home / "api.sock", relay_env, relay_name)
                try:
                    for client_name, client_binary in peers.items():
                        case = f"{client_name}/{relay_name}/{server_name}"
                        outcome = exercise(client_binary, relay_home, repo, home / "agent.token",
                                           expected[case])
                        # A declared incompatibility must refuse before a write.
                        # Inspect only this disposable DB and this fixture UUID.
                        count = subprocess.run(
                            [str(Path(os.environ["CAIRN_PG_BIN"]) / "psql"), dsn, "-X", "-A", "-t",
                             "-v", "ON_ERROR_STOP=1", "-c",
                             "SELECT count(*) FROM cairn.mutation_request WHERE request_id='" +
                             outcome["request_id"] + "'::uuid"],
                            capture_output=True, text=True, check=True, timeout=10)
                        outcome["request_rows"] = int(count.stdout.strip())
                        assert outcome["request_rows"] == (1 if outcome["supported"] else 0), outcome
                        rows.append(dict(case=case, **outcome))
                finally:
                    stop_service(relay)
        finally:
            stop_service(server)
    report = dict(schema="cairn.api-skew-check/1", builds=versions, cases=rows)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, default=Path(__file__).resolve().parents[1] /
                        "fixtures/api-compatibility/legacy-v1-matrix.json")
    parser.add_argument("--output", type=Path)
    check(parser.parse_args())
