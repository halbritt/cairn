"""Real installed Source frontend and direct checked Go bound-memory processes."""
import base64
import copy
import fcntl
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, TURN, compat, encoded, scalar_tree, sha, write
from test_r04_hook_settings import HookFixture


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class BoundMemoryCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary, cls.stager, cls.cli = (Path(cls.build.name) / name for name in ('memory', 'stager', 'cairn'))
        for output, package in ((cls.binary, './cmd/cairn-lifecycle'),
                                (cls.stager, './cmd/cairn-stage-lifecycle'), (cls.cli, './cmd/cairn')):
            subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(output), package], cwd=ROOT, check=True)
            output.chmod(0o500)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = HookFixture(self.root / 'project', self.binary, stager=self.stager)
        token = self.root / 'token'
        write(token, b'bound-memory-synthetic-token', 0o600)
        for config in (self.f.memory, self.f.coord):
            config.update(cairn=str(self.cli), socket=str(self.root / 'api.sock'), token_file=str(token))
        self.f.memory['recall_observations'] = True
        self.f.publish_configuration()
        self.event('SessionStart', source='startup', prompt='Inspect current checked memory source.')
        self.selected = []
        self.refusal = None
        self.calls = []
        self.report_locks = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation = self.path.removeprefix('/v1/')
                owner.calls.append((operation, body, dict(self.headers)))
                if self.headers.get('Authorization') != 'Bearer bound-memory-synthetic-token':
                    self.send_error(401)
                    return
                if operation == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1', entities_omitted=True))
                elif operation == 'index':
                    if getattr(owner, 'index_hook', None):
                        owner.index_hook()
                    if owner.refusal:
                        self.send_response(422)
                        self.end_headers()
                        self.wfile.write(encoded(dict(schema='cairn.response/1', ok=False, status=owner.refusal,
                                                      message='synthetic refusal')))
                        return
                    data = dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111', nonce='',
                                             seal='blake3:' + 'a' * 64,
                                             semantic=dict(schema='cairn.semantic/17', mode='index', status='READY',
                                                           scope=body['scope'], purpose='context',
                                                           destination=dict(name='hosted', allow_local=False),
                                                           query='sha256:bound-memory-fixture', available_tokens=32000,
                                                           selected=owner.selected, omitted={}, index=[])),
                                handles=[], expires_at='2030-01-01T00:00:00Z', credits_remaining=4, bytes_remaining=24000)
                elif operation in ('recall-observation', 'capture-observation'):
                    owner.observe_locks(operation)
                    data = {}
                else:
                    self.send_error(400)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(encoded(dict(schema='cairn.response/1', ok=True, status='OK', data=data)))

        self.server = Server(str(self.root / 'api.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def observe_locks(self, operation):
        import fcntl
        locks = {}
        directory = Path(self.f.memory['state_dir'])
        for suffix in (('.lock', '.capture.lock') if operation == 'capture-observation' else ('.lock',)):
            path = directory / (self.f.event['session_id'] + suffix)
            with path.open('a') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locks[suffix] = True
                except BlockingIOError:
                    locks[suffix] = False
        self.report_locks.append(locks)

    def event(self, name, **fields):
        self.f.event = dict(hook_event_name=name, session_id=SESSION, cwd=str(self.f.root))
        self.f.event.update(fields)
        self.f.raw = encoded(self.f.event)

    def timeout(self):
        return 150 if self.f.event['hook_event_name'] in ('PreCompact', 'Stop', 'SessionEnd') else 13

    def go_vector(self, timeout=None):
        return [str(self.f.binary), 'memory', '--receipt', str(self.f.receipt_path), '--receipt-sha256', self.f.receipt_sha,
                '--role', 'memory', '--entrypoint', 'memory-hook.py', '--config', str(self.f.memory_path),
                '--hook-timeout', str(self.timeout() if timeout is None else timeout),
                '--launch-root', str(self.f.launch_root), '--shell', '/bin/sh', *getattr(self, 'extra_selection', ())]

    def go_launcher(self, timeout=None):
        raw = ('#!/bin/sh\nexec ' + shlex.join(self.go_vector(timeout)) + ' -- "$@"\n').encode()
        directory = self.f.launch_root / sha(raw)
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / 'inbox'
        if path.exists():
            self.assertEqual(path.read_bytes(), raw)
        else:
            write(path, raw, 0o500)
        return path

    def go(self, *, timeout=None, prefix=()):
        return subprocess.run([*(prefix or getattr(self, 'go_prefix', ())), str(self.go_launcher(timeout))], input=self.f.raw, cwd=self.f.root,
                              env=self.f.env(), capture_output=True, timeout=165 if self.timeout() == 150 else 20)

    def source(self, *, timeout=None):
        timeout = self.timeout() if timeout is None else timeout
        if self.f.receipt_sha == self.f.installed['receipt']['sha256']:
            launcher = self.f.launcher('memory', timeout)
        else:
            vector = compat.launch_vector(sys.executable, self.f.receipt_path, self.f.receipt_sha, 'memory',
                                          'memory-hook.py', self.f.memory_path, timeout, self.f.launch_root)
            raw = compat.launcher_bytes(vector)
            directory = self.f.launch_root / sha(raw)
            directory.mkdir(mode=0o700, exist_ok=True)
            launcher = directory / 'inbox'
            if not launcher.exists():
                write(launcher, raw, 0o500)
        return subprocess.run([*getattr(self, 'source_prefix', ()), str(launcher)],
                              input=self.f.raw, cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=165)

    def before_run(self, side):
        if getattr(self, 'prepare_run', None):
            self.prepare_run(side)

    def after_run(self):
        pass

    def state(self):
        directory = Path(self.f.memory['state_dir'])
        result = {}
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix == '.lock':
                    continue
                body = json.loads(path.read_bytes())
                for label in ('last_recall', 'last_capture'):
                    for key in ('at', 'duration_ms', 'elapsed_seconds'):
                        measurement = body.get(label, {}).pop(key, None)
                        if measurement is not None:
                            self.assertIn(type(measurement), (int, float))
                            self.assertGreaterEqual(measurement, 0)
                result[path.name] = body
        return result

    def reset_state(self, seed):
        directory = Path(self.f.memory['state_dir'])
        shutil.rmtree(directory, ignore_errors=True)
        if seed:
            directory.mkdir(mode=0o700)
            for name, raw in seed.items():
                write(directory / name, raw)

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {path.name: path.read_bytes() for path in directory.iterdir()} if directory.exists() else {}
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('source')
        source = self.source()
        self.after_run()
        source_state, calls, locks = self.state(), list(self.calls), list(self.report_locks)
        self.reset_state(seed)
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('go')
        go = self.go()
        self.after_run()
        self.assertEqual((go.returncode, go.stdout, go.stderr), (source.returncode, source.stdout, source.stderr))
        actual_state = self.state()
        self.assertEqual(actual_state.keys(), source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(actual_state[name]), scalar_tree(source_state[name]), name)
        self.assertEqual([row[0] for row in self.calls], [row[0] for row in calls])
        self.assertEqual(self.report_locks, locks)
        self.assertTrue(all(all(row.values()) for row in self.report_locks))
        for actual, expected in zip(self.calls, calls):
            def normalized(row):
                body = copy.deepcopy(row[1])
                identifier = body.pop('request_id', None)
                if row[0] != 'version':
                    self.assertEqual(str(uuid.UUID(identifier)), identifier)
                    self.assertNotEqual(uuid.UUID(identifier).int, 0)
                if row[0] in ('capture-observation', 'recall-observation'):
                    for key in ('duration_ms', 'elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                        if key in body:
                            self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(row[2]['Authorization'], 'Bearer bound-memory-synthetic-token')
                return body
            self.assertEqual(normalized(actual), normalized(expected))
            for row in (actual, expected):
                declaration = row[2]['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(declaration + '=' * (-len(declaration) % 4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                if row is actual or row[0] != 'capture-observation':
                    self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported',
                                                  implementation_id=self.f.receipt['binary']['sha256']))
                else:
                    self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported'))
        return go

    def overwrite(self, path, raw):
        mode = path.stat().st_mode & 0o777
        replacement = path.with_name('.changed-' + path.name)
        write(replacement, raw, mode)
        replacement.replace(path)

    def repin_profiles(self):
        manifest = json.loads(self.f.binding.read_bytes())
        manifest['memory_config']['sha256'] = sha(self.f.memory_path.read_bytes())
        manifest['coordination_configs'][0]['sha256'] = sha(self.f.coord_path.read_bytes())
        self.overwrite(self.f.binding, encoded(manifest))
        self.f.receipt.update(memory_config=manifest['memory_config'], coordination_configs=manifest['coordination_configs'])
        self.f.receipt['binding']['sha256'] = sha(self.f.binding.read_bytes())
        self.overwrite(self.f.receipt_path, encoded(self.f.receipt))
        self.f.receipt_sha = sha(self.f.receipt_path.read_bytes())


class BoundMemoryTests(BoundMemoryCase):
    def test_actual_installed_start_and_owner_prompt(self):
        print('BOUND_MEMORY_PINS=' + json.dumps(dict(receipt_sha256=self.f.receipt_sha,
              source_members=self.f.receipt['members'], go_image_sha256=sha(self.f.binary.read_bytes())), sort_keys=True), flush=True)
        self.selected = [dict(record=dict(record_id='55555555-5555-4555-8555-555555555555', version=1,
                                         body='Required current Source fixture.'), mandatory=True, reason='required',
                              evidence=[], authority=[])]
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.reset_state({})
                self.event(name, prompt='Inspect current checked memory source.', turn_id=TURN)
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                self.assertIn(b'hookSpecificOutput', result.stdout)
                self.assertIn('index', [row[0] for row in self.calls])

    def test_changed_receipt_closure_image_or_enabled_profiles_refuse_before_effects(self):
        for name, path in (('receipt', self.f.receipt_path), ('closure', Path(self.f.receipt['snapshot_root']) / 'memory.py'),
                           ('image', self.f.binary), ('memory', self.f.memory_path),
                           ('coordinator', self.f.coord_path), ('manifest', self.f.binding)):
            with self.subTest(artifact=name):
                old = path.read_bytes()
                self.overwrite(path, old + b' ')
                try:
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                finally:
                    self.overwrite(path, old)

    def test_qualified_wake_uses_one_shared_locked_decision_without_recall(self):
        self.event('UserPromptSubmit', turn_id=TURN, prompt=self.f.wake())
        self.assertEqual(self.pair().stdout, b'{}\n')
        ledger = self.state()[SESSION + '.memory-budget.json']
        self.assertEqual(list(ledger['inbox_activation'].values()), [True])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.state()[SESSION + '.memory-budget.json'], ledger)

    def test_current_required_conflict_keeps_source_stop_and_prompt_refusal(self):
        self.refusal = 'OPEN_CONFLICT'
        for name, expected in (('SessionStart', 0), ('UserPromptSubmit', 2)):
            with self.subTest(event=name):
                self.reset_state({})
                self.event(name, turn_id=TURN, prompt='Restore current required context.')
                result = self.pair()
                self.assertEqual(result.returncode, expected)
                if name == 'SessionStart':
                    self.assertEqual(json.loads(result.stdout)['continue'], False)
                else:
                    self.assertEqual(result.stdout, b'')

    def test_privacy_and_account_exclusions_do_not_read_binding_or_create_state(self):
        for kind in ('privacy', 'account'):
            with self.subTest(kind=kind):
                marker = self.f.root / '.cairn-no-memory'
                original_env = self.f.env
                if kind == 'privacy':
                    write(marker, b'')
                else:
                    self.f.env = lambda: dict(original_env(), CODEX_HOME=str(self.root / 'other-account'))
                parked = self.f.binding.with_name('unavailable-binding.json')
                self.f.binding.rename(parked)
                try:
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                finally:
                    parked.rename(self.f.binding)
                    self.f.env = original_env
                    if marker.exists():
                        marker.unlink()

    def test_repinned_incompatible_profiles_refuse_without_recall_or_state(self):
        original = self.f.coord_path.read_bytes()
        for changes in (dict(native_delivery=False), dict(state_dir=self.f.memory['state_dir']),
                        dict(inbox_recall_binding=str(self.root / 'another-binding.json')),
                        dict(repo='another-collection')):
            with self.subTest(changes=changes):
                config = json.loads(original)
                config.update(changes)
                self.overwrite(self.f.coord_path, encoded(config))
                self.repin_profiles()
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_removing_bound_opt_in_cannot_downgrade_the_enabled_generation(self):
        config = json.loads(self.f.memory_path.read_bytes())
        config.pop('inbox_recall_binding')
        self.overwrite(self.f.memory_path, encoded(config))
        self.repin_profiles()
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'CompatibilityPreflightError', result.stderr)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_disabled_generation_retains_source_unbound_accounting_without_reselection(self):
        self.f.publish_configuration(enabled=False)
        self.event('UserPromptSubmit', turn_id=TURN, prompt='Inspect this owner task.')
        self.assertEqual(self.pair().returncode, 0)
        grant = self.state()[SESSION + '.memory-budget.json']['turns'][TURN]
        self.assertTrue(grant['granted'])
        self.assertNotIn('serialized_hook_bytes', grant)
        self.assertIn('inbox_recall_binding', json.loads(self.f.memory_path.read_bytes()))

    def test_cached_true_requires_final_profile_pins_under_a_disabled_manifest(self):
        self.event('UserPromptSubmit', turn_id=TURN, prompt=self.f.wake())
        self.assertEqual(self.pair().returncode, 0)
        before = self.state()
        self.f.publish_configuration(enabled=False)
        self.overwrite(self.f.coord_path, self.f.coord_path.read_bytes() + b' ')
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), before)

    def test_native_session_shape_refuses_before_recall_and_grants(self):
        for session in ('', 'not-a-session-uuid', 7, ['caller-selected']):
            with self.subTest(session=session):
                self.event('SessionStart', session_id=session)
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_native_turn_metadata_does_not_authenticate_an_inbox_lease(self):
        for turn in (None, 'unproven-turn', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(turn=turn):
                self.reset_state({})
                self.event('UserPromptSubmit', turn_id=turn, prompt='Inspect current source.',
                           agent_id='caller-principal', execution_id='caller-execution', lease_id='caller-lease',
                           expires_at='2099-01-01T00:00:00Z', owner_input_attestation=dict(accepted=True))
                self.assertEqual(self.pair().returncode, 0)
                self.assertTrue(set(row[0] for row in self.calls) <= {'version', 'index', 'recall-observation'})
                turns = self.state()[SESSION + '.memory-budget.json']['turns']
                if turn == '00000000-0000-0000-0000-000000000000':
                    self.assertTrue(turns[turn]['granted'])
                else:
                    self.assertEqual(turns, {})

    def test_bound_recall_session_lock_and_activation_lock_keep_exact_refusals(self):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        for wake in (False, True):
            with self.subTest(activation=wake):
                self.event('UserPromptSubmit', turn_id=TURN, prompt=self.f.wake() if wake else 'Inspect current source.')
                results = []
                with (directory / (SESSION + '.lock')).open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    for side in (self.source, self.go):
                        results.append(side())
                self.assertEqual([(r.returncode, r.stdout, r.stderr) for r in results],
                                 [(results[0].returncode, results[0].stdout, results[0].stderr)] * 2)
                self.assertEqual((results[0].returncode, results[0].stdout), (1, b''))
                self.assertIn(b'phase=activation error_class=BlockingIOError' if wake else b'a memory hook is already running', results[0].stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_extra_auth_deadline_vector_and_descriptor_flags_cannot_enter_body(self):
        for key in ('deadline', 'vector', 'fd', 'agent-id', 'execution-id', 'lease', 'activation-enabled'):
            with self.subTest(flag=key):
                self.extra_selection = ['--' + key, '1']
                result = self.go()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'CompatibilityPreflightError', result.stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_original_entry_deadline_covers_stdin_before_any_body_effect(self):
        started = time.monotonic()
        child = subprocess.Popen([str(self.go_launcher())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=self.f.root, env=self.f.env())
        try:
            child.stdin.write(self.f.raw)
            child.stdin.flush()
            child.wait(timeout=17)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 12)
            self.assertLess(elapsed, 17)
            self.assertEqual((child.returncode, child.stdout.read()), (1, b''))
            self.assertIn(b'CompatibilityPreflightError', child.stderr.read())
            self.assertEqual(self.calls, [])
            self.assertEqual(self.state(), {})
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def test_post_body_pin_refusal_keeps_committed_required_grant(self):
        original = self.f.coord_path.read_bytes()
        self.prepare_run = lambda side: self.overwrite(self.f.coord_path, original)
        self.index_hook = lambda: self.overwrite(self.f.coord_path, original + b' ')
        self.selected = [dict(record=dict(record_id='55555555-5555-4555-8555-555555555555', version=1,
                                         body='Required current Source fixture.'), mandatory=True, reason='required',
                              evidence=[], authority=[])]
        self.event('UserPromptSubmit', turn_id=TURN, prompt='Restore current required context.')
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        self.assertIn(b'phase=activation error_class=ValueError', result.stderr)
        grant = self.state()[SESSION + '.memory-budget.json']['turns'][TURN]
        self.assertTrue(grant['granted'])
        self.assertGreater(grant['emitted_hook_bytes'], 0)
        self.assertGreater(grant['reserved_native_bytes'], 0)

    def test_post_body_check_reuses_original_parent_config_snapshot(self):
        original = self.f.memory_path.read_bytes()
        self.prepare_run = lambda side: self.overwrite(self.f.memory_path, original)
        self.index_hook = lambda: self.overwrite(self.f.memory_path, original + b' ')
        self.event('UserPromptSubmit', turn_id=TURN, prompt='Inspect current checked source.')
        self.assertEqual(self.pair().returncode, 0)
        self.assertTrue(self.state()[SESSION + '.memory-budget.json']['turns'][TURN]['granted'])


if __name__ == '__main__':
    unittest.main()
