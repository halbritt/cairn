#!/usr/bin/env python3
"""Exercise the scoped coordination recipe in docs/guides/scoped-coordination.md.

One coordinator session, three worker sessions and one unrelated session share a
disposable collection. The candidate CLI and local API perform every step: exact
topic and direct requests, a response group, durable waits, selected results,
operator-only work associations and artifact links, quiet-notice refusal, routine
deferral and explicit subscription teardown. Only the Plane CLI is synthetic and
it answers reads only, so any attempt to change Plane state fails the fixture.
No model, network or production store is used.

Database selection, in order: the integration runner's cluster
(CAIRN_DISPOSABLE_TEST_ROOT); an explicit already-migrated disposable database
named by both CAIRN_TEST_DATABASE_URL and CAIRN_DATABASE_URL; otherwise a
cluster this fixture creates and removes.
"""

import argparse
import http.client
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
import uuid

from check_work_projection_restore import PLANE_PROGRAM, digest, uid
from disposable_host import (DisposableHost, OwnedCluster, ProvidedCluster,
                             ProvidedDatabase, require, write_private)

ROLES = ("coordinator", "worker_a", "worker_b", "worker_c", "bystander")
WORKERS = ("worker_a", "worker_b", "worker_c")
FEATURES = ("work-view/1", "work-artifacts/1", "coordination-waits/1", "next-turn-notices/1")
GROUP_SECONDS = 10


def ref(value):
    return {"record_id": value["record_id"], "version": value["version"]}


def rfc3339(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


class Recipe:
    def __init__(self, host, report):
        self.host = host
        self.report = report
        self.repo = host.repo
        self.sessions = {}
        self.registrations = {}
        self.topic = "recipe." + uuid.uuid4().hex[:16]
        self.unrelated_topic = "unrelated." + uuid.uuid4().hex[:16]
        self.project = "recipe"
        self.item = None
        self.reused_database = False

    # ------------------------------------------------------------ primitives

    def register(self, role):
        body = self.registrations.setdefault(role, {
            "binding": "scoped-recipe-" + role,
            "native_session_id": "scoped-recipe-" + uid(),
            "metadata": {"harness": "claude", "project": "scoped-recipe",
                         "workspace": str(self.host.home), "state": "idle",
                         "delivery_mode": "existing-session"}})
        old = self.sessions.get(role)
        current = self.host.agent(role, "agent-register", dict(body, request_id=uid()))
        require(current["inbox"] == "agent/" + current["agent_id"],
                "registration did not allocate its own session inbox")
        if old:
            require(current["agent_id"] == old["agent_id"] and current["execution_id"] != old["execution_id"],
                    "resume did not keep the inbox address with a new execution")
        self.sessions[role] = current
        return current

    def raw_api(self, role, operation, body=None):
        """One authenticated session request straight to the API socket; the token is never logged."""
        connection = http.client.HTTPConnection("cairn", timeout=10)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(10)
        connection.sock.connect(str(self.host.socket))
        try:
            token = Path(self.host.token(role)).read_text().strip()
            connection.request("POST", "/v1/" + operation, json.dumps(body or {}), {
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Cairn-Agent-ID": self.sessions[role]["agent_id"],
                "Cairn-Execution-ID": self.sessions[role]["execution_id"]})
            reply = connection.getresponse()
            data = reply.read(65537)
            require(len(data) <= 65536, "unbounded raw API response")
            return reply.status, json.loads(data)
        finally:
            connection.close()

    def session(self, role):
        return {key: self.sessions[role][key] for key in ("agent_id", "execution_id")}

    def inbox(self, role):
        return self.sessions[role]["inbox"]

    def agent(self, role, operation, body=None, **kwargs):
        return self.host.agent(role, operation, body if body is not None else {},
                               session=self.sessions[role], **kwargs)

    def work(self, *args, **kwargs):
        return self.host.command(["work", *args, "--collection", self.repo], **kwargs)

    def sql_count(self, statement):
        return int(self.host.sql(statement).strip())

    def deliveries_to(self, role):
        return self.sql_count(
            "SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) "
            f"WHERE e.repo='{self.repo}' AND d.consumer='{self.inbox(role)}'")

    def source(self, role, body, task):
        return self.agent(role, "create", {"request_id": uid(), "draft": {
            "kind": "note", "body": body, "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.repo, "task_id": task, "run_id": "*"}}})

    def publish(self, role, body, **kwargs):
        body = dict(body)
        body.setdefault("request_id", uid())
        return self.agent(role, "event-publish", body, **kwargs)

    def next(self, role):
        return self.agent(role, "event-next")["delivery"]

    def settle(self, role, delivery, *, draft=None):
        body = {"request_id": uid(), "delivery_id": delivery["delivery_id"],
                "lease_id": delivery["lease_id"], "disposition": "handled"}
        if draft is not None:
            body["draft"] = draft
        return self.agent(role, "event-complete", body)

    def drain(self, role):
        handled = {}
        while True:
            delivery = self.next(role)
            if delivery is None:
                return handled
            self.settle(role, delivery)
            handled[delivery["event"]["event_id"]] = delivery["event"]

    def subscription(self, role, topic, active, request_id=None):
        body = {"request_id": request_id or uid(), "topic": topic, "active": active}
        result = self.agent(role, "event-subscribe", body)
        require(result["consumer"] == self.inbox(role) and result["topic"] == topic
                and result["active"] is active, "subscription changed another consumer or state")
        return body, result

    def inspect(self, role, event_id):
        return self.agent(role, "event-inspect", {"event_id": event_id})

    def associate(self, target_type, target_id):
        result = self.work("associate", "--project", self.project, "--item", self.item,
                           "--target-type", target_type, "--target-id", target_id,
                           "--request-id", uid())
        require(result["target_type"] == target_type, "association changed its target type")
        return result

    def wait(self, predicate, event_id):
        argv = ["wait", "create", *self.host.connection("coordinator", self.sessions["coordinator"]),
                "--repo", self.repo, "--request-id", uid(), "--predicate", predicate,
                "--dependency", event_id, "--deadline", rfc3339(timedelta(minutes=10))]
        result = self.host.command(argv)
        require(result["state"] == "waiting" and result["dependency_event_uuid"] == event_id
                and result["owner_agent_uuid"] == self.sessions["coordinator"]["agent_id"],
                "wait does not bind the coordinator and its exact dependency")
        return result

    def get_wait(self, wait):
        return self.host.command(["wait", "get", *self.host.connection("coordinator", self.sessions["coordinator"]),
                                  "--repo", self.repo, "--wait", wait["wait_uuid"]])

    def sweep(self):
        return self.host.operator("schedule-serve", args=("--repo", self.repo, "--once"))

    # ------------------------------------------------------------ stages

    def setup(self, plane_path):
        host = self.host
        workspace, project = uid(), uid()
        self.item = uid()
        data = host.home / "plane-data.json"
        write_private(data, json.dumps({project: {
            "project": {"id": project, "workspace": workspace, "identifier": "REC"},
            "items": [{"id": self.item, "project": project, "name": "Scoped coordination recipe",
                       "sequence_id": 1, "state": None, "assignees": [],
                       "updated_at": "2026-11-06T00:00:00Z"}]}}))
        config = host.home / "plane.toml"
        write_private(config, 'base_url = "https://plane.invalid"\nworkspace = "synthetic"\n')
        host.env.update(CAIRN_PLANE_BIN=str(plane_path), PLANE_CONFIG=str(config),
                        WORK_RESTORE_PLANE_DATA=str(data))
        # A reused explicit disposable database keeps its first bootstrap.
        boot = host.operator("bootstrap", {"request_id": uid(), "reason": "Disposable scoped coordination recipe"},
                             expected=None)
        require(boot["status"] == "OK" or (self.reused_database and boot["status"] == "AUTHORITY_DENIED"),
                "operator bootstrap failed: " + str(boot["status"]))
        for feature in FEATURES:
            host.command(["work", "features", "enable", "--feature", feature, "--request-id", uid()])
        listed = host.command(["work", "features", "status"])
        listed = listed["features"] if isinstance(listed, dict) else listed
        status = {f["feature_name"]: f["status"] for f in listed}
        require(all(status.get(f) == "active" for f in FEATURES), "selected work features are not active")
        # The recipe does not enable model advice; CAIRN-227's rollout decision is unchanged.
        require(status.get("work-advice/1", "disabled") == "disabled", "recipe enabled work advice")
        self.report["work_features"] = status
        self.work("project", "add", "--slug", self.project, "--workspace", workspace,
                  "--project", project, "--request-id", uid())
        self.work("refresh", "--project", self.project)
        self.plane_before = self.work("show", "--project", self.project, "--item", self.item)
        for role in ROLES:
            self.register(role)
        # The unrelated participant has its own subscription and must never be
        # excluded, deleted or reached by this workflow.
        self.subscription("bystander", self.unrelated_topic, True)
        self.worker_subscriptions = {role: self.subscription(role, self.topic, True)[0] for role in WORKERS}
        self.report["stages"].append("plane_bound_sessions_registered_workers_subscribed")

    def operator_only_links(self):
        # Association and artifact commands use the operator database channel.
        # The authenticated agent API has no route for them; a known agent route
        # through the same raw request path is the positive control.
        control = self.raw_api("coordinator", "event-subscriptions")
        require(control[0] == 200 and control[1]["ok"] is True, "raw agent API positive control failed")
        for operation in ("work-associate", "work-artifact-register", "work-artifact-link",
                          "work-coordination", "work-features"):
            status, envelope = self.raw_api("coordinator", operation)
            require(status == 404 and envelope["ok"] is False and envelope["status"] == "NOT_FOUND",
                    f"agent API exposed operator operation {operation}")
            self.report.setdefault("agent_api_operator_routes", {})[operation] = envelope["status"]
        for role in ("coordinator", *WORKERS):
            self.associate("session", self.session(role)["agent_id"] + ":" + self.session(role)["execution_id"])
        self.report["stages"].append("operator_associations_agent_api_refused")

    def grouped_topic_request(self):
        publication = uid()
        task = "coordination/" + publication
        assignment = self.source("coordinator", "Review the selected recipe change and reply with a "
                                 "short selected result. Task acceptance is decided elsewhere.", task)
        deadline = rfc3339(timedelta(seconds=GROUP_SECONDS))
        event = self.publish("coordinator", {
            "request_id": publication, "kind": "request", "ref": ref(assignment),
            "destination": {"type": "topic", "name": self.topic},
            "response_group": {"deadline": deadline, "partial_policy": "partial"}})
        status = self.inspect("coordinator", event["event_id"])
        consumers = sorted(d["consumer"] for d in status["deliveries"])
        require(consumers == sorted(self.inbox(role) for role in WORKERS),
                "topic snapshot was not exactly the three subscribed workers")
        self.associate("event", event["event_id"])
        self.associate("response_group", event["event_id"])
        wait = self.wait("response_group_closed", event["event_id"])
        self.group_event, self.group_wait, self.group_deadline = event, wait, deadline
        self.report["stages"].append("topic_request_exact_snapshot_group_and_wait")

    def worker_reply(self, role, event, marker):
        delivery = self.next(role)
        require(delivery is not None and delivery["event"]["event_id"] == event["event_id"],
                "worker did not receive the exact request")
        source = self.agent(role, "history", delivery["event"]["ref"])
        require(source["versions"][-1]["payload_available"], "worker could not read the exact source")
        completed = self.settle(role, delivery, draft={
            "kind": "note", "claim_type": "self", "sensitivity": "shareable",
            "body": f"Selected result {marker}: reviewed exact source "
                    f"{event['ref']['record_id']} v{event['ref']['version']}; outcome unassessed.",
            "scope": {"repo": self.repo, "task_id": "coordination-result/" + event["event_id"], "run_id": "*"}})
        result = completed["result"]
        require(result and result["version"] >= 1, "completion did not return a selected result reference")
        response = self.publish(role, {
            "kind": "response", "ref": ref(result),
            "destination": {"type": "agent", "name": self.inbox("coordinator")},
            "causation_id": event["event_id"], "correlation_id": event["correlation_id"]})
        return result, response

    def collect_group(self):
        event = self.group_event
        self.markers = {role: "marker_" + uuid.uuid4().hex for role in ("worker_a", "worker_b")}
        self.results, responses = {}, {}
        for role in ("worker_a", "worker_b"):
            self.results[role], responses[role] = self.worker_reply(role, event, self.markers[role])
        group = self.agent("coordinator", "event-group", {"event_id": event["event_id"]})
        require(group["state"] == "open", "partial policy closed the group before its deadline")
        require(self.get_wait(self.group_wait)["state"] == "waiting", "wait settled before group closure")
        require(self.next("bystander") is None, "unrelated participant received workflow traffic")
        remaining = (datetime.fromisoformat(self.group_deadline) - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining + 1)
        sweeps = [self.sweep(), self.sweep()]
        group = self.agent("coordinator", "event-group", {"event_id": event["event_id"]})
        require(group["state"] == "partial" and group["responded"] == 2 and group["expected"] == 3
                and group["reported_task_outcome"] == "unknown",
                "deadline did not retain a partial collection without a task outcome")
        wait = self.get_wait(self.group_wait)
        require(wait["state"] == "satisfied" and wait["terminal_reason"] == "group_closed"
                and wait["outbox_state"] == "published", "group closure did not settle and publish the wait")
        handled = self.drain("coordinator")
        expected = {responses["worker_a"]["event_id"], responses["worker_b"]["event_id"],
                    str(uuid.UUID(wait["reserved_notice_event_uuid"]))}
        require(set(handled) == expected, "coordinator inbox differs from two replies and one wait notice")
        self.report["group"] = {"state": group["state"], "responded": group["responded"],
                                "expected": group["expected"], "sweeps": sweeps}
        self.report["stages"].append("partial_group_durable_wait_notice")

    def direct_request(self):
        publication = uid()
        task = "coordination/" + publication
        follow_up = self.source("coordinator", "Follow-up for worker_a only: confirm the selected result.", task)
        before = {role: self.deliveries_to(role) for role in ROLES}
        event = self.publish("coordinator", {"request_id": publication, "kind": "request",
                                             "ref": ref(follow_up), "correlation_id": uid(),
                                             "destination": {"type": "agent", "name": self.inbox("worker_a")}})
        self.associate("event", event["event_id"])
        wait = self.wait("request_response", event["event_id"])
        result, response = self.worker_reply("worker_a", event, "marker_" + uuid.uuid4().hex)
        self.sweep()
        current = self.get_wait(wait)
        require(current["state"] == "satisfied" and current["terminal_reason"] == "response"
                and current["response_ref"] == ref(response["ref"]) and current["outbox_state"] == "published",
                "direct reply did not satisfy and publish its exact wait")
        handled = self.drain("coordinator")
        require(set(handled) == {response["event_id"], str(uuid.UUID(current["reserved_notice_event_uuid"]))},
                "coordinator inbox differs from the direct reply and its wait notice")
        after = {role: self.deliveries_to(role) for role in ROLES}
        require(after["worker_a"] == before["worker_a"] + 1
                and all(after[r] == before[r] for r in ("worker_b", "worker_c", "bystander")),
                "direct request reached a participant other than its exact recipient")
        self.results["worker_a_follow_up"] = result
        self.report["stages"].append("direct_request_exact_recipient_and_wait")

    def selected_results(self):
        listed_ids = []
        for name in ("worker_a", "worker_b"):
            result = self.results[name]
            self.associate("result_record", f"{result['record_id']}:{result['version']}")
            revision = self.work("artifact", "register", "--kind", "cairn_result", "--record",
                                 result["record_id"], "--version", str(result["version"]),
                                 "--display-name", f"Selected result from {name}", "--request-id", uid())
            self.work("artifact", "link", "--project", self.project, "--item", self.item,
                      "--revision", revision["revision_id"], "--role", "output",
                      "--rationale", "Selected recipe result", "--request-id", uid())
            listed_ids.append(revision["revision_id"])
            read = self.agent("coordinator", "history", ref(result))
            exact = [v for v in read["versions"] if v["version"] == result["version"]]
            require(exact and exact[0]["payload_available"] and self.markers[name] in exact[0]["body"],
                    "selected result is not readable at its exact version")
        markers = tuple(self.markers.values())
        listed = self.work("artifact", "list", "--project", self.project, "--item", self.item,
                           forbidden=markers)
        require(all(revision in json.dumps(listed) for revision in listed_ids),
                "work item does not list both exact selected result revisions")
        shown = self.work("show", "--project", self.project, "--item", self.item, forbidden=markers)
        self.report["work_show_keys"] = sorted(shown) if isinstance(shown, dict) else None
        self.report["stages"].append("selected_results_operator_artifact_links_without_payload_copy")

    def notices(self):
        note = self.source("coordinator", "Routine status: the review round closed.", "coordination/status")
        events_before = self.sql_count(f"SELECT count(*) FROM cairn.agent_event WHERE repo='{self.repo}'")
        refused = self.publish("coordinator", {
            "kind": "notice", "ref": ref(note), "destination": {"type": "agent", "name": self.inbox("worker_b")},
            "notice_delivery": "next_organic_turn", "notice_expires_at": rfc3339(timedelta(hours=1))},
            expected="UNSUPPORTED_DELIVERY")
        require(refused["ok"] is False, "unsupported quiet recipient accepted next-turn delivery")
        require(self.sql_count(f"SELECT count(*) FROM cairn.agent_event WHERE repo='{self.repo}'") == events_before,
                "refused quiet notice left an event")
        deferred = self.publish("coordinator", {
            "kind": "notice", "ref": ref(note), "destination": {"type": "agent", "name": self.inbox("worker_b")},
            "notice_policy": {"version": 1, "urgency": "routine", "defer_seconds": 60}})
        require(self.next("worker_b") is None, "routine deferred notice was claimable before its deferral")
        status = self.inspect("coordinator", deferred["event_id"])
        require([d["state"] for d in status["deliveries"]] == ["pending"], "deferred notice was not retained pending")
        self.deferred_notice = deferred
        self.report["stages"].append("quiet_refused_for_unsupported_routine_deferral_retained")

    def teardown(self):
        note = self.source("coordinator", "Teardown: this recipe's temporary topic is closing.", "coordination/teardown")
        # A stopped session remains a subscriber; stopping is not unsubscribing.
        self.host.agent("worker_c", "agent-leave", self.session("worker_c"))
        stopped_notice = self.publish("coordinator", {"kind": "notice", "ref": ref(note),
                                                      "destination": {"type": "topic", "name": self.topic}})
        consumers = sorted(d["consumer"] for d in self.inspect("coordinator", stopped_notice["event_id"])["deliveries"])
        require(consumers == sorted(self.inbox(role) for role in WORKERS),
                "stopped subscriber was silently excluded from topic fanout")
        # The coordinator can only change its own subscription row.
        self.subscription("coordinator", self.topic, False)
        active = f"SELECT count(*) FROM cairn.agent_subscription WHERE repo='{self.repo}' AND topic='{self.topic}' AND active"
        require(self.sql_count(active) == 3, "another session's unsubscribe changed worker subscriptions")
        self.register("worker_c")
        for role in WORKERS:
            body, result = self.subscription(role, self.topic, False)
            require(self.agent(role, "event-subscribe", body) == result, "unsubscribe retry changed its result")
        require(self.sql_count(active) == 0, "explicit teardown left an active temporary subscription")
        after = self.publish("coordinator", {"kind": "notice", "ref": ref(note),
                                             "destination": {"type": "topic", "name": self.topic}})
        require(self.inspect("coordinator", after["event_id"])["deliveries"] == [],
                "publication after teardown still fanned out")
        # Already-addressed work survives unsubscribe: worker_c still holds the
        # original group request and the teardown notice.
        held = {}
        while (delivery := self.next("worker_c")) is not None:
            held[delivery["event"]["event_id"]] = delivery
        require(set(held) == {self.group_event["event_id"], stopped_notice["event_id"]},
                "unsubscribe removed or changed already-addressed deliveries")
        late_result = self.settle("worker_c", held[self.group_event["event_id"]], draft={
            "kind": "note", "claim_type": "self", "sensitivity": "shareable",
            "body": "Late selected result after group closure; outcome unassessed.",
            "scope": {"repo": self.repo, "task_id": "coordination-result/" + self.group_event["event_id"],
                      "run_id": "*"}})["result"]
        self.settle("worker_c", held[stopped_notice["event_id"]])
        late = self.publish("worker_c", {
            "kind": "response", "ref": ref(late_result),
            "destination": {"type": "agent", "name": self.inbox("coordinator")},
            "causation_id": self.group_event["event_id"], "correlation_id": self.group_event["correlation_id"]})
        group = self.agent("coordinator", "event-group", {"event_id": self.group_event["event_id"]})
        require(group["state"] == "partial" and group["responded"] == 2, "late reply reopened the group")
        self.drain("coordinator")
        for role in ("worker_a", "worker_b"):
            handled = self.drain(role)
            require(set(handled) == {stopped_notice["event_id"]}, f"{role} received unexpected teardown traffic")
        deferred = self.inspect("coordinator", self.deferred_notice["event_id"])["deliveries"]
        require([d["state"] for d in deferred] == ["pending"], "teardown removed the deferred direct notice")
        self.report["late_response_event"] = late["event_id"]
        self.report["stages"].append("explicit_teardown_preserves_addressed_and_stopped_subscribers")

    def unrelated_and_plane(self):
        require(self.deliveries_to("bystander") == 0 and self.next("bystander") is None,
                "unrelated participant received a delivery")
        subs = self.agent("bystander", "event-subscriptions")["subscriptions"]
        require([(s["topic"], s["active"]) for s in subs] == [(self.unrelated_topic, True)],
                "workflow changed the unrelated participant's subscription")
        # Plane owns state and assignment. The read-only Plane stand-in refuses any
        # write command, and the projected Plane fields are unchanged.
        plane_fields = ("plane_item_uuid", "identifier", "title", "state_id", "state_name",
                        "state_group", "assignee_ids", "assignee_names", "updated_at_source",
                        "cache_generation", "is_absent")
        after = self.work("show", "--project", self.project, "--item", self.item)
        before_item, after_item = self.plane_before["item"], after["item"]
        require(before_item["plane_item_uuid"] == self.item and before_item["title"] == "Scoped coordination recipe",
                "Plane projection did not identify the synthetic item")
        require({k: before_item.get(k) for k in plane_fields} == {k: after_item.get(k) for k in plane_fields},
                "workflow changed Plane-owned projected fields")
        # Only explicit associations appear: four sessions, two requests, one group, two results.
        targets = sorted(a["target_type"] for a in after["associations"] if a.get("state", "active") == "active")
        require(targets == ["event", "event", "response_group", "result_record", "result_record",
                            "session", "session", "session", "session"],
                "work item associations differ from the explicit selections: " + ",".join(targets))
        require(not any(self.sessions["bystander"]["agent_id"] in json.dumps(a) for a in after["associations"]),
                "unrelated participant became associated with the work item")
        self.report["stages"].append("unrelated_zero_deliveries_plane_unchanged")

    def run(self, plane_path):
        self.setup(plane_path)
        self.operator_only_links()
        self.grouped_topic_request()
        self.collect_group()
        self.direct_request()
        self.selected_results()
        self.notices()
        self.teardown()
        self.unrelated_and_plane()


def scenario(source, binary, report, home=None):
    repo = "fixture:scoped-coordination:" + uid()
    kwargs = {"database": "cairn_scoped_coordination_recipe"} if not isinstance(source, ProvidedDatabase) else {}
    with DisposableHost.start(source, repo=repo, binary=binary, roles=ROLES, home=home,
                              label="scoped_coordination_recipe", **kwargs) as host:
        for name in list(host.env):
            if name.startswith("PLANE_") or name == "CAIRN_PLANE_BIN":
                del host.env[name]
        plane = host.home / "plane-fixture"
        write_private(plane, PLANE_PROGRAM)
        plane.chmod(0o700)
        report["repo"] = repo
        recipe = Recipe(host, report)
        recipe.reused_database = isinstance(source, ProvidedDatabase)
        recipe.run(plane)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--report-directory", type=Path)
    args = parser.parse_args()
    require(args.binary.is_file(), "explicit candidate executable required")
    report = {"schema": "cairn.scoped-coordination-recipe/1", "status": "running", "stages": [],
              "binary_sha256": digest(args.binary), "fixture_sha256": digest(__file__),
              "provider_called": False, "plane": "synthetic read-only CLI boundary"}
    cluster, home = None, None
    try:
        if os.environ.get("CAIRN_DISPOSABLE_TEST_ROOT"):
            cluster = ProvidedCluster.from_environment()
            report["database"] = "integration_runner_cluster"
            scenario(cluster, args.binary.resolve(), report)
        elif os.environ.get("CAIRN_TEST_DATABASE_URL"):
            home = Path(tempfile.mkdtemp(prefix="cairn-scoped-recipe-"))
            report["database"] = "explicit_disposable_database"
            scenario(ProvidedDatabase.from_environment(), args.binary.resolve(), report, home=home)
        else:
            root = Path(tempfile.mkdtemp(prefix="cairn-scoped-recipe-"))
            cluster = OwnedCluster.start(root)
            report["database"] = "fixture_owned_cluster"
            scenario(cluster, args.binary.resolve(), report)
        require(digest(args.binary) == report["binary_sha256"], "candidate changed during the recipe")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        if cluster is not None and cluster.owned:
            cluster.remove()
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
        if args.report_directory:
            args.report_directory.mkdir(parents=True, exist_ok=True)
            (args.report_directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
