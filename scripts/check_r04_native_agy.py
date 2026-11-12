"""Disposable native Agy hook layers, checked coordination and conversation resume."""
import argparse
import base64
from collections import Counter
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
from r04_agy_trace import calls, exec_lines, exits, file_read, hook_writes, io_bytes, joined_pids, loopback_connections, metadata_refusal, opened_paths, thread_pids, walked_path
from test_f04_activation import encoded, overwrite_fixture
from test_r02_compatibility_install import InstalledFixture, InstalledTools
from test_r04_host_wiring import R04HostWiringTests, pin


MODEL = 'Gemini 3.8 Flash (Low)'


def native_context_api_fixture(path):
    native_api_fixture(path)
    source = path.read_text()
    save_reply = " if request:db['replies'][request]=data\n"
    if source.count(save_reply) != 1:
        raise AssertionError('Synthetic API reply seam changed')
    seed_context = (" if op in ('agent-register','agent-context') and request not in db['replies']:\n"
                    "  data['display_name']=p.with_suffix('.context-marker').read_text()\n")
    path.chmod(0o700)
    path.write_text(source.replace(save_reply, seed_context + save_reply))
    path.chmod(0o500)


def provider_texts(request):
    return [part['text'] for content in request['body']['contents']
            for part in content['parts'] if 'text' in part]


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
    def __init__(self, argv, root, env, output, work, *, string_limit=8192, allow_completed=False):
        self.argv, self.binary = argv, Path(argv[0]).resolve()
        self.allow_completed = allow_completed
        self.before_hash = digest(self.binary)
        self.trace = output / (root.name + '.trace')
        self.stderr_path = output / (root.name + '.stderr')
        self.stderr = self.stderr_path.open('wb')
        self.closed, self.forced = False, False
        self.process = subprocess.Popen(
            ['strace', '-f', '-xx', '-s', str(string_limit), '-e',
             'trace=execve,openat,fstat,connect,read,write,close,clone,clone3,readlink,exit,exit_group',
             '-e', 'abbrev=!fstat', '-o', str(self.trace), *argv],
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
        # Short commands can finish before /proc is inspected. Only opt-in
        # callers may use completed exec/exit evidence; no live PID is inferred.
        if self.allow_completed and self.process.poll() is not None:
            trace = self.trace.read_text()
            native = [record for record in successful_execs(exec_lines(trace))
                      if record['path'] == str(self.binary) and record['argv'] == self.argv]
            if len(native) != 1:
                return False
            pid = int(native[0]['pid'])
            if pid <= 1 or exits(trace, pid) != ['exited with 0']:
                return False
            self.native_pid, self.native_exe = pid, str(self.binary)
            return True
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
        native_context_api_fixture(f.api)
        (output / 'cairn-api-fixture.py').write_bytes(f.api.read_bytes())
        home = f.root / 'native home'
        native_profile = home / '.gemini' / 'antigravity-cli'
        native_profile.mkdir(parents=True, mode=0o700)
        account_hook_home = home / '.gemini' / 'config'
        account_hook_home.mkdir(mode=0o700)
        account_hooks = account_hook_home / 'hooks.json'
        for parent in (home, home / '.gemini'):
            parent.chmod(0o700)
        f.coord.update(process_names=[binary.name], process_exe_dirs=[])
        # Native Agy separates global hook discovery from its session settings.
        for configuration in (f.memory, f.coord):
            configuration['config_home'] = str(account_hook_home)
        InstalledFixture.publish_configuration(f, enabled=False)
        f.settings = f.root / 'original-hooks.json'
        original = encoded({'fixture-unrelated': {'enabled': False}})
        f.settings.write_bytes(original)
        f.settings.chmod(0o600)
        f.hook_plan.update(account_home=str(account_hook_home), selection=f.selected, settings=pin(f.settings))
        a = self.selected(f)
        self.assertEqual(Path(a[2]['account_home']) / a[2]['layout']['hook-settings.json'], account_hooks)
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
        project_hooks, control_hooks = (directory / '.agents' / 'hooks.json' for directory in (work, control_work))
        observations, clients, transitions, first_prompts = [], [], [], {}
        self.addCleanup(lambda: (output / 'provider-requests.json').write_bytes(encoded(provider.requests)))
        self.addCleanup(lambda: (output / 'cairn-api-calls.json').write_bytes(encoded(f.api_calls())))
        self.addCleanup(lambda: (output / 'observations.json').write_bytes(encoded(observations)))
        self.addCleanup(lambda: (output / 'transitions.json').write_bytes(encoded(transitions)))

        def publish(selection, target, label):
            raw = Path(selection[0]['settings']['path']).read_bytes()
            staged = target.with_suffix('.staged')
            staged.write_bytes(raw)
            staged.chmod(0o600)
            os.replace(staged, target)
            self.assertEqual(digest(target), selection[0]['settings']['sha256'])
            (output / (label + '-hooks.json')).write_bytes(raw)
            (output / (label + '-selection.json')).write_bytes(encoded(selection[2]))

        def start(label, directory, hook_file, *, resume=None):
            client = AgySession(binary, self.root / label, env, settings, output, directory,
                                resume=resume, string_limit=string_limit)
            client.hook_file = hook_file
            clients.append(client)
            self.addCleanup(client.close)
            return client

        def state():
            base = Path(f.coord['state_dir'])
            return {str(path.relative_to(base)): path.read_bytes() for path in base.rglob('*') if path.is_file()}

        def prompt(client, label, selection, *, refusal=None, layers=None):
            active_layers = [(client.hook_file, selection)] if layers is None else layers
            self.assertTrue(active_layers)
            self.assertEqual(len({path for path, _ in active_layers}), len(active_layers))
            self.assertEqual(active_layers[0][1], selection)
            if refusal:
                self.assertEqual(len(active_layers), 1)
            rows = [row for _, selected in active_layers for row in selected[2]['rows']]
            command_counts = Counter(tuple(row['argv']) for row in rows)
            unique_rows = {tuple(row['argv']): row for row in rows}
            for row in rows:
                self.assertEqual(row, unique_rows[tuple(row['argv'])])
            expected_events = [event for event in ('PreInvocation', 'Stop') for _ in active_layers]
            before = state(), len(f.api_calls()), len(provider.requests)
            marker = 'R04-CONTEXT-' + str(uuid.uuid4())
            self.assertTrue(all(marker not in text for request in provider.requests for text in provider_texts(request)))
            f.api.with_suffix('.context-marker').write_text(marker)
            offset = client.child.trace.stat().st_size
            prompt_text = 'R04 fixture ' + label
            first_prompts.setdefault(client.identity, prompt_text)
            result = client.prompt(prompt_text)

            def finished():
                captured = client.child.trace.read_bytes()
                line_end = captured.rfind(b'\n') + 1
                if line_end < offset:
                    return None
                captured = captured[:line_end]
                all_trace = captured.decode()
                trace = captured[offset:].decode()
                try:
                    executions = list(successful_execs(exec_lines(all_trace, start_offset=offset)))
                    found = []
                    for key, row in unique_rows.items():
                        issued = [entry for entry in executions if entry['path'] == row['argv'][0] and entry['argv'] == row['argv']]
                        if len(issued) < command_counts[key] or any(not exits(trace, entry['pid']) for entry in issued):
                            return None
                        self.assertEqual(len(issued), command_counts[key], (label, row, issued))
                        found.extend((row, entry) for entry in issued)
                    native_threads = thread_pids(all_trace, client.child.native_pid)
                    produced = hook_writes(all_trace, native_threads, start_offset=offset)
                    if len(produced) < len(expected_events):
                        # A completed child alone does not prove that strace
                        # has finished reporting the native producer's write.
                        return None
                    self.assertEqual([record['payload']['hookEventName'] for record in produced], expected_events)
                except AssertionError:
                    (output / (label + '.trace')).write_bytes(captured[offset:])
                    (output / (label + '.prefix.trace')).write_bytes(captured)
                    raise
                return trace, all_trace, executions, found, native_threads, produced

            (trace, all_trace, executions, found, native_threads,
             produced) = wait_for(finished, 'synchronous native callback exits and producer writes')
            # Retain the exact analysis window even if a later assertion fails.
            (output / (label + '.trace')).write_text(trace)
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
                threads = thread_pids(all_trace, issued['pid'])
                raw = io_bytes(trace, threads, 0, 'read')
                stdout = io_bytes(trace, threads, 1, 'write')
                stderr = io_bytes(trace, threads, 2, 'write')
                payload = json.loads(raw) if raw else None
                receipt_read = None
                # The checked launcher carries the receipt path/hash in its
                # literal program vector, before the hook tail.
                receipt_path = vector[vector.index('--receipt') + 1]
                receipt_hash = vector[vector.index('--receipt-sha256') + 1]
                if refusal == 'mode':
                    metadata_evidence = metadata_refusal(trace, threads, 'receipt.json', mode_fault_metadata)
                    self.assertEqual(metadata_evidence['path'], receipt_path)
                else:
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
                    elif refusal == 'symlink':
                        self.assertEqual(observed, dict(open_error='ELOOP (Too many levels of symbolic links)', raw=b''))
                        self.assertNotIn(str(symlink_target), opened_paths(trace, threads))
                        self.assertNotIn(symlink_target.name, opened_paths(trace, threads))
                    elif refusal == 'corrupt':
                        self.assertIsNone(observed['open_error'])
                        self.assertEqual(observed['raw'], corrupt_receipt)
                        self.assertNotEqual(hashlib.sha256(observed['raw']).hexdigest(), receipt_hash)
                    else:
                        self.assertEqual(refusal, 'mode')
                    if refusal == 'mode':
                        receipt_read = dict(open_error=None, bytes=0, metadata_refusal=metadata_evidence)
                    else:
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
            launch_root = str(f.root / 'release' / 'launch') + '/'
            self.assertEqual(Counter(tuple(entry['argv']) for entry in executions if entry['path'].startswith(launch_root)),
                             command_counts)
            if not refusal:
                self.assertEqual(Counter(encoded(record['payload']) for record in reached),
                                 Counter(encoded(record['payload']) for record in produced))
            for hook_file, selected in active_layers:
                self.assertIn(str(hook_file), opened_paths(all_trace, native_threads))
                self.assertEqual(digest(hook_file), selected[0]['settings']['sha256'])
            if len(active_layers) == 1 and active_layers[0][0] == account_hooks:
                self.assertFalse(project_hooks.exists())
                self.assertFalse(control_hooks.exists())
            current_requests = provider.requests[before[2]:]
            context_application = dict(marker=marker, provider_request_indices=[])
            if refusal:
                self.assertEqual(state(), before[0])
                self.assertEqual(len(f.api_calls()), before[1])
                self.assertTrue(all(marker not in text for request in current_requests for text in provider_texts(request)))
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
                preinvocations = [record for record in reached if record['event'] == 'PreInvocation']
                self.assertEqual(len(preinvocations), len(active_layers))
                messages = []
                for preinvocation in preinvocations:
                    injected = json.loads(preinvocation['stdout'])
                    self.assertEqual(set(injected), {'injectSteps'})
                    self.assertEqual(len(injected['injectSteps']), 1)
                    self.assertEqual(set(injected['injectSteps'][0]), {'ephemeralMessage'})
                    message = injected['injectSteps'][0]['ephemeralMessage']
                    self.assertIn('Cairn session ' + marker + ' has inbox ' + owner['inbox'], message)
                    self.assertIn('Use agent UUID ' + owner['agent_id'] + ' and execution UUID ' + owner['execution_id'], message)
                    messages.append(message)
                for request in current_requests:
                    provider_counts = Counter(text.strip() for text in provider_texts(request))
                    for message, count in Counter(message.strip() for message in messages).items():
                        self.assertEqual(provider_counts[message], count)
                context_application.update(message_sha256=hashlib.sha256(messages[0].encode()).hexdigest(),
                    hook_stdout_sha256=hashlib.sha256(preinvocations[0]['stdout'].encode()).hexdigest(),
                    messages=[dict(message_sha256=hashlib.sha256(message.encode()).hexdigest(),
                                   hook_stdout_sha256=hashlib.sha256(record['stdout'].encode()).hexdigest())
                              for record, message in zip(preinvocations, messages)],
                    provider_request_indices=list(range(before[2], len(provider.requests))))
                serialized = json.dumps(provider.requests[-1])
                self.assertIn('R04 fixture ' + label, serialized)
                self.assertIn(first_prompts[client.identity], serialized)
                if client.identity == target.identity:
                    self.assertIn('R04 fixture fresh-a', serialized)
            self.assertEqual(provider.failures, [])
            observations.append(dict(case=label, native_session_id=client.identity, native_pid=client.child.native_pid,
                result=result, selection=selection[0]['selection'], reached=reached, refusal=refusal,
                context_application=context_application,
                hook_layer='layered' if len(active_layers) > 1 else ('account' if active_layers[0][0] == account_hooks else 'project'),
                hook_file=pin(active_layers[0][0]),
                hook_layers=[dict(layer='account' if path == account_hooks else 'project', hook_file=pin(path),
                                  selection=selected[0]['selection']) for path, selected in active_layers],
                expected_commands=[dict(argv=list(argv), count=count) for argv, count in command_counts.items()],
                native_hook_writes=produced,
                api_delta=len(f.api_calls())-before[1], provider_delta=len(provider.requests)-before[2],
                cairn_context_states=[call['metadata']['state'] for call in contexts],
                trace=pin(output / (label + '.trace'))))
            print(json.dumps(dict(progress=label, api_delta=len(f.api_calls())-before[1],
                                  provider_delta=len(provider.requests)-before[2])), flush=True)

        publish(a, project_hooks, 'a')
        publish(a, control_hooks, 'control-a')
        target = start('target-client', work, project_hooks)
        control = start('control-client', control_work, control_hooks)
        self.assertNotEqual(target.identity, control.identity)
        prompt(target, 'fresh-a', a)
        prompt(target, 'existing-a', a)
        prompt(control, 'control-a', a)
        InstalledFixture.publish_configuration(f, enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings))
        b = self.selected(f)
        (output / 'b-receipt.json').write_bytes(f.receipt_path.read_bytes())
        self.assertLessEqual(len(f.receipt_path.read_bytes()) + 1, string_limit)
        self.assertNotEqual(a[0]['selection'], b[0]['selection'])
        for selected in (a, b):
            groups = json.loads(Path(selected[0]['settings']['path']).read_bytes())
            self.assertEqual(set(groups['cairn-coordination']), {'PreInvocation', 'Stop'})
        publish(b, project_hooks, 'b')
        prompt(target, 'existing-after-b-publication', b)
        prompt(control, 'control-still-a', a)
        fresh = start('fresh-client', work, project_hooks)
        prompt(fresh, 'fresh-b', b)
        previous = target
        previous.close()
        target = start('resumed-client', work, project_hooks, resume=previous.identity)
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
        publish(a, project_hooks, 'reversed-a')
        prompt(target, 'reversed-a', a)
        prompt(control, 'control-after-reverse', a)
        self.assertEqual((a[1] / 'previous-settings.json').read_bytes(), original)
        client_reports = []
        for client in clients:
            client.close()
        project_hooks.unlink()
        control_hooks.unlink()
        self.assertFalse(account_hooks.exists())
        prior_ids = {client.identity for client in clients}
        publish(a, account_hooks, 'account-a')
        account = start('account-client', work, account_hooks)
        self.assertNotIn(account.identity, prior_ids)
        prompt(account, 'account-fresh-a', a)
        publish(b, account_hooks, 'account-b')
        prompt(account, 'account-existing-after-b-publication', b)
        previous = account
        previous.close()
        account = start('account-resumed-client', work, account_hooks, resume=previous.identity)
        self.assertEqual(account.identity, previous.identity)
        self.assertNotEqual(account.child.native_pid, previous.child.native_pid)
        transitions.append(dict(operation='account stop/join, then --conversation', native_session_id=account.identity,
            previous_pid=previous.child.native_pid, native_pid=account.child.native_pid))
        prompt(account, 'account-resumed-b', b)
        publish(a, account_hooks, 'account-reversed-a')
        prompt(account, 'account-reversed-a', a)
        account.close()
        prior_ids = {client.identity for client in clients}
        publish(b, project_hooks, 'layers-project-b')
        layered = start('layered-client', work, project_hooks)
        self.assertNotIn(layered.identity, prior_ids)
        prompt(layered, 'layers-account-a-project-b', b, layers=[(project_hooks, b), (account_hooks, a)])
        publish(a, project_hooks, 'layers-project-a')
        publish(b, account_hooks, 'layers-account-b')
        prompt(layered, 'layers-account-b-project-a', a, layers=[(project_hooks, a), (account_hooks, b)])
        publish(a, account_hooks, 'layers-identical-a')
        self.assertEqual(project_hooks.read_bytes(), account_hooks.read_bytes())
        prompt(layered, 'layers-identical-a', a, layers=[(project_hooks, a), (account_hooks, a)])
        project_hooks.unlink()
        prompt(layered, 'layers-project-removed-account-a', a, layers=[(account_hooks, a)])
        publish(b, project_hooks, 'layers-project-restored-b')
        account_hooks.unlink()
        self.assertFalse(account_hooks.exists())
        prompt(layered, 'layers-account-removed-project-b', b, layers=[(project_hooks, b)])
        layered.close()
        prefix_names = [
            'fresh-a', 'existing-a', 'control-a', 'existing-after-b-publication',
            'control-still-a', 'fresh-b', 'resumed-b', 'missing-receipt',
            'restored-after-missing', 'corrupt-receipt', 'restored-b', 'reversed-a',
            'control-after-reverse', 'account-fresh-a',
            'account-existing-after-b-publication', 'account-resumed-b',
            'account-reversed-a', 'layers-account-a-project-b',
            'layers-account-b-project-a', 'layers-identical-a',
            'layers-project-removed-account-a', 'layers-account-removed-project-b']
        prefix_layers = ([('project',)] * 13 + [('account',)] * 4 +
                         [('project', 'account')] * 3 + [('account',), ('project',)])
        prefix_refusals = {'missing-receipt': 'missing', 'corrupt-receipt': 'corrupt'}
        self.assertEqual([record['case'] for record in observations], prefix_names)
        for record, layers in zip(observations, prefix_layers):
            self.assertEqual(record['refusal'], prefix_refusals.get(record['case']))
            self.assertEqual(record['hook_layer'], 'layered' if len(layers) > 1 else layers[0])
            self.assertEqual(tuple(layer['layer'] for layer in record['hook_layers']), layers)
        preserved_prefix = dict(source_commit='0378faebac2bf45f588e9301f36e81c981eb51ff',
            cases=[dict(case=record['case'], refusal=record['refusal'], hook_layer=record['hook_layer'],
                        layers=[layer['layer'] for layer in record['hook_layers']]) for record in observations])
        for client in clients:
            client.close()
        project_hooks.unlink()
        for hook_file in (project_hooks, control_hooks, account_hooks):
            self.assertFalse(hook_file.exists())
        prior_ids = {client.identity for client in clients}
        publish(b, account_hooks, 'account-refusal-b')
        receipt, saved = f.receipt_path, f.receipt_path.read_bytes()
        vector = shlex.split(Path(b[2]['rows'][0]['argv'][0]).read_text().split('\n', 1)[1])[1:]
        self.assertEqual(str(receipt), vector[vector.index('--receipt') + 1])
        self.assertEqual(hashlib.sha256(saved).hexdigest(), vector[vector.index('--receipt-sha256') + 1])
        account_fault_offset = len(observations)
        faults = start('account-refusal-client', work, account_hooks)
        self.assertNotIn(faults.identity, prior_ids)
        prompt(faults, 'account-refusal-valid-b', b)
        receipt.unlink()
        try:
            prompt(faults, 'account-missing-receipt', b, refusal='missing')
        finally:
            receipt.write_bytes(saved)
            receipt.chmod(0o400)
        prompt(faults, 'account-restored-after-missing', b)
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
        (output / 'account-corrupt-receipt.json').write_bytes(corrupt_receipt)
        (output / 'account-receipt-stats.json').write_bytes(encoded(dict(before=tuple(before_stat), after=tuple(after_stat))))
        try:
            prompt(faults, 'account-corrupt-receipt', b, refusal='corrupt')
        finally:
            overwrite_fixture(receipt, saved)
        prompt(faults, 'account-restored-after-corrupt', b)
        account_faults = observations[account_fault_offset:]
        self.assertEqual(len(account_faults), 5)
        self.assertEqual({record['native_session_id'] for record in account_faults}, {faults.identity})
        self.assertEqual({record['native_pid'] for record in account_faults}, {faults.child.native_pid})
        self.assertEqual([record['hook_layer'] for record in account_faults], ['account'] * 5)
        faults.close()
        receipt_prefix_names = prefix_names + [
            'account-refusal-valid-b', 'account-missing-receipt',
            'account-restored-after-missing', 'account-corrupt-receipt',
            'account-restored-after-corrupt']
        receipt_prefix_refusals = dict(prefix_refusals, **{
            'account-missing-receipt': 'missing', 'account-corrupt-receipt': 'corrupt'})
        receipt_prefix_layers = prefix_layers + [('account',)] * 5
        self.assertEqual([record['case'] for record in observations], receipt_prefix_names)
        for record, layers in zip(observations, receipt_prefix_layers):
            self.assertEqual(record['refusal'], receipt_prefix_refusals.get(record['case']))
            self.assertEqual(record['hook_layer'], 'layered' if len(layers) > 1 else layers[0])
            self.assertEqual(tuple(layer['layer'] for layer in record['hook_layers']), layers)
        preserved_receipt_prefix = dict(source_commit='7ef43cb2f509061bb942d5ae3938c29b94327fa3',
            cases=[dict(case=record['case'], refusal=record['refusal'], hook_layer=record['hook_layer'],
                        layers=[layer['layer'] for layer in record['hook_layers']]) for record in observations])
        prior_ids = {client.identity for client in clients}
        symlink_offset = len(observations)
        symlink_client = start('account-symlink-client', work, account_hooks)
        self.assertNotIn(symlink_client.identity, prior_ids)
        prompt(symlink_client, 'account-symlink-valid-b', b)
        self.assertEqual(receipt.read_bytes(), saved)
        self.assertEqual(hashlib.sha256(saved).hexdigest(), vector[vector.index('--receipt-sha256') + 1])
        before_stat = receipt.stat()
        symlink_target = receipt.with_name('symlink-target.json')
        self.assertFalse(symlink_target.exists())
        symlink_target.write_bytes(saved)
        symlink_target.chmod(0o400)
        target_stat = symlink_target.stat()
        self.assertTrue(stat.S_ISREG(target_stat.st_mode))
        self.assertEqual(target_stat.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(target_stat.st_mode), 0o400)
        self.assertEqual(digest(symlink_target), vector[vector.index('--receipt-sha256') + 1])
        (output / 'account-symlink-target.json').write_bytes(symlink_target.read_bytes())
        receipt.unlink()
        try:
            receipt.symlink_to(symlink_target)
            link_stat = receipt.lstat()
            self.assertTrue(stat.S_ISLNK(link_stat.st_mode))
            self.assertEqual(link_stat.st_uid, os.getuid())
            self.assertEqual(os.readlink(receipt), str(symlink_target))
            (output / 'account-symlink-stats.json').write_bytes(encoded(dict(
                receipt_path=str(receipt), before=tuple(before_stat), link=tuple(link_stat),
                link_target=os.readlink(receipt), target_stat=tuple(target_stat), target=pin(symlink_target))))
            prompt(symlink_client, 'account-symlink-receipt', b, refusal='symlink')
        finally:
            receipt.unlink(missing_ok=True)
            receipt.write_bytes(saved)
            receipt.chmod(0o400)
        self.assertTrue(stat.S_ISREG(receipt.lstat().st_mode))
        self.assertEqual(digest(receipt), vector[vector.index('--receipt-sha256') + 1])
        prompt(symlink_client, 'account-restored-after-symlink', b)
        symlink_cases = observations[symlink_offset:]
        self.assertEqual(len(symlink_cases), 3)
        self.assertEqual({record['native_session_id'] for record in symlink_cases}, {symlink_client.identity})
        self.assertEqual({record['native_pid'] for record in symlink_cases}, {symlink_client.child.native_pid})
        self.assertEqual([record['hook_layer'] for record in symlink_cases], ['account'] * 3)
        symlink_client.close()
        symlink_prefix_names = receipt_prefix_names + [
            'account-symlink-valid-b', 'account-symlink-receipt', 'account-restored-after-symlink']
        symlink_prefix_refusals = dict(receipt_prefix_refusals, **{'account-symlink-receipt': 'symlink'})
        symlink_prefix_layers = receipt_prefix_layers + [('account',)] * 3
        self.assertEqual([record['case'] for record in observations], symlink_prefix_names)
        for record, layers in zip(observations, symlink_prefix_layers):
            self.assertEqual(record['refusal'], symlink_prefix_refusals.get(record['case']))
            self.assertEqual(record['hook_layer'], 'layered' if len(layers) > 1 else layers[0])
            self.assertEqual(tuple(layer['layer'] for layer in record['hook_layers']), layers)
        preserved_symlink_prefix = dict(source_commit='0d196e69664c0bcb172a82b603923fd9fcd765ee',
            cases=[dict(case=record['case'], refusal=record['refusal'], hook_layer=record['hook_layer'],
                        layers=[layer['layer'] for layer in record['hook_layers']]) for record in observations])
        prior_ids = {client.identity for client in clients}
        mode_offset = len(observations)
        mode_client = start('account-mode-client', work, account_hooks)
        self.assertNotIn(mode_client.identity, prior_ids)
        prompt(mode_client, 'account-mode-valid-b', b)
        before_stat = receipt.stat()
        self.assertTrue(stat.S_ISREG(before_stat.st_mode))
        self.assertEqual(stat.S_IMODE(before_stat.st_mode), 0o400)
        self.assertEqual(before_stat.st_uid, os.getuid())
        self.assertEqual(str(receipt), vector[vector.index('--receipt') + 1])

        def mode_snapshot(phase):
            snapshot, raw = receipt.stat(), receipt.read_bytes()
            self.assertTrue(stat.S_ISREG(snapshot.st_mode))
            self.assertEqual((snapshot.st_dev, snapshot.st_uid, snapshot.st_ino, snapshot.st_size),
                             (before_stat.st_dev, before_stat.st_uid, before_stat.st_ino, before_stat.st_size))
            self.assertEqual(raw, saved)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), vector[vector.index('--receipt-sha256') + 1])
            metadata = dict(mode=stat.S_IMODE(snapshot.st_mode), uid=snapshot.st_uid,
                            inode=snapshot.st_ino, size=snapshot.st_size)
            (output / ('account-mode-' + phase + '-receipt.json')).write_bytes(raw)
            (output / ('account-mode-' + phase + '-stat.json')).write_bytes(encoded(dict(
                path=str(receipt), stat=tuple(snapshot), metadata=metadata, sha256=hashlib.sha256(raw).hexdigest())))
            return metadata

        mode_before = mode_snapshot('before')
        try:
            receipt.chmod(0o420)
            mode_fault_metadata = mode_snapshot('fault')
            self.assertEqual(mode_fault_metadata, dict(mode_before, mode=0o420))
            prompt(mode_client, 'account-group-writable-receipt', b, refusal='mode')
        finally:
            receipt.chmod(0o400)
        self.assertEqual(mode_snapshot('restored'), mode_before)
        prompt(mode_client, 'account-restored-after-mode', b)
        mode_cases = observations[mode_offset:]
        self.assertEqual(len(mode_cases), 3)
        self.assertEqual({record['native_session_id'] for record in mode_cases}, {mode_client.identity})
        self.assertEqual({record['native_pid'] for record in mode_cases}, {mode_client.child.native_pid})
        self.assertEqual([record['hook_layer'] for record in mode_cases], ['account'] * 3)
        self.assertEqual(len(observations), 33)
        mode_client.close()
        for index, client in enumerate(clients):
            connections = loopback_connections(client.child.trace.read_text(), provider_port=provider.server.server_port)
            history = client.native_history_path
            self.assertTrue(history.is_file())
            if client.identity == target.identity:
                self.assertIn('R04 fixture fresh-a', history.read_text())
            self.assertIn(first_prompts[client.identity], history.read_text())
            (output / ('history-' + str(index) + '.jsonl')).write_bytes(history.read_bytes())
            client_reports.append(dict(argv=client.child.argv, native_pid=client.child.native_pid,
                native_exe=client.child.native_exe, before_sha256=client.child.before_hash,
                after_sha256=client.child.after_hash, joined_pids=client.child.pids, connections=connections,
                native_history_path=str(history), native_session_id=client.identity))
        version_before = len(f.api_calls()), len(provider.requests)
        version = TracedProcess([str(binary), '--version'], self.root / 'version-client', env, output, work,
                                string_limit=string_limit, allow_completed=True)
        self.addCleanup(version.close)
        try:
            version_raw, _ = version.process.communicate(timeout=10)
        finally:
            version.close(timeout=0)
        version_trace = version.trace.read_text()
        version_threads = thread_pids(version_trace, version.native_pid)
        self.assertEqual(io_bytes(version_trace, version_threads, 1, 'write'), version_raw)
        self.assertEqual(io_bytes(version_trace, version_threads, 2, 'write'), b'')
        self.assertTrue(version_raw.strip())
        self.assertEqual((len(f.api_calls()), len(provider.requests)), version_before)
        (output / 'version.stdout').write_bytes(version_raw)
        version_report = dict(argv=version.argv, native_pid=version.native_pid, native_exe=version.native_exe,
            before_sha256=version.before_hash, after_sha256=version.after_hash, joined_pids=version.pids,
            connections=loopback_connections(version_trace, provider_port=provider.server.server_port),
            stdout_sha256=hashlib.sha256(version_raw).hexdigest(), trace=pin(version.trace))
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
                version=version_raw.decode().strip()), version_process=version_report,
            selection_scope='13 project-layer, 4 account-only, 5 layered/removal, 5 account-receipt, 3 account-symlink, then 3 account-mode cases (33 total)',
            preserved_prefix=preserved_prefix,
            preserved_receipt_prefix=preserved_receipt_prefix,
            preserved_symlink_prefix=preserved_symlink_prefix,
            native_home=str(home), native_profile=str(native_profile), binding_account=str(f.account),
            account_hook_home=str(account_hook_home), account_hook_file=str(account_hooks),
            context_seed='Per-prompt synthetic API display_name; marker is absent from user prompts and hook configuration.',
            trace_parser=pin(Path(__file__).with_name('r04_agy_trace.py')), nscd_path_exists=False,
            transitions=transitions, forced_cleanup_negative=dict(failed_as_required=True,
                native_pid=calibration.native_pid, joined_pids=calibration.pids), credential_files=credential_files,
            limits=['Agy ordinary memory is unsupported; this exercises checked Go coordination only.',
                    'Selected Agy aggregates account/project groups, including identical commands; no inter-layer order, precedence, cross-account isolation or installer claim.',
                    'Selected B receipt controls cover project/account missing/corrupt files, an account leaf symlink and an owned regular receipt with group-write mode; no broader receipt-fault qualification.',
                    'PreInvocation context application reaches local provider text; no real model comprehension or inbox delivery claim.',
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
