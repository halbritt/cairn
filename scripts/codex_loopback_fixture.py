"""Shared OpenAI Responses SSE loopback server and .codex staging fixture helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import threading
import time
from typing import Any, Callable


DEFAULT_MODEL = "gpt-6-luna"
CODEX_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
    "Interrupt",
    "PostToolUse",
)


def response_stream(
    ident: str,
    item: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> bytes:
    """Encode one OpenAI Responses API item as a complete SSE byte stream."""
    response = dict(
        id=ident,
        object="response",
        created_at=0,
        status="in_progress",
        output=[],
        model=model,
    )
    events: list[dict[str, Any]] = [
        dict(type="response.created", response=response),
        dict(
            type="response.output_item.added",
            output_index=0,
            item=dict(item, status="in_progress"),
        ),
    ]
    item_type = item.get("type")
    if item_type == "message":
        part = item["content"][0]
        events += [
            dict(
                type="response.content_part.added",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                part=dict(part, text=""),
            ),
            dict(
                type="response.output_text.delta",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                delta=part["text"],
            ),
            dict(
                type="response.output_text.done",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                text=part["text"],
            ),
            dict(
                type="response.content_part.done",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                part=part,
            ),
        ]
    elif item_type == "function_call":
        events.append(
            dict(
                type="response.function_call_arguments.done",
                item_id=item["id"],
                output_index=0,
                arguments=item["arguments"],
            )
        )
    elif item_type == "custom_tool_call":
        pass
    else:
        raise ValueError(f"unsupported response output item type: {item_type!r}")
    events += [
        dict(type="response.output_item.done", output_index=0, item=item),
        dict(
            type="response.completed",
            response=dict(
                response,
                status="completed",
                output=[item],
                usage=dict(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ),
            ),
        ),
    ]
    return "".join(
        "event: "
        + event["type"]
        + "\ndata: "
        + json.dumps(dict(event, sequence_number=i))
        + "\n\n"
        for i, event in enumerate(events)
    ).encode("utf-8")


def exec_tool_item(
    call_id: str,
    command: str,
    *,
    max_output_tokens: int = 3000,
) -> dict[str, Any]:
    """Construct a Codex custom_tool_call item for functions.exec (tools.exec_command)."""
    code = (
        "text(await tools.exec_command("
        + json.dumps(dict(cmd=command, max_output_tokens=max_output_tokens))
        + "));"
    )
    return dict(
        type="custom_tool_call",
        id="ctc_" + call_id,
        name="exec",
        namespace="functions",
        call_id=call_id,
        input=code,
    )


def function_call_item(
    call_id: str,
    name: str,
    arguments: dict[str, Any] | str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Construct a standard Responses API function_call output item."""
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return dict(
        type="function_call",
        id=item_id or ("fc_" + call_id),
        name=name,
        call_id=call_id,
        arguments=encoded,
        status="completed",
    )


def message_item(
    item_id: str,
    text: str,
    *,
    role: str = "assistant",
    status: str = "completed",
) -> dict[str, Any]:
    """Construct a Responses API assistant message output item."""
    return dict(
        type="message",
        id=item_id,
        role=role,
        status=status,
        content=[dict(type="output_text", text=text, annotations=[])],
    )


def custom_tool_outputs(request: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract custom_tool_call_output items keyed by call_id from a Responses request."""
    return {
        item["call_id"]: item
        for item in request.get("input", [])
        if isinstance(item, dict)
        and item.get("type") == "custom_tool_call_output"
        and isinstance(item.get("call_id"), str)
    }


def assert_exec_tool_declared(request: dict[str, Any]) -> None:
    """Assert that the request declares the functions.exec custom tool."""
    tools = next(
        item["tools"]
        for item in request.get("input", [])
        if isinstance(item, dict) and item.get("type") == "additional_tools"
    )
    namespace = next(tool for tool in tools if tool.get("name") == "functions")
    assert any(
        tool.get("name") == "exec" and "tools.exec_command" in tool.get("description", "")
        for tool in namespace.get("tools", [])
    )


@dataclass(frozen=True)
class LoopbackHTTPResponse:
    """Explicit HTTP error or early termination response from a loopback handler."""

    status: int
    message: str | None = None


class LoopbackResponsesServer:
    """Loopback HTTP server speaking the OpenAI Responses SSE and Models endpoints."""

    def __init__(
        self,
        handle_response: Callable[[dict[str, Any], dict[str, dict[str, Any]]], Any],
        *,
        model: str = DEFAULT_MODEL,
        response_id_prefix: str = "resp_",
        sequence_by_http_events: bool = False,
        max_requests: int = 8,
        max_body_bytes: int = 2_000_000,
        enable_models_endpoint: bool = False,
        max_model_requests: int = 3,
        enforce_bounds_via_http_status: bool = False,
        check_size_before_path: bool = False,
        request_timeout: float | None = None,
        caught_exceptions: tuple[type[BaseException], ...] = (Exception,),
        failure_format: str = "type_and_message",
        on_error: Callable[[BaseException], None] | None = None,
        after_response: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.handle_response = handle_response
        self.model = model
        self.response_id_prefix = response_id_prefix
        self.sequence_by_http_events = sequence_by_http_events
        self.max_requests = max_requests
        self.max_body_bytes = max_body_bytes
        self.enable_models_endpoint = enable_models_endpoint
        self.max_model_requests = max_model_requests
        self.enforce_bounds_via_http_status = enforce_bounds_via_http_status
        self.check_size_before_path = check_size_before_path
        self.request_timeout = request_timeout
        self.caught_exceptions = caught_exceptions
        self.failure_format = failure_format
        self.on_error = on_error
        self.after_response = after_response
        self.requests: list[dict[str, Any]] = []
        self.http_events: list[tuple[str, str]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.failures: list[str] = []
        self._closed = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_port)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def post_count(self) -> int:
        return sum(1 for method, _path in self.http_events if method == "POST")

    @property
    def get_count(self) -> int:
        return sum(1 for method, _path in self.http_events if method == "GET")


    def _format_failure(self, exc: BaseException) -> str:
        if self.failure_format == "type_only":
            return type(exc).__name__
        if self.failure_format == "repr":
            return repr(exc)
        return f"{type(exc).__name__}: {exc}"

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                if not outer.enable_models_endpoint:
                    self.send_error(404)
                    return
                if self.path != "/v1/models" or outer.get_count >= outer.max_model_requests:
                    self.send_error(404)
                    return
                outer.http_events.append(("GET", "/v1/models"))
                body = json.dumps(
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": outer.model,
                                "object": "model",
                                "created": 0,
                                "owned_by": "fixture",
                            }
                        ],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if outer.request_timeout is not None:
                    self.connection.settimeout(outer.request_timeout)
                size = int(self.headers.get("Content-Length", "0"))
                if outer.enforce_bounds_via_http_status:
                    if outer.check_size_before_path:
                        if not 0 < size <= outer.max_body_bytes or len(outer.requests) >= outer.max_requests:
                            self.send_error(413)
                            return
                        raw = self.rfile.read(size)
                        if self.path != "/v1/responses":
                            self.send_error(404)
                            return
                    else:
                        if (
                            self.path != "/v1/responses"
                            or not 0 < size <= outer.max_body_bytes
                            or outer.post_count >= outer.max_requests
                        ):
                            self.send_error(413)
                            return
                        raw = self.rfile.read(size)
                    request = json.loads(raw)
                    outer.requests.append(request)
                    outer.http_events.append(("POST", "/v1/responses"))
                try:
                    if not outer.enforce_bounds_via_http_status:
                        assert self.path == "/v1/responses", "unexpected local Responses path"
                        assert 0 < size <= outer.max_body_bytes, "unexpected request size"
                        assert len(outer.requests) < outer.max_requests, "local Responses fixture bounds exceeded"
                        request = json.loads(self.rfile.read(size))
                        outer.requests.append(request)
                        outer.http_events.append(("POST", "/v1/responses"))
                    current_outputs = custom_tool_outputs(request)
                    outer.results.update(current_outputs)
                    outcome = outer.handle_response(request, current_outputs)
                    if outcome is None:
                        return
                    if isinstance(outcome, LoopbackHTTPResponse):
                        if outcome.message is not None:
                            self.send_error(outcome.status, outcome.message)
                        else:
                            self.send_error(outcome.status)
                        return
                    if isinstance(outcome, tuple):
                        item, input_tokens = outcome
                    else:
                        item, input_tokens = outcome, 1
                    seq = len(outer.http_events) if outer.sequence_by_http_events else len(outer.requests)
                    body = response_stream(
                        f"{outer.response_id_prefix}{seq}",
                        item,
                        model=outer.model,
                        input_tokens=input_tokens,
                    )
                except outer.caught_exceptions as exc:
                    if outer.on_error is not None:
                        outer.on_error(exc)
                    outer.failures.append(outer._format_failure(exc))
                    self.send_error(500)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if outer.after_response is not None:
                    outer.after_response(item)

        return Handler


    def start(self) -> "LoopbackResponsesServer":
        if not self.thread.is_alive():
            self.thread.start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def __enter__(self) -> "LoopbackResponsesServer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def write_config_toml(
        self,
        profile_dir: Path | str,
        *,
        include_model: bool = True,
        provider_name: str = "Loopback fixture",
        reasoning_effort: str | None = None,
        check_for_update_on_startup: bool | None = None,
        model_context_window: int | None = None,
        model_auto_compact_token_limit: int | None = None,
    ) -> Path:
        return write_loopback_config_toml(
            profile_dir,
            self.port,
            model=self.model if include_model else None,
            provider_name=provider_name,
            reasoning_effort=reasoning_effort,
            check_for_update_on_startup=check_for_update_on_startup,
            model_context_window=model_context_window,
            model_auto_compact_token_limit=model_auto_compact_token_limit,
        )


def write_private_file(path: Path | str, data: str, *, exclusive: bool = False) -> Path:
    """Write a private 0600 file and fsync it before closing."""
    target = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    fd = os.open(target, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def write_loopback_config_toml(
    profile_dir: Path | str,
    port: int,
    *,
    model: str | None = DEFAULT_MODEL,
    provider_name: str = "Loopback fixture",
    reasoning_effort: str | None = None,
    check_for_update_on_startup: bool | None = None,
    model_context_window: int | None = None,
    model_auto_compact_token_limit: int | None = None,
) -> Path:
    """Write a private config.toml pointing Codex at the local loopback Responses stub."""
    lines: list[str] = []
    if model is not None:
        lines.append(f'model = "{model}"')
    lines.append('model_provider = "fixture"')
    if reasoning_effort is not None:
        lines.append(f'model_reasoning_effort = "{reasoning_effort}"')
    if check_for_update_on_startup is not None:
        flag = "true" if check_for_update_on_startup else "false"
        lines.append(f"check_for_update_on_startup = {flag}")
    if model_context_window is not None:
        lines.append(f"model_context_window = {model_context_window}")
    if model_auto_compact_token_limit is not None:
        lines.append(f"model_auto_compact_token_limit = {model_auto_compact_token_limit}")
    lines += [
        "[model_providers.fixture]",
        f'name = "{provider_name}"',
        f'base_url = "http://127.0.0.1:{port}/v1"',
        'wire_api = "responses"',
        "requires_openai_auth = false",
        "request_max_retries = 0",
        "stream_max_retries = 0",
    ]
    config_path = Path(profile_dir) / "config.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config_path.chmod(0o600)
    return config_path


def prepare_synthetic_auth_fixture(
    profile_dir: Path | str,
    sandbox_auth_file: Path | str | None = None,
    *,
    filename: str = "synthetic-auth-fixture.json",
    api_key: str | None = None,
) -> Path:
    """Create or validate an owner-only synthetic auth JSON file in profile_dir."""
    profile_path = Path(profile_dir)
    if sandbox_auth_file is None:
        payload = "{}\n" if api_key is None else json.dumps({"OPENAI_API_KEY": api_key}) + "\n"
        return write_private_file(profile_path / filename, payload)
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
    return auth_fixture


def load_script_module(path: Path | str, name: str):
    """Load a sibling script or module by path."""
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_coordination_installer(name: str = "codex_coordination_installer"):
    """Load scripts/install-agent-coordination.py as a module."""
    return load_script_module(
        Path(__file__).with_name("install-agent-coordination.py"),
        name,
    )


def stage_hook_metadata_wrapper(profile_dir: Path | str) -> Path:
    """Stage record_codex_hook_metadata.py and codex_fixture_diagnostic.py into profile_dir."""
    source_dir = Path(__file__).resolve().parent
    profile_path = Path(profile_dir)
    wrapper = profile_path / "record_codex_hook_metadata.py"
    helper = profile_path / "codex_fixture_diagnostic.py"
    shutil.copyfile(source_dir / wrapper.name, wrapper)
    shutil.copyfile(source_dir / helper.name, helper)
    wrapper.chmod(0o600)
    helper.chmod(0o600)
    return wrapper


def wrap_hook_commands(
    settings_path: Path | str,
    *,
    metadata_path: Path | str,
    stop_complete_path: Path | str,
    wrapper_path: Path | str | None = None,
    diagnostic_path: Path | str | None = None,
    include_hook_kind: bool = False,
    match_targets: tuple[str, ...] = ("coordination.py",),
    path_replacements: tuple[tuple[str, str], ...] = (),
) -> dict[str, str]:
    """Rewrite hooks.json to wrap matching hook commands with record_codex_hook_metadata.py."""
    settings_file = Path(settings_path)
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    if path_replacements:
        def _replace(value: Any) -> Any:
            if isinstance(value, str):
                for old, new in path_replacements:
                    value = value.replace(old, new)
                return value
            if isinstance(value, list):
                return [_replace(item) for item in value]
            if isinstance(value, dict):
                return {k: _replace(v) for k, v in value.items()}
            return value
        settings = _replace(settings)
    wrapper = Path(wrapper_path) if wrapper_path is not None else Path(__file__).with_name(
        "record_codex_hook_metadata.py"
    ).resolve()
    wrapped_commands: dict[str, str] = {}
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                original = hook.get("command", "")
                if "--config" not in original or not any(t in original for t in match_targets):
                    continue
                hook_kind = "coordination" if "coordination.py" in original else "memory"
                prefix = [
                    sys.executable,
                    str(wrapper),
                    "--metadata",
                    str(metadata_path),
                    "--stop-complete",
                    str(stop_complete_path),
                ]
                if include_hook_kind:
                    prefix += ["--hook-kind", hook_kind]
                if diagnostic_path is not None and hook_kind == "coordination":
                    prefix += ["--diagnostic-summary", str(diagnostic_path)]
                command = shlex.join([*prefix, "--", *shlex.split(original)])
                hook["command"] = command
                wrapped_commands.setdefault(hook_kind, command)
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    settings_file.chmod(0o600)
    return wrapped_commands


def stage_codex_binding_and_hooks(
    engine_root: Path | str,
    profile_dir: Path | str,
    *,
    binding: str,
    config_data: dict[str, Any],
    hook_script: Path | str,
    python_executable: str = "/usr/bin/python3",
) -> tuple[Path, str]:
    """Write a private Codex binding config and matching 6-event hooks.json."""
    engine_path = Path(engine_root)
    profile_path = Path(profile_dir)
    bindings = engine_path / "bindings"
    state_dir = engine_path / "state"
    bindings.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = bindings / f"{binding}.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    config_path.chmod(0o600)
    command = f"{python_executable} {hook_script} hook --config {config_path}"
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event in CODEX_HOOK_EVENTS:
        timeout = 2 if event in ("SessionEnd", "Interrupt", "PostToolUse") else 15
        hooks[event] = [{"hooks": [{"type": "command", "command": command, "timeout": timeout}]}]
    hooks_path = profile_path / "hooks.json"
    hooks_path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    hooks_path.chmod(0o600)
    return config_path, command


def trust_codex_hooks_via_rpc(
    rpc: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    workspace: Path | str,
    profile_dir: Path | str,
    command: str,
    expected: int = 6,
) -> list[dict[str, Any]]:
    """Trust matching Codex hooks on an active app-server RPC connection."""
    profile_path = Path(profile_dir)
    hooks_file = str(profile_path / "hooks.json")
    config_toml = str(profile_path / "config.toml")
    cwds = [str(workspace)]
    listed = rpc("hooks/list", dict(cwds=cwds))
    selected = [
        hook
        for entry in listed.get("data", [])
        for hook in entry.get("hooks", [])
        if hook.get("sourcePath") == hooks_file and hook.get("command") == command
    ]
    if len(selected) != expected:
        raise RuntimeError("codex_hook_definitions_missing")
    for hook in selected:
        rpc(
            "config/value/write",
            dict(
                filePath=config_toml,
                keyPath="hooks.state." + json.dumps(hook["key"]) + ".trusted_hash",
                value=hook["currentHash"],
                mergeStrategy="replace",
            ),
        )
    verified = rpc("hooks/list", dict(cwds=cwds))
    keys = {item["key"] for item in selected}
    loaded = [
        hook
        for entry in verified.get("data", [])
        for hook in entry.get("hooks", [])
        if hook.get("key") in keys
    ]
    if len(loaded) != expected or not all(
        hook.get("enabled") and hook.get("trustStatus") == "trusted" for hook in loaded
    ):
        raise RuntimeError("codex_hooks_not_trusted")
    return loaded


def wait_for_registration_hook(
    stop_complete_path: Path | str,
    turn_id: str,
    timeout: float = 30,
) -> dict[str, Any]:
    """Wait for the registration turn's Stop hook marker to complete with exit_code 0."""
    marker_path = Path(stop_complete_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            completed = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if completed.get("event") != "Stop" or completed.get("turn_id") != turn_id:
            raise AssertionError("registration Stop hook marker does not match the registration turn")
        if completed.get("exit_code") != 0:
            raise AssertionError("registration Stop hook returned nonzero")
        return completed
    raise TimeoutError("registration Stop hook did not complete before fixture lookup")
