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
# A fixture identity whose principal is the operator principal, used only to
# subscribe the operator's inbox to a topic through the API.
OWNER_INBOX = "owner_inbox"
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
        self.sleeper = None
        self.preview_token = ""
        self.root_role = "root_next"
        self.context_expected = {}
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

        # A board question published before the campaign exists: it is listed on
        # the campaign page, but its event is not in the campaign's log.
        self.events["early_board"] = self.step("board question before the campaign", lambda: self.publish(
            "worker_b", "Is the flow order final?", {"type": "topic", "name": BOARD_TOPIC},
            summary="Board question: is the flow order final?"))
        # A request to the owner from a future member, before the campaign exists:
        # the campaign page owns the delivery, but the event is not in its log.
        operator_principal = "local-uid:" + str(os.geteuid())
        self.events["early_request"] = self.step("request to the owner before the campaign", lambda: self.publish(
            "worker_b", "Please confirm the flow layout.", {"type": "agent", "name": operator_principal},
            summary="For the owner: flow reader request from before the campaign"))
        time.sleep(1)
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
        # A delivery to the owner that belongs to no campaign: the landing page owns it.
        self.events["owner_outside"] = self.step("delivery to the owner outside any campaign", lambda: self.publish(
            "bystander", "Outside any campaign.", {"type": "agent", "name": operator}, kind="notice",
            action="review", summary="For the owner: bystander note outside the campaign"))
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
            outside = self.publish("bystander", "Unrelated request.", self.to("bystander"), summary="Bystander asks itself")
            self.waits["outside"] = self.wait("bystander", "request_response", outside["event_id"], deadline=timedelta(seconds=4))
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

    # ------------------------------------------------------- context room

    # Two Claude sessions with sample transcripts (one below, one at or above
    # 90% of the window) and one OpenCode session whose transcript the reader
    # cannot decode. A long-lived child process stands in for each harness
    # process; the readers check it by PID, start time and boot ID.
    WINDOWS = {"root_next": ("claude-opus-5-5", 20_000, 880_000, 20_000, True),
               "worker_a": ("claude-sonnet-5-5", 10_000, 400_000, 10_000, False)}

    def seed_context(self):
        self.sleeper = subprocess.Popen(["sleep", "infinity"], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stat = Path(f"/proc/{self.sleeper.pid}/stat").read_text()
        start = int(stat[stat.rindex(")") + 1:].split()[19])
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        process = {"pid": self.sleeper.pid, "start": start, "boot": boot}
        bindings = self.runtime / "bindings"
        bindings.mkdir(mode=0o700)
        expected = {}
        for harness in ("claude", "opencode"):
            state_dir, home = self.runtime / f"state-{harness}", self.runtime / f"{harness}-home"
            state_dir.mkdir(mode=0o700)
            home.mkdir(mode=0o700)
            disposable_host.write_private(bindings / f"{harness}.json", json.dumps(
                {"harness": harness, "state_dir": str(state_dir), "config_home": str(home)}))
        now = datetime.now(timezone.utc)
        for role, (model, fresh, read, written, near) in self.WINDOWS.items():
            session = self.sessions.get(role)
            if not session:
                continue
            native, workspace = uid(), str(self.runtime / "ws" / role)
            slug = re.sub(r"[^A-Za-z0-9]", "-", workspace)
            folder = self.runtime / "claude-home" / "projects" / slug
            folder.mkdir(mode=0o700, parents=True)
            lines = [{"type": "system", "subtype": "compact_boundary", "timestamp": (now - timedelta(minutes=30)).isoformat()},
                     {"type": "assistant", "timestamp": (now - timedelta(minutes=1)).isoformat(),
                      "message": {"model": model, "usage": {"input_tokens": fresh, "cache_read_input_tokens": read,
                                                            "cache_creation_input_tokens": written}}}]
            disposable_host.write_private(folder / f"{native}.jsonl", "\n".join(json.dumps(line) for line in lines) + "\n")
            disposable_host.write_private(self.runtime / "state-claude" / f"{role}.json", json.dumps({
                "process": process, "workspace": workspace,
                "agent": {"agent_id": session["agent_id"], "native_session_id": native, "metadata": {"harness": "claude"}}}))
            used = fresh + read + written
            expected[session["agent_id"]] = dict(role=role, used=used, window=1_000_000, near=near)
        worker_b = self.sessions["worker_b"]
        disposable_host.write_private(self.runtime / "state-opencode" / "worker_b.json", json.dumps({
            "process": process, "workspace": str(self.runtime / "ws" / "worker_b"),
            "agent": {"agent_id": worker_b["agent_id"], "native_session_id": uid(), "metadata": {"harness": "opencode"}}}))
        self.context_expected = expected
        self.report["context_seed"] = {"claude_sessions": sorted(v["role"] for v in expected.values()),
                                       "unknown_session": "worker_b (opencode)"}
        self.seeded("bindings, session state and sample transcripts for the context room")

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
                token_role = "root" if role.startswith("root_") else role
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
        self.check("the context room shows the transcript fill, the near-limit mark and unknowns", lambda: self.context_cell(page), page)
        self.check("every member row says what it works on, by source", lambda: self.working_on(page), page)
        self.check("open work survives the ledger history cap", lambda: self.open_ledger_work(page), page)
        self.check("the header counts enrolled, online and stopped apart", lambda: self.header_counts(page), page)
        self.check("waits on, oldest pending and waited on are separate facts", lambda: self.waiting_facts(page), page)
        self.check("needs-you rows: board opens the event, failed delivery names its request", lambda: self.needs_rows(page), page)
        self.check("every command a needs-you row offers runs with exit 0", lambda: self.copy_commands(page), page,
                   category="slice:CAIRN-831")
        self.check("send is offered only to current principals", lambda: self.current_principals(page), page)
        self.check("paper cuts: close button, selection, reply naming, plain words, filtered count", lambda: self.paper_cuts(page), page)
        self.check("flow rows show each recipient's state; agent rows label waits apart", lambda: self.flow_rows(page), page,
                   category="slice:CAIRN-830")
        self.check("needs you lists the board question and the operator event, not the lead decision",
                   lambda: self.needs_you(page), page, category="slice:CAIRN-831")
        self.check("every needs-you type the fixture can produce appears", lambda: self.need_types(page), page,
                   category="slice:CAIRN-831")
        self.check("cancel preview names the recipient and waits before confirm; siblings and counted reply unchanged",
                   lambda: self.cancel(page), page, category="slice:CAIRN-830+833")
        self.check("a refused cancel shows the store's reason and cannot be confirmed", lambda: self.refused_cancel(page), page,
                   category="slice:CAIRN-833")
        self.check("a board question from before the campaign says plainly where it is", lambda: self.early_board_question(page), page,
                   category="slice:CAIRN-831")
        self.check("acknowledge is offered only on the inbox head, and acknowledges it",
                   lambda: self.acknowledge_head(page), page, category="slice:CAIRN-831")
        self.check("an owner notice is shown as sent, not yet seen", lambda: self.send_unseen(page), page,
                   category="slice:CAIRN-833")
        self.check("a landing Needs you row opens its event with the empty campaign; no raw principal on the landing page",
                   lambda: self.landing_event(page), page, category="slice:CAIRN-833")
        self.check("empty summaries say what the message is, in the flow and in Details", lambda: self.empty_summaries(page), page)
        self.check("the landing page acknowledges a delivery outside any campaign; a campaign page says it is not the next",
                   lambda: self.landing_acknowledge(page), page, category="slice:CAIRN-833")
        self.check("a request Cairn's scheduler sent to the owner reads as from Cairn in Needs you, desktop and 390 px",
                   lambda: self.needs_you_system(page), page, category="slice:CAIRN-831")
        self.check("Supersede is offered only on the owner's notice that is still open", lambda: self.supersede_open_only(page), page,
                   category="slice:CAIRN-833")
        self.check("a cancel confirmed after a handoff is refused with a fresh preview, then confirmed again",
                   lambda: self.cancel_conflict(page), page, category="slice:CAIRN-833")
        self.check("a wait cancelled after its owner changed is refused with the current wait, then confirmed again",
                   lambda: self.wait_conflict(page), page, category="slice:CAIRN-833")
        self.check("every Cairn-published event reads as from Cairn wherever a sender is shown", lambda: self.system_everywhere(page), page,
                   category="slice:CAIRN-830")
        self.check("an open Cairn delivery whose event is outside the window still reads as from Cairn",
                   lambda: self.system_outside_window(page), page, category="slice:CAIRN-830")
        self.check("an ended session shows sign-in over dialogs and on the landing page; routes answer 401",
                   lambda: self.signed_out_routes(page), page)
        self.check("a member that left gets no send control and the store refuses a send to it",
                   lambda: self.member_left(page), page, category="slice:CAIRN-833")
        self.check("member details limits cancellation to in-scope recipients and wait owners",
                   lambda: self.external_cancellation_controls(page), page)
        self.check("no forbidden control exists", lambda: self.forbidden(page), page)
        self.check("screenshots of the owner's views at 1280 px", lambda: self.view_shots(page, "desktop"), page, category="evidence")
        self.shot(page, "desktop-final")

    def external_cancellation_controls(self, page):
        outbound = self.publish("lead", "Outside recipient", self.to("bystander"), summary="Outside recipient")
        inbound = self.publish("bystander", "Outside wait owner", self.to("lead"), summary="Outside wait owner")
        self.wait("lead", "request_response", outbound["event_id"])
        self.wait("bystander", "request_response", inbound["event_id"])
        self.member_row(page, "lead").get_by_role("button", name="Details").click()
        try:
            body = page.locator("#detail-body")
            for event, can_cancel in [(inbound, True), (outbound, False)]:
                row = body.locator(f"ul.deliveries-list li[data-event='{event['event_id']}']")
                self.expect(row).to_be_visible(timeout=15000)
                self.expect(row.get_by_role("button", name="Cancel this recipient's delivery")).to_have_count(int(can_cancel))
            waits = body.locator("h3", has_text="Waits").locator("xpath=following-sibling::ul[1]").locator("li")
            for summary, can_cancel in [("Outside recipient", True), ("Outside wait owner", False)]:
                row = waits.filter(has_text=summary)
                self.expect(row).to_be_visible(timeout=15000)
                self.expect(row.get_by_role("button", name="Cancel the wait")).to_have_count(int(can_cancel))
        finally:
            page.locator("#detail button.close").click()

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

    def member_row(self, page, role):
        return page.locator(f"#agents .agent[data-member='{self.principal(role)}']")

    def context_cell(self, page):
        observed = self.page_json("/api/swarm/panes")["window"]
        self.report["window_observation"] = {k: observed.get(k) for k in ("status", "error", "stale", "sessions", "unknown")}
        require(observed.get("status") == "ok", "the transcript reader is not ok: " + json.dumps(observed)[:300])
        windows, unknown = observed.get("windows") or {}, observed.get("unknown") or {}
        self.expect(page.locator("#observations")).to_contain_text("Transcripts")
        for agent_id, want in self.context_expected.items():
            require(agent_id in windows, f"the reader did not measure {want['role']}: " + json.dumps(unknown.get(agent_id)))
            got = windows[agent_id]
            require(got["used_tokens"] == want["used"] and got["window_tokens"] == want["window"] and got["near_limit"] == want["near"],
                    f"the reader measured {want['role']} differently: {got}")
            role = "root" if want["role"] == "root_next" else want["role"]
            row = page.locator(f"#agents .agent[data-member='agent/{agent_id}']")
            text = row.inner_text()
            percent = f"{round(want['used'] * 100 / want['window'])}% · "
            require(percent in text, f"{role}'s row does not show its fill {percent!r}: " + text[:300])
            require("1 compaction" in text, f"{role}'s row does not show the transcript's compaction")
            if want["near"]:
                require("near context limit" in text and "near its limit" in text, f"{role}'s row lacks the near-limit mark")
                require("near-limit-row" in (row.get_attribute("class") or ""), f"{role}'s row is not marked near its limit")
            else:
                require("near context limit" not in text, f"{role}'s row is marked near its limit")
        near = sum(1 for want in self.context_expected.values() if want["near"])
        if near:
            self.expect(page.locator("#near-limit-count")).to_contain_text(f"{near} agent{'' if near == 1 else 's'} near")
        worker_b = self.sessions["worker_b"]["agent_id"]
        require(worker_b in unknown, "the opencode session is not reported unknown by the reader")
        self.expect(self.member_row(page, "worker_b")).to_contain_text("unknown: " + unknown[worker_b][:40])
        self.expect(self.member_row(page, "lead")).to_contain_text("unknown: no transcript on this host")
        cases = [({"used_tokens": 807000, "window_tokens": 1000000}, "81% · 807k of 1M tokens"),
                 ({"used_tokens": 807000, "window_tokens": 0}, "807k tokens"),
                 ({"used_tokens": 150000, "window_tokens": 200000}, "75% · 150k of 200k tokens"),
                 ({"used_tokens": 1234567, "window_tokens": 2000000}, "62% · 1.2M of 2M tokens")]
        for window, expected in cases:
            got = page.evaluate("(w) => window.cairnSwarmView.formatWindow(w)", window)
            require(got == expected, f"formatWindow({window}) gave {got!r}, expected {expected!r}")

    def working_on(self, page):
        # Every member row answers "working on what" without opening anything.
        for role in ("root_next", "lead", "worker_a", "worker_b", "worker_c"):
            if role not in self.sessions:
                continue
            row = page.locator(f"#agents .agent[data-member='{self.sessions[role]['inbox']}']")
            text = row.inner_text()
            require("no assignment recorded" not in text, f"{role} shows no assignment recorded")
        for role in ("root_next", "lead"):
            text = page.locator(f"#agents .agent[data-member='{self.sessions[role]['inbox']}']").inner_text()
            require("Coordinates" in text, f"{role} does not say what it coordinates")
            require("Reported by the session" in text, f"{role} hides what its session reports")
        worker_c = self.member_row(page, "worker_c").inner_text()
        require("Reported by the session" in worker_c, "a worker with no assignment hides its reported task")
        worker_a = self.member_row(page, "worker_a").inner_text()
        require(worker_a.index("Ledger assignments") < worker_a.index("Reported by the session"), "assignments do not come first")

    def open_ledger_work(self, page):
        # The capped ledger is ordered by kind and key. Fill its first 500
        # entries with closed assignments before slice-a and slice-b.
        for index in range(500):
            self.ledger("set", "--kind", "assignment", "--key", f"00-history-{index:03d}",
                        "--status", "completed", "--summary", "Finished fixture work")
        snapshot = self.snapshot()
        require(snapshot.get("ledger_truncated") and not any(item["open"] for item in snapshot["ledger"]),
                "the fixture did not push all open work beyond the history cap")
        page.reload()
        lead = self.member_row(page, "lead")
        # Derive the expected number from the two workers' complete assignments.
        assignments = sum(item["kind"] == "assignment" for m in snapshot["members"]
                          if m.get("lead") == self.principal("lead") for item in m["holds"])
        require(assignments > 0, "no open assignment in lead's subtree")
        self.expect(page.locator("#flow-meta")).to_contain_text("ledger links may be incomplete")
        self.expect(lead).to_contain_text(f"{assignments} assignment")
        self.member_row(page, "worker_a").get_by_role("button", name="Details").click()
        try:
            held = page.locator("#detail-body h3", has_text="Ledger items held").locator("xpath=following-sibling::*[1]")
            self.expect(held).to_contain_text("slice-a")
        finally:
            page.locator("#detail button.close").click()

    def header_counts(self, page):
        snapshot = self.snapshot()
        enrolled = [m for m in snapshot["members"] if m["active"]]
        presence = {}
        for m in enrolled:
            p = (m.get("session") or {}).get("presence")
            presence[p] = presence.get(p, 0) + 1
        text = page.locator("#campaign-meta").inner_text()
        require(f"{len(enrolled)} members enrolled" in text, "enrolled count missing: " + text)
        for p in ("online", "stopped"):
            if presence.get(p):
                require(f"{presence[p]} {p}" in text, f"the header does not count {p} members: " + text)
        require(" active member" not in text, "the header still says active members: " + text)

    def waiting_facts(self, page):
        lead = self.member_row(page, "lead")
        labels = lead.locator("dl.facts dt").all_inner_texts()
        require(labels == ["Waits on", "Oldest pending", "Waited on"], "waiting facts are not three separate labels: " + json.dumps(labels))
        snapshot = self.snapshot()
        member = snapshot["flow"]["by_member"].get(self.principal("lead"), {})
        waited = lead.locator("dl.facts dd").nth(2).inner_text()
        if not member.get("peer_waits"):
            require("no peer waiting" in waited, "a member with no peer waiting does not say so: " + waited)
        worker_b = self.member_row(page, "worker_b").locator("dl.facts dd").nth(2).inner_text()
        if snapshot["flow"]["by_member"].get(self.principal("worker_b"), {}).get("peer_waits"):
            require("waits for" in worker_b, "worker_b's peer waits are not under Waited on")

    def needs_rows(self, page):
        board = page.locator(f"#needs li.need[data-event='{self.events['board']['event_id']}']")
        self.expect(board.locator("button", has_text="Open the event")).to_have_count(1)
        require(board.inner_text().count("on the board") == 1, "the board row repeats its action text")
        buttons = board.locator("button").all_inner_texts()
        require(all(b in ("Open the event",) or b.startswith("Copy") for b in buttons), "a board question offers a compose control: " + json.dumps(buttons))
        board.locator("button", has_text="Open the event").click()
        self.expect(page.locator("#event-body")).to_contain_text("Answer it on the board", timeout=10000)
        page.locator("#event-detail button.close").click()
        for kind in ("handoff_due", "missed_checkpoint", "wait_timed_out"):
            row = page.locator(f"#needs li.need[data-type='{kind}']").first
            meta = row.locator("p.meta").inner_text().strip()
            require(not meta.endswith("·") and "· ·" not in meta, f"the {kind} row has a dangling separator: {meta!r}")
            item = next(i for i in self.snapshot()["needs_you"]["items"] if i["type"] == kind)
            target = (item.get("refs") or {}).get("principal")
            require(target, f"the {kind} row carries no refs.principal")
            current = page.evaluate("(p) => window.cairnSwarmView.canWrite(p)", target)
            buttons = row.locator("button", has_text="Write to").count()
            require(buttons == (1 if current else 0), f"the {kind} row's send control does not match whether {target} is current")
            if kind == "handoff_due" and not item.get("since"):
                require("since" not in meta, f"the handoff row shows a since without one: {meta!r}")
        failed = page.locator("#needs li.need[data-type='failed_delivery']").first.inner_text()
        require("Run the flaky check" in failed and self.label(self.snapshot(), "lead") in failed,
                "the failed-delivery row does not name the request and its sender: " + failed[:300])

    def early_board_question(self, page):
        early = self.events.get("early_board")
        require(early, "the fixture did not publish a board question before the campaign")
        row = page.locator(f"#needs li.need[data-event='{early['event_id']}']")
        self.expect(row).to_have_count(1, timeout=10000)
        with page.expect_response(lambda r: "/api/swarm/event?" in r.url) as info:
            row.locator("button", has_text="Open the event").click()
        require(info.value.status == 404, f"the detail route answered {info.value.status} for a pre-campaign question")
        body = page.locator("#event-body")
        self.expect(body).to_contain_text("not in this campaign's log", timeout=10000)
        self.expect(body).to_contain_text("on the board")
        require("Refused" not in body.inner_text(), "the refusal is shown as a generic failure")
        page.locator("#event-detail button.close").click()

    def acknowledge_head(self, page):
        items = [i for i in self.snapshot()["needs_you"]["items"] if i["type"] == "operator_delivery"]
        heads = [i for i in items if i.get("inbox_head") is True]
        rows = page.locator("#needs li.need[data-type='operator_delivery']")
        for item in items:
            row = page.locator(f"#needs li.need[data-need='{item['id']}']")
            has_button = row.locator("button", has_text="Acknowledge").count() == 1
            require(has_button == (item.get("inbox_head") is True), f"acknowledge offered on the wrong row: {item['title']}")
            if not has_button:
                self.expect(row).to_contain_text("Not the next delivery in your inbox")
        if not heads:
            raise AssertionError("waiting: the snapshot marks no inbox_head row, so no row offers Acknowledge (needs-you author's field)")
        head = page.locator(f"#needs li.need[data-need='{heads[0]['id']}']")
        head_event = (heads[0].get("refs") or {}).get("event_id")
        early = self.events.get("early_request")
        require(early and head_event == early["event_id"], "the request from before the campaign is not the owner's oldest delivery")
        in_log = head_event in page.evaluate("() => window.cairnSwarmView.logIDs()")
        require(not in_log, "the pre-campaign request is in the campaign log")
        head.locator("button", has_text="Acknowledge").click()
        # Readable through the inbox only: the dialog warns and offers the event.
        only = page.locator("#confirm-body .only-link")
        self.expect(only).to_contain_text("only while it is in your inbox")
        require(only.locator("button", has_text="Open the event").count() == 1, "the confirmation offers no Open the event")
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/acknowledge")) as info:
            page.locator("#confirm-go").click()
        reply = info.value.json()
        require(info.value.ok and (reply.get("delivery") or {}).get("state") == "handled", "acknowledge: " + json.dumps(reply)[:300])
        self.expect(page.locator(f"#needs li.need[data-need='{heads[0]['id']}']")).to_have_count(0, timeout=15000)
        page.locator("#confirm-keep").click()
        # After acknowledging, the page has no link to it: the event route no longer opens it here.
        status = page.evaluate("async ([c, e]) => (await fetch(`/api/swarm/event?campaign=${c}&event_id=${e}`, {cache: 'no-store'})).status",
                               [CAMPAIGN, head_event])
        require(status == 404, f"the campaign page can still open an acknowledged inbox-only event ({status})")
        # The next head's event is in the campaign log: its confirmation says nothing extra.
        page.wait_for_timeout(2500)
        nxt = [i for i in self.snapshot()["needs_you"]["items"] if i["type"] == "operator_delivery" and i.get("inbox_head") is True]
        require(nxt, "no next operator delivery is the head")
        require((nxt[0].get("refs") or {}).get("event_id") in page.evaluate("() => window.cairnSwarmView.logIDs()"),
                "the next head's event is not in the campaign log")
        page.locator(f"#needs li.need[data-need='{nxt[0]['id']}'] button", has_text="Acknowledge").click()
        require(page.locator("#confirm-body .only-link").count() == 0, "the confirmation warns about a message the page can still open")
        page.locator("#confirm-keep").click()

    def no_null(self, locator, where):
        lines = [line.strip() for line in locator.inner_text().splitlines()]
        require("null" not in lines, f"{where} shows the text null")

    def landing_event(self, page):
        outside = self.events["owner_outside"]
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            row = landing.locator(f"#landing-needs li.need[data-event='{outside['event_id']}']")
            self.expect(row).to_have_count(1, timeout=20000)
            require("local-uid:" not in landing.locator("#landing").inner_text(), "the landing page shows a raw principal")
            with landing.expect_response(lambda r: "/api/swarm/event?" in r.url) as info:
                row.locator("button", has_text="Open the event").click()
            require("campaign=&" in info.value.url, "the landing page did not read the event with the empty campaign")
            require(info.value.ok, "the event route refuses the landing scope: " + json.dumps(info.value.json())[:200])
            body = landing.locator("#event-body")
            self.expect(body).to_contain_text("Outside any campaign.", timeout=10000)
            self.no_null(body, "the landing event panel")
            landing.locator("#event-detail button.close").click()
            # A landing row whose event is in the landing Messages opens it too: the
            # bystander's timed-out wait names its dependency event.
            waits = [i for i in self.page_json("/api/swarm/landing")["needs_you"]["items"] if i["type"] == "wait_timed_out"]
            require(waits, "the landing page lists no timed-out wait")
            wait_event = (waits[0].get("refs") or {}).get("event_id")
            wait_row = landing.locator(f"#landing-needs li.need[data-need='{waits[0]['id']}']")
            self.expect(wait_row.locator("button", has_text="Open the event")).to_have_count(1, timeout=10000)
            with landing.expect_response(lambda r: "/api/swarm/event?" in r.url) as opened:
                wait_row.locator("button", has_text="Open the event").click()
            require(opened.value.ok, "the landing wait row's event does not open: " + json.dumps(opened.value.json())[:200])
            self.expect(landing.locator("#event-body")).to_contain_text("Unrelated request.", timeout=10000)
            self.no_null(landing.locator("#event-body"), "the landing wait event panel")
            self.report["landing_wait_event"] = wait_event
        finally:
            landing.close()

    def landing_acknowledge(self, page):
        operator = self.report["operator_principal"]
        # Clear the campaign's own operator deliveries through the campaign page, head first.
        for _ in range(10):
            head = page.locator("#needs li.need[data-type='operator_delivery'] button", has_text="Acknowledge")
            if head.count() == 0:
                break
            head.first.click()
            with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/acknowledge")) as info:
                page.locator("#confirm-go").click()
            require(info.value.ok, "acknowledging the campaign head failed: " + json.dumps(info.value.json())[:200])
            page.locator("#confirm-keep").click()
            page.wait_for_timeout(2500)
        # A delivery to the operator that belongs to no campaign is now the head; a newer campaign one is not.
        outside = self.events["owner_outside"]
        require(outside, "the seed has no delivery to the owner outside any campaign")
        inside = self.publish("worker_b", "Inside the campaign.", {"type": "agent", "name": operator}, kind="request",
                              summary="For the owner: flow reader request")
        row = page.locator(f"#needs li.need[data-event='{inside['event_id']}']")
        self.expect(row).to_have_count(1, timeout=20000)
        self.expect(row).to_contain_text("Not the next delivery in your inbox")
        require(row.locator("button", has_text="Acknowledge").count() == 0, "a campaign page offers Acknowledge on a delivery that is not the head")
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            outside_row = landing.locator(f"#landing-needs li.need[data-event='{outside['event_id']}']")
            self.expect(outside_row).to_have_count(1, timeout=20000)
            controls = outside_row.locator("button").all_inner_texts()
            require(all(c in ("Acknowledge", "Open the event") or c.startswith("Copy") for c in controls),
                    "the landing page offers another action: " + json.dumps(controls))
            require(outside_row.locator("button", has_text="Acknowledge").count() == 1, "the landing head offers no Acknowledge")
            with landing.expect_response(lambda r: "/api/swarm/event?campaign=&" in r.url) as info:
                outside_row.get_by_role("button", name="Open the event", exact=True).click()
            require(info.value.ok and info.value.json().get("body") == "Outside any campaign.",
                    "landing event detail did not return the referenced body")
            self.expect(landing.locator("#event-body pre.body")).to_have_text("Outside any campaign.")
            require(landing.locator("#event-body button").count() == 0, "the landing event detail offers a write control")
            landing.locator("#event-detail button.close").click()
            outside_row.locator("button", has_text="Acknowledge").click()
            self.no_null(landing.locator("#confirm-body"), "the landing acknowledge dialog")
            # The event is also in the landing Messages: the page keeps a link, so no warning.
            require(landing.locator(f"#landing-events li[data-event='{outside['event_id']}']").count() == 1,
                    "the landing delivery's event is not in the landing Messages")
            require(landing.locator("#confirm-body .only-link").count() == 0, "the landing dialog warns although Messages keeps the link")
            with landing.expect_response(lambda r: r.url.endswith("/api/swarm/action/acknowledge")) as info:
                landing.locator("#confirm-go").click()
            sent = info.value.request.post_data_json or {}
            require(sent.get("campaign") == "", "the landing page did not send the empty campaign")
            reply = info.value.json()
            if not info.value.ok:
                raise AssertionError("waiting: the store refuses an acknowledge with the empty (landing) campaign: "
                                     + str(reply.get("code")) + " " + str(reply.get("error"))[:160])
            require((reply.get("delivery") or {}).get("state") == "handled", "landing acknowledge: " + json.dumps(reply)[:300])
            self.expect(landing.locator(f"#landing-needs li.need[data-event='{outside['event_id']}']")).to_have_count(0, timeout=15000)
            landing.locator("#confirm-keep").click()
            # After acknowledging and a reload, Messages still opens it.
            landing.reload()
            message = landing.locator(f"#landing-events li[data-event='{outside['event_id']}']")
            self.expect(message).to_have_count(1, timeout=15000)
            with landing.expect_response(lambda r: "/api/swarm/event?" in r.url) as opened:
                message.locator("button", has_text="Open").click()
            require(opened.value.ok, "the landing Messages entry no longer opens after acknowledging")
            self.expect(landing.locator("#event-body")).to_contain_text("Outside any campaign.", timeout=10000)
            landing.locator("#event-detail button.close").click()
        finally:
            landing.close()
        campaign_ack = row.locator("button", has_text="Acknowledge")
        self.expect(campaign_ack).to_have_count(1, timeout=15000)
        campaign_ack.click()
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/acknowledge")) as info:
            page.locator("#confirm-go").click()
        reply = info.value.json()
        require(info.value.ok and (reply.get("delivery") or {}).get("state") == "handled",
                "campaign request acknowledge: " + json.dumps(reply)[:300])
        page.locator("#confirm-keep").click()

    def empty_summaries(self, page):
        wait_rows = page.locator("#flow li.event", has_text="Cairn (wait notice)")
        require(wait_rows.count() >= 1, "no Cairn wait notice in the flow")
        for text in wait_rows.all_inner_texts():
            require("Wait notice from Cairn" in text and "no summary" not in text, "a Cairn wait notice has no words: " + text[:160])
        page.locator(f"#agents .agent[data-member='{self.principal('lead')}'] button", has_text="Details").click()
        try:
            detail = page.locator("#detail")
            responses = [t for t in detail.locator("li").all_inner_texts() if "response" in t.split("\n")[0] or " response " in t]
            require(responses, "the lead's Details list no response")
            for text in responses:
                require("Reply to:" in text, "a response in Details has a blank summary: " + text[:160])
        finally:
            page.locator("#detail button.close").click()

    def copy_commands(self, page):
        env = dict(self.host.env, PATH=str(self.runtime) + os.pathsep + self.host.env.get("PATH", ""))
        commands = sorted({i["action"]["command"] for i in self.snapshot()["needs_you"]["items"] if i.get("action", {}).get("command")})
        failed = []
        for command in commands:
            result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=30,
                                    stdin=subprocess.DEVNULL, cwd=str(self.runtime))
            if result.returncode != 0:
                failed.append(f"{command[:120]} -> exit {result.returncode}: {(result.stdout + result.stderr).strip()[:160]}")
        self.report["copy_commands"] = dict(run=len(commands), failed=failed)
        require(commands, "no row offers a command to copy")
        require(not failed, "copy commands fail: " + " | ".join(failed))

    def current_principals(self, page):
        snapshot = self.snapshot()
        lineage = snapshot.get("lineage") or []
        require(lineage, "the fixture's handoff is not in the lineage")
        old = lineage[0]["from_principal"]
        can = lambda p: page.evaluate("(p) => window.cairnSwarmView.canWrite(p)", p)
        require(can(old) is False, "the page would send to a coordinator that handed off")
        require(can(self.principal("lead")) is True, "the page would not send to an active member")
        require(can(snapshot["campaign"]["role_address"]) is True, "the page would not send to the role address")
        require(page.locator("#lineage button").count() == 0, "the lineage offers a control")
        root = page.locator(f"#agents .agent[data-member='{self.sessions['root_next']['inbox']}'] button", has_text="Details")
        root.click()
        options = page.locator("#detail form.compose select[name=to] option").evaluate_all("nodes => nodes.map(n => n.value)")
        require(snapshot["campaign"]["role_address"] in options, "the root holder cannot be written to by its role address")
        page.locator("#detail button.close").click()

    def paper_cuts(self, page):
        # A long event title does not wrap the Close button.
        row = page.locator("#flow li.event", has_text="Untrusted").first
        row.locator("button", has_text="Open").click()
        close = page.locator("#event-detail .panel-head button.close")
        box = close.bounding_box()
        require(box and box["height"] < 40, f"the Close button wraps: {box}")
        page.locator("#event-detail button.close").click()
        # Selecting text in an agent row does not open the detail panel.
        cell = self.member_row(page, "worker_a").locator("td").nth(1)
        cell.evaluate("(td) => { const r = document.createRange(); r.selectNodeContents(td); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }")
        cell.click()
        require(not page.evaluate("() => document.getElementById('detail').open"), "a click while selecting text opened the detail panel")
        page.evaluate("() => getSelection().removeAllRanges()")
        # A response names the request it answers; a superseded chip says it once.
        response = page.locator("#flow li.event-response").first
        self.expect(response).to_contain_text("Reply to:")
        chips = page.locator("#flow .chip-superseded").all_inner_texts()
        require(chips and all("(superseded)" not in c for c in chips), "a superseded chip says it twice: " + json.dumps(chips[:2]))
        require("no delivery in the snapshot" not in page.locator("#flow").inner_text(), "the flow still says no delivery in the snapshot")
        # The count follows the filter.
        self.member_row(page, "worker_b").locator("button", has_text="Messages").click()
        self.expect(page.locator("#flow-meta")).to_contain_text(re.compile(r"\d+ of \d+ events in the window, filtered to"))
        self.member_row(page, "worker_b").locator("button", has_text="All messages").click()

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
        board = page.locator(f"#needs li.need[data-event='{self.events['board']['event_id']}']")
        self.expect(board).to_contain_text("Answer on the board")
        buttons = board.locator("button").all_inner_texts()
        require(all(b == "Open the event" or b.startswith("Copy") for b in buttons), "a board question offers a control: " + json.dumps(buttons))

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
            require("waits for" in worker_b, "worker_b's peer waits are not shown")

    def need_types(self, page):
        items = self.snapshot()["needs_you"]["items"]
        types = sorted({item["type"] for item in items})
        self.report["needs_you_types"] = dict(produced=types, not_producible=self.UNPRODUCED_NEEDS)
        missing = [t for t in self.PRODUCED_NEEDS if t not in types]
        require(not missing, "needs-you types missing from the seeded campaign: " + ", ".join(missing))
        for t in types:
            require(page.locator(f"#needs li.need[data-type='{t}']").count() >= 1, f"the page does not show the {t} row")

    def open_member_delivery(self, page, role, delivery_id):
        """Open the member's detail panel and the cancel control of one of its open deliveries."""
        self.expect(page.locator(f"#agents .agent[data-member='{self.principal(role)}']")).to_be_visible()
        page.locator(f"#agents .agent[data-member='{self.principal(role)}'] button", has_text="Details").click()
        item = page.locator("#detail li", has=page.locator(f"[data-delivery='{delivery_id}']"))
        self.expect(item).to_have_count(1, timeout=15000)
        item.locator("button", has_text="Cancel this recipient's delivery").click()

    def cancel(self, page):
        snapshot = self.snapshot()
        group_id = self.events["group"]["event_id"]
        group = [d for d in snapshot["flow"]["deliveries"] if d["event_id"] == group_id]
        require(len(group) == 3, "the grouped request does not have its three deliveries in the snapshot")
        target = next((d for d in group if d["consumer"] == self.principal("worker_a") and d["display"] == "pending"), None)
        require(target, "worker_a's pending group delivery is not in the snapshot")
        before_status = {d["delivery_id"]: d["state"] for d in self.event_status(group_id)["deliveries"]}
        before_group = self.group_state()
        # Hold the real preview reply for 3 s: the confirm control must stay
        # disabled until it has loaded. The reply itself is the server's.
        held = []

        def delay(route):
            response = route.fetch()
            held.append(response.status)
            try:
                self.preview_token = json.loads(response.text()).get("preview_token") or ""
            except ValueError:
                self.preview_token = ""
            page.wait_for_timeout(3000)
            route.fulfill(response=response)
        page.route("**/api/swarm/action/cancel-preview*", delay)
        try:
            self.open_member_delivery(page, "worker_a", target["delivery_id"])
            go = page.locator("#confirm-go")
            self.expect(page.locator("#confirm")).to_be_visible()
            require(go.is_disabled(), "confirm is enabled before the preview has loaded")
            body = page.locator("#confirm-body")
            self.expect(body).to_contain_text("Recipient", timeout=15000)
            self.no_null(body, "the cancel preview")
        finally:
            page.unroute("**/api/swarm/action/cancel-preview*")
        require(held == [200], f"the preview did not answer 200: {held}")
        text = body.inner_text()
        require(self.label(snapshot, "worker_a") in text and self.principal("worker_a") in text,
                "the preview does not name the exact recipient (label and principal)")
        # Direct-reply waits on the grouped request: none were registered.
        direct = body.locator(".lookup", has_text="Direct-reply waits").inner_text()
        require("None" in direct, "the direct-reply lookup is not empty: " + direct)
        # The group: one counted reply (worker_c) of three, worker_a's does not count, one closure wait by the lead.
        require("1 of 3 replies counted" in text, "the preview does not give the group's reply count")
        require("This recipient's reply does not count yet" in text, "the preview misstates whether this reply counts")
        closure = body.locator(".lookup", has_text="Waits on the group's closure").inner_text()
        require("group closure" in closure and self.label(snapshot, "lead") in closure, "the closure wait of the lead is not shown: " + closure)
        siblings = body.locator(".lookup", has_text="Other recipients").inner_text()
        for role in ("worker_b", "worker_c"):
            require(self.label(snapshot, role) in siblings, f"sibling {role} is not listed")
        self.expect(go).to_be_enabled(timeout=5000)
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/cancel-delivery")) as info:
            go.click()
        reply = info.value.json()
        sent = info.value.request.post_data_json or {}
        self.report["preview_token"] = dict(served=bool(self.preview_token), sent=bool(sent.get("preview_token")))
        require(sent.get("preview_token", "") == self.preview_token, "the cancel did not send the preview's token")
        require(info.value.ok and reply.get("outcome") == "cancelled", "cancel outcome: " + json.dumps(reply)[:300])
        self.expect(page.locator("#confirm-result")).to_contain_text("The store recorded the cancellation", timeout=10000)
        self.expect(page.locator("#confirm-keep")).to_have_text("Close")
        after_status = {d["delivery_id"]: d["state"] for d in self.event_status(group_id)["deliveries"]}
        require(after_status[target["delivery_id"]] == "failed", "the cancelled delivery is not failed in the store")
        for delivery, state in before_status.items():
            if delivery != target["delivery_id"]:
                require(after_status.get(delivery) == state, "a sibling delivery changed state")
        require(self.group_state()["responded"] == before_group["responded"], "the counted reply changed")
        self.report["cancel_outcome"] = reply.get("outcome")

    def current_root(self):
        return self.root_role

    def handoff_from(self, role, to_role):
        harness, model = HARNESS["root"]
        result = self.cli(["ledger", "handoff", *self.session_flags(role), "--campaign", CAMPAIGN,
                           "--new-binding", "swarm-fixture-" + to_role, "--new-native-session", "swarm-fixture-" + uid(),
                           "--workspace", str(self.runtime / to_role), "--harness", harness, "--project", "cairn",
                           "--model", model, "--reason", "Fixture handoff during a confirmation", "--request-id", uid()])
        successor = result.get("to_session") or {}
        require(successor.get("agent_id"), "the handoff named no successor session")
        self.sessions[to_role] = dict(successor, inbox="agent/" + successor["agent_id"])
        self.host.identities[to_role] = self.host.identities["root"]
        self.root_role = to_role
        return result

    def wait_for_snapshot(self, page, predicate, what, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.snapshot()
            if predicate(snapshot):
                return snapshot
            page.wait_for_timeout(1000)
        raise AssertionError("the snapshot never showed " + what)

    def cancel_conflict(self, page):
        # A notice to the coordinator, previewed, then a handoff moves it to the
        # successor before the owner confirms: the server answers 409 with a
        # fresh preview naming the new recipient, and the page asks again.
        old = self.current_root()
        marker = "conflict-" + uid()[:8]
        event = self.publish("lead", "To the coordinator.", self.to(old), kind="notice", action="review", summary="Coordinator " + marker)
        snapshot = self.wait_for_snapshot(page, lambda s: any(d["event_id"] == event["event_id"] for d in s["flow"]["deliveries"]),
                                          "the notice to the coordinator")
        delivery = next(d for d in snapshot["flow"]["deliveries"] if d["event_id"] == event["event_id"])
        page.locator(f"#agents .agent[data-member='{self.principal(old)}'] button", has_text="Details").click()
        item = page.locator("#detail li", has=page.locator(f"[data-delivery='{delivery['delivery_id']}']"))
        item.locator("button", has_text="Cancel this recipient's delivery").click()
        body = page.locator("#confirm-body")
        self.expect(body).to_contain_text(self.principal(old), timeout=15000)
        self.expect(page.locator("#confirm-go")).to_be_enabled()
        new = "root_" + str(int(old.split("_")[-1]) + 1) if old.split("_")[-1].isdigit() else "root_3"
        self.handoff_from(old, new)
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/cancel-delivery")) as info:
            page.locator("#confirm-go").click()
        first = info.value.json()
        require(info.value.status == 409 and first.get("code") == "VERSION_CONFLICT" and first.get("preview"),
                "a confirmation with the old preview was not refused with a fresh preview: " + json.dumps(first)[:300])
        self.expect(body).to_contain_text("changed since it was shown", timeout=5000)
        self.expect(body).to_contain_text(self.principal(new))
        self.expect(page.locator("#confirm-go")).to_be_enabled()
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/cancel-delivery")) as info:
            page.locator("#confirm-go").click()
        second = info.value.json()
        require(info.value.ok and second.get("outcome") == "cancelled", "the second confirmation: " + json.dumps(second)[:300])
        sent = info.value.request.post_data_json or {}
        require(sent.get("preview_token") == first["preview"].get("preview_token"), "the second confirmation did not use the fresh token")
        self.expect(page.locator("#confirm-keep")).to_have_text("Close")
        page.locator("#confirm-keep").click()
        page.locator("#detail button.close").click()
        self.report["cancel_conflict"] = dict(first=first.get("code"), second=second.get("outcome"))

    def wait_conflict(self, page):
        # A wait owned by the coordinator; a handoff moves it to the successor
        # between the dialog and the confirmation.
        old = self.current_root()
        request = self.publish(old, "Report back.", self.to("worker_a"), summary="Coordinator asks worker_a to report back")
        wait = self.wait(old, "request_response", request["event_id"])
        snapshot = self.wait_for_snapshot(page, lambda s: any(w["wait_id"] == wait["wait_uuid"] for w in s["flow"]["waits"]),
                                          "the coordinator's wait")
        page.locator(f"#agents .agent[data-member='{self.principal(old)}'] button", has_text="Details").click()
        item = page.locator("#detail li", has_text="Coordinator asks worker_a to report back")
        item.locator("button", has_text="Cancel the wait").first.click()
        self.expect(page.locator("#confirm-body")).to_contain_text("stops waiting for")
        self.no_null(page.locator("#confirm-body"), "the cancel-wait dialog")
        new = "root_" + str(int(old.split("_")[-1]) + 1) if old.split("_")[-1].isdigit() else "root_3"
        self.handoff_from(old, new)
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/cancel-wait")) as info:
            page.locator("#confirm-go").click()
        first = info.value.json()
        require(info.value.status == 409 and first.get("code") == "VERSION_CONFLICT" and first.get("wait"),
                "a wait whose owner changed was not refused with the current wait: " + json.dumps(first)[:300])
        self.expect(page.locator("#confirm-body")).to_contain_text("The wait changed", timeout=5000)
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/cancel-wait")) as info:
            page.locator("#confirm-go").click()
        second = info.value.json()
        require(info.value.ok and second.get("outcome") == "cancelled", "the second wait confirmation: " + json.dumps(second)[:300])
        require((info.value.request.post_data_json or {}).get("expected_owner") == first["wait"]["owner"],
                "the second confirmation did not name the current owner")
        page.locator("#confirm-keep").click()
        page.locator("#detail button.close").click()

    def member_left(self, page):
        self.team("set-member", "--campaign", CAMPAIGN, "--member", self.principal("worker_c"), "--inactive")
        self.wait_for_snapshot(page, lambda s: any(m["member"] == self.principal("worker_c") and not m["active"] for m in s["members"]),
                               "worker_c inactive")
        # The page learns it from its next live snapshot.
        page.wait_for_function("(p) => window.cairnSwarmView.canWrite(p) === false", arg=self.principal("worker_c"), timeout=15000)
        page.locator("#agents details.inactive-members > summary").click()
        page.locator(f"#agents .agent[data-member='{self.principal('worker_c')}'] button", has_text="Details").click()
        self.expect(page.locator("#detail")).to_contain_text("Not a current principal")
        require(page.locator("#detail form.compose").count() == 0, "a send form is offered to a member that left")
        page.locator("#detail button.close").click()
        status = page.evaluate("""async (to) => {
            const session = await (await fetch('/api/auth/session', {cache: 'no-store'})).json();
            const r = await fetch('/api/swarm/action/send', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token},
              body: JSON.stringify({request_id: crypto.randomUUID(), campaign: 'swarm-fixture', to, kind: 'notice', action: 'none', summary: 'to a member that left', body: 'x'})});
            return [r.status, await r.json()];
        }""", self.principal("worker_c"))
        require(status[0] >= 400, "the store accepted a send to a member that left: " + json.dumps(status)[:300])
        self.report["send_to_left_member"] = status[1].get("code")

    def system_delivery(self):
        """An open delivery of a Cairn-published event to a member, from the snapshot."""
        members = {m["member"] for m in self.snapshot()["members"] if m["active"]}
        for delivery in self.snapshot()["flow"]["deliveries"]:
            if delivery.get("system") and delivery["consumer"] in members and delivery["display"] in ("pending", "leased", "held"):
                return delivery
        return None

    def system_cancel_preview(self, page):
        delivery = self.system_delivery()
        require(delivery, "no open Cairn-published delivery to a member to preview")
        page.locator(f"#agents .agent[data-member='{delivery['consumer']}'] button", has_text="Details").click()
        item = page.locator("#detail li", has=page.locator(f"[data-delivery='{delivery['delivery_id']}']"))
        item.locator("button", has_text="Cancel this recipient's delivery").click()
        body = page.locator("#confirm-body")
        self.expect(body).to_contain_text("Recipient", timeout=15000)
        text = body.inner_text()
        require("from Cairn (" in text and "from you (operator)" not in text and "sent, not yet seen" not in text,
                "the cancel preview labels a Cairn delivery as the owner's: " + text[:300])
        require("null" not in [line.strip() for line in text.splitlines()], "the cancel preview prints a null line")
        page.locator("#confirm-keep").click()
        page.locator("#detail button.close").click()

    def element_shot(self, locator, name):
        # A dialog scrolls on its own; capture the dialog, not the page behind it.
        locator.evaluate("(d) => { d.style.maxHeight = 'none'; }")
        locator.screenshot(path=str(self.output / (name + ".png")))
        locator.evaluate("(d) => { d.style.maxHeight = ''; }")
        self.report["screenshots"].append(name + ".png")

    def view_shots(self, page, suffix):
        """Screenshots of the views the owner reads: Needs you, a member's Details with a Cairn
        notice, the cancel preview of that notice, and the landing page."""
        page.locator("#needs-region").screenshot(path=str(self.output / f"needs-you-{suffix}.png"))
        self.report["screenshots"].append(f"needs-you-{suffix}.png")
        delivery = self.system_delivery()
        if delivery:
            page.locator(f"#agents .agent[data-member='{delivery['consumer']}'] button", has_text="Details").click()
            self.expect(page.locator("#detail")).to_contain_text("Cairn (")
            self.element_shot(page.locator("#detail"), f"details-system-{suffix}")
            item = page.locator("#detail li", has=page.locator(f"[data-delivery='{delivery['delivery_id']}']"))
            item.locator("button", has_text="Cancel this recipient's delivery").click()
            self.expect(page.locator("#confirm-body")).to_contain_text("Recipient", timeout=15000)
            self.element_shot(page.locator("#confirm"), f"cancel-preview-{suffix}")
            page.locator("#confirm-keep").click()
            page.locator("#detail button.close").click()
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            self.expect(landing.locator("#campaigns")).to_contain_text(CAMPAIGN, timeout=15000)
            self.shot(landing, f"landing-{suffix}")
        finally:
            landing.close()

    def scheduled_to_operator(self):
        """A request the scheduler publishes to a topic the operator principal
        subscribes to. The subscription is made through the real API by a
        fixture identity whose principal is the operator principal (the CLI has
        no operator subscribe); the schedule and its firing use the real
        operator commands."""
        topic = "swarm-fixture.scheduled"
        self.cli(["subscribe", "--token-file", str(self.host.token("owner_inbox")), "--socket", str(self.host.socket),
                  "--repo", self.collection, "--request-id", uid(), "--topic", topic])
        note = self.agent("bystander", "create", {"request_id": uid(), "draft": {
            "kind": "note", "body": "Scheduled reminder for the owner.", "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.collection, "task_id": "coordination/" + uid(), "run_id": "*"}}})
        scheduled = self.host.operator("event-schedule", {
            "request_id": uid(), "not_before": rfc3339(timedelta(seconds=2)), "grace_seconds": 600,
            "publication": {"repo": self.collection, "kind": "request", "ref": ref(note),
                            "destination": {"type": "topic", "name": topic}}})
        time.sleep(3)
        fired = self.host.operator("schedule-tick", {"repo": self.collection})
        events = [o.get("event_id") for o in fired.get("occurrences", []) if o.get("event_id")]
        require(events, "the schedule did not fire: " + json.dumps(fired)[:300])
        self.events["scheduled"] = {"event_id": events[0], "occurrence_id": scheduled.get("occurrence_id")}
        return events[0]

    def scheduled_board_question(self):
        """A request Cairn's scheduler publishes to the agent-board topic."""
        note = self.agent("bystander", "create", {"request_id": uid(), "draft": {
            "kind": "note", "body": "Should the fixture keep its scheduled reminder?", "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.collection, "task_id": "coordination/" + uid(), "run_id": "*"}}})
        self.host.operator("event-schedule", {
            "request_id": uid(), "not_before": rfc3339(timedelta(seconds=2)), "grace_seconds": 600,
            "publication": {"repo": self.collection, "kind": "request", "ref": ref(note),
                            "destination": {"type": "topic", "name": BOARD_TOPIC}}})
        time.sleep(3)
        fired = self.host.operator("schedule-tick", {"repo": self.collection})
        events = [o.get("event_id") for o in fired.get("occurrences", []) if o.get("event_id")]
        require(events, "the board schedule did not fire: " + json.dumps(fired)[:300])
        return events[0]

    def needs_you_system(self, page):
        event_id = self.scheduled_to_operator()
        board_id = self.scheduled_board_question()
        operator = self.report["operator_principal"]

        def check(view, where):
            row = view.locator(f"li.need[data-event='{event_id}']")
            self.expect(row).to_have_count(1, timeout=20000)
            text = row.inner_text()
            require("Cairn (scheduled message)" in text, f"{where}: the scheduled request's row does not read as from Cairn: " + text[:200])
            require("you (operator)" not in text, f"{where}: the scheduled request's row reads as the owner's: " + text[:200])
            return row

        def board(view, where):
            # The board question's row reads as from Cairn wherever it is listed.
            row = view.locator(f"li.need[data-event='{board_id}']")
            if row.count() == 0:
                return False
            sender = row.locator("p.meta").inner_text()
            require("Cairn (scheduled message)" in sender, f"{where}: the scheduled board question's sender is not Cairn: " + sender[:200])
            require("you (operator)" not in sender and "local-uid:" not in sender, f"{where}: the scheduled board question's sender reads as the owner's")
            return True

        page.wait_for_timeout(2500)
        board_places = []
        if board(page, "campaign page"):
            board_places.append("campaign")
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            check(landing, "landing page")
            if board(landing, "landing page"):
                board_places.append("landing")
            # The landing page re-renders every 5 s; a page capture does not wait for it to hold still.
            self.shot(landing, "needs-you-landing-system-desktop")
        finally:
            landing.close()
        phone = page.context.browser.new_context(viewport=PHONE)
        try:
            small = phone.new_page()
            small.goto(f"http://127.0.0.1:{self.port}/#token=" + self.token)
            check(small, "landing page at 390 px")
            if board(small, "landing page at 390 px"):
                board_places.append("landing-390")
            small.goto(self.campaign_url())
            self.expect(small.locator("#agents .agent").first).to_be_visible(timeout=20000)
            small.wait_for_timeout(1500)
            if board(small, "campaign page at 390 px"):
                board_places.append("campaign-390")
            self.shot(small, "needs-you-landing-system-phone")
        finally:
            phone.close()
        self.report["scheduled_board_places"] = board_places
        rows = [i for i in self.page_json("/api/swarm/landing")["needs_you"]["items"] if (i.get("refs") or {}).get("event_id") == board_id]
        self.report["scheduled_board_row_system"] = rows[0].get("system") if rows else "row not on landing"
        require(board_places, "the scheduled board question is listed on neither page")
        # No raw operator principal anywhere on the campaign page or the landing page.
        def raw_principal(view, where):
            # Name the element that shows it, so a store-written title is told apart from page text.
            hits = view.evaluate("""(needle) => [...document.querySelectorAll('body *')]
                .filter((n) => n.children.length === 0 && (n.textContent || '').includes(needle))
                .map((n) => (n.closest('li') ? n.closest('li').dataset.type || n.closest('li').className : n.tagName) + ': ' + n.textContent.trim().slice(0, 80))""", operator)
            require(not hits, f"{where} shows the raw operator principal: " + json.dumps(hits[:3]))
        raw_principal(page, "the campaign page")
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            self.expect(landing.locator("#campaigns")).to_contain_text(CAMPAIGN, timeout=15000)
            raw_principal(landing, "the landing page")
        finally:
            landing.close()
        # The campaign page: no row with a Cairn sender reads as the owner's.
        for item in self.snapshot()["needs_you"]["items"]:
            if item.get("system"):
                text = page.locator(f"#needs li.need[data-need='{item['id']}']").inner_text()
                require("you (operator)" not in text and "Cairn (" in text, "a campaign need row with a Cairn sender reads as the owner's: " + text[:200])

    def system_everywhere(self, page):
        """Every Cairn-published event, in every place the page shows a sender."""
        snapshot = self.snapshot()
        expected = {}
        for delivery in snapshot["flow"]["deliveries"]:
            if delivery.get("system"):
                expected.setdefault(delivery["event_id"], []).append(delivery)
        kinds = {d[0]["system"] for d in expected.values()}
        self.report["system_expected"] = sorted(kinds)
        require({"wait_notice", "overdue_checkpoint"} <= kinds, "the store marks fewer system events than the fixture produced: " + json.dumps(sorted(kinds)))
        logged = set(page.evaluate("() => window.cairnSwarmView.logIDs()"))
        seen = []

        def cairn_only(text, where):
            require("Cairn (" in text, f"{where} does not label a Cairn event as from Cairn: " + text[:200])
            require("you (operator) →" not in text and "from you (operator)" not in text, f"{where} labels a Cairn event as the owner's: " + text[:200])
            require("sent, not yet seen" not in text, f"{where} gives a Cairn event the owner's unseen mark: " + text[:200])

        for event_id, deliveries in expected.items():
            if event_id in logged:
                row = page.locator(f"#flow li.event[data-event='{event_id}']")
                cairn_only(row.inner_text(), "the flow")
                require("owner-sent" not in (row.get_attribute("class") or ""), "a Cairn event has owner styling in the flow")
                require(row.locator("button", has_text="Supersede").count() == 0, "a Cairn event offers Supersede")
                row.locator("button", has_text="Open").click()
                self.expect(page.locator("#event-body")).to_contain_text("From", timeout=10000)
                cairn_only(page.locator("#event-body dl").first.inner_text(), "the event panel")
                page.locator("#event-detail button.close").click()
                seen.append("flow+event")
            for consumer in sorted({d["consumer"] for d in deliveries}):
                card = page.locator(f"#agents .agent[data-member='{consumer}']")
                if card.count() == 0:
                    continue
                card.locator("button", has_text="Details").click()
                try:
                    detail = page.locator("#detail")
                    rows = detail.locator(f"li[data-event='{event_id}']")
                    require(rows.count() >= 1, "the recipient's Details do not list the Cairn event")
                    for text in rows.all_inner_texts():
                        cairn_only(text, "the recipient's Details")
                    seen.append("details")
                finally:
                    page.locator("#detail button.close").click()
        # Cairn's notices outside any campaign appear on the landing page.
        landing = page.context.new_page()
        try:
            landing.goto(f"http://127.0.0.1:{self.port}/")
            self.expect(landing.locator("#campaigns")).to_contain_text(CAMPAIGN, timeout=15000)
            outside = [e for e in self.page_json("/api/swarm/landing")["events"] if e.get("system")]
            require(outside, "no Cairn-published event outside a campaign is on the landing page")
            for event in outside:
                cairn_only(landing.locator(f"#landing-events li[data-event='{event['event_id']}']").inner_text(), "the landing page")
            seen.append("landing")
        finally:
            landing.close()
        self.system_cancel_preview(page)
        seen.append("cancel-preview")
        self.report["system_places"] = sorted(set(seen))
        # The owner's own send still reads as the owner's.
        owner = page.locator("#flow li.event.owner-sent").first
        self.expect(owner).to_contain_text("you (operator)")
        require(owner.locator(".tag.system").count() == 0, "the owner's own send is labelled as from Cairn")

    def system_outside_window(self, page):
        # Push the scheduler's notices out of the page's window: publish more
        # campaign events than one window holds, then reload so the page reads a
        # new window. Their deliveries to the lead stay open, so the snapshot still
        # lists them, but no event of theirs is in the log any more.
        system_ids = page.evaluate("() => [...document.querySelectorAll('#flow li.event')].filter((n) => n.querySelector('.tag.system')).map((n) => n.dataset.event)")
        require(system_ids, "no Cairn-published event is in the window to push out")
        for n in range(320):
            self.publish("worker_a", f"Filler {n}.", {"type": "topic", "name": "swarm-fixture.filler"}, kind="notice",
                         action="none", summary=f"Filler event {n}")
        page.reload()
        self.expect(page.locator("#live")).to_have_text("live", timeout=20000)
        self.expect(page.locator("#flow li.event", has_text="Filler event 319")).to_have_count(1, timeout=20000)
        ids = set(page.evaluate("() => window.cairnSwarmView.logIDs()"))
        snapshot = self.snapshot()
        lead = self.principal("lead")
        require(not (set(system_ids) & ids), "the Cairn-published events are still in the window")
        outside = [d for d in snapshot["flow"]["deliveries"]
                   if d["event_id"] in system_ids and d["display"] in ("pending", "leased", "held")]
        self.report["outside_window_candidates"] = [dict(consumer=d["consumer"][:14], display=d["display"]) for d in snapshot["flow"]["deliveries"] if d["event_id"] in system_ids]
        require(outside, "no open delivery of a Cairn-published event outside the window is in the snapshot")
        lead = outside[0]["consumer"]
        self.report["outside_window_system"] = [dict(summary=d.get("summary"), system=d.get("system")) for d in outside]
        page.locator(f"#agents .agent[data-member='{lead}'] button", has_text="Details").click()
        try:
            for delivery in [d for d in outside if d["consumer"] == lead]:
                row = page.locator("#detail li", has=page.locator(f"[data-delivery='{delivery['delivery_id']}']"))
                self.expect(row).to_have_count(1, timeout=10000)
                text = row.inner_text()
                if not delivery.get("system"):
                    raise AssertionError("waiting: the snapshot's delivery carries no `system` value (flow author's field), "
                                         "so a delivery outside the window cannot be told apart from the owner's: " + text[:160])
                require("Cairn (" in text and "from you (operator)" not in text, "an open Cairn delivery outside the window reads as the owner's: " + text[:200])
                require("sent, not yet seen" not in text, "an open Cairn delivery outside the window has the owner's unseen mark")
        finally:
            page.locator("#detail button.close").click()

    def signed_out_routes(self, page):
        landing = page.context.new_page()
        landing.goto(f"http://127.0.0.1:{self.port}/")
        self.expect(landing.locator("#campaigns")).to_contain_text(CAMPAIGN, timeout=15000)
        page.locator("#flow li.event button", has_text="Open").first.click()
        self.expect(page.locator("#event-detail")).to_be_visible()
        other = page.context.new_page()
        try:
            other.goto(self.campaign_url(token=False))
            self.expect(other.locator("#signout")).to_be_visible(timeout=15000)
            other.click("#signout")
        finally:
            other.close()
        self.expect(page.locator("#signin")).to_be_visible(timeout=20000)
        require(not page.evaluate("() => document.getElementById('event-detail').open"), "the event detail stayed open over the sign-in")
        self.expect(landing.locator("#signin")).to_be_visible(timeout=15000)
        landing.close()
        codes = page.evaluate("""async (campaign) => {
            const event = await fetch('/api/swarm/event?campaign=' + campaign + '&event_id=' + crypto.randomUUID(), {cache: 'no-store'});
            const action = await fetch('/api/swarm/action/acknowledge', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': 'x'},
              body: JSON.stringify({request_id: crypto.randomUUID(), campaign, delivery_id: crypto.randomUUID()})});
            return [[event.status, (await event.json()).code], [action.status, (await action.json()).code]];
        }""", CAMPAIGN)
        require(all(c == [401, "UNAUTHENTICATED"] for c in codes), "a route of an ended session did not answer 401: " + json.dumps(codes))
        page.fill("#token-input", self.token)
        page.click("#signin button[type=submit]")
        self.expect(page.locator("#live")).to_have_text("live", timeout=15000)

    def refused_cancel(self, page):
        # worker_a takes a live manual lease on its oldest delivery through the CLI.
        leased = self.next("worker_a")
        require(leased, "worker_a had nothing to claim")
        try:
            page.wait_for_timeout(3000)
            self.open_member_delivery(page, "worker_a", leased["delivery_id"])
            body = page.locator("#confirm-body")
            self.expect(body).to_contain_text("would refuse this cancel", timeout=15000)
            self.expect(body).to_contain_text("UNSUPPORTED_CONTROL")
            require(body.inner_text().count("UNSUPPORTED_CONTROL") == 1, "the refusal prints its code twice")
            require(page.locator("#confirm-go").is_disabled(), "confirm is enabled for a cancel the store refuses")
        finally:
            self.complete("worker_a", leased)

    def send_page(self, page, role, summary, key=""):
        page.locator(f"#agents .agent[data-member='{self.principal(role)}'] button", has_text="Details").click()
        form = page.locator("#detail form.compose")
        form.locator("select[name=kind]").select_option("notice")
        form.locator("select[name=action]").select_option("review")
        if key:
            form.locator("input[name=supersedes]").fill(key)
        form.locator("input[name=summary]").fill(summary)
        form.locator("textarea[name=body]").fill("A note from the owner.")
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/send")) as info:
            form.locator("button[type=submit]").click()
        reply = info.value.json()
        require(info.value.ok, "send: " + json.dumps(reply)[:300])
        page.locator("#detail button.close").click()
        return reply

    def supersede_open_only(self, page):
        key = "owner-" + uid()[:8]
        first = self.send_page(page, "worker_a", "Owner v1 " + key, key)
        second = self.send_page(page, "worker_a", "Owner v2 " + key, key)
        v1 = page.locator(f"#flow li.event[data-event='{first['event']['event_id']}']")
        v2 = page.locator(f"#flow li.event[data-event='{second['event']['event_id']}']")
        self.expect(v1.locator(".chip-superseded")).to_have_count(1, timeout=15000)
        self.expect(v1.locator("button", has_text="Supersede")).to_have_count(0)
        self.expect(v2.locator("button", has_text="Supersede")).to_have_count(1)

    def send_unseen(self, page):
        marker = "owner-" + uid()[:8]
        page.locator(f"#agents .agent[data-member='{self.principal('worker_a')}'] button", has_text="Details").click()
        form = page.locator("#detail form.compose")
        form.locator("select[name=kind]").select_option("notice")
        form.locator("select[name=action]").select_option("review")
        form.locator("input[name=summary]").fill("Owner note " + marker)
        form.locator("textarea[name=body]").fill("A note from the owner.")
        with page.expect_response(lambda r: r.url.endswith("/api/swarm/action/send")) as info:
            form.locator("button[type=submit]").click()
        reply = info.value.json()
        require(info.value.ok and reply.get("event", {}).get("event_id"), "send: " + json.dumps(reply)[:300])
        page.locator("#detail button.close").click()
        row = page.locator("#flow li.event.owner-sent", has_text=marker)
        self.expect(row).to_have_count(1, timeout=15000)
        self.expect(row).to_contain_text("sent, not yet seen", timeout=15000)
        # The recipient reads the event through the CLI: it is the only consumer, still unclaimed.
        status = self.agent("worker_a", "event-inspect", {"event_id": reply["event"]["event_id"]})
        require([d["consumer"] for d in status["deliveries"]] == [self.principal("worker_a")], "the owner's notice reached another inbox")
        require(all(d["state"] == "pending" for d in status["deliveries"]), "the owner's notice is not pending in the store")

    def label(self, snapshot, role):
        for member in snapshot["members"]:
            if member["member"] == self.principal(role):
                return member.get("label") or role
        return role

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
            self.view_shots(page, "phone")
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
        operator = "local-uid:" + str(os.geteuid())
        principals = lambda role: operator if role == OWNER_INBOX else f"agent/swarm-ui-{role}-{uid()}"
        self.host = DisposableHost.start(self.cluster, binary=self.binary, repo=self.collection, roles=(*ROLES, OWNER_INBOX),
                                         principals=principals, database="swarm_ui", home=self.runtime / "h",
                                         label="swarm_ui", env=self.env)
        require(self.host.env["CAIRN_HOME"].startswith(str(self.runtime)), "fixture state directory escaped the runtime")
        self.report["collection"] = self.collection
        self.seed()
        self.seed_context()
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
        if getattr(self, "sleeper", None) is not None:
            stop_process(self.sleeper, timeout=5, label="stand-in harness process")
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
