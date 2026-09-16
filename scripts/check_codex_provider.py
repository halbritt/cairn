"""Installed Codex fixture for subscription-limit reporting, using loopback only."""
import importlib.util
import json
import shlex
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class CodexQuotaFixture:
    def __init__(self, root, binary, coordination, plan="plus"):
        self.root = Path(root)
        self.binary = binary
        self.coordination = coordination
        self.plan = plan
        self.requests = 0

    def __enter__(self):
        self.root.mkdir(parents=True, mode=0o700)
        home = self.root / "home"
        home.mkdir(mode=0o700)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path != "/v1/responses":
                    self.send_error(404)
                    return
                fixture.requests += 1
                body = json.dumps({"error": {"type": "usage_limit_reached",
                    "message": "fixture account limit", "plan_type": fixture.plan,
                    "resets_at": 1893456000}}).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        try:
            (home / "config.toml").write_text(
                'model_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\n'
                f'base_url="http://127.0.0.1:{self.server.server_port}/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n'
                'request_max_retries=0\nstream_max_retries=0\n')
            spec = importlib.util.spec_from_file_location("codex_provider_installer",
                Path(__file__).with_name("install-agent-coordination.py"))
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            config = dict(self.coordination, harness="codex", process_names=["codex"],
                          config_home=str(home), native_delivery=True)
            engine = self.root / "engine"
            installed = installer.install(engine, home / "hooks.json", config)
            command = shlex.join([sys.executable, str(engine / "coordination.py"),
                                  "hook", "--config", str(installed)])
            installer.trust_codex_hooks(home, home / "hooks.json", command,
                                        codex_binary=self.binary)
            self.command = ["/usr/bin/env", "-u", "OPENAI_API_KEY", "-u", "OPENAI_BASE_URL",
                "CODEX_HOME=" + str(home), self.binary, "exec", "--json", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "-m", "gpt-6-astra", "--"]
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.server.server_close()
            raise

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
