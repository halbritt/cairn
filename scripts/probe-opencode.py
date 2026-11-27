#!/usr/bin/env python3
"""Probe installed OpenCode's real first-request ingress against a local fixture.

No provider credentials, model call, user sessions, or user configs are needed.
The only retained result is a capability report; request bodies stay in memory.
"""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

ALLOW_THINKING_ENV = "CAIRN_OPENCODE_ALLOW_THINKING"


class StructuredOutputError(RuntimeError):
    """Raised when a structured completion does not yield valid bounded JSON."""


class ReasoningBudgetExhaustedError(StructuredOutputError):
    """Raised when reasoning consumes the output budget without usable content."""


def is_truthy_env(value):
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def resolve_thinking_options(*, disable_thinking=False, structured=False, allow_thinking=None):
    """Return model options bounding thinking on structured turns.

    Structured extraction/communication turns always set enable_thinking=False
    so reasoning cannot silently swallow JSON output. Non-structured turns may
    re-enable unrestricted thinking via allow_thinking=True or
    CAIRN_OPENCODE_ALLOW_THINKING=1.
    """
    if allow_thinking is None:
        allow_thinking = is_truthy_env(os.environ.get(ALLOW_THINKING_ENV))
    if structured:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if disable_thinking and not allow_thinking:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def build_structured_chat_request(prompt, *, model="qwen3.8-27b", max_tokens=512, temperature=0, allow_thinking=None):
    """Build a bounded OpenAI-compatible structured completion request."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    payload.update(resolve_thinking_options(structured=True, allow_thinking=allow_thinking))
    return payload


def extract_structured_completion_json(response, *, require_object=True):
    """Parse a chat completion JSON payload, failing visibly on empty/reasoning exhaustion."""
    if not isinstance(response, dict):
        raise StructuredOutputError("chat completion response must be a JSON object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise StructuredOutputError("chat completion response missing choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise StructuredOutputError("chat completion choice must be an object")
    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise StructuredOutputError("chat completion choice missing message")
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    has_reasoning = isinstance(reasoning_content, str) and bool(reasoning_content.strip())
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    completion_tokens = usage.get("completion_tokens")

    if not isinstance(content, str) or not content.strip():
        if finish_reason == "length" or has_reasoning:
            raise ReasoningBudgetExhaustedError(
                f"reasoning exhausted token budget with empty content "
                f"(finish_reason={finish_reason!r}, reasoning_bytes={len(reasoning_content or '')}, "
                f"completion_tokens={completion_tokens!r})"
            )
        raise StructuredOutputError(
            f"structured completion returned empty content (finish_reason={finish_reason!r})"
        )

    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        if finish_reason == "length":
            raise ReasoningBudgetExhaustedError(
                f"structured completion truncated at token budget before valid JSON "
                f"(finish_reason='length', content_bytes={len(stripped)}, "
                f"reasoning_bytes={len(reasoning_content or '')}): {exc}"
            ) from exc
        raise StructuredOutputError(
            f"structured completion returned invalid JSON (finish_reason={finish_reason!r}): {exc}"
        ) from exc

    if require_object and not isinstance(parsed, dict):
        raise StructuredOutputError(
            f"structured completion expected JSON object, got {type(parsed).__name__}"
        )
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cairn-binary")
    parser.add_argument("--disable-thinking", action="store_true", help="verify forwarding of the local model thinking override")
    parser.add_argument("--structured", action="store_true", help="require bounded non-thinking structured turn options unless --allow-thinking overrides non-structured mode")
    parser.add_argument("--allow-thinking", action="store_true", help="explicit off-switch: re-enable unrestricted thinking on non-structured turns (also via CAIRN_OPENCODE_ALLOW_THINKING=1)")
    parser.add_argument("--check-edit", action="store_true", help="exercise read then edit through the installed harness against a disposable file")
    parser.add_argument("--simulate-budget-exhaustion", action="store_true", help="simulate finish_reason=length with reasoning_content and empty content to verify visible failure")
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve(strict=True))
    observed = []
    edit_stage = 0
    canary = "CAIRN-FIRST-REQUEST-7f5b31bb"

    class Fixture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal edit_stage
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(length))
            observation = {"path": self.path, "has_canary": canary in json.dumps(request),
                           "enable_thinking": request.get("chat_template_kwargs", {}).get("enable_thinking"),
                           "max_tokens": request.get("max_tokens"),
                           "tools": sorted(t.get("function", {}).get("name", "unknown") for t in request.get("tools", []))}
            observed.append(observation)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                     "model": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture received."}, "finish_reason": None}]}
            finish_reason = "stop"
            if args.simulate_budget_exhaustion:
                chunk["choices"][0]["delta"] = {"role": "assistant", "reasoning_content": "Exhausted token budget inside reasoning."}
                finish_reason = "length"
                observation["simulated_finish_reason"] = "length"
            elif args.check_edit and request.get("tools") and edit_stage < 2:
                tool = "read" if edit_stage == 0 else "edit"
                arguments = {"filePath": str(work / "edit-fixture.txt")}
                if tool == "edit":
                    arguments.update(oldString="before", newString="after")
                chunk["choices"][0]["delta"] = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "fixture-" + tool, "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
                observation["fixture_tool_requested"] = tool
                edit_stage += 1
                finish_reason = "tool_calls"
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="cairn-opencode-probe-") as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            work = root / "work"
            work.mkdir()
            if args.check_edit:
                (work / "edit-fixture.txt").write_text("before\n")
            config = {"$schema": "https://opencode.ai/config.json", "autoupdate": False,
                      "share": "disabled", "enabled_providers": ["cairn-fixture"],
                      "provider": {"cairn-fixture": {"npm": "@ai-sdk/openai-compatible", "name": "Cairn local fixture",
                          "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1", "apiKey": "synthetic-fixture"},
                          "models": {"fixture": {"name": "Fixture", "limit": {"context": 32000, "output": 1000}}}}}}
            config_path = root / "opencode.json"
            model_options = resolve_thinking_options(
                disable_thinking=args.disable_thinking,
                structured=args.structured,
                allow_thinking=True if args.allow_thinking else None,
            )
            if model_options:
                config["provider"]["cairn-fixture"]["models"]["fixture"]["options"] = model_options
            if args.check_edit:
                config["permission"] = {"edit": "allow", "read": "allow", "bash": "deny"}
            config_path.write_text(json.dumps(config))
            # Build a minimal child environment, not a modified credential-bearing one.
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                   "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"),
                   "XDG_STATE_HOME": str(home / ".local/state"), "OPENCODE_CONFIG": str(config_path),
                   "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true"}
            version = subprocess.check_output([binary, "--version"], env=env, text=True, timeout=10).strip()
            prompt = f"MEM-STATUS/READY\nAdvisory fixture: {canary}\nReply with a short acknowledgement. Do not use tools."
            if args.check_edit:
                prompt = f"{canary}\nRead edit-fixture.txt and change before to after."
            command = [binary, "run", "--pure", "--format", "json", "-m", "cairn-fixture/fixture", prompt]
            if args.cairn_binary:
                cairn = str(Path(args.cairn_binary).resolve(strict=True))
                env["CAIRN_DATABASE_URL"] = os.environ["CAIRN_DATABASE_URL"]
                env["CAIRN_HOME"] = str(root / "cairn")
                subprocess.run([cairn, "remember", "--repo", "fixture:opencode", "--shareable", canary],
                               env=env, stdout=subprocess.DEVNULL, check=True, timeout=10)
                command = [cairn, "run", "--repo", "fixture:opencode", "--dir", str(work), "--destination", "hosted",
                           "--carrier", "argv", "--tokens", "64000", "--timeout", "30s", "--query", canary,
                           "--prompt", ("Read edit-fixture.txt and change before to after." if args.check_edit
                                        else "Acknowledge the advisory fixture. Do not use tools."), "--",
                           binary, "run", "--pure", "--format", "json", "-m", "cairn-fixture/fixture"]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            trace_events = []
            for raw_line in stdout.splitlines():
                try:
                    trace_events.append(json.loads(raw_line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            has_text_or_tool = any(
                (ev.get("type") == "text" and bool((ev.get("part") or {}).get("text", "").strip()))
                or ev.get("type") == "tool_use"
                for ev in trace_events if isinstance(ev, dict)
            )
            budget_exhausted = (
                any(
                    ev.get("type") == "step_finish"
                    and (ev.get("part") or {}).get("reason") == "length"
                    and ((ev.get("part") or {}).get("tokens") or {}).get("output", 0) == 0
                    for ev in trace_events if isinstance(ev, dict)
                )
                or any(o.get("simulated_finish_reason") == "length" for o in observed)
            ) and not has_text_or_tool
            report = {"schema": "cairn.adapter-probe/1", "binary": binary, "version": version,
                      "through_cairn_wrapper": bool(args.cairn_binary),
                      "thinking_override_requested": bool(model_options),
                      "structured_mode": args.structured,
                      "allow_thinking_override": args.allow_thinking or is_truthy_env(os.environ.get(ALLOW_THINKING_ENV)),
                      "reasoning_budget_exhausted": budget_exhausted,
                      "edit_probe_requested": args.check_edit,
                      "edit_observed": args.check_edit and (work / "edit-fixture.txt").read_text() == "after\n",
                      "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                      "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "carrier": "argv", "first_model_request_contains_canary": bool(observed and observed[0]["has_canary"]),
                      "requests": observed, "exit_code": process.returncode, "timed_out": timed_out,
                      "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                      "limits": ["Synthetic local endpoint, no model capability evaluation", "No runtime mediation", "No live user configuration conformance claim"]}
            Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            override_missing = bool(model_options) and (not observed or observed[0]["enable_thinking"] is not False)
            if not report["first_model_request_contains_canary"] or process.returncode != 0 or override_missing or budget_exhausted or (args.check_edit and not report["edit_observed"]):
                # This environment contains only synthetic inputs; diagnostic output is safe.
                print(stderr.decode(errors="replace")[-4000:])
                raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
