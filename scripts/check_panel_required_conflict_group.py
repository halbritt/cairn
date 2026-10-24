#!/usr/bin/env python3
"""Exercise the frozen required conflict through the authenticated local API.

The two instructions and agent profile belong to a disposable database. No
actor is selected and no provider is called.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
import uuid

import preflight_token_efficiency_panel as panel


def require(value, message):
    if not value:
        raise AssertionError(message)


def invoke(binary, env, command, body=None, *, socket=None, token=None,
           expected="OK", forbidden=()):
    argv = [str(binary)]
    if token is not None:
        argv += ["agent", "--socket", str(socket), "--token-file", str(token), command]
    else:
        argv.append(command)
    process = subprocess.run(argv, input=json.dumps(body) if body is not None else None,
                             text=True, capture_output=True, env=env, timeout=20)
    require(len(process.stdout) < 1_000_000 and len(process.stderr) < 100_000,
            "API response exceeds bound")
    require(all(value not in process.stdout and value not in process.stderr
                for value in forbidden), "conflicting source body disclosed")
    envelope = json.loads(process.stdout, object_pairs_hook=panel.pairs_unique)
    require(envelope.get("schema") == "cairn.response/1"
            and envelope.get("status") == expected
            and envelope.get("ok") is (expected == "OK")
            and (process.returncode == 0) is (expected == "OK"),
            f"{command} returned {envelope.get('status')}, expected {expected}")
    if expected != "OK":
        require(envelope.get("data") is None, "policy refusal included partial data")
    return envelope.get("data")


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_required_conflict_group sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                             "SELECT count(*) FROM cairn.memory_record"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == "0", "database not fresh")

    fixture, fixture_hash = panel.read_fixture()
    rows = [row for row in fixture["cases"] if row["id"] == "required_conflict_group"]
    require(len(rows) == 1 and rows[0]["setup"] == {
        "sources": ["conflict_24", "conflict_48"], "events": [], "transitions": []},
        "required conflict fixture changed")
    names = rows[0]["setup"]["sources"]
    sources = [fixture["sources"][name] for name in names]
    require(all(source["kind"] == "instruction" and source["record_class"] == "C"
                and source["operation"] == "issue" and source["claim_type"] == "self"
                and source["sensitivity"] == "shareable"
                and source["authority"] == {
                    "grant": "disposable_operator_root", "mandatory": True,
                    "requires_runtime": False, "category": "workflow",
                    "policy_key": "synthetic-evacuation-threshold"}
                for source in sources)
            and sources[0]["conflicts_with"] == [names[1]]
            and sources[1]["conflicts_with"] == [names[0]]
            and sources[0]["scope"] == sources[1]["scope"],
            "two mandatory conflicting source contracts changed")
    bodies = tuple(source["body"] for source in sources)
    forbidden_claims = (*bodies, "24 hours", "48 hours", "24-hour", "48-hour")
    repo = sources[0]["scope"]["repo"]
    home = root / "panel-required-conflict-home"
    home.mkdir(mode=0o700)
    token = secrets.token_urlsafe(32)
    token_file = home / "agent.token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    (home / "identities.json").write_text(json.dumps([{
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "principal": f"agent/panel-conflict-{uuid.uuid4()}",
        "repo": repo, "role": "agent", "destination": "hosted"}]), encoding="utf-8")
    (home / "identities.json").chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode),
                "local API socket missing")
        invoke(binary, env, "version", {}, socket=home / "api.sock", token=token_file)
        grant = invoke(binary, env, "bootstrap", {
            "request_id": str(uuid.uuid4()),
            "reason": "Authorize frozen disposable conflict panel sources"})
        refs = []
        for source in sources:
            issued = invoke(binary, env, "issue", {
                "request_id": str(uuid.uuid4()), "grant_id": grant["grant_id"],
                "draft": {key: source[key] for key in
                          ("kind", "body", "scope", "claim_type", "sensitivity")},
                "mandatory": source["authority"]["mandatory"],
                "requires_runtime": source["authority"]["requires_runtime"],
                "category": source["authority"]["category"],
                "policy_key": source["authority"]["policy_key"],
                "reason": "Materialize frozen paired conflict source"})
            require(issued["class"] == "C" and issued["version"] == 1
                    and issued["body"] == source["body"],
                    "issued instruction differs from fixture")
            ref = {"record_id": issued["record_id"], "version": issued["version"]}
            history = invoke(binary, env, "history", ref)
            versions = history.get("versions") or []
            require(len(versions) == 1 and versions[0]["body"] == source["body"],
                    "operator cannot inspect whole exact instruction")
            refs.append(ref)
        require(refs[0]["record_id"] != refs[1]["record_id"],
                "conflict sources collapsed into one record")

        scope = {"repo": repo, "task_id": "panel-conflict", "run_id": "panel-conflict"}
        request = {"request_id": str(uuid.uuid4()), "scope": scope,
                   "query": "synthetic evacuation threshold", "purpose": "context",
                   "available_tokens": 32000, "context": {}}
        invoke(binary, env, "index", request, socket=home / "api.sock",
               token=token_file, expected="OPEN_CONFLICT", forbidden=forbidden_claims)
        search = subprocess.run(
            [str(binary), "agent", "--socket", str(home / "api.sock"),
             "--token-file", str(token_file), "search", "--repo", repo,
             "--task", scope["task_id"], "--run", scope["run_id"],
             "--request-id", str(uuid.uuid4()), "synthetic evacuation threshold"],
            text=True, capture_output=True, env=env, timeout=20)
        require(all(claim not in search.stdout and claim not in search.stderr
                    for claim in forbidden_claims),
                "search disclosed a conflicting source claim")
        response = json.loads(search.stdout, object_pairs_hook=panel.pairs_unique)
        data = response.get("data")
        require(search.returncode != 0 and response.get("schema") == "cairn.response/1"
                and response.get("ok") is False and response.get("status") == "OPEN_CONFLICT"
                and isinstance(data, dict) and data.get("selected") is None
                and not data.get("index"),
                f"CLI search did not fail closed on exact conflict: exit={search.returncode}, "
                f"status={response.get('status')}, data_status={data.get('status') if isinstance(data, dict) else None}, "
                f"selected_type={type(data.get('selected')).__name__ if isinstance(data, dict) else None}, "
                f"index_type={type(data.get('index')).__name__ if isinstance(data, dict) else None}")
        print(json.dumps({"case": "required_conflict_group", "fixture_sha256": fixture_hash,
                          "issued_source_refs": refs, "operator_whole_history": True,
                          "agent_index": "OPEN_CONFLICT", "agent_search": "OPEN_CONFLICT",
                          "agent_body_delivery": "none", "actor_task": "unrun",
                          "provider_called": False, "task_outcome": "unassessed"},
                         sort_keys=True))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_required_conflict_group.py BINARY")
    check(Path(sys.argv[1]))
