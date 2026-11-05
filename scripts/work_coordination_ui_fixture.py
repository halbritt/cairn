#!/usr/bin/env python3
"""WC01 CLI/browser acceptance against a built candidate and disposable PostgreSQL.

Run with the UIPass Python environment; see tests/fixtures/work_ui/README.md.
No Cairn HTTP response is mocked. A failure is retained and exits nonzero.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

import disposable_host
from disposable_host import DisposableHost, OwnedCluster, HostRefusal, checked, require, stop_process


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tests/fixtures/work_ui"
ORIGIN = "https://plane.fixture.invalid"
NAMESPACE = uuid.UUID("f431dcf6-f8f9-4bb9-8905-10b2e24671ab")
LONG_TITLE = 'Untrusted <img src=x onerror="window.workUiInjected=true"> ' + "long-title-" * 35
STATES = ("workflow", "dense", "empty", "empty_failed", "stale", "auth_rejected", "unavailable", "typed")
WEB_FILES = ("web/workview/app.js", "web/workview/index.html", "web/workview/styles.css")
EXTERNAL_FIELDS = ("identifier", "title", "state_id", "state_name", "state_group",
                   "assignee_ids", "assignee_names", "updated_at_source", "source_url")


def uid(name):
    return str(uuid.uuid5(NAMESPACE, name))


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def snapshot_projection(listing):
    items = listing["items"]
    require(isinstance(items, list) or (items is None and listing["total"] == 0),
            "snapshot items are missing despite a nonempty total")
    return dict(total=listing["total"], items=[{field: item.get(field) for field in
        ("plane_item_uuid", "cache_generation", "is_absent", *EXTERNAL_FIELDS)}
        for item in (items or [])])


def text_values(value):
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        return set().union(*(text_values(item) for item in value))
    return set()


def source_identity(candidate, capture_manifest=None):
    if capture_manifest:
        raw = capture_manifest.read_bytes()
        capture = json.loads(raw)
        require(capture.get("schema") == "cairn.work-ui-source/1"
                and capture.get("source_path") == str(candidate.resolve()), "source capture manifest does not bind this directory")
        identity = capture["source"]
        paths = [path for path in candidate.rglob("*") if path.is_file()]
        require(not any(path.is_symlink() for path in candidate.rglob("*")), "source capture contains a symlink")
        observed = {str(path.relative_to(candidate)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        require(observed == identity["inputs"], "immutable source capture differs from its retained manifest")
        require(hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest() == identity["input_sha256"],
                "immutable source capture digest is invalid")
        return dict(identity, capture_manifest_sha256=hashlib.sha256(raw).hexdigest(), immutable_capture=True)
    head = checked(["git", "rev-parse", "HEAD"], "candidate revision", cwd=candidate).stdout.decode().strip()
    dirty = checked(["git", "status", "--porcelain"], "candidate status", cwd=candidate).stdout.decode()
    paths = checked(["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
                    "candidate inputs", cwd=candidate).stdout.decode().split("\0")
    inputs = {}
    for name in sorted(set(paths)):
        if name:
            path = candidate / name
            if path.is_file():
                inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return dict(revision=head, dirty=dirty.splitlines(), input_sha256=digest, inputs=inputs)


class Fixture:
    states = STATES

    def __init__(self, args):
        self.args = args
        require(not args.output.is_symlink(), "output must not be a symlink")
        self.output = args.output.resolve()
        require(not self.output.exists() or not any(self.output.iterdir()), "output must be new or empty")
        require(not self.output.is_relative_to(ROOT) and not self.output.is_relative_to(args.candidate.resolve()),
                "output must be outside both source checkouts")
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output.chmod(0o700)
        self.report = dict(schema="cairn.work-ui-proof/1", candidate=str(args.candidate.resolve()),
                           checks=[], uipass=[], visual_review="pending actual screenshot inspection",
                           state_results={name: "not_run" for name in self.states},
                           selected_scope=args.scope,
                           scope="Real Cairn CLI/Store/browser; synthetic external Plane CLI only")
        self.report["fixture_inputs"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__).resolve(), Path(disposable_host.__file__).resolve(),
                         *sorted(ASSETS.glob("*.py")), ROOT / ".uipass-work-coordination.yaml")}
        self.runtime = Path(tempfile.mkdtemp(prefix="cairn-work-ui."))
        self.cluster = None
        self.host = None
        self.server = None
        self.state = {"origin": ORIGIN, "workspace": uid("workspace"), "projects": {}}
        self.state_path = self.runtime / "plane-state.json"
        self.binary = (self.output if args.retain_binary else self.runtime) / "cairn"
        self.report["binary_path"] = str(self.binary)
        self.report["binary_retained"] = args.retain_binary
        self.slug = "work-ui"
        self.collection = None
        self.item_id = None
        self.guard_item_id = None
        self.blocked_item_id = None
        self.request_event_id = None
        self.typed_targets = {}
        self.session = None

    def check(self, name, operation, page=None, category="acceptance"):
        try:
            operation()
            self.report["checks"].append(dict(name=name, category=category, status="passed"))
            return True
        except Exception as error:
            item = dict(name=name, category=category, status="failed", error=str(error)[:1800])
            if page is not None:
                path = self.output / (re.sub(r"[^a-z0-9_-]", "-", name.lower()) + ".png")
                try:
                    page.screenshot(path=str(path), full_page=True)
                    item["screenshot"] = path.name
                except Exception as capture_error:
                    item["screenshot_error"] = type(capture_error).__name__
            self.report["checks"].append(item)
            return False

    def work(self, *args, expected="OK"):
        return self.host.command(["work", *args, "--collection", self.collection], expected=expected)

    def detail(self):
        return self.work("show", "--project", self.slug, "--item", self.item_id)

    def plane_calls(self):
        path = self.state_path.with_suffix(".calls.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def coordination_state(self):
        # Observe actual rows so a lease/presence edit cannot hide behind stable counts.
        state = self.host.sql("""SELECT json_build_object(
            'sessions', (SELECT jsonb_agg(to_jsonb(s) ORDER BY agent_id) FROM cairn.agent_session s),
            'events', (SELECT jsonb_agg(to_jsonb(e) ORDER BY event_id) FROM cairn.agent_event e),
            'deliveries', (SELECT jsonb_agg(to_jsonb(d) ORDER BY delivery_id) FROM cairn.agent_delivery d))""")
        return hashlib.sha256(state.encode()).hexdigest()

    def register(self):
        return self.host.command(["agents", "register", *self.host.connection("worker"),
            "--request-id", str(uuid.uuid4()), "--binding", "work-ui", "--native-session", str(uuid.uuid4()),
            "--harness", "fixture", "--project", "cairn-work-ui", "--workspace", str(self.runtime),
            "--task", "Selected synthetic work summary", "--repo", self.collection])

    def seed(self, state_name):
        self.collection = "fixture:work-ui:" + state_name
        self.host = DisposableHost.start(self.cluster, binary=self.binary, repo=self.collection,
            database="work_ui_" + state_name, home=self.runtime / (state_name + "-home"),
            destination="local", label="work_ui_" + state_name, env=self.env)
        project_id = uid(state_name + ":project")
        count = self.item_count(state_name)
        items = []
        for index in range(count):
            group = "started" if state_name == "workflow" or index % 2 == 0 else "backlog"
            title = ("Source retention task" if state_name == "unavailable"
                     else f"{state_name} task {index + 1:03}")
            items.append(dict(id=uid(f"{state_name}:item:{index}"), sequence_id=index + 1,
                name=LONG_TITLE if state_name == "dense" and index == 0 else title,
                workspace=self.state["workspace"], project=project_id,
                state=dict(id=uid(group), name=group.title(), group=group),
                assignees=[dict(id=uid("assignee"), display_name="Synthetic external assignee")],
                updated_at="2026-11-05T12:00:00Z",
                project_detail=dict(id=project_id, identifier="WUI")))
        self.item_id = items[0]["id"] if items else None
        self.guard_item_id = items[1]["id"] if state_name == "workflow" else None
        project = dict(name="Synthetic work UI " + state_name, identifier="WUI", mode="normal", items=items)
        self.state["projects"][project_id] = project
        dump(self.state_path, self.state)
        self.work("project", "add", "--slug", self.slug, "--workspace", self.state["workspace"],
                  "--project", project_id, "--origin", ORIGIN, "--instance", "work-ui")
        self.work("refresh", "--project", self.slug)
        listing = self.work("list", "--project", self.slug, "--limit", "100")
        require(listing["total"] == count, "seed refresh did not persist the exact Plane item count")
        if state_name == "workflow":
            self.session = self.register()
        if state_name == "dense":
            # WUI-99 lies beyond the first page in the fixture's identifier order.
            # Blocker filtering must search the complete project before paging.
            self.blocked_item_id = items[98]["id"]
            self.work("blocker", "add", "--project", self.slug, "--item", self.blocked_item_id,
                      "--text", "Selected dense fixture blocker")
        if state_name == "stale":
            # Only a clock boundary is staged directly; the failed refresh is real.
            self.host.sql("UPDATE cairn.work_project SET last_successful_refresh_at=clock_timestamp()-interval '2 days'")
            self.host.sql("""UPDATE cairn.work_item i SET last_refreshed_at=p.last_successful_refresh_at
                FROM cairn.work_project p WHERE i.binding_id=p.binding_id""")
            before = self.work("list", "--project", self.slug)
            require(all(item["is_stale"] for item in before["items"]),
                    "private clock fixture did not age the item observations")
            observed = self.work("project", "show", "--project", self.slug)["last_successful_refresh_at"]
            self.age_refresh_window()
            project["mode"] = "network"
            dump(self.state_path, self.state)
            call_count = len(self.plane_calls())
            refusal = self.work("refresh", "--project", self.slug, expected="INVALID_REQUEST")
            require("synthetic Plane connection failure" in refusal.get("message", ""),
                    "refresh refusal did not report the supplied transport failure")
            self.verify_failed_refresh(project_id, before, observed, call_count)
        if state_name == "auth_rejected":
            original = self.work("list", "--project", self.slug)["items"]
            project["mode"] = "auth"
            dump(self.state_path, self.state)
            self.age_refresh_window()
            self.work("refresh", "--project", self.slug, expected="AUTHORITY_DENIED")
            hidden = self.work("list", "--project", self.slug)
            require(self.work("project", "show", "--project", self.slug)["auth_rejected"] is True,
                    "auth rejection did not retain the restricted state")
            masked = {item["plane_item_uuid"]: item for item in hidden["items"]}
            for item in original:
                for field in EXTERNAL_FIELDS:
                    require(not text_values(item.get(field)).intersection(
                                text_values(masked.get(item["plane_item_uuid"], {}).get(field))),
                            "auth rejection retained original external field: " + field)
        if state_name == "unavailable":
            self.seed_forgotten_request()
        if state_name == "typed":
            self.seed_typed_targets()

    def item_count(self, name):
        return 125 if name == "dense" else 0 if name in ("empty", "empty_failed") else 2 if name == "workflow" else 1

    def age_refresh_window(self):
        project = self.work("project", "show", "--project", self.slug)
        require(project.get("refresh_in_flight") is False,
                "cannot stage the next refresh time while a refresh is in flight")
        self.host.sql("UPDATE cairn.work_project SET next_refresh_allowed_at=clock_timestamp()-interval '1 second'")
        self.report.setdefault("clock_staging", []).append(dict(collection=self.collection,
            field="next_refresh_allowed_at", purpose="allow the next explicit fixture refresh; claim state unchanged"))

    def verify_failed_refresh(self, project_id, before, last_success, call_count):
        require(any(call["project_id"] == project_id and call["mode"] == "network"
                    and call["exit_code"] == 6 and call["error_code"] == "network"
                    for call in self.plane_calls()[call_count:]),
                "refresh did not reach the intended Plane transport failure")
        require(snapshot_projection(self.work("list", "--project", self.slug)) == snapshot_projection(before),
                "failed refresh overwrote the prior snapshot generation or external metadata")
        failed = self.work("project", "show", "--project", self.slug)
        require(failed["last_successful_refresh_at"] == last_success,
                "failed refresh advanced the last successful observation")
        require(failed.get("last_refresh_error") and failed.get("last_refresh_error_at"),
                "failed refresh was not retained on the project")

    def browser_failed_refresh(self, page):
        from playwright.sync_api import expect
        before = self.work("list", "--project", self.slug)
        project = self.work("project", "show", "--project", self.slug)
        self.state["projects"][project["project_uuid"]]["mode"] = "network"
        dump(self.state_path, self.state)
        self.age_refresh_window()
        page.reload()
        call_count = len(self.plane_calls())
        with page.expect_response(lambda response: "/api/work/refresh" in response.url
                                  and response.request.method == "POST") as pending:
            page.locator("#refresh-btn").click()
        response = pending.value
        require(response.status == 400 and response.json().get("code") == "INVALID_REQUEST"
                and "synthetic Plane connection failure" in response.json().get("error", ""),
                "browser refresh did not report the supplied transport refusal: " + response.text()[:300])
        self.verify_failed_refresh(project["project_uuid"], before, project["last_successful_refresh_at"], call_count)
        expect(page.locator("#view-error")).to_contain_text("synthetic Plane connection failure")
        expect(page.locator("#refresh-error")).to_contain_text("Refresh failed")
        expect(page.locator("#refresh-btn")).to_be_disabled()

    def seed_typed_targets(self):
        self.session = self.register()
        record = self.host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
            kind="note", body="Synthetic typed view evidence TYPED_SOURCE_BODY_NEVER_RENDERED", claim_type="self",
            sensitivity="local", scope=dict(repo=self.collection, task_id="*", run_id="*"))))
        group = self.host.agent("sender", "event-publish", dict(request_id=str(uuid.uuid4()), kind="request",
            repo=self.collection, ref=dict(record_id=record["record_id"], version=record["version"]),
            destination=dict(type="agent", name=self.session["inbox"]), response_group=dict(
                deadline=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(), partial_policy="all")))
        process = subprocess.run([str(self.binary), "run", "--repo", self.collection, "--dir", str(self.host.home),
            "--task", "work-ui", "--run", str(uuid.uuid4()), "--prompt", "Synthetic typed view evidence",
            "--destination", "local", "--timeout", "3s", "--", "/bin/sh", "-c", "cat >/dev/null"],
            env=self.host.env, capture_output=True, timeout=15)
        # Native run reserves stdout for the child and emits its terminal
        # envelope on stderr (cmd/cairn/main.go). This child emits no bytes.
        require(process.returncode == 0 and not process.stdout and len(process.stderr) <= 100_000,
                "real fixture run failed: " + disposable_host.excerpt(process.stderr))
        envelope = disposable_host.decode_json(process.stderr)
        require(envelope.get("schema") == "cairn.response/1" and envelope.get("ok") is True
                and envelope.get("status") == "OK", "real run terminal envelope did not confirm success")
        run = envelope["data"]
        require(run.get("exit_code") == 0 and run.get("receipt_id"), "real fixture runner did not return a receipt")
        self.typed_targets = dict(session=self.session["agent_id"] + ":" + self.session["execution_id"],
            event=group["event_id"], response_group=group["event_id"],
            result_record=record["record_id"] + ":" + str(record["version"]), run_receipt=run["receipt_id"])
        for kind, target in self.typed_targets.items():
            self.work("associate", "--project", self.slug, "--item", self.item_id,
                      "--target-type", kind, "--target-id", target, "--rationale", "Selected typed view fixture link")
        detail = self.detail()
        require(all(detail.get(key) for key in ("sessions", "requests", "response_groups", "result_records", "run_receipts")),
                "real targets are missing from typed current projections")
        require(detail["requests"][0]["deliveries"], "direct group request has no projected delivery")
        require(detail["response_groups"][0]["state"] == "open", "unanswered real response group is not open")
        require(detail["result_records"][0]["payload_available"] is True, "active exact result version is unavailable")
        require(detail["run_receipts"][0]["status"] != "unknown"
                and detail["run_receipts"][0]["assessment_outcome"] != "accepted",
                "real unassessed process receipt is missing or incorrectly implies acceptance")

    def seed_forgotten_request(self):
        self.session = self.register()
        record = self.host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
            kind="note", body="SYNTHETIC_FORGOTTEN_PAYLOAD", claim_type="self", sensitivity="local",
            scope=dict(repo=self.collection, task_id="work-ui", run_id="unavailable"))))
        event = self.host.agent("sender", "event-publish", dict(request_id=str(uuid.uuid4()), kind="request",
            repo=self.collection, ref=dict(record_id=record["record_id"], version=record["version"]),
            destination=dict(type="agent", name=self.session["inbox"])))
        self.request_event_id = event["event_id"]
        self.work("associate", "--project", self.slug, "--item", self.item_id,
                  "--target-type", "event", "--target-id", event["event_id"], "--rationale", "Linked request fixture")
        grant = self.host.operator("bootstrap", dict(request_id=str(uuid.uuid4()), reason="Disposable UI deletion fixture"))
        preview = self.host.operator("preview-delete", args=[record["record_id"]])
        self.host.operator("forget", dict(request_id=str(uuid.uuid4()), record_id=record["record_id"],
            expected_version=record["version"], grant_id=grant["grant_id"], preview_id=preview["preview_id"]))
        detail = self.detail()
        request = next((r for r in detail["requests"] if r["event_id"] == self.request_event_id), None)
        require(request is not None and request["payload_available"] is False,
                "forgotten request is not reported unavailable")
        require("SYNTHETIC_FORGOTTEN_PAYLOAD" not in json.dumps(detail), "forgotten payload escaped into work detail")

    def cli_journey(self):
        text = "CLI selected progress"
        progress = self.work("progress", "add", "--project", self.slug, "--item", self.item_id, "--text", text)
        edited = self.work("progress", "edit", "--project", self.slug, "--annotation-id", progress["annotation_id"],
                          "--item", self.item_id, "--text", text + " revised", "--expected-revision", str(progress["revision"]))
        require(edited["revision"] > progress["revision"], "CLI progress edit did not advance revision")
        blocker = self.work("blocker", "add", "--project", self.slug, "--item", self.item_id, "--text", "CLI blocker")
        self.work("blocker", "resolve", "--project", self.slug, "--annotation-id", blocker["annotation_id"],
                  "--item", self.item_id, "--expected-revision", str(blocker["revision"]))
        target = self.session["agent_id"] + ":" + self.session["execution_id"]
        association = self.work("associate", "--project", self.slug, "--item", self.item_id,
                               "--target-type", "session", "--target-id", target, "--rationale", "CLI exact execution")
        require(association["target_id"] == target, "CLI association changed the exact target")
        self.work("unassociate", "--project", self.slug, "--association-id", association["association_id"],
                  "--item", self.item_id, "--expected-revision", str(association["revision"]))
        detail = self.detail()
        require(any(a["body"] == text + " revised" for a in detail["annotations"]), "CLI edited text not retained")
        require(any(a["annotation_id"] == blocker["annotation_id"] and a["state"] == "resolved"
                    for a in detail["annotations"]), "CLI blocker resolution not retained")
        require(not any(a["association_id"] == association["association_id"] and a["state"] == "active"
                        for a in detail["associations"]), "CLI unassociation not retained")

    def start_web(self):
        self.server = subprocess.Popen([str(self.binary), "work", "serve", "--port", "0", "--collection", self.collection],
            env=self.host.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines, deadline = bytearray(), time.monotonic() + 15
        while time.monotonic() < deadline:
            require(self.server.poll() is None, "work server exited before terminal code")
            ready, _, _ = select.select([self.server.stdout], [], [], 0.2)
            if ready:
                lines.extend(os.read(self.server.stdout.fileno(), 4096))
                require(len(lines) < 8192, "unexpected work server startup output")
                address = re.search(rb"http://127\.0\.0\.1:[0-9]+/", lines)
                code = re.search(rb"launch code: ([A-Za-z0-9_-]+)", lines)
                if address and code:
                    self.url = address.group().decode().rstrip("/")
                    self.code = code.group(1).decode()
                    return
        raise HostRefusal("work server did not print its loopback URL and one-time code")

    def stop_web(self):
        if self.server is not None:
            stop_process(self.server, timeout=5, label="work UI server")
            self.server = None

    def login(self, context):
        page = context.new_page()
        page.goto(self.url)
        page.locator("#login-form").wait_for(state="visible")
        page.locator("#launch-code-input").fill(self.code)
        with page.expect_response(lambda response: response.url.endswith("/api/auth/login") and response.request.method == "POST") as pending:
            page.locator("#login-form button[type=submit]").click()
        response = pending.value
        require(response.status == 200, "terminal-code login POST failed")
        page.locator("#login-form").wait_for(state="hidden")
        require(self.code not in page.url and self.code not in page.content(), "launch code remained in page/URL")
        require(page.locator("#launch-code-input").input_value() == "", "launch code remained in the input after login")
        cookies = context.cookies()
        require(any(c["name"] == "work_session" and c["httpOnly"] and c["sameSite"] == "Strict" for c in cookies),
                "browser session cookie lacks required protections")
        self.code = None
        return page

    def activate_card_by_keyboard(self, page, item_id=None):
        card = page.locator(f'.work-card[data-item-uuid="{item_id}"]') if item_id else page.locator(".work-card").first
        card.wait_for(state="visible")
        for _ in range(80 if item_id else 40):
            page.keyboard.press("Tab")
            if card.evaluate("e => e === document.activeElement || e.contains(document.activeElement)"):
                page.keyboard.press("Enter")
                page.locator("#detail-modal").wait_for(state="visible")
                return
        raise HostRefusal("work card cannot be reached by keyboard Tab navigation")

    def browser_mutations(self, page, viewport):
        from playwright.sync_api import expect
        prefix = "Browser " + viewport
        self.age_refresh_window()
        page.reload()
        before = self.work("project", "show", "--project", self.slug)["refresh_sequence"]
        with page.expect_response(lambda response: "/api/work/refresh" in response.url) as pending:
            page.locator("#refresh-btn").click()
        require(pending.value.ok, "browser refresh returned HTTP " + str(pending.value.status))
        require(self.work("project", "show", "--project", self.slug)["refresh_sequence"] > before,
                "browser refresh did not commit a new generation")
        expect(page.locator("#refresh-btn")).to_be_disabled()
        expect(page.locator("#refresh-availability")).to_contain_text("Refresh available after")
        self.activate_card_by_keyboard(page)
        expect(page.locator("#modal-close")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        require(page.locator("#detail-modal").evaluate("e => e.contains(document.activeElement)"),
                "reverse Tab escaped the open detail modal")
        page.keyboard.press("Tab")
        expect(page.locator("#modal-close")).to_be_focused()
        page.locator("#annotation-input").fill(prefix + " progress")
        self.browser_write(page, "/api/work/progress/add", lambda: page.locator("#add-progress-btn").click())
        page.get_by_text(prefix + " progress", exact=False).wait_for()
        detail = self.detail()
        progress = next((a for a in detail["annotations"] if a["body"] == prefix + " progress"), None)
        require(progress is not None, "browser progress was displayed without persistence")
        if viewport == "desktop":
            self.http_guards(page, "/api/work/progress/edit", "annotations", "annotation_id", progress,
                             dict(text="Guard refusal must not replace progress"))
        row = page.locator(f'.annot-item[data-annotation-id="{progress["annotation_id"]}"]')
        row.locator(".edit-progress-btn").click()
        row.locator(".edit-progress-input").fill(prefix + " progress revised")
        self.browser_write(page, "/api/work/progress/edit", lambda: row.locator(".save-progress-btn").click())
        page.get_by_text(prefix + " progress revised", exact=False).wait_for()
        revised = next(a for a in self.detail()["annotations"] if a["annotation_id"] == progress["annotation_id"])
        require(revised["body"] == prefix + " progress revised" and revised["revision"] > progress["revision"],
                "browser progress edit not persisted with a new revision")
        if viewport == "desktop":
            self.http_guards(page, "/api/work/progress/edit", "annotations", "annotation_id", revised,
                dict(text="Stale edit must not replace revised progress"), variants=(
                    ("stale prior revision", {"expected_revision": progress["revision"]}, 409, "VERSION_CONFLICT"),))
        page.locator("#annotation-input").fill(prefix + " blocker")
        self.browser_write(page, "/api/work/blocker/add", lambda: page.locator("#add-blocker-btn").click())
        blocker = next(a for a in self.detail()["annotations"] if a["body"] == prefix + " blocker")
        if viewport == "desktop":
            self.http_guards(page, "/api/work/blocker/resolve", "annotations", "annotation_id", blocker)
        row = page.locator(f'.annot-item[data-annotation-id="{blocker["annotation_id"]}"]')
        self.browser_write(page, "/api/work/blocker/resolve", lambda: row.locator(".resolve-blocker-btn").click())
        expect(row).to_contain_text(re.compile("resolved", re.I))
        require(any(a["annotation_id"] == blocker["annotation_id"] and a["state"] == "resolved"
                    for a in self.detail()["annotations"]), "browser blocker resolution not persisted")
        target = self.session["agent_id"] + ":" + self.session["execution_id"]
        page.locator("#assoc-target-type").select_option("session")
        page.locator("#assoc-target-id").fill(target)
        page.locator("#assoc-rationale").fill(prefix + " exact execution")
        result = self.browser_write(page, "/api/work/associate", lambda: page.locator("#add-assoc-btn").click())
        association = next((a for a in self.detail()["associations"] if a["association_id"] == result["association_id"]), None)
        require(association is not None and association["target_id"] == target and association["state"] == "active"
                and association["rationale"] == prefix + " exact execution",
                "browser exact execution association and selected rationale not persisted")
        if viewport == "desktop":
            self.http_guards(page, "/api/work/unassociate", "associations", "association_id", association)
        row = page.locator(f'.assoc-item[data-association-id="{association["association_id"]}"]')
        self.browser_write(page, "/api/work/unassociate", lambda: row.locator(".remove-assoc-btn").click())
        require(not any(a["association_id"] == association["association_id"] and a["state"] == "active"
                        for a in self.detail()["associations"]), "browser unassociation not persisted")
        row.locator(".remove-assoc-btn").wait_for(state="hidden")
        page.keyboard.press("Escape")
        page.locator("#detail-modal").wait_for(state="hidden")
        expect(page.locator(f'.work-card[data-item-uuid="{self.item_id}"]')).to_be_focused()

    def browser_write(self, page, path, action):
        with page.expect_response(lambda response: response.url.split("?", 1)[0] == self.url + path
                                 and response.request.method == "POST") as pending:
            action()
        response = pending.value
        require(response.ok, path + " returned HTTP " + str(response.status)
                + (": " + response.text()[:400] if not response.ok else ""))
        return response.json()

    def http_guards(self, page, path, section, id_field, entity, extra=None, variants=None):
        session = page.request.get(self.url + "/api/auth/session")
        require(session.ok and session.json().get("authenticated") is True, "guard test lacks a real operator session")
        token = session.json()["csrf_token"]
        base = dict(project=self.slug, item_uuid=self.item_id, expected_revision=entity["revision"],
                    **{id_field: entity[id_field]}, **(extra or {}))
        variants = variants or (
            ("missing item", {"item_uuid": None}, 400, "INVALID_REQUEST"),
            # The API also accepts Plane identifiers; an unknown non-UUID must
            # resolve to NOT_FOUND, never become an omitted item guard.
            ("unknown non-UUID item", {"item_uuid": "not-a-uuid"}, 404, "NOT_FOUND"),
            ("zero UUID item", {"item_uuid": str(uuid.UUID(int=0))}, 404, "NOT_FOUND"),
            ("wrong item", {"item_uuid": self.guard_item_id}, 404, "NOT_FOUND"),
            ("missing revision", {"expected_revision": None}, 400, "INVALID_REQUEST"),
            ("zero revision", {"expected_revision": 0}, 400, "INVALID_REQUEST"),
            ("negative revision", {"expected_revision": -1}, 400, "INVALID_REQUEST"),
            ("stale revision" if entity["revision"] > 1 else "noncurrent revision",
             {"expected_revision": entity["revision"] - 1 if entity["revision"] > 1 else 2}, 409, "VERSION_CONFLICT"),
        )
        for label, changes, status, code in variants:
            body = dict(base, request_id=str(uuid.uuid4()))
            for key, value in changes.items():
                if value is None:
                    body.pop(key)
                else:
                    body[key] = value
            response = page.request.post(self.url + path, data=body,
                headers={"Origin": self.url, "X-CSRF-Token": token})
            after = next((row for row in self.detail()[section] if row[id_field] == entity[id_field]), None)
            name = path + " refuses " + label
            require(after == entity, name + ": exact retained row changed after HTTP " + str(response.status))
            require(response.status == status and response.json().get("code") == code,
                    name + ": expected " + code + ", got HTTP " + str(response.status) + " " + response.text()[:300])
            self.report["checks"].append(dict(name=name, category="acceptance", status="passed"))

    def visual_state_assertions(self, page, name, viewport="desktop"):
        from playwright.sync_api import expect
        page.locator("#project-select").select_option(self.slug)
        if name == "empty":
            expect(page.locator(".work-card")).to_have_count(0)
            expect(page.get_by_text(re.compile("no (work )?items|empty project", re.I)).first).to_be_visible()
        elif name == "auth_rejected":
            expect(page.locator("#auth-alert")).to_be_visible()
            require("auth_rejected task" not in page.inner_text("body"), "auth-rejected source title remains visible")
            expect(page.locator("#col-unknown .work-card")).to_have_count(1)
            for group in ("backlog", "unstarted", "started", "completed", "cancelled"):
                expect(page.locator("#col-" + group + " .work-card")).to_have_count(0)
        elif name == "stale":
            expect(page.locator("#staleness-indicator")).to_contain_text(re.compile("stale|failed", re.I))
            expect(page.locator("#refresh-error")).to_contain_text(re.compile("failed", re.I))
        elif name == "empty_failed":
            expect(page.locator(".work-card")).to_have_count(0)
            expect(page.locator("#refresh-error")).to_contain_text(re.compile("failed", re.I))
            expect(page.locator("#staleness-indicator")).to_contain_text(re.compile("failed", re.I))
        elif name == "unavailable":
            page.locator(".work-card").first.click()
            row = page.locator(f'.request-item[data-event-id="{self.request_event_id}"]')
            expect(row.locator(".payload-status")).to_have_text("Payload unavailable")
            require("SYNTHETIC_FORGOTTEN_PAYLOAD" not in page.content(), "forgotten source body rendered")
        elif name == "dense":
            expect(page.get_by_text(LONG_TITLE, exact=True)).to_be_visible()
            require(page.evaluate("window.workUiInjected === undefined"), "untrusted title executed script")
            require(page.locator(".card-title img").count() == 0, "untrusted title became HTML")
            before = page.locator(".card-id").all_text_contents()
            page.locator("#page-next-btn").click()
            expect(page.locator(".card-id").first).not_to_have_text(before[0])
            after = page.locator(".card-id").all_text_contents()
            require(after and set(before).isdisjoint(after), "paging repeats cards or has no next page")
            page.locator("#filter-state-group").select_option("started")
            expect(page.locator("#col-backlog .work-card")).to_have_count(0)
            expect(page.locator("#col-started .work-card").first).to_be_visible()
            page.locator("#filter-blocker-only").check()
            expect(page.locator(".work-card")).to_have_count(1)
            expect(page.locator(".work-card")).to_have_attribute("data-item-uuid", self.blocked_item_id)
            expect(page.locator("#page-next-btn")).to_be_disabled()
            page.locator("#filter-blocker-only").uncheck()
            expect(page.locator(".work-card")).to_have_count(50)
        elif name == "typed":
            page.locator(".work-card").first.click()
            expect(page.locator(".session-item")).to_contain_text("Selected synthetic work summary")
            event = page.locator(f'.request-item[data-event-id="{self.typed_targets["event"]}"]')
            expect(event.locator(".payload-status")).to_have_text("Payload available")
            require(event.locator(".delivery-item").count() > 0, "actual event delivery rows are not rendered")
            group = page.locator(f'.response-group-item[data-group-event-id="{self.typed_targets["response_group"]}"]')
            expect(group.locator(".target-state")).to_have_text("Group state: open")
            record_id, version = self.typed_targets["result_record"].split(":")
            record = page.locator(f'.result-record-item[data-record-id="{record_id}"][data-version="{version}"]')
            expect(record.locator(".payload-status")).to_have_text("Payload available")
            receipt = page.locator(f'.run-receipt-item[data-target-id="{self.typed_targets["run_receipt"]}"]')
            expect(receipt.locator(".receipt-status")).not_to_contain_text("unknown")
            expect(receipt.locator(".assessment-outcome")).not_to_contain_text("accepted")
            require("TYPED_SOURCE_BODY_NEVER_RENDERED" not in page.content(), "typed projection exposed raw source bytes")
            receipt.scroll_into_view_if_needed()
            page.screenshot(path=str(self.output / ("typed-targets-" + viewport + ".png")), full_page=True)

    def uipass(self, name, storage):
        import yaml
        fixture = self.output / (name + "-fixture")
        fixture.mkdir(mode=0o700)
        manifest = yaml.safe_load((ROOT / ".uipass-work-coordination.yaml").read_text())
        manifest["surfaces"] = {name: manifest["surfaces"][name]}
        self.prepare_manifest(name, manifest)
        if storage is not None:
            dump(fixture / "session.json", storage)
            for state in manifest["surfaces"][name]["states"].values():
                state["storage_state_file"] = "session.json"
        path = fixture / "manifest.yaml"
        path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        bundle = self.output / (name + "-uipass")
        command = ([shutil.which("uipass")] if shutil.which("uipass") else
                   ["uv", "run", "--project", str(self.args.uipass_home.resolve()), "--frozen", "uipass"])
        result = subprocess.run([*command, "run", "--manifest", str(path), "--base-url", self.url,
                                 "--output", str(bundle)], capture_output=True, text=True, timeout=210)
        (self.output / (name + "-uipass.log")).write_text(result.stdout + result.stderr)
        verdict = json.loads((bundle / "verdict.json").read_text()) if (bundle / "verdict.json").exists() else None
        self.report["uipass"].append(dict(state=name, exit_code=result.returncode,
                                        bundle=bundle.name, verdict=verdict))
        require(result.returncode == 0, f"UIPass {name} exit {result.returncode}; inspect {bundle.name}")

    def prepare_manifest(self, name, manifest):
        pass

    def run_state(self, browser, name):
        self.seed(name)
        if name == "workflow":
            self.check("CLI mutation journey", self.cli_journey)
        self.start_web()
        if name == "workflow":
            self.check("UIPass login", lambda: self.uipass("login", None))
        with browser.new_context(viewport=dict(width=1440, height=1000)) as context:
            unchanged = self.coordination_state()
            page = self.login(context)
            storage = context.storage_state()
            if name == "workflow":
                # Each viewport exercises actual writes with independently selected prose.
                for viewport, size in (("desktop", dict(width=1440, height=1000)),
                                       ("phone", dict(width=390, height=844))):
                    with browser.new_context(viewport=size, storage_state=storage) as journey:
                        ui = journey.new_page()
                        ui.set_default_timeout(5000)
                        ui.on("dialog", lambda dialog: dialog.dismiss())
                        ui.goto(self.url)
                        self.check("browser mutations " + viewport,
                                   lambda: self.browser_mutations(ui, viewport), ui)
            elif name == "typed":
                for viewport, size in (("desktop", dict(width=1440, height=1000)),
                                       ("phone", dict(width=390, height=844))):
                    with browser.new_context(viewport=size, storage_state=storage) as targets:
                        ui = targets.new_page()
                        ui.set_default_timeout(5000)
                        ui.goto(self.url)
                        self.check("typed target state " + viewport,
                                   lambda: self.visual_state_assertions(ui, name, viewport), ui)
            else:
                page.set_default_timeout(5000)
                if name == "empty_failed":
                    self.check("browser empty-cache refresh failure", lambda: self.browser_failed_refresh(page), page)
                self.check("state " + name, lambda: self.visual_state_assertions(page, name), page)
            self.check("UIPass " + name, lambda: self.uipass(name, storage))
            self.check("view preserves coordination " + name,
                       lambda: require(self.coordination_state() == unchanged,
                                       "work browser changed session, event or delivery state"))

    def run(self):
        from playwright.sync_api import sync_playwright
        before = source_identity(self.args.candidate, self.args.candidate_manifest)
        self.report["source"] = before
        staged = self.runtime / "build-source"
        staged.mkdir()
        for name in before["inputs"]:
            path = self.args.candidate / name
            require(not path.is_symlink(), "candidate source symlink is unsupported: " + name)
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for name, digest in before["inputs"].items():
            require(hashlib.sha256((staged / name).read_bytes()).hexdigest() == digest,
                    "candidate changed while staging: " + name)
        composed = dict(before["inputs"])
        overlay = {}
        if self.args.web_overlay:
            for name in WEB_FILES:
                data = (self.args.web_overlay / name).read_bytes()
                overlay[name] = hashlib.sha256(data).hexdigest()
                (staged / name).write_bytes(data)
                composed[name] = overlay[name]
        self.report["web_overlay"] = overlay
        self.report["build_input_sha256"] = hashlib.sha256(json.dumps(composed, sort_keys=True).encode()).hexdigest()
        self.report["build_vcs"] = "disabled for private source copy; base revision and all overlay hashes recorded"
        checked(["go", "build", "-buildvcs=false", "-o", self.binary, "./cmd/cairn"],
                "build candidate", cwd=staged, timeout=300)
        self.binary.chmod(0o500)
        after = source_identity(self.args.candidate, self.args.candidate_manifest)
        require(before == after,
                "candidate changed during build; rerun a coherent candidate")
        self.report["source"] = after
        self.report["binary_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        home = self.runtime / "home"
        home.mkdir(mode=0o700)
        config = home / "plane.toml"
        key = home / "plane.key"
        key.write_text("synthetic-fixture-key-never-sent\n")
        key.chmod(0o600)
        config.write_text(f'default_instance = "work-ui"\n[instance.work-ui]\nbase_url = "{ORIGIN}"\nworkspace = "work-ui"\napi_key_file = "{key}"\n')
        config.chmod(0o600)
        self.env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LANG="C.UTF-8", TZ="UTC",
            HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), TMPDIR=str(self.runtime),
            PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache/ms-playwright")),
            PLANE_CONFIG=str(config), CAIRN_PLANE_BIN=str(ASSETS / "plane.py"),
            CAIRN_WORK_UI_PLANE_STATE=str(self.state_path))
        # This is the fixture subprocess's environment, not its parent shell.
        # DisposableHost inherits it; owner profiles/routing variables cannot leak in.
        os.environ.clear()
        os.environ.update(self.env)
        self.cluster = OwnedCluster(self.runtime / "pg")
        OwnedCluster.start(self.cluster.root, pg_bin=self.cluster.pg_bin)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for name in self.states:
                    start = len(self.report["checks"])
                    self.report["state_results"][name] = "running"
                    try:
                        self.check("scenario setup/execution " + name, lambda: self.run_state(browser, name),
                                   category="scenario_setup")
                        self.report["state_results"][name] = ("failed" if any(
                            item["status"] == "failed" for item in self.report["checks"][start:]) else "passed")
                    finally:
                        self.stop_web()
                        if self.host:
                            self.host.close()
                            self.host = None
            finally:
                browser.close()
        final = source_identity(self.args.candidate, self.args.candidate_manifest)
        self.report["source_after_run"] = final
        require(final == after,
                "candidate source changed during run; observations cover the private built binary, acceptance incomplete")
        require(hashlib.sha256(self.binary.read_bytes()).hexdigest() == self.report["binary_sha256"],
                "private candidate binary changed during run")
        if self.args.web_overlay:
            require(all(hashlib.sha256((self.args.web_overlay / name).read_bytes()).hexdigest() == digest
                        for name, digest in overlay.items()),
                    "web overlay changed during run; acceptance incomplete")

    def close(self):
        try:
            self.stop_web()
        finally:
            try:
                if self.host:
                    self.host.close()
            finally:
                stopped = self.cluster is None or self.cluster.stop()
                require(stopped, "disposable PostgreSQL cleanup failed; runtime retained")
        calls = self.state_path.with_suffix(".calls.jsonl")
        if calls.exists():
            shutil.copyfile(calls, self.output / "plane-calls.jsonl")
        shutil.rmtree(self.runtime)

    def finish(self):
        failed = [check for check in self.report["checks"] if check["status"] == "failed"]
        self.report["passed"] = not failed
        dump(self.output / "report.json", self.report)
        lines = ["# Work view browser evidence", "", self.report["scope"], "",
                 "Mechanical and behavioral result: " + ("PASS" if not failed else "FAIL"), "",
                 "Visual review: pending actual screenshot inspection. This file is not a visual acceptance claim.", "",
                 "Source revision: " + self.report.get("source", {}).get("revision", "build incomplete"), "",
                 "State coverage: " + json.dumps(self.report["state_results"], sort_keys=True), "",
                 "## Failed checks", ""]
        lines += ["- " + item["name"] + ": " + item["error"] for item in failed] or ["None."]
        lines += ["", "## UIPass bundles", ""] + ["- " + item["bundle"] for item in self.report["uipass"]]
        (self.output / "review.md").write_text("\n".join(lines) + "\n")
        print(json.dumps(dict(passed=not failed, report=str(self.output / "report.json"), failures=len(failed))))
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path,
                        help="verified cairn.work-ui-source/1 manifest for a captured immutable source directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("wc01", "wc02"), default="wc01",
                        help="explicit acceptance scope; required commands/routes fail within the selected scope")
    parser.add_argument("--web-overlay", type=Path,
                        help="copy only the three workview browser files from this reviewed checkout into private build source")
    parser.add_argument("--retain-binary", action="store_true",
                        help="retain the exact tested binary in the private output directory for independent proofs")
    parser.add_argument("--uipass-home", type=Path, default=Path.home() / "git/uipass")
    args = parser.parse_args()
    fixture_class = Fixture
    if args.scope == "wc02":
        import importlib.util
        spec = importlib.util.spec_from_file_location("work_ui_artifacts", ASSETS / "artifacts.py")
        require(spec is not None and spec.loader is not None, "artifact acceptance module is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixture_class = module.artifact_fixture(Fixture, uid, dump, require)
    fixture = fixture_class(args)
    try:
        fixture.check("fixture setup and execution", fixture.run, category="fixture")
    finally:
        fixture.check("disposable cleanup", fixture.close, category="fixture")
    return fixture.finish()


if __name__ == "__main__":
    raise SystemExit(main())
