#!/usr/bin/env python3
"""Exercise one selected panel setup in this invocation's disposable database."""

import hashlib
from datetime import datetime, timedelta
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


def check(binary, workflow_id):
    require(workflow_id in ("baseline.single_notice", "baseline.actionable_response",
                            "baseline.duplicate_send", "baseline.small_request_result_reply",
                            "baseline.concurrent_receivers") or workflow_id in tuple(
                                f"{arm}.{case}" for arm in
                                ("baseline", "envelope", "batch", "combined")
                                for case in ("routine_burst_urgent_request",
                                             "wrong_project_memory", "corrected_note",
                                             "meaningful_owner_correction", "unfinished_handoff",
                                             "required_conflict_group", "semantic_unavailable",
                                             "source_forgotten_before_read")),
            "unsupported workflow")
    arm, case = workflow_id.split(".", 1)
    burst = case == "routine_burst_urgent_request"
    source_case = case in ("wrong_project_memory", "corrected_note",
                           "meaningful_owner_correction", "unfinished_handoff",
                           "required_conflict_group", "semantic_unavailable")
    per_arm_case = source_case or case == "source_forgotten_before_read"
    label = workflow_id.replace(".", "-") if burst or per_arm_case else case
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."), "fresh cluster root")
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "private cluster root")
    require((root / "data" / "PG_VERSION").is_file(), "cluster not initialized")
    require(binary == root / "cairn" and binary.is_file(), "disposable binary")
    database = f"cairn_panel_{arm}_{case}" if burst or per_arm_case else f"cairn_panel_{case}"
    dsn = f"host={root / 'socket'} dbname={database} sslmode=disable"
    require(os.environ["CAIRN_TEST_DATABASE_URL"] == os.environ["CAIRN_DATABASE_URL"] == dsn,
            "dedicated database")

    export_parent = root / f"panel-export-{label}"
    export_parent.mkdir(mode=0o700)
    panel.PRIVATE_PARENT = export_parent
    export = export_parent / "frozen"
    fixture, fixture_hash = panel.read_fixture()
    panel.export(fixture, fixture_hash, export)
    home = root / f"panel-controller-home-{label}"
    home.mkdir(mode=0o700)
    principals = {role: f"agent/panel-{role}-{uuid.uuid4()}"
                  for role in ("sender", "worker", "other", "stranger")}
    identities = []
    for role, principal in principals.items():
        token = secrets.token_urlsafe(32)
        token_path = home / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        identities.append({"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                           "principal": principal, "repo": "fixture:cairn-token-efficiency",
                           "role": "agent", "destination": "hosted"})
    config = home / "identities.json"
    config.write_text(json.dumps(identities), encoding="utf-8")
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                text=True, capture_output=True, env=env, timeout=10)
        require(result.returncode == 0, result.stderr)
        return result.stdout.strip()

    require(sql("SELECT count(*) FROM cairn.agent_event") == "0", "event table not fresh")
    require(sql("SELECT count(*) FROM cairn.agent_session") == "0", "session table not fresh")
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True)

    def cli(role, *args, body=None, session=None):
        connection = ["--socket", str(home / "api.sock"),
                      "--token-file", str(home / f"{role}.token")]
        if session is not None:
            connection += ["--agent-id", session["agent_id"],
                           "--execution-id", session["execution_id"]]
        result = subprocess.run([str(binary), args[0], *connection, *args[1:]],
                                input=body, text=True, capture_output=True, env=env, timeout=20)
        envelope = json.loads(result.stdout)
        require(result.returncode == 0 and envelope["status"] == "OK", (args, envelope, result.stderr))
        return envelope["data"]

    try:
        deadline = time.monotonic() + 10
        while not (home / "api.sock").exists():
            if server.poll() is not None:
                raise AssertionError(server.stderr.read())
            require(time.monotonic() < deadline, "panel API startup timed out")
            time.sleep(0.02)
        require(stat.S_ISSOCK((home / "api.sock").stat().st_mode), "API socket required")
        for role in principals:
            result = subprocess.run([str(binary), "agent", "--socket", str(home / "api.sock"),
                                     "--token-file", str(home / f"{role}.token"), "version"],
                                    text=True, capture_output=True, env=env, timeout=10)
            require(result.returncode == 0 and json.loads(result.stdout)["status"] == "OK",
                    "token authentication failed")

        controller = Path(__file__).with_name("token_efficiency_panel_controller.py")
        command = [sys.executable, str(controller), "--fixture", str(panel.FIXTURE),
                   "--export", str(export), "--workflow-id", workflow_id,
                   "--binary", str(binary), "--home", str(home),
                   "--sender-token", str(home / "sender.token"),
                   "--worker-token", str(home / "worker.token"),
                   "--sender-principal", principals["sender"],
                   "--run-dir", str(root / f"panel-run-{label}")]
        if case == "concurrent_receivers":
            command += ["--other-token", str(home / "other.token")]
        swapped = list(command)
        swapped[swapped.index("--worker-token") + 1] = str(home / "sender.token")
        rejected = subprocess.run(swapped, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "token identities must differ" in rejected.stderr,
                "same-token identity was accepted")
        require(not (root / f"panel-run-{label}").exists(), "same-token request mutated state")
        if case == "concurrent_receivers":
            swapped = list(command)
            swapped[swapped.index("--other-token") + 1] = str(home / "worker.token")
            rejected = subprocess.run(swapped, text=True, capture_output=True, env=env, timeout=20)
            require(rejected.returncode != 0 and "token identities must differ" in rejected.stderr,
                    "same-token second worker was accepted")
            require(not (root / f"panel-run-{label}").exists(),
                    "same-token second worker mutated state")
        actor_path = export / "actor" / f"{case}.json"
        actor_bytes = actor_path.read_bytes()
        actor = json.loads(actor_bytes)
        actor["task"] += " Changed."
        actor_path.write_text(json.dumps(actor), encoding="utf-8")
        rejected = subprocess.run(command, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "actor export differs" in rejected.stderr,
                "tampered actor was accepted")
        require(not (root / f"panel-run-{label}").exists(), "tampered actor mutated state")
        actor_path.write_bytes(actor_bytes)

        first = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
        require(first.returncode == 0, first.stderr)
        receipt = json.loads(first.stdout)
        require(receipt["schema"] == "cairn.panel-materialization/2", "receipt schema")
        require(receipt["workflow_id"] == workflow_id, "workflow identity")
        require(receipt["setup_harness"] == "codex", "synthetic session harness")
        require(receipt["fixture_sha256"] == fixture_hash, "fixture binding")
        require(receipt["provider_called"] is False and receipt["native_turn_join"] == "unimplemented",
                "no actor turn")
        require(receipt["task_outcome"] == "unassessed", "no task outcome")
        if case == "source_forgotten_before_read":
            loss = receipt.get("source_loss") or {}
            event = receipt["events"].get("request_missing_source") or {}
            fixture_event = next(row for row in fixture["cases"]
                                 if row["id"] == case)["setup"]["events"][0]
            intent = json.loads((root / f"panel-run-{label}" / "intent.json").read_text())
            require(loss.get("transition") == {
                "phase": "after_admission_before_read", "action": "remove_source",
                "target": "request_missing_source", "status": "verified"}
                and loss.get("source_ref") == event.get("source_ref")
                and loss.get("event_id") == event.get("event_id")
                and loss.get("delivery_id") == event.get("delivery_id")
                and loss.get("attempt_id") and loss.get("synthetic_turn_id") ==
                    intent["synthetic_turn_id"]
                and loss.get("claim_request_id") ==
                    intent["source_loss_request_ids"]["claim"]
                and loss.get("source_read_status") == "PAYLOAD_UNAVAILABLE"
                and loss.get("settlement") == "failed/source_unavailable"
                and loss.get("settlement_request_id") ==
                    intent["source_loss_request_ids"]["ack"]
                and loss.get("settlement_replay_identical") is True
                and loss.get("final_attempts") == 1
                and loss.get("result_ref") is None
                and loss.get("actor_task") == "unrun"
                and loss.get("requester_explanation") == "unrun"
                and loss.get("actor_acceptance") == "unassessed"
                and receipt["provider_called"] is False
                and receipt["native_turn_join"] == "unimplemented"
                and receipt["task_outcome"] == "unassessed",
                "authenticated source-loss transition or evidence limit differs")
            require(fixture_event["body"] not in json.dumps(receipt),
                    "source-loss receipt contains fixture body")
        if case == "concurrent_receivers":
            require(receipt["native_actor_concurrency"] == "unrun", "no concurrent actors")
        if burst:
            require(receipt["native_batch_transfer"] == "unrun", "batch transfer not run")
            snapshot = receipt["readiness_snapshot"]
            selected = receipt["events"]["urgent_1" if arm in ("batch", "combined")
                                         else "routine_1"]
            require(snapshot["basis"] == "selected_native_ready_hint"
                    and snapshot["event_id"] == selected["event_id"]
                    and snapshot["delivery_id"] == selected["delivery_id"],
                    "one-time native readiness snapshot")
            if arm in ("batch", "combined"):
                require(datetime.fromisoformat(snapshot["observed_at"])
                        < datetime.fromisoformat(snapshot["earliest_routine_available_at"]),
                        "readiness was captured after routine deadline")
            else:
                require(snapshot["earliest_routine_available_at"] is None,
                        "default arm has no deferral deadline")
            require(json.loads((root / f"panel-run-{label}" / "readiness.json").read_text())
                    == snapshot, "readiness receipt persisted")
        expected_sessions = ({"worker_a": "worker", "worker": "sender"}
                             if case == "actionable_response" else
                             {"worker": "worker", "sender": "sender"}
                             if case == "small_request_result_reply" else
                             {"worker_a": "worker", "worker_b": "other"}
                             if case == "concurrent_receivers" else {"worker": "worker"})
        require(set(receipt["sessions"]) == set(expected_sessions), "session roles")
        for label, token_role in expected_sessions.items():
            session = receipt["sessions"][label]
            agent_id = str(uuid.UUID(session["agent_id"]))
            require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{agent_id}'")
                    == session["observed_owner"] == principals[token_role], "session owner")
            require(session["inbox"] == f"agent/{agent_id}", "session inbox")
        require(principals["sender"] != principals["worker"], "separate profiles")
        if case == "concurrent_receivers":
            require(len({principals["sender"], receipt["sessions"]["worker_a"]["observed_owner"],
                         receipt["sessions"]["worker_b"]["observed_owner"]}) == 3,
                    "two distinct worker owners")
            require(receipt["sessions"]["worker_a"]["inbox"] !=
                    receipt["sessions"]["worker_b"]["inbox"], "two distinct worker inboxes")
        require(set(json.loads(actor_path.read_text())) == {"schema", "case", "task"},
                "actor export isolation")
        if burst and arm == "batch":
            deadline = datetime.fromisoformat(receipt["readiness_snapshot"]["earliest_routine_available_at"])
            now = datetime.fromisoformat(sql("SELECT clock_timestamp()"))
            time.sleep(max(0, (deadline - now).total_seconds()) + 0.2)
        if case == "source_forgotten_before_read":
            require(receipt.get("source_loss", {}).get("settlement_replay_identical") is True,
                    "one-way source forgetting case lacks exact settlement replay")
        else:
            second = subprocess.run(command, text=True, capture_output=True, env=env, timeout=30)
            require(second.returncode == 0 and json.loads(second.stdout) == receipt, second.stderr)
        changed = list(command)
        changed[changed.index("--worker-token") + 1] = str(
            home / ("stranger.token" if case == "concurrent_receivers" else "other.token"))
        rejected = subprocess.run(changed, text=True, capture_output=True, env=env, timeout=20)
        require(rejected.returncode != 0 and "prior intent differs" in rejected.stderr,
                "changed worker identity accepted on retry")
        require(sql("SELECT count(*) FROM cairn.agent_session") == str(len(expected_sessions)),
                "changed worker identity registered a session")
        if case == "source_forgotten_before_read":
            require(sql("SELECT count(*) FROM cairn.agent_event") == "1",
                    "source-loss retry changed durable event count")
            require(sql("SELECT count(*) FROM cairn.agent_session_attempt") == "1"
                    and sql("SELECT count(*) FROM cairn.agent_session_poll") == "1",
                    "source-loss acknowledgement replay duplicated native admission")
        if case == "concurrent_receivers":
            changed = list(command)
            changed[changed.index("--other-token") + 1] = str(home / "stranger.token")
            rejected = subprocess.run(changed, text=True, capture_output=True, env=env, timeout=20)
            require(rejected.returncode != 0 and "prior intent differs" in rejected.stderr,
                    "changed second worker identity accepted on retry")
            require(sql("SELECT count(*) FROM cairn.agent_session") == str(len(expected_sessions)),
                    "changed second worker identity registered a session")
        fixture_case = next(row for row in fixture["cases"] if row["id"] == case)
        require(set(receipt.get("sources", {})) == set(fixture_case["setup"]["sources"]),
                "source identities differ from frozen setup")
        for name in fixture_case["setup"]["sources"]:
            source = fixture["sources"][name]
            observed = receipt["sources"][name]
            require(observed["source_record_sha256"] == panel.sha(source)
                    and observed["source_body_sha256"] == hashlib.sha256(
                        source["body"].encode()).hexdigest()
                    and observed["version"] == source["logical_version"]
                    and observed["operation"] == source["operation"]
                    and (observed["observed_writer"] == f"local-uid:{os.geteuid()}"
                         if case == "required_conflict_group" else
                         observed["observed_writer"] == principals["sender"]),
                    "durable source receipt differs from fixture")
            ref = {"record_id": observed["record_id"], "version": observed["version"]}
            if case == "required_conflict_group":
                result = subprocess.run([str(binary), "history"], input=json.dumps(ref),
                                        text=True, capture_output=True, env=env, timeout=20)
                require(result.returncode == 0, "operator cannot inspect required source")
                exact = json.loads(result.stdout)["data"]
            else:
                exact = cli("sender", "agent", "history", body=json.dumps(ref))
            versions = exact["versions"]
            require(len(versions) == 1 and versions[0]["body"] == source["body"]
                    and versions[0]["version"] == observed["version"],
                    "exact Store history differs from frozen source")
        if case in ("corrected_note", "meaningful_owner_correction"):
            old_name, current_name = (("old_export", "export_correction")
                                      if case == "corrected_note" else ("owner_old", "owner_new"))
            require(receipt["sources"][old_name]["record_id"] ==
                    receipt["sources"][current_name]["record_id"],
                    "edit did not preserve Store record identity")
        if source_case:
            preparation = receipt.get("source_preparation")
            require(preparation == {
                "status": "source_lineage_and_history_verified",
                "corrections": ([{
                    "target": "owner_new", "record_id": receipt["sources"]["owner_new"]["record_id"],
                    "from_version": 1, "to_version": 2, "fixture_phase": "after_old_record",
                    "fixture_action": "apply_correction", "status": "source_setup_only",
                }] if case == "meaningful_owner_correction" else
                    [{"target": "export_correction",
                      "record_id": receipt["sources"]["export_correction"]["record_id"],
                      "from_version": 1, "to_version": 2, "fixture_phase": None,
                      "fixture_action": None, "status": "source_setup_only"}]
                    if case == "corrected_note" else []),
                "runtime_transition": "unrun",
            } | ({
                "unrun_transitions": [{"phase": "before_resume",
                                       "action": "pause_after_source_check",
                                       "target": "handoff_state", "status": "unrun"}],
                "actor_acceptance": "unassessed",
                } if case == "unfinished_handoff" else {
                    "unrun_transitions": [{"phase": "before_search",
                                           "action": "disable_semantic",
                                           "target": "lexical_anchor",
                                           "status": "unrun"}],
                    "semantic_fallback": "unrun",
                    "whole_source_pull": "unrun",
                    "actor_acceptance": "unassessed",
                } if case == "semantic_unavailable" else {}),
                "source preparation must not claim runtime transition or actor acceptance")
            require(receipt["task_outcome"] == "unassessed"
                    and receipt["provider_called"] is False
                    and receipt["native_turn_join"] == "unimplemented",
                    "source setup cannot establish actor acceptance")
            if case == "unfinished_handoff":
                read = receipt.get("handoff_read") or {}
                require(receipt.get("transitions") == []
                        and read == {
                            "reader_agent_id": receipt["sessions"]["worker"]["agent_id"],
                            "record_id": receipt["sources"]["handoff_state"]["record_id"],
                            "version": 1,
                            "index_body_sha256": hashlib.sha256(
                                fixture["sources"]["handoff_state"]["body"].encode()).hexdigest(),
                            "pull_extent": "whole", "history_versions": [1],
                            "observed_writer": principals["sender"],
                        }, "authenticated handoff source read or unrun boundary differs")
                require("Cairn handoff for" not in json.dumps(receipt),
                        "durable controller receipt contains handoff source text")
            if case == "required_conflict_group":
                require(receipt["conflict_read"] == {
                    "reader_agent_id": receipt["sessions"]["worker"]["agent_id"],
                    "index": "OPEN_CONFLICT", "search": "OPEN_CONFLICT",
                    "delivered_source": "none", "actor_decision": "unassessed"},
                    "required conflict was not refused for worker")
                require(all(fixture["sources"][name]["body"] not in json.dumps(receipt)
                            for name in ("conflict_24", "conflict_48")),
                        "controller receipt contains required source text")
            require(sql("SELECT count(*) FROM cairn.memory_record") ==
                    ("1" if case in ("corrected_note", "meaningful_owner_correction",
                                     "unfinished_handoff", "semantic_unavailable") else "2"),
                    "source-only Store record count differs")
        expected_publishers = ({"request_decision": "sender", "response_a": "worker"}
                               if case == "actionable_response" else
                               {event["id"]: "sender" for event in fixture_case["setup"]["events"]})
        require(set(receipt["events"]) == set(expected_publishers), "event IDs")
        last_position = 0
        for spec in fixture_case["setup"]["events"]:
            event = receipt["events"][spec["id"]]
            publisher = expected_publishers[spec["id"]]
            require(event["observed_writer"] == principals[publisher], "source writer")
            require(event["source_body_sha256"] == hashlib.sha256(spec["body"].encode()).hexdigest(),
                    "source body hash")
            expected_state = ("handled" if case == "small_request_result_reply" else
                              "failed" if case == "source_forgotten_before_read" else "pending")
            expected_attempts = (1 if case in ("small_request_result_reply",
                                                "source_forgotten_before_read") else 0)
            require(event["delivery_state"] == expected_state
                    and event["attempts"] == expected_attempts, "delivery state")
            require(event["recipient_inbox"] == receipt["sessions"][spec["recipient"]]["inbox"],
                    "recipient session")
            cause = (receipt["events"][spec["causation"]]["event_id"]
                     if "causation" in spec else "")
            require(event["causation_id"] == cause, "exact causal parent")
            publisher_session = (receipt["sessions"]["worker_a"]
                                 if case == "actionable_response" and spec["id"] == "response_a"
                                 else None)
            expected_publisher = (publisher_session["inbox"] if publisher_session
                                  else principals[publisher])
            status = cli(publisher, "event-status", event["event_id"],
                         session=publisher_session)
            require(status["event"]["ref"] == event["source_ref"], "event source ref")
            require(status["event"]["from"] == event["event_publisher"]
                    == expected_publisher, "event publisher")
            require(status["event"].get("causation_id", "") == cause, "native causation")
            require(len(status["deliveries"]) == 1
                    and status["deliveries"][0]["delivery_id"] == event["delivery_id"]
                    and status["deliveries"][0]["state"] == expected_state
                    and status["deliveries"][0]["attempts"] == expected_attempts,
                    "exact delivery")
            if burst:
                require(type(event["position"]) is int and event["position"] > last_position,
                        "fixture publication order")
                last_position = event["position"]
                policy = ({"owner": principals["sender"], "version": 1,
                           "urgency": "routine", "defer_seconds": 30}
                          if arm in ("batch", "combined") and spec["kind"] == "notice"
                          else None)
                require(event["notice_policy"] == status["event"].get("notice_policy") == policy,
                        "publisher-owned notice policy")
                available = status["deliveries"][0].get("available_at")
                require(event["available_at"] == available, "stable native availability")
                if policy is None:
                    require(available is None, "default or urgent event is immediate")
                else:
                    require(datetime.fromisoformat(available.replace("Z", "+00:00")) ==
                            datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
                            + timedelta(seconds=30), "fixed routine availability deadline")
                require("task_deadline" not in status["event"], "fixture has no task deadline")
        if case == "concurrent_receivers":
            isolation = receipt["recipient_isolation"]
            require(isolation["basis"] == "native_readiness_and_cross_session_refusal"
                    and isolation["worker_a_to_worker_b_status"] == "NOT_FOUND",
                    "native isolation basis")
            for recipient, event_id, token_role in (("worker_a", "request_a", "worker"),
                                                    ("worker_b", "request_b", "other")):
                session = receipt["sessions"][recipient]
                event = receipt["events"][event_id]
                expected = {"session_agent_id": session["agent_id"],
                            "event_id": event["event_id"], "delivery_id": event["delivery_id"]}
                require(isolation[recipient] == expected, "receipt readiness identity")
                result = subprocess.run(
                    [str(binary), "agent", "--socket", str(home / "api.sock"),
                     "--token-file", str(home / f"{token_role}.token"), "session-inbox-ready"],
                    input=json.dumps({"agent_id": session["agent_id"],
                                      "execution_id": session["execution_id"]}),
                    text=True, capture_output=True, env=env, timeout=20)
                envelope = json.loads(result.stdout)
                require(result.returncode == 0 and envelope["status"] == "OK"
                        and envelope["data"] == {"event_id": event["event_id"],
                                                 "delivery_id": event["delivery_id"]},
                        "exact native recipient readiness")
            b_session = receipt["sessions"]["worker_b"]
            b_event = receipt["events"]["request_b"]
            result = subprocess.run(
                [str(binary), "agent", "--socket", str(home / "api.sock"),
                 "--token-file", str(home / "worker.token"), "session-inbox-ready"],
                input=json.dumps({"agent_id": b_session["agent_id"],
                                  "execution_id": b_session["execution_id"]}),
                text=True, capture_output=True, env=env, timeout=20)
            envelope = json.loads(result.stdout)
            require(result.returncode != 0 and envelope["schema"] == "cairn.response/1"
                    and envelope["ok"] is False and envelope["status"] == "NOT_FOUND",
                    "A profile must be refused on exact B session")
            b_status = cli("sender", "event-status", b_event["event_id"])
            require(len(b_status["deliveries"]) == 1 and
                    b_status["deliveries"][0]["delivery_id"] == b_event["delivery_id"] and
                    b_status["deliveries"][0]["consumer"] == b_session["inbox"] and
                    b_status["deliveries"][0]["state"] == "pending" and
                    b_status["deliveries"][0]["attempts"] == 0 and
                    isolation["worker_b_after_refusal"] ==
                    {"delivery_id": b_event["delivery_id"], "state": "pending", "attempts": 0},
                    "B delivery unchanged by A refusal")
            require(sql("SELECT count(*) FROM cairn.agent_session_attempt") == "0",
                    "setup must not claim either session")
        sender_stats = cli("sender", "event-stats")
        expected_events = len(fixture_case["setup"]["events"])
        if case == "small_request_result_reply":
            expected_events += 1
        require(sender_stats["published"] == (4 if burst else 2 if case == "concurrent_receivers"
                                               else 0 if source_case else 1), sender_stats)
        require(sql("SELECT count(*) FROM cairn.agent_event") == str(expected_events),
                "unexpected event count")
        require(sql("SELECT count(*) FROM cairn.agent_delivery") == str(expected_events),
                "unexpected delivery count")
        if case == "small_request_result_reply":
            settlement = receipt["synthetic_store_settlement"]
            request = receipt["events"]["request_hash"]
            worker = receipt["sessions"]["worker"]
            sender = receipt["sessions"]["sender"]
            digest = hashlib.sha256(b"cedar").hexdigest()
            require(digest == "0c274639936717793d7695ef0373039e397acdabc247d9953d3c9209a95044cd",
                    "fixture digest")
            require(settlement["schema"] == "cairn.panel-synthetic-settlement/1"
                    and settlement["synthetic_store_settlement"] is True
                    and settlement["actor_result"] == "unassessed"
                    and settlement["turn_exclusive"] is False
                    and settlement["attempt_reconciled"] is False,
                    "synthetic Store boundary")
            require(settlement["claim_attempt_id"] == settlement["claim_request_id"]
                    and settlement["synthetic_turn_id"] == f'panel-synthetic/{receipt["run_id"]}'
                    and settlement["request_delivery_id"] == request["delivery_id"]
                    and settlement["request_event_id"] == request["event_id"],
                    "stable synthetic claim")
            require(settlement["result_body_sha256"] == hashlib.sha256(digest.encode()).hexdigest()
                    and settlement["result_observed_writer"] == worker["inbox"],
                    "synthetic result identity")
            require(settlement["response_causation_id"] == request["event_id"]
                    and settlement["request_state"] == "handled"
                    and settlement["response_state"] == "pending", "settlement and reply states")
            reply_status = cli("worker", "event-status", settlement["response_event_id"],
                               session=worker)
            require(reply_status["event"]["ref"] == settlement["result_ref"]
                    and reply_status["event"]["causation_id"] == request["event_id"]
                    and reply_status["event"]["from"] == worker["inbox"]
                    and reply_status["event"]["destination"]["name"] == sender["inbox"]
                    and len(reply_status["deliveries"]) == 1
                    and reply_status["deliveries"][0]["delivery_id"] ==
                    settlement["response_delivery_id"], "durable exact reply")
            require(sql("SELECT count(*) FROM cairn.memory_record") == "2", "duplicate result source")
            require(sql("SELECT count(*) FROM cairn.agent_session_attempt") == "1", "duplicate session claim")
            require(sql("SELECT count(*) FROM cairn.agent_session_poll") == "1", "duplicate claim journal")
            require(sql("SELECT count(*) FROM cairn.agent_delivery WHERE state='handled'") == "1",
                    "request completion count")
            changed_result = subprocess.run(
                [str(binary), "complete", "--socket", str(home / "api.sock"),
                 "--token-file", str(home / "worker.token"),
                 "--agent-id", worker["agent_id"], "--execution-id", worker["execution_id"],
                 "--request-id", settlement["completion_request_id"],
                 "--lease", settlement["claim_lease_id"], "--shareable", "--stdin",
                 "--task", "coordination-result/" + request["delivery_id"],
                 "--run", "*", request["delivery_id"]],
                input=digest + "\n", text=True, capture_output=True, env=env, timeout=20)
            changed_envelope = json.loads(changed_result.stdout)
            require(changed_result.returncode != 0
                    and changed_envelope["status"] == "IDEMPOTENCY_CONFLICT",
                    "changed result bytes reused completion identity")
            require(sql("SELECT count(*) FROM cairn.memory_record") == "2",
                    "changed result created a second source")
        if case == "duplicate_send":
            require(len(receipt["transitions"]) == 1, "missing duplicate transition")
            transition = receipt["transitions"][0]
            event = receipt["events"]["duplicate_a"]
            expected_request = str(uuid.uuid5(uuid.UUID(receipt["run_id"]), "duplicate_send/ck8"))
            require(transition["publication_request_id"] == event["publication_request_id"]
                    == expected_request
                    and transition["first_event_id"] == transition["retry_event_id"]
                    == event["event_id"] and transition["delivery_id"] == event["delivery_id"],
                    "same native publication identity")
        elif case == "source_forgotten_before_read":
            require(receipt["transitions"] == [receipt["source_loss"]["transition"]],
                    "source-loss transition identity differs")
        else:
            require(receipt["transitions"] == [], "unexpected transition")
        print(json.dumps({"workflow_id": workflow_id, "events": expected_events,
                          "provider_called": False}, sort_keys=True))
    finally:
        server.terminate()
        server.communicate(timeout=10)
        require(server.returncode == 0, "API server did not stop cleanly")


if __name__ == "__main__":
    check(Path(sys.argv[1]), sys.argv[2])
