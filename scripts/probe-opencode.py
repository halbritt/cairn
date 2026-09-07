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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cairn-binary")
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve(strict=True))
    observed = []
    canary = "CAIRN-FIRST-REQUEST-7f5b31bb"

    class Fixture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(length))
            observed.append({"path": self.path, "has_canary": canary in json.dumps(request)})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                     "model": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture received."}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
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
            config = {"$schema": "https://opencode.ai/config.json", "autoupdate": False,
                      "share": "disabled", "enabled_providers": ["cairn-fixture"],
                      "provider": {"cairn-fixture": {"npm": "@ai-sdk/openai-compatible", "name": "Cairn local fixture",
                          "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1", "apiKey": "synthetic-fixture"},
                          "models": {"fixture": {"name": "Fixture", "limit": {"context": 32000, "output": 1000}}}}}}
            config_path = root / "opencode.json"
            config_path.write_text(json.dumps(config))
            # Build a minimal child environment, not a modified credential-bearing one.
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                   "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"),
                   "XDG_STATE_HOME": str(home / ".local/state"), "OPENCODE_CONFIG": str(config_path),
                   "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true"}
            version = subprocess.check_output([binary, "--version"], env=env, text=True, timeout=10).strip()
            prompt = f"MEM-STATUS/READY\nAdvisory fixture: {canary}\nReply with a short acknowledgement. Do not use tools."
            command = [binary, "run", "--pure", "--format", "json", "-m", "cairn-fixture/fixture", prompt]
            if args.cairn_binary:
                cairn = str(Path(args.cairn_binary).resolve(strict=True))
                env["CAIRN_DATABASE_URL"] = os.environ["CAIRN_DATABASE_URL"]
                env["CAIRN_HOME"] = str(root / "cairn")
                subprocess.run([cairn, "remember", "--repo", "fixture:opencode", "--shareable", canary],
                               env=env, stdout=subprocess.DEVNULL, check=True, timeout=10)
                command = [cairn, "run", "--repo", "fixture:opencode", "--dir", str(work), "--destination", "hosted",
                           "--carrier", "argv", "--tokens", "64000", "--timeout", "30s", "--query", canary,
                           "--prompt", "Acknowledge the advisory fixture. Do not use tools.", "--",
                           binary, "run", "--pure", "--format", "json", "-m", "cairn-fixture/fixture"]
            process = subprocess.Popen(command,
                                       cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            report = {"schema": "cairn.adapter-probe/1", "binary": binary, "version": version,
                      "through_cairn_wrapper": bool(args.cairn_binary),
                      "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                      "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "carrier": "argv", "first_model_request_contains_canary": bool(observed and observed[0]["has_canary"]),
                      "requests": observed, "exit_code": process.returncode, "timed_out": timed_out,
                      "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                      "limits": ["Synthetic local endpoint, no model capability evaluation", "No runtime mediation", "No live user configuration conformance claim"]}
            Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            if not report["first_model_request_contains_canary"] or process.returncode != 0:
                # This environment contains only synthetic inputs; diagnostic output is safe.
                print(stderr.decode(errors="replace")[-4000:])
                raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
