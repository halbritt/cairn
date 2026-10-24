#!/usr/bin/env python3
"""Observe Cairn's real OpenCode tool declarations at a loopback provider.

The request is held only in memory. The tool adapter is installed in a private
temporary project; no tool is executed and no Cairn API or external provider is
contacted.
"""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading


EXPECTED = {
    "cairn_search", "cairn_prepare_note", "cairn_history", "cairn_assessments",
    "cairn_assess", "cairn_pull", "cairn_pull_evidence", "cairn_remember", "cairn_edit",
}
CANARY = "CAIRN-225-LOCAL-REQUEST-SHAPE"
EXPECTED_VALIDATION_SET_SHA256 = "08968bbea4606c355440d273d826368c28eeb36ddfbde743005042eba82d977c"
EXPECTED_DECLARATION_SET_SHA256 = "7eeee7a8157975de8fdb172d6b3041b7b1870ffd797b4f4270de4c6337018aff"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def summarize_tools(request):
    tools = request.get("tools")
    if not isinstance(tools, list):
        raise AssertionError("first tool request omitted its tool list")
    by_name = {}
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise AssertionError("request contains an unrecognized tool declaration")
        name = function["name"]
        if name in by_name:
            raise AssertionError(f"duplicate tool declaration: {name}")
        by_name[name] = (tool, function)
    cairn = {name: declaration for name, declaration in by_name.items() if name.startswith("cairn_")}
    if set(cairn) != EXPECTED:
        raise AssertionError(f"Cairn tool availability differs: {sorted(cairn)}")
    declarations = {}
    complete_declarations = {}
    validation_shapes = {}
    for name, (declaration, function) in sorted(cairn.items()):
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            raise AssertionError(f"{name} has no JSON parameter schema")
        declarations[name] = {
            "canonical_json_utf8_bytes": len(canonical(declaration)),
            "schema_sha256": hashlib.sha256(canonical(parameters)).hexdigest(),
            "schema_json_utf8_bytes": len(canonical(parameters)),
        }
        complete_declarations[name] = declaration
        validation_shapes[name] = parameters
    validation_set_sha256 = hashlib.sha256(canonical(validation_shapes)).hexdigest()
    if validation_set_sha256 != EXPECTED_VALIDATION_SET_SHA256:
        raise AssertionError(
            "Cairn validation schema hash changed; review the request shape before updating the fixture"
        )
    declaration_set_sha256 = hashlib.sha256(canonical(complete_declarations)).hexdigest()
    if declaration_set_sha256 != EXPECTED_DECLARATION_SET_SHA256:
        raise AssertionError(
            "Cairn declaration hash changed; review the request shape before updating the fixture"
        )
    return {
        "all_tool_names": sorted(by_name),
        "cairn_tool_names": sorted(cairn),
        "cairn_tool_count": len(cairn),
        "cairn_declarations_canonical_json_utf8_bytes": sum(
            item["canonical_json_utf8_bytes"] for item in declarations.values()),
        "declarations": declarations,
        "declaration_set_sha256": declaration_set_sha256,
        "validation_set_sha256": validation_set_sha256,
    }


def check(binary, output):
    binary = Path(binary).resolve(strict=True)
    adapter = Path(__file__).resolve().parents[1] / "integrations/opencode/cairn.ts"
    canary_requests = []
    canary_request_count = 0
    failures = []

    class Fixture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal canary_request_count
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length <= 2_000_000:
                failures.append("unexpected route or body size")
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(length))
            if CANARY in json.dumps(request.get("messages", [])):
                canary_request_count += 1
                if request.get("tools"):
                    try:
                        canary_requests.append(summarize_tools(request))
                    except AssertionError as exc:
                        failures.append(str(exc))
                        self.send_error(400)
                        return
                else:
                    canary_requests.append({"all_tool_names": [], "cairn_tool_names": [],
                                            "cairn_tool_count": 0})
            # Return text only. The client performs no tool call or inference.
            chunks = [
                {"id": "local-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": "fixture", "choices": [{"index": 0,
                 "delta": {"role": "assistant", "content": "Local fixture received."},
                 "finish_reason": None}]},
                {"id": "local-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with tempfile.TemporaryDirectory(prefix="cairn-opencode-shape-") as temporary:
        root = Path(temporary)
        home = root / "home"
        work = root / "work"
        tool_dir = work / ".opencode" / "tools"
        home.mkdir(mode=0o700)
        tool_dir.mkdir(parents=True, mode=0o700)
        shutil.copyfile(adapter, tool_dir / "cairn.ts")
        settings = {
            "executable": str(root / "uninvoked-cairn-binary"),
            "socket": str(root / "unavailable-cairn.sock"),
            "token_file": str(root / "unavailable-token"),
            "repo": "fixture:opencode-request-shape",
        }
        settings_path = work / ".opencode" / "cairn.json"
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        settings_path.chmod(0o600)
        config = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "enabled_providers": ["cairn-fixture"],
            "provider": {"cairn-fixture": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Cairn local request-shape fixture",
                "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1",
                            "apiKey": "local-only-fixture"},
                "models": {"fixture": {"name": "Fixture",
                                          "limit": {"context": 32000, "output": 1000}}},
            }},
            "permission": {"*": "deny", **{name: "allow" for name in sorted(EXPECTED)}},
        }
        config_path = root / "opencode.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        env = {
            "PATH": os.pathsep.join([str(binary.parent), "/usr/bin", "/bin"]),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
        }
        version = subprocess.check_output([str(binary), "--version"], env=env,
                                          text=True, timeout=10).strip()
        thread.start()
        process = subprocess.Popen(
            [str(binary), "run", "--format", "json", "--model", "cairn-fixture/fixture",
             f"Acknowledge {CANARY}. Do not call any tools."],
            cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            failures.append("OpenCode timed out")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        if process.returncode != 0:
            failures.append(f"OpenCode exited {process.returncode}")
        tool_requests = [request for request in canary_requests if request["cairn_tool_count"]]
        if len(tool_requests) != 1:
            failures.append(f"expected one canary tool request, observed {len(tool_requests)}")
        shape = tool_requests[0] if tool_requests else None
        if failures:
            raise AssertionError("; ".join(failures) + "\n" + stderr.decode("utf-8", errors="replace")[-3000:])
        report = {
            "schema": "cairn.opencode-tool-request-shape/1",
            "opencode_version": version,
            "opencode_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
            "route": "OpenCode native custom tool adapter to local OpenAI-compatible stub",
            "canary_request_count": canary_request_count,
            "canary_tool_request_count": len(tool_requests),
            "canary_tool_free_request_count": sum(not request["cairn_tool_count"]
                                                   for request in canary_requests),
            "canary_request_order": [
                {"cairn_tool_count": request["cairn_tool_count"],
                 "tool_names": request["cairn_tool_names"]}
                for request in canary_requests
            ],
            "external_provider_called": False,
            "model_inference": False,
            "tool_execution": False,
            "cairn_api_or_store_access": False,
            "raw_request_retained": False,
            "first_request": shape,
            "limits": [
                "Single current native adapter profile; no full-vs-compact comparison",
                "Schema presence is not provider billing or accepted task evidence",
                "No cold/retry/resume/cache accounting or task outcome",
            ],
        }
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    check(arguments.opencode, arguments.output)
