"""Real installed launchers selected into disposable Codex/Claude settings."""
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, encoded, write
from test_f04_deadlines import ROWS
from test_r02_compatibility_install import InstalledFixture, InstalledTools
import test_r02_compatibility_install as installed


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class HookFixture(InstalledFixture):
    def publish_configuration(self, **kwargs):
        self.account = self.root / 'account with spaces'
        self.account.mkdir(mode=0o700, exist_ok=True)
        for config in (self.memory, self.coord):
            config['config_home'] = str(self.account)
        super().publish_configuration(**kwargs)

    def env(self):
        variable = 'CODEX_HOME' if self.memory['harness'] == 'codex' else 'CLAUDE_CONFIG_DIR'
        return dict(super().env(), **{variable: str(self.account)})


class R04HookSettingsTests(InstalledTools, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name, harness='codex', settings=None):
        f = HookFixture(self.root / name, self.binary, stager=self.stager, harness=harness)
        f.renderer = f.package / 'stage_lifecycle_hooks.py'
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_hooks.py', f.renderer)
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_compatibility.py',
                        f.package / 'stage_lifecycle_compatibility.py')
        f.settings = f.account / ('hooks.json' if harness == 'codex' else 'settings.json')
        f.settings.write_bytes(encoded(settings if settings is not None else {}))
        f.settings.chmod(0o600)
        f.hook_root = f.root / 'hook-generations'
        f.hook_root.mkdir(mode=0o700)
        f.hook_plan = dict(id=str(uuid.uuid4()), selection=f.selected, harness=harness,
                           account_home=str(f.account), settings=pin(f.settings), coordination_index=0,
                           replace_commands=dict(memory=['old memory'], coordination=['old coordination']))
        return f

    def stage(self, f, plan=None):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(f.renderer), '--stager', str(f.stager),
                               '--root', str(f.hook_root)], input=encoded(plan or f.hook_plan),
                              capture_output=True, timeout=40, cwd=f.root, env=f.env())

    def selected(self, f, plan=None):
        result = self.stage(f, plan)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        selected = json.loads(result.stdout)
        root = Path(selected['selection']['path']).parent
        for name in ('hook-settings.json', 'previous-settings.json', 'hook-selection.json'):
            self.assertEqual((root / name).stat().st_mode & 0o777, 0o400)
        settings = json.loads(Path(selected['settings']['path']).read_bytes())
        metadata = json.loads((root / 'hook-selection.json').read_bytes())
        return selected, settings, metadata

    def recall_api(self, f):
        # SessionStart now reaches the real Go SDK. Keep the CLI fixture for
        # retained rows and provision its new external API boundary as well.
        token = f.root / 'recall-token'
        socket = f.root / 'recall.sock'
        write(token, b'r04-synthetic-token', 0o600)
        for config in (f.memory, f.coord):
            config.update(socket=str(socket), token_file=str(token))
        f.publish_configuration()
        f.hook_plan['selection'] = f.selected
        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation = self.path.removeprefix('/v1/')
                authorization = self.headers.get('Authorization')
                calls.append((operation, authorization))
                if authorization != 'Bearer r04-synthetic-token':
                    self.send_error(401)
                    return
                if operation == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1', entities_omitted=True))
                elif operation == 'index':
                    data = dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111', nonce='',
                                             seal='blake3:' + 'a' * 64,
                                             semantic=dict(schema='cairn.semantic/17', mode='index', status='READY',
                                                           scope=request['scope'],
                                                           purpose='context', destination=dict(name='hosted', allow_local=False),
                                                           query='sha256:fixture-query', available_tokens=32000,
                                                           selected=[], omitted={}, index=[])),
                                handles=[], expires_at='2030-01-01T00:00:00Z', credits_remaining=4, bytes_remaining=24000)
                else:
                    self.send_error(400)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(encoded(dict(schema='cairn.response/1', ok=True, status='OK', data=data)))

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        server = Server(str(socket), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close)
        return calls

    def test_preserves_other_settings_and_group_position_and_exact_previous_bytes(self):
        foreign = dict(type='command', command='/other/hook --unrelated', timeout=123, custom=True)
        data = dict(env=dict(OTHER='keep'), opaque=[900719925474099312345, 'unchanged'], hooks={
            'SessionStart': [dict(matcher='startup|resume', custom='group', hooks=[foreign,
                dict(type='command', command='old memory', timeout=1),
                dict(type='command', command='old coordination', timeout=1), foreign])],
            'UnsupportedEvent': [dict(hooks=[dict(type='command', command='old memory', timeout=71)])]})
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness, data)
                original = b'  ' + json.dumps(data, indent=2).encode() + b'\n'
                f.settings.write_bytes(original)
                f.hook_plan['settings'] = pin(f.settings)
                selected, settings, metadata = self.selected(f)
                self.assertEqual(f.settings.read_bytes(), original)
                self.assertEqual(Path(selected['previous_settings']['path']).read_bytes(), original)
                self.assertEqual(settings['env'], data['env'])
                self.assertEqual(settings['opaque'], data['opaque'])
                self.assertEqual(settings['hooks']['UnsupportedEvent'], data['hooks']['UnsupportedEvent'])
                group = settings['hooks']['SessionStart'][0]
                self.assertEqual((group['matcher'], group['custom']), ('startup|resume', 'group'))
                self.assertEqual((group['hooks'][0], group['hooks'][3]), (foreign, foreign))
                self.assertEqual(len(group['hooks']), 4)
                self.assertEqual(group['hooks'][1]['timeout'], 13)
                self.assertEqual(group['hooks'][2]['timeout'], 15)
                for role, events in ROWS[harness].items():
                    for event, budget in events.items():
                        row = next(row for row in metadata['rows'] if (row['role'], row['event']) == (role, event))
                        self.assertEqual(row['hook']['timeout'], budget)
                        self.assertEqual(shlex.split(row['hook']['command']), row['argv'])
                        self.assertEqual(row['argv'], f.installed['launchers'][role + ':' + ('0' if role == 'memory' else '1') + ':' + str(budget)])
                        self.assertEqual(row['hook'].get('async'), True if (harness, role, event) == ('codex', 'memory', 'Stop') else None)
                        limit = 12000 if harness == 'codex' and role == 'memory' and event in ('SessionStart', 'UserPromptSubmit') else None
                        self.assertEqual(row['hook'].get('additionalContextLimit'), limit)
                for key in ('native_trust', 'refresh', 'activation'):
                    self.assertEqual((selected[key], metadata[key]), ('not-performed', 'not-performed'))
                self.assertEqual(metadata['lifecycle_selection'], f.selected)
                self.assertEqual(f.api_calls(), [])
                self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_each_emitted_hook_runs_selected_real_activation_after_producer_inputs_are_removed(self):
        for harness in ('codex', 'claude'):
            f = self.fixture(harness + " hook's profile", harness)
            recall_calls = self.recall_api(f) if harness == 'codex' else None
            _, _, metadata = self.selected(f)
            shutil.rmtree(f.package)
            for row in metadata['rows']:
                with self.subTest(harness=harness, role=row['role'], event=row['event']):
                    raw = encoded(dict(f.event, hook_event_name=row['event'], prompt='', host_pid=os.getpid()))
                    result = subprocess.run(['/bin/sh', '-c', row['hook']['command']], input=raw,
                                            capture_output=True, timeout=18, cwd=f.root, env=f.env())
                    self.assertNotIn(b'compatibility_preflight', result.stderr)
                    self.assertNotIn(b'CompatibilityPreflightError', result.stderr)
                    self.assertIn(result.returncode, (0, 1), result.stderr)
                    if row['event'] in ('SessionStart', 'UserPromptSubmit'):
                        self.assertEqual((result.returncode, result.stderr), (0, b''))
                    for source in (ROOT / 'scripts', ROOT / 'integrations'):
                        self.assertNotIn((str(source) + '/').encode(), result.stdout + result.stderr)
                    if result.stdout:
                        self.assertIsInstance(json.loads(result.stdout), dict)
            suffix = '.memory-budget.json' if harness == 'codex' else '.inbox-recall.json'
            self.assertTrue(list(Path(f.memory['state_dir']).glob('*' + suffix)))
            self.assertIn('agent-register', [call['operation'] for call in f.api_calls()])
            if recall_calls is not None:
                self.assertIn('index', [operation for operation, _ in recall_calls])
                self.assertTrue(all(authorization == 'Bearer r04-synthetic-token'
                                    for _, authorization in recall_calls))

    def test_repeat_rendering_has_no_duplicate_hooks_and_old_settings_selection_remains(self):
        f = self.fixture('repeat')
        selected, before, _ = self.selected(f)
        repeated, _, _ = self.selected(f)
        self.assertEqual(repeated['selection'], selected['selection'])
        plan = dict(f.hook_plan, id=str(uuid.uuid4()), settings=selected['settings'])
        new, after, _ = self.selected(f, plan)
        self.assertEqual(after, before)
        self.assertEqual(Path(new['previous_settings']['path']).read_bytes(), Path(selected['settings']['path']).read_bytes())
        self.assertEqual(json.loads(Path(selected['settings']['path']).read_bytes()), before)
        self.assertNotEqual(new['selection'], selected['selection'])

    def test_wrong_selected_profile_or_pin_refuses_without_settings_publication(self):
        for kind in ('harness', 'account', 'index', 'boolean-index', 'generation-pin', 'settings-pin'):
            with self.subTest(kind=kind):
                f = self.fixture(kind)
                plan = copy.deepcopy(f.hook_plan)
                if kind == 'harness': plan['harness'] = 'claude'
                elif kind == 'account': plan['account_home'] += '-other'
                elif kind == 'index': plan['coordination_index'] = 1
                elif kind == 'boolean-index': plan['coordination_index'] = True
                elif kind == 'generation-pin': plan['selection']['sha256'] = '0' * 64
                else: plan['settings']['sha256'] = '0' * 64
                result = self.stage(f, plan)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'hook staging failed:', result.stderr)
                self.assertFalse(list(f.hook_root.glob('selection-*')))
                self.assertEqual(f.settings.read_bytes(), b'{}')
                self.assertEqual(f.api_calls(), [])

    def test_ambiguous_or_malformed_touched_hooks_refuse_without_rewriting_input(self):
        for groups in ([dict(hooks=[dict(command='old memory'), dict(command='old memory')])],
                       [dict(hooks='not an array')], 'not an array'):
            f = self.fixture(str(uuid.uuid4()), settings=dict(hooks=dict(SessionStart=groups)))
            before = f.settings.read_bytes()
            result = self.stage(f)
            self.assertEqual((result.returncode, result.stdout), (1, b''))
            self.assertEqual(f.settings.read_bytes(), before)
            self.assertFalse(list(f.hook_root.glob('selection-*')))

    def test_changed_installed_launcher_refuses_before_a_settings_selection_is_published(self):
        f = self.fixture('tamper')
        launcher = f.launcher('memory')
        launcher.chmod(0o700)
        launcher.write_bytes(b'#!/bin/sh\nprintf false-success\n')
        launcher.chmod(0o500)
        result = self.stage(f)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertFalse(list(f.hook_root.glob('selection-*')))
        self.assertEqual(f.api_calls(), [])

    def test_previous_commands_cannot_repurpose_another_selected_role(self):
        f = self.fixture('cross-role-plan')
        _, _, metadata = self.selected(f)
        for role, other in (('memory', 'coordination'), ('coordination', 'memory')):
            with self.subTest(role=role):
                command = next(row['hook']['command'] for row in metadata['rows']
                               if (row['role'], row['event']) == (other, 'SessionStart'))
                plan = dict(f.hook_plan, id=str(uuid.uuid4()), replace_commands=dict(memory=[], coordination=[]))
                plan['replace_commands'][role] = [command]
                result = self.stage(f, plan)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'previous command selects another current role', result.stderr)
                self.assertFalse((f.hook_root / ('selection-' + plan['id'])).exists())
                self.assertEqual(f.settings.read_bytes(), b'{}')
                self.assertEqual(f.api_calls(), [])
                self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_publication_failure_reconciles_and_interrupted_renderer_joins_its_child(self):
        f = self.fixture('publication')
        real = f.stager
        for side in ('before', 'after'):
            with self.subTest(side=side):
                f.hook_plan['id'] = str(uuid.uuid4())
                wrapper, _ = installed.R02CompatibilityInstallationTests.fault_stager(self, f, abort=[1, side])
                f.stager = wrapper
                result = self.stage(f)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertEqual((f.hook_root / ('selection-' + f.hook_plan['id'])).exists(), side == 'after')
                f.stager = real
                selected, _, _ = self.selected(f)
                repeated, _, _ = self.selected(f)
                self.assertEqual(selected['selection'], repeated['selection'])
        f.hook_plan['id'] = str(uuid.uuid4())
        wrapper, stopped = installed.R02CompatibilityInstallationTests.fault_stager(self, f, stop=True)
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(f.renderer), '--stager', str(wrapper),
                                    '--root', str(f.hook_root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=f.root, env=f.env())
        try:
            process.stdin.write(encoded(f.hook_plan)); process.stdin.close(); process.stdin = None
            deadline = time.monotonic() + 10
            while not stopped.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(stopped.exists(), 'renderer did not reach its owned publishing child')
            child = int(stopped.read_text())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual((process.returncode, stdout), (1, b''))
            self.assertIn(b'interrupted', stderr)
            with self.assertRaises(ProcessLookupError): os.kill(child, 0)
        finally:
            if process.poll() is None: process.kill()
            process.communicate()
        self.assertEqual(f.settings.read_bytes(), b'{}')
        self.assertEqual(f.api_calls(), [])
        self.selected(f)

    def test_new_settings_selection_keeps_the_old_command_and_its_enabled_binding(self):
        f = self.fixture('cached')
        self.recall_api(f)
        old, _, metadata = self.selected(f)
        previous_settings = Path(old['settings']['path']).read_bytes()
        previous = next(row['hook']['command'] for row in metadata['rows']
                        if (row['role'], row['event']) == ('memory', 'UserPromptSubmit'))
        f.publish_configuration(enabled=False)
        plan = dict(f.hook_plan, id=str(uuid.uuid4()), selection=f.selected, settings=old['settings'],
                    replace_commands=dict(memory=[row['hook']['command'] for row in metadata['rows']
                                                  if row['role'] == 'memory'], coordination=[]))
        # Distinct events share an H-specific command; the explicit owned
        # command selection names each string once.
        plan['replace_commands']['memory'] = list(dict.fromkeys(plan['replace_commands']['memory']))
        plan['replace_commands']['coordination'] = list(dict.fromkeys(row['hook']['command'] for row in metadata['rows']
                                                                      if row['role'] == 'coordination'))
        _, _, current = self.selected(f, plan)
        command = next(row['hook']['command'] for row in current['rows']
                       if (row['role'], row['event']) == ('memory', 'UserPromptSubmit'))
        self.assertNotEqual(command, previous)
        for chosen, native, expected in ((previous, str(uuid.uuid4()), True), (command, str(uuid.uuid4()), False)):
            result = subprocess.run(['/bin/sh', '-c', chosen], input=encoded(dict(f.event, session_id=native)),
                                    capture_output=True, cwd=f.root, env=f.env(), timeout=18)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
            ledger = json.loads((Path(f.memory['state_dir']) / (native + '.memory-budget.json')).read_bytes())
            self.assertEqual(list(ledger['inbox_activation'].values()), [expected])
        self.assertEqual(Path(old['settings']['path']).read_bytes(), previous_settings)


if __name__ == '__main__':
    unittest.main()
