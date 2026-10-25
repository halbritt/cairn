"""Exercise staged Codex compact notice read/settlement via a local Responses stub."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import threading
import time
import uuid


MODEL = "gpt-6-luna"
SOURCE_BODY = "Codex compact notice exact source. " + ("retain this whole source. " * 260)


def response_stream(ident, item):
    response = dict(id=ident, object="response", created_at=0, status="in_progress",
                    output=[], model=MODEL)
    events = [dict(type="response.created", response=response),
              dict(type="response.output_item.added", output_index=0,
                   item=dict(item, status="in_progress"))]
    if item["type"] == "message":
        part = item["content"][0]
        events += [dict(type="response.content_part.added", item_id=item["id"], output_index=0,
                        content_index=0, part=dict(part, text="")),
                   dict(type="response.output_text.delta", item_id=item["id"], output_index=0,
                        content_index=0, delta=part["text"]),
                   dict(type="response.output_text.done", item_id=item["id"], output_index=0,
                        content_index=0, text=part["text"]),
                   dict(type="response.content_part.done", item_id=item["id"], output_index=0,
                        content_index=0, part=part)]
    elif item["type"] == "custom_tool_call":
        pass  # Match the installed Codex CLI's accepted custom-tool fixture shape.
    events += [dict(type="response.output_item.done", output_index=0, item=item),
               dict(type="response.completed", response=dict(
                   response, status="completed", output=[item],
                   usage=dict(input_tokens=1, output_tokens=1, total_tokens=2)))]
    return "".join("event: " + event["type"] + "\ndata: " +
                   json.dumps(dict(event, sequence_number=i)) + "\n\n"
                   for i, event in enumerate(events)).encode()


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exec_tool(call_id, command):
    code = "text(await tools.exec_command(" + json.dumps(dict(
        cmd=command, max_output_tokens=3000)) + "));"
    return dict(type="custom_tool_call", id="ctc_" + call_id, name="exec",
                namespace="functions", call_id=call_id, input=code)


def check(binary, root, config, api_call, candidate_root, scenario="notice"):
    if scenario not in ("notice", "response"):
        raise ValueError("unsupported compact fixture scenario")
    candidate_root = Path(candidate_root).resolve()
    source_home = candidate_root / "codex"
    home = root / "home"
    root.mkdir(mode=0o700)
    shutil.copytree(source_home, home)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py",
                    home / "coordination/coordination.py")

    # All candidate bindings point into this private copy and disposable API.
    for rel in ("ordinary/config.json", "coordination/bindings/codex-one.json"):
        path = home / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(cairn=str(candidate_root / "bin/cairn"), socket=str(root.parent / "api.sock"),
                    token_file=str(root.parent / "carol.token"), repo=config["repo"],
                    config_home=str(home))
        data["state_dir"] = str(home / ("ordinary/state" if rel.startswith("ordinary/")
                                         else "coordination/state/codex-one"))
        data["inbox_recall_binding"] = str(home / "binding.json")
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    settings_path = home / "hooks.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    def replace_paths(value):
        if isinstance(value, str):
            return value.replace(str(source_home), str(home))
        if isinstance(value, list):
            return [replace_paths(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_paths(item) for key, item in value.items()}
        return value
    settings = replace_paths(settings)
    metadata_path = root / "hook-metadata.jsonl"
    stop_complete_path = root / "registration-stop-complete.json"
    metadata_wrapper = Path(__file__).with_name("record_codex_hook_metadata.py").resolve()
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                original = hook.get("command", "")
                if "coordination.py" in original and "--config" in original:
                    hook["command"] = shlex.join([sys.executable, str(metadata_wrapper),
                        "--metadata", str(metadata_path), "--stop-complete",
                        str(stop_complete_path), "--", *shlex.split(original)])
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    settings_path.chmod(0o600)

    bridge_path = home / "inbox_recall.py"
    memory_path = home / "ordinary/memory.py"
    coordination_path = home / "coordination/bindings/codex-one.json"
    manifest_path = home / "binding.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(enabled=True,
        bridge=dict(path=str(bridge_path), sha256=hashlib.sha256(bridge_path.read_bytes()).hexdigest()),
        engine=dict(path=str(memory_path), sha256=hashlib.sha256(memory_path.read_bytes()).hexdigest()),
        memory_config=dict(path=str(home / "ordinary/config.json"),
                           sha256=hashlib.sha256((home / "ordinary/config.json").read_bytes()).hexdigest()),
        coordination_configs=[dict(path=str(coordination_path),
                                   sha256=hashlib.sha256(coordination_path.read_bytes()).hexdigest())])
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)

    bridge = _module(bridge_path, "staged_codex_compact_bridge")
    memory = json.loads((home / "ordinary/config.json").read_text(encoding="utf-8"))
    coordination = json.loads(coordination_path.read_text(encoding="utf-8"))
    bridge.validate_binding(memory, require_enabled=True)
    bridge.validate_binding(coordination, require_enabled=True)

    installer = _module(Path(__file__).with_name("install-agent-coordination.py"),
                        "codex_compact_installer")
    settings = home / "hooks.json"
    command = next(hook["command"] for groups in json.loads(settings.read_text())["hooks"].values()
                   for group in groups for hook in group["hooks"]
                   if "coordination.py" in hook.get("command", "") and "--config" in hook.get("command", ""))
    installed = home / "coordination/bindings/codex-one.json"
    binding = json.loads(installed.read_text(encoding="utf-8"))
    binding.update(cairn=str(candidate_root / "bin/cairn"), socket=str(root.parent / "api.sock"),
                   token_file=str(root.parent / "carol.token"), repo=config["repo"],
                   state_dir=str(home / "coordination/state/codex-one"), config_home=str(home))
    installed.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    installed.chmod(0o600)

    causal_request = None
    if scenario == "notice":
        source = api_call("alice", "create", raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            draft=dict(kind="note", body=SOURCE_BODY, sensitivity="shareable", claim_type="self",
                       scope=dict(repo=config["repo"], task_id="*", run_id="*")))))
    else:
        request_source = api_call("alice", "create", raw=True, body=json.dumps(dict(
            request_id=str(uuid.uuid4()), draft=dict(kind="note", body="Native Codex causal task request",
            sensitivity="shareable", claim_type="self", scope=dict(repo=config["repo"],
            task_id="*", run_id="*")))))
        causal_request = api_call("alice", "publish", "--request-id", str(uuid.uuid4()),
            "--to", "agent/bob", "--kind", "request", "--version", str(request_source["version"]),
            request_source["record_id"])
        request_delivery = api_call("bob", "inbox")["delivery"]
        assert request_delivery and request_delivery["event"]["event_id"] == causal_request["event_id"]
        completion = api_call("bob", "complete", "--request-id", str(uuid.uuid4()),
            "--lease", request_delivery["lease_id"], "--shareable", "--stdin",
            request_delivery["delivery_id"], body=SOURCE_BODY)
        assert completion["state"] == "handled" and completion["result"]["version"] == 1
        source = completion["result"]
    native_id = None
    registration_turn_id = None
    native_turn_id = None
    event = None
    delivery_id = None
    attempt_id = None
    requests, failures, results = [], [], {}
    finished = threading.Event()
    event_owner = "alice" if scenario == "notice" else "bob"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal event, delivery_id, attempt_id
            try:
                size = int(self.headers.get("Content-Length", "0"))
                assert self.path == "/v1/responses" and 0 < size <= 4_000_000, "unexpected request path or size"
                assert len(requests) < 5, "Codex compact loopback request budget exceeded"
                request = json.loads(self.rfile.read(size))
                requests.append(request)
                results.update({item["call_id"]: item for item in request.get("input", [])
                                if item.get("type") == "custom_tool_call_output"})

                if event is None:
                    item = dict(type="message", id="msg_initial", role="assistant", status="completed",
                        content=[dict(type="output_text", text="Local fixture registration turn completed.", annotations=[])])
                else:
                    context_paths = list((home / "coordination/state/codex-one/inbox").glob("*.json"))
                    context_path = next(path for path in context_paths
                        if json.loads(path.read_text()).get("event_id") == event["event_id"])
                    context = json.loads(context_path.read_text())
                    attempt = context["attempt_id"]
                    attempt_id = attempt
                    assert context.get("native_turn_id") == native_turn_id, \
                        "compact claim did not retain the exact app-server turn ID"
                    assert context["event_id"] == event["event_id"], "compact context changed event identity"
                    assert context["delivery_id"] == delivery_id, "compact context changed delivery identity"
                    assert context["source"] == event["ref"], "compact context changed source reference"
                    assert event["ref"]["record_id"] == source["record_id"]
                    assert event["ref"]["version"] == source["version"]
                    assert event["kind"] == scenario, "compact context changed notice/response kind"
                    if scenario == "response":
                        assert event["causation_id"] == causal_request["event_id"]
                    claimed = api_call("carol", "session-inbox-claim", raw=True,
                        body=json.dumps(dict(request_id=attempt, session=dict(
                            agent_id=context["agent_id"], execution_id=context["execution_id"]),
                            native_turn_id=native_turn_id)))["attempt"]
                    assert claimed["attempt_id"] == attempt, "session claim changed attempt identity"
                    assert claimed.get("native_turn_id") == native_turn_id, \
                        "Store claim did not retain the actual compact turn ID"
                    assert claimed["session"] == dict(agent_id=context["agent_id"],
                        execution_id=context["execution_id"]), "session claim changed session identity"
                    assert claimed["delivery"]["delivery_id"] == delivery_id, "session claim changed delivery identity"
                    serialized = json.dumps(request.get("input"))
                    handle = context.get("compact_handle")
                    assert isinstance(handle, str) and handle in serialized, "compact handle absent from native request"
                    if "compact-read" not in results:
                        assert SOURCE_BODY not in serialized, "full source was inlined before explicit read"
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "read", handle])
                        item = _exec_tool("compact-read", command)
                    elif "compact-settle" not in results:
                        read_result = results["compact-read"]
                        output = json.dumps(read_result)
                        assert SOURCE_BODY in output and source["record_id"] in output, \
                            "compact read omitted exact source"
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "settle", handle, "handled"])
                        item = _exec_tool("compact-settle", command)
                    else:
                        settled = json.dumps(results["compact-settle"])
                        assert "handled" in settled and "attempts" in settled, "compact settlement did not confirm handled"
                        current = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                        assert current["state"] == "handled" and current["attempts"] == 1, "Store did not confirm one handled attempt"
                        if scenario == "response":
                            assert not current.get("result"), "response settlement falsely created a task result"
                            visible_events = api_call("bob", "events", "--limit", "100")
                            assert not visible_events.get("more")
                            assert not any(row.get("causation_id") == event["event_id"]
                                for row in visible_events["events"]), "settling response created a reply-to-response loop"
                        item = dict(type="message", id="msg_done", role="assistant", status="completed",
                            content=[dict(type="output_text", text="Compact notice handled.", annotations=[])])
                        finished.set()
                body = response_stream("resp_" + str(len(requests)), item)
            except (AssertionError, KeyError, StopIteration, ValueError, TypeError) as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    (home / "config.toml").write_text(
        'model = "gpt-6-luna"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "Loopback fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n', encoding="utf-8")
    (home / "config.toml").chmod(0o600)
    def verify(rpc):
        nonlocal native_id, registration_turn_id, native_turn_id, event, delivery_id
        started = rpc("thread/start", dict(cwd=str(root), model=MODEL,
            approvalPolicy="never", sandbox="danger-full-access", ephemeral=True))
        native_id = started["thread"]["id"]
        registration_turn = rpc("turn/start", dict(threadId=native_id,
            input=[dict(type="text", text="Register the native fixture session.", text_elements=[])]))
        registration_turn_id = registration_turn["turn"]["id"]
        registration_deadline = time.monotonic() + 30
        target = None
        while time.monotonic() < registration_deadline and not stop_complete_path.exists():
            agents = api_call("carol", "agents", "list", "--harness", "codex")["agents"]
            target = next((row for row in agents if row["native_session_id"] == native_id), None)
            time.sleep(.05)
        assert stop_complete_path.exists(), "Codex registration Stop hook did not complete"
        stop_metadata = json.loads(stop_complete_path.read_text(encoding="utf-8"))
        assert stop_metadata["event"] == "Stop" and stop_metadata["turn_id"] == registration_turn_id, \
            "completed Stop hook did not identify the registration turn"
        assert stop_metadata.get("exit_code") == 0, \
            "Codex registration Stop hook returned nonzero: " + json.dumps({
                key: stop_metadata.get(key) for key in ("exit_code", "output_status", "output_code", "stderr_bytes", "stderr_code")})
        agents = api_call("carol", "agents", "list", "--harness", "codex")["agents"]
        target = next((row for row in agents if row["native_session_id"] == native_id), None)
        assert target is not None, "Codex native session was not registered"
        if scenario == "notice":
            event = api_call("alice", "publish", "--request-id", str(uuid.uuid4()),
                "--to", target["inbox"], "--kind", "notice", "--version",
                str(source["version"]), source["record_id"])
        else:
            event = api_call("bob", "publish", "--request-id", str(uuid.uuid4()),
                "--to", target["inbox"], "--kind", "response", "--version",
                str(source["version"]), "--causation-id", causal_request["event_id"],
                source["record_id"])
            assert event["causation_id"] == causal_request["event_id"]
            assert event["ref"] == source
        delivery_id = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]["delivery_id"]
        turn = rpc("turn/start", dict(threadId=native_id,
            input=[dict(type="text", text="Reply OK to begin local fixture.", text_elements=[])]))
        native_turn_id = turn["turn"]["id"]
        deadline = time.monotonic() + 60
        while not finished.wait(.1):
            assert not failures, failures
            assert time.monotonic() < deadline, "Codex compact loopback did not read and settle"
        terminal_deadline = time.monotonic() + 30
        compact_stop = {}
        while time.monotonic() < terminal_deadline:
            if stop_complete_path.exists():
                compact_stop = json.loads(stop_complete_path.read_text(encoding="utf-8"))
                if compact_stop.get("turn_id") == native_turn_id:
                    break
            time.sleep(.05)
        assert compact_stop.get("event") == "Stop" and compact_stop.get("turn_id") == native_turn_id, \
            "Codex compact turn did not reach its observed native Stop boundary"
        assert compact_stop.get("exit_code") == 0, \
            "Codex compact Stop hook returned nonzero: " + json.dumps({
                key: compact_stop.get(key) for key in ("exit_code", "output_status", "output_code", "stderr_bytes", "stderr_code")})

    thread.start()
    previous_inbox_root = os.environ.get("CAIRN_INBOX_ROOT")
    os.environ["CAIRN_INBOX_ROOT"] = str(home / "coordination")
    try:
        installer.trust_codex_hooks(home, settings, command, verify=verify, codex_binary=binary)
        assert not failures, failures
        assert event is not None and len(requests) == 4
        hook_metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line]
        assert any(row.get("event") == "UserPromptSubmit" and row.get("turn_id") == native_turn_id
                   for row in hook_metadata), "hook metadata did not observe the exact compact turn"
        scripted_response_ids = ["resp_" + str(i) for i in range(1, len(requests) + 1)]
        print(json.dumps(dict(result="PASS", native_session_registered=True, scenario=scenario,
            kind=event["kind"], causal_request_id=causal_request["event_id"] if causal_request else None,
            event_id=event["event_id"], delivery_id=delivery_id,
            attempt_id=attempt_id, native_session_id=native_id,
            registration_turn_id=registration_turn_id,
            native_turn_id=native_turn_id, native_turn_joined=True,
            registration_turn_terminal_observed=True,
            compact_turn_terminal_observed=True,
            exact_source_bytes=len(SOURCE_BODY.encode()), exact_source_read=True,
            explicit_handled_settlement=True, attempt_count=1, model=MODEL,
            request_count=len(requests), scripted_response_ids=scripted_response_ids,
            model_decision="scripted fixture sequence",
            compact_turn_hook_observed=True,
            provider_response_ids_are_scripted=True,
            provider_usage_observed=False, provider="loopback_fixture", provider_call=False), sort_keys=True))
    finally:
        if previous_inbox_root is None:
            os.environ.pop("CAIRN_INBOX_ROOT", None)
        else:
            os.environ["CAIRN_INBOX_ROOT"] = previous_inbox_root
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
