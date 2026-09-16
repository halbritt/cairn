"""Installed Agy provider fixture; isolated settings and loopback HTTP only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class AgyProviderFixture:
    def __init__(self, root, binary, coordination, mode):
        self.root = Path(root)
        self.binary = binary
        self.coordination = coordination
        self.mode = mode
        self.requests = []

    def __enter__(self):
        self.root.mkdir(parents=True, mode=0o700)
        gemini = self.root / 'gemini'
        cli = gemini / 'antigravity-cli'
        cli.mkdir(parents=True, mode=0o700)
        (cli / 'settings.json').write_text(json.dumps({'modelProvider': 'gemini'}))
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True, timeout=5)
        spec = importlib.util.spec_from_file_location('agy_provider_installer',
            Path(__file__).with_name('install-agent-coordination.py'))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        settings = self.root / '.agents/hooks.json'
        self.installed = installer.install(self.root / 'engine', settings,
            dict(self.coordination, harness='agy', process_names=['agy']))
        hooks = json.loads(settings.read_text())
        for handlers in hooks['cairn-coordination'].values():
            for handler in handlers:
                handler['command'] = '/usr/bin/env CAIRN_COORDINATION_DISABLED=0 ' + handler['command']
        settings.write_text(json.dumps(hooks))
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                main = 'flash-lite' not in self.path
                attempt = sum(r['main'] for r in fixture.requests) + int(main)
                status = 429
                if fixture.mode == 'nonquota' or (fixture.mode == 'later-error' and main and attempt >= 2):
                    status = 400
                if fixture.mode == 'recovered' and main and attempt >= 2:
                    status = 200
                    body = ('data: ' + json.dumps({'candidates': [{'content': {'role': 'model',
                        'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP', 'index': 0}],
                        'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1,
                                          'totalTokenCount': 2}}) + '\n\n').encode()
                else:
                    body = json.dumps({'error': {'code': status,
                        'message': 'Resource has been exhausted (e.g. check quota).' if status == 429 else
                                   'Invalid argument: quota 429 RESOURCE_EXHAUSTED is fixture text.',
                        'status': 'RESOURCE_EXHAUSTED' if status == 429 else 'INVALID_ARGUMENT'}}).encode()
                fixture.requests.append(dict(path=self.path, main=main, status=status))
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        # The real supervisor owns the process cgroup. Keep host PID visibility
        # so native hooks associate the actual Agy process with its session.
        self.command = ['/usr/bin/env', 'CAIRN_COORDINATION_DISABLED=1',
            'GEMINI_API_KEY=fake-loopback-key',
            f'GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:{self.server.server_port}',
            'NO_PROXY=127.0.0.1,localhost', 'bwrap', '--die-with-parent',
            '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
            '--bind', '/tmp', '/tmp', '--bind', str(gemini), str(Path.home() / '.gemini'),
            '--chdir', str(self.root), self.binary, '--new-project', '--output-format', 'stream-json',
            '--print-timeout', '10s', '--print']
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        (self.root / 'requests.json').write_text(json.dumps(self.requests, indent=2))
