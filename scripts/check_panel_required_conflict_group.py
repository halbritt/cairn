#!/usr/bin/env python3
"""Exercise the frozen required conflict through the authenticated local API.

The two instructions and agent profile belong to a disposable database. No
actor is selected and no provider is called.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import disposable_host

import preflight_token_efficiency_panel as panel


def require(value, message):
    if not value:
        raise AssertionError(message)


def invoke(binary, env, command, body=None, *, socket=None, token=None,
           expected="OK", forbidden=()):
    argv = [binary]
    if token is not None:
        argv += ["agent", "--socket", str(socket), "--token-file", str(token), command]
    else:
        argv.append(command)
    result = disposable_host.run_envelope(argv, body, env=env, expected=expected, timeout=20,
                                          forbidden=forbidden, label=command)
    if expected != "OK":
        require(result.get("data") is None, "policy refusal included partial data")
        return None
    return result


def check(binary):
    cluster = disposable_host.ProvidedCluster.from_environment()
    root = cluster.root

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
    host = disposable_host.DisposableHost.start(
        cluster, binary=binary, repo=repo, database="cairn_panel_required_conflict_group",
        home=root / "panel-required-conflict-home", roles=("agent",),
        principals={"agent": f"agent/panel-conflict-{uuid.uuid4()}"})
    home, env, token_file = host.home, host.env, host.token("agent")
    try:
        host.require_fresh("memory_record", message="database not fresh")
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
        host.close()


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_panel_required_conflict_group.py BINARY")
    check(Path(sys.argv[1]))
