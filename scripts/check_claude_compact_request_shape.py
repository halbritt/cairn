#!/usr/bin/env python3
"""Capture Claude's outbound MCP declarations at a local Messages stub.

Only aggregate tool data leaves memory. Both homes use a fake API key, private
MCP configuration and a loopback endpoint; no Cairn API or provider is called.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from check_claude_tools import response_events


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("mcp__cairn__cairn_search", "mcp__cairn__cairn_pull")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def without_descriptions(value):
    if isinstance(value, dict):
        return {key: without_descriptions(item) for key, item in value.items()
                if key != "description"}
    if isinstance(value, list):
        return [without_descriptions(item) for item in value]
    return value


def capture(claude, cairn, root, profile):
    label = profile.replace("/", "-") if profile else "full"
    area = root / label
    area.mkdir(mode=0o700)
    home, config_home, work = (area / name for name in ("home", "config", "work"))
    for directory in (home, config_home, work):
        directory.mkdir(mode=0o700)
    token = area / "fake.token"
    token.write_text("private-local-fixture-only", encoding="utf-8")
    token.chmod(0o600)
    socket = area / "api-never-started.sock"
    require(not socket.exists(), "fixture socket unexpectedly exists")

    flags = [str(cairn), "claude-config", "--socket", str(socket),
             "--token-file", str(token), "--repo", "fixture:cairn-schema-shape",
             "--task", "request-shape", "--run", label]
    if profile:
        flags.extend(("--tool-profile", profile))
    rendered = subprocess.run(flags, text=True, capture_output=True,
                              check=True, timeout=10).stdout
    config = json.loads(rendered)["mcpServers"]["cairn"]
    profile_values = [config["args"][index + 1]
                      for index, value in enumerate(config["args"][:-1])
                      if value == "--tool-profile"]
    require(config["type"] == "stdio" and config["command"] == str(cairn)
            and config["args"][0] == "mcp"
            and profile_values == ([profile] if profile else [])
            and "--search-view" not in config["args"],
            "generated MCP configuration changed profile or view")
    config_file = area / "mcp.json"
    config_file.write_text(rendered, encoding="utf-8")
    config_file.chmod(0o600)

    requests, failures = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                require(self.path.split("?", 1)[0] == "/v1/messages"
                        and 0 < size <= 1_000_000 and not requests,
                        "unexpected loopback request or request count")
                body = json.loads(self.rfile.read(size))
                require(isinstance(body, dict) and isinstance(body.get("tools"), list)
                        and isinstance(body.get("model"), str),
                        "Claude request omitted tools or model")
                requests.append({"tools": body["tools"], "model": body["model"]})
                output = response_events(
                    "msg_cairn_schema_fixture", body["model"],
                    {"type": "text", "text": "Local schema fixture complete."})
            except (AssertionError, ValueError, TypeError, OSError) as exc:
                failures.append(type(exc).__name__)
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(config_home),
               ANTHROPIC_API_KEY="fixture-only",
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               ENABLE_TOOL_SEARCH="false")
    command = [str(claude), "--bare", "--print", "--verbose",
               "--output-format", "stream-json", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config",
               "--mcp-config", str(config_file), "--tools", "",
               "--permission-mode", "dontAsk", "--max-turns", "1",
               "--model", "sonnet", "--",
               "Return the local scripted response. No tools are needed."]
    thread.start()
    try:
        result = subprocess.run(command, cwd=work, env=env, text=True,
                                capture_output=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    require(result.returncode == 0 and len(result.stdout) < 1_000_000
            and len(result.stderr) < 100_000 and not failures
            and len(requests) == 1 and not socket.exists(),
            f"Claude local request failed: exit={result.returncode}, "
            f"requests={len(requests)}, failures={failures}")
    return requests[0]


def check():
    claude = shutil.which("claude")
    require(claude is not None, "installed Claude CLI required")
    with tempfile.TemporaryDirectory(prefix="cairn-claude-schema-") as path:
        root = Path(path)
        cairn = root / "cairn"
        subprocess.run(["go", "build", "-o", str(cairn), "./cmd/cairn"],
                       cwd=ROOT, check=True, timeout=180)
        binary_sha256 = sha256(cairn.read_bytes())
        claude_version = subprocess.run([claude, "--version"], check=True,
                                        capture_output=True, text=True,
                                        timeout=10).stdout.strip()
        full = capture(claude, cairn, root, "")
        compact = capture(claude, cairn, root, "compact/1")

        def declarations(request):
            names = [tool["name"] for tool in request["tools"]]
            require(len(names) == len(set(names)), "duplicate provider tool name")
            return {tool["name"]: tool for tool in request["tools"]}

        before, after = declarations(full), declarations(compact)
        require(full["model"] == compact["model"]
                and set(before) == set(after)
                and all(name in before for name in TARGETS),
                "model or available tool set drifted")
        for name in before:
            require(without_descriptions(before[name])
                    == without_descriptions(after[name]),
                    f"{name}: validation shape changed")
            if name not in TARGETS:
                require(before[name] == after[name],
                        f"{name}: unrelated tool declaration changed")
        reductions = {}
        for name in TARGETS:
            old, new = canonical(before[name]), canonical(after[name])
            require(len(new) < len(old), f"{name}: compact tool did not shrink")
            reductions[name.removeprefix("mcp__cairn__")] = {
                "full_bytes": len(old), "compact_bytes": len(new),
                "full_sha256": sha256(old), "compact_sha256": sha256(new)}
        report = {
            "schema": "cairn.claude-loopback-schema-exposure/1",
            "candidate_binary_sha256": binary_sha256,
            "claude_version": claude_version,
            "model_label": full["model"],
            "request_count_per_profile": 1,
            "tool_count": len(before),
            "tool_names_equal": True,
            "validation_shapes_equal": True,
            "other_declarations_equal": True,
            "retrieval": reductions,
            "transport": "local_messages_stub_fake_key",
            "provider_called": False,
            "subscription_request_observed": False,
            "provider_usage": "unmeasured",
            "actor_task_outcome": "unassessed",
        }
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    check()
