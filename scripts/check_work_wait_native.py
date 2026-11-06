#!/usr/bin/env python3
"""WC03 acceptance against a fresh Store and the actual Codex loopback harness.

The local Responses server scripts source-read and settlement tool calls. This
proves native delivery and durable coordination boundaries, not model judgment
or the outcome of the original request. No production profile or database is
used. Supply the exact candidate executable; this script never finds/builds one.
"""

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import time
import uuid

import disposable_host


class EnvironmentUnavailable(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def new_id():
    return str(uuid.uuid4())


def ref(value):
    return {key: value[key] for key in ("record_id", "version")}


def session(value):
    return {key: value[key] for key in ("agent_id", "execution_id")}


def deadline():
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()


class LostReply:
    """Forward one actual API commit, consume its OK reply, then close the client.

    The proxy neither records nor prints headers. Its only retained response is
    the nonsecret wait result, used to check retry identities after Store reopen.
    """

    def __init__(self, host):
        self.path = host.home / "lost-reply.sock"
        self.result = None
        self.error = None
        self.done = threading.Event()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                connection = http.client.HTTPConnection("cairn", timeout=10)
                try:
                    require(self.path == "/v1/wait-create", "unexpected loss-boundary operation")
                    length = int(self.headers.get("Content-Length", "-1"))
                    require(0 < length < 65536, "unbounded loss-boundary request")
                    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.sock.settimeout(10)
                    connection.sock.connect(str(host.socket))
                    connection.request("POST", self.path, self.rfile.read(length),
                                       dict(self.headers.items()))
                    reply = connection.getresponse()
                    body = reply.read(65537)
                    require(len(body) <= 65536, "unbounded loss-boundary response")
                    envelope = json.loads(body)
                    require(reply.status == 200 and envelope["status"] == "OK"
                            and envelope["ok"] is True,
                            "loss boundary did not observe a committed OK response")
                    proxy.result = envelope["data"]
                except Exception as error:
                    proxy.error = error
                finally:
                    connection.close()
                    self.close_connection = True
                    proxy.done.set()

        self.server = socketserver.UnixStreamServer(str(self.path), Handler)
        self.server.timeout = 15
        os.chmod(self.path, 0o600)
        self.thread = threading.Thread(target=self.server.handle_request, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.thread.join(16)
        self.server.server_close()
        self.path.unlink(missing_ok=True)
        require(not self.thread.is_alive(), "loss-boundary proxy did not terminate")
        if self.error is not None:
            raise self.error
        require(self.done.is_set() and self.result is not None,
                "lost reply was not observed after a real commit")


class WaitFixture:
    def __init__(self, host):
        self.host = host
        self.sessions = {}
        self.registrations = {}
        self.created_waits = []
        self.native_target = None

    def register(self, role):
        body = self.registrations.setdefault(role, {
            "binding": "work-wait-" + role,
            "native_session_id": "work-wait-" + new_id(),
            "metadata": {"harness": "codex", "project": "wait-fixture",
                         "workspace": str(self.host.home), "state": "idle",
                         "delivery_mode": "existing-session"}})
        old = self.sessions.get(role)
        current = self.host.agent(role, "agent-register", dict(body, request_id=new_id()))
        require(current["inbox"] == "agent/" + current["agent_id"],
                "registration did not allocate its own inbox")
        if old:
            require(current["agent_id"] == old["agent_id"]
                    and current["inbox"] == old["inbox"]
                    and current["execution_id"] != old["execution_id"],
                    "resume did not retain address and replace execution")
        self.sessions[role] = current
        return current

    def agent(self, role, operation, body, **kwargs):
        return self.host.agent(role, operation, body, session=self.sessions[role], **kwargs)

    def source(self, role, body):
        return self.agent(role, "create", {"request_id": new_id(), "draft": {
            "kind": "note", "body": body, "claim_type": "self", "sensitivity": "shareable",
            "scope": {"repo": self.host.repo, "task_id": "*", "run_id": "*"}}})

    def request(self, predicate):
        source = self.source("bob", "Return the selected fixture result; task success is unassessed.")
        body = {"request_id": new_id(), "kind": "request", "ref": ref(source),
                "destination": {"type": "agent", "name": self.sessions["alice"]["inbox"]},
                "correlation_id": new_id()}
        if predicate == "response_group_closed":
            body["response_group"] = {"deadline": deadline(), "partial_policy": "all"}
        return self.agent("bob", "event-publish", body)

    def create_wait(self, event, predicate, *, lost_reply=False):
        payload = {"request_id": new_id(), "collection": self.host.repo,
                   "session": session(self.sessions["bob"]), "predicate_kind": predicate,
                   "dependency_event_uuid": event["event_id"], "deadline": deadline()}
        argv = ["wait", "create", *self.host.connection("bob", self.sessions["bob"]),
                "--repo", self.host.repo, "--request-id", payload["request_id"],
                "--predicate", predicate, "--dependency", event["event_id"],
                "--deadline", payload["deadline"]]
        if lost_reply:
            with LostReply(self.host) as loss:
                lost_argv = argv.copy()
                lost_argv[lost_argv.index("--socket") + 1] = str(loss.path)
                self.host.command(lost_argv, expected="STORE_ERROR")
            committed = loss.result
            require(committed["state"] == "waiting", "lost create did not commit waiting state")
            require(self.host.stop_server() == 0, "API did not stop cleanly at reopen boundary")
            self.host.start_server()
            result = self.host.command(argv)
            require(result == committed, "create retry after reopen changed the committed wait")
        else:
            result = self.host.command(argv)
        require(result["state"] == "waiting"
                and result["dependency_event_uuid"] == event["event_id"]
                and result["owner_agent_uuid"] == self.sessions["bob"]["agent_id"]
                and result["creation_execution_uuid"] == self.sessions["bob"]["execution_id"],
                "wait does not bind its real dependency and registered owner")
        for key in ("wait_uuid", "reserved_notice_event_uuid", "reserved_source_record_uuid"):
            uuid.UUID(result[key])
        self.created_waits.append(result)
        return result

    def get_wait(self, wait):
        return self.host.command(["wait", "get", *self.host.connection("bob", self.sessions["bob"]),
                                  "--repo", self.host.repo, "--wait", wait["wait_uuid"]])

    def settle_next(self, role, event):
        delivery = self.agent(role, "event-next", {})["delivery"]
        require(delivery is not None and delivery["event"]["event_id"] == event["event_id"],
                "poll did not return the exact expected event")
        self.agent(role, "event-complete", {"request_id": new_id(),
            "delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"],
            "disposition": "handled"})

    def respond(self, event, wait, predicate):
        self.settle_next("alice", event)
        require(self.get_wait(wait)["state"] == "waiting",
                "handling the original request incorrectly satisfied its wait")
        result = self.source("alice", "Selected fixture response. Original task outcome remains unassessed.")
        response = self.agent("alice", "event-publish", {
            "request_id": new_id(), "kind": "response", "ref": ref(result),
            "destination": {"type": "agent", "name": self.sessions["bob"]["inbox"]},
            "causation_id": event["event_id"], "correlation_id": event["correlation_id"]})
        current = self.get_wait(wait)
        reason = "response" if predicate == "request_response" else "group_closed"
        require(current["state"] == "satisfied" and current["terminal_reason"] == reason
                and current["outbox_state"] == "pending", "response did not atomically resolve wait/outbox")
        if predicate == "request_response":
            require(current["response_ref"] == ref(result), "wait selected a different response version")
        else:
            group = self.agent("bob", "event-group", {"event_id": event["event_id"]})
            require(group["state"] == "collected" and group["responded"] == group["expected"] == 1
                    and group["reported_task_outcome"] == "unknown", "group closure became a task outcome")
        self.settle_next("bob", response)
        require(self.agent("bob", "event-next", {})["delivery"] is None,
                "unrelated response deliveries remain before outbox publication")
        return response

    def unchanged_outcome(self, event):
        status = self.agent("bob", "event-inspect", {"event_id": event["event_id"]})
        require(len(status["deliveries"]) == 1, "original request acquired an extra delivery")
        delivery = status["deliveries"][0]
        review = self.host.operator("coordination-review", {
            "repo": self.host.repo, "delivery_id": delivery["delivery_id"], "limit": 1})
        rows = review["deliveries"]["deliveries"]
        require(delivery["state"] == "handled" and delivery.get("result") is None
                and len(rows) == 1 and rows[0]["delivery_id"] == delivery["delivery_id"]
                and rows[0]["reported_task_outcome"] == "unknown"
                and not rows[0].get("assessment"),
                "wait notification rewrote the original task outcome")

    def published(self, wait):
        current = self.get_wait(wait)
        require(current["outbox_state"] == "published", "scheduler did not publish the wait outbox")
        event_id = str(uuid.UUID(wait["reserved_notice_event_uuid"]))
        source_id = str(uuid.UUID(wait["reserved_source_record_uuid"]))
        wait_id = str(uuid.UUID(wait["wait_uuid"]))
        status = self.agent("bob", "event-inspect", {"event_id": event_id})
        event = status["event"]
        require(event["event_id"] == event_id and event["kind"] == "notice"
                and event["ref"] == {"record_id": source_id, "version": 1}
                and event["destination"] == {"type": "agent", "name": self.sessions["bob"]["inbox"]}
                and len(status["deliveries"]) == 1, "scheduler changed reserved source/event/destination")
        history = self.agent("bob", "history", event["ref"])
        source = history["versions"]
        require(history["record_id"] == source_id and len(source) == 1
                and source[0]["version"] == 1 and source[0]["payload_available"]
                and wait_id in source[0]["body"], "exact reserved source version was not readable")
        counts = self.host.sql(f"""SELECT
            (SELECT count(*) FROM cairn.agent_wait WHERE wait_uuid='{wait_id}'),
            (SELECT count(*) FROM cairn.agent_wait_outbox WHERE wait_uuid='{wait_id}'
              AND notice_event_uuid='{event_id}' AND source_record_uuid='{source_id}'),
            (SELECT count(*) FROM cairn.memory_record WHERE record_id='{source_id}'),
            (SELECT count(*) FROM cairn.record_version WHERE record_id='{source_id}'),
            (SELECT count(*) FROM cairn.agent_event WHERE event_id='{event_id}'
              AND record_id='{source_id}' AND version=1),
            (SELECT count(*) FROM cairn.agent_delivery WHERE event_id='{event_id}')""")
        require(counts == "1|1|1|1|1|1", "reserved wait/source/version/event/delivery counts differ")
        delivery = status["deliveries"][0]
        return {"source": dict(source[0], record_id=source_id), "event": event,
                "delivery": {key: delivery[key] for key in ("delivery_id", "state", "attempts")}}

    def counts(self):
        return {table: self.host.count(table) for table in (
            "agent_wait", "agent_wait_outbox", "memory_record", "record_version",
            "agent_event", "agent_delivery")}

    def empty_all_paths(self):
        require(self.agent("bob", "event-next", {})["delivery"] is None,
                "generic polling admitted the fenced wait notice")
        current = session(self.sessions["bob"])
        ready = self.host.agent("bob", "session-inbox-ready", current)
        require(not ready.get("delivery_id") and not ready.get("event_id"),
                "native readiness admitted the fenced wait notice")
        pending = self.host.agent("bob", "session-inbox-pending", current)
        require(pending["requests"] == pending["responses"] == pending["notices"] == 0
                and not pending.get("truncated"), "native pending included a fenced or unrelated event")
        claim = self.host.agent("bob", "session-inbox-claim", {
            "request_id": new_id(), "session": current})
        require(claim["attempt"] is None, "native claim admitted the fenced wait notice")

    def finish_native_control(self, publication):
        current = session(self.sessions["bob"])
        ready = self.host.agent("bob", "session-inbox-ready", current)
        require(ready.get("event_id") == publication["event"]["event_id"]
                and ready.get("delivery_id") == publication["delivery"]["delivery_id"],
                "postrestore native readiness did not admit the fresh exact notice")
        attempt = self.host.agent("bob", "session-inbox-claim", {
            "request_id": new_id(), "session": current})["attempt"]
        require(attempt is not None and attempt["delivery"]["event"] == publication["event"],
                "postrestore native claim did not admit the fresh exact notice")
        history = self.agent("bob", "history", publication["event"]["ref"])
        require(len(history["versions"]) == 1
                and history["versions"][0]["body"] == publication["source"]["body"],
                "postrestore native attempt could not read the exact source")
        delivery = attempt["delivery"]
        self.agent("bob", "event-complete", {"request_id": new_id(),
            "delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"],
            "disposition": "handled"})
        reconciled = self.host.agent("bob", "session-inbox-reconcile", {
            "request_id": new_id(), "session": current, "attempt_id": attempt["attempt_id"],
            "reason": "delivery_completed"})
        require(reconciled["finished_at"], "postrestore native attempt was not released")
        self.empty_all_paths()

    def store_boundaries(self, scheduler):
        self.register("alice")
        self.register("bob")
        for predicate in ("request_response", "response_group_closed"):
            event = self.request(predicate)
            wait = self.create_wait(event, predicate, lost_reply=predicate == "request_response")
            self.respond(event, wait, predicate)
            old = self.sessions["bob"]
            self.host.agent("bob", "agent-leave", session(old))
            before = self.counts()
            scheduler()
            self.register("bob")
            self.host.agent("bob", "event-next", {}, session=old, expected="STALE_SESSION")
            publication = self.published(wait)
            require(publication["delivery"]["state"] == "pending"
                    and publication["delivery"]["attempts"] == 0,
                    "offline wait notification was lost or claimed before resume")
            after = self.counts()
            require(after == {key: value + (key in ("memory_record", "record_version", "agent_event",
                                                     "agent_delivery")) for key, value in before.items()},
                    "outbox publication did not add exactly one source/version/event/delivery")
            require(self.host.stop_server() == 0, "API did not stop cleanly after publication")
            self.host.start_server()
            scheduler()
            require(self.counts() == after and self.published(wait) == publication,
                    "outbox retry after reopen duplicated or changed its publication")
            self.settle_next("bob", publication["event"])
            self.empty_all_paths()
            self.unchanged_outcome(event)

        # Consume the ordinary response before draining: the reserved notice must
        # remain published and unclaimed when restore fencing changes generation.
        event = self.request("request_response")
        old_wait = self.create_wait(event, "request_response")
        self.respond(event, old_wait, "request_response")
        scheduler()
        before_restore = self.published(old_wait)
        require(before_restore["delivery"]["state"] == "pending"
                and before_restore["delivery"]["attempts"] == 0,
                "restore target was already claimed")
        before = self.counts()
        self.host.operator("fence-restore", {"request_id": new_id(),
            "reason": "Disposable WC03 published-unclaimed admission-fence proof"})
        self.register("alice")
        self.register("bob")
        self.empty_all_paths()
        scheduler()
        require(self.counts() == before, "restore/scheduler changed exact publication counts")
        fenced = self.published(old_wait)
        require(fenced["delivery"]["state"] == "pending" and fenced["delivery"]["attempts"] == 0,
                "fenced notice was claimed through a tested delivery path")

        # A fresh notice for the same resumed owner must remain deliverable. This
        # rules out an inactive recipient or an indiscriminately blocked inbox.
        event = self.request("request_response")
        fresh = self.create_wait(event, "request_response")
        require(fresh["generation"] > old_wait["generation"], "postrestore wait reused old generation")
        self.respond(event, fresh, "request_response")
        scheduler()
        self.finish_native_control(self.published(fresh))
        self.unchanged_outcome(event)
        require(self.published(old_wait)["delivery"]["attempts"] == 0,
                "positive control released the old admission fence")

    def native_delivery(self, runner, args, scheduler, predicate):
        original = {}

        def publish_wait(target):
            self.native_target = target
            self.sessions["bob"] = target
            self.host.agent("alice", "agent-heartbeat", session(self.sessions["alice"]))
            event = self.request(predicate)
            wait = self.create_wait(event, predicate)
            self.respond(event, wait, predicate)
            scheduler()
            publication = self.published(wait)
            require(publication["delivery"]["state"] == "pending"
                    and publication["delivery"]["attempts"] == 0,
                    "native wait notice was not pending before the actual turn")
            original.update(event=event, wait=wait, publication=publication, counts=self.counts())
            return publication

        config = {"cairn": str(self.host.binary), "socket": str(self.host.socket),
                  "token_file": str(self.host.token("bob")), "repo": self.host.repo}
        with private_environment(self.host.env), contextlib.redirect_stdout(io.StringIO()):
            receipt = runner.check(args.codex_binary, self.host.home / ("native-" + predicate),
                config, self.api_call, candidate_root=args.candidate_root,
                codex_entry=args.codex_entry, codex_root=args.codex_root,
                turn_timeout=args.turn_timeout, api_role="bob", publication_factory=publish_wait)
        require(receipt["result"] == "PASS" and receipt["provider_call"] is False
                and receipt["exact_full_source_read"] is True
                and receipt["explicit_handled_settlement"] is True
                and receipt["one_event_one_delivery_one_attempt"] is True
                and receipt["native_turn_joined"] is True,
                "actual native runner did not prove exact-source read and explicit settlement")
        publication = original["publication"]
        observed = receipt["response_refs"]
        require(observed["event_id"] == publication["event"]["event_id"]
                and observed["source"] == publication["event"]["ref"]
                and observed["delivery_id"] == publication["delivery"]["delivery_id"]
                and observed["attempt_id"] and observed["native_turn_id"],
                "native runner settled a different event/source/delivery")
        attempt_id = str(uuid.UUID(observed["attempt_id"]))
        end = time.monotonic() + 10
        while True:
            attempt = self.host.sql(f"SELECT count(*) FROM cairn.agent_session_attempt "
                f"WHERE attempt_id='{attempt_id}' AND finished_at IS NOT NULL")
            if attempt == "1":
                break
            require(time.monotonic() < end, "native Stop did not release the completed exact attempt")
            time.sleep(0.1)
        require(self.counts() == original["counts"], "native settlement duplicated publication or source")
        status = self.published(original["wait"])
        require(status["delivery"]["state"] == "handled" and status["delivery"]["attempts"] == 1,
                "actual native notice did not retain exactly one handled attempt")
        self.unchanged_outcome(original["event"])
        return {"predicate": predicate, "exact_full_source_read": True,
                "native_turn_pinned": True, "explicit_settlement": True,
                "attempt_released": True, "provider_called": False}

    def api_call(self, role, command, *args, body=None, expected="OK", raw=False):
        if raw:
            return self.host.agent(role, command, body, args=args, expected=expected)
        if command == "event-status":
            require(role == "bob" and self.native_target is not None,
                    "wait event inspection requires its actual native recipient")
            return self.host.agent(role, "event-inspect", {"event_id": args[0]},
                                   session=self.native_target, expected=expected)
        require(command == "agents" and args[0] == "list", "unexpected native harness operation")
        return self.host.command(["agents", "list", *self.host.connection(role), *args[1:]],
                                 expected=expected)


def load_native_runner(source_root, staged_root):
    # The native runner prepends its own directory on import, and the installer
    # locates lifecycle sources relative to that directory. Stage one coherent
    # tree so every relative import and installation uses the selected candidate.
    for directory in ("scripts", "integrations/lifecycle"):
        for path in (source_root / directory).rglob("*.py"):
            target = staged_root / path.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, target)
    fixture = staged_root / "trials/token-efficiency/FIXTURE.json"
    fixture.parent.mkdir(parents=True, mode=0o700)
    shutil.copyfile(source_root / "trials/token-efficiency/FIXTURE.json", fixture)
    scripts = staged_root / "scripts"
    path = scripts / "check_codex_full_inbox_native.py"
    shutil.copyfile(Path(__file__).with_name("check_codex_full_inbox_native.py"), path)
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("work_wait_native_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require("publication_factory" in inspect.signature(module.check).parameters,
            "missing native runtime fixture contract: publication_factory")
    require(Path(inspect.getsourcefile(module.load_coordination_installer)).parent == scripts,
            "native installer helper came from outside the staged candidate sources")
    return module


@contextlib.contextmanager
def private_environment(env):
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True,
                        help="exact already-built WC03 Cairn executable")
    parser.add_argument("--source-root", type=Path, required=True,
                        help="coherent WC03 source tree containing shared native helpers and lifecycle hooks")
    parser.add_argument("--codex-binary", type=Path, required=True,
                        help="selected native Codex installation identity (never executed directly)")
    parser.add_argument("--codex-entry", type=Path, required=True,
                        help="explicit Node Codex CLI script")
    parser.add_argument("--codex-root", type=Path, required=True,
                        help="explicit installed Codex package root")
    parser.add_argument("--turn-timeout", type=int, default=60)
    args = parser.parse_args(argv)
    for key in ("binary", "source_root", "codex_binary", "codex_entry", "codex_root"):
        setattr(args, key, getattr(args, key).resolve(strict=True))
    require(args.binary.is_file() and os.access(args.binary, os.X_OK), "candidate executable unavailable")
    require(0 < args.turn_timeout <= 120, "native turn timeout must be between 1 and 120 seconds")
    return args


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_digest(source_root):
    own_root = Path(__file__).resolve().parents[1]
    paths = {*source_root.joinpath("scripts").rglob("*.py"),
             *source_root.joinpath("integrations/lifecycle").rglob("*.py"),
             Path(__file__).resolve(),
             own_root / "scripts/check_codex_full_inbox_native.py",
             own_root / "scripts/disposable_host.py",
             source_root / "trials/token-efficiency/FIXTURE.json",
             source_root / "go.mod", source_root / "go.sum"}
    for directory in ("core", "cmd", "internal", "localapi"):
        for pattern in ("*.go", "*.sql"):
            paths.update(source_root.joinpath(directory).rglob(pattern))
    result = hashlib.sha256()
    for path in sorted(paths):
        result.update(str(path).encode() + b"\0" + bytes.fromhex(digest(path)))
    return result.hexdigest()


def check(args):
    root = Path(tempfile.mkdtemp(prefix="cairn-postgres.wc03-"))
    cluster = None
    host = None
    try:
        original_binary = args.binary
        binary_sha = digest(original_binary)
        args = argparse.Namespace(**vars(args))
        args.candidate_root = root / "candidate"
        args.candidate_root.mkdir(mode=0o700)
        args.binary = args.candidate_root / "cairn"
        shutil.copyfile(original_binary, args.binary)
        args.binary.chmod(0o500)
        require(digest(args.binary) == binary_sha == digest(original_binary),
                "candidate executable changed while staging the private copy")
        sources_sha = source_digest(args.source_root)
        runner = load_native_runner(args.source_root, root / "native-sources")
        require(source_digest(args.source_root) == sources_sha,
                "candidate/helper/lifecycle sources changed while staging")
        for command in ("node", "bwrap"):
            if shutil.which(command) is None:
                raise EnvironmentUnavailable(command + " is unavailable")
        try:
            pg_bin = disposable_host.pg_bindir()
        except FileNotFoundError:
            raise EnvironmentUnavailable("pg_config is unavailable") from None
        for command in ("initdb", "pg_ctl", "createdb", "psql"):
            if not os.access(pg_bin / command, os.X_OK):
                raise EnvironmentUnavailable("PostgreSQL " + command + " is unavailable")
        cluster = disposable_host.OwnedCluster(root, pg_bin)
        disposable_host.OwnedCluster.start(root, pg_bin)
        host = disposable_host.DisposableHost.start(cluster, binary=args.binary,
            repo="fixture:wc03-native-waits", database="cairn_work_wait_native",
            roles=("alice", "bob"), principals=lambda role: "agent/" + role,
            label="work_wait_native", inbox_root=True)
        host.require_fresh("agent_event", "agent_session", "agent_wait", "agent_wait_outbox")
        enabled = host.operator("wait-feature-set", {"request_id": new_id(), "enabled": True})
        require(enabled["feature_name"] == "coordination-waits/1" and enabled["enabled"] is True
                and enabled["status"] == "active", "operator did not enable coordination waits")

        def scheduler():
            expected = int(host.sql("SELECT count(*) FROM cairn.agent_wait_outbox WHERE state='pending'"))
            result = host.operator("schedule-serve", args=("--repo", host.repo, "--once"))
            require(result["repo"] == host.repo and result["swept_timeouts"] == 0
                    and result["published_notices"] == expected and result["schedule_occurrences"] == 0,
                    "scheduler did not publish every exact eligible pending notice")
            retry = host.operator("wait-scheduler-tick", {"repo": host.repo, "limit": 100})
            require(retry == {"repo": host.repo, "swept_timeouts": 0, "published_notices": 0},
                    "operator tick replayed an already-published wait notification")

        fixture = WaitFixture(host)
        fixture.store_boundaries(scheduler)
        native = [fixture.native_delivery(runner, args, scheduler, predicate)
                  for predicate in ("request_response", "response_group_closed")]
        require(digest(args.binary) == binary_sha, "candidate executable changed during acceptance")
        require(source_digest(args.source_root) == sources_sha,
                "candidate/helper/lifecycle sources changed during run; acceptance incomplete")
        return {"schema": "cairn.work-wait-native-proof/1", "result": "PASS",
                "candidate_sha256": binary_sha, "source_inputs_sha256": sources_sha,
                "store_boundaries": {
                    "lost_reply": "committed_create_reply_dropped_then_reopened",
                    "outbox_restart_exact_counts": True, "offline_resume_retained_address": True,
                    "published_unclaimed_restore_fence": ["generic_poll", "native_ready",
                                                           "native_pending", "native_claim"],
                    "same_owner_postrestore_positive_control": True},
                "native_delivery": native, "original_task_outcome": "unknown",
                "actor_decision": "scripted_fixture_sequence", "provider_called": False}
    finally:
        try:
            if host is not None:
                require(host.close() == 0, "disposable API did not exit cleanly")
        finally:
            if cluster is not None:
                cluster.remove()
            else:
                shutil.rmtree(root)


def main():
    try:
        args = arguments()
    except FileNotFoundError as error:
        print(json.dumps({"schema": "cairn.work-wait-native-proof/1", "result": "UNRUN",
                          "reason": "required fixture path unavailable", "path": error.filename}))
        return 2
    try:
        result = check(args)
    except EnvironmentUnavailable as error:
        print(json.dumps({"schema": "cairn.work-wait-native-proof/1", "result": "UNRUN",
                          "reason": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
