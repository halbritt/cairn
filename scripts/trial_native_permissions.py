"""Exercise native OpenCode permissions with a local scripted provider, no model.

Run through the same filesystem bridge as the repair experiment. A real write
must reach scratch, while sealed input bytes and unrelated home files remain
unchanged. The provider requests fixed tools; it does not perform reasoning.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading

from trial_native_runtime import runtime_command
from trial_retrieval_tools import private_file


def probe_permissions(root, opencode, cairn, go_paths, permission):
    root.mkdir(mode=0o700)
    work = root / 'work'
    work.mkdir(mode=0o700)
    for name in ('inputs', 'outputs'):
        (work / name).mkdir(mode=0o700)
    scratch = root / 'scratch'
    scratch.mkdir(mode=0o700)
    source_body = 'source must survive\n'
    sealed = json.dumps(dict(schema_version=1, files={'source.txt': source_body}))
    private_file(work / 'inputs/01-base', sealed)
    operations = [
        ('write', dict(filePath='/tmp/opencode/probe.txt', content='scratch allowed\n')),
        ('read', dict(filePath=str(work / 'inputs/01-base'))),
        ('write', dict(filePath=str(work / 'inputs/01-base'), content='sealed input overwritten\n')),
        ('write', dict(filePath='/trial-home/blocked.txt', content='outside scratch\n')),
    ]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 1024 * 1024 or len(requests) >= 12:
                self.send_error(400, 'Probe request limit')
                return
            body = json.loads(self.rfile.read(size))
            requests.append(dict(path=self.path, bytes=size))
            step = sum(m['role'] == 'tool' for m in body['messages'])
            # OpenCode may issue a separate title request without tools.
            if body.get('tools') and step < len(operations):
                name, arguments = operations[step]
                delta = dict(role='assistant', tool_calls=[dict(index=0, id='probe_' + str(step),
                    type='function', function=dict(name=name, arguments=json.dumps(arguments)))])
                finish = 'tool_calls'
            else:
                delta, finish = dict(role='assistant', content='Permission probe complete.'), 'stop'
            common = dict(id='permission-probe', object='chat.completion.chunk', created=1, model='fixture')
            chunks = [dict(common, choices=[dict(index=0, delta=delta, finish_reason=None)]),
                      dict(common, choices=[dict(index=0, delta={}, finish_reason=finish)])]
            data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    config_path = root / 'opencode.json'
    config = dict(autoupdate=False, share='disabled', enabled_providers=['fixture'],
        provider=dict(fixture=dict(npm='@ai-sdk/openai-compatible', name='Local scripted permission probe',
            options=dict(baseURL=f'http://127.0.0.1:{server.server_port}/v1', apiKey='local-fixture'),
            models=dict(fixture=dict(name='fixture', limit=dict(context=32768, output=1024))))),
        agent=dict(build=dict(steps=8)), permission=permission)
    private_file(config_path, json.dumps(config))
    runtime = dict(opencode=str(opencode), cairn=str(cairn), go_paths=go_paths,
        home=str(root / 'home'), cache=str(root / 'cache'), opencode_config=str(config_path),
        observation_file=str(root / 'prompt.sha256'), model='fixture/fixture',
        source_manifest=[dict(path='source.txt', sha256=hashlib.sha256(source_body.encode()).hexdigest())])
    thread.start()
    try:
        command = runtime_command(runtime, work, 'Execute the scripted permission checks.')
        # Retain only the authorized temporary scratch directory for byte checks.
        index = command.index('--')
        command[index:index] = ['--bind', str(scratch), '/tmp/opencode']
        result = subprocess.run(command, capture_output=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    private_file(root / 'stdout.jsonl', result.stdout.decode())
    private_file(root / 'stderr', result.stderr.decode())
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
    tools = [e['part'] for e in events if e.get('type') == 'tool_use']
    observed = [dict(tool=p['tool'], status=p['state']['status'], input=p['state']['input']) for p in tools]
    expected = [dict(tool=name, status=status, input=arguments)
                for (name, arguments), status in zip(operations, ['completed', 'completed', 'error', 'error'])]
    target = scratch / 'probe.txt'
    report = dict(schema='cairn.native-permission-probe/1', opencode_sha256=hashlib.sha256(opencode.read_bytes()).hexdigest(),
        permission=permission, process_exit=result.returncode, scripted_requests=len(requests), model_requests=0,
        tools=observed, scratch_written=target.exists() and target.read_text() == 'scratch allowed\n',
        sealed_input_unchanged=(work / 'inputs/01-base').read_text() == sealed,
        outside_scratch_absent=not (root / 'home/blocked.txt').exists())
    report['passed'] = (result.returncode == 0 and observed == expected and report['scratch_written']
                        and report['sealed_input_unchanged'] and report['outside_scratch_absent'])
    private_file(root / 'report.json', json.dumps(report, indent=2) + '\n')
    return report
