#!/usr/bin/env python3
"""Exercise the frozen lexical anchor through a failing disposable semantic worker.

The source and authenticated read are real Store operations. No model actor or
semantic scorer runs; the local worker exits before producing a ranking.
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
    dsn = f"host={root / 'socket'} dbname=cairn_panel_semantic_unavailable sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                text=True, capture_output=True, timeout=10)
        require(result.returncode == 0, result.stderr)
        return result.stdout.strip()

    require(sql("SELECT count(*) FROM cairn.memory_record") == "0",
            "semantic panel database is not fresh")
    fixture, fixture_hash = panel.read_fixture()
    cases = [row for row in fixture["cases"] if row["id"] == "semantic_unavailable"]
    require(len(cases) == 1 and cases[0]["setup"] == {
        "sources": ["lexical_anchor"], "events": [],
        "transitions": [{"phase": "before_search", "action": "disable_semantic",
                         "target": "lexical_anchor"}]},
        "semantic-unavailable fixture setup changed")
    source = fixture["sources"]["lexical_anchor"]
    require(source["project"] == "Cairn" and source["operation"] == "create"
            and source["record_class"] == "A" and source["kind"] == "procedure"
            and source["logical_version"] == 1 and source["claim_type"] == "self"
            and source["sensitivity"] == "shareable"
            and source["body"] == "Recovery code RAVEN-42: verify the persisted receipt before retrying."
            and source["scope"] == {"repo": "fixture:cairn-token-efficiency",
                                     "task_id": "*", "run_id": "*"},
            "lexical anchor source contract changed")
    home = root / "panel-semantic-unavailable-home"
    home.mkdir(mode=0o700)
    token = secrets.token_urlsafe(32)
    token_file = home / "agent.token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    principal = f"agent/panel-semantic-{uuid.uuid4()}"
    identities = home / "identities.json"
    identities.write_text(json.dumps([{
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "principal": principal, "repo": source["scope"]["repo"],
        "role": "agent", "destination": "hosted"}]), encoding="utf-8")
    identities.chmod(0o600)
    marker = home / "semantic-worker-invoked"
    worker = home / "failing-semantic-worker"
    worker.write_text("#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a', encoding='utf-8') as calls:\n"
        "    calls.write('failed\\n')\n"
        "raise SystemExit(7)\n", encoding="utf-8")
    worker.chmod(0o700)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve", "--semantic-command", str(worker)],
                              env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        socket = home / "api.sock"
        deadline = time.monotonic() + 10
        while not socket.exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable semantic API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK(socket.stat().st_mode), "API socket missing")

        def agent(operation, *args, payload=None):
            command = [str(binary), "agent", "--socket", str(socket),
                       "--token-file", str(token_file), operation, *args]
            result = subprocess.run(command,
                                    input=json.dumps(payload) if payload is not None else None,
                                    text=True, capture_output=True, env=env, timeout=20)
            require(len(result.stdout) < 1_000_000, "API response too large")
            envelope = json.loads(result.stdout, object_pairs_hook=panel.pairs_unique)
            require(result.returncode == 0 and envelope.get("status") == "OK"
                    and envelope.get("ok") is True,
                    f"{operation}: {envelope.get('status')} {result.stderr}")
            return envelope["data"]

        agent("version", payload={})
        draft = {key: source[key] for key in
                 ("kind", "body", "scope", "claim_type", "sensitivity")}
        stored = agent("create", payload={"request_id": str(uuid.uuid4()), "draft": draft})
        require(stored["record_id"] and stored["version"] == 1
                and stored["body"] == source["body"]
                and stored["observed_writer"] == principal,
                "Store-created source differs from frozen lexical anchor")
        ref = {"record_id": stored["record_id"], "version": 1}
        scope = source["scope"]
        flags = ["--repo", scope["repo"], "--task", "panel-semantic",
                 "--run", "panel-semantic", "--tokens", "64000"]
        semantic = agent("search", *flags, "--semantic", "RAVEN-42")
        worker_calls = marker.read_text(encoding="utf-8")
        require(worker_calls and set(worker_calls.splitlines()) == {"failed"},
                "failing local semantic worker was not invoked")
        require(semantic["schema"] == "cairn.agent-search/1"
                and semantic["status"] == "DEGRADED_NO_EMBEDDINGS"
                and semantic["discovery"]["state"] == "unavailable"
                and not semantic["discovery"].get("model_sha256")
                and not semantic["discovery"].get("algorithm")
                and semantic["ranking"] == "lexical-scope-recency/4"
                and semantic["destination"] == {"name": "hosted", "allow_local": False},
                "semantic failure did not report labelled lexical fallback")
        require(semantic.get("selected") == [] and len(semantic["index"]) == 1,
                "fallback selected unexpected authority or omitted lexical source")
        entry = semantic["index"][0]
        digest = hashlib.sha256(source["body"].encode()).hexdigest()
        require(entry["record_id"] == ref["record_id"] and entry["version"] == 1
                and entry["class"] == "A" and entry["body_sha256"] == digest
                and "similarity" not in entry and "semantic_score" not in entry,
                "fallback source identity, hash or score differs")
        expanded = agent("pull", payload=entry["pull_arguments"])
        record = expanded["selection"]["record"]
        require(record["record_id"] == ref["record_id"] and record["version"] == 1
                and record["body"] == source["body"]
                and expanded.get("span") is None
                and expanded.get("source_extent", "whole") == "whole",
                "fallback pull did not return the whole exact source")
        plain = agent("search", *flags, "RAVEN-42")
        require(plain["status"] == "READY" and len(plain["index"]) == 1
                and plain["index"][0]["record_id"] == ref["record_id"]
                and plain["index"][0]["body_sha256"] == digest
                and marker.read_text(encoding="utf-8") == worker_calls,
                "plain lexical comparison changed the source or invoked semantic work")
        require(sql("SELECT count(*) FROM cairn.memory_record") == "1",
                "semantic fallback created another source")
        print(json.dumps({"fixture_sha256": fixture_hash,
                          "case": "semantic_unavailable",
                          "worker": "private_command_failed",
                          "discovery": "unavailable",
                          "fallback": "labelled_lexical_whole_pull",
                          "required_context": "not_in_frozen_fixture",
                          "actor_task": "unrun", "provider_called": False,
                          "task_outcome": "unassessed"}, sort_keys=True))
    finally:
        if server.poll() is None:
            server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_semantic_unavailable.py BINARY")
    check(Path(sys.argv[1]).resolve())
