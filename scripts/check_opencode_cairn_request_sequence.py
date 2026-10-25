#!/usr/bin/env python3
"""Measure OpenCode request schemas through local failure/retry and resume turns.

All provider requests terminate at a loopback scripted endpoint. Cairn points
at an absent local executable and unavailable socket; no API, store, or external
provider is contacted. Raw requests and dialogue are summarized in memory only.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading

from check_opencode_cairn_request_shape import CANARY, EXPECTED_DECLARATION_SET_SHA256, canonical, summarize_tools


RESUME_CANARY = "CAIRN-225-LOCAL-RESUME-SHAPE"
CACHE_REQUEST_FIELDS = frozenset(("cache_control", "prompt_cache_key", "cached_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "cache_read_tokens",
    "cache_write_tokens", "cached_prompt_tokens"))


def object_fields(value, allowlist):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in allowlist:
                found.add(key)
            found.update(object_fields(child, allowlist))
    elif isinstance(value, list):
        for child in value:
            found.update(object_fields(child, allowlist))
    return found


def parse_events(output):
    try:
        return [json.loads(line) for line in output.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("OpenCode returned malformed JSON events") from exc


def session_ids(value):
    found = set()
    if isinstance(value, dict):
        for key in ("sessionID", "sessionId", "session_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("ses_"):
                found.add(candidate)
        for child in value.values():
            found.update(session_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(session_ids(child))
    return found


def reported_usage_fields(events):
    fields = set()

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else key
                if key in ("input", "output", "reasoning", "cache", "read", "write") and type(child) is int:
                    fields.add(next_path)
                walk(child, next_path)
        elif isinstance(value, list):
            for child in value:
                walk(child, path)

    for event in events:
        walk(event)
    return sorted(fields)


def check(binary, output):
    binary = Path(binary).resolve(strict=True)
    adapter = Path(__file__).resolve().parents[1] / "integrations/opencode/cairn.ts"
    reports, scripted_usage, failures = [], [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)

    def response(request_id, model, *, tool_call=None, content=None, usage=None):
        if tool_call:
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": tool_call[0],
                "type": "function", "function": {"name": tool_call[1],
                "arguments": json.dumps(tool_call[2], separators=(",", ":"))}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"role": "assistant", "content": content or "Local fixture finished."}, "stop"
        chunks = [
            {"id": request_id, "object": "chat.completion.chunk", "created": 1, "model": model,
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": request_id, "object": "chat.completion.chunk", "created": 1, "model": model,
             "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
        ]
        return ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n").encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length <= 2_000_000 or len(reports) >= 20:
                failures.append("unexpected_route_or_request_budget")
                self.send_error(400)
                return
            try:
                request = json.loads(self.rfile.read(length))
                messages = request.get("messages")
                if not isinstance(messages, list):
                    raise ValueError("messages_missing")
                user_messages = [message for message in messages
                                 if isinstance(message, dict) and message.get("role") == "user"]
                last_user = user_messages[-1].get("content", "") if user_messages else ""
                if isinstance(last_user, list):
                    last_user = json.dumps(last_user)
                is_resume = RESUME_CANARY in str(last_user)
                is_initial = CANARY in str(last_user)
                if not (is_initial or is_resume):
                    block = dict(tool_call=None, content="OpenCode fixture title")
                    label = "untagged_title"
                else:
                    last_user_index = max((index for index, message in enumerate(messages)
                        if isinstance(message, dict) and message.get("role") == "user"), default=-1)
                    recent_tool_results = [message for message in messages[last_user_index + 1:]
                        if isinstance(message, dict) and message.get("role") == "tool"]
                    result_count = len(recent_tool_results)
                    error_count = sum(any(marker in str(message.get("content", "")).lower()
                        for marker in ("error", "failed", "enoent", "unavailable", "refused"))
                        for message in recent_tool_results)
                    has_tools = bool(request.get("tools"))
                    if not has_tools:
                        label = "resume_tool_free" if is_resume else "initial_tool_free"
                        block = dict(tool_call=None, content="OpenCode fixture title")
                    else:
                        shape = summarize_tools(request)
                        label = (("resume_after_tool_failure" if result_count else "resume_first_use")
                                 if is_resume else
                                 ("retry_after_tool_failure" if result_count == 1 else
                                  "post_retry_final" if result_count >= 2 else "first_use"))
                        summary = dict(label=label, resume=is_resume, tool_result_count=result_count,
                            tool_error_result_count=error_count,
                            tool_names=shape["cairn_tool_names"],
                            declaration_set_sha256=shape["declaration_set_sha256"],
                            validation_set_sha256=shape["validation_set_sha256"],
                            declarations=shape["declarations"],
                            declaration_bytes=shape["cairn_declarations_canonical_json_utf8_bytes"],
                            cache_request_fields=sorted(object_fields(request, CACHE_REQUEST_FIELDS)))
                        reports.append(summary)
                        if is_resume:
                            should_call = result_count == 0
                        else:
                            should_call = result_count < 2
                        block = dict(tool_call=("fixture_" + label, "cairn_search",
                            {"query": "CAIRN-225 local request shape"}) if should_call else None,
                            content="Local retry fixture completed.")
                usage = dict(prompt_tokens=127 + len(reports) * 11, completion_tokens=17,
                    total_tokens=144 + len(reports) * 11,
                    prompt_tokens_details=dict(cached_tokens=31 + len(reports)))
                scripted_usage.append(dict(request_label=label, usage_fields=sorted(usage),
                    cached_tokens=usage["prompt_tokens_details"]["cached_tokens"]))
                encoded = response("opencode-local-fixture", request.get("model", "fixture"),
                    tool_call=block["tool_call"], content=block["content"], usage=usage)
            except (AssertionError, KeyError, TypeError, ValueError) as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server.RequestHandlerClass = Handler
    with tempfile.TemporaryDirectory(prefix="cairn-opencode-sequence-") as temporary:
        root = Path(temporary)
        home, work, tool_dir = root / "home", root / "work", root / "work/.opencode/tools"
        home.mkdir(mode=0o700)
        tool_dir.mkdir(parents=True, mode=0o700)
        shutil.copyfile(adapter, tool_dir / "cairn.ts")
        settings_path = work / ".opencode/cairn.json"
        settings_path.write_text(json.dumps(dict(executable=str(root / "absent-cairn"),
            socket=str(root / "absent-cairn.sock"), token_file=str(root / "absent-token"),
            repo="fixture:opencode-request-sequence")), encoding="utf-8")
        settings_path.chmod(0o600)
        config = dict(autoupdate=False, share="disabled", enabled_providers=["cairn-fixture"],
            provider={"cairn-fixture": dict(npm="@ai-sdk/openai-compatible",
                name="Cairn local retry/resume fixture",
                options=dict(baseURL=f"http://127.0.0.1:{server.server_port}/v1", apiKey="local-only-fixture"),
                models={"fixture": dict(name="Fixture", limit=dict(context=32000, output=1000))})},
            permission={"*": "deny", **{name: "allow" for name in sorted(
                ("cairn_search", "cairn_prepare_note", "cairn_history", "cairn_assessments",
                 "cairn_assess", "cairn_pull", "cairn_pull_evidence", "cairn_remember", "cairn_edit"))}})
        config_path = root / "opencode.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        env = {"PATH": os.pathsep.join([str(binary.parent), "/usr/bin", "/bin"]), "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
            "OPENCODE_CONFIG": str(config_path), "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true"}
        version = subprocess.check_output([str(binary), "--version"], env=env, text=True, timeout=10).strip()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def run(prompt, session_id=None):
            argv = [str(binary), "run", "--format", "json", "--model", "cairn-fixture/fixture"]
            if session_id:
                argv += ["--session", session_id]
            argv.append(prompt)
            process = subprocess.Popen(argv, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
            try:
                stdout, _stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    stdout, _stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, _stderr = process.communicate(timeout=5)
                raise AssertionError("local OpenCode request sequence exceeded 60-second bound")
            if process.returncode != 0:
                raise AssertionError("OpenCode exited " + str(process.returncode) + "; raw stderr discarded")
            events = parse_events(stdout)
            ids = session_ids(events)
            if len(ids) != 1:
                raise AssertionError("OpenCode did not expose one stable session identifier")
            return next(iter(ids)), events

        try:
            initial_id, initial_events = run(
                f"Search for {CANARY}; retry once after the local error, then finish.")
            resumed_id, resumed_events = run(
                f"Continue the same local request sequence using {RESUME_CANARY}.", initial_id)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if failures:
            raise AssertionError("loopback provider rejected a request: " + "; ".join(failures))
        labels = [item["label"] for item in reports]
        expected = ["first_use", "retry_after_tool_failure", "post_retry_final",
                    "resume_first_use", "resume_after_tool_failure"]
        if labels != expected:
            raise AssertionError("request phase sequence differed: " + ",".join(labels))
        hashes = {item["declaration_set_sha256"] for item in reports}
        if hashes != {EXPECTED_DECLARATION_SET_SHA256}:
            raise AssertionError("native tool declaration set changed across retry/resume")
        if initial_id != resumed_id:
            raise AssertionError("OpenCode resume changed the native session identity")
        if any(len(item["tool_names"]) != 9 for item in reports):
            raise AssertionError("a retry/resume request lost Cairn tool availability")
        if any(item["tool_error_result_count"] < 1 for item in reports
               if item["label"] in ("retry_after_tool_failure", "post_retry_final", "resume_after_tool_failure")):
            raise AssertionError("a scripted native tool error was not present on the retry/resume request")

        all_events = initial_events + resumed_events
        report = dict(schema="cairn.opencode-request-sequence/1", opencode_version=version,
            opencode_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            adapter_sha256=hashlib.sha256(adapter.read_bytes()).hexdigest(),
            route="native OpenCode tool adapter to scripted loopback OpenAI-compatible endpoint",
            phases=reports, phase_order=labels, initial_and_resume_session_equal=True,
            local_tool_invocations=sum(item["tool_result_count"] == 0 and item["label"] in
                ("first_use", "resume_first_use") for item in reports) +
                sum(item["label"] == "retry_after_tool_failure" for item in reports),
            local_tool_errors_triggered=True, local_cairn_api_or_store_access=False,
            external_provider_called=False, model_inference=False)
        report["raw_requests_retained"] = False
        report["raw_dialogue_retained"] = False
        report["fixture_response_usage"] = scripted_usage
        report["opencode_reported_usage_field_paths"] = reported_usage_fields(all_events)
        report["usage_and_cache_scope"] = "scripted loopback response fields only; not provider usage or billing"
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    check(args.opencode, args.output)
