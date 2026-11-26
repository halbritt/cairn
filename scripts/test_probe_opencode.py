"""Deterministic unit and loopback HTTP tests for probe-opencode structured extraction."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
from unittest import mock
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe_opencode", ROOT / "scripts/probe-opencode.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeOpenCodeStructuredTests(unittest.TestCase):
    def test_structured_completion_valid_json_with_thinking_disabled(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                received.append(body)
                resp = {
                    "id": "chatcmpl-ok",
                    "object": "chat.completion",
                    "model": "qwen3.8-27b",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"ok": true, "count": 2}'},
                    }],
                    "usage": {"prompt_tokens": 38, "completion_tokens": 18, "total_tokens": 56},
                }
                encoded = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = probe.build_structured_chat_request(
                "Return JSON", model="qwen3.8-27b", max_tokens=128
            )
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parsed = probe.extract_structured_completion_json(data)
            self.assertEqual(parsed, {"ok": True, "count": 2})
            self.assertEqual(len(received), 1)
            self.assertIs(received[0]["chat_template_kwargs"]["enable_thinking"], False)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_insufficient_budget_reasoning_exhaustion_fails_visibly(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                _ = self.rfile.read(length)
                resp = {
                    "id": "chatcmpl-exhausted",
                    "object": "chat.completion",
                    "model": "qwen3.8-27b",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Thinking consumed all tokens before emitting JSON...",
                        },
                    }],
                    "usage": {"prompt_tokens": 78, "completion_tokens": 32, "total_tokens": 110},
                }
                encoded = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "Return JSON"}], "max_tokens": 32}
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            with self.assertRaisesRegex(probe.ReasoningBudgetExhaustedError, "finish_reason='length'"):
                probe.extract_structured_completion_json(data)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_empty_and_truncated_outputs_fail_visibly(self):
        for bad in (
            {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(probe.StructuredOutputError):
                probe.extract_structured_completion_json(bad)

        truncated = {"choices": [{"finish_reason": "length", "message": {"content": '{"ok":', "reasoning_content": "step 1"}}]}
        with self.assertRaises(probe.ReasoningBudgetExhaustedError):
            probe.extract_structured_completion_json(truncated)

    def test_allow_thinking_off_switch_on_non_structured_turns(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(probe.ALLOW_THINKING_ENV, None)
            self.assertEqual(
                probe.resolve_thinking_options(disable_thinking=True, structured=False),
                {"chat_template_kwargs": {"enable_thinking": False}},
            )
            os.environ[probe.ALLOW_THINKING_ENV] = "1"
            self.assertEqual(
                probe.resolve_thinking_options(disable_thinking=True, structured=False),
                {},
            )
            self.assertEqual(
                probe.resolve_thinking_options(disable_thinking=False, structured=True),
                {"chat_template_kwargs": {"enable_thinking": False}},
            )
        self.assertEqual(
            probe.resolve_thinking_options(disable_thinking=True, structured=False, allow_thinking=True),
            {},
        )


if __name__ == "__main__":
    unittest.main()
