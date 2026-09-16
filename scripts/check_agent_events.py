"""Real CLI/API restart probe, only against the integration suite's disposable DB."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import time
import uuid


def check(binary, directory):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert os.environ["CAIRN_DATABASE_URL"] == os.environ["CAIRN_TEST_DATABASE_URL"]
    root = Path(directory)
    root.mkdir(mode=0o700)
    repo = "event-probe:" + str(uuid.uuid4())
    identities = []
    for name in ("alice", "bob", "carol"):
        token = secrets.token_urlsafe(32)
        path = root / (name + ".token")
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal="agent/" + name, repo=repo, role="agent",
                               destination="hosted"))
    config = root / "identities.json"
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    provision = Path(__file__).with_name("provision-event-profiles.py")
    provision_args = [sys.executable, str(provision), "--home", str(root), "--repo", repo, "probe"]
    subprocess.run(provision_args, check=True, capture_output=True, text=True, timeout=10)
    original_token = (root / "event-profiles/probe.token").read_bytes()
    subprocess.run(provision_args, check=True, capture_output=True, text=True, timeout=10)
    assert (root / "event-profiles/probe.token").read_bytes() == original_token
    env = dict(os.environ, CAIRN_HOME=str(root))
    process = None

    def call(name, command, *args, body=None, expected="OK", raw=False):
        connection = ["--token-file", str(root / (name + ".token"))]
        if raw:
            argv = [binary, "agent", *connection, command, *args]
        else:
            argv = [binary, command]
            if command == "inbox":
                argv.append("next")
            if command == "agents":
                argv.append(args[0])
                args = args[1:]
            argv += connection + list(args)
        result = subprocess.run(argv, input=body, env=env, text=True,
                                capture_output=True, timeout=15)
        output = json.loads(result.stdout)
        assert output["status"] == expected, (argv, output, result.stderr)
        assert (result.returncode == 0) == (expected == "OK"), output
        return output.get("data")

    def start():
        proc = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read())
            if (root / "api.sock").exists():
                call("alice", "version", raw=True)
                return proc
            time.sleep(0.02)
        proc.terminate()
        proc.wait(timeout=10)
        raise AssertionError("event probe API did not start")

    def stop(proc):
        proc.terminate()
        proc.communicate(timeout=10)
        assert proc.returncode == 0

    def publish(to=None, topic=None, request=None, source=None, cause=None):
        args = ["--request-id", request or str(uuid.uuid4()), "--kind", "request", "--version", "1"]
        args += ["--to", to] if to else ["--topic", topic]
        if cause:
            args += ["--causation-id", cause]
        return call("alice", "publish", *args, source or note["record_id"])

    try:
        process = start()
        profile = subprocess.run([binary, "event-stats", "--profile", "probe"], env=env,
                                 capture_output=True, text=True, timeout=10, check=True)
        assert json.loads(profile.stdout)["data"]["deliveries"] == 0
        note = call("alice", "remember", "--shareable", "--repo", repo,
                    "Event probe source", raw=True)
        request = str(uuid.uuid4())
        event = publish(to="agent/bob", request=request)
        assert publish(to="agent/bob", request=request) == event
        stop(process)
        process = start()
        delivery = call("bob", "inbox")["delivery"]
        assert delivery["event"]["event_id"] == event["event_id"]
        history = call("bob", "history", raw=True, body=json.dumps(delivery["event"]["ref"]))
        assert history["versions"][0]["body"] == "Event probe source"
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", delivery["lease_id"],
             delivery["delivery_id"], expected="NOT_FOUND")
        call("bob", "inbox", "--agent", "agent/alice", expected="AUTHORITY_DENIED")
        complete_args = ["--request-id", str(uuid.uuid4()), "--lease", delivery["lease_id"],
                         "--shareable", "--stdin", delivery["delivery_id"]]
        done = call("bob", "complete", *complete_args, body="Selected response")
        assert done["state"] == "handled" and done["result"]["version"] == 1
        assert call("bob", "complete", *complete_args, body="Selected response") == done
        assert call("bob", "inbox")["delivery"] is None
        status = call("alice", "event-status", event["event_id"])
        assert status["deliveries"][0]["result"] == done["result"]
        response = call("bob", "publish", "--request-id", str(uuid.uuid4()), "--to", "agent/alice",
                        "--kind", "response", "--version", "1", "--causation-id", event["event_id"],
                        done["result"]["record_id"])
        assert response["causation_id"] == event["event_id"]
        for name in ("bob", "carol"):
            call(name, "subscribe", "--request-id", str(uuid.uuid4()), "--topic", "reviews")
        topic_event = publish(topic="reviews")
        call("bob", "unsubscribe", "--request-id", str(uuid.uuid4()), "--topic", "reviews")
        for name in ("bob", "carol"):
            d = call(name, "inbox")["delivery"]
            assert d["event"]["event_id"] == topic_event["event_id"]
            call(name, "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"], d["delivery_id"])
        publish(topic="reviews")
        assert call("bob", "inbox")["delivery"] is None
        d = call("carol", "inbox", "--lease-seconds", "1")["delivery"]
        stop(process)
        process = start()
        time.sleep(1.05)
        again = call("carol", "inbox")["delivery"]
        assert again["event"]["event_id"] == d["event"]["event_id"]
        assert again["lease_id"] != d["lease_id"] and again["attempts"] == 2
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"],
             d["delivery_id"], expected="STALE_LEASE")
        call("carol", "renew", "--lease", again["lease_id"], again["delivery_id"])
        call("carol", "retry", "--lease", again["lease_id"], again["delivery_id"])
        d = call("carol", "inbox")["delivery"]
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", d["lease_id"],
             "--disposition", "failed", "--code", "processing_failed", d["delivery_id"])
        assert call("carol", "event-stats")["redeliveries"] == 2
        # Local operator recovery uses the real store channel, preserves failed
        # history, and creates a fresh request for only the failed recipient.
        def operator(command, request, expected="OK"):
            result = subprocess.run([binary, command], input=json.dumps(request), env=env,
                                    text=True, capture_output=True, timeout=15)
            envelope = json.loads(result.stdout)
            assert envelope["status"] == expected, envelope
            assert (result.returncode == 0) == (expected == "OK"), envelope
            return envelope.get("data")
        review = operator("coordination-review", dict(repo=repo, state="failed"))
        failed = next(row for row in review["deliveries"]["deliveries"] if row["delivery_id"] == d["delivery_id"])
        assert failed["execution"] == "may_have_executed" and failed["reported_task_outcome"] == "unknown"
        request = dict(request_id=str(uuid.uuid4()), repo=repo, delivery_id=d["delivery_id"],
                       reason="Review fixture prior effects before an explicit new request", accept_uncertain_effects=False)
        operator("event-reissue", request, expected="INVALID_REQUEST")
        request.update(request_id=str(uuid.uuid4()), accept_uncertain_effects=True)
        fresh = operator("event-reissue", request)
        assert operator("event-reissue", request) == fresh
        assert fresh["causation_id"] == d["event"]["event_id"] and fresh["from"].startswith("local-uid:")
        assert fresh["destination"] == dict(type="agent", name="agent/carol")
        retained = operator("coordination-review", dict(repo=repo, delivery_id=d["delivery_id"]))["deliveries"]["deliveries"][0]
        assert retained["state"] == "failed" and retained["reissued_as"]["event_id"] == fresh["event_id"]
        successor = call("carol", "inbox")["delivery"]
        assert successor["event"]["event_id"] == fresh["event_id"] and successor["attempts"] == 1
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", successor["lease_id"], successor["delivery_id"])
        first = call("bob", "events", "--limit", "1")
        assert first["more"] and first["events"][0]["event_id"] == event["event_id"]
        later = call("bob", "events", "--after", str(first["next_after"]), "--limit", "100")
        assert all(e["position"] > first["next_after"] for e in later["events"])
        raw = dict(request_id=str(uuid.uuid4()), kind="notice", ref=event["ref"],
                   destination=dict(type="agent", name="agent/bob"), **{"from": "agent/carol"})
        call("alice", "event-publish", raw=True, body=json.dumps(raw), expected="INVALID_REQUEST")
        registration = ["register", "--request-id", str(uuid.uuid4()), "--binding", "codex-default",
                        "--native-session", "one", "--harness", "codex", "--model", "configured-model",
                        "--project", "rhumb", "--project-alias", "charts", "--workspace", str(root), "--state", "busy"]
        registered = call("bob", "agents", *registration)
        assert call("bob", "agents", *registration) == registered
        second_registration = list(registration)
        second_registration[2] = str(uuid.uuid4())
        second_registration[second_registration.index("--native-session") + 1] = "two"
        second_agent = call("bob", "agents", *second_registration)
        assert second_agent["agent_id"] != registered["agent_id"]
        listing = call("alice", "agents", "list", "--harness", "codex", "--project", "rhumb")
        assert len(listing["agents"]) == 2
        session = ["--agent-id", registered["agent_id"], "--execution-id", registered["execution_id"]]
        ambiguous = call("alice", "agents", "resolve", "--harness", "codex", "--project", "charts")
        assert ambiguous["state"] == "ambiguous" and len(ambiguous["candidates"]) == 2
        call("bob", "agents", "leave", "--agent-id", second_agent["agent_id"],
             "--execution-id", second_agent["execution_id"])
        selected = call("alice", "agents", "resolve", "--harness", "codex", "--project", "charts")
        assert selected["state"] == "unique" and selected["resolution"]["agent_id"] == registered["agent_id"]
        selection_file = root / "selected.json"
        selection_file.write_text(json.dumps(selected["resolution"]))
        send_args = ["--request-id", str(uuid.uuid4()), "--kind", "request", "--version", "1",
                     "--resolution", str(selection_file), note["record_id"]]
        message = call("alice", "publish", *send_args)
        assert message["destination"]["name"] == registered["inbox"]
        assert message["resolution"] == selected["resolution"]
        assert call("bob", "inbox")["delivery"] is None
        native_claim = dict(request_id=str(uuid.uuid4()), session={k: registered[k] for k in ('agent_id', 'execution_id')})
        native_attempt = call('bob', 'session-inbox-claim', raw=True, body=json.dumps(native_claim))['attempt']
        stop(process)
        process = start()
        recovered_attempt = call('bob', 'session-inbox-claim', raw=True, body=json.dumps(native_claim))['attempt']
        assert recovered_attempt == native_attempt, 'API restart changed native delivery ownership'
        assert call('bob', 'inbox', *session)['delivery'] is None
        delivered = recovered_attempt['delivery']
        assert delivered["event"]["event_id"] == message["event_id"]
        finished = call("bob", "complete", *session, "--request-id", str(uuid.uuid4()),
                        "--lease", delivered["lease_id"], "--shareable", "--stdin",
                        delivered["delivery_id"], body="Session handled selected request")
        assert finished["state"] == "handled"
        call('bob', 'session-inbox-reconcile', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            session=native_claim['session'], attempt_id=native_attempt['attempt_id'], reason='delivery_completed')))
        call("bob", "agents", "heartbeat", *session)
        registration[2] = str(uuid.uuid4())
        resumed = call("bob", "agents", *registration)
        assert resumed["agent_id"] == registered["agent_id"]
        assert resumed["execution_id"] != registered["execution_id"]
        call("bob", "inbox", *session, expected="STALE_SESSION")
        assert call("alice", "publish", *send_args) == message
        send_args[1] = str(uuid.uuid4())
        call("alice", "publish", *send_args, expected="STALE_RESOLUTION")
        call("bob", "agents", "leave", "--agent-id", resumed["agent_id"], "--execution-id", resumed["execution_id"])
        assert call("alice", "agents", "resolve", "--project", "rhumb")["state"] == "stale"
        assert call("alice", "agents", "resolve", "--project", "missing")["state"] == "no-match"
        from check_agent_coordination import check as check_coordination
        check_coordination(binary, root, repo, call)
        # Keep a real watch process alive across API restart. It must read the
        # next arrival using the persisted cursor without claiming either page.
        cursor_path = root / "bob-watch.cursor"
        watch_args = [binary, "watch", "--token-file", str(root / "bob.token"),
                      "--cursor-file", str(cursor_path)]
        initial = subprocess.run(watch_args + ["--once"], env=env, capture_output=True,
                                 text=True, timeout=10, check=True)
        prior = json.loads(initial.stdout)
        assert prior["cursor"] == cursor_path.read_text()
        with (root / "watch.jsonl").open("w") as output, (root / "watch.stderr").open("w+") as diagnostic:
            watcher = subprocess.Popen(watch_args, env=env, stdout=output, stderr=diagnostic)
            def watch_pages(count):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    assert watcher.poll() is None, "watch exited unexpectedly"
                    lines = (root / "watch.jsonl").read_text().splitlines()
                    try:
                        pages = [json.loads(line) for line in lines]
                    except json.JSONDecodeError:
                        pages = []  # The writer may still be finishing a page.
                    if len(pages) >= count:
                        return pages
                    time.sleep(0.02)
                raise AssertionError("watch did not produce the expected bounded page")
            try:
                first_watch = watch_pages(1)[0]
                assert first_watch["deliveries"] == [] and first_watch["cursor"] == prior["cursor"]
                stop(process)
                process = None
                time.sleep(1.1)  # Cross a failed poll before restarting the real API.
                process = start()
                marker = publish(to="agent/bob")
                arrivals = watch_pages(2)[1]["deliveries"]
                assert len(arrivals) == 1 and arrivals[0]["event"]["event_id"] == marker["event_id"]
                claim = call("bob", "inbox")["delivery"]
                assert claim["delivery_id"] == arrivals[0]["delivery_id"] and claim["attempts"] == 1
                call("bob", "ack", "--request-id", str(uuid.uuid4()), "--lease", claim["lease_id"], claim["delivery_id"])
            finally:
                watcher.terminate()
                watcher.wait(timeout=5)
            assert watcher.returncode == 0
            diagnostic.seek(0)
            assert (root / "bob.token").read_text() not in diagnostic.read()
        resumed_watch = subprocess.run(watch_args + ["--once"], env=env, capture_output=True,
                                      text=True, timeout=10, check=True)
        assert json.loads(resumed_watch.stdout)["deliveries"] == []
        # A stopped scheduler retains intent. Restart catches up once within
        # grace, and a second restart cannot create another event/arrival.
        due = datetime.now(timezone.utc) + timedelta(seconds=2)
        scheduling = dict(request_id=str(uuid.uuid4()), not_before=due.isoformat(), grace_seconds=30,
                          publication=dict(repo=repo, kind="notice", ref=event["ref"],
                                           destination=dict(type="agent", name="agent/carol")))
        scheduled = operator("event-schedule", scheduling)
        assert operator("event-schedule", scheduling) == scheduled
        assert operator("schedule-tick", dict(repo=repo))["occurrences"] == []
        scheduler_args = [binary, "schedule-serve", "--repo", repo]
        scheduler = subprocess.Popen(scheduler_args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert select.select([scheduler.stderr], [], [], 5)[0], "scheduler readiness timed out"
            assert scheduler.stderr.readline().strip() == "scheduler ready"
        finally:
            scheduler.terminate()
            scheduler.communicate(timeout=5)
        assert scheduler.returncode == 0
        time.sleep(2.1)
        scheduler = subprocess.Popen(scheduler_args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            end = time.monotonic() + 10
            while time.monotonic() < end:
                current = operator("schedule-list", dict(repo=repo, occurrence_id=scheduled["occurrence_id"]))["occurrences"][0]
                if current["state"] == "fired":
                    break
                assert scheduler.poll() is None
                time.sleep(0.05)
            else:
                raise AssertionError("scheduler did not fire due occurrence")
        finally:
            scheduler.terminate()
            scheduler.communicate(timeout=5)
        assert scheduler.returncode == 0
        assert current["event_id"] == scheduled["occurrence_id"]
        delivered = call("carol", "inbox")["delivery"]
        assert delivered["event"]["event_id"] == scheduled["occurrence_id"]
        assert delivered["event"]["from"].startswith("local-uid:")
        call("carol", "ack", "--request-id", str(uuid.uuid4()), "--lease", delivered["lease_id"], delivered["delivery_id"])
        assert operator("schedule-tick", dict(repo=repo))["occurrences"] == []
        assert call("carol", "inbox")["delivery"] is None
        # Keep populated pending, skipped and cancelled states for backup checks.
        scheduling.update(request_id=str(uuid.uuid4()), not_before=(due + timedelta(hours=1)).isoformat())
        pending = operator("event-schedule", scheduling)
        scheduling["request_id"] = str(uuid.uuid4())
        cancelled = operator("event-schedule", scheduling)
        cancellation = dict(request_id=str(uuid.uuid4()), repo=repo, occurrence_id=cancelled["occurrence_id"])
        saved = operator("schedule-cancel", cancellation)
        assert saved["state"] == "cancelled" and operator("schedule-cancel", cancellation) == saved
        scheduling.update(request_id=str(uuid.uuid4()), not_before=(due - timedelta(hours=1)).isoformat())
        skipped = operator("event-schedule", scheduling)
        missed = operator("schedule-tick", dict(repo=repo))["occurrences"]
        assert len(missed) == 1 and missed[0]["occurrence_id"] == skipped["occurrence_id"] and missed[0]["code"] == "misfire"
        assert operator("schedule-list", dict(repo=repo, state="pending"))["occurrences"][0]["occurrence_id"] == pending["occurrence_id"]
        operator("schedule-cancel", dict(request_id=str(uuid.uuid4()), repo=repo, occurrence_id=scheduled["occurrence_id"]), "VERSION_CONFLICT")
        print("Scheduling: durable one-shot restart, exact occurrence identity, misfire audit and pending cancellation passed")
        print("Inbox watch: real CLI/API restart, cursor checkpoint, resume and read-only delivery passed")
        print("Agent resolution: exact aliases, ambiguity, pinned publication, retry and stale refusal passed")
        print("Agent sessions: stable identity, shared-profile inbox separation, completion and API restart passed")
        print("Agent events: CLI direct/offline delivery, replies, fanout, retry, leases and API restart passed")
        print("Operator recovery: explicit reissue, retained failure, new attribution, no topic refanout and exact retry passed")
    finally:
        if process is not None and process.poll() is None:
            stop(process)


if __name__ == "__main__":
    check(*sys.argv[1:])
