"""Characterize custom-tool defaults through a real OpenCode session, without inference."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading


# Reconstruct the adapter's development mistake; this was never a released defect.
TOOL = '''import { tool } from "@opencode-ai/plugin"
export const declared = tool({
  description: "Return the supplied body with kind defaulting to note.",
  args: { body: tool.schema.string(), kind: tool.schema.string().default("note") },
  async execute(args) { return JSON.stringify({ body: args.body, kind: args.kind }) },
})
export const runtime = tool({
  description: "Return the supplied body with kind defaulting to note.",
  args: { body: tool.schema.string(), kind: tool.schema.string().optional() },
  async execute(args) { return JSON.stringify({ body: args.body, kind: args.kind ?? "note" }) },
})
'''


def check(opencode, output, *, connection=None, extra_cases=()):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    work = output / 'workspace'
    tool_dir = work / '.opencode/tools'
    tool_dir.mkdir(parents=True)
    (tool_dir / 'defaults.ts').write_text(TOOL)
    adapter = Path(__file__).resolve().parents[1] / 'integrations/opencode/cairn.ts'
    shutil.copyfile(adapter, tool_dir / 'cairn.ts')
    if connection is not None:
        settings = work / '.opencode/cairn.json'
        settings.write_text(json.dumps(connection))
        settings.chmod(0o600)
    env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    for key, folder in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                        ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')]:
        directory = output / folder
        directory.mkdir()
        env[key] = str(directory)
    cases = [
        ('declared-omitted', 'defaults_declared', {'body': 'selected lesson'}),
        ('declared-explicit', 'defaults_declared', {'body': 'selected lesson', 'kind': 'lesson'}),
        ('runtime-omitted', 'defaults_runtime', {'body': 'selected lesson'}),
        ('runtime-explicit', 'defaults_runtime', {'body': 'selected lesson', 'kind': 'lesson'}),
        ('declared-invalid', 'defaults_declared', {'body': 'selected lesson', 'kind': 7}),
        ('runtime-invalid', 'defaults_runtime', {'body': 'selected lesson', 'kind': 7}),
        ('adapter-shareable', 'cairn_remember', {'request_id': 'e7cb56d4-3c24-47a9-8705-c694f036130d',
                                               'body': 'selected lesson', 'shareable': 'false'}),
        ('adapter-query', 'cairn_search', {'query': 7}),
        ('adapter-browse', 'cairn_search', {'browse': 'true'}),
        ('adapter-pull', 'cairn_pull', {'request_id': 'invalid'}),
        ('adapter-evidence', 'cairn_pull_evidence', {'expected_sha256': 'invalid'}),
        ('adapter-edit', 'cairn_edit', {'draft': []}),
    ]
    cases.extend(extra_cases)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < size <= 1024 * 1024:
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(size))
            request_number = len(requests)
            requests.append(request)
            (output / f'request-{request_number}.json').write_text(json.dumps(request, indent=2) + '\n')
            index = sum(bool(item.get('tools')) for item in requests) - 1
            if request_number >= len(cases) + 3:
                self.send_error(429, 'Scripted request budget exhausted')
                return
            if not request.get('tools'):
                # OpenCode also asks this provider to title the session.
                delta, finish = dict(role='assistant', content='Custom-tool defaults check'), 'stop'
            elif index < len(cases):
                identifier, name, args = cases[index]
                delta = dict(role='assistant', tool_calls=[dict(index=0, id=identifier,
                             type='function', function=dict(name=name, arguments=json.dumps(args)))])
                finish = 'tool_calls'
            else:
                delta, finish = dict(role='assistant', content='Fixture complete.'), 'stop'
            chunks = [dict(id=f'fixture-{index}', object='chat.completion.chunk', created=0,
                           model='probe', choices=[dict(index=0, delta=delta, finish_reason=None)]),
                      dict(id=f'fixture-{index}', object='chat.completion.chunk', created=0,
                           model='probe', choices=[dict(index=0, delta={}, finish_reason=finish)])]
            body = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            encoded = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    config = dict(model='fixture/probe', provider={'fixture': {
        'npm': '@ai-sdk/openai-compatible', 'name': 'Scripted fixture, no inference',
        'options': {'baseURL': f'http://127.0.0.1:{server.server_port}/v1', 'apiKey': 'unused'},
        'models': {'probe': {'name': 'Probe'}}}},
        permission={'*': 'deny', **{name: 'allow' for _, name, _ in cases}})
    config_path = output / 'opencode.json'
    config_path.write_text(json.dumps(config))
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE='true',
               OPENCODE_DISABLE_MODELS_FETCH='true')
    version = subprocess.run([opencode, '--version'], env=env, capture_output=True,
                             text=True, check=True, timeout=15).stdout.strip()
    thread.start()
    try:
        with (output / 'stdout.jsonl').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
            result = subprocess.run([opencode, 'run', '--format', 'json', '--model', 'fixture/probe',
                                     'Execute the scripted custom-tool checks.'], cwd=work, env=env,
                                    stdout=stdout, stderr=stderr, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, f'OpenCode exited {result.returncode}; see {output}/stderr.log'
    tool_requests = [request for request in requests if request.get('tools')]
    assert len(tool_requests) == len(cases) + 1, f'Expected {len(cases) + 1} tool requests, observed {len(tool_requests)}'
    schemas = {tool['function']['name']: tool['function']['parameters'] for tool in tool_requests[0]['tools']}
    assert schemas['defaults_declared']['properties']['kind']['default'] == 'note'
    results = {message['tool_call_id']: message['content'] for message in tool_requests[-1]['messages']
               if message['role'] == 'tool'}
    assert set(results) == {case[0] for case in cases}, results
    for name, expected in [('declared-omitted', {'body': 'selected lesson'}),
                           ('declared-explicit', {'body': 'selected lesson', 'kind': 'lesson'}),
                           ('runtime-omitted', {'body': 'selected lesson', 'kind': 'note'}),
                           ('runtime-explicit', {'body': 'selected lesson', 'kind': 'lesson'})]:
        assert json.loads(results[name]) == expected, (name, results[name])
    for name in ('declared-invalid', 'runtime-invalid'):
        assert json.loads(results[name]) == {'body': 'selected lesson', 'kind': 7}, (name, results[name])
    # Malformed calls must be refused whether or not a connection is configured.
    for name, _, _ in cases:
        if name.startswith('adapter-'):
            assert 'INVALID_REQUEST: invalid arguments for Cairn tool' in results[name], (name, results[name])
    report = dict(schema='cairn.opencode-defaults-check/1', opencode_version=version,
                  opencode_sha256=hashlib.sha256(Path(opencode).read_bytes()).hexdigest(),
                  tool_sha256=hashlib.sha256(TOOL.encode()).hexdigest(),
                  adapter_sha256=hashlib.sha256(adapter.read_bytes()).hexdigest(),
                  route='opencode run with scripted loopback completions; no inference',
                  requests=len(requests), tool_requests=len(tool_requests), results=results,
                  declared_default_reaches_execute=False, runtime_default_preserves_override=True,
                  invalid_types_reach_execute=True, adapter_invalid_arguments_refused=True,
                  operational_store_access=False)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opencode', type=lambda value: str(Path(value).resolve()), required=True)
    parser.add_argument('--output', type=lambda value: Path(value).resolve(), required=True)
    args = parser.parse_args()
    check(args.opencode, args.output)
