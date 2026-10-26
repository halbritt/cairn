"""Run the baseline single-notice workflow through native Codex hooks and a local stub.

This fixture proves transport and source-read plumbing only. Its Responses
messages, function calls, and usage are scripted; they are not actor decisions
or provider measurements.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import stat
import sys
import threading
import time
import uuid


MODEL = "gpt-6-luna"
REPORT_SCHEMA = "cairn.codex-full-inbox-native-fixture/1"
FIXTURE = Path(__file__).resolve().parents[1] / "trials/token-efficiency/FIXTURE.json"
SOURCE_BODY = "Build 81 completed; artifact checksum is 7b1a."
BATCH_BODIES = (
    "Batch notice 1: record alpha is ready.",
    "Batch notice 2: record bravo is ready.",
    "Batch notice 3: record charlie is ready.",
    "Batch notice 4: remains pending after the bounded turn.",
)


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
        pass
    events += [dict(type="response.output_item.done", output_index=0, item=item),
               dict(type="response.completed", response=dict(
                   response, status="completed", output=[item],
                   usage=dict(input_tokens=1, output_tokens=1, total_tokens=2)))]
    return "".join("event: " + event["type"] + "\ndata: " +
                   json.dumps(dict(event, sequence_number=i)) + "\n\n"
                   for i, event in enumerate(events)).encode()


def _load_installer():
    path = Path(__file__).with_name("install-agent-coordination.py")
    spec = importlib.util.spec_from_file_location("codex_full_inbox_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage_hook_metadata_wrapper(profile):
    """Stage the wrapper and its diagnostic helper into the isolated profile."""
    source_dir = Path(__file__).resolve().parent
    wrapper = Path(profile) / "record_codex_hook_metadata.py"
    helper = Path(profile) / "codex_fixture_diagnostic.py"
    shutil.copyfile(source_dir / wrapper.name, wrapper)
    shutil.copyfile(source_dir / helper.name, helper)
    wrapper.chmod(0o600)
    helper.chmod(0o600)
    return wrapper


def _fixture_task_and_notice(scenario="single"):
    if scenario == "batch":
        return ("Process the first three already-waiting routine notices in this turn. "
                "Read each exact source, explicitly settle each as handled, and stop at the batch limit.",
                BATCH_BODIES)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = next(item for item in data["cases"] if item.get("id") == "single_notice")
    assert row["actor_task"] == "Process the single routine build notice for Cairn and record its exact disposition."
    events = row["setup"]["events"]
    assert len(events) == 1 and events[0]["id"] == "notice_a" and events[0]["kind"] == "notice"
    assert events[0]["body"] == SOURCE_BODY
    # Deliberately return only actor-visible inputs. Never read acceptance_check.
    return row["actor_task"], events[0]["body"]


def _exec_tool(call_id, command):
    code = "text(await tools.exec_command(" + json.dumps(dict(
        cmd=command, max_output_tokens=3000)) + "));"
    return dict(type="custom_tool_call", id="ctc_" + call_id, name="exec",
                namespace="functions", call_id=call_id, input=code)


def _wait_for_registration_hook(stop_complete_path, turn_id, timeout=30):
    """Wait for the registration turn's Stop hook to finish before querying state.

    Codex can report turn/completed before its command hook has exited. The
    marker is written by record_codex_hook_metadata.py only after the Stop
    hook returns, so it provides the ordering boundary needed by the fixture.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            completed = json.loads(Path(stop_complete_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if completed.get("event") != "Stop" or completed.get("turn_id") != turn_id:
            raise AssertionError("registration Stop hook marker does not match the registration turn")
        if completed.get("exit_code") != 0:
            raise AssertionError("registration Stop hook returned nonzero")
        return completed
    raise TimeoutError("registration Stop hook did not complete before fixture lookup")


def _diagnostic_path_roles(binary, config, candidate_root, codex_entry, codex_root, root):
    """Build private path-role inputs for filename classification only."""
    config = config if isinstance(config, dict) else {}
    root = Path(root)
    token_file = config.get("token_file")
    return {
        "selected_codex": binary,
        "codex_entry": codex_entry,
        "codex_package": codex_root,
        "candidate_root": candidate_root,
        "cairn_binary": config.get("cairn"),
        "api_socket": config.get("socket"),
        "token_file": token_file,
        "identity_file": (Path(token_file).parent / "identities.json"
                          if isinstance(token_file, (str, os.PathLike)) else None),
        "fixture_source": FIXTURE,
        "coordination_source": Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py",
        "hook_metadata_wrapper": Path(__file__).with_name("record_codex_hook_metadata.py"),
        "actor_workspace": root / "workspace",
        "profile_settings": root / "workspace" / ".codex" / "hooks.json",
        "launcher": root / "codex-app-server-bwrap",
    }


def _check_impl(binary, root, config, api_call, candidate_root=None, sandbox_auth_file=None,
                turn_timeout=60, codex_entry=None, codex_root=None, mode="complete",
                scenario="single", diagnostic_output=None, api_role="bob",
                preopened_trace=None):
    """Run one disposable native notice turn.

    ``binary`` is the selected Codex installation identity. It is never
    executed. The bwrap launcher is built only from explicit Node CLI entry
    and package-root paths; a synthetic empty auth object is used by default.
    """
    trace = preopened_trace

    def setup_stage(name):
        if trace is not None and scenario == "batch":
            trace.record_setup_stage(name, "start")

    def setup_done():
        if trace is not None and scenario == "batch":
            trace.record_setup_stage(trace.setup_stage, "complete")

    if scenario not in ("single", "batch"):
        raise ValueError("scenario must be single or batch")
    if scenario == "batch" and diagnostic_output is None:
        raise ValueError("batch actor fixture requires a private diagnostic output path")
    setup_stage("validate_codex_paths")
    if codex_entry is None or codex_root is None:
        raise RuntimeError("explicit Node Codex entry and package root are required for the bwrap fixture")
    selected_binary = Path(binary).resolve(strict=True)
    codex_entry = Path(codex_entry).resolve(strict=True)
    codex_root = Path(codex_root).resolve(strict=True)
    if not selected_binary.is_file() or not codex_entry.is_file():
        raise RuntimeError("selected Codex installation and Node entry must be regular files")
    if codex_entry.suffix not in (".js", ".mjs", ".cjs"):
        raise RuntimeError("Codex entry must be its Node CLI script, not an installed shell wrapper")
    setup_done()
    setup_stage("validate_candidate_paths")
    if candidate_root is None:
        raise RuntimeError("disposable candidate root is required")
    candidate_root = Path(candidate_root).resolve(strict=True)
    cairn_binary = Path(config["cairn"]).resolve(strict=True)
    if not cairn_binary.is_relative_to(candidate_root):
        raise RuntimeError("Cairn executable must come from the disposable candidate root")
    setup_done()
    setup_stage("verify_test_database")
    if not os.environ.get("CAIRN_TEST_DATABASE_URL"):
        raise RuntimeError("disposable integration database is required")
    setup_done()
    token_identity_state = "unavailable"
    if scenario == "batch":
        setup_stage("verify_token_identity")
        token_path = Path(config["token_file"]).resolve(strict=True)
        identity_path = token_path.parent / "identities.json"
        token_hash = hashlib.sha256(token_path.read_bytes()).hexdigest()
        identities = json.loads(identity_path.read_text(encoding="utf-8"))
        binding_principal = next((row["principal"] for row in identities
                                  if row.get("token_sha256") == token_hash), None)
        if binding_principal != "agent/" + api_role:
            raise RuntimeError("native batch API role does not match binding token principal")
        token_identity_state = "matched"
        setup_done()
    setup_stage("load_actor_task")
    actor_task, notice_source = _fixture_task_and_notice(scenario)
    notice_body = notice_source[0] if scenario == "batch" else notice_source
    if not isinstance(turn_timeout, (int, float)) or turn_timeout <= 0:
        raise ValueError("turn_timeout must be positive seconds")
    if mode not in ("complete", "stall"):
        raise ValueError("mode must be complete or stall")
    setup_done()
    root = Path(root).resolve()
    if preopened_trace is None:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    else:
        if (not root.is_dir() or root.is_symlink() or
                {path.name for path in root.iterdir()}):
            raise RuntimeError("pre-created native fixture root must be an empty directory")
    diagnostic_path = Path(diagnostic_output) if diagnostic_output is not None else None
    classify_advance_tool_result = None
    if diagnostic_path is not None:
        from native_notice_batch_diagnostics import (
            AdvanceDiagnosticWriter,
            advance_tool_result_stage,
            classify_advance_tool_result as classify,
            classify_setup_filename,
        )
        classify_advance_tool_result_stage = advance_tool_result_stage
        classify_advance_tool_result = classify
        if trace is None:
            trace = AdvanceDiagnosticWriter(diagnostic_path, root)
        path_roles = _diagnostic_path_roles(binary, config, candidate_root, codex_entry,
                                           codex_root, root)
        if scenario == "batch":
            trace.record_preflight("token_identity", token_identity_state)
            setup_stage("verify_api")
            try:
                socket_path = Path(config["socket"]).resolve(strict=True)
                if not stat.S_ISSOCK(socket_path.stat().st_mode):
                    raise RuntimeError("unavailable")
                trace.record_preflight("api_socket", "available")
            except FileNotFoundError as exc:
                trace.record_preflight("api_socket", "unavailable")
                trace.record_setup_failure(type(exc).__name__,
                    classify_setup_filename(exc.filename, path_roles))
                raise RuntimeError("native batch API socket preflight failed") from None
            except (OSError, KeyError, TypeError):
                trace.record_preflight("api_socket", "unavailable")
                raise RuntimeError("native batch API socket preflight failed") from None
            try:
                version = api_call(api_role, "version", raw=True)
                if not isinstance(version, dict):
                    raise RuntimeError("unavailable")
                trace.record_preflight("api_version", "authenticated")
            except FileNotFoundError as exc:
                trace.record_preflight("api_version", "unavailable")
                trace.record_setup_failure(type(exc).__name__,
                    classify_setup_filename(exc.filename, path_roles))
                raise RuntimeError("native batch API identity preflight failed") from None
            except Exception:
                trace.record_preflight("api_version", "unavailable")
                raise RuntimeError("native batch API identity preflight failed") from None
            setup_done()
    setup_stage("create_actor_workspace")
    actor_root = root / "workspace"
    actor_root.mkdir(mode=0o700)
    setup_done()
    if scenario == "batch":
        setup_stage("copy_coordination")
        shutil.copyfile(Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py",
                        actor_root / "coordination.py")
        (actor_root / "coordination.py").chmod(0o600)
        setup_done()
    setup_stage("configure_profile")
    profile = actor_root / ".codex"
    profile.mkdir(mode=0o700)
    if sandbox_auth_file is None:
        auth_fixture = profile / "synthetic-auth-fixture.json"
        auth_fixture.write_text("{}\n", encoding="utf-8")
        auth_fixture.chmod(0o600)
    else:
        auth_input = Path(sandbox_auth_file)
        if auth_input.is_symlink():
            raise RuntimeError("synthetic auth fixture must not be a symlink")
        auth_fixture = auth_input.resolve(strict=True)
        if not auth_fixture.is_file():
            raise RuntimeError("synthetic auth fixture must be a regular file")
        try:
            synthetic_auth = json.loads(auth_fixture.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("synthetic auth fixture must be readable JSON") from exc
        if synthetic_auth != {} or stat.S_IMODE(auth_fixture.stat().st_mode) != 0o600:
            raise RuntimeError("synthetic auth fixture must be an owner-only empty JSON object")
    actor_task_path = actor_root / "task.txt"
    actor_task_path.write_text(actor_task + "\n", encoding="utf-8")
    actor_task_path.chmod(0o600)

    installer = _load_installer()
    settings = profile / "hooks.json"
    native_config = dict(config, harness="codex", binding="codex-full-inbox",
                         process_names=["codex"], config_home=str(profile), native_delivery=True)
    if scenario == "batch":
        native_config.update(compact_inbox_version=1, notice_batch_version=1)
    engine_root = profile / "coordination"
    if scenario == "batch":
        setup_stage("install_hooks")
    installed = installer.install(engine_root, settings, native_config)
    # The frozen check and fixture are outside the workspace. Only the actor
    # task and its private Codex profile are staged inside it.
    expected_workspace_files = {"task.txt", ".codex"}
    if scenario == "batch":
        expected_workspace_files.add("coordination.py")
    assert {path.name for path in actor_root.iterdir()} == expected_workspace_files
    setup_done()

    state_dir = engine_root / "state/codex-full-inbox"
    metadata_path = profile / "hook-metadata.jsonl"
    stop_complete_path = profile / "registration-stop-complete.json"
    requests, failures = [], []
    notice = None
    source = None
    native_id = None
    actor_turn_id = None
    attempt_id = native_turn_id = None
    delivery_id = None
    observed_turn_ids = set()
    completed_turn_ids = set()
    source_read_confirmed = settlement_confirmed = False
    finished = threading.Event()
    stalled = threading.Event()
    release_stall = threading.Event()
    timeout_evidence = None
    event_owner = "alice"
    attempt_claimed = False
    actor_turn_started = False
    batch_item_index = 0
    batch_action = "read"
    batch_last_call_id = None
    batch_call_index = 0
    batch_notices = []
    batch_sources = []
    batch_root_attempt_id = None
    batch_stage = "unknown"
    actor_terminal_class = "not_started"

    def batch_context():
        key = hashlib.sha256(("codex-full-inbox\0" + str(native_id)).encode()).hexdigest()
        session_path = state_dir / (key + ".json")
        current = json.loads(session_path.read_text(encoding="utf-8"))["inbox_attempt"]
        context_path = state_dir / "inbox" / (current["attempt_id"] + ".json")
        return current, json.loads(context_path.read_text(encoding="utf-8"))

    def batch_exec(action, handle):
        nonlocal batch_call_index, batch_last_call_id, batch_action
        command = ["/usr/bin/env", "CAIRN_INBOX_ROOT=" + str(engine_root),
                   "CODEX_THREAD_ID=" + str(native_id), "/usr/bin/python3",
                   str(actor_root / "coordination.py"), "inbox", "compact", action, handle]
        if action == "settle":
            command.append("handled")
        batch_call_index += 1
        call_id = "batch_" + str(batch_call_index)
        batch_last_call_id = call_id
        batch_action = "await_" + action
        return _exec_tool(call_id, shlex.join(command))

    if scenario == "batch":
        setup_stage("build_sandbox_launcher")
    helper_spec = importlib.util.spec_from_file_location(
        "trial_codex_app_server_sandbox",
        Path(__file__).with_name("trial_codex_app_server_sandbox.py"))
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    binds = []
    for field, bind_mode in (("cairn", "ro"), ("socket", "rw"), ("token_file", "ro")):
        original_path = Path(config[field])
        if original_path.is_symlink():
            raise RuntimeError(f"required disposable {field} path is unavailable")
        path = original_path.resolve(strict=True)
        if field == "socket":
            if not stat.S_ISSOCK(path.stat().st_mode):
                raise RuntimeError("API endpoint must be a disposable Unix socket")
        elif not path.is_file():
            raise RuntimeError(f"required disposable {field} path is not a regular file")
        binds.append((str(path), str(path), bind_mode))
    launcher = root / "codex-app-server-bwrap"
    if not codex_entry.is_relative_to(codex_root):
        raise RuntimeError("Codex Node entry must be inside the read-only package root")
    helper.write_launcher(launcher, workspace=actor_root, profile=profile,
                          codex_entry=codex_entry, selected_auth=auth_fixture,
                          codex_root=codex_root, extra_binds=binds)
    setup_done()

    metadata_wrapper = _stage_hook_metadata_wrapper(profile)
    hook_settings = json.loads(settings.read_text(encoding="utf-8"))
    for groups in hook_settings.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                original = hook.get("command", "")
                if str(engine_root / "coordination.py") in original and "--config" in original:
                    hook["command"] = shlex.join([os.sys.executable, str(metadata_wrapper),
                        "--metadata", str(metadata_path), "--stop-complete", str(stop_complete_path),
                        "--", *shlex.split(original)])
    settings.write_text(json.dumps(hook_settings, indent=2) + "\n", encoding="utf-8")
    settings.chmod(0o600)
    command = next(hook["command"] for groups in hook_settings["hooks"].values()
                   for group in groups for hook in group.get("hooks", [])
                   if "record_codex_hook_metadata.py" in hook.get("command", "")
                   and "--config" in hook.get("command", ""))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal notice, source, attempt_id, native_turn_id, delivery_id
            nonlocal source_read_confirmed, settlement_confirmed
            nonlocal attempt_claimed
            nonlocal batch_item_index, batch_action, batch_last_call_id, batch_root_attempt_id
            nonlocal batch_stage
            try:
                assert self.path == "/v1/responses", "unexpected local Responses path"
                size = int(self.headers.get("Content-Length", "0"))
                assert 0 < size <= 2_000_000 and len(requests) < (16 if scenario == "batch" else 8), \
                    "local Responses fixture bounds exceeded"
                request = json.loads(self.rfile.read(size))
                requests.append(request)
                results = {item["call_id"]: item for item in request.get("input", [])
                           if item.get("type") == "custom_tool_call_output"}
                if actor_turn_started and mode == "stall":
                    assert notice is not None and source is not None
                    context = None
                    context_deadline = time.monotonic() + min(0.75, turn_timeout / 2)
                    while time.monotonic() < context_deadline:
                        for path in (state_dir / "inbox").glob("*.json"):
                            candidate = json.loads(path.read_text(encoding="utf-8"))
                            if candidate.get("event_id") == notice["event_id"]:
                                context = candidate
                                break
                        if context is not None:
                            break
                        time.sleep(0.02)
                    if context is None:
                        status = api_call(event_owner, "event-status", notice["event_id"])
                        deliveries = status.get("deliveries", [])
                        state = deliveries[0] if len(deliveries) == 1 else {}
                        native_states = []
                        ready_state = "unavailable"
                        for path in state_dir.glob("*.json"):
                            try:
                                saved = json.loads(path.read_text(encoding="utf-8"))
                            except (OSError, ValueError):
                                continue
                            native_states.append(dict(
                                registered=bool(saved.get("agent")),
                                state_keys=sorted(saved),
                                inbox_intent=bool(saved.get("inbox_intent")),
                                inbox_attempt=bool(saved.get("inbox_attempt")),
                                active_prompt=bool(saved.get("active_prompt")),
                                delivered_since_idle=bool(saved.get("delivered_since_idle")),
                                state=saved.get("agent", {}).get("metadata", {}).get("state")))
                            agent = saved.get("agent") or {}
                            if agent.get("agent_id") and agent.get("execution_id"):
                                try:
                                    ready = api_call(api_role, "session-inbox-ready", raw=True,
                                        body=json.dumps(dict(agent_id=agent["agent_id"],
                                                             execution_id=agent["execution_id"])))
                                    ready_state = dict(ready=ready.get("ready"),
                                                       delivery_id_present=bool(ready.get("delivery_id")))
                                except Exception as exc:
                                    ready_state = type(exc).__name__
                        hook_meta = ([json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
                                     if metadata_path.exists() else [])
                        raise AssertionError("stall reached before exact inbox context; "
                            f"delivery_state={state.get('state', 'missing')} "
                            f"attempts={state.get('attempts', 'missing')} "
                            f"ready={ready_state} "
                            f"native_state={native_states} "
                            f"hook_events={[{key: row.get(key) for key in ('event', 'turn_id', 'phase', 'exit_code', 'stderr_code', 'stderr_class')}
                                           for row in hook_meta if row.get('phase') == 'exit']}")
                    attempt_id = context["attempt_id"]
                    native_turn_id = context.get("native_turn_id")
                    assert native_turn_id == actor_turn_id, "stall attempt is not bound to this native turn"
                    assert context["source"] == notice["ref"] == dict(
                        record_id=source["record_id"], version=source["version"])
                    claimed = api_call(api_role, "session-inbox-claim", raw=True, body=json.dumps(dict(
                        request_id=attempt_id,
                        session={key: context[key] for key in ("agent_id", "execution_id")},
                        native_turn_id=native_turn_id)))["attempt"]
                    assert claimed["attempt_id"] == attempt_id
                    assert claimed["native_turn_id"] == native_turn_id
                    assert claimed["delivery"]["delivery_id"] == delivery_id
                    attempt_claimed = True
                    status = api_call(event_owner, "event-status", notice["event_id"])
                    assert len(status["deliveries"]) == 1
                    delivery = status["deliveries"][0]
                    assert delivery["state"] == "leased" and delivery["attempts"] == 1, delivery
                    stalled.set()
                    release_stall.wait(turn_timeout + 10)
                    return
                if not actor_turn_started:
                    item = dict(type="message", id="msg_initial", role="assistant", status="completed",
                        content=[dict(type="output_text", text="Native fixture registration turn completed.", annotations=[])])
                    if trace is not None:
                        trace.record_actor_exchange("registration_message", ["none"], stage="registration")
                elif scenario == "batch":
                    response_class = "read_notice"
                    result_classes = ["none"]
                    result_stage = None
                    if batch_last_call_id is None:
                        batch_stage = "context_lookup"
                        current, context = batch_context()
                        attempt_id = current["attempt_id"]
                        batch_root_attempt_id = attempt_id
                        native_turn_id = current.get("native_turn_id")
                        assert native_turn_id == actor_turn_id, "initial notice is not bound to the actor turn"
                        assert current["delivery"]["event"]["ref"] == batch_sources[0]
                        assert current["delivery"]["event"]["event_id"] == batch_notices[0]["event_id"]
                        assert context["source"] == batch_sources[0]
                        assert context["compact_handle"]
                        if not attempt_claimed:
                            batch_stage = "session_claim"
                            claimed = api_call(api_role, "session-inbox-claim", raw=True,
                                body=json.dumps(dict(request_id=attempt_id,
                                    session={key: context[key] for key in ("agent_id", "execution_id")},
                                    native_turn_id=native_turn_id)))["attempt"]
                            assert claimed["attempt_id"] == attempt_id
                            assert claimed["native_turn_id"] == native_turn_id
                            assert claimed["delivery"]["delivery_id"] == batch_notices[0]["delivery_id"]
                            attempt_claimed = True
                        batch_stage = "read_tool_call"
                        item = batch_exec("read", context["compact_handle"])
                    else:
                        result = results.get(batch_last_call_id)
                        assert result is not None, "Codex omitted the previous batch tool result"
                        result_text = json.dumps(result, sort_keys=True)
                        current, context = batch_context()
                        assert current.get("native_turn_id") == actor_turn_id
                        assert (current.get("batch_root") or batch_root_attempt_id) == batch_root_attempt_id
                        if batch_action == "await_read":
                            batch_stage = "read_tool_result"
                            expected_source = batch_sources[batch_item_index]
                            assert BATCH_BODIES[batch_item_index] in result_text
                            assert expected_source["record_id"] in result_text
                            source_read_confirmed = True
                            result_classes = ["read_success"]
                            response_class = "settle_handled"
                            batch_stage = "settle_tool_call"
                            item = batch_exec("settle", context["compact_handle"])
                        elif batch_action == "await_settle":
                            batch_stage = "settle_tool_result"
                            event = batch_notices[batch_item_index]
                            delivery = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                            assert delivery["state"] == "handled" and delivery["attempts"] == 1
                            assert "handled" in result_text
                            settlement_confirmed = True
                            result_classes = ["settle_success"]
                            response_class = "advance_successor"
                            batch_stage = "advance_tool_call"
                            item = batch_exec("advance", context["compact_handle"])
                        elif batch_action == "await_advance":
                            batch_stage = "advance_tool_result"
                            result_stage = classify_advance_tool_result_stage(result)
                            if trace is not None:
                                trace.record_advance_result_stage(result_stage)
                            advance_class = classify_advance_tool_result(result)
                            if batch_item_index < 2:
                                assert advance_class == "advance_successor"
                                result_classes = ["advance_successor"]
                                batch_item_index += 1
                                next_attempt, next_context = batch_context()
                                expected_source = batch_sources[batch_item_index]
                                assert next_attempt["delivery"]["event"]["ref"] == expected_source
                                assert next_attempt.get("batch_index") == batch_item_index + 1
                                assert next_attempt.get("batch_root") == batch_root_attempt_id
                                assert next_context["source"] == expected_source
                                assert next_context.get("compact_handle")
                                response_class = "read_notice"
                                batch_stage = "read_tool_call"
                                item = batch_exec("read", next_context["compact_handle"])
                            else:
                                assert advance_class == "advance_limit"
                                result_classes = ["advance_limit"]
                                pending = api_call(event_owner, "event-status",
                                                   batch_notices[3]["event_id"])["deliveries"][0]
                                assert pending["state"] == "pending" and pending["attempts"] == 0
                                response_class = "final_message"
                                batch_stage = "turn_terminal"
                                item = dict(type="message", id="msg_batch_done", role="assistant",
                                    status="completed", content=[dict(type="output_text",
                                    text="Read and handled the first three notices; the fourth remains pending.",
                                    annotations=[])])
                                finished.set()
                        else:
                            raise AssertionError("unexpected batch tool result action")
                    if trace is not None:
                        trace.record_actor_exchange(response_class, result_classes, stage=batch_stage)
                else:
                    assert notice is not None and source is not None, "notice must be published before the actor turn"
                    paths = list((state_dir / "inbox").glob("*.json"))
                    context_path = next(path for path in paths
                        if json.loads(path.read_text(encoding="utf-8"))["event_id"] == notice["event_id"])
                    context = json.loads(context_path.read_text(encoding="utf-8"))
                    delivery_id_local = api_call(event_owner, "event-status", notice["event_id"])["deliveries"][0]["delivery_id"]
                    assert context["source"] == notice["ref"] == dict(record_id=source["record_id"], version=source["version"])
                    assert context["delivery_id"] == delivery_id_local == delivery_id
                    assert context["kind"] == "notice" and context["event_id"] == notice["event_id"]
                    if not attempt_claimed:
                        attempt_id = context["attempt_id"]
                        native_turn_id = context.get("native_turn_id")
                        hook_meta = ([json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
                                     if metadata_path.exists() else [])
                        assert native_turn_id, "inbox attempt lacks a native turn binding; observed hooks=" + json.dumps(
                            [{key: row.get(key) for key in ("event", "turn_id", "phase", "keys")}
                             for row in hook_meta])
                        assert native_turn_id in (actor_turn_id, *observed_turn_ids), \
                            "attempt is not bound to an observed native turn"
                        claim = api_call(api_role, "session-inbox-claim", raw=True, body=json.dumps(dict(
                            request_id=attempt_id,
                            session={key: context[key] for key in ("agent_id", "execution_id")},
                            native_turn_id=native_turn_id)))['attempt']
                        assert claim["attempt_id"] == attempt_id
                        assert claim["native_turn_id"] == native_turn_id
                        assert claim["delivery"]["delivery_id"] == delivery_id_local
                        attempt_claimed = True
                    if "source" not in results:
                        command = "printf %s " + shlex.quote(json.dumps(context["read_input"])) + " | " + shlex.join(context["read"])
                        item = _exec_tool("source", command)
                    elif "ack" not in results:
                        read_text = json.dumps(results["source"])
                        assert notice_body in read_text and source["record_id"] in read_text, \
                            "native turn did not read the exact full notice source"
                        source_read_confirmed = True
                        command = shlex.join(context["acknowledgement"])
                        item = _exec_tool("ack", command)
                    else:
                        ack_text = json.dumps(results["ack"])
                        state = api_call(event_owner, "event-status", notice["event_id"])["deliveries"][0]
                        assert "handled" in ack_text and state["state"] == "handled" and state["attempts"] == 1
                        settlement_confirmed = True
                        item = dict(type="message", id="msg_done", role="assistant", status="completed",
                            content=[dict(type="output_text", text="Build 81 completed; checksum 7b1a recorded.", annotations=[])])
                        finished.set()
                body = response_stream("resp_" + str(len(requests)), item)
            except Exception as exc:
                if trace is not None:
                    trace.record_actor_exchange("unexpected", ["tool_error"], stage=batch_stage)
                failures.append(type(exc).__name__)
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    if scenario == "batch":
        setup_stage("configure_responses")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    (profile / "config.toml").write_text(
        'model = "gpt-6-luna"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "Local fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n', encoding="utf-8")
    (profile / "config.toml").chmod(0o600)
    setup_done()

    def verify(rpc):
        nonlocal native_id, actor_turn_id, actor_turn_started, notice, source, delivery_id
        nonlocal actor_terminal_class
        nonlocal attempt_id, native_turn_id, timeout_evidence
        if scenario == "batch":
            setup_stage("registration_turn")
        started = rpc("thread/start", dict(cwd=str(actor_root), model=MODEL,
            approvalPolicy="never", sandbox="danger-full-access", ephemeral=True))
        native_id = started["thread"]["id"]
        registration = rpc("turn/start", dict(threadId=native_id,
            input=[dict(type="text", text="Register the native fixture session.", text_elements=[])]))
        registration_id = registration["turn"]["id"]
        deadline = time.monotonic() + turn_timeout
        registration_completed = False
        while time.monotonic() < deadline and not registration_completed:
            try:
                notification = rpc.next_notification(timeout=min(1, max(.1, deadline - time.monotonic())))
            except queue.Empty:
                continue
            params = notification.get("params", {})
            current = params.get("turn", {}).get("id") or params.get("turnId")
            if notification.get("method") == "turn/completed" and current == registration_id:
                registration_completed = True
        assert registration_completed, "native session registration turn did not complete"
        setup_done()

        # turn/completed can arrive before Codex's Stop command hook exits.
        # Do not tear down app-server or inspect the agent list until the
        # wrapper has recorded that hook's completion.
        if scenario == "batch":
            setup_stage("registration_hook_wait")
        _wait_for_registration_hook(stop_complete_path, registration_id, timeout=turn_timeout)
        setup_done()

        agents = api_call(api_role, "agents", "list", "--harness", "codex")["agents"]
        target = next(row for row in agents if row["native_session_id"] == native_id)
        assert target["metadata"]["state"] == "idle", "fixture notice must be published between turns"
        if scenario == "batch":
            setup_stage("publish_notices")
            for body in BATCH_BODIES:
                created = api_call("alice", "create", raw=True, body=json.dumps(dict(
                    request_id=str(uuid.uuid4()), draft=dict(kind="note", body=body,
                        sensitivity="shareable", claim_type="self",
                        scope=dict(repo=config["repo"], task_id="*", run_id="*")))))
                ref = dict(record_id=created["record_id"], version=created["version"])
                event = api_call("alice", "publish", "--request-id", str(uuid.uuid4()),
                    "--to", target["inbox"], "--kind", "notice", "--version",
                    str(created["version"]), created["record_id"])
                published = api_call(event_owner, "event-status", event["event_id"])
                assert len(published["deliveries"]) == 1
                pending = published["deliveries"][0]
                assert pending["state"] == "pending" and pending["attempts"] == 0
                batch_sources.append(ref)
                batch_notices.append(dict(event_id=event["event_id"], ref=ref,
                                          delivery_id=pending["delivery_id"]))
            notice, source, delivery_id = batch_notices[0], batch_sources[0], batch_notices[0]["delivery_id"]
            setup_done()
        else:
            created = api_call("alice", "create", raw=True, body=json.dumps(dict(
                request_id=str(uuid.uuid4()), draft=dict(kind="note", body=notice_body,
                    sensitivity="shareable", claim_type="self",
                    scope=dict(repo=config["repo"], task_id="*", run_id="*")))))
            source = created
            notice = api_call("alice", "publish", "--request-id", str(uuid.uuid4()),
                "--to", target["inbox"], "--kind", "notice", "--version",
                str(created["version"]), created["record_id"])
            published = api_call(event_owner, "event-status", notice["event_id"])
            assert len(published["deliveries"]) == 1, "fixture notice did not create exactly one delivery"
            pending = published["deliveries"][0]
            assert pending["state"] == "pending" and pending["attempts"] == 0
            delivery_id = pending["delivery_id"]

        if scenario == "batch":
            setup_stage("actor_turn_start")
        actor_turn_started = True
        if scenario == "batch":
            trace.record_setup_complete()
        task_text = actor_task_path.read_text(encoding="utf-8").strip()
        turn = rpc("turn/start", dict(threadId=native_id,
            input=[dict(type="text", text=task_text, text_elements=[])]))
        turn_id = turn["turn"]["id"]
        setup_done()
        actor_turn_id = turn_id
        observed_turn_ids.add(turn_id)
        active_deadline = rpc.arm_turn_deadline(native_id, turn_id, turn_timeout)
        deadline = time.monotonic() + turn_timeout + 5
        terminal = False
        while (time.monotonic() < deadline and not active_deadline.expired.is_set()
               and not (terminal and finished.is_set())):
            assert not failures, failures
            try:
                notification = rpc.next_notification(timeout=min(1, max(.1, deadline - time.monotonic())))
            except queue.Empty:
                continue
            params = notification.get("params", {})
            current = params.get("turn", {}).get("id") or params.get("turnId")
            if notification.get("method") == "turn/started" and current:
                observed_turn_ids.add(current)
            if notification.get("method") == "turn/completed" and current:
                completed_turn_ids.add(current)
                if current == turn_id or current == native_turn_id:
                    terminal = True
                    actor_terminal_class = "completed"
            elif current == turn_id and notification.get("method") in (
                    "turn/failed", "turn/cancelled", "turn/canceled"):
                actor_terminal_class = ("failed" if notification["method"] == "turn/failed"
                                        else "cancelled")
        if active_deadline.expired.is_set():
            cleanup_finished = active_deadline.wait_cleanup(5)
            release_stall.set()
            if mode != "stall":
                raise TimeoutError("native task turn exceeded its active deadline")
            assert cleanup_finished, "active timeout did not finish owned app-server cleanup"
            assert active_deadline.stop_error is None, active_deadline.stop_error
            assert stalled.is_set(), "stalled local Responses handler was not reached before timeout"
            assert notice is not None
            contexts = [json.loads(path.read_text(encoding="utf-8"))
                        for path in (state_dir / "inbox").glob("*.json")]
            context = next(row for row in contexts if row.get("event_id") == notice["event_id"])
            attempt_id = context["attempt_id"]
            native_turn_id = context.get("native_turn_id")
            assert native_turn_id == actor_turn_id
            claimed = api_call(api_role, "session-inbox-claim", raw=True, body=json.dumps(dict(
                request_id=attempt_id,
                session={key: context[key] for key in ("agent_id", "execution_id")},
                native_turn_id=native_turn_id)))['attempt']
            assert claimed["attempt_id"] == attempt_id and claimed["native_turn_id"] == native_turn_id
            assert claimed["delivery"]["delivery_id"] == delivery_id
            event_status = api_call(event_owner, "event-status", notice["event_id"])
            assert len(event_status["deliveries"]) == 1
            state = event_status["deliveries"][0]
            assert state["state"] == "leased" and state["attempts"] == 1, state
            timeout_evidence = dict(event_id=notice["event_id"], delivery_id=delivery_id,
                attempt_id=attempt_id, native_turn_id=native_turn_id,
                source=notice["ref"], delivery_state=state["state"], attempts=state["attempts"],
                settlement="not_performed", retry="not_attempted", outcome="unscored",
                cancel_error=active_deadline.cancel_error,
                cancel_error_code=active_deadline.cancel_error_code,
                cancel_error_message=active_deadline.cancel_error_message,
                stop_error=active_deadline.stop_error,
                owned_app_server_group_stopped=True)
            return
        active_deadline.disarm()
        assert terminal, "native Codex turn did not complete within its timeout"
        assert finished.is_set() and source_read_confirmed and settlement_confirmed, \
            "native turn did not read and settle its notice work"
        assert native_turn_id in observed_turn_ids and native_turn_id in completed_turn_ids, \
            "attempt native turn did not have observed start and completion notifications"
        if scenario == "batch":
            assert batch_item_index == 2 and len(requests) == 11
            for event in batch_notices[:3]:
                delivery = api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]
                assert delivery["delivery_id"] == event["delivery_id"]
                assert delivery["state"] == "handled" and delivery["attempts"] == 1
            pending = api_call(event_owner, "event-status", batch_notices[3]["event_id"])["deliveries"][0]
            assert pending["state"] == "pending" and pending["attempts"] == 0
        else:
            assert notice is not None and notice["ref"] == dict(record_id=source["record_id"], version=source["version"])
            event_status = api_call(event_owner, "event-status", notice["event_id"])
            assert len(event_status["deliveries"]) == 1, "fixture event created more than one delivery"
            state = event_status["deliveries"][0]
            assert state["delivery_id"] == delivery_id_local_for(notice) and state["state"] == "handled" and state["attempts"] == 1
    # Helpers read the exact observed context rather than caller-selected IDs.
    def delivery_id_local_for(event):
        return api_call(event_owner, "event-status", event["event_id"])["deliveries"][0]["delivery_id"]

    if scenario == "batch":
        setup_stage("start_actor_server")
    thread.start()
    setup_done()
    try:
        if scenario == "batch":
            setup_stage("register_hooks")
        installer.trust_codex_hooks(profile, settings, command, verify=verify,
                                    codex_binary=str(launcher), expected=6)
        assert not failures, failures
        if mode == "stall":
            assert timeout_evidence is not None
            final_status = api_call(event_owner, "event-status", timeout_evidence["event_id"])
            assert len(final_status["deliveries"]) == 1
            final_delivery = final_status["deliveries"][0]
            assert final_delivery["delivery_id"] == timeout_evidence["delivery_id"]
            assert final_delivery["state"] == "leased" and final_delivery["attempts"] == 1, final_delivery
            timeout_evidence["delivery_state_after_app_server_cleanup"] = final_delivery["state"]
            timeout_evidence["attempts_after_app_server_cleanup"] = final_delivery["attempts"]
            timeout_evidence["cancel_confirmed"] = timeout_evidence["cancel_error"] is None
            result = dict(schema=REPORT_SCHEMA, result="PASS", workflow_id="baseline.single_notice",
                mode="stall", native_hooks_registered=True, native_turn_joined=True,
                timeout_receipt=timeout_evidence, provider="local_responses_stub",
                provider_call=False, actor_decision="no_completed_actor_response",
                hidden_grade_exposed=False, outcome="unscored", runtime_timeout_proven=True,
                cancellation_confirmed=timeout_evidence["cancel_confirmed"],
                owned_app_server_group_stopped=True,
                settlement_performed=False, retry_performed=False)
            print(json.dumps(result, sort_keys=True))
            return result
        metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
        assert any(row.get("event") == "UserPromptSubmit" and row.get("turn_id") == native_turn_id for row in metadata)
        assert any(row.get("event") == "Stop" and row.get("turn_id") == native_turn_id for row in metadata)
        if scenario == "batch":
            trace_rows = [json.loads(line) for line in diagnostic_path.read_text(encoding="utf-8").splitlines()]
            actor_rows = [row for row in trace_rows if row.get("operation") == "actor_exchange"]
            assert len(actor_rows) == 11
            assert all(row.get("stage") in ("registration", "read_tool_call", "read_tool_result",
                "settle_tool_call", "settle_tool_result", "advance_tool_call",
                "advance_tool_result", "turn_terminal") for row in actor_rows)
            result = dict(schema=REPORT_SCHEMA, result="PASS", workflow_id="native.notice_batch",
                experimental_actor_fixture=True, task_outcome="unassessed",
                verified_store_boundary="first_three_notices_handled_fourth_pending",
                native_hooks_registered=True, native_turn_joined=True,
                scripted_response_count=len(actor_rows), diagnostic_rows=len(trace_rows),
                provider="local_responses_stub", provider_call=False,
                actor_decision="scripted_fixture_sequence")
            print(json.dumps(result, sort_keys=True))
            return result
        final_state = api_call(event_owner, "event-status", notice["event_id"])["deliveries"][0]
        assert final_state["state"] == "handled" and final_state["attempts"] == 1
        refs = dict(event_id=notice["event_id"], delivery_id=final_state["delivery_id"], attempt_id=attempt_id,
                    native_turn_id=native_turn_id, source=notice["ref"])
        result = dict(schema=REPORT_SCHEMA, result="PASS", workflow_id="baseline.single_notice",
            native_hooks_registered=True, exact_full_source_read=True, explicit_handled_settlement=True,
            one_event_one_delivery_one_attempt=True, native_turn_joined=True, response_refs=refs,
            synthetic_usage=dict(label="scripted_fixture_only", per_response=dict(input_tokens=1,
                output_tokens=1, total_tokens=2), response_count=len(requests)),
            provider="local_responses_stub", provider_call=False,
            actor_decision="scripted_fixture_sequence", hidden_grade_exposed=False,
            actor_workspace_files=["task.txt", ".codex"], runtime_timeout_proven=False,
            cancellation_confirmed=False, descendant_cleanup_confirmed=False)
        print(json.dumps(result, sort_keys=True))
        return result
    finally:
        release_stall.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if trace is not None:
            if scenario == "batch":
                exception_type = sys.exc_info()[0]
                exception = sys.exc_info()[1]
                if (exception is not None and not trace.setup_finished
                        and not trace.setup_failure_recorded):
                    filename = exception.filename if isinstance(exception, FileNotFoundError) else None
                    role = classify_setup_filename(filename, path_roles)
                    trace.record_setup_failure(type(exception).__name__, role)
                trace.record_fixture_failure(
                    exception_type.__name__ if exception_type is not None else None)
                try:
                    hook_rows = ([json.loads(line) for line in metadata_path.read_text(
                        encoding="utf-8").splitlines()] if metadata_path.exists() else [])
                except (OSError, ValueError):
                    hook_rows = []
                trace.record_hook_metadata(hook_rows)
                for slot in range(4):
                    if slot >= len(batch_notices):
                        trace.record_store_slot(slot + 1, status_class="not_created",
                            observed=False)
                        continue
                    try:
                        deliveries = api_call(event_owner, "event-status",
                                              batch_notices[slot]["event_id"]).get("deliveries", [])
                        delivery = deliveries[0] if len(deliveries) == 1 else None
                        if delivery is None:
                            trace.record_store_slot(slot + 1, status_class="unexpected",
                                observed=False)
                            continue
                        state = delivery.get("state")
                        attempts = delivery.get("attempts")
                        trace.record_store_slot(slot + 1, status_class="available",
                            observed=True, pending=state == "pending", leased=state == "leased",
                            handled=state == "handled", attempts_zero=attempts == 0,
                            attempted=(attempts > 0 if type(attempts) is int and attempts >= 0 else None))
                    except Exception:
                        trace.record_store_slot(slot + 1, status_class="unavailable",
                            observed=False)
                trace.record_actor_terminal(actor_terminal_class)
            trace.close()


def check(binary, root, config, api_call, candidate_root=None, sandbox_auth_file=None,
          turn_timeout=60, codex_entry=None, codex_root=None, mode="complete",
          scenario="single", diagnostic_output=None, api_role="bob"):
    """Prepare the private batch trace before running any fixture setup."""
    if scenario != "batch" or diagnostic_output is None:
        return _check_impl(binary, root, config, api_call, candidate_root,
                           sandbox_auth_file, turn_timeout, codex_entry, codex_root,
                           mode, scenario, diagnostic_output, api_role)
    from native_notice_batch_diagnostics import (
        AdvanceDiagnosticWriter,
        classify_setup_filename,
    )
    root_path = Path(root).resolve()
    root_path.mkdir(mode=0o700, parents=True, exist_ok=False)
    trace = AdvanceDiagnosticWriter(diagnostic_output, root_path)
    trace.record_setup_stage("create_trace_directory", "complete")
    trace.record_setup_stage("check_entry", "start")
    path_roles = _diagnostic_path_roles(binary, config, candidate_root, codex_entry,
                                        codex_root, root_path)
    try:
        return _check_impl(binary, root_path, config, api_call, candidate_root,
                           sandbox_auth_file, turn_timeout, codex_entry, codex_root,
                           mode, scenario, diagnostic_output, api_role,
                           preopened_trace=trace)
    except BaseException as exc:
        if (not trace.stream.closed and not trace.setup_finished
                and not trace.setup_failure_recorded):
            filename = exc.filename if isinstance(exc, FileNotFoundError) else None
            trace.record_setup_failure(type(exc).__name__,
                classify_setup_filename(filename, path_roles))
        if not trace.stream.closed:
            trace.close()
        raise
    finally:
        if not trace.stream.closed:
            trace.close()
