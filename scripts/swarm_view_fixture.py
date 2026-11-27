#!/usr/bin/env python3
"""Swarm view acceptance fixture: a built candidate, a fresh PostgreSQL cluster,
one realistic campaign seeded through the real CLI and API, `cairn swarm serve`,
and browser assertions in Chromium through Playwright.

Run with the UIPass Python environment; see tests/fixtures/swarm_ui/README.md.
No Cairn HTTP response is mocked and no row is inserted by hand. With --hold the
fixture keeps everything up for a person or an agent to drive a browser, and
stops and removes it on interrupt. Without it, the browser assertions run and a
failure exits nonzero.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import disposable_host
from disposable_host import DisposableHost, OwnedCluster, HostRefusal, checked, require, stop_process

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tests/fixtures/swarm_ui"
CAMPAIGN = "swarm-fixture"
TOPIC = "swarm-fixture.review"
BOARD_TOPIC = "agent-board"
ROLES = ("root", "lead", "worker_a", "worker_b", "worker_c", "bystander")
WORKERS = ("worker_a", "worker_b", "worker_c")
HARNESS = {"root": ("claude", "claude-opus-5-5"), "lead": ("codex", "gpt-5.5-codex"),
           "worker_a": ("claude", "claude-sonnet-5-5"), "worker_b": ("opencode", "qwen3.8-27b"),
           "worker_c": ("agy", "gemini-3-pro"), "bystander": ("hermes", "qwen3.8-27b")}
ROLE_LABELS = {"lead": "Integration lead (CAIRN-829)", "worker_a": "Page builder",
               "worker_b": "Flow reader", "worker_c": "Context counters", "bystander": "Unrelated session"}
TASKS = {"root": "Coordinate the swarm view campaign (CAIRN-826)", "lead": "Integrate the swarm view slices (CAIRN-829)",
         "worker_a": "Build the swarm page (CAIRN-829)", "worker_b": "Fill the message flow (CAIRN-830)",
         "worker_c": "Read the context counters (CAIRN-832)", "bystander": "Unrelated maintenance outside any campaign"}
DESCRIPTION = ("Disposable swarm view fixture: one lead and three workers build a page. "
               "Plane issue CAIRN-829 is linked, never mirrored.")
LONG_SUMMARY = ("Untrusted <img src=x onerror=\"window.swarmInjected=true\"> summary that is long "
                + "and-keeps-going-" * 12)
# Controls the page must never offer (docs/swarm-view.md).
FORBIDDEN = re.compile(r"\b(settle|start|stop|restart|kill|send keys|worktree|compact(ion)?|select model|choose model|spawn|launch)\b", re.I)
DESKTOP = {"width": 1280, "height": 900}
PLANE_URL = "https://plane.fixture.invalid/cairn/browse/{id}/"
PHONE = {"width": 390, "height": 844}


def uid():
    return str(uuid.uuid4())


def rfc3339(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def ref(value):
    return {"record_id": value["record_id"], "version": value["version"]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class Fixture:
    def __init__(self, args):
        self.args = args
        self.candidate = args.candidate.resolve()
        require(not args.output.is_symlink(), "output must not be a symlink")
        self.output = args.output.resolve()
        require(not self.output.exists() or not any(self.output.iterdir()), "output must be new or empty")
        require(not self.output.is_relative_to(ROOT) and not self.output.is_relative_to(self.candidate),
                "output must be outside the source checkouts")
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output.chmod(0o700)
        self.report = dict(schema="cairn.swarm-ui-proof/1", candidate=str(self.candidate), checks=[],
                           seed=[], seed_gaps=[], screenshots=[], mode="hold" if args.hold else "assert",
                           visual_review="pending actual screenshot inspection",
                           scope="Real Cairn CLI/API/store/swarm server/browser; nothing mocked")
        self.report["fixture_inputs"] = {str(Path(p).relative_to(ROOT)): digest(p) for p in
                                         (Path(__file__).resolve(), Path(disposable_host.__file__).resolve())}
        # A short path: the API socket lives under it.
        self.runtime = Path(tempfile.mkdtemp(prefix="cairn-swarm-ui."))
        self.binary = self.runtime / "cairn"
        self.cluster = None
        self.host = None
        self.serve = None
        self.port = 0
        self.url = ""
        self.sessions = {}
        self.events = {}
        self.deliveries = {}
        self.waits = {}
        self.heartbeat_stop = threading.Event()
        self.heartbeat = None
        self.collection = "fixture:swarm-ui:" + uid()[:8]

    # -------------------------------------------------------------- checks

    def check(self, name, operation, page=None, category="acceptance"):
        if page is not None:
            # A check that failed with a dialog open must not block the next one.
            try:
                page.evaluate("() => document.querySelectorAll('dialog[open]').forEach((d) => d.close())")
            except Exception:
                pass
        try:
            operation()
            self.report["checks"].append(dict(name=name, category=category, status="passed"))
            return True
        except Exception as error:
            item = dict(name=name, category=category, status="failed", error=f"{type(error).__name__}: {error}"[:1800])
            if page is not None:
                path = self.output / ("failed-" + re.sub(r"[^a-z0-9_-]", "-", name.lower()) + ".png")
                try:
                    page.screenshot(path=str(path), full_page=True)
                    item["screenshot"] = path.name
                except Exception as capture:
                    item["screenshot_error"] = type(capture).__name__
            self.report["checks"].append(item)
            return False

    def seeded(self, what):
        self.report["seed"].append(what)

    def gap(self, what, error):
        self.report["seed_gaps"].append(dict(step=what, error=str(error)[:600]))

    # ---------------------------------------------------------------- build

    def build(self):
        rev = subprocess.run(["git", "-C", str(self.candidate), "rev-parse", "HEAD"], capture_output=True, text=True)
        dirty = subprocess.run(["git", "-C", str(self.candidate), "status", "--porcelain"], capture_output=True, text=True)
        self.report["source"] = dict(revision=rev.stdout.strip() or "unknown", dirty=bool(dirty.stdout.strip()),
                                     dirty_paths=dirty.stdout.splitlines()[:50])
        checked(["go", "build", "-buildvcs=false", "-o", self.binary, "./cmd/cairn"], "build candidate",
                cwd=self.candidate, timeout=600)
        self.binary.chmod(0o500)
        self.report["binary_sha256"] = digest(self.binary)
        self.report["go_version"] = subprocess.run(["go", "version"], capture_output=True, text=True).stdout.strip()

    # ----------------------------------------------------------------- seed

    def agent(self, role, operation, body=None, expected="OK"):
        return self.host.agent(role, operation, body if body is not None else {},
                               session=self.sessions[role], expected=expected)

    def cli(self, argv, body=None, expected="OK", timeout=30):
        return self.host.command(argv, body, expected=expected, timeout=timeout, label=" ".join(argv[:2]))

    def session_flags(self, role):
        session = self.sessions[role]
        return [*self.host.connection(role, session), "--repo", self.collection]

    def principal(self, role):
        return self.sessions[role]["inbox"]

    def register(self, role, binding=None, native=None):
        harness, model = HARNESS[role]
        body = {"request_id": uid(), "binding": binding or "swarm-fixture-" + role,
                "native_session_id": native or "swarm-fixture-" + uid(),
                "metadata": {"harness": harness, "model": model, "observed_model": model, "project": "cairn",
                             "workspace": str(self.runtime / role), "state": "busy" if role != "worker_c" else "idle",
                             "delivery_mode": "existing-session",
                             "task_summary": TASKS[role]}}
        session = self.host.agent(role, "agent-register", body)
        self.sessions[role] = session
        return session

    def note(self, role, text):
        return self.agent(role, "create", {"request_id": uid(), "draft": {
            "kind": "note", "body": text, "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.collection, "task_id": "coordination/" + uid(), "run_id": "*"}}})

    def publish(self, role, text, destination, kind="request", **extra):
        record = self.note(role, text)
        body = {"request_id": uid(), "kind": kind, "ref": ref(record), "destination": destination}
        body.update(extra)
        return self.agent(role, "event-publish", body)

    def to(self, role):
        return {"type": "agent", "name": self.principal(role)}

    def next(self, role):
        return self.agent(role, "event-next")["delivery"]

    def complete(self, role, delivery, text=None, disposition="handled", code=None):
        body = {"request_id": uid(), "delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"],
                "disposition": disposition}
        if code:
            body["code"] = code
        if text is not None:
            body["draft"] = {"kind": "note", "claim_type": "self", "sensitivity": "shareable", "body": text,
                             "scope": {"repo": self.collection, "task_id": "coordination-result/" + uid(), "run_id": "*"}}
        return self.agent(role, "event-complete", body)

    def reply(self, role, event, delivery, text):
        done = self.complete(role, delivery, text)
        return self.agent(role, "event-publish", {
            "request_id": uid(), "kind": "response", "ref": ref(done["result"]),
            "destination": self.to("lead"), "causation_id": event["event_id"],
            "correlation_id": event.get("correlation_id") or event["event_id"]})

    def wait(self, role, predicate, event_id, deadline=timedelta(hours=2)):
        return self.cli(["wait", "create", *self.session_flags(role), "--request-id", uid(), "--predicate", predicate,
                         "--dependency", event_id, "--deadline", rfc3339(deadline)])

    def team(self, *args):
        return self.cli(["team", *args, "--operator", "--repo", self.collection, "--request-id", uid()])

    def ledger(self, *args):
        return self.cli(["ledger", *args, "--operator", "--repo", self.collection, "--campaign", CAMPAIGN])

    def supports(self, argv, flag):
        result = subprocess.run([str(self.binary), *argv, "--help"], capture_output=True, text=True, env=self.host.env)
        return flag in (result.stdout + result.stderr)

    def step(self, what, operation):
        try:
            value = operation()
            self.seeded(what)
            return value
        except Exception as error:
            self.gap(what, error)
            return None

    def seed(self):
        host = self.host
        boot = host.operator("bootstrap", {"request_id": uid(), "reason": "Disposable swarm view fixture"}, expected=None)
        require(boot["status"] == "OK", "operator bootstrap failed: " + str(boot.get("status")))
        self.cli(["work", "features", "enable", "--feature", "coordination-waits/1", "--request-id", uid(),
                  "--collection", self.collection])
        for role in ROLES:
            self.register(role)
        self.seeded("six sessions registered with harnesses " + ", ".join(sorted({h for h, _ in HARNESS.values()})))

        describe = self.supports(["team", "set-campaign"], "--description")
        labels = self.supports(["team", "set-member"], "--role-label")
        self.report["campaign_metadata_flags"] = dict(description=describe, role_label=labels)
        campaign = ["set-campaign", "--campaign", CAMPAIGN, "--root-agent", self.principal("root"),
                    "--out-of-chain-policy", "warn"]
        if describe:
            campaign += ["--description", DESCRIPTION]
        self.team(*campaign)
        for role in ("lead", *WORKERS):
            member = ["set-member", "--campaign", CAMPAIGN, "--member", self.principal(role),
                      "--lead", self.principal("root" if role == "lead" else "lead"),
                      "--role", "lead" if role == "lead" else "worker"]
            if labels:
                member += ["--role-label", ROLE_LABELS[role]]
            self.team(*member)
        self.seeded("campaign with root, lead and three workers" + (" with description and role labels" if describe and labels else
                    "; description/role-label flags absent in this candidate (CAIRN-828), skipped"))
        for role in WORKERS:
            self.agent(role, "event-subscribe", {"request_id": uid(), "topic": TOPIC, "active": True})

        # worker_a: a request answered (settled) with a response.
        self.events["assign_a"] = self.ledger_send("lead", "worker_a", "slice-a", "Build the swarm page (CAIRN-829)")
        delivery = self.next("worker_a")
        self.reply("worker_a", self.events["assign_a"], delivery, "Selected result: page skeleton done.")
        self.seeded("request settled with a response")

        # The grouped request: three recipients, a closure wait, one counted reply (worker_c).
        group = self.publish("lead", "Review the selected change and reply.", {"type": "topic", "name": TOPIC},
                             summary="Review the swarm stream change (grouped)",
                             response_group={"deadline": rfc3339(timedelta(hours=2)), "partial_policy": "all"})
        self.events["group"] = group
        self.waits["group"] = self.step("group-closure wait", lambda: self.wait("lead", "response_group_closed", group["event_id"]))
        delivery = self.next("worker_c")
        require(delivery and delivery["event"]["event_id"] == group["event_id"], "worker_c did not receive the grouped request first")
        self.reply("worker_c", group, delivery, "Selected group reply from worker_c.")
        self.seeded("three-recipient grouped request with one counted reply")

        # worker_b: a direct request it holds under lease, with a direct-reply wait.
        self.events["assign_b"] = self.ledger_send("lead", "worker_b", "slice-b", "Read the flow deliveries (CAIRN-830)")
        self.waits["direct"] = self.step("direct-reply wait", lambda: self.wait("lead", "request_response", self.events["assign_b"]["event_id"]))
        # worker_b's oldest delivery is the grouped request; claim it into a live lease.
        held = self.next("worker_b")
        self.deliveries["worker_b_leased"] = held
        self.seeded("leased delivery (worker_b) and a direct-reply wait")

        # worker_c: a failed delivery, then superseded and pending notices, then it stops with backlog.
        failing = self.publish("lead", "Run the flaky check.", self.to("worker_c"), summary="Run the flaky check")
        delivery = self.next("worker_c")
        require(delivery and delivery["event"]["event_id"] == failing["event_id"], "worker_c did not claim the failing request")
        self.complete("worker_c", delivery, disposition="failed", code="processing_failed")
        self.seeded("failed delivery (processing_failed)")
        for n in (1, 2):
            self.publish("lead", f"Status update {n}.", self.to("worker_c"), kind="notice", action="review",
                         supersedes="status-c", summary=f"Context counter status {n}")
        self.seeded("superseded notice and its pending successor")

        # A board question and events addressed to the operator.
        self.events["board"] = self.step("board question", lambda: self.publish(
            "worker_a", "Should the token file live in the state directory?", {"type": "topic", "name": BOARD_TOPIC},
            summary="Board question: where should the swarm token file live?"))
        operator = "local-uid:" + str(os.geteuid())
        self.report["operator_principal"] = operator
        for n in (1, 2):
            self.events[f"operator_{n}"] = self.step(f"event addressed to the operator ({n})", lambda n=n: self.publish(
                "worker_b", f"Owner note {n}.", {"type": "agent", "name": operator}, kind="notice", action="review",
                summary=f"For the owner: flow reader note {n}"))
        self.step("hostile and long summary", lambda: self.publish("lead", "Long text.", self.to("worker_a"), kind="notice",
                                                                   action="none", summary=LONG_SUMMARY[:280]))

        # A session in no campaign and a message that no campaign claims.
        self.events["bystander"] = self.step("message outside the campaign", lambda: self.publish(
            "bystander", "Unrelated work.", self.to("bystander"), kind="notice", action="none",
            summary="Bystander note outside any campaign"))

        # Ledger items of each kind.
        self.step("ledger hold", lambda: self.ledger("set", "--kind", "hold", "--key", "integration-slot", "--status", "active",
                                                     "--owner", self.principal("lead"), "--summary", "Integration slot held for CAIRN-829"))
        self.step("ledger escalated blocker", lambda: self.ledger("set", "--kind", "blocker", "--key", "herdr-socket", "--status", "escalated",
                                                                  "--owner", self.principal("root"), "--summary", "Herdr socket not found under the user unit"))
        self.step("ledger root decision", lambda: self.ledger("set", "--kind", "decision", "--key", "token-location", "--status", "open",
                                                              "--owner", self.principal("root"), "--summary", "Decide where the swarm token file lives"))
        self.step("ledger lead decision", lambda: self.ledger("set", "--kind", "decision", "--key", "flow-order", "--status", "open",
                                                              "--owner", self.principal("lead"), "--summary", "Lead decides the flow sort order"))

        # A workstream decision written by a member.
        self.step("workstream decision", lambda: self.cli([
            "status", "set", *self.session_flags("lead"), "--stream", "swarm-fixture.flow", "--state", "waiting",
            "--current", "Flow ordering", "--decision-needed", "Pick the default flow order for the owner view",
            "--request-id", uid()]))

        # A request with a short expect_by and a short wait on it: after the
        # deadline the real sweep records a missed checkpoint and a timed-out wait.
        def short_deadlines():
            self.events["checkpoint"] = self.publish("lead", "Report the flow status.", self.to("worker_b"),
                                                     summary="Report the flow status by the checkpoint",
                                                     expect_by=rfc3339(timedelta(seconds=4)))
            self.waits["short"] = self.wait("lead", "request_response", self.events["checkpoint"]["event_id"],
                                            deadline=timedelta(seconds=4))
            time.sleep(6)
            return self.host.operator("schedule-serve", args=("--repo", self.collection, "--once", "--overdue-grace-seconds", "0"))
        self.step("missed checkpoint and timed-out wait (real sweep)", short_deadlines)

        # worker_c stops with a pending notice: work sent to a stopped session.
        self.step("stopped member with backlog", lambda: self.host.agent("worker_c", "agent-leave", {
            "agent_id": self.sessions["worker_c"]["agent_id"], "execution_id": self.sessions["worker_c"]["execution_id"]}))

        # Coordinator handoff: the root hands off to a fresh session.
        def handoff():
            harness, model = HARNESS["root"]
            result = self.cli(["ledger", "handoff", *self.session_flags("root"), "--campaign", CAMPAIGN,
                               "--new-binding", "swarm-fixture-root-2", "--new-native-session", "swarm-fixture-" + uid(),
                               "--workspace", str(self.runtime / "root-2"), "--harness", harness, "--project", "cairn",
                               "--model", model, "--reason", "Fixture handoff after the age threshold", "--request-id", uid()])
            self.report["handoff"] = {k: result.get(k) for k in ("handoff_id", "role_address") if isinstance(result, dict)}
            return result
        result = self.step("coordinator handoff", handoff)
        if isinstance(result, dict):
            successor = result.get("to_session") or result.get("successor") or {}
            if successor.get("agent_id"):
                self.sessions["root_next"] = dict(successor, inbox="agent/" + successor["agent_id"])
                self.host.identities["root_next"] = self.host.identities["root"]
        # The campaign's existing handoff check: a compaction limit of 1 and one
        # compaction recorded for the new coordinator through `ledger compact`.
        def handoff_due():
            require("root_next" in self.sessions, "no successor session to compact")
            self.team("set-campaign", "--campaign", CAMPAIGN, "--max-coordinator-compactions", "1")
            return self.cli(["ledger", "compact", *self.session_flags("root_next"), "--campaign", CAMPAIGN,
                             "--request-id", uid()])
        self.step("handoff due (compaction limit)", handoff_due)

    def ledger_send(self, role, target, key, summary):
        reply = self.cli(["ledger", "send", *self.session_flags(role), "--campaign", CAMPAIGN, "--op-key", "send-" + key,
                          "--to", self.principal(target), "--kind", "request", "--stdin", "--shareable",
                          "--summary", summary, "--item-key", key], body=f"Assignment {key}: {summary}.")
        event = reply.get("event") if isinstance(reply, dict) else None
        event = event or reply.get("publication") or reply
        require(event.get("event_id"), "ledger send returned no event: " + json.dumps(reply)[:400])
        return event

    def beat(self):
        while not self.heartbeat_stop.wait(25):
            for role, session in list(self.sessions.items()):
                if role == "worker_c" or role == "root":
                    continue
                token_role = "root" if role == "root_next" else role
                try:
                    self.host.agent(token_role, "agent-heartbeat", {"agent_id": session["agent_id"], "execution_id": session["execution_id"]},
                                    expected=None)
                except Exception:
                    pass

    # ---------------------------------------------------------------- serve

    def start_serve(self, port=0):
        argv = [str(self.binary), "swarm", "serve", "--collection", self.collection, "--port", str(port),
                "--token-file", str(self.runtime / "swarm-view.token"), "--bindings", str(self.runtime / "bindings"),
                "--plane-url", PLANE_URL]
        log = open(self.output / "swarm-serve.log", "ab")
        self.serve_log = self.output / "swarm-serve.log"
        start = self.serve_log.stat().st_size if self.serve_log.exists() else 0
        self.serve = subprocess.Popen(argv, env=self.host.env, stdout=log, stderr=log)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(self.serve.poll() is None, "swarm serve exited: " + self.serve_log.read_text()[-600:])
            text = self.serve_log.read_bytes()[start:].decode(errors="replace")
            match = re.search(r"http://127\.0\.0\.1:(\d+)/", text)
            if match:
                self.port = int(match.group(1))
                break
            time.sleep(0.1)
        require(self.port, "swarm serve printed no address")
        url = self.cli(["swarm", "url", "--port", str(self.port), "--token-file", str(self.runtime / "swarm-view.token")])
        self.url = url["url"]
        self.token = self.url.split("#token=", 1)[1]
        return self.url

    def stop_serve(self):
        if self.serve is not None:
            stop_process(self.serve, timeout=10, label="swarm serve")
            self.serve = None

    def campaign_url(self, token=True):
        base = f"http://127.0.0.1:{self.port}/?campaign={CAMPAIGN}"
        return base + ("#token=" + self.token if token else "")

    # ---------------------------------------------------------------- store

    def event_status(self, event_id):
        return self.agent("lead", "event-inspect", {"event_id": event_id})

    def group_state(self):
        return self.agent("lead", "event-group", {"event_id": self.events["group"]["event_id"]})

    # -------------------------------------------------------------- browser

    def shot(self, page, name):
        path = self.output / (name + ".png")
        page.screenshot(path=str(path), full_page=True)
        self.report["screenshots"].append(path.name)

    def assertions(self, page):
        expect = self.expect
        def sign_in():
            page.goto(self.campaign_url())
            expect(page.locator("#campaign")).to_be_visible(timeout=15000)
            require("#token" not in page.url and self.token not in page.url, "token left in the address bar")
            expect(page.locator("#live")).to_have_text(re.compile("live"), timeout=15000)
        if not self.check("sign-in by fragment removes the token", sign_in, page):
            return

        def regions():
            expect(page.locator("#title")).to_have_text(CAMPAIGN)
            expect(page.locator("#needs")).not_to_be_empty()
            expect(page.locator("#agents .agent").first).to_be_visible(timeout=10000)
            labels = page.locator("#agents .agent").evaluate_all("nodes => nodes.map(n => n.dataset.member)")
            for role in ("lead", *WORKERS):
                require(self.principal(role) in labels, f"agent row missing for {role}")
            expect(page.locator("#flow li.event").first).to_be_visible(timeout=10000)
            ages = page.locator("#observations li").all_inner_texts()
            require(len(ages) == 4 and any(a.startswith("Transcripts") for a in ages),
                    "four observation ages (database, Herdr, transcripts, session counters) expected: " + json.dumps(ages))
            flags = self.report.get("campaign_metadata_flags", {})
            if flags.get("description"):
                expect(page.locator("#description")).to_contain_text("one lead and three workers build a page")
            if flags.get("role_label"):
                lead = page.locator(f"#agents .agent[data-member='{self.principal('lead')}'] .agent-cell")
                expect(lead).to_contain_text(ROLE_LABELS["lead"])
            require(not page.evaluate("window.swarmInjected === true"), "summary text was parsed as HTML")
            require(page.locator("img").count() == 0, "an image element was created from server text")
            link = page.locator("a.issue", has_text="CAIRN-829").first
            expect(link).to_have_attribute("href", PLANE_URL.replace("{id}", "CAIRN-829"))
            expect(link).to_have_attribute("rel", "noopener noreferrer")
        self.check("the four regions render from the seeded campaign", regions, page)
        self.check("each agent row shows harness, pane and context room", lambda: self.agent_rows(page), page)
        self.shot(page, "desktop-campaign")

        def live_event():
            marker = "live-" + uid()[:8]
            event = self.publish("lead", "Live event.", self.to("worker_a"), kind="notice", action="none", summary="Live " + marker)
            row = page.locator("#flow li.event", has_text=marker)
            expect(row).to_have_count(1, timeout=15000)
            page.wait_for_timeout(5000)
            expect(row).to_have_count(1)
            ids = page.evaluate("window.cairnSwarmView.logIDs()")
            require(ids.count(event["event_id"]) == 1 and len(ids) == len(set(ids)), "log holds an event twice")
            self.live_ids = set(ids)
        self.check("a new event appears without a reload and exactly once", live_event, page)

        def reload_keeps():
            before = set(page.evaluate("window.cairnSwarmView.logIDs()"))
            marker = "reload-" + uid()[:8]
            page.reload()
            event = self.publish("lead", "During reload.", self.to("worker_a"), kind="notice", action="none", summary="Reload " + marker)
            expect(page.locator("#flow li.event", has_text=marker)).to_have_count(1, timeout=15000)
            after = page.evaluate("window.cairnSwarmView.logIDs()")
            require(len(after) == len(set(after)), "reload repeated an event")
            missing = before - set(after)
            require(not missing, f"reload lost {len(missing)} event(s)")
            require(event["event_id"] in after, "event published during the reload is missing")
        self.check("a reload neither loses nor repeats an event", reload_keeps, page)

        def server_restart():
            before = set(page.evaluate("window.cairnSwarmView.logIDs()"))
            port = self.port
            self.stop_serve()
            marker = "restart-" + uid()[:8]
            during = self.publish("lead", "While the server is down.", self.to("worker_a"), kind="notice", action="none",
                                  summary="Restart " + marker)
            self.port = 0
            self.start_serve(port)
            require(self.port == port, "server came back on another port")
            # The new process has no session for this browser: the page asks to sign in again.
            expect(page.locator("#signin")).to_be_visible(timeout=30000)
            page.fill("#token-input", self.token)
            page.click("#signin button[type=submit]")
            expect(page.locator("#flow li.event", has_text=marker)).to_have_count(1, timeout=20000)
            after_marker = "after-" + uid()[:8]
            self.publish("lead", "After the restart.", self.to("worker_a"), kind="notice", action="none", summary="After " + after_marker)
            expect(page.locator("#flow li.event", has_text=after_marker)).to_have_count(1, timeout=15000)
            ids = page.evaluate("window.cairnSwarmView.logIDs()")
            require(len(ids) == len(set(ids)), "restart repeated an event")
            require(not (before - set(ids)), "restart lost an event")
            require(during["event_id"] in ids, "event published while the server was down is missing")
        self.check("a server restart neither loses nor repeats an event", server_restart, page)

        self.check("a reset with STALE_CURSOR starts a new stream without a cursor", lambda: self.stale_cursor(page), page)
        self.check("NOT_FOUND stops, offers the landing page and does not retry", lambda: self.not_found(page), page)
        self.check("a refusal that persists stays visible and is retried slowly", lambda: self.persistent_refusal(page), page)
        self.check("a transient store fault keeps the cursor", lambda: self.store_fault(page), page)
        self.check("sign-out in a second tab ends the first tab's stream", lambda: self.second_tab_signout(page), page)
        self.check("the context cell renders the transcript observation", lambda: self.context_cell(page), page)
        self.check("flow rows show each recipient's state; agent rows label waits apart", lambda: self.flow_rows(page), page,
                   category="slice:CAIRN-830")
        self.check("needs you lists the board question and the operator event, not the lead decision",
                   lambda: self.needs_you(page), page, category="slice:CAIRN-831")
        self.check("every needs-you type the fixture can produce appears", lambda: self.need_types(page), page,
                   category="slice:CAIRN-831")
        self.check("cancel preview names the recipient and waits before confirm; siblings and counted reply unchanged",
                   lambda: self.cancel(page), page, category="slice:CAIRN-830+833")
        self.check("a refused action shows the store's reason", lambda: self.refusal(page), page)
        self.check("no forbidden control exists", lambda: self.forbidden(page), page)
        self.shot(page, "desktop-final")

    def stale_cursor(self, page):
        cursor = page.evaluate("window.cairnSwarmView.cursor()")
        require(cursor, "the page holds no cursor")
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        raw["g"] = int(raw["g"]) + 1  # a cursor from another restore generation
        forged = base64.urlsafe_b64encode(json.dumps(raw, separators=(",", ":")).encode()).decode().rstrip("=")
        requests = []

        def forge(route):
            url = route.request.url
            requests.append(url)
            if len(requests) == 1:
                route.continue_(url=url + "&cursor=" + forged)
            else:
                route.continue_()
        page.route("**/api/swarm/stream*", forge)
        try:
            page.reload()
            # A function, not an expression: the page's CSP forbids eval.
            page.wait_for_function("() => !!window.cairnSwarmView && window.cairnSwarmView.resets().includes('STALE_CURSOR')", timeout=15000)
            self.expect(page.locator("#live")).to_have_text("live", timeout=15000)
            self.expect(page.locator("#flow li.event").first).to_be_visible(timeout=10000)
            require(len(requests) >= 2 and "cursor=" not in requests[1], "the page did not restart without a cursor: " + json.dumps(requests[:3]))
            ids = page.evaluate("window.cairnSwarmView.logIDs()")
            require(ids and len(ids) == len(set(ids)), "the new window is empty or repeats an event")
            require(page.locator("#signin").is_hidden(), "a stale cursor asked for sign-in")
            page.wait_for_timeout(4000)
            require(page.evaluate("window.cairnSwarmView.resets()").count("STALE_CURSOR") == 1, "the stale cursor was reset more than once")
        finally:
            page.unroute("**/api/swarm/stream*")

    def stream_requests(self, other, campaign, seconds):
        requests = []
        other.on("request", lambda r: requests.append(r.url) if "/api/swarm/stream" in r.url else None)
        other.goto(f"http://127.0.0.1:{self.port}/?campaign={campaign}")
        other.wait_for_timeout(seconds * 1000)
        return requests

    def not_found(self, page):
        other = page.context.new_page()
        try:
            requests = self.stream_requests(other, "no-such-campaign", 8)
            require("NOT_FOUND" in other.evaluate("window.cairnSwarmView.resets()"), "no NOT_FOUND reset arrived")
            self.expect(other.locator("#fault")).to_contain_text("does not exist")
            self.expect(other.locator("#fault a")).to_have_attribute("href", "./")
            require(len(requests) == 1, f"NOT_FOUND was retried: {len(requests)} stream requests in 8 s")
        finally:
            other.close()

    def persistent_refusal(self, page):
        other = page.context.new_page()
        try:
            # A campaign name the store refuses: no cursor is involved, so the
            # refusal persists. It must stay visible and not loop.
            requests = self.stream_requests(other, "bad%20name%21", 10)
            codes = other.evaluate("window.cairnSwarmView.resets()")
            self.report["persistent_refusal_codes"] = codes
            require(codes, "no reset arrived for a refused campaign name")
            self.expect(other.locator("#fault")).to_be_visible()
            require(len(requests) <= 2, f"a persistent refusal was retried {len(requests)} times in 10 s")
        finally:
            other.close()

    def store_fault(self, page):
        before = set(page.evaluate("window.cairnSwarmView.logIDs()"))
        resets = len(page.evaluate("window.cairnSwarmView.resets()"))
        cluster = self.cluster
        require(disposable_host.stop_postgres(cluster.pg_bin, cluster.root / "data"), "could not stop the disposable cluster")
        try:
            page.wait_for_function("() => window.cairnSwarmView.faults() > 0", timeout=20000)
            self.expect(page.locator("#fault")).to_be_visible()
            self.expect(page.locator("#observations")).to_contain_text("store unreadable")
        finally:
            checked(disposable_host.pg_start_command(cluster.pg_bin, cluster.root / "data", cluster.socket,
                                                     cluster.root / "postgres.log"), "restart disposable cluster", timeout=60)
        self.expect(page.locator("#fault")).to_be_hidden(timeout=30000)
        self.expect(page.locator("#live")).to_have_text("live", timeout=15000)
        require(len(page.evaluate("window.cairnSwarmView.resets()")) == resets, "a fault reset the stream")
        marker = "fault-" + uid()[:8]
        self.publish("lead", "After the outage.", self.to("worker_a"), kind="notice", action="none", summary="Outage " + marker)
        self.expect(page.locator("#flow li.event", has_text=marker)).to_have_count(1, timeout=20000)
        ids = page.evaluate("window.cairnSwarmView.logIDs()")
        require(len(ids) == len(set(ids)) and not (before - set(ids)), "the outage lost or repeated an event")

    def second_tab_signout(self, page):
        other = page.context.new_page()
        try:
            other.goto(self.campaign_url(token=False))
            self.expect(other.locator("#live")).to_have_text("live", timeout=15000)
            other.click("#signout")
            self.expect(page.locator("#signin")).to_be_visible(timeout=20000)
            require("UNAUTHENTICATED" in page.evaluate("window.cairnSwarmView.resets()"), "the stream did not end with UNAUTHENTICATED")
        finally:
            other.close()
        before = set(page.evaluate("window.cairnSwarmView.logIDs()"))
        page.fill("#token-input", self.token)
        page.click("#signin button[type=submit]")
        self.expect(page.locator("#live")).to_have_text("live", timeout=15000)
        marker = "signin-" + uid()[:8]
        self.publish("lead", "After signing in again.", self.to("worker_a"), kind="notice", action="none", summary="Back " + marker)
        self.expect(page.locator("#flow li.event", has_text=marker)).to_have_count(1, timeout=15000)
        ids = page.evaluate("window.cairnSwarmView.logIDs()")
        require(len(ids) == len(set(ids)) and not (before - set(ids)), "signing in again lost or repeated an event")

    def context_cell(self, page):
        observed = self.page_json("/api/swarm/panes")["window"]
        self.report["window_observation"] = {k: observed.get(k) for k in ("status", "error", "stale", "sessions", "near_limit_rule")}
        self.report["window_observation"]["unknown"] = observed.get("unknown")
        agent_id = self.sessions["lead"]["agent_id"]
        cell = page.locator(f"#agents .agent[data-member='{self.principal('lead')}'] td").nth(4)
        text = cell.inner_text()
        windows, unknown = observed.get("windows") or {}, observed.get("unknown") or {}
        if agent_id in windows:
            require("%" in text or "tokens" in text, "a known window is not shown as a figure: " + text)
        elif agent_id in unknown:
            require("unknown" in text and unknown[agent_id] in text, "the unknown reason is not shown: " + text)
        elif observed.get("status") == "unavailable":
            require("transcripts unavailable" in text, "unavailable transcripts are not shown: " + text)
        else:
            require("unknown" in text or "reading transcripts" in text, "context cell is not honest: " + text)
        require("Cairn-injected since compaction" in text, "the Cairn proxy is not labelled under the true figure")
        cases = [({"used_tokens": 807000, "window_tokens": 1000000}, "81% · 807k of 1M tokens"),
                 ({"used_tokens": 807000, "window_tokens": 0}, "807k tokens"),
                 ({"used_tokens": 150000, "window_tokens": 200000}, "75% · 150k of 200k tokens"),
                 ({"used_tokens": 1234567, "window_tokens": 2000000}, "62% · 1.2M of 2M tokens")]
        for window, expected in cases:
            got = page.evaluate("(w) => window.cairnSwarmView.formatWindow(w)", window)
            require(got == expected, f"formatWindow({window}) gave {got!r}, expected {expected!r}")

    def agent_rows(self, page):
        require(page.locator("#agents table.agents-table").count() == 1, "agents are not a table at 1280 px")
        headers = page.locator("#agents table.agents-table thead th").all_inner_texts()
        for name in ("Agent", "State", "Working on", "Holds", "Waiting", "Context room", "Pane"):
            require(any(h.startswith(name) for h in headers), f"agents table lacks the {name} column")
        lead = page.locator(f"#agents .agent[data-member='{self.principal('lead')}']")
        text = lead.inner_text()
        require(HARNESS["lead"][0] in text, "lead row lacks its harness")
        require("session started" in text, "lead row lacks the session age")
        require("unknown" in text or "Cairn-injected since compaction" in text, "context room cell is empty")
        worker = page.locator(f"#agents .agent[data-member='{self.principal('worker_a')}']").inner_text()
        require("Build the swarm page" in worker, "working on does not lead with the member's open assignment")
        require(f"campaign {CAMPAIGN} [" not in worker, "working on repeats the derived campaign line")
        order = page.locator("#agents .agent").evaluate_all("nodes => nodes.map(n => n.dataset.member)")
        snapshot = self.snapshot()
        roles = {m["member"]: m["role"] for m in snapshot["members"]}
        presence = {m["member"]: (m.get("session") or {}).get("presence") for m in snapshot["members"]}
        rank = [({"root": 0, "lead": 1, "worker": 2}.get(roles.get(m), 3),
                 {"online": 0, "offline": 1, "stopped": 2}.get(presence.get(m), 3)) for m in order if m in roles]
        require(rank == sorted(rank), "members are not ordered root, leads, workers, then online before stopped")
        require("no pane on this host" in text or "Herdr" in text or "reading Herdr" in text or "herdr agent focus" in text,
                "pane cell is not labelled")
        snapshot = self.snapshot()
        if (snapshot.get("needs_you") or {}).get("lead_decisions", {}).get(self.principal("lead")):
            require("Lead decides the flow sort order" in text, "lead-owned decision is not on the lead's row")

    def snapshot(self):
        # Read the same snapshot the page reads, through the same signed-in route.
        return self.page_json(f"/api/swarm/campaign?campaign={CAMPAIGN}")

    def page_json(self, path):
        return self.current_page.evaluate("async (path) => (await fetch(path, {cache: 'no-store'})).json()", path)

    def needs_you(self, page):
        snapshot = self.snapshot()
        items = snapshot["needs_you"]["items"]
        text = page.locator("#needs").inner_text()
        heading = page.locator("#needs-heading").inner_text()
        require(heading == (f"Needs you ({len(items)})" if items else "Needs you"), "needs-you heading count is wrong: " + heading)
        if not items:
            require("Nothing waits on you" in text or "unavailable" in text, "empty needs-you is not shown honestly")
            raise AssertionError("snapshot.needs_you is empty in this candidate: the CAIRN-831 slice is not present")
        # The page lists the snapshot's rows in the order given.
        shown = page.locator("#needs li.need").evaluate_all("nodes => nodes.map(n => n.dataset.need)")
        require(shown == [item["id"] for item in items], "needs-you rows differ from the snapshot or its order")
        require(page.locator(f"#needs li.need[data-type='board_question'][data-event='{self.events['board']['event_id']}']").count() == 1,
                "the board question is not listed")
        for n in (1, 2):
            event = self.events.get(f"operator_{n}")
            require(event and page.locator(f"#needs li.need[data-event='{event['event_id']}']").count() == 1,
                    f"operator-addressed event {n} is not listed")
        require("Lead decides the flow sort order" not in text, "lead-owned decision listed in needs you")
        lead = page.locator(f"#agents .agent[data-member='{self.principal('lead')}']").inner_text()
        require("Lead decides the flow sort order" in lead, "lead-owned decision is not on the lead's row")
        # A board question is answered on the board: no compose or reply control.
        board = page.locator("#needs li.need[data-type='board_question']").first
        self.expect(board).to_contain_text("Answer on the board")
        buttons = board.locator("button").all_inner_texts()
        require(all(b.startswith("Copy") for b in buttons), "a board question offers a control: " + json.dumps(buttons))

    PRODUCED_NEEDS = ("board_question", "operator_delivery", "decision", "escalation", "workstream_decision",
                      "handoff_due", "failed_delivery", "stopped_backlog", "wait_timed_out", "missed_checkpoint")
    # owner_turn needs a native host observation; stalled_agent needs a session
    # starved for 600 s (idle) or 1800 s (busy), longer than the fixture runs.
    UNPRODUCED_NEEDS = {"owner_turn": "needs a native host owner_turn_required observation",
                        "stalled_agent": "needs pending_starved (600/1800 s) or hold_stalled (3600 s)"}

    def flow_rows(self, page):
        snapshot = self.snapshot()
        group_id = self.events["group"]["event_id"]
        deliveries = [d for d in snapshot["flow"]["deliveries"] if d["event_id"] == group_id]
        require(len(deliveries) == 3, f"the grouped request has {len(deliveries)} deliveries in the snapshot")
        row = page.locator(f"#flow li.event[data-event='{group_id}']")
        self.expect(row).to_have_count(1)
        chips = row.locator(".chip").evaluate_all("nodes => nodes.map(n => [n.dataset.delivery, n.dataset.display, n.innerText])")
        require(sorted(c[0] for c in chips) == sorted(d["delivery_id"] for d in deliveries), "flow row chips differ from the deliveries")
        by_id = {d["delivery_id"]: d for d in deliveries}
        for delivery_id, display, text in chips:
            require(display == by_id[delivery_id]["display"], "a chip shows another state than the snapshot")
            require(("handled" if display == "settled" else display) in text, "a chip does not name its state in words")
        require("settle" not in " ".join(c[2] for c in chips).lower(), "a chip says settle")
        lead = page.locator(f"#agents .agent[data-member='{self.principal('lead')}']").inner_text()
        require("group closure" in lead and "direct reply" in lead, "the lead's waits are not labelled apart")
        member = snapshot["flow"]["by_member"].get(self.principal("lead"), {})
        if member.get("oldest_pending"):
            require("Oldest pending" in lead, "the lead row omits its oldest pending item")
        worker_b = page.locator(f"#agents .agent[data-member='{self.principal('worker_b')}']").inner_text()
        if snapshot["flow"]["by_member"].get(self.principal("worker_b"), {}).get("peer_waits"):
            require("A peer waits on it" in worker_b, "worker_b's peer waits are not shown")

    def need_types(self, page):
        items = self.snapshot()["needs_you"]["items"]
        types = sorted({item["type"] for item in items})
        self.report["needs_you_types"] = dict(produced=types, not_producible=self.UNPRODUCED_NEEDS)
        missing = [t for t in self.PRODUCED_NEEDS if t not in types]
        require(not missing, "needs-you types missing from the seeded campaign: " + ", ".join(missing))
        for t in types:
            require(page.locator(f"#needs li.need[data-type='{t}']").count() >= 1, f"the page does not show the {t} row")

    def cancel(self, page):
        snapshot = self.snapshot()
        group_id = self.events["group"]["event_id"]
        group = [d for d in snapshot["flow"]["deliveries"] if d["event_id"] == group_id]
        if not group:
            raise AssertionError("snapshot.flow has no deliveries of the grouped request: the CAIRN-830 slice is not present")
        target = next((d for d in group if d["consumer"] == self.principal("worker_a") and d["display"] == "pending"), None)
        require(target, "worker_a's pending group delivery is not in the snapshot")
        before_status = {d["delivery_id"]: d["state"] for d in self.event_status(group_id)["deliveries"]}
        before_group = self.group_state()
        page.locator(f"#agents .agent[data-member='{self.principal('worker_a')}'] button", has_text="Details").click()
        item = page.locator("#detail li", has=page.locator(f"[data-delivery='{target['delivery_id']}']"))
        item.locator("button", has_text="Cancel this recipient's delivery").click()
        confirm = page.locator("#confirm")
        self.expect(confirm).to_be_visible()
        go = page.locator("#confirm-go")
        body = page.locator("#confirm-body")
        self.expect(body).to_contain_text("Recipient", timeout=10000)
        text = body.inner_text()
        require(self.label(snapshot, "worker_a") in text, "preview does not name the exact recipient")
        require("Direct-reply waits" in text and "Waits on the group's closure" in text, "preview omits the affected waits")
        require("replies counted" in text, "preview omits the group's counted replies")
        self.expect(go).to_be_enabled(timeout=5000)
        go.click()
        self.expect(page.locator("#confirm-result")).to_contain_text(re.compile("cancel", re.I), timeout=10000)
        after_status = {d["delivery_id"]: d["state"] for d in self.event_status(group_id)["deliveries"]}
        for delivery, state in before_status.items():
            if delivery == target["delivery_id"]:
                continue
            require(after_status.get(delivery) == state, "a sibling delivery changed state")
        after_group = self.group_state()
        require(after_group["responded"] == before_group["responded"], "the counted reply changed")
        page.locator("#confirm button[data-close='confirm']").first.click()
        page.locator("#detail button.close").click()

    def label(self, snapshot, role):
        for member in snapshot["members"]:
            if member["member"] == self.principal(role):
                return member.get("label") or role
        return role

    def refusal(self, page):
        page.locator(f"#agents .agent[data-member='{self.principal('worker_a')}'] button", has_text="Details").click()
        form = page.locator("#detail form.compose")
        form.locator("select[name=kind]").select_option("notice")
        form.locator("input[name=summary]").fill("Refusal probe")
        form.locator("textarea[name=body]").fill("Refusal probe body.")
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/send")) as info:
            form.locator("button[type=submit]").click()
        response = info.value
        body = response.json()
        if not response.ok:
            self.expect(form.locator(".result")).to_contain_text(body["error"], timeout=5000)
            self.expect(form.locator(".result")).to_contain_text(body["code"])
            self.report["refusal_path"] = "send: " + body.get("code", "")
            page.locator("#detail button.close").click()
            return
        # The candidate accepts the send. Acknowledging the newer of the two
        # operator deliveries is refused: the server claims in order (409).
        page.locator("#detail button.close").click()
        row = page.locator("#flow li.event", has_text="flow reader note 2")
        row.locator("button", has_text="Open").click()
        page.locator("#event-detail button", has_text="Acknowledge").first.click(timeout=10000)
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/acknowledge")) as info:
            page.locator("#confirm-go").click()
        response = info.value
        body = response.json()
        require(not response.ok, "acknowledging the newer operator delivery was accepted out of order")
        self.expect(page.locator("#confirm-result")).to_contain_text(body.get("code", ""), timeout=5000)
        self.report["refusal_path"] = "acknowledge: " + body.get("code", "")
        page.locator("#confirm button[data-close='confirm']").first.click()
        page.locator("#event-detail button.close").click()

    def forbidden(self, page):
        for dialog in ("#detail", "#event-detail", "#confirm"):
            page.evaluate("(sel) => { const d = document.querySelector(sel); if (d && d.open) d.close(); }", dialog)
        page.locator(f"#agents .agent[data-member='{self.principal('lead')}'] button", has_text="Details").click()
        controls = page.locator("button, a, input, select, textarea, [role=button]").evaluate_all(
            "nodes => nodes.map(n => (n.innerText || n.value || n.getAttribute('aria-label') || '').trim())")
        bad = [c for c in controls if FORBIDDEN.search(c) and not c.startswith("Copy")]
        require(not bad, "forbidden controls: " + json.dumps(bad[:10]))
        require("settle" not in page.locator("body").inner_text().lower(), "'settle' appears on the page")
        page.locator("#detail button.close").click()

    def phone(self, browser):
        context = browser.new_context(viewport=PHONE)
        page = context.new_page()
        try:
            page.goto(self.campaign_url())
            self.expect(page.locator("#agents .agent").first).to_be_visible(timeout=20000)
            page.wait_for_timeout(1000)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            require(overflow <= 1, f"page scrolls horizontally by {overflow}px at phone width")
            self.shot(page, "phone-campaign")
            page.goto(f"http://127.0.0.1:{self.port}/")
            self.expect(page.locator("#campaigns")).to_contain_text(CAMPAIGN, timeout=15000)
            if self.events.get("bystander"):
                self.expect(page.locator("#landing-events")).to_contain_text("Bystander note outside any campaign", timeout=10000)
                self.expect(page.locator("#unaffiliated")).to_contain_text(HARNESS["bystander"][0])
            self.shot(page, "phone-landing")
        finally:
            context.close()

    # ------------------------------------------------------------------ run

    def run(self):
        self.build()
        home = self.runtime / "home"
        home.mkdir(mode=0o700)
        self.env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LANG="C.UTF-8", TZ="UTC", HOME=str(home),
                        XDG_CONFIG_HOME=str(home / ".config"), TMPDIR=str(self.runtime),
                        PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH",
                                                                str(Path.home() / ".cache/ms-playwright")))
        # The fixture's children see only this environment: no owner profile,
        # live store or installed state directory can leak in.
        os.environ.clear()
        os.environ.update(self.env)
        self.cluster = OwnedCluster.start(self.runtime / "pg")
        self.host = DisposableHost.start(self.cluster, binary=self.binary, repo=self.collection, roles=ROLES,
                                         database="swarm_ui", home=self.runtime / "h", label="swarm_ui", env=self.env)
        require(self.host.env["CAIRN_HOME"].startswith(str(self.runtime)), "fixture state directory escaped the runtime")
        self.report["collection"] = self.collection
        self.seed()
        self.heartbeat = threading.Thread(target=self.beat, daemon=True)
        self.heartbeat.start()
        self.start_serve()
        print(json.dumps({"url": self.url.replace("/#", f"/?campaign={CAMPAIGN}#"), "landing": self.url,
                          "collection": self.collection, "output": str(self.output)}), flush=True)
        if self.args.hold:
            self.hold()
            return
        self.require_playwright()
        self.browser_run()

    def require_playwright(self):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError as error:
            raise HostRefusal("prerequisite: Playwright is not importable; run with the UIPass environment "
                              "(uv run --project ~/git/uipass --frozen python ...): " + str(error)) from None

    def browser_run(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                raise HostRefusal("prerequisite: Chromium for Playwright is missing: " + str(error)[:300]) from None
            self.expect = __import__("playwright.sync_api", fromlist=["expect"]).expect
            try:
                context = browser.new_context(viewport=DESKTOP)
                page = context.new_page()
                self.current_page = page
                self.console = []
                page.on("console", lambda message: self.console.append(f"{message.type}: {message.text}"[:300]))
                page.on("pageerror", lambda error: self.console.append(f"pageerror: {error}"[:300]))
                self.assertions(page)
                self.check("phone width renders without horizontal scroll", lambda: self.phone(browser), category="layout")
            finally:
                browser.close()
        self.report["console"] = self.console[-50:]

    def hold(self):
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        print("holding; interrupt to stop and remove the fixture", file=sys.stderr, flush=True)
        while not stop.wait(1):
            if self.serve is not None and self.serve.poll() is not None:
                print("swarm serve exited", file=sys.stderr)
                break

    def close(self):
        self.heartbeat_stop.set()
        try:
            self.stop_serve()
        finally:
            try:
                if self.host:
                    self.host.close()
            finally:
                stopped = self.cluster is None or self.cluster.stop()
                require(stopped, "disposable PostgreSQL cleanup failed; runtime retained at " + str(self.runtime))
        shutil.rmtree(self.runtime)

    def finish(self):
        failed = [c for c in self.report["checks"] if c["status"] == "failed"]
        self.report["passed"] = not failed
        dump(self.output / "report.json", self.report)
        lines = ["# Swarm view browser evidence", "", self.report["scope"], "",
                 "Result: " + ("PASS" if not failed else "FAIL"), "",
                 "Visual review: pending actual screenshot inspection. This file is not a visual acceptance claim.", "",
                 "Source: " + json.dumps(self.report.get("source", {})), "", "## Failed checks", ""]
        lines += [f"- [{c['category']}] {c['name']}: {c['error']}" for c in failed] or ["None."]
        lines += ["", "## Seed gaps", ""] + ([f"- {g['step']}: {g['error']}" for g in self.report["seed_gaps"]] or ["None."])
        lines += ["", "## Screenshots", ""] + ["- " + name for name in self.report["screenshots"]]
        (self.output / "review.md").write_text("\n".join(lines) + "\n")
        print(json.dumps(dict(passed=not failed, report=str(self.output / "report.json"), failures=len(failed))))
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, default=ROOT, help="source checkout to build (default: this checkout)")
    parser.add_argument("--output", type=Path, required=True, help="new or empty directory outside the checkout")
    parser.add_argument("--hold", action="store_true", help="keep the fixture up until interrupted; no assertions")
    args = parser.parse_args()
    fixture = Fixture(args)
    try:
        fixture.check("fixture setup and execution", fixture.run, category="fixture")
    finally:
        fixture.check("disposable cleanup", fixture.close, category="fixture")
    return fixture.finish()


if __name__ == "__main__":
    raise SystemExit(main())
