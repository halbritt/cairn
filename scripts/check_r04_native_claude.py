"""Disposable native Claude hook selections and same-conversation resume.

This checks explicit --settings snapshots across an orderly process restart,
not an in-process patch, filesystem watcher or interactive trust menu. All
provider and Cairn responses are local.
"""
import argparse
import base64
import http.server
import io
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import uuid

from check_claude_tools import response_events
from check_r04_native_opencode import digest, native_api_fixture, successful_execs, wait_for
from test_f04_activation import encoded, overwrite_fixture
from test_r02_compatibility_install import InstalledTools
from test_r04_host_wiring import R04HostWiringTests, pin


class MessagesFixture:
    def __init__(self):
        self.requests, self.failures = [], []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if self.path.split('?')[0] != '/v1/messages' or not 0 < size <= 2 * 1024 * 1024:
                    owner.failures.append(dict(path=self.path, bytes=size))
                    self.send_error(400)
                    return
                request = json.loads(self.rfile.read(size))
                owner.requests.append(request)
                body = response_events('r04_' + str(len(owner.requests)), request['model'],
                                       dict(type='text', text='R04 local fixture reply.'))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError('Local Messages fixture did not stop')


class ClaudeSession:
    def __init__(self, binary, root, account, settings, provider, output, work, *, resume=None):
        self.root, self.output = root, output
        self.identity = resume or str(uuid.uuid4())
        root.mkdir(mode=0o700)
        home = root / 'home'
        home.mkdir(mode=0o700)
        env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME=str(home), CLAUDE_CONFIG_DIR=str(account),
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false',
                   ANTHROPIC_API_KEY='r04-synthetic-unused', ANTHROPIC_BASE_URL=provider.url,
                   CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS='150000',
                   CAIRN_DATABASE_URL='host=/absent-r04-native-db dbname=denied')
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
            directory = root / key.lower()
            directory.mkdir(mode=0o700)
            env[key] = str(directory)
        self.trace = output / (root.name + '.exec')
        self.diagnostics = output / (root.name + '.stderr')
        self.stderr = open(self.diagnostics, 'wb')
        self.log = open(output / (root.name + '.events.jsonl'), 'wb')
        self.events, self.buffer = [], bytearray()
        self.process = subprocess.Popen(
            ['strace', '-f', '-s', '16384', '-e', 'trace=execve', '-o', str(self.trace), str(binary),
             '--print', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json',
             '--setting-sources', '', '--settings', str(settings), '--strict-mcp-config', '--mcp-config',
             '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk', '--max-turns', '3',
             '--model', 'sonnet', '--resume' if resume else '--session-id', self.identity],
            cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.control('initialize')
        except BaseException:
            self.close()
            raise

    def send(self, message):
        self.process.stdin.write(encoded(message) + b'\n')
        self.process.stdin.flush()

    def receive(self, predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                raw, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                event = json.loads(raw)
                self.events.append(event)
                # Initialization includes a large bundled command catalogue;
                # retain only selected protocol evidence, not that catalogue.
                if event.get('type') != 'control_response':
                    self.log.write(encoded(event) + b'\n')
                    self.log.flush()
                if predicate(event):
                    return event
            if not self.selector.select(timeout=min(0.2, max(0, deadline-time.monotonic()))):
                if self.process.poll() is not None:
                    raise AssertionError('Native Claude exited before its response')
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError('Native Claude stdout closed before its response')
            self.buffer.extend(chunk)
        raise AssertionError('Native Claude response timed out')

    def control(self, kind, **fields):
        request = str(uuid.uuid4())
        self.send(dict(type='control_request', request_id=request, request=dict(subtype=kind, **fields)))
        event = self.receive(lambda e: e.get('type') == 'control_response' and
                             e['response'].get('request_id') == request)
        response = event['response']
        if response.get('subtype') != 'success':
            raise AssertionError('Native control refused ' + kind + ': ' + json.dumps(response))
        result = response.get('response', {})
        if kind != 'initialize':
            self.log.write(encoded(dict(control=kind, response=result)) + b'\n')
            self.log.flush()
        return result

    def prompt(self, text):
        request = str(uuid.uuid4())
        self.send(dict(type='user', uuid=request, message=dict(role='user', content=text)))
        result = self.receive(lambda e: e.get('type') == 'result')
        if result.get('session_id') != self.identity:
            raise AssertionError('Native conversation identity changed')
        return result

    def close(self):
        forced = False
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    # Continue draining stdout: a pending SessionEnd can emit hook
                    # messages, and a full pipe must not block orderly shutdown.
                    deadline = time.monotonic() + 30
                    while self.process.poll() is None and time.monotonic() < deadline:
                        if self.selector.select(timeout=0.1):
                            chunk = os.read(self.process.stdout.fileno(), 65536)
                            if chunk:
                                self.buffer.extend(chunk)
                    self.process.wait(timeout=max(0.1, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    forced = True
                    if self.process.pid <= 1:
                        raise AssertionError('Refusing to signal an unsafe native client PID')
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.stderr.close()
            self.log.close()
        if forced:
            raise AssertionError('Native Claude required forced shutdown')
        if self.process.returncode != 0:
            raise AssertionError('Native Claude shutdown exit: ' + str(self.process.returncode))
        if any(line.startswith('SessionEnd hook [') and 'failed:' in line
               for line in self.diagnostics.read_text().splitlines()):
            raise AssertionError('Native Claude SessionEnd hook failed: ' + str(self.diagnostics))


class NativeFixture(InstalledTools, unittest.TestCase):
    fixture = R04HostWiringTests.fixture
    selected = R04HostWiringTests.selected
    stage = R04HostWiringTests.stage
    recall_api = R04HostWiringTests.recall_api

    def run_fixture(self, binary, output, *, bound_agent_tools=False):
        self.root = output / 'owned'
        self.root.mkdir(mode=0o700)
        f = self.fixture('installation', harness='claude', bound=bound_agent_tools)
        native_api_fixture(f.api)
        selector = f.root / 'null-selector'
        selector.write_text('#!/usr/bin/python3\nimport json,sys\njson.load(sys.stdin)\n'
                            'print(json.dumps({"structured_output":{"checkpoint":None,"workstream":None,"memories":[]}}))\n')
        selector.chmod(0o500)
        f.memory['claude'] = str(selector)
        f.memory['recall_mode'] = 'agent_tools'
        f.coord.update(process_names=[binary.name], process_exe_dirs=[])
        recall_calls = self.recall_api(f)
        f.publish_configuration(enabled=bound_agent_tools)
        original = encoded(dict(alwaysThinkingEnabled=False, permissions=dict(defaultMode='dontAsk')))
        overwrite_fixture(f.settings, original)
        f.hook_plan.update(selection=f.selected, settings=pin(f.settings))
        a = self.selected(f)
        provider = MessagesFixture()
        self.addCleanup(provider.close)
        work = self.root / 'work'
        work.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '--quiet', str(work)], check=True, capture_output=True, timeout=5)
        observations, refreshes, startups = [], [], []
        image_pins = {name: dict(path=str(path), sha256=digest(path), mode=oct(path.stat().st_mode & 0o777),
                      build_info=subprocess.check_output(['go', 'version', '-m', str(path)], text=True))
                      for name, path in (('lifecycle', self.binary), ('stager', self.stager))}
        (output / 'image-pins.json').write_bytes(encoded(image_pins))
        self.addCleanup(lambda: (output / 'recall-api-calls.json').write_bytes(
            encoded([dict(operation=op) for op, _authorization in recall_calls])))
        self.addCleanup(lambda: (output / 'provider-requests.json').write_bytes(encoded(provider.requests)))
        self.addCleanup(lambda: (output / 'cairn-api-calls.json').write_bytes(encoded(f.api_calls())))
        self.addCleanup(lambda: (output / 'observations.json').write_bytes(encoded(observations)))

        def reached_hooks(trace, label, selection, event, refused=False):
            executions = list(successful_execs(trace))
            launch_root = str(f.root / 'release' / 'launch') + '/'
            selected_launchers = {row['argv'][0] for row in selection[2]['rows']}
            self.assertEqual([e for e in executions if e['path'].startswith(launch_root)
                              and e['path'] not in selected_launchers], [], label)
            reached = []
            for role in ('memory', 'coordination'):
                row = next(r for r in selection[2]['rows'] if r['role'] == role and r['event'] == event)
                launcher = row['argv'][0]
                issued = [e for e in executions if e['path'] == launcher and e['argv'] == row['argv']]
                self.assertTrue(issued, (label, role, trace[-5000:]))
                # Shell launchers preserve the native event on stdin. Their
                # entire exec statement, including a retained bootstrap, is one vector.
                vector = shlex.split(Path(launcher).read_text().split('\n', 1)[1])[1:]
                self.assertEqual(vector[-1], '$@')
                if role == 'memory':
                    self.assertEqual((row['implementation'], row['hook']['timeout']), ('go', 13))
                    self.assertEqual(vector[1], 'memory')
                    self.assertEqual(len(vector), 20)
                    self.assertEqual(vector[2:-2:2], ['--receipt', '--receipt-sha256', '--role', '--entrypoint',
                        '--config', '--hook-timeout', '--launch-root', '--shell'])
                    self.assertEqual(vector[vector.index('--hook-timeout') + 1], '13')
                    self.assertEqual(vector[vector.index('--role') + 1], 'memory')
                actual = [e for e in executions if e['pid'] in {i['pid'] for i in issued} and
                          e['path'] == vector[0] and e['argv'] == vector[:-1]+row['argv'][1:]]
                self.assertTrue(actual, (label, role, trace[-5000:]))
                exits = {e['pid']: re.findall(r'^' + e['pid'] + r'\s+\+\+\+ exited with (\d+) \+\+\+$',
                                             trace, flags=re.MULTILINE) for e in actual}
                self.assertEqual(exits, {e['pid']: ['1' if refused else '0'] for e in actual},
                                 (label, role, exits))
                reached.append(dict(role=role, implementation=row['implementation'], argv=row['argv'],
                                    launcher=dict(path=launcher, sha256=digest(Path(launcher))),
                                    program=dict(path=vector[0], sha256=digest(Path(vector[0]))),
                                    execs=actual, exits=exits))
            return reached

        def start(name, selection, *, resume=None):
            client = ClaudeSession(binary, self.root / name, f.account, Path(selection[0]['settings']['path']),
                                   provider, output, work, resume=resume)
            self.addCleanup(client.close)
            readback(client, selection)
            quiet(client)
            trace = client.trace.read_text()
            reached = reached_hooks(trace, name, selection, 'SessionStart')
            self.assertEqual([e for e in client.events if e.get('subtype') == 'hook_response' and
                              (e.get('exit_code') != 0 or e.get('outcome') != 'success')], [], name)
            startups.append(dict(client=name, session_id=client.identity, reached=reached,
                                 selection=selection[0]['selection']))
            return client

        def state():
            return {role: {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()}
                    for role, cfg in (('memory', f.memory), ('coordination', f.coord))
                    if (base := Path(cfg['state_dir'])).exists()}

        def readback(client, selection):
            expected = json.loads(Path(selection[0]['settings']['path']).read_bytes())
            actual = client.control('get_settings')
            self.assertEqual(actual['effective']['hooks'], expected['hooks'])
            self.assertEqual(actual['effective']['alwaysThinkingEnabled'], False)
            self.assertEqual(actual['effective']['permissions'], dict(defaultMode='dontAsk'))
            listing = client.control('get_hooks_listing')
            for row in selection[2]['rows']:
                self.assertTrue(any(h['event'] == row['event'] and h.get('commandText') == row['hook']['command']
                                    and h.get('source') == 'flagSettings' for h in listing['hooks']), row)
            return dict(settings=actual, listing=listing)

        def quiet(client):
            stable = [None, time.monotonic()]

            def settled():
                raw = client.trace.read_text()
                active = set()
                for line in raw.splitlines():
                    pieces = line.split(maxsplit=1)
                    if len(pieces) != 2:
                        continue
                    pid, event = pieces
                    if 'execve(' in event and '"' + str(binary) + '"' not in event:
                        active.add(pid)
                    if '+++ exited with' in event or '+++ killed by' in event:
                        active.discard(pid)
                stamp = (len(raw), len(f.api_calls()))
                if active or stamp != stable[0]:
                    stable[:] = [stamp, time.monotonic()]
                    return False
                return time.monotonic() - stable[1] >= 0.3

            wait_for(settled, 'native hook descendants settled')

        def prompt(client, label, selection, refused=False):
            quiet(client)
            offset = client.trace.stat().st_size
            events_at = len(client.events)
            before = state(), len(f.api_calls()), len(provider.requests), len(recall_calls)
            result = client.prompt('R04 fixture ' + label)
            quiet(client)
            trace = client.trace.read_text()[offset:]
            reached = reached_hooks(trace, label, selection, 'UserPromptSubmit', refused)
            hook_errors = [e for e in client.events[events_at:] if e.get('subtype') == 'hook_response' and
                           (e.get('exit_code') != 0 or e.get('outcome') != 'success')]
            if refused:
                self.assertEqual(state(), before[0])
                self.assertEqual(len(f.api_calls()), before[1])
                self.assertEqual(len(recall_calls), before[3])
            else:
                self.assertEqual(result.get('subtype'), 'success', result)
                self.assertFalse(result.get('is_error'), result)
                self.assertEqual(hook_errors, [], label)
                self.assertGreater(len(f.api_calls()), before[1], label)
                self.assertGreater(len(provider.requests), before[2], label)
                matching = [r for r in provider.requests[before[2]:]
                            if 'R04 fixture ' + label in json.dumps(r['messages'])]
                self.assertTrue(matching, label)
                serialized = json.dumps(matching[-1]['messages'])
                if label != 'fresh-a' and client is target:
                    self.assertIn('R04 fixture fresh-a', serialized)
            self.assertEqual(provider.failures, [])
            (output / (label + '.exec')).write_text(trace)
            view = readback(client, selection)
            observations.append(dict(case=label, session_id=client.identity, client=client.root.name,
                                     selection=selection[0]['selection'], reached=reached,
                                     result=dict(subtype=result.get('subtype'), is_error=result.get('is_error')),
                                     native_readback=view, refused_before_cairn_effects=refused,
                                     api_delta=len(f.api_calls())-before[1], provider_delta=len(provider.requests)-before[2],
                                     recall_operations=[op for op, _authorization in recall_calls[before[3]:]],
                                     trace=dict(name=label+'.exec', sha256=digest(output/(label+'.exec')))))

        target = start('target-client', a)
        control = start('control-client', a)
        prompt(target, 'fresh-a', a)
        prompt(target, 'existing-a', a)
        prompt(control, 'control-a', a)
        f.publish_configuration(enabled=bound_agent_tools)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings))
        b = self.selected(f)
        self.assertNotEqual(a[0]['selection'], b[0]['selection'])
        prompt(target, 'cached-a-after-b-stage', a)
        fresh = start('fresh-client', b)
        prompt(fresh, 'fresh-b', b)

        def refresh(selection):
            nonlocal target
            previous = target
            before_provider = len(provider.requests)
            # apply_flag_settings appends hook lists in the selected native
            # client. Restart with the exact settings and resume its history.
            previous.close()
            self.assertEqual(previous.process.returncode, 0)
            target = start('resumed-client-' + str(len(refreshes) + 1), selection,
                           resume=previous.identity)
            self.assertEqual(target.identity, previous.identity)
            self.assertNotEqual(target.process.pid, previous.process.pid)
            readback(target, selection)
            self.assertEqual(len(provider.requests), before_provider)
            refreshes.append(dict(session_id=target.identity, previous_client=previous.root.name,
                                  client=target.root.name, previous_exit=previous.process.returncode,
                                  selection=selection[0]['selection']))

        refresh(b)
        prompt(target, 'refreshed-b', b)
        prompt(control, 'control-still-a', a)
        receipt, saved = f.receipt_path, f.receipt_path.read_bytes()
        receipt.unlink()
        try:
            prompt(target, 'missing-receipt', b, refused=True)
        finally:
            receipt.write_bytes(saved)
            receipt.chmod(0o400)
        overwrite_fixture(receipt, saved+b'\n')
        try:
            prompt(target, 'corrupt-receipt', b, refused=True)
        finally:
            overwrite_fixture(receipt, saved)
        prompt(target, 'cached-b-before-reverse', b)
        refresh(a)
        prompt(target, 'reversed-a', a)
        self.assertEqual((a[1]/'previous-settings.json').read_bytes(), original)
        for label, selection in (('a', a), ('b', b)):
            (output / ('selection-'+label+'.json')).write_bytes(encoded(selection[2]))
        for label, record in (('a', a[2]), ('b', b[2])):
            memory_row = next(r for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'SessionStart')
            vector = shlex.split(Path(memory_row['argv'][0]).read_text().split('\n', 1)[1])[1:]
            config = Path(vector[vector.index('--config') + 1])
            receipt_path = Path(vector[vector.index('--receipt') + 1])
            receipt = json.loads(receipt_path.read_bytes())
            (output / ('receipt-'+label+'.json')).write_bytes(encoded(receipt))
            (output / ('profiles-'+label+'.json')).write_bytes(encoded(dict(
                memory=json.loads(config.read_bytes()),
                coordination=[json.loads(Path(row['path']).read_bytes()) for row in receipt['coordination_configs']],
                binding=json.loads(Path(receipt['binding']['path']).read_bytes()))))
            trace_path = config.parent / 'invocation-trace' / 'trace.jsonl'
            if trace_path.exists():
                (output / ('invocations-'+label+'.jsonl')).write_bytes(trace_path.read_bytes())
        (output / 'cairn-api-calls.json').write_bytes(encoded(f.api_calls()))
        (output / 'provider-requests.json').write_bytes(encoded(provider.requests))
        return dict(schema='cairn.r04-native-claude/1', passed=True, cases=observations, startups=startups,
                    profile='bound-agent-tools' if bound_agent_tools else 'unbound',
                    image_pins=image_pins,
                    fixture=dict(sha256=digest(Path(__file__))),
                    client=dict(path=str(binary), sha256=digest(binary),
                                version=subprocess.check_output([binary, '--version'], text=True, timeout=10).strip()),
                    refresh=dict(operation='orderly stop/join, then --resume with --settings',
                                 scope='same conversation UUID and history; new process',
                                 settings_layer='flagSettings', transitions=refreshes,
                                 control_remained_a=True),
                    limits=['Explicit --settings snapshots and same-conversation process restart only; no in-process patch, filesystem watcher or interactive trust claim.',
                            'Same private Go image with different receipt/config/V/L; not a code-image upgrade.',
                            'Selected checked Go recall/coordination are observed; bound tool rows retain Python and are not exercised as Go.',
                            'Local scripted provider and synthetic Cairn API; no real model/account or production apply.',
                            'No complete watcher-unit barrier, store/auth qualification or full R04/R06 acceptance.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--archive-stdout', action='store_true')
    parser.add_argument('--profile', choices=('unbound', 'bound-agent-tools'), default='unbound')
    args = parser.parse_args()
    if digest(args.claude) != args.sha256:
        parser.error('Selected native client hash mismatch')
    if os.environ.get('HOME') != '/tmp/home' or not Path('/tmp/work-tmp').is_dir():
        parser.error('Run inside scripts/test_hybrid_runtime_operations.py')
    with tempfile.TemporaryDirectory(prefix='r04c-', dir='/tmp') as temporary:
        output = Path(temporary)
        test, report = NativeFixture(), None
        try:
            test.setUpClass()
            report = test.run_fixture(args.claude.resolve(), output, bound_agent_tools=args.profile == 'bound-agent-tools')
        finally:
            cleanup_ok = test.doCleanups()
            if hasattr(test, 'build'):
                test.tearDownClass()
            if digest(args.claude) != args.sha256:
                raise AssertionError('Selected native client changed during fixture')
            if report is not None:
                report['cleanup_passed'] = cleanup_ok
                report['passed'] = report['passed'] and cleanup_ok
                (output/'report.json').write_bytes(encoded(report))
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
