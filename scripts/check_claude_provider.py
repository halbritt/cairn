"""Installed Claude rate-limit fixture with explicit hooks and loopback provider."""
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from check_claude_tools import response_events


class ClaudeProviderFixture:
    def __init__(self, root, binary, coordination, mode):
        self.root = Path(root).resolve()
        self.binary = binary
        self.coordination = coordination
        self.mode = mode
        self.requests = []

    def __enter__(self):
        self.root.mkdir(parents=True, mode=0o700)
        config = self.root / 'config'
        config.mkdir(mode=0o700)
        spec = importlib.util.spec_from_file_location('claude_provider_installer',
            Path(__file__).with_name('install-agent-coordination.py'))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        settings = self.root / 'settings.json'
        installer.install(self.root / 'engine', settings,
            dict(self.coordination, harness='claude', process_names=['claude'], config_home=str(config)))
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(10)
                request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                if self.path.split('?')[0] != '/v1/messages':
                    self.send_error(404)
                    return
                status = 429
                if fixture.requests:
                    if fixture.mode == 'recovered':
                        status = 200
                    elif fixture.mode == 'later-error':
                        status = 400
                fixture.requests.append(status)
                if status == 200:
                    body = response_events('fixture-response', request['model'], dict(type='text', text='OK'))
                else:
                    body = json.dumps(dict(type='error', error=dict(
                        type='rate_limit_error' if status == 429 else 'invalid_request_error',
                        message='fixture rate limit' if status == 429 else 'fixture invalid request'))).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Retry-After', '0')
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.command = ['/usr/bin/env', '-u', 'ANTHROPIC_AUTH_TOKEN',
            'CLAUDE_CONFIG_DIR=' + str(config), 'ANTHROPIC_API_KEY=fake-loopback-key',
            f'ANTHROPIC_BASE_URL=http://127.0.0.1:{self.server.server_port}',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1', 'ENABLE_TOOL_SEARCH=false',
            'bwrap', '--die-with-parent', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
            '--bind', str(self.root.parent), str(self.root.parent),
            '--chdir', str(self.root), self.binary,
            '--print', '--verbose', '--output-format', 'stream-json', '--no-session-persistence',
            '--setting-sources', '', '--settings', str(settings), '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk',
            '--max-turns', '1', '--model', 'sonnet', '--']
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        (self.root / 'requests.json').write_text(json.dumps(self.requests))
