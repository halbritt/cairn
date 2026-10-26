"""Installed Codex Stop continuation with a local Responses protocol fixture."""
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
MODEL = "gpt-6-astra"
try:
    from codex_loopback_fixture import (
        LoopbackResponsesServer,
        assert_exec_tool_declared,
        exec_tool_item,
        load_coordination_installer,
        message_item,
        response_stream as _shared_response_stream,
    )
except ModuleNotFoundError:  # Isolated /probe bwrap copy in socket-namespace probe.
    _shared_response_stream = None


def response_stream(ident, item, *, model=MODEL, input_tokens=1, output_tokens=1):
    if _shared_response_stream is not None:
        return _shared_response_stream(
            ident, item, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
    response = dict(id=ident, object="response", created_at=0, status="in_progress", output=[], model=model)
    events = [
        dict(type="response.created", response=response),
        dict(type="response.output_item.added", output_index=0, item=dict(item, status="in_progress")),
    ]
    if item["type"] == "message":
        part = item["content"][0]
        events += [
            dict(type="response.content_part.added", item_id=item["id"], output_index=0, content_index=0, part=dict(part, text="")),
            dict(type="response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=part["text"]),
            dict(type="response.output_text.done", item_id=item["id"], output_index=0, content_index=0, text=part["text"]),
            dict(type="response.content_part.done", item_id=item["id"], output_index=0, content_index=0, part=part),
        ]
    events += [
        dict(type="response.output_item.done", output_index=0, item=item),
        dict(type="response.completed", response=dict(response, status="completed", output=[item], usage=dict(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens))),
    ]
    return "".join("event: " + event["type"] + "\ndata: " + json.dumps(dict(event, sequence_number=i)) + "\n\n" for i, event in enumerate(events)).encode()


def check(binary, root, config, api_call):
    installer = load_coordination_installer("codex_inbox_installer")
    root.mkdir(mode=0o700)
    home = root / "home"
    home.mkdir()
    settings = home / "hooks.json"
    config = dict(
        config,
        harness="codex",
        binding="codex-inbox",
        process_names=["codex"],
        config_home=str(home),
        native_delivery=True,
    )
    installed = installer.install(root / "engine", settings, config)
    native_id = None
    source = api_call(
        "alice",
        "create",
        raw=True,
        body=json.dumps(
            dict(
                request_id=str(uuid.uuid4()),
                draft=dict(
                    kind="note",
                    body="Native Codex selected request source",
                    sensitivity="shareable",
                    claim_type="self",
                    scope=dict(repo=config["repo"], task_id="*", run_id="*"),
                ),
            )
        ),
    )
    message = None
    completed = threading.Event()
    finished_turn = False

    def handle_response(request, results):
        nonlocal message, finished_turn
        finished_turn = False
        if message is None:
            entries = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
            target = next(a for a in entries if a["native_session_id"] == native_id)
            assert target["metadata"]["state"] == "busy"
            message = api_call(
                "alice",
                "publish",
                "--request-id",
                str(uuid.uuid4()),
                "--to",
                target["inbox"],
                "--kind",
                "request",
                "--version",
                "1",
                source["record_id"],
            )
            return message_item("msg_" + str(len(server.requests)), "Initial user task finished.")
        paths = list((root / "engine/state/codex-inbox/inbox").glob("*.json"))
        path = next(p for p in paths if json.loads(p.read_text())["event_id"] == message["event_id"])
        context = json.loads(path.read_text())
        assert str(path) in json.dumps(request["input"]), "Stop context did not reach native provider"
        if "source" not in results:
            command = (
                "printf %s "
                + shlex.quote(json.dumps(context["read_input"]))
                + " | "
                + shlex.join(context["read"])
            )
            call_id = "source"
        elif "completion" not in results:
            assert "Native Codex selected request source" in json.dumps(results["source"])
            command = (
                "printf %s 'Native Codex handled selected request' | "
                + shlex.join(context["completion"])
            )
            call_id = "completion"
        else:
            assert "handled" in json.dumps(results["completion"])
            finished_turn = True
            return message_item("msg_" + str(len(server.requests)), "Native inbox request handled.")
        assert_exec_tool_declared(request)
        return exec_tool_item(call_id, command, max_output_tokens=2000)

    server = LoopbackResponsesServer(
        handle_response,
        model=MODEL,
        max_requests=8,
        max_body_bytes=2 * 1024 * 1024,
        enforce_bounds_via_http_status=True,
        check_size_before_path=True,
        request_timeout=10,
        caught_exceptions=(AssertionError, ValueError, KeyError, StopIteration),
        failure_format="repr",
        after_response=lambda _item: completed.set() if finished_turn else None,
    )
    server.write_config_toml(home, include_model=False, provider_name="Fixture")
    command = shlex.join(
        [os.sys.executable, str(root / "engine/coordination.py"), "hook", "--config", str(installed)]
    )

    def verify(rpc):
        nonlocal native_id
        native_id = rpc(
            "thread/start",
            dict(
                cwd=str(root),
                model=MODEL,
                approvalPolicy="never",
                sandbox="danger-full-access",
                ephemeral=True,
            ),
        )["thread"]["id"]
        rpc(
            "turn/start",
            dict(threadId=native_id, input=[dict(type="text", text="Reply INITIAL_DONE.", text_elements=[])]),
        )
        deadline = time.monotonic() + 45
        while not completed.wait(0.1):
            assert not server.failures, server.failures
            assert time.monotonic() < deadline, "Native Codex Stop continuation did not complete"
        assert api_call("alice", "event-status", message["event_id"])["deliveries"][0]["state"] == "handled"
        context = next(json.loads(p.read_text()) for p in (root / "engine/state/codex-inbox/inbox").glob("*.json"))
        while True:
            attempt = api_call(
                "bob",
                "session-inbox-claim",
                raw=True,
                body=json.dumps(
                    dict(
                        request_id=context["attempt_id"],
                        session={k: context[k] for k in ("agent_id", "execution_id")},
                    )
                ),
            )["attempt"]
            if attempt.get("finished_at"):
                break
            assert time.monotonic() < deadline, "Native Codex final Stop did not reconcile completed handling"
            time.sleep(0.1)

    server.start()
    try:
        installer.trust_codex_hooks(home, settings, command, verify=verify, codex_binary=binary)
        assert not server.failures, server.failures
        print("Installed Codex receives busy-session mail at Stop, reads source and completes through native exec_command")
    finally:
        installer.engine.watch_once(installer.engine.load_config(installed))
        server.close(timeout=5)
        (root / "failures.json").write_text(json.dumps(server.failures))
        (root / "requests.json").write_text(json.dumps(server.requests))
