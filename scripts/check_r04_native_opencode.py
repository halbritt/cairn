"""Native OpenCode cache/refresh fixture; run only in a disposable namespace.

The selected client is an explicit input, never discovered in an account home.
Only the local scripted model and synthetic Cairn API below can be reached.
"""
import argparse
import base64
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

from test_f04_activation import encoded, overwrite_fixture
from test_r02_compatibility_install import InstalledTools
from test_r04_host_wiring import R04HostWiringTests, pin


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def successful_execs(trace):
    decoder = json.JSONDecoder()
    pending = {}
    for line in trace.splitlines():
        unfinished = re.match(r'^(\d+)\s+execve\(.* <unfinished \.\.\.>$', line)
        if unfinished:
            pid = unfinished[1]
            if pid in pending:
                raise AssertionError('Overlapping unfinished execve evidence')
            pending[pid] = line.removesuffix(' <unfinished ...>')
            continue
        resumed = re.match(r'^(\d+)\s+<\.\.\. execve resumed>(.*)$', line)
        if resumed:
            if resumed[1] not in pending:
                raise AssertionError('Resumed execve without its issued call')
            line = pending.pop(resumed[1]) + resumed[2]
        match = re.match(r'^(\d+)\s+execve\((.*)\)\s+= 0$', line)
        if match is None:
            continue
        path, end = decoder.raw_decode(match[2])
        argv, _ = decoder.raw_decode(match[2][end + 2:])
        yield dict(pid=match[1], path=path, argv=argv)
    if pending:
        raise AssertionError('Unfinished execve evidence')


def wait_for(action, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = action()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError('Timed out: ' + description)


def native_api_fixture(path):
    """Synthetic CLI transport with separate identities for native sessions.

    The single-session R02 helper cannot model several conversations and a
    retired/reopened plugin. Keep this extra fixture local to the native check.
    """
    path.chmod(0o700)
    path.write_text('''#!/usr/bin/python3
import fcntl,json,pathlib,sys,uuid
p=pathlib.Path(__file__);args=sys.argv[1:];i=1
if args[0]=='agent':
 while args[i].startswith('--'): i+=2
 op=args[i]
else:op=args[0]
raw=sys.stdin.read();r=json.loads(raw or '{}') if args[0]=='agent' else {}
with p.with_suffix('.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 with p.with_suffix('.calls').open('a') as log:log.write(op+'\\n')
 with p.with_suffix('.jsonl').open('a') as log:log.write(json.dumps(dict(operation=op,argv=args,input=raw))+'\\n')
 store=p.with_suffix('.agents');db=json.loads(store.read_text()) if store.exists() else {'agents':{},'replies':{}}
 request=r.get('request_id');data=None
 if request in db['replies']:data=db['replies'][request]
 elif op=='agent-register':
  native=r['native_session_id'];agent=str(uuid.uuid5(uuid.NAMESPACE_URL,'r04-native:'+native))
  data=dict(agent_id=agent,execution_id=str(uuid.uuid5(uuid.NAMESPACE_URL,request)),native_session_id=native,
            display_name='agent-native-fixture',inbox='agent/'+agent,context_revision=1,metadata=r['metadata'],stopped=False)
  db['agents'][agent]=data
 elif op in ('agent-context','agent-heartbeat','agent-leave'):
  owner=r.get('session',r);data=db['agents'][owner['agent_id']]
  assert data['execution_id']==owner['execution_id'], 'fixture stale execution'
  if op=='agent-context':
   assert data['context_revision']==r['expected_revision'], 'fixture stale context'
   data.update(metadata=r['metadata'],context_revision=r['expected_revision']+1)
  if op=='agent-leave':data['stopped']=True
 elif op=='agent-directory':data=dict(agents=[db['agents'][r['agent_id']]] if r['agent_id'] in db['agents'] else [])
 elif op in ('event-next','session-inbox-claim'):data={'delivery':None}
 elif op=='session-inbox-pending':data={'requests':0,'notices':0,'responses':0}
 elif op=='search':data={'status':'READY','destination':{'name':'hosted','allow_local':False},'selected':[],'index':[],'omitted':{}}
 else:raise AssertionError('Unexpected native fixture operation: '+op)
 if request:db['replies'][request]=data
 store.write_text(json.dumps(db))
 print(json.dumps({'ok':True,'status':'OK','data':data}))
''')
    path.chmod(0o500)


class ScriptedProvider:
    """A real HTTP transport with fixed responses, no model or credentials."""
    def __init__(self):
        self.requests = []
        requests = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if self.path != '/v1/chat/completions' or not 0 < size <= 2 * 1024 * 1024:
                    self.send_error(400)
                    return
                data = json.loads(self.rfile.read(size))
                requests.append(data)
                chunks = [dict(id='r04', object='chat.completion.chunk', created=0, model='fixture',
                               choices=[dict(index=0, delta=dict(role='assistant', content='R04 local fixture reply.'),
                                             finish_reason=None)]),
                          dict(id='r04', object='chat.completion.chunk', created=0, model='fixture',
                               choices=[dict(index=0, delta={}, finish_reason='stop')])]
                body = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError('Scripted provider did not stop')


class NativeServer:
    def __init__(self, binary, root, account, output):
        self.root, self.output = root, output
        root.mkdir(mode=0o700)
        env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', OPENCODE_DISABLE_AUTOUPDATE='true',
                   OPENCODE_DISABLE_MODELS_FETCH='true', OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                   OPENCODE_CONFIG_DIR=str(account), OPENCODE_CONFIG=str(account / 'opencode.json'),
                   CAIRN_DATABASE_URL='host=/absent-r04-native-db dbname=denied')
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
            path = root / key.lower()
            path.mkdir(mode=0o700)
            env[key] = str(path)
        # All plugin imports are type-only or node builtins. Present a sealed,
        # complete configuration rather than letting native bootstrap install
        # npm packages from the network in the disconnected namespace. The
        # selected client's Npm.install explicitly skips unwritable directories.
        global_config = Path(env['XDG_CONFIG_HOME']) / 'opencode'
        global_config.mkdir(mode=0o700)
        (global_config / '.gitignore').write_text('node_modules\n')
        global_config.chmod(0o500)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.logs = [open(output / (root.name + suffix), 'wb') for suffix in ('.stdout', '.stderr')]
        self.trace = output / (root.name + '.exec')
        self.process = subprocess.Popen(
            ['strace', '-f', '-s', '16384', '-e', 'trace=execve', '-o', str(self.trace),
             str(binary), 'serve', '--print-logs', '--hostname', '127.0.0.1', '--port', str(port)],
            cwd=root, env=env, stdout=self.logs[0], stderr=self.logs[1], start_new_session=True)
        try:
            wait_for(self.ready, 'native server ready', timeout=45)
        except BaseException:
            self.close()
            raise

    def ready(self):
        if self.process.poll() is not None:
            raise AssertionError('Native client exited: ' + str(self.process.returncode))
        try:
            return self.request('GET', '/global/health', timeout=1)
        except urllib.error.HTTPError as exc:
            raise AssertionError('Native readiness HTTP error: ' + str(exc.code) + ' ' + exc.read().decode()) from exc
        except OSError:
            return False

    def request(self, method, path, directory=None, body=None, timeout=45):
        if directory is not None:
            path += '?' + urllib.parse.urlencode(dict(directory=str(directory)))
        request = urllib.request.Request(self.url + path, method=method,
                                         data=encoded(body) if body is not None else None,
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        for log in self.logs:
            log.close()


class NativeFixture(InstalledTools, unittest.TestCase):
    fixture = R04HostWiringTests.fixture
    selected = R04HostWiringTests.selected
    stage = R04HostWiringTests.stage
    recall_api = R04HostWiringTests.recall_api

    def run_fixture(self, binary, output):
        self.root = output / 'owned'
        self.root.mkdir(mode=0o700)
        self.observations = []
        f = self.fixture('installation')
        selector = f.root / 'local-null-selector'
        selector.write_text('#!/usr/bin/python3\nimport json,sys\njson.load(sys.stdin)\n'
                            'print(json.dumps({"structured_output":{"checkpoint":None,"workstream":None,"memories":[]}}))\n')
        selector.chmod(0o500)
        f.memory['claude'] = str(selector)
        native_api_fixture(f.api)
        calls = self.recall_api(f)
        original = dict(memory=f.settings.read_bytes(), coordination=f.coord_settings.read_bytes())
        selection_a = self.selected(f)
        (output / 'selection-a.json').write_bytes(encoded(selection_a[2]))
        provider = ScriptedProvider()
        self.addCleanup(provider.close)
        settings = dict(model='fixture/probe', enabled_providers=['fixture'], share='disabled', autoupdate=False,
                        provider={'fixture': dict(npm='@ai-sdk/openai-compatible', name='Local R04 fixture',
                                                 options=dict(baseURL=provider.url, apiKey='synthetic-unused'),
                                                 models={'probe': dict(name='Fixture')})},
                        permission={'*': 'deny'})
        (f.account / 'opencode.json').write_bytes(encoded(settings))
        (f.account / '.gitignore').write_text('node_modules\n')

        def publish(selection):
            _, directory, record = selection
            f.account.chmod(0o700)
            try:
                for name, relative in record['layout'].items():
                    target = f.account / relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if target.exists():
                        target.chmod(0o600)
                    shutil.copyfile(directory / name, target)
                    target.chmod(0o400)
            finally:
                f.account.chmod(0o500)
            # This fixture uses the normal plugin directory; no wrapper plugin.
            self.assertEqual(json.loads((f.account / 'cairn-lifecycle.json').read_bytes())['unrelated'], {'keep': True})

        publish(selection_a)
        first = NativeServer(binary, self.root / 'existing-client', f.account, output)
        self.addCleanup(first.close)
        schema = first.request('GET', '/doc')
        refresh = schema['paths']['/instance/dispose']['post']
        self.assertEqual(refresh['operationId'], 'instance.dispose')
        self.assertIn('directory', [p['name'] for p in refresh['parameters']])
        (output / 'native-refresh-schema.json').write_bytes(encoded(refresh))
        projects = []
        for name in ('target-project', 'control-project'):
            project = self.root / name
            project.mkdir(mode=0o700)
            (project / '.git').mkdir(mode=0o700)
            projects.append(project)
        target, control = projects
        session = first.request('POST', '/session', target, dict(title='R04 target'))['id']
        sibling = first.request('POST', '/session', target, dict(title='R04 same-directory sibling'))['id']
        other = first.request('POST', '/session', control, dict(title='R04 control'))['id']

        def quiet(server):
            # All execFile descendants have terminated. Strace's exit records
            # provide a boundary independent of self-reported hook identities.
            stable = [None, 0]

            def settled():
                raw = server.trace.read_text()
                active = set()
                for line in raw.splitlines():
                    words = line.split(maxsplit=1)
                    if len(words) != 2:
                        continue
                    pid, event = words
                    if 'execve(' in event and '"' + str(binary) + '"' not in event:
                        active.add(pid)
                    if '+++ exited with' in event or '+++ killed by' in event:
                        active.discard(pid)
                stamp = (len(raw), len(calls), len(f.api_calls()))
                if active or stamp != stable[0]:
                    stable[:] = [stamp, time.monotonic()]
                    return False
                return time.monotonic() - stable[1] >= 0.3

            wait_for(settled, 'hook descendants and idle capture settled')

        def prompt(server, project, identity, label, selection, refused=False):
            quiet(server)
            offset = server.trace.stat().st_size
            diagnostics_at = (output / (server.root.name + '.stderr')).stat().st_size
            before_calls = len(calls), len(f.api_calls())
            before_state = state()
            answer = server.request('POST', f'/session/{identity}/message', project,
                                    dict(model=dict(providerID='fixture', modelID='probe'),
                                         parts=[dict(type='text', text='R04 fixture ' + label)]))
            self.assertNotIn('error', answer['info'], answer)
            self.assertTrue(any(p.get('text') == 'R04 local fixture reply.' for p in answer['parts']), answer)
            quiet(server)
            trace = server.trace.read_text()[offset:]
            execs = list(successful_execs(trace))
            diagnostics = (output / (server.root.name + '.stderr')).read_text()[diagnostics_at:]
            _, _, record = selection
            reached = []
            for role in ('memory', 'coordination'):
                events = ('SessionStart', 'UserPromptSubmit') if role == 'memory' else ('TurnStart',)
                rows = [r for r in record['rows'] if r['role'] == role and r['event'] in events]
                launcher = rows[0]['argv'][0]
                launch_execs = [e for e in execs if e['path'] == launcher and e['argv'] == rows[0]['argv']]
                self.assertTrue(launch_execs, (label, role, trace[-5000:]))
                vector = shlex.split(Path(launcher).read_text().splitlines()[1])[1:]
                image = vector[0]
                self.assertEqual(vector[-1], '$@')
                actual = [e for e in execs if e['path'] == image and e['argv'] == vector[:-1] + rows[0]['argv'][1:]
                          and e['pid'] in {p['pid'] for p in launch_execs}]
                self.assertTrue(actual, (label, role, trace[-5000:]))
                reached.append(dict(role=role, launcher=dict(path=launcher, sha256=digest(Path(launcher))),
                                    image=dict(path=image, sha256=digest(Path(image))), argv=rows[0]['argv'],
                                    execs=actual))
            if refused:
                self.assertEqual((len(calls), len(f.api_calls())), before_calls)
                self.assertEqual(state(), before_state)
                self.assertIn('phase=compatibility_preflight', diagnostics)
            else:
                self.assertGreater(len(calls), before_calls[0], (label, f.api_calls(), trace[-3000:]))
                self.assertNotIn('Cairn session presence unavailable', diagnostics, label)
                self.assertNotIn('Cairn lifecycle: operation failed', diagnostics, label)
                capture = next(r['argv'] for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'SessionEnd')
                self.assertTrue(any(e['path'] == capture[0] and e['argv'] == capture for e in execs), label)
            (output / (label + '.exec')).write_text(trace)
            (output / (label + '.stderr')).write_text(diagnostics)
            self.observations.append(dict(case=label, session=identity, project=str(project),
                                          server=server.root.name, expected_selection=selection[0]['selection'],
                                          reached=reached, refused_before_cairn_effects=refused,
                                          trace=dict(name=label + '.exec', sha256=digest(output / (label + '.exec'))),
                                          api_delta=[len(calls)-before_calls[0], len(f.api_calls())-before_calls[1]]))

        def state():
            result = {}
            for role, config in (('memory', f.memory), ('coordination', f.coord)):
                base = Path(config['state_dir'])
                if base.exists():
                    result[role] = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()}
            return result

        prompt(first, target, session, 'fresh-a', selection_a)
        prompt(first, target, session, 'existing-a', selection_a)
        prompt(first, target, sibling, 'sibling-a', selection_a)
        prompt(first, control, other, 'control-a', selection_a)
        before_messages = first.request('GET', f'/session/{session}/message', target)
        # Stage B without rewriting the selected A generation or issued argv.
        f.publish_configuration(enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings),
                           coordination_settings=pin(f.coord_settings))
        selection_b = self.selected(f)
        self.assertNotEqual(selection_a[0]['selection'], selection_b[0]['selection'])
        publish(selection_b)
        prompt(first, target, session, 'cached-a-after-b-publish', selection_a)
        fresh = NativeServer(binary, self.root / 'fresh-client', f.account, output)
        self.addCleanup(fresh.close)
        new_session = fresh.request('POST', '/session', target, dict(title='R04 fresh B'))['id']
        prompt(fresh, target, new_session, 'fresh-b', selection_b)
        self.assertIs(first.request('POST', '/instance/dispose', target, {}), True)
        prompt(first, target, session, 'refreshed-b', selection_b)
        prompt(first, target, sibling, 'sibling-refreshed-b', selection_b)
        prompt(first, control, other, 'control-still-a', selection_a)
        after_messages = first.request('GET', f'/session/{session}/message', target)
        self.assertEqual([m['info']['id'] for m in after_messages[:len(before_messages)]],
                         [m['info']['id'] for m in before_messages])
        self.assertEqual(first.request('GET', f'/session/{session}', target)['id'], session)

        # Both pins are injected only into this owned, disposable installation.
        receipt = f.receipt_path
        receipt_bytes = receipt.read_bytes()
        receipt.unlink()
        try:
            prompt(first, target, session, 'missing-receipt', selection_b, refused=True)
        finally:
            receipt.write_bytes(receipt_bytes)
            receipt.chmod(0o400)
        overwrite_fixture(receipt, receipt_bytes + b'\n')
        try:
            prompt(first, target, session, 'corrupt-receipt', selection_b, refused=True)
        finally:
            overwrite_fixture(receipt, receipt_bytes)
        publish(selection_a)
        prompt(first, target, session, 'cached-b-after-reverse-publish', selection_b)
        self.assertIs(first.request('POST', '/instance/dispose', target, {}), True)
        prompt(first, target, session, 'reversed-a', selection_a)
        self.assertEqual((selection_a[1] / 'previous-cairn-lifecycle.json').read_bytes(), original['memory'])
        self.assertEqual((selection_a[1] / 'previous-cairn-coordination.json').read_bytes(), original['coordination'])
        for selection in (selection_a, selection_b):
            (output / ('selection-' + selection[0]['selection']['sha256'] + '.json')).write_bytes(encoded(selection[2]))
        (output / 'cairn-api-calls.json').write_bytes(encoded(dict(recall=calls, coordination=f.api_calls())))
        (output / 'provider-requests.json').write_bytes(encoded(provider.requests))
        self.assertTrue(provider.requests)
        return dict(schema='cairn.r04-native-opencode/1', passed=True, cases=self.observations,
                    client=dict(path=str(binary), sha256=digest(binary),
                                version=subprocess.check_output([binary, '--version'], text=True).strip()),
                    refresh=dict(operation='instance.dispose', scope='directory instance, all sessions in that instance',
                                 sibling_refreshed=True, other_directory_unchanged=True),
                    limits=['Disposable scripted-provider client only; no real account/provider or production apply.',
                            'No single-thread refresh claim, full watcher-unit barrier, or other harness qualification.',
                            'Kernel execve observations select commands/images; no self-reported identity attestation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--archive-stdout', action='store_true', help='Emit selected synthetic artifacts for an isolated runner')
    args = parser.parse_args()
    if digest(args.opencode) != args.sha256:
        parser.error('Selected native client hash mismatch')
    if not shutil.which('strace'):
        parser.error('strace is required for actual exec evidence')
    # Require the established wrapper, which supplies these private homes and
    # removes non-loopback routes. This command never discovers a live server.
    if os.environ.get('HOME') != '/tmp/home' or not Path('/tmp/work-tmp').is_dir():
        parser.error('Run inside scripts/test_hybrid_runtime_operations.py')
    with tempfile.TemporaryDirectory(prefix='r04n-', dir='/tmp') as temporary:
        output = Path(temporary)
        test = NativeFixture()
        report = None
        try:
            test.setUpClass()
            report = test.run_fixture(args.opencode.resolve(), output)
        finally:
            cleanup_ok = test.doCleanups()
            if hasattr(test, 'build'):
                test.tearDownClass()
            # Only selected synthetic logs/pins leave the ephemeral namespace;
            # native homes, databases, tokens and generated binaries do not.
            if report is not None:
                report['cleanup_passed'] = cleanup_ok
                report['passed'] = report['passed'] and cleanup_ok
                (output / 'report.json').write_bytes(encoded(report))
            if args.archive_stdout:
                archive = io.BytesIO()
                with tarfile.open(fileobj=archive, mode='w:gz') as tar:
                    for path in sorted(output.iterdir()):
                        if path.is_file():
                            tar.add(path, arcname=path.name)
                print(json.dumps(dict(archive_base64=base64.b64encode(archive.getvalue()).decode())))
            if not cleanup_ok:
                raise AssertionError('Native fixture cleanup failed')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
