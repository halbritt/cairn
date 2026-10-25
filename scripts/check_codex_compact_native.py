"""Exercise staged Codex compact notice read/settlement via a local Responses stub."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
import queue
from pathlib import Path
import shlex
import shutil
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_recovery_fixture_report import compile_report as compile_recovery_report


MODEL = "gpt-6-luna"
SOURCE_BODY = "Codex compact notice exact source. " + ("retain this whole source. " * 260)
SELECTOR = r'''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps(dict(type='result', is_error=False,
    structured_output=dict(checkpoint=None, workstream=None, memories=[]))))
'''


def response_stream(ident, item, *, input_tokens=1):
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
                   usage=dict(input_tokens=input_tokens, output_tokens=1,
                              total_tokens=input_tokens + 1)))]
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


def _context_for_event(inbox_dir, event_id, stage, diagnostic_path=None):
    """Select one fixture context and optionally retain bounded lookup metadata."""
    if stage not in {"batch_context", "auto_context", "notice_context"}:
        raise ValueError("invalid fixture context lookup stage")
    inbox_dir = Path(inbox_dir)
    paths = list(inbox_dir.glob("*.json"))
    matches = []
    for path in paths:
        try:
            if json.loads(path.read_text()).get("event_id") == event_id:
                matches.append(path)
        except (OSError, ValueError, TypeError):
            continue
    result = "matched" if len(matches) == 1 else "missing" if not matches else "ambiguous"
    if diagnostic_path is not None:
        payload = json.dumps(dict(schema="cairn.codex-fixture-context-lookup/1", stage=stage,
            context_files=min(len(paths), 1000), matching_contexts=min(len(matches), 1000),
            result=result), sort_keys=True).encode() + b"\n"
        target = Path(diagnostic_path)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = target.parent.stat()
        if (target.parent.is_symlink() or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
            raise PermissionError("fixture diagnostic directory must be owner-only")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    if len(matches) != 1:
        raise LookupError("fixture_context_" + result + ":" + stage)
    return matches[0]


def check(binary, root, config, api_call, candidate_root, scenario="notice", diagnostic_path=None):
    if scenario not in ("notice", "response", "batch-admission", "claimed-before-compact",
                        "auto-in-turn-claimed"):
        raise ValueError("unsupported compact fixture scenario")
    candidate_root = Path(candidate_root).resolve()
    source_home = candidate_root / "codex"
    home = root / "home"
    root.mkdir(mode=0o700)
    shutil.copytree(source_home, home)
    selector_path = root / "local-selector"
    selector_path.write_text(SELECTOR, encoding="utf-8")
    selector_path.chmod(0o700)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py",
                    home / "coordination/coordination.py")

    # All candidate bindings point into this private copy and disposable API.
    for rel in ("ordinary/config.json", "coordination/bindings/codex-one.json"):
        path = home / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(cairn=str(candidate_root / "bin/cairn"), socket=str(root.parent / "api.sock"),
                    token_file=str(root.parent / "carol.token"), repo=config["repo"],
                    config_home=str(home))
        if rel.startswith("ordinary/"):
            data["claude"] = str(selector_path)
        elif scenario == "batch-admission":
            data["notice_batch_version"] = 1
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
                if ("coordination.py" in original or "memory.py" in original) and "--config" in original:
                    hook_kind = "coordination" if "coordination.py" in original else "memory"
                    hook["command"] = shlex.join([sys.executable, str(metadata_wrapper),
                        "--metadata", str(metadata_path), "--stop-complete",
                        str(stop_complete_path), "--hook-kind", hook_kind,
                        "--", *shlex.split(original)])
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
    if scenario == "batch-admission":
        assert coordination.get("compact_inbox_version") == 1
        assert coordination.get("notice_batch_version") == 1

    installer = _module(Path(__file__).with_name("install-agent-coordination.py"),
                        "codex_compact_installer")
    settings = home / "hooks.json"
    hook_commands = {kind: next(hook["command"] for groups in json.loads(settings.read_text())["hooks"].values()
                   for group in groups for hook in group["hooks"]
                   if kind + ".py" in hook.get("command", "") and "--config" in hook.get("command", ""))
                     for kind in ("coordination", "memory")}
    installed = home / "coordination/bindings/codex-one.json"
    binding = json.loads(installed.read_text(encoding="utf-8"))
    binding.update(cairn=str(candidate_root / "bin/cairn"), socket=str(root.parent / "api.sock"),
                   token_file=str(root.parent / "carol.token"), repo=config["repo"],
                   state_dir=str(home / "coordination/state/codex-one"), config_home=str(home))
    installed.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    installed.chmod(0o600)

    causal_request = None
    batch_sources = []
    if scenario == "batch-admission":
        for body in ("Log rotation finished.", "Cache warmed.", "Index refreshed."):
            batch_sources.append(api_call("alice", "create", raw=True, body=json.dumps(dict(
                request_id=str(uuid.uuid4()), draft=dict(kind="note", body=body,
                    sensitivity="shareable", claim_type="self",
                    scope=dict(repo=config["repo"], task_id="*", run_id="*"))))))
        source = batch_sources[0]
    elif scenario in ("notice", "claimed-before-compact", "auto-in-turn-claimed"):
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
    compact_turn_id = None
    continuation_turn_id = None
    native_turn_id = None
    attempt_native_turn_id = None
    event = None
    claimed_before_compact = None
    batch_events = []
    admission_checked = threading.Event()
    admission_requests = 0
    admission_states = None
    delivery_id = None
    attempt_id = None
    requests, failures, results = [], [], {}
    failed_completion = None
    finished = threading.Event()
    native_turn_ready = threading.Event()
    compact_in_progress = False
    source_read_confirmed = False
    settlement_confirmed = False
    event_owner = "alice" if scenario in ("notice", "batch-admission", "claimed-before-compact",
                                           "auto-in-turn-claimed") else "bob"
    precompact_read_turn_id = None
    precompact_read_completed = threading.Event()
    auto_read_tool_sent = False
    auto_attempt_id = None
    automatic_compaction_observed = False
    automatic_compaction_source_order = []
    automatic_compaction_store_state = None
    automatic_compaction_after_sessionstart_state = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal event, delivery_id, attempt_id, failed_completion, attempt_native_turn_id
            nonlocal source_read_confirmed, settlement_confirmed
            nonlocal admission_requests
            nonlocal admission_states
            nonlocal auto_read_tool_sent
            nonlocal automatic_compaction_observed, automatic_compaction_store_state
            nonlocal automatic_compaction_after_sessionstart_state
            nonlocal auto_attempt_id
            try:
                size = int(self.headers.get("Content-Length", "0"))
                assert self.path == "/v1/responses" and 0 < size <= 4_000_000, "unexpected request path or size"
                assert len(requests) < 8, "Codex compact loopback request budget exceeded"
                request = json.loads(self.rfile.read(size))
                requests.append(request)
                results.update({item["call_id"]: item for item in request.get("input", [])
                                if item.get("type") == "custom_tool_call_output"})

                if scenario == "batch-admission" and event is not None:
                    assert native_turn_ready.wait(15), "native batch admission turn ID was not published to fixture"
                    first = batch_events[0]
                    context_path = _context_for_event(home / "coordination/state/codex-one/inbox",
                        first["event_id"], "batch_context", diagnostic_path)
                    context = json.loads(context_path.read_text())
                    attempt = context["attempt_id"]
                    attempt_id = attempt
                    attempt_native_turn_id = context.get("native_turn_id")
                    assert attempt_native_turn_id == native_turn_id, "batch admission lost native-turn binding"
                    hook_rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
                    assert any(row.get("event") == "UserPromptSubmit" and row.get("turn_id") == native_turn_id
                               for row in hook_rows), "admission was not joined to the pinned UserPromptSubmit turn"
                    assert context.get("batch_advance_request_id"), "batch-enabled binding did not journal advance identity"
                    assert context["source"] == first["ref"] and context["delivery_id"] == \
                        api_call("alice", "event-status", first["event_id"])["deliveries"][0]["delivery_id"]
                    claimed = api_call("carol", "session-inbox-claim", raw=True, body=json.dumps(dict(
                        request_id=attempt, session=dict(agent_id=context["agent_id"],
                            execution_id=context["execution_id"]), native_turn_id=native_turn_id)))
                    assert claimed["attempt"]["attempt_id"] == attempt
                    assert claimed["attempt"]["native_turn_id"] == native_turn_id
                    assert claimed["attempt"]["delivery"]["delivery_id"] == context["delivery_id"]
                    handle = context.get("compact_handle")
                    serialized = json.dumps(request.get("input"))
                    assert isinstance(handle, str) and handle in serialized, "first batch handle absent from native request"
                    assert batch_sources[0]["body"] in serialized, "first batch source is absent from native request"
                    assert "cairn-inbox advance " + handle in serialized and "up to three total" in serialized, \
                        "native request did not load the bounded batch continuation cue"
                    states = [api_call("alice", "event-status", item["event_id"])["deliveries"][0]
                              for item in batch_events]
                    assert states[0]["state"] == "leased" and states[0]["attempts"] == 1, \
                        "first routine notice was not admitted exactly once"
                    assert all(row["state"] == "pending" and row["attempts"] == 0 for row in states[1:]), \
                        "a batch neighbor was claimed before explicit actor advance"
                    assert not any(item.get("type") == "custom_tool_call" for item in request.get("input", [])), \
                        "fixture actor input already contains a tool action"
                    admission_requests += 1
                    if admission_states is None:
                        admission_states = states
                    admission_checked.set()
                    self.send_error(503, "fixture stopped before inference")
                    return

                if event is None or compact_in_progress:
                    item = dict(type="message", id="msg_initial", role="assistant", status="completed",
                        content=[dict(type="output_text", text="Local fixture registration turn completed.", annotations=[])])
                elif scenario == "auto-in-turn-claimed":
                    assert native_turn_ready.wait(15), "automatic-compaction turn ID was not published"
                    context_path = _context_for_event(home / "coordination/state/codex-one/inbox",
                        event["event_id"], "auto_context", diagnostic_path)
                    context = json.loads(context_path.read_text())
                    attempt_id = context["attempt_id"]
                    attempt_native_turn_id = context.get("native_turn_id")
                    delivery_id = context["delivery_id"]
                    assert context.get("source") == event["ref"], \
                        "automatic-compaction context changed the exact source ref"
                    if auto_attempt_id is None:
                        auto_attempt_id = attempt_id
                    assert attempt_id == auto_attempt_id, \
                        "automatic compaction changed the admitted Store attempt identity"
                    assert attempt_native_turn_id == native_turn_id, "automatic claim lost native turn binding"
                    claimed = api_call("carol", "session-inbox-claim", raw=True, body=json.dumps(dict(
                        request_id=attempt_id, session=dict(agent_id=context["agent_id"],
                            execution_id=context["execution_id"]), native_turn_id=native_turn_id)))
                    store_attempt = claimed["attempt"]
                    assert store_attempt["attempt_id"] == attempt_id and \
                        store_attempt["native_turn_id"] == native_turn_id and \
                        store_attempt["delivery"]["delivery_id"] == delivery_id, \
                        "Store claim no longer identifies the same automatic-compaction attempt"
                    state = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                    assert state["delivery_id"] == delivery_id and state["state"] == "leased" \
                        and state["attempts"] == 1, "automatic path did not start with one live claim"
                    for call_id, output in results.items():
                        if call_id.startswith("auto-read"):
                            serialized_output = json.dumps(output)
                            assert SOURCE_BODY in serialized_output and source["record_id"] in serialized_output, \
                                "automatic path did not read the exact Store source"
                            source_read_confirmed = True
                            if "exact_source_read" not in automatic_compaction_source_order:
                                automatic_compaction_source_order.append("exact_source_read")
                    hook_rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
                    compact_session_exits = [row for row in hook_rows if row.get("event") == "SessionStart" and
                        row.get("source") == "compact" and row.get("phase") == "exit"]
                    if compact_session_exits:
                        assert {row.get("hook_kind") for row in compact_session_exits} >= \
                            {"coordination", "memory"}, "compact SessionStart did not run both hook families"
                        compact_outcomes = {row.get("hook_kind"): row for row in compact_session_exits}
                        assert compact_outcomes["memory"].get("exit_code") == 0, \
                            "automatic compact memory refresh failed: " + json.dumps(compact_outcomes["memory"])
                        assert compact_outcomes["coordination"].get("exit_code") == 1 and \
                            compact_outcomes["coordination"].get("stderr_code") == "NATIVE_TURN_MISMATCH", \
                            "automatic compact coordination boundary changed unexpectedly: " + \
                            json.dumps({key: row.get(key) for key, row in compact_outcomes.items()})
                    compact_rows = [row for row in hook_rows if row.get("event") == "PreCompact" and
                                    row.get("phase") == "exit" and row.get("exit_code") == 0]
                    compact_resume = compact_session_exits
                    if compact_rows and not automatic_compaction_observed:
                        automatic_compaction_observed = True
                        automatic_compaction_source_order.append("PreCompact")
                        automatic_compaction_store_state = state
                        assert state["state"] == "leased" and state["attempts"] == 1, \
                            "PreCompact changed the claimed delivery before settlement: " + \
                            json.dumps({key: state.get(key) for key in
                                ("delivery_id", "state", "attempts", "code")})
                    if compact_resume and "SessionStart(compact)" not in automatic_compaction_source_order:
                        automatic_compaction_source_order.append("SessionStart(compact)")
                    if automatic_compaction_observed and compact_resume and source_read_confirmed:
                        automatic_compaction_after_sessionstart_state = state
                        assert state["state"] == "leased" and state["attempts"] == 1, \
                            "compact SessionStart changed the claimed delivery before settlement: " + \
                            json.dumps({key: state.get(key) for key in
                                ("delivery_id", "state", "attempts", "code")})
                        item = dict(type="message", id="msg_auto_compact_done", role="assistant",
                            status="completed", content=[dict(type="output_text",
                                text="The exact notice source was read across automatic compaction.", annotations=[])])
                        finished.set()
                    elif automatic_compaction_observed and compact_rows and not compact_resume:
                        # A model response to this request supplies the native
                        # compact summary. Keep the same turn open for its
                        # compact SessionStart continuation.
                        item = dict(type="message", id="msg_auto_compact_summary", role="assistant",
                            status="completed", content=[dict(type="output_text",
                                text="The compacted state preserves the active claimed notice.", annotations=[])])
                    elif automatic_compaction_observed and not source_read_confirmed:
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "read", context["compact_handle"]])
                        item = _exec_tool("auto-read-after-compact", command)
                        auto_read_tool_sent = True
                    elif not auto_read_tool_sent:
                        serialized = json.dumps(request.get("input"))
                        assert context["compact_handle"] in serialized
                        assert SOURCE_BODY not in serialized, "exact source was inlined before explicit read"
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "read", context["compact_handle"]])
                        item = _exec_tool("auto-read", command)
                        auto_read_tool_sent = True
                    else:
                        item = _exec_tool("auto-followup", "true")
                elif scenario == "claimed-before-compact" and compact_turn_id is not None:
                    current = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                    assert current["state"] == "failed" and current["attempts"] == 1 and \
                        current.get("code") == "processing_failed", \
                        "compaction continuation unexpectedly replayed or changed the failed delivery"
                    item = dict(type="message", id="msg_operator_recovery", role="assistant", status="completed",
                        content=[dict(type="output_text", text=(
                            "The prior attempt ended without settlement. Review prior effects, then explicitly reissue if needed."),
                            annotations=[])])
                    finished.set()
                else:
                    assert native_turn_ready.wait(15), "native continuation turn ID was not published to fixture"
                    context_path = _context_for_event(home / "coordination/state/codex-one/inbox",
                        event["event_id"], "notice_context", diagnostic_path)
                    context = json.loads(context_path.read_text())
                    attempt = context["attempt_id"]
                    attempt_id = attempt
                    attempt_native_turn_id = context.get("native_turn_id")
                    if attempt_native_turn_id not in (None, native_turn_id):
                        hook_turn_ids = []
                        if metadata_path.exists():
                            hook_turn_ids = [dict(event=row.get("event"), turn_id=row.get("turn_id"),
                                source=row.get("source"), phase=row.get("phase", "entry"))
                                for line in metadata_path.read_text(encoding="utf-8").splitlines()
                                if (row := json.loads(line)).get("event") in ("UserPromptSubmit", "SessionStart")]
                        raise AssertionError("native inbox context turn does not match turn/start response: " +
                            json.dumps(dict(context_turn_id=attempt_native_turn_id,
                                turn_start_response_id=native_turn_id, hook_turn_ids=hook_turn_ids)))
                    if attempt_native_turn_id is None:
                        hook_rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
                        compact_exit = max((i for i, row in enumerate(hook_rows)
                            if row.get("event") == "SessionStart" and row.get("source") == "compact"
                            and row.get("phase") == "exit" and row.get("exit_code") == 0), default=-1)
                        prompt_entry = min((i for i, row in enumerate(hook_rows)
                            if row.get("event") == "UserPromptSubmit" and row.get("turn_id") == native_turn_id
                            and row.get("phase", "entry") == "entry"), default=len(hook_rows))
                        assert compact_exit >= 0 and compact_exit < prompt_entry, \
                            "unbound notice claim lacks completed SessionStart(compact) before continuation prompt"
                    assert context["event_id"] == event["event_id"], "compact context changed event identity"
                    assert context["delivery_id"] == delivery_id, "compact context changed delivery identity"
                    assert context["source"] == event["ref"], "compact context changed source reference"
                    assert event["ref"]["record_id"] == source["record_id"]
                    assert event["ref"]["version"] == source["version"]
                    assert event["kind"] == ("notice" if scenario == "claimed-before-compact" else scenario), \
                        "compact context changed notice/response kind"
                    if scenario == "response":
                        assert event["causation_id"] == causal_request["event_id"]
                    claim = dict(request_id=attempt, session=dict(
                        agent_id=context["agent_id"], execution_id=context["execution_id"]))
                    if attempt_native_turn_id is not None:
                        claim["native_turn_id"] = attempt_native_turn_id
                    claimed = api_call("carol", "session-inbox-claim", raw=True,
                        body=json.dumps(claim))["attempt"]
                    assert claimed["attempt_id"] == attempt, "session claim changed attempt identity"
                    assert claimed.get("native_turn_id") == attempt_native_turn_id, \
                        "Store claim changed the admission turn binding"
                    assert claimed["session"] == dict(agent_id=context["agent_id"],
                        execution_id=context["execution_id"]), "session claim changed session identity"
                    assert claimed["delivery"]["delivery_id"] == delivery_id, "session claim changed delivery identity"
                    if failed_completion is None:
                        # Exercise a real, state-preserving Store refusal on the
                        # claimed attempt. The valid compact settlement below
                        # is the same-intent recovery path.
                        refusal_request = str(uuid.uuid4())
                        api_call("carol", "ack", "--request-id", refusal_request,
                            "--agent-id", context["agent_id"], "--execution-id", context["execution_id"],
                            "--lease", str(uuid.uuid4()), "--disposition", "handled",
                            delivery_id, expected="STALE_LEASE")
                        still_leased = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                        assert still_leased["state"] == "leased" and still_leased["attempts"] == 1, \
                            "refused completion changed the admitted delivery"
                        failed_completion = dict(request_id=refusal_request,
                            code="STALE_LEASE", delivery_id=delivery_id,
                            event_id=event["event_id"], attempt_id=attempt,
                            native_turn_id=native_turn_id,
                            admission_state=claimed["delivery"]["state"],
                            store_attempt_id=claimed["attempt_id"],
                            store_attempt_native_turn_id=claimed.get("native_turn_id"))
                    serialized = json.dumps(request.get("input"))
                    handle = context.get("compact_handle")
                    assert isinstance(handle, str) and handle in serialized, "compact handle absent from native request"
                    if "compact-read" not in results:
                        assert SOURCE_BODY not in serialized, "full source was inlined before explicit read"
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "read", handle])
                        item = _exec_tool("compact-read", command)
                    elif scenario == "claimed-before-compact" and not settlement_confirmed and compact_turn_id is None:
                        read_result = results["compact-read"]
                        output = json.dumps(read_result)
                        assert SOURCE_BODY in output and source["record_id"] in output, \
                            "pre-compaction read omitted exact Store source"
                        source_read_confirmed = True
                        if compact_in_progress:
                            raise AssertionError("attempt was read only after compaction began")
                        item = dict(type="message", id="msg_read_before_compact", role="assistant", status="completed",
                            content=[dict(type="output_text", text="I read the exact notice source; continue after compaction.", annotations=[])])
                        precompact_read_completed.set()
                    elif "compact-settle" not in results:
                        read_result = results["compact-read"]
                        output = json.dumps(read_result)
                        assert SOURCE_BODY in output and source["record_id"] in output, \
                            "compact read omitted exact source"
                        source_read_confirmed = True
                        command = shlex.join([str(home / "coordination/coordination.py"),
                            "inbox", "compact", "settle", handle, "handled"])
                        item = _exec_tool("compact-settle", command)
                    else:
                        settled = json.dumps(results["compact-settle"])
                        assert "handled" in settled and "attempts" in settled, "compact settlement did not confirm handled"
                        current = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                        assert current["state"] == "handled" and current["attempts"] == 1, "Store did not confirm one handled attempt"
                        settlement_confirmed = True
                        if scenario == "response":
                            assert not current.get("result"), "response settlement falsely created a task result"
                            visible_events = api_call("bob", "events", "--limit", "100")
                            assert not visible_events.get("more")
                            assert not any(row.get("causation_id") == event["event_id"]
                                for row in visible_events["events"]), "settling response created a reply-to-response loop"
                        item = dict(type="message", id="msg_done", role="assistant", status="completed",
                            content=[dict(type="output_text", text="Compact notice handled.", annotations=[])])
                        finished.set()
                body = response_stream("resp_" + str(len(requests)), item,
                    input_tokens=200 if scenario == "auto-in-turn-claimed" and
                    item.get("type") == "custom_tool_call" else 1)
            except (AssertionError, KeyError, LookupError, StopIteration, ValueError, TypeError) as exc:
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
        + ('model_context_window = 1000\nmodel_auto_compact_token_limit = 50\n'
           if scenario == "auto-in-turn-claimed" else '')
        + '[model_providers.fixture]\nname = "Loopback fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n', encoding="utf-8")
    (home / "config.toml").chmod(0o600)
    def verify(rpc):
        nonlocal native_id, registration_turn_id, compact_turn_id, continuation_turn_id
        nonlocal native_turn_id, event, delivery_id, compact_in_progress, claimed_before_compact
        nonlocal precompact_read_turn_id
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

        registration_completed = False
        registration_events = []
        registration_terminal_deadline = time.monotonic() + 30
        while time.monotonic() < registration_terminal_deadline and not registration_completed:
            try:
                notification = rpc.next_notification(timeout=min(5, max(.1,
                    registration_terminal_deadline - time.monotonic())))
            except queue.Empty:
                continue
            params = notification.get("params", {})
            turn = params.get("turn", {})
            registration_events.append(dict(method=notification.get("method"),
                turn_id=turn.get("id") or params.get("turnId")))
            if (notification.get("method") == "turn/completed" and
                    params.get("threadId") == native_id and turn.get("id") == registration_turn_id):
                registration_completed = True
        assert registration_completed, "registration turn did not reach matching terminal notification: " + \
            json.dumps(registration_events)

        agents = api_call("carol", "agents", "list", "--harness", "codex")["agents"]
        target = next((row for row in agents if row["native_session_id"] == native_id), None)
        assert target is not None, "Codex native session was not registered"
        if scenario == "batch-admission":
            for batch_source in batch_sources:
                batch_events.append(api_call("alice", "publish", "--request-id", str(uuid.uuid4()),
                    "--to", target["inbox"], "--kind", "notice", "--version",
                    str(batch_source["version"]), batch_source["record_id"]))
            event = batch_events[0]
        elif scenario in ("notice", "claimed-before-compact", "auto-in-turn-claimed"):
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
        before_compact = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
        assert before_compact["delivery_id"] == delivery_id and before_compact["state"] == "pending" \
            and before_compact["attempts"] == 0, "notice was not pending before compaction"

        if scenario == "claimed-before-compact":
            preturn = rpc("turn/start", dict(threadId=native_id,
                input=[dict(type="text", text="Read the exact notice source, then wait for compaction.", text_elements=[])]))
            precompact_read_turn_id = preturn["turn"]["id"]
            native_turn_id = precompact_read_turn_id
            native_turn_ready.set()
            assert precompact_read_completed.wait(45), "native actor did not read the source before compaction"
            preturn_terminal = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not preturn_terminal:
                notification = rpc.next_notification(timeout=min(5, max(.1, deadline - time.monotonic())))
                params = notification.get("params", {})
                if (notification.get("method") == "turn/completed" and params.get("threadId") == native_id
                        and params.get("turn", {}).get("id") == precompact_read_turn_id):
                    preturn_terminal = True
            assert preturn_terminal, "source-read turn did not reach matching turn/completed"
            preturn_hooks = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()
                             if line]
            assert any(row.get("event") == "Stop" and row.get("phase") == "exit"
                       and row.get("turn_id") == precompact_read_turn_id and row.get("exit_code") == 0
                       for row in preturn_hooks), "source-read turn did not finish its native Stop hook"
            claimed_before_compact = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
            assert claimed_before_compact["delivery_id"] == delivery_id and \
                claimed_before_compact["state"] == "failed" and claimed_before_compact["attempts"] == 1 and \
                claimed_before_compact.get("code") == "processing_failed", \
                "native Stop did not reconcile the unfinished exact-source attempt before compaction: " + \
                json.dumps(claimed_before_compact)

        if scenario == "batch-admission":
            continuation = rpc("turn/start", dict(threadId=native_id,
                input=[dict(type="text", text="Observe the staged batch admission fixture.", text_elements=[])]))
            continuation_turn_id = continuation["turn"]["id"]
            native_turn_id = continuation_turn_id
            native_turn_ready.set()
            assert admission_checked.wait(30), "staged Codex did not reach batch-enabled first admission"
            return

        if scenario == "auto-in-turn-claimed":
            auto_turn = rpc("turn/start", dict(threadId=native_id,
                input=[dict(type="text", text=("Read the exact claimed notice, then continue the same "
                    "turn until Codex automatically compacts its context."), text_elements=[])]))
            native_turn_id = auto_turn["turn"]["id"]
            native_turn_ready.set()
            auto_completed = False
            auto_notifications = []
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not auto_completed:
                try:
                    notification = rpc.next_notification(timeout=min(5, max(.1, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                params = notification.get("params", {})
                turn = params.get("turn", {})
                item = params.get("item", {})
                auto_notifications.append(dict(method=notification.get("method"),
                    turn_id=turn.get("id") or params.get("turnId"),
                    item_type=item.get("type")))
                if (notification.get("method") == "turn/completed" and
                        params.get("threadId") == native_id and turn.get("id") == native_turn_id):
                    auto_completed = True
            assert auto_completed, "automatic-compaction turn did not reach matching turn/completed: " + \
                json.dumps(auto_notifications)
            assert automatic_compaction_observed, "high-usage tool follow-up did not trigger native PreCompact"
            assert source_read_confirmed, "automatic-compaction fixture did not read the exact source"
            assert automatic_compaction_source_order[0] == "exact_source_read" and \
                automatic_compaction_source_order.index("exact_source_read") < \
                automatic_compaction_source_order.index("PreCompact"), \
                "claimed exact source was not read before automatic compaction: " + \
                json.dumps(automatic_compaction_source_order)
            assert automatic_compaction_store_state and automatic_compaction_store_state["state"] == "leased" \
                and automatic_compaction_store_state["attempts"] == 1, \
                "Store claim was not preserved as leased with one attempt through automatic compaction: " + \
                json.dumps(dict(store_state=automatic_compaction_store_state,
                    source_order=automatic_compaction_source_order,
                    source_read_confirmed=source_read_confirmed,
                    compact_observed=automatic_compaction_observed))
            assert automatic_compaction_after_sessionstart_state and \
                automatic_compaction_after_sessionstart_state["state"] == "leased" and \
                automatic_compaction_after_sessionstart_state["attempts"] == 1, \
                "compact SessionStart changed the exact delivery or attempt"
            hook_rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line]
            precompact_index = next(i for i, row in enumerate(hook_rows)
                if row.get("event") == "PreCompact" and row.get("phase") == "exit" and row.get("exit_code") == 0)
            stop_candidates = [i for i, row in enumerate(hook_rows)
                if row.get("event") == "Stop" and row.get("phase", "entry") == "entry" and
                row.get("turn_id") == native_turn_id]
            assert stop_candidates, "native Stop hook for automatic turn missing: " + json.dumps(dict(
                native_turn_id=native_turn_id,
                hooks=[{key: row.get(key) for key in ("event", "turn_id", "source", "phase", "exit_code",
                                                        "stderr_code", "stderr_class")}
                       for row in hook_rows]))
            stop_index = stop_candidates[0]
            assert precompact_index < stop_index, "automatic PreCompact did not occur before turn-end Stop"
            precompact_row = hook_rows[precompact_index]
            assert precompact_row.get("turn_id") == native_turn_id, \
                "automatic PreCompact was not bound to the active native turn"
            compact_resume_indices = [i for i, row in enumerate(hook_rows)
                if row.get("event") == "SessionStart" and row.get("source") == "compact" and
                row.get("phase") == "exit"]
            assert compact_resume_indices and all(hook_rows[i].get("turn_id") is None
                for i in compact_resume_indices), \
                "automatic compact SessionStart unexpectedly carried a native turn id"
            if compact_resume_indices:
                assert precompact_index < compact_resume_indices[0] < stop_index, \
                    "automatic compact SessionStart did not occur between PreCompact and Stop"
                compact_outcomes = {row.get("hook_kind"): row for row in hook_rows
                    if row.get("event") == "SessionStart" and row.get("source") == "compact" and
                    row.get("phase") == "exit"}
                assert set(compact_outcomes) == {"coordination", "memory"} and \
                    compact_outcomes["memory"].get("exit_code") == 0 and \
                    compact_outcomes["coordination"].get("exit_code") == 1 and \
                    compact_outcomes["coordination"].get("stderr_code") == "NATIVE_TURN_MISMATCH", \
                    "automatic compact hook outcomes changed: " + json.dumps({kind: {
                        "exit_code": row.get("exit_code"), "stderr_code": row.get("stderr_code")}
                        for kind, row in compact_outcomes.items()})
            context_compaction_items = [row for row in auto_notifications
                if row.get("item_type") == "contextCompaction"]
            assert {row.get("method") for row in context_compaction_items} >= {"item/started", "item/completed"}, \
                "app-server did not report matching automatic contextCompaction item start/completion"
            final_state = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
            assert final_state["delivery_id"] == delivery_id and final_state["attempts"] == 1 and \
                final_state["state"] == "failed" and final_state.get("code") == "processing_failed", \
                "unsettled attempt was falsely settled or replayed after automatic compaction and Stop"
            stop_row = next(row for row in hook_rows if row.get("event") == "Stop" and
                row.get("phase") == "exit" and row.get("turn_id") == native_turn_id)
            assert stop_row.get("exit_code") == 0, "automatic-compaction turn Stop hook failed"
            print(json.dumps(dict(result="PASS", scenario=scenario,
                native_automatic_compaction=True, native_turn_id=native_turn_id,
                event_id=event["event_id"], delivery_id=delivery_id, attempt_id=attempt_id,
                attempt_native_turn_id=attempt_native_turn_id,
                source_ref=event["ref"], exact_source_read=source_read_confirmed,
                source_order=automatic_compaction_source_order,
                delivery_state_during_compaction=automatic_compaction_store_state["state"],
                attempts_during_compaction=automatic_compaction_store_state["attempts"],
                delivery_state_after_compact_sessionstart=automatic_compaction_after_sessionstart_state["state"],
                attempts_after_compact_sessionstart=automatic_compaction_after_sessionstart_state["attempts"],
                final_delivery_state=final_state["state"], final_delivery_code=final_state.get("code"),
                final_attempts=final_state["attempts"], false_settlement=False,
                compact_sessionstart_observed=bool(compact_resume_indices),
                compact_sessionstart_outcomes={kind: {"exit_code": row.get("exit_code"),
                    "error_code": row.get("stderr_code")} for kind, row in compact_outcomes.items()}
                    if compact_resume_indices else {},
                precompact_turn_id=precompact_row.get("turn_id"),
                precompact_before_stop=True, loopback_response_requests=len(requests),
                response_notifications=auto_notifications, provider_call=False,
                provider_usage_observed=False), sort_keys=True))
            return

        compact_in_progress = True
        compact_response = rpc("thread/compact/start", dict(threadId=native_id))
        assert compact_response == {}, "manual compact did not return the empty accepted response"
        compact_started = None
        compact_completed = None
        compact_notifications = []
        compact_deadline = time.monotonic() + 60
        while time.monotonic() < compact_deadline and compact_completed is None:
            try:
                notification = rpc.next_notification(timeout=min(5, max(.1, compact_deadline - time.monotonic())))
            except queue.Empty:
                continue
            method = notification.get("method")
            params = notification.get("params", {})
            if params.get("threadId") != native_id:
                continue
            compact_notifications.append(dict(method=method, turn_id=params.get("turn", {}).get("id")))
            if method == "turn/started":
                compact_started = params
                compact_turn_id = params.get("turn", {}).get("id")
            elif method == "turn/completed" and params.get("turn", {}).get("id") == compact_turn_id:
                compact_completed = params
        hook_summary = []
        if metadata_path.exists():
            for line in metadata_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                hook_summary.append(dict(phase=row.get("phase", "entry"), event=row.get("event"),
                    turn_id=row.get("turn_id"), source=row.get("source"), exit_code=row.get("exit_code")))
        assert compact_started and compact_turn_id and compact_completed, \
            "manual compact did not reach matching turn/completed: " + json.dumps(dict(
                notifications=compact_notifications, hooks=hook_summary,
                loopback_requests=len(requests)))
        compact_in_progress = False
        after_compact = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
        if scenario == "claimed-before-compact":
            assert after_compact["delivery_id"] == delivery_id and after_compact["state"] == "failed" \
                and after_compact["attempts"] == 1 and after_compact.get("code") == "processing_failed", \
                "failed delivery changed or auto-replayed while native compaction was running"
        else:
            assert after_compact["delivery_id"] == delivery_id and after_compact["state"] == "pending" \
                and after_compact["attempts"] == 0, "notice changed while native compaction was running"
        compact_hook_metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()
                                 if line]
        observed_hooks = [dict(event=row.get("event"), turn_id=row.get("turn_id"), source=row.get("source"))
                          for row in compact_hook_metadata]
        assert any(row.get("event") == "PreCompact" and row.get("turn_id") == compact_turn_id
                   for row in compact_hook_metadata), \
            "native compact omitted matching PreCompact hook: " + json.dumps(observed_hooks)
        continuation = rpc("turn/start", dict(threadId=native_id,
            input=[dict(type="text", text="Reply OK to continue the local fixture.", text_elements=[])]))
        continuation_turn_id = continuation["turn"]["id"]
        native_turn_id = continuation_turn_id
        native_turn_ready.set()
        continuation_completed = False
        continuation_deadline = time.monotonic() + 60
        while time.monotonic() < continuation_deadline and not continuation_completed and not finished.is_set():
            notification = rpc.next_notification(timeout=max(.1, continuation_deadline - time.monotonic()))
            params = notification.get("params", {})
            if (notification.get("method") == "turn/completed" and
                    params.get("threadId") == native_id and
                    params.get("turn", {}).get("id") == continuation_turn_id):
                continuation_completed = True
        deadline = time.monotonic() + 60
        while not finished.wait(.1):
            assert not failures, failures
            assert time.monotonic() < deadline, "Codex compact loopback did not read and settle after compaction"
        assert continuation_completed or finished.is_set(), \
            "native continuation did not complete after compact terminal notification"
        compact_hook_metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()
                                 if line]
        observed_hooks = [dict(phase=row.get("phase", "entry"), event=row.get("event"),
            turn_id=row.get("turn_id"), source=row.get("source"), exit_code=row.get("exit_code"))
            for row in compact_hook_metadata]
        assert any(row.get("event") == "SessionStart" and row.get("source") == "compact"
                   for row in compact_hook_metadata), \
            "continuation did not observe SessionStart(source=compact): " + json.dumps(dict(
                hooks=observed_hooks, loopback_requests=len(requests)))

        assert event is not None and delivery_id is not None
        if scenario == "claimed-before-compact":
            assert before_compact["state"] == "pending" and after_compact["state"] == "failed"
        else:
            assert before_compact["state"] == "pending" and after_compact["state"] == "pending"
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
        installer.trust_codex_hooks(home, settings, hook_commands["coordination"],
                                    codex_binary=binary, expected=6)
        installer.trust_codex_hooks(home, settings, hook_commands["memory"],
                                    verify=verify, codex_binary=binary, expected=4)
        assert not failures, failures
        if scenario == "auto-in-turn-claimed":
            assert automatic_compaction_observed and source_read_confirmed
            return
        if scenario == "batch-admission":
            assert admission_checked.is_set() and admission_requests >= 1, \
                "readiness run did not stop at the first native batch admission"
            final_states = [api_call("alice", "event-status", item["event_id"])["deliveries"][0]
                            for item in batch_events]
            assert admission_states and admission_states[0]["state"] == "leased"
            assert all(row["state"] == "pending" and row["attempts"] == 0 for row in admission_states[1:])
            assert all(row["state"] == "pending" and row["attempts"] == 0 for row in final_states[1:])
            assert final_states[0]["state"] in ("leased", "failed")
            assert not source_read_confirmed and not settlement_confirmed
            print(json.dumps(dict(result="PASS", scenario=scenario, staged_batch_config=True,
                exact_first_admission=True, pending_neighbors=2, settlement="not_performed",
                admission_requests=admission_requests, first_item_after_fixture_stop=final_states[0]["state"],
                provider="local_fixture_refused_before_inference")))
            return
        assert event is not None and len(requests) == 5
        hook_metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line]
        assert any(row.get("event") == "UserPromptSubmit" and row.get("turn_id") == native_turn_id
                   for row in hook_metadata), "hook metadata did not observe the exact compact turn"
        scripted_response_ids = ["resp_" + str(i) for i in range(1, len(requests) + 1)]
        final_delivery = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
        if scenario == "claimed-before-compact":
            assert source_read_confirmed and not settlement_confirmed, \
                "fixture did not preserve the pre-compaction read and turn-end failure boundary"
        else:
            assert source_read_confirmed and settlement_confirmed, \
                "post-compaction source read and settlement were not both observed"
        native_report = None
        if attempt_native_turn_id is not None and scenario != "claimed-before-compact":
            native_report = compile_recovery_report(dict(schema="cairn.codex-recovery-probe/1",
                refusal=failed_completion,
                settlement=dict(event_id=event["event_id"], delivery_id=delivery_id,
                    attempt_id=attempt_id, native_turn_id=attempt_native_turn_id,
                    state=final_delivery["state"], attempts=final_delivery["attempts"],
                    code=final_delivery.get("code"))))
            report_json = json.dumps(native_report)
            assert native_report["provider_turn"] == dict(status="not_observed", responses=None, usage=None)
            assert all(value not in report_json for value in
                       (event["event_id"], delivery_id, attempt_id, attempt_native_turn_id)), \
                "recovery report retained a raw Store or native-turn identifier"
        print(json.dumps(dict(result="PASS", native_session_registered=True, scenario=scenario,
            kind=event["kind"], causal_request_id=causal_request["event_id"] if causal_request else None,
            event_id=event["event_id"], delivery_id=delivery_id,
            attempt_id=attempt_id, native_session_id=native_id,
            registration_turn_id=registration_turn_id,
            native_turn_id=native_turn_id, native_turn_joined=True,
            registration_turn_terminal_observed=True,
            compact_turn_terminal_observed=True,
            native_manual_compact_turn_id=compact_turn_id,
            native_compact_terminal_notification="turn/completed",
            compact_hooks_observed=True,
            continuation_after_compact_completed=True,
            continuation_turn_id=continuation_turn_id,
            attempt_native_turn_id=attempt_native_turn_id,
            precompact_read_turn_id=precompact_read_turn_id,
            loopback_response_requests=len(requests),
            notice_pending_before_and_after_compact=(scenario != "claimed-before-compact"),
            claimed_attempt_source_read_before_compact=(scenario == "claimed-before-compact"),
            precompact_delivery_state=(claimed_before_compact["state"] if scenario == "claimed-before-compact" else None),
            precompact_delivery_code=(claimed_before_compact.get("code") if scenario == "claimed-before-compact" else None),
            notice_delivery_id=delivery_id,
            notice_event_id=event["event_id"],
            source_read_after_compact=(source_read_confirmed and scenario != "claimed-before-compact"),
            source_read_before_compact=(source_read_confirmed and scenario == "claimed-before-compact"),
            settlement_after_compact=settlement_confirmed,
            failed_delivery_persisted_after_compact=(scenario == "claimed-before-compact"),
            native_stop_preceded_compact=(scenario == "claimed-before-compact"),
            observed_hook_events=[dict(phase=row.get("phase", "entry"), event=row.get("event"),
                turn_id=row.get("turn_id"), source=row.get("source"), exit_code=row.get("exit_code"))
                for row in hook_metadata],
            exact_source_bytes=len(SOURCE_BODY.encode()), exact_source_read=True,
            explicit_handled_settlement=True, attempt_count=1, model=MODEL,
            request_count=len(requests), scripted_response_ids=scripted_response_ids,
            model_decision="scripted fixture sequence",
            compact_turn_hook_observed=True,
            provider_response_ids_are_scripted=True,
            provider_usage_observed=False, provider="loopback_fixture", provider_call=False,
            recovery_report=native_report), sort_keys=True))
    finally:
        if previous_inbox_root is None:
            os.environ.pop("CAIRN_INBOX_ROOT", None)
        else:
            os.environ["CAIRN_INBOX_ROOT"] = previous_inbox_root
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
