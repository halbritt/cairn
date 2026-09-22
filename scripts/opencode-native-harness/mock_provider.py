#!/usr/bin/env python3
"""Deterministic mock OpenAI-compatible provider. Binds loopback only; replies with a
fixed canary so any non-mock model output is detectable immediately."""
import json
import os
from pathlib import Path
from verify import verify_current

verify_current()
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
HOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
CANARY = "MOCK-CANARY-REPLY"
LOG = (Path(os.environ["HARNESS_FIXTURE_ROOT"]) / "mock.log").open("a", buffering=1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        LOG.write(f"GET {self.path}\n")
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
        else:
            self._json(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        LOG.write(f"POST {self.path} model={body.get('model')}\n")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        user_text = ""
        for m in body.get("messages", []):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    user_text = content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            user_text = part.get("text", "")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def chunk(delta, finish=None):
            event = {
                "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                "model": body.get("model", "mock-model"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.flush()

        chunk({"role": "assistant", "content": f"{CANARY} :: {user_text}"})
        try:
            self.wfile.flush()
        except Exception:
            return
        time.sleep(HOLD)
        chunk({}, finish="stop")
        final = {
            "id": "chatcmpl-mock", "object": "chat.completion.chunk",
            "model": body.get("model", "mock-model"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        self.wfile.write(b"data: " + json.dumps(final).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        try:
            self.connection.close()
        except Exception:
            pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
LOG.write(f"mock provider listening on 127.0.0.1:{PORT}\n")
server.serve_forever()
