"""Exercise real API, supervisor and systemd cgroups on a disposable database.

The --opencode and --hermes fixtures use a synthetic provider and isolated homes.
Explicit --live-binding probes use the selected native account and real model
calls; the database, API identities and requested result remain disposable.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
import uuid


def wait_for(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("wakeup condition timed out")


def process_exited_or_zombie(pid):
    stat = Path(f"/proc/{pid}/stat")
    try:
        return ") Z " in stat.read_text()
    except (FileNotFoundError, ProcessLookupError):
        return True


def check(binary, root, opencode=None, hermes=None, bindings=None):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert os.environ["CAIRN_DATABASE_URL"] == os.environ["CAIRN_TEST_DATABASE_URL"]
    root.mkdir(mode=0o700)
    repo = "wake-probe:" + str(uuid.uuid4())
    identities = []
    for name, role in (("agent", "agent"), ("observer", "observer")):
        token = secrets.token_urlsafe(32)
        path = root / (name + ".token")
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal="wake-probe/" + name, role=role, repo=repo,
                               destination="hosted"))
    (root / "identities.json").write_text(json.dumps(identities))
    (root / "identities.json").chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                           stderr=(root / "api.log").open("w"))
    supervisor = None
    units = set()
    report = []
    config = dict(name="probe", principal="wake-probe/agent", repo=repo, socket=str(root / "api.sock"),
                  agent_token=str(root / "agent.token"), observer_token=str(root / "observer.token"),
                  directory=str(root), state_directory=str(root / "artifacts"), timeout_seconds=60)
    config_path = root / "wake.json"

    def call(op, data, identity="agent"):
        p = subprocess.run([binary, "agent", "--socket", config["socket"], "--token-file",
                            str(root / (identity + ".token")), op], input=json.dumps(data),
                           text=True, capture_output=True, timeout=15)
        assert p.returncode == 0, (op, p.stdout, p.stderr)
        return json.loads(p.stdout)["data"]

    def publish(body, kind="request", group=False):
        note = call("create", dict(request_id=str(uuid.uuid4()), draft=dict(
            kind="note", body=body, scope=dict(repo=repo, task_id="*", run_id="*"),
            claim_type="self", sensitivity="shareable")))
        return call("event-publish", dict(request_id=str(uuid.uuid4()), kind=kind,
                    ref=dict(record_id=note["record_id"], version=1),
                    destination=dict(type="agent", name="wake-probe/agent"),
                    **(dict(response_group=dict(deadline=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat(),partial_policy="all")) if group else {})))

    def status(event):
        return call("event-inspect", dict(event_id=event["event_id"]))["deliveries"][0]

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
assert wake["binding"]=="probe" and wake["inbox"]=="wake-probe/agent"
assert wake["execution_id"]==context_path.name.removesuffix(".context.json")
command=re.search(r"^('.* complete .*) < RESULT_FILE$",prompt,re.M).group(1)
assert shlex.split(command)==wake["completion"]
assert wake["delivery_id"]==wake["completion"][-1]
assert wake["lease_id"]==wake["completion"][wake["completion"].index("--lease")+1]
assert wake["source"]["record_id"] in prompt
assert wake["deadline"] and wake["workspace"]==str(Path.cwd())
if "BLOCK-WORKER" in prompt:
 child=subprocess.Popen(["/usr/bin/sleep","300"],start_new_session=True)
 Path("child.pid").write_text(str(child.pid))
 Path("started").write_text("yes")
 time.sleep(300)
if "EXIT-WITHOUT-ACK" in prompt:
 raise SystemExit(0)
completed=subprocess.run(wake["completion"],input="Selected fixture result",text=True,capture_output=True,check=True)
if wake.get("response_group"):
 result=json.loads(completed.stdout)["data"]["result"]
 response=subprocess.run(wake["response"]+["--version",str(result["version"]),result["record_id"]],text=True,capture_output=True,check=True)
 assert json.loads(response.stdout)["data"]["correlation_id"]==wake["response"][wake["response"].index("--correlation-id")+1]
''')
    command = ["/usr/bin/python3", str(worker)]
    try:
        wait_for(lambda: (root / "api.sock").exists())
        notice = publish("reply must not launch", "response")
        event = publish("COMPLETE-FIXTURE")
        start(command)
        wait_for(lambda: status(event)["state"] == "handled")
        wait_for(lambda: not active())
        assert status(notice)["state"] == "pending"
        done = [w for w in call("wake-attempts", {})["attempts"] if w["delivery"]["event"]["event_id"] == event["event_id"]][0]
        assert done["receipt_id"] and done["delivery"]["result"]
        report.append("request-only launch, explicit completion and runner receipt")
        grouped = publish("COMPLETE-GROUPED-FIXTURE", group=True)
        wait_for(lambda: call("event-group",dict(event_id=grouped["event_id"]))["state"]=="collected")
        wait_for(lambda: not active())
        group=call("event-group",dict(event_id=grouped["event_id"]))
        assert group["responded"]==1 and group["members"][0]["payload_available"]
        assert group["reported_task_outcome"]=="unknown"
        report.append("fresh worker uses slot-owned context reply to collect a group without inferring task acceptance")
        event = publish("EXIT-WITHOUT-ACK")
        wait_for(lambda: status(event)["state"] == "failed")
        report.append("exit zero without handling is failed")
        event = publish("BLOCK-WORKER")
        wait_for(lambda: (root / "started").exists())
        child = int((root / "child.pid").read_text())
        running = active()[0]
        stop(kill=True)
        assert Path(f"/proc/{child}").exists(), "fixture child did not survive supervisor crash"
        start(command)
        wait_for(lambda: status(event)["state"] == "failed")
        wait_for(lambda: process_exited_or_zombie(child))
        assert len([w for w in call("wake-attempts", {})["attempts"] if w["delivery"]["event"]["event_id"] == event["event_id"]]) == 1
        report.append("supervisor SIGKILL, restarted cgroup cleanup, no uncertain replay")
        (root / "started").unlink()
        event = publish("BLOCK-WORKER")
        wait_for(lambda: (root / "started").exists())
        active()
        child = int((root / "child.pid").read_text())
        # Lose the API while a worker is active: renewal must stop the worker,
        # while durable reconciliation waits for the API to return.
        api.terminate()
        api.wait(timeout=15)
        wait_for(lambda: process_exited_or_zombie(child), 40)
        supervisor.wait(timeout=20)
        assert supervisor.returncode != 0
        supervisor = None
        api = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                               stderr=(root / "api-restart.log").open("w"))
        wait_for(lambda: (root / "api.sock").exists())
        start(command)
        wait_for(lambda: status(event)["state"] == "failed")
        report.append("API outage cancels worker on control/renewal failure; restart reconciles hold")
        stop()
        for name, native in (("opencode", opencode), ("hermes", hermes)):
            if not native:
                continue
            with NativeFixture(root / name, native, name, dict(cairn=binary, socket=config['socket'],
                token_file=config['agent_token'], repo=repo, harness=name, binding=name+'-native', process_names=[])) as fixture:
                event = publish("NATIVE-COMPLETE-FIXTURE")
                start(fixture.command)
                wait_for(lambda: status(event)["state"] in ("handled", "failed"), 90)
                assert status(event)["state"] == "handled", (name, fixture.observed, (root / "supervisor.log").read_text())
                wait_for(lambda: not active())
                stop()
                assert fixture.completed_tool, (name, fixture.observed)
                attempt = next(w for w in call('wake-attempts', {})['attempts'] if w['delivery']['event']['event_id'] == event['event_id'])
                assert attempt.get('session'), (name, 'native wake session missing', root)
                native_agent = call('agent-directory', dict(agent_id=attempt['session']['agent_id'], include_offline=True))['agents'][0]
                assert native_agent['metadata']['harness'] == name and native_agent['stopped']
                assert native_agent['native_session_id']
                report.append(name + " native session association, tool execution and atomic result completion")
        for binding in bindings or []:
            name = binding["name"]
            config["timeout_seconds"] = 120
            event = publish("Deployment verification only. Execute the supplied Cairn completion command "
                            "with selected result text 'Native " + name + " wakeup verified'. "
                            "Do not inspect other projects, change workspace files, send messages, "
                            "or use other memory profiles. This is a disposable test database.")
            start(binding["command"])
            wait_for(lambda: status(event)["state"] in ("handled", "failed"), 150)
            assert status(event)["state"] == "handled", (name, status(event), str(root))
            wait_for(lambda: not active())
            stop()
            done = [w for w in call("wake-attempts", {})["attempts"]
                    if w["delivery"]["event"]["event_id"] == event["event_id"]][0]
            assert done["receipt_id"] and done["delivery"]["result"]
            report.append(name + " live native completion against disposable database")
        print(json.dumps(dict(checks=report), indent=2))
    finally:
        if supervisor:
            supervisor.terminate()
            supervisor.wait(timeout=35)
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=20)
        api.terminate()
        api.wait(timeout=15)


class NativeFixture:
    def __init__(self, root, binary, kind, coordination, failure_status=None):
        root.mkdir()
        self.observed = []
        self.completed_tool = False
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(dict(data=[dict(id="fixture", object="model")])).encode())

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                tools = [t["function"]["name"] for t in request.get("tools", [])]
                fixture.observed.append(dict(path=self.path, tools=tools, stream=request.get("stream")))
                if failure_status is not None:
                    body = json.dumps(dict(error=dict(type='rate_limit_error',code='rate_limit_exceeded',message='Synthetic rate limit'))).encode()
                    self.send_response(failure_status)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(body)))
                    self.send_header('Retry-After','0')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                content = "Fixture finished."
                tool_calls = None
                # Only issue the tool once; later model turns receive its result.
                if not fixture.completed_tool and tools:
                    messages = "\n".join(m.get('content', '') if isinstance(m.get('content'), str) else
                        '\n'.join(p.get('text', '') for p in m.get('content', []) if isinstance(p, dict))
                        for m in request.get('messages', []))
                    match = re.search(r"^('.* complete .*) < RESULT_FILE$", messages, re.M)
                    if match:
                        tool = "bash" if kind == "opencode" else "terminal"
                        assert tool in tools, tools
                        command = "printf 'Native fixture selected result' | " + match.group(1)
                        arguments = dict(command=command)
                        if kind == "opencode":
                            arguments["description"] = "Complete the synthetic Cairn request"
                        tool_calls = [dict(id="call_fixture", type="function", function=dict(name=tool, arguments=json.dumps(arguments)))]
                        fixture.completed_tool = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if request.get("stream") else "application/json")
                self.end_headers()
                message = dict(role="assistant", content=content)
                if tool_calls:
                    message["content"] = None
                    message["tool_calls"] = tool_calls
                finish = "tool_calls" if tool_calls else "stop"
                if request.get("stream"):
                    if tool_calls:
                        tool_calls[0]["index"] = 0
                    chunk = dict(id="fixture", object="chat.completion.chunk", created=1, model="fixture",
                                 choices=[dict(index=0, delta=message, finish_reason=None)])
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    chunk["choices"] = [dict(index=0, delta={}, finish_reason=finish)]
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                else:
                    response = dict(id="fixture", object="chat.completion", created=1, model="fixture",
                                    choices=[dict(index=0, message=message, finish_reason=finish)],
                                    usage=dict(prompt_tokens=100, completion_tokens=10, total_tokens=110))
                    self.wfile.write(json.dumps(response).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        endpoint = f"http://127.0.0.1:{self.server.server_port}/v1"
        env = ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(root),
               "XDG_CONFIG_HOME=" + str(root / "config"), "XDG_DATA_HOME=" + str(root / "data"),
               "XDG_CACHE_HOME=" + str(root / "cache"), "XDG_STATE_HOME=" + str(root / "state")]
        if kind == "opencode":
            config = dict(autoupdate=False, share="disabled", enabled_providers=["fixture"],
                          permission={"bash": "allow"}, provider={"fixture": dict(npm="@ai-sdk/openai-compatible",
                          options=dict(baseURL=endpoint, apiKey="synthetic"),
                          models={"fixture": dict(name="Fixture", limit=dict(context=64000, output=1000))})})
            path = root / "opencode.json"
            path.write_text(json.dumps(config))
            self.command = env + ["OPENCODE_CONFIG=" + str(path), "OPENCODE_DISABLE_AUTOUPDATE=true", "OPENCODE_DISABLE_MODELS_FETCH=true",
                                  binary, "run", "--format", "json", "-m", "fixture/fixture"]
        else:
            home = root / "hermes"
            home.mkdir()
            (home / "config.yaml").write_text(json.dumps(dict(model=dict(default="fixture", provider="custom", base_url=endpoint),
                                                            agent=dict(max_turns=5), toolsets=["terminal"])))
            self.command = env + ["HERMES_HOME=" + str(home), "OPENAI_BASE_URL=" + endpoint, "OPENAI_API_KEY=synthetic",
                                  binary, "--ignore-rules", "--provider", "custom", "--model", "fixture", "-z"]
        spec = importlib.util.spec_from_file_location('native_wake_installer', Path(__file__).with_name('install-agent-coordination.py'))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        settings = root / 'config/opencode' if kind == 'opencode' else home
        installer.install(root / 'engine', settings, coordination)
        # Isolate native accounts/provider settings, preserving only the wake
        # context supplied after the supervisor creates its actual attempt.
        clean_env = {}
        argv = list(self.command[2:])
        while argv and '=' in argv[0]:
            key, value = argv.pop(0).split('=', 1)
            clean_env[key] = value
        wrapper = root / 'launch.py'
        wrapper.write_text('import os,sys\n' + 'env=' + repr(clean_env) + '\n' +
            "env['CAIRN_WAKE_CONTEXT']=os.environ['CAIRN_WAKE_CONTEXT']\n" +
            "env['CAIRN_LIFECYCLE_CHILD']='1'\n" + 'argv=' + repr(argv) + " + [sys.argv[-1]]\n" +
            'os.execve(argv[0],argv,env)\n')
        self.command = ['/usr/bin/python3', str(wrapper)]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--opencode")
    parser.add_argument("--hermes")
    parser.add_argument("--live-binding", type=Path, action="append", default=[],
                        help="Explicit opt-in: use an authenticated native launcher from a binding JSON; may incur model usage")
    args = parser.parse_args()
    check(args.binary, args.directory, args.opencode, args.hermes,
          [json.loads(path.read_text()) for path in args.live_binding])
