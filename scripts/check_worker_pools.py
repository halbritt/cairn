"""Exercise real API, supervisor and worker pools on a disposable database.

Probes pool configuration, publication, queued status before launch,
atomic assignment, explicit worker completion, slot unavailable state with
heartbeat preservation, explicit health recovery, and supervisor restart
retaining accepted work.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import uuid


def wait_for(check, seconds=30, interval=0.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition timed out")


def load_native_fixture():
    wakeups_script = Path(__file__).with_name("check_wakeups.py")
    if wakeups_script.exists():
        spec = importlib.util.spec_from_file_location("check_wakeups", wakeups_script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "NativeFixture", None)
    return None


def check(binary, root, opencode=None, hermes=None, codex=None, agy=None):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL"), "CAIRN_TEST_DATABASE_URL required"
    assert os.environ["CAIRN_DATABASE_URL"] == os.environ["CAIRN_TEST_DATABASE_URL"], (
        "CAIRN_DATABASE_URL must be identical to CAIRN_TEST_DATABASE_URL"
    )

    root.mkdir(mode=0o700)
    repo = "pool-probe:" + str(uuid.uuid4())
    pool_name = "probe-pool"

    identities = []
    for name, role in (("agent", "agent"), ("observer", "observer"), ("publisher", "agent")):
        token = secrets.token_urlsafe(32)
        path = root / (name + ".token")
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal="pool-probe/" + name, role=role, repo=repo,
                               destination="hosted"))
    (root / "identities.json").write_text(json.dumps(identities))
    (root / "identities.json").chmod(0o600)

    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                           stderr=(root / "api.log").open("w"))
    supervisor = None
    units = set()
    report = []

    config = dict(
        name="probe-worker",
        principal="pool-probe/agent",
        repo=repo,
        socket=str(root / "api.sock"),
        agent_token=str(root / "agent.token"),
        observer_token=str(root / "observer.token"),
        directory=str(root),
        state_directory=str(root / "artifacts"),
        timeout_seconds=60,
        worker=dict(
            name="probe-worker",
            harness="generic",
            workspace=str(root),
            pools=[pool_name],
            launch_spacing_seconds=1,
        ),
    )
    config_path = root / "wake.json"

    def call(op, data, identity="agent"):
        p = subprocess.run([binary, "agent", "--socket", config["socket"], "--token-file",
                            str(root / (identity + ".token")), op], input=json.dumps(data),
                           text=True, capture_output=True, timeout=15)
        assert p.returncode == 0, (op, p.stdout, p.stderr)
        return json.loads(p.stdout)["data"]

    def configure_pool(name, enabled=True, max_pending=20, max_pending_per_publisher=10, expected_rev=0):
        req = dict(
            request_id=str(uuid.uuid4()),
            repo=repo,
            name=name,
            enabled=enabled,
            max_pending=max_pending,
            max_pending_per_publisher=max_pending_per_publisher,
            expected_revision=expected_rev,
        )
        p = subprocess.run([binary, "worker-pool-configure"],
                           input=json.dumps(req), text=True, capture_output=True,
                           env=env, timeout=15)
        assert p.returncode == 0, ("worker-pool-configure", p.stdout, p.stderr)
        return json.loads(p.stdout)["data"]

    def publish(body, pool=pool_name, workspace=None, harness=None, identity="publisher"):
        if workspace is None:
            workspace = str(root)
        note = call("create", dict(request_id=str(uuid.uuid4()), draft=dict(
            kind="note", body=body, scope=dict(repo=repo, task_id="*", run_id="*"),
            claim_type="self", sensitivity="shareable")), identity=identity)
        pool_req = dict(workspace=workspace)
        if harness:
            pool_req["harness"] = harness
        return call("event-publish", dict(request_id=str(uuid.uuid4()), kind="request",
                    ref=dict(record_id=note["record_id"], version=1),
                    destination=dict(type="pool", name=pool),
                    pool=pool_req), identity=identity)

    def status(event, identity="publisher"):
        return call("event-inspect", dict(event_id=event["event_id"]), identity=identity)

    def active():
        page = call("wake-attempts", dict(active=True))["attempts"]
        for w in page:
            units.add("cairn-wake-attempt-" + w["attempt_id"] + ".service")
        return page

    def start(command):
        nonlocal supervisor
        config["command"] = command
        config_path.write_text(json.dumps(config))
        supervisor = subprocess.Popen([binary, "wake", "serve", "--config", str(config_path)],
                                      stdout=subprocess.DEVNULL, stderr=(root / "supervisor.log").open("a"))

    def stop(kill=False):
        nonlocal supervisor
        if supervisor:
            supervisor.send_signal(signal.SIGKILL if kill else signal.SIGTERM)
            supervisor.wait(timeout=35)
            if not kill:
                assert supervisor.returncode == 0, (root / "supervisor.log").read_text()
            supervisor = None

    worker = root / "worker.py"
    worker.write_text('''import json,os,re,shlex,stat,subprocess,sys,time
from pathlib import Path
prompt=sys.argv[-1]
assert os.environ.get('CAIRN_LIFECYCLE_CHILD')=='1', 'wake must suppress duplicate lifecycle memory capture'
context_path=Path(os.environ["CAIRN_WAKE_CONTEXT"])
assert stat.S_IMODE(context_path.stat().st_mode)==0o600
wake=json.loads(context_path.read_text())
assert wake["schema"]=="cairn.wake-context/1"
assert wake["binding"]=="probe-worker" and wake["inbox"]=="pool-probe/agent"
assert wake["execution_id"]==context_path.name.removesuffix(".context.json")
command=re.search(r"^('.* complete .*) < RESULT_FILE$",prompt,re.M).group(1)
assert shlex.split(command)==wake["completion"]
assert wake["delivery_id"]==wake["completion"][-1]
assert wake["lease_id"]==wake["completion"][wake["completion"].index("--lease")+1]
assert wake["source"]["record_id"] in prompt
assert wake["deadline"] and wake["workspace"]==str(Path.cwd())
subprocess.run(wake["completion"],input="Selected worker pool fixture result",text=True,check=True)
''')
    command = ["/usr/bin/python3", str(worker)]

    try:
        wait_for(lambda: (root / "api.sock").exists())

        # 1. Operator pool configuration
        configured = configure_pool(pool_name, enabled=True, max_pending=20, max_pending_per_publisher=10, expected_rev=0)
        assert configured["name"] == pool_name and configured["enabled"] is True
        assert configured["revision"] == 1

        # 2. Pool publication and queued status before launch
        event1 = publish("POOL-COMPLETE-FIXTURE-1")
        st1 = status(event1)
        assert st1.get("pool"), ("missing pool status in event inspection", st1)
        assert st1["pool"]["state"] == "queued", ("expected queued state before launch", st1)
        assert not st1["pool"].get("delivery_id"), ("queued pool event should not have delivery_id", st1)
        assert len(st1.get("deliveries", [])) == 0, ("queued pool event should have no deliveries", st1)
        report.append("pool publication and queued status before launch")

        # 3. Supervisor launch, atomic assignment and explicit worker completion
        start(command)
        wait_for(lambda: status(event1)["pool"]["state"] == "assigned" and len(status(event1).get("deliveries", [])) == 1)
        assigned_st1 = status(event1)
        assert assigned_st1["deliveries"][0]["consumer"] == "pool-probe/agent"
        assert assigned_st1["pool"]["delivery_id"] == assigned_st1["deliveries"][0]["delivery_id"]

        wait_for(lambda: status(event1)["deliveries"][0]["state"] == "handled")
        wait_for(lambda: not active())
        done1 = [w for w in call("wake-attempts", {})["attempts"] if w["delivery"]["event"]["event_id"] == event1["event_id"]][0]
        assert done1["receipt_id"] and done1["delivery"]["result"]
        report.append("atomic pool assignment, explicit completion and runner receipt")

        # 4. Slot unavailable plus heartbeat preservation
        workers = call("worker-list", {})["workers"]
        slot = next(w for w in workers if w["consumer"] == "pool-probe/agent")
        assert slot["health"] == "available"
        slot_rev = slot["revision"]

        unavail_res = call("worker-health", dict(
            request_id=str(uuid.uuid4()),
            supervisor_id=slot["supervisor_id"],
            expected_revision=slot_rev,
            health="unavailable",
            reason="simulated account quota exhaustion",
        ))
        assert unavail_res["health"] == "unavailable"
        assert unavail_res["revision"] == slot_rev + 1

        event2 = publish("POOL-UNAVAILABLE-FIXTURE-2")
        st2 = status(event2)
        assert st2["pool"]["state"] == "queued"

        hb_res = call("worker-heartbeat", dict(supervisor_id=slot["supervisor_id"]))
        assert hb_res["health"] == "unavailable", ("heartbeat must not heal unavailable health", hb_res)

        # Confirm supervisor does not claim while unavailable
        time.sleep(3)
        st2_check = status(event2)
        assert st2_check["pool"]["state"] == "queued", ("event claimed while worker slot unavailable", st2_check)
        assert len(st2_check.get("deliveries", [])) == 0
        assert not active()
        report.append("slot unavailable plus heartbeat preservation")

        # 5. Explicit health recovery
        rec_res = call("worker-health", dict(
            request_id=str(uuid.uuid4()),
            supervisor_id=slot["supervisor_id"],
            expected_revision=unavail_res["revision"],
            health="available",
            reason="quota restored, explicit recovery",
        ))
        assert rec_res["health"] == "available"

        # Supervisor should now claim and handle event2
        wait_for(lambda: status(event2)["deliveries"] and status(event2)["deliveries"][0]["state"] == "handled")
        wait_for(lambda: not active())
        done2 = [w for w in call("wake-attempts", {})["attempts"] if w["delivery"]["event"]["event_id"] == event2["event_id"]][0]
        assert done2["receipt_id"] and done2["delivery"]["result"]
        report.append("explicit health recovery and queued work drainage")

        # 6. Supervisor restart retaining accepted work
        stop()
        # Verify previously accepted and handled work remains intact
        assert status(event1)["deliveries"][0]["state"] == "handled"
        assert status(event2)["deliveries"][0]["state"] == "handled"

        # Publish work while supervisor is offline
        event3 = publish("POOL-RESTART-FIXTURE-3")
        assert status(event3)["pool"]["state"] == "queued"

        # Restart supervisor and verify it handles event3 without corrupting prior state
        start(command)
        wait_for(lambda: status(event3)["deliveries"] and status(event3)["deliveries"][0]["state"] == "handled")
        wait_for(lambda: not active())
        stop()

        assert status(event1)["deliveries"][0]["state"] == "handled"
        assert status(event2)["deliveries"][0]["state"] == "handled"
        assert status(event3)["deliveries"][0]["state"] == "handled"
        report.append("supervisor restart retaining accepted work")

        # 7. Native OpenCode fixture if requested
        if opencode:
            native_fixture_cls = load_native_fixture()
            assert native_fixture_cls is not None, "NativeFixture could not be loaded from check_wakeups.py"
            native_harness = "opencode"
            with native_fixture_cls(root / "opencode", opencode, native_harness, dict(
                cairn=binary, socket=config["socket"], token_file=config["agent_token"],
                repo=repo, harness=native_harness, binding="probe-worker", process_names=[]
            )) as fixture:
                config["worker"]["harness"] = native_harness
                event_native = publish("POOL-NATIVE-COMPLETE-FIXTURE", harness=native_harness)
                start(fixture.command)
                wait_for(lambda: status(event_native)["deliveries"] and status(event_native)["deliveries"][0]["state"] in ("handled", "failed"), 90)
                assert status(event_native)["deliveries"][0]["state"] == "handled", ("native worker pool event failed", (root / "supervisor.log").read_text())
                wait_for(lambda: not active())
                stop()
                assert fixture.completed_tool, ("opencode fixture did not complete tool call", fixture.observed)
                attempt = next(w for w in call("wake-attempts", {})["attempts"] if w["delivery"]["event"]["event_id"] == event_native["event_id"])
                assert attempt.get("session"), ("opencode", "native wake session missing", root)
                native_agent = call("agent-directory", dict(agent_id=attempt["session"]["agent_id"], include_offline=True))["agents"][0]
                assert native_agent["metadata"]["harness"] == native_harness and native_agent["stopped"]
                assert native_agent["native_session_id"]
                report.append("opencode native session association, tool execution and atomic pool completion")
        else:
            report.append("native harness gap: --opencode was not provided; probe executed with generic worker fixture")

        if codex:
            from check_codex_provider import CodexQuotaFixture
            config['worker']['harness'] = 'codex'
            for plan in ('plus', 'pro'):
                with CodexQuotaFixture(root / ('codex-quota-' + plan), codex, dict(
                    cairn=binary, socket=config['socket'], token_file=config['agent_token'],
                    repo=repo, binding='probe-worker'), plan=plan) as fixture:
                    native_quota = publish('NATIVE-CODEX-QUOTA-' + plan, harness='codex')
                    start(fixture.command)
                    wait_for(lambda: status(native_quota)['deliveries'] and status(native_quota)['deliveries'][0]['state'] == 'failed', 90)
                    wait_for(lambda: not active())
                    stop()
                    attempt = next(w for w in call('wake-attempts', {})['attempts'] if w['delivery']['event']['event_id'] == native_quota['event_id'])
                    assert fixture.requests, 'Codex did not reach loopback provider'
                    assert attempt.get('session'), ('Codex native session missing', attempt)
                    assert attempt['provider_failure']['code'] == 'codex_usage_limit_reached', attempt
                    assert attempt['provider_failure']['source'] == 'native-diagnostic', attempt
                    slot = next(w for w in call('worker-list', {})['workers'] if w['consumer'] == 'pool-probe/agent')
                    assert slot['health'] == 'unavailable', slot
                    call('worker-health', dict(request_id=str(uuid.uuid4()), supervisor_id=slot['supervisor_id'],
                        expected_revision=slot['revision'], health='available', reason='Explicit fixture recovery after verified Codex subscription limit'))
            report.append('installed Codex Plus/Pro subscription-limit events retain native session and suspend the owning slot')

        if agy:
            from check_agy_provider import AgyProviderFixture
            config['worker']['harness'] = 'agy'
            for mode in ('quota', 'nonquota', 'later-error', 'recovered'):
                with AgyProviderFixture(root / ('agy-' + mode), agy, dict(
                    cairn=binary, socket=config['socket'], token_file=config['agent_token'],
                    repo=repo, binding='probe-worker'), mode) as fixture:
                    event = publish('NATIVE-AGY-' + mode, harness='agy')
                    start(fixture.command)
                    wait_for(lambda: status(event)['deliveries'] and status(event)['deliveries'][0]['state'] == 'failed', 90)
                    wait_for(lambda: not active())
                    stop()
                    attempt = next(w for w in call('wake-attempts', {})['attempts'] if w['delivery']['event']['event_id'] == event['event_id'])
                    assert fixture.requests and attempt.get('session'), ('missing native Agy provider/session', attempt)
                    slot = next(w for w in call('worker-list', {})['workers'] if w['consumer'] == 'pool-probe/agent')
                    if mode == 'quota':
                        assert attempt['provider_failure'] == dict(harness='agy', source='native-diagnostic', kind='rate_limit', code='agy_http_429', status=429), attempt
                        assert slot['health'] == 'unavailable', slot
                        call('worker-health', dict(request_id=str(uuid.uuid4()), supervisor_id=slot['supervisor_id'],
                            expected_revision=slot['revision'], health='available', reason='Explicit fixture recovery after observed Agy rate limit'))
                    else:
                        assert not attempt.get('provider_failure') and slot['health'] == 'available', (attempt, slot)
                        if mode == 'recovered':
                            assert any(r['main'] and r['status'] == 200 for r in fixture.requests), fixture.requests
            report.append('installed Agy rate limit suspends its slot; different errors and recovered retries preserve availability')

        # Exercise the stream -> local observation -> report -> durable admission
        # path with selected synthetic native envelopes, without a provider call.
        config['worker']['harness'] = 'codex'
        failed_worker = root/'quota.py'
        failed_worker.write_text('import json\nprint(json.dumps({"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}),flush=True)\n')
        quota_event = publish('QUOTA-FAILURE-FIXTURE',harness='codex')
        start(['/usr/bin/python3',str(failed_worker)])
        # A preceding native failure retains the slot's 30-second launch backoff
        # even after explicit health recovery; include admission and execution.
        wait_for(lambda: status(quota_event)['deliveries'] and status(quota_event)['deliveries'][0]['state']=='failed', 90)
        wait_for(lambda:not active())
        quota_attempt = next(w for w in call('wake-attempts',{})['attempts'] if w['delivery']['event']['event_id']==quota_event['event_id'])
        assert quota_attempt['provider_failure']['kind']=='quota',quota_attempt
        assert quota_attempt['provider_failure']['source']=='native-diagnostic',quota_attempt
        assert quota_attempt['provider_failure_at']
        slot = next(w for w in call('worker-list',{})['workers'] if w['consumer']=='pool-probe/agent')
        assert slot['health']=='unavailable',slot
        stop()
        start(command)
        wait_for(lambda:next(w for w in call('worker-list',{})['workers'] if w['consumer']=='pool-probe/agent')['supervisor_id']!=slot['supervisor_id'])
        slot = next(w for w in call('worker-list',{})['workers'] if w['consumer']=='pool-probe/agent')
        assert slot['health']=='unavailable',slot
        blocked = publish('QUOTA-BLOCKED-FIXTURE',harness='codex')
        time.sleep(3)
        assert status(blocked)['pool']['state']=='queued'
        assert not active()
        stop()
        report.append('synthetic Codex terminal quota suspends only its slot, persists after restart and prevents subsequent admission')

        if hermes:
            call('worker-health',dict(request_id=str(uuid.uuid4()),supervisor_id=slot['supervisor_id'],
                expected_revision=slot['revision'],health='available',reason='Explicit fixture recovery for independent Hermes probe'))
            config['worker']['harness']='hermes'
            native_fixture_cls=load_native_fixture()
            assert native_fixture_cls is not None
            with native_fixture_cls(root/'hermes-quota',hermes,'hermes',dict(cairn=binary,
                socket=config['socket'],token_file=config['agent_token'],repo=repo,harness='hermes',
                binding='probe-worker',process_names=[]),failure_status=429) as fixture:
                native_quota=publish('NATIVE-HERMES-QUOTA-FIXTURE',harness='hermes')
                start(fixture.command)
                wait_for(lambda:status(native_quota)['deliveries'] and status(native_quota)['deliveries'][0]['state']=='failed',120)
                wait_for(lambda:not active())
                stop()
                attempt=next(w for w in call('wake-attempts',{})['attempts'] if w['delivery']['event']['event_id']==native_quota['event_id'])
                assert fixture.observed, 'Hermes did not reach loopback provider'
                assert attempt.get('session'), ('Hermes native session missing',attempt)
                assert attempt['provider_failure']['harness']=='hermes',attempt
                assert attempt['provider_failure']['source']=='native-hook',attempt
                assert attempt['provider_failure']['status']==429,attempt
                slot=next(w for w in call('worker-list',{})['workers'] if w['consumer']=='pool-probe/agent')
                assert slot['health']=='unavailable',slot
                assert status(blocked)['pool']['state']=='queued'
                report.append('installed Hermes native API-error hook retains loopback 429 and suspends its binding')

        print(json.dumps(dict(checks=report), indent=2))
    finally:
        if supervisor:
            supervisor.terminate()
            supervisor.wait(timeout=35)
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=20)
        api.terminate()
        api.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Runtime probe for Cairn agent worker pools.")
    parser.add_argument("binary", help="Path to cairn executable")
    parser.add_argument("directory", type=Path, help="Directory for probe execution and state")
    parser.add_argument("--opencode", help="Optional path to opencode executable")
    parser.add_argument("--hermes", help="Optional path to Hermes executable for native quota probe")
    parser.add_argument("--codex", help="Optional path to Codex executable for native subscription quota probe")
    parser.add_argument("--agy", help="Optional path to Agy executable for isolated native rate-limit probe (requires bwrap)")
    args = parser.parse_args()
    check(args.binary, args.directory, opencode=args.opencode, hermes=args.hermes, codex=args.codex, agy=args.agy)
