"""Event-scaling state across an actual older physical restore.

An extension of scripts/check_work_coordination_restore.py. That runner owns
the disposable cluster, the pg_dump/pg_restore, the restore session and the
native conversation; this case adds admission policy and closure obligations
through real CLI/API operations and checks how they come back. SQL only
observes exact rows.

Two sessions of its own (requester under the bob profile, responder under the
alice profile) keep the runner's wait-fixture inboxes untouched, because the
runner asserts those inboxes are empty after restore.

`admission_mode` selects the configured policy mode. `enforce` is the proof
mode: it requires the enforcement capability and exercises the stale
reconciliation refusal. `observe` checks only the mode-independent closure and
policy restore behavior; the wrapper reports it as an incomplete subset.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import uuid

from disposable_host import require

PRODUCER_RATE = {"per_second": 1, "burst": 1000}
LIMITS = {
    # Finite queue and closure bounds are generous so the runner's own traffic is
    # never refused; this case proves restore semantics, not saturation.
    "max_topic_recipients": 100, "max_recipient_outstanding": 1000,
    "max_producer_recipient_outstanding": 1000, "max_collection_outstanding": 10000,
    "max_closure_reserved": 1000, "max_recipient_closure_reserved": 1000,
    # Producer buckets refill slowly, so a restored balance (seconds since the
    # fence) is distinguishable from a full burst; owner and collection buckets
    # refill fast enough that shared runner traffic is never rate-limited.
    "producer_publications": PRODUCER_RATE, "producer_deliveries": PRODUCER_RATE,
    **{name: {"per_second": 1000, "burst": 10000} for name in (
        "owner_publications", "owner_deliveries", "collection_publications", "collection_deliveries")},
}
NANO = 1_000_000_000
PHASES = ("notices_queued", "backup_complete", "source_forgotten", "restore_paused",
          "restore_resumed", "coordination_controls_complete")


def new_id():
    return str(uuid.uuid4())


def ref(value):
    return {"record_id": value["record_id"], "version": value["version"]}


class EventScalingRestoreCase:
    def __init__(self, admission_mode="enforce"):
        require(admission_mode in ("enforce", "observe"), "admission mode must be enforce or observe")
        self.mode = admission_mode
        self.host = None
        self.sessions, self.registrations = {}, {}
        self.extra_inputs = []
        self.report = {"status": "not_started", "admission_mode": admission_mode, "stages": []}

    # ------------------------------------------------------------ runner hooks

    def input_files(self):
        return [Path(__file__).resolve(), *self.extra_inputs]

    def snapshot(self, controller):
        if self.host is None:
            return None
        return {"policy": self.policy(), "closures": self.closures_for_events()}

    def on_phase(self, name, controller):
        require(name in PHASES, "unreviewed event-scaling restore phase: " + name)
        self.controller = controller
        handler = getattr(self, "phase_" + name, None)
        if handler is not None:
            handler(controller)

    def close(self, controller):
        # No processes, sockets or files outside the runner's disposable home.
        if self.report["status"] == "running":
            self.report["status"] = "incomplete"
        controller.record("event_scaling_restore", self.report)

    # ------------------------------------------------------------ primitives

    def record(self, name, value):
        self.report[name] = value
        self.controller.record("event_scaling_restore", self.report)

    def stage(self, name):
        self.report["stages"].append(name)
        self.controller.record("event_scaling_restore", self.report)

    def register(self, label, profile):
        body = self.registrations.setdefault(label, {
            "binding": "event-scaling-restore-" + label,
            "native_session_id": "event-scaling-restore-" + new_id(),
            "metadata": {"harness": "claude", "project": "event-scaling-restore",
                         "workspace": str(self.host.home), "state": "idle",
                         "delivery_mode": "existing-session"}})
        old = self.sessions.get(label)
        current = self.host.agent(profile, "agent-register", dict(body, request_id=new_id()))
        if old:
            require(current["agent_id"] == old["agent_id"] and current["execution_id"] != old["execution_id"],
                    label + " resume did not keep its inbox with a new execution")
        self.sessions[label] = dict(current, profile=profile)
        return current

    def agent(self, label, operation, body=None, **kwargs):
        current = self.sessions[label]
        return self.host.agent(current["profile"], operation, body if body is not None else {},
                               session={"agent_id": current["agent_id"], "execution_id": current["execution_id"]},
                               **kwargs)

    def sql(self, statement):
        return self.host.sql(statement)

    def count(self, statement):
        return int(self.sql(statement).strip())

    def generation(self):
        return self.count("SELECT generation FROM cairn.retrieval_generation WHERE singleton")

    def policy(self):
        return self.host.operator("event-admission-policy", {"repo": self.host.repo})

    def configure(self, mode, expected, workload):
        policy = {"version": 1, "mode": mode, "workload": workload, "limits": LIMITS}
        return self.host.operator("event-admission-configure", {
            "request_id": new_id(), "repo": self.host.repo, "expected_revision": expected, "policy": policy})

    def reconcile(self):
        batches = []
        while True:
            request = {"request_id": new_id(), "repo": self.host.repo, "limit": 500}
            batch = self.host.operator("event-closure-reconcile", request)
            require(self.host.operator("event-closure-reconcile", request) == batch,
                    "reconciliation retry with the same request UUID changed its result")
            batches.append(batch)
            if batch["complete"]:
                return batches
            require(batch["more"] and len(batches) < 50, "reconciliation neither completed nor progressed")

    def collection_counts(self):
        return self.host.operator("event-closures-collection", {"repo": self.host.repo, "limit": 1})["counts"]

    def closure(self, event_id):
        rows = self.sql("SELECT json_agg(json_build_object('obligation', obligation, 'state', state, "
                        "'release_reason', release_reason, 'restore_generation', restore_generation, "
                        "'consumed_event_id', consumed_event_id) ORDER BY reservation_id)::text "
                        f"FROM cairn.agent_closure_reservation WHERE request_event_id='{event_id}'")
        return json.loads(rows) if rows.strip() else []

    def wait_closure(self, wait_uuid):
        rows = self.sql("SELECT json_agg(json_build_object('state', state, 'release_reason', release_reason))::text "
                        f"FROM cairn.agent_closure_reservation WHERE wait_uuid='{wait_uuid}' AND obligation='wait_notice'")
        return json.loads(rows) if rows.strip() else []

    def closures_for_events(self):
        return {name: self.closure(event["event_id"]) for name, event in getattr(self, "events", {}).items()}

    def source(self, label, body):
        return self.agent(label, "create", {"request_id": new_id(), "draft": {
            "kind": "note", "body": body, "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.host.repo, "task_id": "coordination/event-scaling-restore", "run_id": "*"}}})

    def request(self, body, *, grouped=False, expected="OK"):
        payload = {"request_id": new_id(), "kind": "request", "ref": ref(self.source("requester", body)),
                   "destination": {"type": "agent", "name": self.sessions["responder"]["inbox"]},
                   "correlation_id": new_id()}
        if grouped:
            payload["response_group"] = {"partial_policy": "partial",
                                         "deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}
        self.last_request = payload
        return self.agent("requester", "event-publish", payload, expected=expected)

    def producer_refilled(self, label):
        """Whether both of a session's producer buckets now hold one whole token (DB time)."""
        inbox = self.sessions[label]["inbox"]
        ready = self.sql("SELECT COALESCE(bool_and(tokens_nano + extract(epoch FROM clock_timestamp()-refill_at)"
                         f"*{PRODUCER_RATE['per_second']}*{NANO} >= {NANO}), true) FROM cairn.agent_event_rate_bucket "
                         f"WHERE repo='{self.host.repo}' AND identity_kind='producer' AND identity='{inbox}'")
        return ready.strip() == "t"

    def wait_producer_refill(self, label, timeout=15):
        # Buckets refill only with elapsed database time; wait for it, bounded.
        deadline = time.monotonic() + timeout
        while not self.producer_refilled(label):
            require(time.monotonic() < deadline, label + " producer bucket did not refill with database time")
            time.sleep(0.2)

    def claim(self, label, event):
        delivery = self.agent(label, "event-next")["delivery"]
        require(delivery is not None and delivery["event"]["event_id"] == event["event_id"],
                label + " did not receive the exact expected event")
        return delivery

    def settle(self, label, delivery, *, result=None):
        body = {"request_id": new_id(), "delivery_id": delivery["delivery_id"],
                "lease_id": delivery["lease_id"], "disposition": "handled"}
        if result is not None:
            body["draft"] = {"kind": "note", "body": result, "claim_type": "self", "sensitivity": "shareable",
                             "scope": {"repo": self.host.repo, "task_id": "coordination-result/event-scaling",
                                       "run_id": "*"}}
        return self.agent(label, "event-complete", body)

    def reply(self, event, result_ref):
        return self.agent("responder", "event-publish", {
            "request_id": new_id(), "kind": "response", "ref": result_ref,
            "destination": {"type": "agent", "name": self.sessions["requester"]["inbox"]},
            "causation_id": event["event_id"], "correlation_id": event["correlation_id"]})

    def drain_requester(self, expected_ids):
        handled = set()
        while (delivery := self.agent("requester", "event-next")["delivery"]) is not None:
            self.settle("requester", delivery)
            handled.add(delivery["event"]["event_id"])
        require(handled == set(expected_ids), "requester inbox differs from the expected replies")

    def buckets(self):
        rows = self.sql("SELECT json_agg(json_build_object('kind', identity_kind, 'identity', identity, 'unit', unit, "
                        "'tokens_nano', tokens_nano, 'refill_at', refill_at, 'generation', restore_generation, "
                        "'policy_revision', policy_revision) ORDER BY identity_kind, identity, unit)::text "
                        f"FROM cairn.agent_event_rate_bucket WHERE repo='{self.host.repo}'")
        return json.loads(rows) if rows.strip() else []

    def bucket(self, kind, identity, unit):
        found = [b for b in self.buckets() if (b["kind"], b["identity"], b["unit"]) == (kind, identity, unit)]
        return found[0] if found else None

    def db_now(self):
        return self.sql("SELECT to_json(clock_timestamp())::text").strip().strip('"')

    def event_count(self):
        return self.count(f"SELECT count(*) FROM cairn.agent_event WHERE repo='{self.host.repo}'")

    # ------------------------------------------------------------ phases

    def phase_notices_queued(self, controller):
        """Before the backup: a selected policy and retained direct and grouped obligations."""
        self.host = controller.ctx["host"]
        self.report["status"] = "running"
        self.register("requester", "bob")
        self.register("responder", "alice")
        self.record("pre_backup_reconciliation", self.reconcile())
        observed = self.configure("observe", self.policy()["revision"], "restore proof: observe before canary")
        selected = observed
        if self.mode == "enforce":
            # Rollout order: complete reconciliation, observe, then enforce.
            selected = self.configure("enforce", observed["revision"], "restore proof: enforce at backup")
        require(selected["policy"]["mode"] == self.mode, "policy mode was not selected")
        self.backup_policy = selected
        self.events = {"direct": self.request("Direct request whose reply obligation must survive restore."),
                       "grouped": self.request("Grouped request whose reply is lost by restoring.", grouped=True)}
        direct, grouped = self.events["direct"], self.events["grouped"]
        for name, event in self.events.items():
            rows = self.closure(event["event_id"])
            require(len(rows) == 1 and rows[0]["state"] == "reserved"
                    and rows[0]["obligation"] == ("group_reply" if name == "grouped" else "request_reply"),
                    name + " request did not reserve exactly one reply closure")
        # Handling the direct request without replying keeps its obligation.
        self.settle("responder", self.claim("responder", direct))
        require(self.closure(direct["event_id"])[0]["state"] == "reserved",
                "completion alone erased a pending reply obligation")
        grouped_delivery = self.claim("responder", grouped)
        self.grouped_result = ref(self.settle("responder", grouped_delivery,
                                              result="Grouped result committed after the backup.")["result"])
        self.direct_result = None
        self.backup_generation = self.generation()
        self.backup_buckets = self.buckets()
        if self.mode == "enforce":
            producer = self.bucket("producer", self.sessions["requester"]["inbox"], "publication")
            require(producer is not None and producer["tokens_nano"] < PRODUCER_RATE["burst"] * NANO,
                    "enforced publications did not debit the requester's producer bucket")
        else:
            require(self.backup_buckets == [], "observe mode created or debited a rate bucket")
        self.backup_clock = self.db_now()
        self.record("backup_state", {"policy_revision": selected["revision"], "generation": self.backup_generation,
                                     "closures": self.closures_for_events(), "buckets": self.backup_buckets})
        self.stage("policy_and_direct_grouped_obligations_before_backup")

    def phase_backup_complete(self, controller):
        """After the backup: later state the restore must discard."""
        grouped = self.events["grouped"]
        lost = self.reply(grouped, self.grouped_result)
        rows = self.closure(grouped["event_id"])
        require(rows[0]["state"] == "delivered" and rows[0]["consumed_event_id"] == lost["event_id"],
                "post-backup reply did not consume the grouped closure")
        self.drain_requester({lost["event_id"]})
        before_revision = self.buckets()
        later = self.configure(self.mode, self.backup_policy["revision"], "restore proof: revision after backup")
        require(later["revision"] == self.backup_policy["revision"] + 1, "post-backup policy edit did not advance")
        require(self.buckets() == before_revision, "a policy revision changed retained bucket balances")
        self.record("post_backup_state", {"policy_revision": later["revision"], "lost_reply": lost["event_id"]})
        self.stage("post_backup_reply_and_policy_revision")

    def phase_restore_resumed(self, controller):
        """The older state returns, fenced; accepted closures drain; new obligations wait for reconciliation."""
        generation = self.generation()
        require(generation > self.backup_generation, "restore did not advance the generation")
        restored = self.policy()
        require(restored["revision"] == self.backup_policy["revision"]
                and restored["policy"] == self.backup_policy["policy"],
                "policy did not return to its exact backup revision")
        direct, grouped = self.events["direct"], self.events["grouped"]
        grouped_rows = self.closure(grouped["event_id"])
        require(grouped_rows[0]["state"] == "released" and grouped_rows[0]["release_reason"] == "restore_fenced",
                "fenced group kept or revived its reply closure")
        direct_rows = self.closure(direct["event_id"])
        require(direct_rows[0]["state"] == "reserved" and direct_rows[0]["restore_generation"] == self.backup_generation,
                "restore dropped or rewrote the direct reply obligation")
        fenced_waits = {}
        for wait in controller.waits:
            fenced_waits[wait["wait_uuid"]] = self.wait_closure(wait["wait_uuid"])
        waiting = fenced_waits[controller.w1["wait_uuid"]]
        require(waiting and all(row["state"] == "released" and row["release_reason"] == "restore_fenced"
                                for row in waiting), "fenced waiting wait kept its notice closure")
        require(self.count("SELECT count(*) FROM cairn.agent_closure_reconciliation "
                           f"WHERE repo='{self.host.repo}' AND restore_generation={generation}") == 0,
                "restored database already claims reconciliation for the new generation")
        counts = self.collection_counts()
        require(counts["available"] is False and counts["reason"] == "unreconciled" and counts.get("reserved") is None,
                "unreconciled closure counts were reported as known")
        fenced = self.buckets()
        resumed_clock = self.db_now()
        require(sorted((b["kind"], b["identity"], b["unit"]) for b in fenced) ==
                sorted((b["kind"], b["identity"], b["unit"]) for b in self.backup_buckets),
                "restore added or dropped rate buckets before any publication")
        anchored = self.count("SELECT count(*) FROM cairn.agent_event_rate_bucket "
                              f"WHERE repo='{self.host.repo}' AND tokens_nano=0 AND restore_generation={generation} "
                              f"AND refill_at BETWEEN '{self.backup_clock}'::timestamptz AND '{resumed_clock}'::timestamptz")
        require(anchored == len(fenced) and all(b["tokens_nano"] == 0 and b["generation"] == generation for b in fenced),
                "restore fence did not eagerly zero buckets with a fence-time refill anchor")
        self.register("requester", "bob")
        self.register("responder", "alice")
        group = self.agent("requester", "event-group", {"event_id": grouped["event_id"]})
        require(group["state"] == "incomplete" and group["responded"] == 0,
                "restored group reopened or kept the lost reply")
        refusal = None
        if self.mode == "enforce":
            before = self.event_count()
            refusal = self.request("New obligation before reconciliation.", expected="CLOSURE_UNRECONCILED")
            require(self.event_count() == before, "refused new obligation left an event")
            require(self.buckets() == fenced, "a refused publication changed the fenced rate anchors")
        # An already accepted obligation still closes without reconciliation.
        self.direct_result = ref(self.source("responder", "Direct reply after restore."))
        drained = self.reply(direct, self.direct_result)
        rows = self.closure(direct["event_id"])
        require(rows[0]["state"] == "delivered" and rows[0]["consumed_event_id"] == drained["event_id"],
                "accepted direct obligation did not drain after restore")
        self.drain_requester({drained["event_id"]})
        batches = self.reconcile()
        require(batches[-1]["restore_generation"] == generation, "reconciliation recorded another generation")
        counts = self.collection_counts()
        require(counts["available"] is True and counts["restore_generation"] == generation,
                "reconciled closure counts are unavailable")
        accepted_body = "New obligation after reconciliation."
        rate_refusal = None
        if self.mode == "enforce":
            # The fence left the requester's producer buckets empty with a fence-time
            # anchor. Until one token of database time has passed, the publication
            # is refused on a producer rate dimension without any side effect, and
            # the same request UUID is admitted after the refill.
            before_events, before_buckets = self.event_count(), self.buckets()
            envelope = self.request(accepted_body, expected=None)
            if envelope["ok"]:
                accepted = envelope["data"]
            else:
                rate_refusal = envelope.get("admission")
                require(envelope["status"] == "EVENT_ADMISSION_REFUSED" and rate_refusal
                        and rate_refusal["dimension"] in ("producer_publications", "producer_deliveries"),
                        "first post-restore publication refused for a reason other than producer rate: "
                        + json.dumps({k: envelope.get(k) for k in ("status", "admission")}))
                require(self.event_count() == before_events and self.buckets() == before_buckets,
                        "rate-refused publication left an event or changed a bucket")
                self.wait_producer_refill("requester")
                accepted = self.agent("requester", "event-publish", self.last_request)
        else:
            accepted = self.request(accepted_body)
        require(self.closure(accepted["event_id"])[0]["restore_generation"] == generation,
                "new obligation did not reserve under the current generation")
        self.settle("responder", self.claim("responder", accepted))
        self.events["after_reconcile"] = accepted
        rate_state = None
        if self.mode == "enforce":
            producer = self.bucket("producer", self.sessions["requester"]["inbox"], "publication")
            require(producer["generation"] == generation
                    and producer["tokens_nano"] < (PRODUCER_RATE["burst"] - 1) * NANO,
                    "restore granted the requester a fresh producer burst")
            retried_before = self.buckets()
            retry = self.agent("requester", "event-publish", self.last_request)
            require(retry["event_id"] == accepted["event_id"] and self.buckets() == retried_before,
                    "committed same-UUID retry changed its event or debited again")
            # A producer first seen after the backup has no restored bucket; its
            # first use is the normal bounded initial burst, not restored spend.
            self.register("latecomer", "sender")
            require(self.bucket("producer", self.sessions["latecomer"]["inbox"], "publication") is None,
                    "post-restore producer unexpectedly had a restored bucket")
            note = self.agent("latecomer", "create", {"request_id": new_id(), "draft": {
                "kind": "note", "body": "First publication by a producer absent from the backup.",
                "claim_type": "self", "sensitivity": "shareable",
                "scope": {"repo": self.host.repo, "task_id": "coordination/event-scaling-restore", "run_id": "*"}}})
            notice = self.agent("latecomer", "event-publish", {
                "request_id": new_id(), "kind": "notice", "ref": ref(note),
                "destination": {"type": "agent", "name": self.sessions["responder"]["inbox"]}})
            self.settle("responder", self.claim("responder", notice))
            first = self.bucket("producer", self.sessions["latecomer"]["inbox"], "publication")
            require(first["generation"] == generation and first["tokens_nano"] == (PRODUCER_RATE["burst"] - 1) * NANO,
                    "absent producer key did not receive exactly the bounded initial burst")
            rate_state = {"requester_tokens_nano": producer["tokens_nano"], "latecomer_tokens_nano": first["tokens_nano"],
                          "first_post_restore_refusal": rate_refusal}
        else:
            require(self.buckets() == [], "observe mode created or debited a rate bucket after restore")
        self.record("restored_state", {
            "generation": generation, "policy_revision": restored["revision"],
            "unreconciled_refusal": refusal and refusal["status"], "drained_reply": drained["event_id"],
            "reconciliation": batches, "wait_closures": fenced_waits, "closures": self.closures_for_events(),
            "fenced_buckets": fenced, "rates": rate_state})
        self.stage("restored_policy_fenced_closures_drain_and_reconcile")

    def phase_coordination_controls_complete(self, controller):
        """A fresh grouped round closes normally in the new generation."""
        accepted = self.events["after_reconcile"]
        result = ref(self.source("responder", "Reply to the post-reconciliation obligation."))
        reply = self.reply(accepted, result)
        if self.mode == "enforce":
            self.wait_producer_refill("requester")
        fresh = self.request("Fresh grouped round in the new generation.", grouped=True)
        group_result = ref(self.settle("responder", self.claim("responder", fresh),
                                       result="Fresh grouped result.")["result"])
        group_reply = self.reply(fresh, group_result)
        self.drain_requester({reply["event_id"], group_reply["event_id"]})
        group = self.agent("requester", "event-group", {"event_id": fresh["event_id"]})
        require(group["state"] == "collected", "fresh group did not collect its single reply")
        for event in (accepted, fresh):
            rows = self.closure(event["event_id"])
            require(len(rows) == 1 and rows[0]["state"] == "delivered", "fresh closure was not consumed exactly once")
        self.report["status"] = "passed" if self.mode == "enforce" else "observe_subset_passed"
        self.stage("fresh_generation_closures_consumed_once")
