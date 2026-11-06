"""Real wait-view setup; native quiet-notice lifecycle integration remains pending.

This module never inserts wait/notice/session-binding rows. A coherent candidate
must supply the selected typed operator endpoint; absence is a failure.
"""
import hashlib
import json
import copy
import time
import uuid
from datetime import datetime, timedelta, timezone


def coordination_fixture(Base, uid, dump, require):
    class CoordinationFixture(Base):
        states = ("coordination_waits", "coordination_empty", "coordination_disabled")

        def __init__(self, args):
            super().__init__(args)
            self.report["coordination_scope"] = {
                "implemented_fixture_cases": ["real waits and exact associations", "wait pagination",
                    "unsupported recipient", "empty membership", "disabled retained reads"],
                "pending_fixture_cases": ["native supported quiet-notice lifecycle and notice pagination",
                    "restore/privacy transitions", "delayed item switch/refusal", "30-second live observation"],
                "whole_coordination_acceptance": False,
            }

        def item_count(self, name):
            return 2

        def coordination_state(self):
            base = super().coordination_state()
            rows = self.host.sql("""SELECT json_build_object(
                'waits', (SELECT jsonb_agg(to_jsonb(w) ORDER BY wait_uuid) FROM cairn.agent_wait w),
                'outboxes', (SELECT jsonb_agg(to_jsonb(o) ORDER BY wait_uuid) FROM cairn.agent_wait_outbox o),
                'notices', (SELECT jsonb_agg(to_jsonb(n) ORDER BY notice_id) FROM cairn.agent_organic_notice n),
                'turns', (SELECT jsonb_agg(to_jsonb(t) ORDER BY root_id) FROM cairn.agent_organic_notice_turn_root t))""")
            return hashlib.sha256((base + rows).encode()).hexdigest()

        def seed(self, name):
            super().seed(name)
            self.guard_item_id = uid(name + ":item:1")
            self.work("features", "enable", "--feature", "coordination-waits/1")
            self.work("features", "enable", "--feature", "next-turn-notices/1")
            self.owner = self.register()
            self.recipient = self.register()
            self.work("associate", "--project", self.slug, "--item", self.item_id,
                "--target-type", "session", "--target-id", self.owner["agent_id"] + ":" + self.owner["execution_id"])
            self.expected_waits, self.unrelated_waits = set(), set()
            self.expected_associations = {}
            # A shared source body remains private; every dependency has its own event UUID.
            source = self.host.agent("worker", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
                kind="note", body="COORDINATION_SOURCE_BODY_MUST_NOT_RENDER", claim_type="self", sensitivity="local",
                scope=dict(repo=self.collection, task_id="*", run_id="*"))), session=self.owner)
            deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            for index in range(29):
                predicate = "response_group_closed" if index == 0 else "request_response"
                body = dict(request_id=str(uuid.uuid4()), kind="request",
                    ref=dict(record_id=source["record_id"], version=source["version"]),
                    destination=dict(type="agent", name=self.recipient["inbox"]), correlation_id=str(uuid.uuid4()))
                if predicate == "response_group_closed":
                    body["response_group"] = dict(deadline=deadline, partial_policy="all")
                event = self.host.agent("worker", "event-publish", body, session=self.owner)
                wait = self.host.command(["wait", "create", *self.host.connection("worker", self.owner),
                    "--repo", self.collection, "--request-id", str(uuid.uuid4()), "--predicate", predicate,
                    "--dependency", event["event_id"], "--deadline", deadline])
                if index == 28 or name == "coordination_empty":
                    self.unrelated_waits.add(wait["wait_uuid"])
                    continue
                association = self.work("associate", "--project", self.slug, "--item", self.item_id,
                    "--target-type", "response_group" if index == 0 else "event", "--target-id", event["event_id"])
                self.expected_waits.add(wait["wait_uuid"])
                self.expected_associations[wait["wait_uuid"]] = {(association["association_id"], association["revision"])}
                if index == 0:
                    second = self.work("associate", "--project", self.slug, "--item", self.item_id,
                        "--target-type", "event", "--target-id", event["event_id"])
                    self.expected_associations[wait["wait_uuid"]].add((second["association_id"], second["revision"]))
                if index == 1:
                    self.host.command(["wait", "cancel", *self.host.connection("worker", self.owner),
                        "--repo", self.collection, "--request-id", str(uuid.uuid4()), "--wait", wait["wait_uuid"]])
            if name == "coordination_disabled":
                self.work("features", "disable", "--feature", "coordination-waits/1")
                self.work("features", "disable", "--feature", "next-turn-notices/1")

        def read_panel(self, page, cursor=""):
            params = dict(project=self.slug, item=self.item_id, limit=25)
            if cursor:
                params["wait_cursor"] = cursor
            response = page.request.get(self.url + "/api/work/item/coordination", params=params)
            require(response.ok, "required real coordination endpoint refused: " + response.text()[:400])
            body = response.json()
            detail = self.detail()
            require(body["schema"] == "cairn.work-coordination-view/1" and body["collection"] == self.collection
                    and body["binding_id"] == detail["item"]["binding_id"]
                    and body["plane_item_uuid"] == self.item_id, "coordination response identity mismatch")
            require(set(body) == {"schema", "collection", "binding_id", "plane_item_uuid", "observed_at",
                                 "restore_generation", "waits", "quiet_notices"}, "unexpected top-level coordination fields")
            require(body["restore_generation"] == 0, "fresh disposable coordination view must accept initial generation zero")
            forbidden = {"response_ref", "reserved_source_record_uuid", "owner_principal", "profile_binding_id",
                         "source_body", "body", "title", "payload", "response_text", "owner_input_attestation", "native_turn_id"}
            def no_private_fields(value):
                if isinstance(value, dict):
                    require(not forbidden.intersection(value), "coordination DTO exposed a forbidden field")
                    for item in value.values():
                        no_private_fields(item)
                elif isinstance(value, list):
                    for item in value:
                        no_private_fields(item)
            no_private_fields(body)
            require("COORDINATION_SOURCE_BODY_MUST_NOT_RENDER" not in json.dumps(body), "source body leaked through coordination DTO")
            return body

        def browser_projection(self, page, name, viewport):
            from playwright.sync_api import expect
            before = self.coordination_state()
            observed, cursor, cursors = {}, "", set()
            while True:
                body = self.read_panel(page, cursor)
                for wait in body["waits"]["items"]:
                    require(wait["wait_uuid"] not in observed, "wait duplicated across pages")
                    observed[wait["wait_uuid"]] = wait
                cursor = body["waits"]["next_cursor"]
                if not cursor:
                    break
                require(cursor not in cursors and len(cursors) < 10, "wait cursor repeats or is unbounded")
                cursors.add(cursor)
            require(set(observed) == self.expected_waits and not set(observed).intersection(self.unrelated_waits),
                    "wait membership differs from exact explicit associations")
            for wait_id, wait in observed.items():
                require({(a["association_id"], a["revision"]) for a in wait["associations"]} == self.expected_associations[wait_id],
                        "wait association provenance differs from retained exact edges")
            recipients = body["quiet_notices"]["recipients"]
            require(len(recipients) == 1 and recipients[0]["agent_id"] == self.owner["agent_id"]
                    and recipients[0]["execution_id"] == self.owner["execution_id"]
                    and recipients[0]["support"] == "unsupported", "ordinary fixture session incorrectly reports native support")
            require(body["quiet_notices"]["items"] == [] and body["quiet_notices"]["queued_count"] == 0,
                    "unrelated or invented notice backlog returned")
            page.locator(f'.work-card[data-item-uuid="{self.item_id}"]').click()
            expect(page.locator("#coordination-observed")).to_contain_text("Observed")
            if name == "coordination_empty":
                expect(page.locator("#coordination-waits-empty")).to_be_visible()
            else:
                expect(page.locator(".coordination-wait")).to_have_count(25)
                first_ids = set(page.locator(".coordination-wait").evaluate_all("nodes => nodes.map(node => node.dataset.waitId)"))
                page.locator("#coordination-waits-more").click()
                expect(page.locator("#coordination-waits-page")).to_have_text("Page 2")
                expect(page.locator(".coordination-wait")).to_have_count(3)
                second_ids = set(page.locator(".coordination-wait").evaluate_all("nodes => nodes.map(node => node.dataset.waitId)"))
                require(first_ids.isdisjoint(second_ids) and first_ids | second_ids == self.expected_waits,
                        "browser wait pages omit or duplicate exact rows")
                expect(page.locator("#coordination-notices-page")).to_have_text("Page 1")
                page.locator("#coordination-waits-first").click()
                expect(page.locator("#coordination-waits-page")).to_have_text("Page 1")
            expect(page.locator("#coordination-notices-empty")).to_be_visible()
            expect(page.locator(".coordination-recipient")).to_contain_text("unsupported")
            if name == "coordination_disabled":
                expect(page.locator("#coordination-waits-status")).to_contain_text("disabled")
                expect(page.locator("#coordination-notices-status")).to_contain_text("disabled")
            require("COORDINATION_SOURCE_BODY_MUST_NOT_RENDER" not in page.content(), "browser rendered source body")
            page.locator("#item-coordination-title").scroll_into_view_if_needed()
            page.screenshot(path=str(self.output / (name + "-" + viewport + ".png")), full_page=True)
            require(self.coordination_state() == before, "coordination GET/view mutated waits, outboxes, notices or sessions")

        def run_state(self, browser, name):
            self.seed(name)
            self.start_web()
            with browser.new_context(viewport=dict(width=1440, height=1000)) as context:
                page = self.login(context)
                storage = context.storage_state()
                for viewport, size in (("desktop", dict(width=1440, height=1000)), ("phone", dict(width=390, height=844))):
                    with browser.new_context(viewport=size, storage_state=storage) as view:
                        current = view.new_page()
                        current.goto(self.url)
                        self.check("real coordination projection " + viewport,
                                   lambda: self.browser_projection(current, name, viewport), current)
                self.check("UIPass " + name, lambda: self.uipass(name, storage))

        def prepare_manifest(self, name, manifest):
            for state in manifest["surfaces"][name]["states"].values():
                state["actions"][0]["selector"] = f'.work-card[data-item-uuid="{self.item_id}"]'

    return CoordinationFixture


def make_native_queued_case(Base, fixture, browser, uid, dump, require):
    """Build the native proof's bounded callback; never own or close its host.

    The caller must use the same composed candidate and run the verified native
    scenario's queued_case variant. This factory does not provision capability.
    """
    class BorrowedView:
        start_web = Base.start_web
        stop_web = Base.stop_web
        login = Base.login
        work = Base.work

        def __init__(self, host):
            self.host = copy.copy(host)
            self.host.env = dict(host.env, **{name: fixture.env[name] for name in (
                "PLANE_CONFIG", "CAIRN_PLANE_BIN", "CAIRN_WORK_UI_PLANE_STATE")})
            self.binary = host.binary
            self.collection = host.repo
            self.server = None

    def queued_case(host, session):
        from playwright.sync_api import expect
        started = time.monotonic()
        require(hashlib.sha256(host.binary.read_bytes()).hexdigest() == fixture.report["binary_sha256"],
                "native callback host differs from the composed UI candidate")
        view = BorrowedView(host)
        slug = "native-quiet-view"
        project_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
        workspace = fixture.state["workspace"]
        fixture.state["projects"][project_id] = dict(name="Native quiet view", identifier="NQV", mode="normal", items=[
            dict(id=item_id, sequence_id=1, name="Native quiet panel", workspace=workspace, project=project_id,
                 state=dict(id=uid("started"), name="Started", group="started"), assignees=[],
                 updated_at=datetime.now(timezone.utc).isoformat(), project_detail=dict(id=project_id, identifier="NQV"))])
        dump(fixture.state_path, fixture.state)
        queued_report = dict(variant="queued_case", whole_coordination_acceptance=False,
                            setup="actual native PTY activation and API publication", screenshots=[])
        fixture.report["native_queued_panel"] = queued_report
        try:
            view.work("features", "enable", "--feature", "work-view/1")
            view.work("project", "add", "--slug", slug, "--workspace", workspace, "--project", project_id,
                      "--origin", fixture.state["origin"], "--instance", "work-ui")
            view.work("refresh", "--project", slug)
            pending = host.agent("sender", "event-notice-pending", dict(repo=host.repo, agent_id=session["agent_id"], limit=100))
            require(pending["supported"] is True and pending["count"] == 4 and len(pending["notices"]) == 4
                    and all(n["state"] == "queued" for n in pending["notices"]),
                    "callback did not receive four genuinely supported queued notices")
            selected = min(pending["notices"], key=lambda notice: (notice["created_at"], notice["notice_id"]))
            association = view.work("associate", "--project", slug, "--item", item_id, "--target-type", "session",
                                   "--target-id", session["agent_id"] + ":" + session["execution_id"])
            explicit = view.work("associate", "--project", slug, "--item", item_id, "--target-type", "event",
                                "--target-id", selected["event_id"])
            source = host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(kind="note",
                body="NATIVE_PANEL_SOURCE_NEVER_RENDERED", scope=dict(repo=host.repo, task_id="*", run_id="*"),
                claim_type="self", sensitivity="shareable")))
            for _ in range(22):
                require(time.monotonic() - started < 20, "native panel setup exhausted its bounded callback time")
                host.agent("sender", "event-publish", dict(request_id=str(uuid.uuid4()), repo=host.repo, kind="notice",
                    ref={key: source[key] for key in ("record_id", "version")},
                    destination=dict(type="agent", name=selected["recipient_inbox"]), notice_delivery="next_organic_turn",
                    notice_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()))
            pending = host.agent("sender", "event-notice-pending", dict(repo=host.repo, agent_id=session["agent_id"], limit=100))
            expected = {n["notice_id"] for n in pending["notices"]}
            require(len(expected) == 26 and pending["count"] == 26, "native publication did not retain 26 exact queued notices")
            require(host.sql("SELECT count(*) FROM cairn.agent_delivery") == "0", "quiet setup created ordinary delivery")
            before = host.sql("SELECT jsonb_agg(to_jsonb(n) ORDER BY notice_id) FROM cairn.agent_organic_notice n")
            view.start_web()
            with browser.new_context(viewport=dict(width=1440, height=1000)) as login:
                login.set_default_timeout(5000)
                view.login(login)
                storage = login.storage_state()
                for viewport, size in (("desktop", dict(width=1440, height=1000)), ("phone", dict(width=390, height=844))):
                    require(time.monotonic() - started < 35, "native panel browser exhausted its bounded callback time")
                    with browser.new_context(viewport=size, storage_state=storage) as context:
                        context.set_default_timeout(5000)
                        page = context.new_page()
                        page.goto(view.url)
                        page.locator(f'.work-card[data-item-uuid="{item_id}"]').click()
                        expect(page.locator("#coordination-queue-summary")).to_contain_text("26 queued")
                        expect(page.locator(".coordination-recipient")).to_contain_text("Quiet-notice support: supported")
                        expect(page.locator(".coordination-notice")).to_have_count(25)
                        first = set(page.locator(".coordination-notice").evaluate_all("nodes => nodes.map(node => node.dataset.noticeId)"))
                        page.locator("#annotation-input").fill("Unsaved native panel note " + viewport)
                        page.locator("#coordination-notices-more").click()
                        expect(page.locator("#coordination-notices-page")).to_have_text("Page 2")
                        expect(page.locator("#coordination-waits-page")).to_have_text("Page 1")
                        expect(page.locator(".coordination-notice")).to_have_count(1)
                        second = set(page.locator(".coordination-notice").evaluate_all("nodes => nodes.map(node => node.dataset.noticeId)"))
                        require(first.isdisjoint(second) and first | second == expected, "native notice pages changed exact membership")
                        expect(page.locator("#annotation-input")).to_have_value("Unsaved native panel note " + viewport)
                        require(selected["notice_id"] in second, "oldest notice did not remain on the final descending page")
                        row = page.locator(f'.coordination-notice[data-notice-id="{selected["notice_id"]}"]')
                        expect(row).to_contain_text(association["association_id"])
                        expect(row).to_contain_text(explicit["association_id"])
                        expect(row).to_contain_text("Backlog for linked conversation")
                        expect(row).to_contain_text("Linked event")
                        require("NATIVE_PANEL_SOURCE_NEVER_RENDERED" not in page.content(), "native notice source body rendered")
                        page.locator(".coordination-notice summary").click()
                        page.locator("#coordination-notices-title").scroll_into_view_if_needed()
                        screenshot = "native-queued-panel-" + viewport + ".png"
                        page.screenshot(path=str(fixture.output / screenshot), full_page=True)
                        queued_report["screenshots"].append(screenshot)
                require(host.sql("SELECT jsonb_agg(to_jsonb(n) ORDER BY notice_id) FROM cairn.agent_organic_notice n") == before
                        and host.sql("SELECT count(*) FROM cairn.agent_delivery") == "0", "panel reads changed native queue/delivery state")
            queued_report.update(status="checks_complete", elapsed_seconds=time.monotonic() - started,
                covered=["real supported queue", "exact conversation/event provenance", "independent notice pagination",
                         "preserved draft", "read-only queue and delivery state"],
                pending=["flush/forget transitions", "admission/history/restore", "expiry", "UIPass and actual screenshot review"])
        finally:
            view.stop_web()
        queued_report["status"] = "passed"

    return queued_case
