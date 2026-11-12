"""Disposable native Agy project hooks, checked coordination and conversation resume."""
import argparse
import base64
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import uuid

from check_r04_native_opencode import digest, native_api_fixture, successful_execs, wait_for
from r04_agy_trace import calls, exec_lines, exits, file_read, hook_writes, io_bytes, joined_pids, loopback_connections, opened_paths, thread_pids, walked_path
from test_f04_activation import encoded, overwrite_fixture
from test_r02_compatibility_install import InstalledTools
from test_r04_host_wiring import R04HostWiringTests, pin


MODEL = 'Gemini 3.8 Flash (Low)'


class GeminiFixture:
    def __init__(self):
        self.requests, self.failures = [], []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('x-goog-api-key') != 'r04-synthetic-unused' or self.headers.get('Authorization'):
                    owner.failures.append(dict(error='unexpected fixture authorization'))
                    self.send_error(401)
                    return
                if not self.path.startswith('/v1beta/models/') or not 0 < size <= 2 * 1024 * 1024:
                    owner.failures.append(dict(path=self.path, bytes=size))
                    self.send_error(400)
                    return
                request = json.loads(self.rfile.read(size))
                owner.requests.append(dict(path=self.path, body=request, synthetic_key_verified=True))
                response = dict(candidates=[dict(content=dict(role='model', parts=[dict(text='R04 local Agy reply.')]),
                                                  finishReason='STOP', index=0)],
                                usageMetadata=dict(promptTokenCount=10, candidatesTokenCount=5, totalTokenCount=15),
                                modelVersion='gemini-3.8-flash')
                body = ('data: ' + json.dumps(response) + '\n\n').encode()
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
            raise AssertionError('Local Gemini fixture did not stop')


class TracedProcess:
    def __init__(self, argv, root, env, output, work, *, string_limit=8192):
        self.argv, self.binary = argv, Path(argv[0]).resolve()
        self.before_hash = digest(self.binary)
        self.trace = output / (root.name + '.trace')
        self.stderr_path = output / (root.name + '.stderr')
        self.stderr = self.stderr_path.open('wb')
        self.closed, self.forced = False, False
        self.process = subprocess.Popen(
            ['strace', '-f', '-xx', '-s', str(string_limit), '-e',
             'trace=execve,openat,connect,read,write,close,clone,clone3,readlink,exit,exit_group', '-o', str(self.trace), *argv],
            cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, start_new_session=True)
        self.native_pid = None
        try:
            wait_for(self.identify, 'owned native executable', timeout=15)
        except BaseException:
            # Native startup failures are not qualified. A missing identity
            # cannot authorize signalling an unrelated PID.
            self.process.stdin.close()
            self.process.wait(timeout=20)
            self.stderr.close()
            raise

    def identify(self):
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                parent = int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1])
                executable = os.readlink(entry / 'exe')
            except (OSError, ValueError, IndexError):
                continue
            if parent == self.process.pid and executable == str(self.binary):
                self.native_pid, self.native_exe = int(entry.name), executable
                return True
        return False

    def signal_native(self, value):
        if self.native_pid is None or self.native_pid <= 1:
            raise AssertionError('Refusing to signal an unsafe native PID')
        try:
            executable = os.readlink(f'/proc/{self.native_pid}/exe')
        except FileNotFoundError:
            return
        if executable != self.native_exe:
            raise AssertionError('Owned native executable identity changed')
        os.kill(self.native_pid, value)

    def close(self, *, timeout=30, close_stdin=True):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.poll() is None:
                if close_stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.forced = True
                    self.signal_native(signal.SIGTERM)
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.signal_native(signal.SIGKILL)
                        self.process.wait(timeout=5)
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
            self.stderr.close()
        self.after_hash = digest(self.binary)
        self.pids = joined_pids(self.trace.read_text())
        if self.before_hash != self.after_hash:
            raise AssertionError('Selected executable changed during execution')
        if self.forced:
            raise AssertionError('Native process required forced shutdown')
        if self.process.returncode != 0:
            raise AssertionError('Native process exit: ' + str(self.process.returncode))


class AgySession:
    def __init__(self, binary, root, env, settings, output, work, *, resume=None, string_limit=8192):
        self.output, self.work = output, work
        root.mkdir(mode=0o700)
        argv = [str(binary), '--conversation', resume] if resume else [str(binary), '--new-project']
        argv += ['--settings', str(settings), '--model', MODEL, '--input-format', 'stream-json',
                 '--output-format', 'stream-json', '--print-timeout', '30s', '--print', '']
        self.child = TracedProcess(argv, root, env, output, work, string_limit=string_limit)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.child.process.stdout, selectors.EVENT_READ)
        self.buffer, self.events = bytearray(), []
        self.log = (output / (root.name + '.events.jsonl')).open('wb')
        self.closed = False
        try:
            initial = self.receive(lambda event: event.get('event') == 'init')
            self.identity = initial['conversation_id']
            if resume and self.identity != resume:
                raise AssertionError('Native resume changed conversation ID')
        except BaseException:
            self.close()
            raise

    def receive(self, predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                raw, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                event = json.loads(raw)
                self.events.append(event)
                saved = event
                if event.get('event') == 'init':
                    saved = dict(event='init', conversation_id=event['conversation_id'],
                                 init={key: value for key, value in event['init'].items() if key != 'tools'})
                self.log.write(encoded(saved) + b'\n')
                self.log.flush()
                if predicate(event):
                    return event
            if self.selector.select(timeout=.1):
                chunk = os.read(self.child.process.stdout.fileno(), 65536)
                if not chunk:
                    raise AssertionError('Native Agy stdout closed before its response')
                self.buffer.extend(chunk)
            elif self.child.process.poll() is not None:
                raise AssertionError('Native Agy exited before its response')
        raise AssertionError('Native Agy result timed out')

    def prompt(self, text):
        self.child.process.stdin.write(encoded(dict(event='user', message=dict(role='user', content=text))) + b'\n')
        self.child.process.stdin.flush()
        result = self.receive(lambda event: event.get('event') == 'result')['result']
        if result['conversation_id'] != self.identity:
            raise AssertionError('Native conversation ID changed')
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.child.close()
        finally:
            self.selector.close()
            self.log.close()


class NativeFixture(InstalledTools, unittest.TestCase):
    fixture = R04HostWiringTests.fixture
    selected = R04HostWiringTests.selected
    stage = R04HostWiringTests.stage

    def run_fixture(self, binary, output):
        self.root = output / 'owned'
        self.root.mkdir(mode=0o700)
        f = self.fixture('installation', harness='agy')
        native_api_fixture(f.api)
        home = f.root / 'native home'
        native_profile = home / '.gemini' / 'antigravity-cli'
        native_profile.mkdir(parents=True, mode=0o700)
        for parent in (home, home / '.gemini'):
            parent.chmod(0o700)
        f.coord.update(process_names=[binary.name], process_exe_dirs=[])
        f.publish_configuration(enabled=False)
        f.settings = f.root / 'original-hooks.json'
        original = encoded({'fixture-unrelated': {'enabled': False}})
        f.settings.write_bytes(original)
        f.settings.chmod(0o600)
        f.hook_plan.update(account_home=str(f.account), selection=f.selected, settings=pin(f.settings))
        a = self.selected(f)
        (output / 'a-receipt.json').write_bytes(f.receipt_path.read_bytes())
        string_limit = max(2048, len(f.receipt_path.read_bytes()) + 1)
        provider = GeminiFixture()
        self.addCleanup(provider.close)
        settings = f.account / 'settings.json'
        settings.write_bytes(encoded(dict(modelProvider='gemini')))
        settings.chmod(0o600)
        self.assertFalse(Path('/var/run/nscd/socket').exists())
        env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', TERM='dumb', HOME=str(home),
                   ANTIGRAVITY_CLI_CONFIG_DIR=str(f.account), GEMINI_API_KEY='r04-synthetic-unused',
                   GOOGLE_GEMINI_BASE_URL=provider.url, NO_PROXY='127.0.0.1,localhost',
                   CAIRN_DATABASE_URL='host=/absent-r04-agy-db dbname=denied')
        work, control_work = self.root / 'target workspace', self.root / 'control workspace'
        for directory in (work, control_work):
            directory.mkdir(mode=0o700)
            (directory / '.agents').mkdir(mode=0o700)
            subprocess.run(['git', 'init', '--quiet', str(directory)], check=True, capture_output=True, timeout=5)
        observations, clients, transitions = [], [], []
        self.addCleanup(lambda: (output / 'provider-requests.json').write_bytes(encoded(provider.requests)))
        self.addCleanup(lambda: (output / 'cairn-api-calls.json').write_bytes(encoded(f.api_calls())))
        self.addCleanup(lambda: (output / 'observations.json').write_bytes(encoded(observations)))
        self.addCleanup(lambda: (output / 'transitions.json').write_bytes(encoded(transitions)))

        def publish(selection, directory, label):
            raw = Path(selection[0]['settings']['path']).read_bytes()
            target = directory / '.agents' / 'hooks.json'
            staged = target.with_suffix('.staged')
            staged.write_bytes(raw)
            staged.chmod(0o600)
            os.replace(staged, target)
            self.assertEqual(digest(target), selection[0]['settings']['sha256'])
            (output / (label + '-hooks.json')).write_bytes(raw)
            (output / (label + '-selection.json')).write_bytes(encoded(selection[2]))

        def start(label, directory, *, resume=None):
            client = AgySession(binary, self.root / label, env, settings, output, directory,
                                resume=resume, string_limit=string_limit)
            clients.append(client)
            self.addCleanup(client.close)
            return client

        def state():
            base = Path(f.coord['state_dir'])
            return {str(path.relative_to(base)): path.read_bytes() for path in base.rglob('*') if path.is_file()}

        def prompt(client, label, selection, *, refusal=None):
            before = state(), len(f.api_calls()), len(provider.requests)
            offset = client.child.trace.stat().st_size
            result = client.prompt('R04 fixture ' + label)
            rows = selection[2]['rows']

            def finished():
                trace = client.child.trace.read_bytes()[offset:].decode()
                try:
                    executions = list(successful_execs(exec_lines(trace)))
                except AssertionError as error:
                    if str(error) == 'Unfinished execve evidence':
                        return None
                    raise
                found = []
                for row in rows:
                    issued = [entry for entry in executions if entry['path'] == row['argv'][0] and entry['argv'] == row['argv']]
                    if not issued or not exits(trace, issued[0]['pid']):
                        return None
                    self.assertEqual(len(issued), 1, (label, row, issued))
                    found.append((row, issued[0]))
                return trace, executions, found

            trace, executions, found = wait_for(finished, 'synchronous native callback exits')
            all_trace = client.child.trace.read_text()
            native_threads = thread_pids(all_trace, client.child.native_pid)
            produced = hook_writes(trace, native_threads)
            self.assertEqual([record['payload']['hookEventName'] for record in produced], ['PreInvocation', 'Stop'])
            for record in produced:
                self.assertEqual(record['payload']['conversationId'], client.identity)
                self.assertEqual(record['payload']['workspacePaths'], [str(client.work)])
                history = Path(record['payload']['transcriptPath'])
                self.assertTrue(history.is_absolute() and home in history.parents)
                self.assertIn(client.identity, history.parts)
                client.native_history_path = history
                if record['payload']['hookEventName'] == 'Stop':
                    self.assertIs(record['payload']['fullyIdle'], True)
            reached = []
            for row, issued in found:
                self.assertEqual(row['role'], 'coordination')
                self.assertEqual(row['implementation'], 'go')
                vector = shlex.split(Path(row['argv'][0]).read_text().split('\n', 1)[1])[1:]
                self.assertEqual(vector[-1], '$@')
                program = [entry for entry in executions if entry['pid'] == issued['pid'] and
                           entry['path'] == vector[0] and entry['argv'] == vector[:-1] + row['argv'][1:]]
                self.assertEqual(len(program), 1, (label, row, program))
                self.assertEqual(exits(trace, issued['pid']), ['exited with ' + ('1' if refusal else '0')])
                threads = thread_pids(trace, issued['pid'])
                raw = io_bytes(trace, threads, 0, 'read')
                stdout = io_bytes(trace, threads, 1, 'write')
                stderr = io_bytes(trace, threads, 2, 'write')
                payload = json.loads(raw) if raw else None
                receipt_read = None
                # The checked launcher carries the receipt path/hash in its
                # literal program vector, before the hook tail.
                receipt_path = vector[vector.index('--receipt') + 1]
                receipt_hash = vector[vector.index('--receipt-sha256') + 1]
                observed = file_read(trace, threads, 'receipt.json')
                self.assertEqual(walked_path(trace, threads, 'receipt.json'), receipt_path)
                if refusal:
                    self.assertEqual(stderr, b'Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
                    self.assertEqual(len([event for _, _, event in calls(trace, {'write'}, pid=threads)
                                          if event.startswith('write(2,')]), 1)
                    self.assertEqual([event for _, _, event in calls(trace, {'write'}, pid=threads)
                                      if event.startswith('write(1,')], [])
                    self.assertEqual(vector[vector.index('--role') + 1], 'coordination')
                    if refusal == 'missing':
                        self.assertEqual(observed, dict(open_error='ENOENT (No such file or directory)', raw=b''))
                    else:
                        self.assertIsNone(observed['open_error'])
                        self.assertEqual(observed['raw'], corrupt_receipt)
                        self.assertNotEqual(hashlib.sha256(observed['raw']).hexdigest(), receipt_hash)
                    receipt_read = dict(open_error=observed['open_error'], bytes=len(observed['raw']),
                                        sha256=hashlib.sha256(observed['raw']).hexdigest())
                else:
                    self.assertTrue(raw, (label, issued['pid']))
                    self.assertEqual(payload['conversationId'], client.identity)
                    self.assertEqual(payload['workspacePaths'], [str(client.work)])
                    self.assertEqual(payload['hookEventName'], row['event'])
                    self.assertEqual(payload, next(record['payload'] for record in produced
                                                   if record['payload']['hookEventName'] == row['event']))
                    if row['event'] == 'Stop':
                        self.assertIs(payload['fullyIdle'], True)
                    self.assertEqual(stderr, b'')
                    self.assertIsNone(observed['open_error'])
                    self.assertEqual(hashlib.sha256(observed['raw']).hexdigest(), receipt_hash)
                    receipt_read = dict(open_error=None, bytes=len(observed['raw']), sha256=receipt_hash)
                reached.append(dict(event=row['event'], implementation=row['implementation'], pid=issued['pid'], threads=threads,
                    argv=row['argv'], launcher=pin(Path(row['argv'][0])), program=pin(Path(vector[0])),
                    raw_stdin_sha256=hashlib.sha256(raw).hexdigest(), payload=payload,
                    stdout=stdout.decode(), stderr=stderr.decode(), receipt_read=receipt_read, exec=program[0]))
            selected_launchers = {row['argv'][0] for row in rows}
            launch_root = str(f.root / 'release' / 'launch') + '/'
            self.assertEqual([entry for entry in executions if entry['path'].startswith(launch_root)
                              and entry['path'] not in selected_launchers], [])
            self.assertIn(str(client.work / '.agents' / 'hooks.json'),
                          opened_paths(all_trace, native_threads))
            self.assertEqual(digest(client.work / '.agents' / 'hooks.json'), selection[0]['settings']['sha256'])
            if refusal:
                self.assertEqual(state(), before[0])
                self.assertEqual(len(f.api_calls()), before[1])
                contexts = []
            else:
                self.assertEqual(result['status'], 'SUCCESS', result)
                api_calls = f.api_calls()[before[1]:]
                contexts = [json.loads(call['input']) for call in api_calls if call['operation'] == 'agent-context']
                agents = json.loads(f.api.with_suffix('.agents').read_bytes())['agents']
                owner = next(agent for agent in agents.values() if agent['native_session_id'] == client.identity)
                self.assertTrue(contexts)
                self.assertEqual({call['session']['agent_id'] for call in contexts}, {owner['agent_id']})
                registrations = [json.loads(call['input']) for call in api_calls if call['operation'] == 'agent-register']
                self.assertTrue(all(call['native_session_id'] == client.identity for call in registrations))
                presence = [json.loads(call['input']) for call in api_calls if call['operation'] in ('agent-register', 'agent-context')]
                self.assertIn('busy', [call['metadata']['state'] for call in presence])
                self.assertEqual(contexts[-1]['metadata']['state'], 'idle')
                self.assertGreater(len(provider.requests), before[2])
                serialized = json.dumps(provider.requests[-1])
                self.assertIn('R04 fixture ' + label, serialized)
                if client.identity == target.identity:
                    self.assertIn('R04 fixture fresh-a', serialized)
            self.assertEqual(provider.failures, [])
            (output / (label + '.trace')).write_text(trace)
            observations.append(dict(case=label, native_session_id=client.identity, native_pid=client.child.native_pid,
                result=result, selection=selection[0]['selection'], reached=reached, refusal=refusal,
                native_hook_writes=produced,
                api_delta=len(f.api_calls())-before[1], provider_delta=len(provider.requests)-before[2],
                cairn_context_states=[call['metadata']['state'] for call in contexts],
                trace=pin(output / (label + '.trace'))))
            print(json.dumps(dict(progress=label, api_delta=len(f.api_calls())-before[1],
                                  provider_delta=len(provider.requests)-before[2])), flush=True)

        publish(a, work, 'a')
        publish(a, control_work, 'control-a')
        target = start('target-client', work)
        control = start('control-client', control_work)
        self.assertNotEqual(target.identity, control.identity)
        prompt(target, 'fresh-a', a)
        prompt(target, 'existing-a', a)
        prompt(control, 'control-a', a)
        f.publish_configuration(enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings))
        b = self.selected(f)
        (output / 'b-receipt.json').write_bytes(f.receipt_path.read_bytes())
        self.assertLessEqual(len(f.receipt_path.read_bytes()) + 1, string_limit)
        self.assertNotEqual(a[0]['selection'], b[0]['selection'])
        publish(b, work, 'b')
        prompt(target, 'existing-after-b-publication', b)
        prompt(control, 'control-still-a', a)
        fresh = start('fresh-client', work)
        prompt(fresh, 'fresh-b', b)
        previous = target
        previous.close()
        target = start('resumed-client', work, resume=previous.identity)
        self.assertEqual(target.identity, previous.identity)
        self.assertNotEqual(target.child.native_pid, previous.child.native_pid)
        transitions.append(dict(operation='stop/join, then --conversation', native_session_id=target.identity,
            previous_pid=previous.child.native_pid, native_pid=target.child.native_pid))
        prompt(target, 'resumed-b', b)
        receipt, saved = f.receipt_path, f.receipt_path.read_bytes()
        receipt.unlink()
        try:
            prompt(target, 'missing-receipt', b, refusal='missing')
        finally:
            receipt.write_bytes(saved)
            receipt.chmod(0o400)
        prompt(target, 'restored-after-missing', b)
        before_stat = receipt.stat()
        corrupt_receipt = saved + b'\n'
        overwrite_fixture(receipt, corrupt_receipt)
        after_stat = receipt.stat()
        self.assertTrue(stat.S_ISREG(after_stat.st_mode))
        self.assertEqual((before_stat.st_uid, before_stat.st_mode, before_stat.st_ino),
                         (after_stat.st_uid, after_stat.st_mode, after_stat.st_ino))
        self.assertEqual(after_stat.st_uid, os.getuid())
        self.assertEqual(after_stat.st_mode & 0o022, 0)
        self.assertLessEqual(after_stat.st_size, 1048576)
        (output / 'corrupt-receipt.json').write_bytes(corrupt_receipt)
        (output / 'receipt-stats.json').write_bytes(encoded(dict(before=tuple(before_stat), after=tuple(after_stat))))
        try:
            prompt(target, 'corrupt-receipt', b, refusal='corrupt')
        finally:
            overwrite_fixture(receipt, saved)
        prompt(target, 'restored-b', b)
        publish(a, work, 'reversed-a')
        prompt(target, 'reversed-a', a)
        prompt(control, 'control-after-reverse', a)
        self.assertEqual((a[1] / 'previous-settings.json').read_bytes(), original)
        client_reports = []
        for client in clients:
            client.close()
        for index, client in enumerate(clients):
            connections = loopback_connections(client.child.trace.read_text(), provider_port=provider.server.server_port)
            history = client.native_history_path
            self.assertTrue(history.is_file())
            if client.identity == target.identity:
                self.assertIn('R04 fixture fresh-a', history.read_text())
            (output / ('history-' + str(index) + '.jsonl')).write_bytes(history.read_bytes())
            client_reports.append(dict(argv=client.child.argv, native_pid=client.child.native_pid,
                native_exe=client.child.native_exe, before_sha256=client.child.before_hash,
                after_sha256=client.child.after_hash, joined_pids=client.child.pids, connections=connections,
                native_history_path=str(history), native_session_id=client.identity))
        calibration = TracedProcess([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'],
            self.root / 'forced-cleanup', env, output, work)
        with self.assertRaisesRegex(AssertionError, 'required forced shutdown'):
            calibration.close(timeout=0, close_stdin=False)
        self.assertTrue(calibration.forced)
        self.assertIn('killed by SIGTERM', exits(calibration.trace.read_text(), calibration.native_pid))
        credential_files = [str(path.relative_to(home)) for pattern in ('*token*', '*credential*', 'auth.json')
                            for path in home.rglob(pattern) if path.is_file() and 'builtin' not in path.parts]
        self.assertEqual(credential_files, [])
        return dict(schema='cairn.r04-native-agy/1', passed=True, cases=observations, clients=client_reports,
            fixture=pin(Path(__file__)), client=dict(path=str(binary), sha256=digest(binary),
                version=subprocess.check_output([binary, '--version'], env=env, text=True, timeout=10).strip()),
            selection_scope='project .agents/hooks.json; same account, independent workspace control',
            native_home=str(home), native_profile=str(native_profile), binding_account=str(f.account),
            trace_parser=pin(Path(__file__).with_name('r04_agy_trace.py')), nscd_path_exists=False,
            transitions=transitions, forced_cleanup_negative=dict(failed_as_required=True,
                native_pid=calibration.native_pid, joined_pids=calibration.pids), credential_files=credential_files,
            limits=['Agy ordinary memory is unsupported; this exercises checked Go coordination only.',
                    'Project-layer callback re-read and same-conversation restart, not account-layer or layered duplicates.',
                    'PreInvocation context injection is not qualified separately from registration and busy/idle transitions.',
                    'No non-fullyIdle Stop, Stop continue-once, child/subagent, host presence/host_pid association or Herdr wake claim.',
                    'No store/auth, code-image upgrade, complete activation-unit barrier or full R04/R05/R06/ticket-closure qualification.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agy', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--archive-stdout', action='store_true')
    args = parser.parse_args()
    if digest(args.agy) != args.sha256:
        parser.error('Selected native client hash mismatch')
    if os.environ.get('HOME') != '/tmp/home' or not Path('/tmp/work-tmp').is_dir():
        parser.error('Run inside scripts/test_hybrid_runtime_operations.py')
    with tempfile.TemporaryDirectory(prefix='r04agy-', dir='/tmp') as temporary:
        output = Path(temporary)
        test, report = NativeFixture(), None
        try:
            test.setUpClass()
            report = test.run_fixture(args.agy.resolve(), output)
        finally:
            cleanup_ok = test.doCleanups()
            if hasattr(test, 'build'):
                test.tearDownClass()
            if report is not None:
                report.update(cleanup_passed=cleanup_ok, passed=report['passed'] and cleanup_ok)
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
