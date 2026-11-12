"""Disposable native Codex hook trust, cached selection and same-thread refresh.

The explicitly selected app-server uses only a local scripted Responses server
and a synthetic Cairn API. Archive/unarchive/resume retains the native thread;
this does not qualify live trust, MCP reconnection or a whole activation unit.
"""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
import unittest
import uuid

from check_r04_native_opencode import digest, native_api_fixture, successful_execs, wait_for
from codex_app_server_session import CodexAppServerSession, StdioWire
from codex_loopback_fixture import LoopbackResponsesServer, message_item, write_loopback_config_toml
from test_f04_activation import encoded, overwrite_fixture
from test_r02_compatibility_install import InstalledTools
from test_r04_host_wiring import R04HostWiringTests, pin


MODEL = 'r04-local-fixture'


class CodexSession:
    def __init__(self, binary, root, account, provider, output, work):
        root.mkdir(mode=0o700)
        home = root / 'home'
        home.mkdir(mode=0o700)
        self.trace = output / 'native.exec'
        self.diagnostics = output / 'native.stderr'
        self.stderr = self.diagnostics.open('w')
        self.session = None
        self.closed = False
        self.env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME=str(home), CODEX_HOME=str(account),
                        CAIRN_DATABASE_URL='host=/absent-r04-native-db dbname=denied')
        self.config = write_loopback_config_toml(account, provider.port, model=MODEL,
                                                 check_for_update_on_startup=False)
        with self.config.open('a') as stream:
            stream.write('[hooks]\nenabled = true\n')
        self.process = subprocess.Popen(
            ['strace', '-f', '-s', '16384', '-e', 'trace=execve', '-o', str(self.trace),
             str(binary), 'app-server', '--stdio'], cwd=work, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, bufsize=1, start_new_session=True)
        try:
            # The shared process owner uses process-group signalling. This
            # fixture owns and joins only its explicit child PID instead.
            self.session = CodexAppServerSession.start(StdioWire(self.process),
                client_name='cairn-r04-native-codex', default_timeout=30)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        forced = False
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=30)
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
            if self.session is not None:
                self.session.close()
            self.process.stdout.close()
            self.stderr.close()
        if forced or self.process.returncode != 0:
            raise AssertionError('Native Codex did not stop cleanly: ' + str(self.process.returncode))


class NativeFixture(InstalledTools, unittest.TestCase):
    fixture = R04HostWiringTests.fixture
    selected = R04HostWiringTests.selected
    stage = R04HostWiringTests.stage

    def run_fixture(self, binary, output):
        self.root = output / 'owned'
        self.root.mkdir(mode=0o700)
        f = self.fixture('installation', harness='codex')
        native_api_fixture(f.api)
        selector = f.root / 'null-selector'
        selector.write_text('#!/usr/bin/python3\nimport json,sys\njson.load(sys.stdin)\n'
                            'print(json.dumps({"structured_output":{"checkpoint":None,"workstream":None,"memories":[]}}))\n')
        selector.chmod(0o500)
        f.memory['claude'] = str(selector)
        f.coord.update(process_names=[binary.name], process_exe_dirs=[])
        f.publish_configuration(enabled=False)
        original = encoded(dict(description='Preserve this fixture description.'))
        overwrite_fixture(f.settings, original)
        f.hook_plan.update(selection=f.selected, settings=pin(f.settings))
        a = self.selected(f)
        (output / 'selection-a.json').write_bytes(encoded(a[2]))
        (output / 'settings-a.json').write_bytes(Path(a[0]['settings']['path']).read_bytes())
        provider = LoopbackResponsesServer(
            lambda request, outputs: message_item('r04_' + str(len(provider.requests)),
                                                   'R04 local fixture reply.'),
            model=MODEL, max_requests=20, enable_models_endpoint=True).start()
        self.addCleanup(provider.close)
        work = self.root / 'work'
        work.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '--quiet', str(work)], check=True, capture_output=True, timeout=5)
        observations, publications, refreshes = [], [], []
        self.addCleanup(lambda: (output / 'provider-requests.json').write_bytes(encoded(provider.requests)))
        self.addCleanup(lambda: (output / 'cairn-api-calls.json').write_bytes(encoded(f.api_calls())))
        self.addCleanup(lambda: (output / 'observations.json').write_bytes(encoded(observations)))
        self.addCleanup(lambda: (output / 'publications.json').write_bytes(encoded(publications)))
        self.addCleanup(lambda: (output / 'refreshes.json').write_bytes(encoded(refreshes)))
        active_hooks = f.account / 'hooks.json'
        active_hooks.write_bytes(Path(a[0]['settings']['path']).read_bytes())
        active_hooks.chmod(0o600)
        client = CodexSession(binary, self.root / 'client', f.account, provider, output, work)
        self.addCleanup(client.close)
        session = client.session

        def readback(selection, *, trusted):
            self.assertEqual(active_hooks.read_bytes(), Path(selection[0]['settings']['path']).read_bytes())
            listing = session.call('hooks/list', dict(cwds=[str(work)]))
            (output / 'hooks-list-last.json').write_bytes(encoded(listing))
            for entry in listing['data']:
                self.assertEqual(entry['warnings'], [], listing)
                self.assertEqual(entry['errors'], [], listing)
            rows = [hook for entry in listing['data']
                    for hook in entry['hooks']]
            expected = {(row['event'][0].lower() + row['event'][1:], row['hook']['command'])
                        for row in selection[2]['rows']}
            self.assertEqual({(row['eventName'], row['command']) for row in rows}, expected)
            self.assertEqual(len(rows), len(selection[2]['rows']))
            for row in rows:
                self.assertEqual(row['sourcePath'], str(active_hooks))
                self.assertTrue(row['enabled'])
                if trusted:
                    self.assertEqual(row['trustStatus'], 'trusted', row)
            return rows

        def publish(selection):
            active_hooks.write_bytes(Path(selection[0]['settings']['path']).read_bytes())
            before = readback(selection, trusted=False)
            for hook in before:
                session.call('config/value/write', dict(filePath=str(client.config),
                    keyPath='hooks.state.' + json.dumps(hook['key']) + '.trusted_hash',
                    value=hook['currentHash'], mergeStrategy='replace'))
            after = readback(selection, trusted=True)
            publications.append(dict(selection=selection[0]['selection'], before=before, trusted=after))

        def state():
            return {role: {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()}
                    for role, cfg in (('memory', f.memory), ('coordination', f.coord))
                    if (base := Path(cfg['state_dir'])).exists()}

        def quiet():
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
                stamp = len(raw), len(f.api_calls())
                if active or stamp != stable[0]:
                    stable[:] = [stamp, time.monotonic()]
                    return False
                return time.monotonic() - stable[1] >= .3

            wait_for(settled, 'native hook descendants settled')

        def reached(trace, selection, *, refused=False, events=('UserPromptSubmit', 'Stop')):
            executions = list(successful_execs(trace))
            selected_launchers = {row['argv'][0] for row in selection[2]['rows']}
            launch_root = str(f.root / 'release' / 'launch') + '/'
            self.assertEqual([e for e in executions if e['path'].startswith(launch_root)
                              and e['path'] not in selected_launchers], [])
            evidence = []
            groups = {}
            for row in selection[2]['rows']:
                if row['event'] in events:
                    groups.setdefault(tuple(row['argv']), []).append(row)
            for rows in groups.values():
                row, role = rows[0], rows[0]['role']
                launcher = row['argv'][0]
                issued = [e for e in executions if e['path'] == launcher and e['argv'] == row['argv']]
                self.assertGreaterEqual(len(issued), len(rows), (role, events, trace[-5000:]))
                vector = shlex.split(Path(launcher).read_text().split('\n', 1)[1])[1:]
                self.assertEqual(vector[-1], '$@')
                actual = [e for e in executions if e['pid'] in {i['pid'] for i in issued} and
                          e['path'] == vector[0] and e['argv'] == vector[:-1] + row['argv'][1:]]
                self.assertEqual({e['pid'] for e in actual}, {e['pid'] for e in issued},
                                 (role, trace[-5000:]))
                self.assertEqual(len(actual), len(issued))
                exits = {e['pid']: re.findall(r'^' + e['pid'] + r'\s+\+\+\+ exited with (\d+) \+\+\+$',
                                             trace, flags=re.MULTILINE) for e in actual}
                self.assertEqual(exits, {e['pid']: ['1' if refused else '0'] for e in actual})
                evidence.append(dict(role=role, events=[r['event'] for r in rows],
                                     implementation=row['implementation'], argv=row['argv'],
                                     launcher=pin(Path(launcher)), program=pin(Path(vector[0])),
                                     execs=actual, exits=exits))
            return evidence

        def prompt(thread, label, selection, *, published=None, refused=False, starting=False):
            quiet()
            offset = client.trace.stat().st_size
            before = state(), len(f.api_calls()), len(provider.requests)
            turn = thread.turn('R04 fixture ' + label)
            result = turn.wait_completed(45, stop_on_failure=True)
            self.assertTrue(result.completed and result.status == 'completed', result.summary(items=True))
            quiet()
            trace = client.trace.read_text()[offset:]
            events = ('SessionStart', 'UserPromptSubmit', 'Stop') if starting else ('UserPromptSubmit', 'Stop')
            evidence = reached(trace, selection, refused=refused, events=events)
            if refused:
                self.assertEqual(state(), before[0])
                self.assertEqual(len(f.api_calls()), before[1])
                context_states = []
            else:
                self.assertGreater(len(f.api_calls()), before[1])
                calls = f.api_calls()[before[1]:]
                agents = json.loads(f.api.with_suffix('.agents').read_bytes())['agents']
                owner = next(agent for agent in agents.values() if agent['native_session_id'] == thread.id)
                contexts = [json.loads(call['input']) for call in calls if call['operation'] == 'agent-context']
                self.assertTrue(contexts)
                self.assertEqual({call['session']['agent_id'] for call in contexts}, {owner['agent_id']})
                registrations = [json.loads(call['input']) for call in calls if call['operation'] == 'agent-register']
                self.assertTrue(all(call['native_session_id'] == thread.id for call in registrations))
                context_states = [call['metadata']['state'] for call in contexts]
                self.assertIn('busy', context_states)
                self.assertEqual(context_states[-1], 'idle')
            self.assertGreater(len(provider.requests), before[2])
            self.assertEqual(provider.failures, [])
            request = json.dumps(provider.requests[-1])
            self.assertIn('R04 fixture ' + label, request)
            if thread is target and label != 'fresh-a':
                self.assertIn('R04 fixture fresh-a', request)
            view = readback(published or selection, trusted=True)
            (output / (label + '.exec')).write_text(trace)
            observations.append(dict(case=label, session_id=thread.id, turn_id=turn.id,
                selection=selection[0]['selection'], reached=evidence, native_readback=view,
                cairn_native_session_id=thread.id, cairn_context_states=context_states,
                model_status=result.status, hook_notifications=result.summary(items=True),
                refused_before_cairn_effects=refused, api_delta=len(f.api_calls())-before[1],
                provider_delta=len(provider.requests)-before[2],
                trace=pin(output / (label + '.exec'))))

        publish(a)

        def start(label, selection):
            quiet()
            offset = client.trace.stat().st_size
            thread = session.thread(work, MODEL, sandbox='danger-full-access', ephemeral=False,
                                    modelProvider='fixture', allowProviderModelFallback=False)
            quiet()
            trace = client.trace.read_text()[offset:]
            # This native client defers SessionStart until its first turn.
            (output / (label + '.exec')).write_text(trace)
            return thread

        target = start('target-start', a)
        control = start('control-start', a)
        self.assertNotEqual(target.id, control.id)
        prompt(target, 'fresh-a', a, starting=True)
        prompt(target, 'existing-a', a)
        prompt(control, 'control-a', a, starting=True)
        f.publish_configuration(enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings))
        b = self.selected(f)
        (output / 'selection-b.json').write_bytes(encoded(b[2]))
        (output / 'settings-b.json').write_bytes(Path(b[0]['settings']['path']).read_bytes())
        self.assertNotEqual(a[0]['selection'], b[0]['selection'])
        publish(b)
        prompt(target, 'cached-a-after-b-trust', a, published=b)
        fresh = start('fresh-start', b)
        prompt(fresh, 'fresh-b', b, starting=True)

        def refresh(selection, previous):
            quiet()
            history = session.call('thread/read', dict(threadId=target.id, includeTurns=True))
            self.assertIn('R04 fixture fresh-a', json.dumps(history))
            path = history['thread']['path']
            offset = client.trace.stat().st_size
            session.call('thread/archive', dict(threadId=target.id))
            wait_for(lambda: target.id not in session.loaded_threads(), 'target native thread unloaded')
            quiet()
            self.assertIn(control.id, session.loaded_threads())
            self.assertIn(fresh.id, session.loaded_threads())
            closed = client.trace.read_text()[offset:]
            (output / ('archive-' + str(len(refreshes)) + '.exec')).write_text(closed)
            reached(closed, previous, events=('SessionEnd',))
            session.call('thread/unarchive', dict(threadId=target.id))
            offset = client.trace.stat().st_size
            resumed = session.resume(target.id)
            self.assertEqual(resumed['thread']['id'], target.id)
            self.assertEqual(resumed['thread']['path'], path)
            retained = session.call('thread/read', dict(threadId=target.id, includeTurns=True))
            self.assertIn('R04 fixture fresh-a', json.dumps(retained))
            self.assertIn('R04 fixture cached-a-after-b-trust', json.dumps(retained))
            quiet()
            started = client.trace.read_text()[offset:]
            (output / ('resume-' + str(len(refreshes)) + '.exec')).write_text(started)
            readback(selection, trusted=True)
            refreshes.append(dict(session_id=target.id, rollout_path=path,
                previous=previous[0]['selection'], selection=selection[0]['selection'],
                history_preserved=True, control_loaded=True, fresh_loaded=True))

        refresh(b, a)
        prompt(target, 'refreshed-b', b, starting=True)
        prompt(control, 'control-still-a', a, published=b)
        receipt, saved = f.receipt_path, f.receipt_path.read_bytes()
        receipt.unlink()
        try:
            prompt(target, 'missing-receipt', b, refused=True)
        finally:
            receipt.write_bytes(saved)
            receipt.chmod(0o400)
        overwrite_fixture(receipt, saved + b'\n')
        try:
            prompt(target, 'corrupt-receipt', b, refused=True)
        finally:
            overwrite_fixture(receipt, saved)
        publish(a)
        prompt(target, 'cached-b-before-reverse', b, published=a)
        refresh(a, b)
        prompt(target, 'reversed-a', a, starting=True)
        prompt(fresh, 'fresh-control-still-b', b, published=a)
        self.assertEqual((a[1] / 'previous-settings.json').read_bytes(), original)
        for label, thread, selection in (('target', target, a), ('control', control, a), ('fresh', fresh, b)):
            quiet()
            offset = client.trace.stat().st_size
            session.call('thread/archive', dict(threadId=thread.id))
            wait_for(lambda: thread.id not in session.loaded_threads(), 'native thread closed')
            quiet()
            trace = client.trace.read_text()[offset:]
            reached(trace, selection, events=('SessionEnd',))
            (output / ('close-' + label + '.exec')).write_text(trace)
        self.assertEqual(session.loaded_threads(), [])
        client.close()
        return dict(schema='cairn.r04-native-codex/1', passed=True, cases=observations,
            fixture=pin(Path(__file__)), client=dict(path=str(binary), sha256=digest(binary),
                version=subprocess.check_output([binary, '--version'], text=True, timeout=10,
                                                 env=client.env).strip()),
            refresh=dict(operation='thread/archive, thread/unarchive, thread/resume',
                scope='same thread UUID and rollout history; one app-server process',
                transitions=refreshes, control_remained_a=True, fresh_control_remained_b=True),
            limits=['Disposable profile hooks and native trust only; no live trust, MCP connection or filesystem watcher claim.',
                    'Same private Go image with different receipt/config/V/L; not a code-image upgrade.',
                    'Source Python memory and checked Go coordination are labelled separately.',
                    'Local scripted provider and synthetic Cairn API; no real model/account or production apply.',
                    'No tool/interrupt/compaction coverage, complete watcher-unit barrier, store/auth qualification or full R04/R06 acceptance.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--archive-stdout', action='store_true')
    args = parser.parse_args()
    if digest(args.codex) != args.sha256:
        parser.error('Selected native client hash mismatch')
    if os.environ.get('HOME') != '/tmp/home' or not Path('/tmp/work-tmp').is_dir():
        parser.error('Run inside scripts/test_hybrid_runtime_operations.py')
    with tempfile.TemporaryDirectory(prefix='r04codex-', dir='/tmp') as temporary:
        output = Path(temporary)
        test, report = NativeFixture(), None
        try:
            test.setUpClass()
            report = test.run_fixture(args.codex.resolve(), output)
        finally:
            cleanup_ok = test.doCleanups()
            if hasattr(test, 'build'):
                test.tearDownClass()
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
