#!/usr/bin/env python3
"""Observe Codex's first local Responses request and Cairn MCP declarations.

The selected Codex CLI is configured only for a loopback fixture with OpenAI
authentication disabled. The fixture returns HTTP 418 immediately. Request
bodies are parsed in memory and never written; only byte/token counts, names,
hashes, and validation booleans are printed. This is not a model inference.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from codex_app_server_session import (  # noqa: E402
    AppServerError, AppServerTimeout, AppServerTransportError, CodexAppServerSession,
)

TARGETS = {"cairn_search", "cairn_pull"}
EXPECTED_MCP_TOOLS = {
    "cairn_search", "cairn_pull", "cairn_pull_evidence", "cairn_remember",
    "cairn_prepare_note", "cairn_edit", "cairn_history", "cairn_assess",
    "cairn_assessments", "cairn_client_info",
}
EXPECTED_CODEX_NAMESPACES = ["clock", "collaboration", "functions"]
STOP_STATUS = 418
MODEL = "gpt-6-luna"
PROFILE = "compact/1"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validation_contract(value):
    """Drop prose annotations while keeping every argument constraint."""
    if isinstance(value, dict):
        return {key: validation_contract(item) for key, item in value.items()
                if key != "description"}
    if isinstance(value, list):
        return [validation_contract(item) for item in value]
    return value


def _clean_environment(home: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TERM") if key in os.environ}
    env.update({"HOME": str(home), "CAIRN_HOME": str(home),
                "CAIRN_DATABASE_URL": "host=/absent dbname=denied"})
    return env


def request_declarations(body: dict) -> tuple[list[dict], int]:
    """Extract exactly the tool descriptors Codex puts in additional_tools."""
    inputs = body.get("input")
    require(isinstance(inputs, list), "request input is not an array")
    additional = [item for item in inputs if isinstance(item, dict)
                  and item.get("type") == "additional_tools"]
    require(len(additional) == 1, "expected one input[].additional_tools item")
    tools = additional[0].get("tools")
    require(isinstance(tools, list), "additional_tools.tools is missing")
    names = [item.get("name") for item in tools if isinstance(item, dict)]
    require(len(names) == len(tools) and all(isinstance(name, str) for name in names),
            "additional_tools namespace inventory changed")
    return tools, len(canonical(additional[0]))


def nested_tool_names(namespaces: list[dict]) -> list[str]:
    names = []
    for namespace in namespaces:
        nested = namespace.get("tools")
        require(isinstance(nested, list), "additional-tools namespace omitted its tool list")
        for tool in nested:
            require(isinstance(tool, dict) and isinstance(tool.get("name"), str),
                    "additional-tools function identity changed")
            names.append(tool["name"])
    require(len(names) == len(set(names)), "duplicate additional-tools function name")
    return sorted(names)


def codex_mcp_inventory(env: dict[str, str]) -> list[dict]:
    """Read Codex's local MCP registry without starting a model thread."""
    def inventory_error(exc: Exception) -> ValueError:
        if isinstance(exc, AppServerTimeout):
            return ValueError("Codex app-server MCP inventory timed out")
        if isinstance(exc, AppServerTransportError):
            return ValueError("Codex app-server closed before response")
        return ValueError("Codex app-server MCP inventory failed")

    try:
        session = CodexAppServerSession.spawn(
            [shutil.which("codex") or "codex", "app-server", "--listen", "stdio://"],
            client_name="cairn-schema-probe", client_title="Local schema probe",
            experimental_api=False, initialized_params={}, jsonrpc=True, env=env,
            default_timeout=20)
    except AppServerError as exc:
        raise inventory_error(exc) from None
    try:
        try:
            result = session.call("mcpServerStatus/list", {"serverName": "cairn", "detail": "full"})
        except AppServerError as exc:
            raise inventory_error(exc) from None
        response = dict(result=result)
        rows = response.get("result", {}).get("data")
        require(isinstance(rows, list), "Codex MCP registry response missing")
        row = next((item for item in rows if item.get("name") == "cairn"), None)
        require(isinstance(row, dict), "Codex MCP server is not registered")
        require(not row.get("toolsError"), "Codex MCP tool inventory reports an error")
        tools = row.get("tools")
        require(isinstance(tools, dict) and tools, "Codex MCP tool schemas are unavailable")
        result = list(tools.values())
        require(all(isinstance(tool, dict) and isinstance(tool.get("name"), str)
                    and isinstance(tool.get("inputSchema"), dict) for tool in result),
                "Codex MCP tool descriptor shape changed")
        require(set(tool["name"] for tool in result) == EXPECTED_MCP_TOOLS,
                "Codex MCP tool names changed; review before updating expectations")
        return result
    finally:
        session.close()


def local_request(binary: Path, parent: Path, profile: str) -> tuple[dict, list[dict]]:
    """Run one Codex turn against the local-only stub and discard its body."""
    root = parent / profile.replace("/", "-")
    root.mkdir(mode=0o700)
    home, work = root / "codex-home", root / "work"
    home.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    token = root / "fixture.token"
    token.write_text("local fixture only\n", encoding="utf-8")
    token.chmod(0o600)
    config_args = [str(binary), "codex-config", "--socket", str(root / "no-api.sock"),
                   "--token-file", str(token), "--repo", "fixture:cairn-schema-request-shape",
                   "--task", "request-shape", "--run", profile.replace("/", "-")]
    if profile != "full":
        config_args.extend(("--tool-profile", profile))
    mcp = tomllib.loads(subprocess.run(config_args, check=True, capture_output=True,
                                       text=True, timeout=10).stdout)["mcp_servers"]["cairn"]

    captured: list[tuple[str, str, dict]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            require(0 < length <= 5_000_000, "unexpected request size")
            host = self.headers.get("Host", "")
            require(host.startswith("127.0.0.1:"), "request was not addressed to loopback")
            require(self.path == "/v1/responses", "unexpected local request path")
            require(not captured, "more than one local request")
            body = json.loads(self.rfile.read(length))
            require(isinstance(body, dict) and body.get("model") == MODEL,
                    "model or request shape drifted")
            captured.append((self.path, host, body))
            payload = b'{"error":{"message":"local fixture stops before inference"}}'
            self.send_response(STOP_STATUS)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    config = ("model = \"gpt-6-luna\"\nmodel_reasoning_effort = \"low\"\n"
              "model_provider = \"fixture\"\ncheck_for_update_on_startup = false\n"
              "cli_auth_credentials_store = \"ephemeral\"\n"
              "[model_providers.fixture]\nname = \"Local fixture\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "wire_api = \"responses\"\nrequires_openai_auth = false\n"
              "supports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n"
              "[mcp_servers.cairn]\n"
              f"command = {json.dumps(mcp['command'])}\n"
              f"args = {json.dumps(mcp['args'])}\n"
              "enabled = true\nrequired = true\n"
              f"enabled_tools = {json.dumps(mcp['enabled_tools'])}\n")
    config_path = home / "config.toml"
    config_path.write_text(config, encoding="utf-8")
    config_path.chmod(0o600)
    env = _clean_environment(root)
    env["CODEX_HOME"] = str(home)
    registry_tools = codex_mcp_inventory(env)
    command = [shutil.which("codex") or "codex", "exec", "--ephemeral", "--json",
               "--skip-git-repo-check", "--cd", str(work), "--sandbox", "read-only",
               "--model", MODEL, "--", "Return the single word OK."]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    require(len(captured) == 1, "expected exactly one loopback request")
    path, host, body = captured[0]
    require(result.returncode != 0, "Codex unexpectedly treated the stop fixture as success")
    tools, additional_bytes = request_declarations(body)
    names = sorted(tool["name"] for tool in tools)
    require(names == EXPECTED_CODEX_NAMESPACES,
            "Codex additional-tools namespace inventory changed")
    function_names = nested_tool_names(tools)
    require(not any("cairn" in name.lower() for name in [*names, *function_names]),
            "Cairn schema appeared in the initial request; review before updating expectations")
    request_bytes = len(canonical(body))
    # Keep only sanitized aggregate values, then release the body reference.
    output = {
        "profile": profile or "full",
        "endpoint": path,
        "model_label": body["model"],
        "serialized_request_bytes": request_bytes,
        "additional_tools_position": "input[].additional_tools.tools[]",
        "additional_tools_bytes": additional_bytes,
        "additional_tool_names": names,
        "additional_tool_names_sha256": digest(canonical(names)),
        "nested_tool_names": function_names,
        "nested_tool_names_sha256": digest(canonical(function_names)),
        "cairn_schema_present": False,
        "local_stop_status": STOP_STATUS,
        "local_request_count": 1,
        "external_provider_called": False,
        "provider_auth_configured": False,
        "raw_request_saved": False,
    }
    del body, captured[:]
    return output, registry_tools


def schema_summary(tools: list[dict], tokenizer) -> dict:
    by_name = {tool["name"]: tool for tool in tools}
    require(len(by_name) == len(tools), "duplicate MCP tool identity")
    require(TARGETS <= set(by_name), "target MCP tools missing")
    result = {}
    for name in sorted(TARGETS):
        tool = by_name[name]
        serialized = canonical(tool)
        contract = canonical(validation_contract(tool["inputSchema"]))
        description = tool.get("description", "")
        require(isinstance(description, str), "MCP description type changed")
        result[name] = {
            "declared_json_bytes": len(serialized),
            "declared_o200k_proxy_tokens": len(tokenizer.encode(serialized.decode("utf-8"))),
            "declared_schema_sha256": digest(serialized),
            "argument_contract_sha256": digest(contract),
            "description_bytes": len(description.encode("utf-8")),
        }
    return result


def run() -> dict:
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError("run with uv --with tiktoken==0.12.0") from exc
    codex = shutil.which("codex")
    require(codex is not None, "Codex CLI unavailable")
    cache = Path.home() / ".cache" / "cairn-codex-shape-probe"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cairn-codex-shape-", dir=cache) as directory:
        root = Path(directory)
        binary = root / "cairn"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/cairn"],
                       cwd=ROOT, check=True, timeout=180)
        binary_hash = digest(binary.read_bytes())
        full, full_registry = local_request(binary, root, "full")
        compact, compact_registry = local_request(binary, root, PROFILE)
    encoder = tiktoken.get_encoding("o200k_base")
    full_by_name = {tool["name"]: tool for tool in full_registry}
    compact_by_name = {tool["name"]: tool for tool in compact_registry}
    require(set(full_by_name) == EXPECTED_MCP_TOOLS,
            "source MCP tool inventory changed; review before updating expectations")
    require(set(full_by_name) == set(compact_by_name), "full/compact MCP tool identities differ")
    require(all(canonical(validation_contract(full_by_name[name]["inputSchema"]))
                == canonical(validation_contract(compact_by_name[name]["inputSchema"]))
                for name in full_by_name), "full/compact MCP argument contract differs")
    require(full["additional_tool_names"] == compact["additional_tool_names"],
            "full/compact Codex additional-tools identities differ")
    require(full["nested_tool_names"] == compact["nested_tool_names"],
            "full/compact Codex nested additional-tools identities differ")
    require(full["additional_tools_bytes"] == compact["additional_tools_bytes"],
            "full/compact first-request additional-tools bytes differ unexpectedly")
    return {
        "schema": "cairn.codex-local-schema-exposure/1",
        "codex_cli_version": subprocess.run([codex, "--version"], check=True,
                                             capture_output=True, text=True,
                                             timeout=10).stdout.strip(),
        "source_revision": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                           check=True, capture_output=True, text=True,
                                           timeout=10).stdout.strip(),
        "cairn_source_binary_sha256": binary_hash,
        "tokenizer_proxy": "tiktoken o200k_base; not a Codex/provider tokenizer claim",
        "transport": "isolated CODEX_HOME; custom local-only Responses stub; no API key",
        "mcp_static_declarations": {
            "tool_names": sorted(full_by_name),
            "tool_names_equal": True,
            "argument_contracts_equal": True,
            "codex_registry_source": "mcpServerStatus/list; no thread or provider request",
            "full_targets": schema_summary(full_registry, encoder),
            "compact_targets": schema_summary(compact_registry, encoder),
        },
        "first_request": full,
        "compact_first_request": compact,
        "first_request_mcp_discovery": "no Cairn schema in input[].additional_tools; schema exposure not observed",
        "comparison_boundary": "static MCP definitions compare; Codex first model request contains no Cairn declarations",
        "provider_usage": "unmeasured",
        "quota_or_capacity": "unmeasured",
        "accepted_task_outcome": "unassessed",
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
