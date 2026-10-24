#!/usr/bin/env python3
"""Check frozen memory case currentness through a disposable authenticated API."""

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


def call(binary, env, socket, token, operation, *, payload=None, args=(),
         expected="OK"):
    command = [str(binary), "agent", "--socket", str(socket),
               "--token-file", str(token), operation, *args]
    result = subprocess.run(command,
                            input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, env=env, timeout=20)
    require(len(result.stdout) < 1_000_000 and len(result.stderr) < 100_000,
            f"{operation}: response exceeds bound")
    envelope = json.loads(result.stdout, object_pairs_hook=panel.pairs_unique)
    require(envelope.get("schema") == "cairn.response/1"
            and envelope.get("status") == expected
            and envelope.get("ok") is (expected == "OK")
            and (result.returncode == 0) is (expected == "OK"),
            f"{operation}: got {envelope.get('status')}, expected {expected}")
    return envelope.get("data")


def fixture_case(fixture, case_id, names, transitions):
    rows = [row for row in fixture["cases"] if row["id"] == case_id]
    require(len(rows) == 1 and rows[0]["setup"] == {
        "sources": names, "events": [], "transitions": transitions},
        f"{case_id}: frozen setup changed")
    return [fixture["sources"][name] for name in names]


def draft(source):
    return {key: source[key] for key in
            ("kind", "body", "scope", "claim_type", "sensitivity")}


def source_contract(source, *, kind, operation, version, project):
    require(source["record_class"] == "A" and source["kind"] == kind
            and source["operation"] == operation
            and source["logical_version"] == version
            and source["project"] == project
            and source["claim_type"] == "self"
            and source["sensitivity"] == "shareable"
            and source["scope"] == {"repo": "fixture:cairn-token-efficiency",
                                    "task_id": "*", "run_id": "*"},
            "frozen A-source contract changed")


def entry_for(search, record, source, *, version):
    require(search["schema"] == "cairn.agent-search/1"
            and search["status"] == "READY"
            and search["destination"] == {"name": "hosted", "allow_local": False}
            and search["selected"] == [], "search authority or destination changed")
    entries = [row for row in search["index"]
               if row["record_id"] == record["record_id"]]
    require(len(entries) == 1, "record missing or duplicated in current index")
    entry = entries[0]
    require(entry["version"] == version and entry["class"] == "A"
            and entry["body_sha256"] == hashlib.sha256(
                source["body"].encode("utf-8")).hexdigest(),
            "indexed version or source digest differs")
    return entry


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_panel_memory_lifecycle sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")
    count = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                            "SELECT count(*) FROM cairn.memory_record"],
                           text=True, capture_output=True, timeout=10)
    require(count.returncode == 0 and count.stdout.strip() == "0",
            "memory panel database is not fresh")

    fixture, fixture_hash = panel.read_fixture()
    trail, cairn = fixture_case(fixture, "wrong_project_memory",
                                ["trail_atlas", "cairn_atlas"], [])
    old_export, new_export = fixture_case(fixture, "corrected_note",
                                          ["old_export", "export_correction"], [])
    old_owner, new_owner = fixture_case(
        fixture, "meaningful_owner_correction", ["owner_old", "owner_new"],
        [{"phase": "after_old_record", "action": "apply_correction",
          "target": "owner_new"}])
    for source, kind, operation, version, project in (
        (trail, "note", "create", 1, "Trail"),
        (cairn, "note", "create", 1, "Cairn"),
        (old_export, "procedure", "create", 1, "Cairn"),
        (new_export, "procedure", "edit", 2, "Cairn"),
        (old_owner, "preference", "create", 1, "Cairn"),
        (new_owner, "preference", "edit", 2, "Cairn"),
    ):
        source_contract(source, kind=kind, operation=operation,
                        version=version, project=project)
    require(new_export["supersedes"] == ["old_export"]
            and old_export["record_family"] == new_export["record_family"]
            and new_owner["supersedes"] == ["owner_old"]
            and old_owner["record_family"] == new_owner["record_family"],
            "frozen correction lineage changed")
    require("Cairn Atlas search hints are retained for 30 days." == cairn["body"]
            and "Trail Atlas navigation map tiles are retained for 90 days." == trail["body"]
            and "POST /v1/export" in old_export["body"]
            and "POST /v2/export" in new_export["body"]
            and "summarize every routine notice immediately" in old_owner["body"]
            and "defer routine notices until the active task ends" in new_owner["body"],
            "panel source values no longer cover the frozen distinction")

    home = root / "panel-memory-lifecycle-home"
    home.mkdir(mode=0o700)
    token_value = secrets.token_urlsafe(32)
    token = home / "agent.token"
    token.write_text(token_value, encoding="utf-8")
    token.chmod(0o600)
    principal = f"agent/panel-memory-{uuid.uuid4()}"
    identities = home / "identities.json"
    identities.write_text(json.dumps([{
        "token_sha256": hashlib.sha256(token_value.encode()).hexdigest(),
        "principal": principal, "repo": trail["scope"]["repo"],
        "role": "agent", "destination": "hosted",
    }]), encoding="utf-8")
    identities.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        socket = home / "api.sock"
        deadline = time.monotonic() + 10
        while not socket.exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK(socket.stat().st_mode), "local API socket missing")

        def agent(operation, *, payload=None, args=(), expected="OK"):
            return call(binary, env, socket, token, operation, payload=payload,
                        args=args, expected=expected)

        def create(source):
            record = agent("create", payload={
                "request_id": str(uuid.uuid4()), "draft": draft(source)})
            require(record["version"] == 1 and record["body"] == source["body"]
                    and record["observed_writer"] == principal,
                    "created source differs from fixture or writer")
            return record

        def search(query):
            return agent("search", args=(
                "--repo", trail["scope"]["repo"], "--task", "panel-memory",
                "--run", "panel-memory", "--tokens", "64000", query))

        def pull(entry, record, source, version):
            expanded = agent("pull", payload=entry["pull_arguments"])
            selected = expanded["selection"]["record"]
            require(selected["record_id"] == record["record_id"]
                    and selected["version"] == version
                    and selected["body"] == source["body"]
                    and expanded.get("span") is None
                    and expanded.get("source_extent", "whole") == "whole",
                    "exact whole-source pull changed")
            return expanded

        agent("version", payload={})
        trail_record, cairn_record = create(trail), create(cairn)
        project_search = search("Cairn Atlas")
        trail_entry = entry_for(project_search, trail_record, trail, version=1)
        cairn_entry = entry_for(project_search, cairn_record, cairn, version=1)
        require(project_search["index"].index(cairn_entry)
                < project_search["index"].index(trail_entry),
                "Cairn source did not rank before Trail source")
        pull(cairn_entry, cairn_record, cairn, 1)
        pull(trail_entry, trail_record, trail, 1)

        correction_results = {}
        for case_id, old, current, old_query, current_query in (
            ("corrected_note", old_export, new_export,
             "POST /v1/export", "POST /v2/export"),
            ("meaningful_owner_correction", old_owner, new_owner,
             "routine notice immediately", "routine notices urgent requests"),
        ):
            first = create(old)
            before = search(old_query)
            old_entry = entry_for(before, first, old, version=1)
            pull(old_entry, first, old, 1)
            second = agent("edit", payload={
                "request_id": str(uuid.uuid4()), "record_id": first["record_id"],
                "expected_version": 1, "draft": draft(current)})
            require(second["record_id"] == first["record_id"]
                    and second["version"] == 2
                    and second["body"] == current["body"]
                    and second["observed_writer"] == principal,
                    f"{case_id}: edit did not retain record identity")
            agent("pull", payload=old_entry["pull_arguments"],
                  expected="STALE_HANDLE")
            after = search(current_query)
            current_entry = entry_for(after, second, current, version=2)
            pull(current_entry, second, current, 2)
            history = agent("history", payload={
                "record_id": first["record_id"], "version": 1})
            versions = history["versions"]
            require(history["current_version"] == 2 and len(versions) == 1
                    and versions[0]["version"] == 1
                    and versions[0]["body"] == old["body"],
                    f"{case_id}: old wording missing from exact history")
            correction_results[case_id] = {
                "old_pull_after_edit": "STALE_HANDLE",
                "current_index_version": 2, "old_history_version": 1}

        require(count_records(dsn) == 4, "unexpected durable record count")
        print(json.dumps({
            "fixture_sha256": fixture_hash,
            "wrong_project_memory": {
                "cairn_ranked_before_trail": True,
                "both_whole_sources_available": True,
                "project_decision": "actor_unrun"},
            **correction_results,
            "mandatory_C": "not_in_these_frozen_cases",
            "actor_task": "unrun", "provider_called": False,
            "task_outcome": "unassessed",
        }, sort_keys=True))
    finally:
        if server.poll() is None:
            server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def count_records(dsn):
    result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c",
                             "SELECT count(*) FROM cairn.memory_record"],
                            text=True, capture_output=True, timeout=10)
    require(result.returncode == 0, "record count query failed")
    return int(result.stdout.strip())


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_memory_lifecycle.py BINARY")
    check(Path(sys.argv[1]).resolve())
