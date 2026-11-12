#!/usr/bin/env python3
"""R-03 installed Hermes processes using real R-02 staging and local API fixtures.

Run through the documented host executor. No real selector/model account is
used. Fixture roots, exact streams, requests, state and executable pins remain
under --output, including failed attempts. Native object checks require the
existing Hermes interpreter/source explicitly; they never silently skip.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import unittest

import test_f03_capture_selection as capture
from test_r04_host_wiring import UnboundFixture
from test_r03_hermes_installer import installer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = HERMES_ROOT = HERMES_PYTHON = PREBUILT_IMAGES = SOURCE_REVISION = None
EMIT_CASES = False


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HermesInstalledFixture(UnboundFixture):
    def configuration_plan(self, **kwargs):
        plan = super().configuration_plan(**kwargs)
        plan['source_revision'] = SOURCE_REVISION
        return plan


class InstalledMemory(unittest.TestCase):
    configure = capture.CaptureSelectionTests.configure
    session_key = capture.CaptureSelectionTests.session_key
    close_server = capture.CaptureSelectionTests.close_server
    rollout = capture.CaptureSelectionTests.rollout
    note = capture.CaptureSelectionTests.note

    @classmethod
    def setUpClass(cls):
        cls.images = PREBUILT_IMAGES or OUTPUT / 'images'
        if PREBUILT_IMAGES is None:
            cls.images.mkdir(mode=0o700)
        cls.binary, cls.stager, cls.cli = (cls.images / name for name in ('lifecycle', 'stager', 'cairn'))
        if PREBUILT_IMAGES is not None:
            pins = json.loads(cls.images.joinpath('pins.json').read_bytes())
            for path in (cls.binary, cls.stager, cls.cli):
                if sha(path) != pins[path.name]['sha256'] or path.stat().st_mode & 0o222:
                    raise RuntimeError('R03_IMAGE_INPUT: frozen image/pin mismatch: ' + str(path))
            OUTPUT.joinpath('input-images.json').write_text(json.dumps(pins, indent=2) + '\n')
            return
        for path, target in ((cls.binary, './cmd/cairn-lifecycle'),
                             (cls.stager, './cmd/cairn-stage-lifecycle'), (cls.cli, './cmd/cairn')):
            command = ['go', 'build', '-buildvcs=false', '-o', str(path), target]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=120)
            (cls.images / (path.name + '.build.stdout')).write_bytes(result.stdout)
            (cls.images / (path.name + '.build.stderr')).write_bytes(result.stderr)
            if result.returncode:
                raise RuntimeError('R03_BUILD_INPUT: ' + str(command) + ' exit=' + str(result.returncode))
            path.chmod(0o500)
        pins = {p.name: dict(path=str(p), sha256=sha(p)) for p in (cls.binary, cls.stager, cls.cli)}
        pins['go_version'] = subprocess.check_output(['go', 'version'], text=True).strip()
        pins['source_head'] = SOURCE_REVISION
        (cls.images / 'pins.json').write_text(json.dumps(pins, indent=2) + '\n')

    def setUp(self):
        # Reuse the existing external API and scripted selector engines. No
        # Source/candidate handler, admission, SDK or state body is replaced.
        capture.CaptureSelectionTests.setUp(self)
        self.addCleanup(self.retain)
        old = self.f
        self.f = HermesInstalledFixture(self.root / 'installed project', self.binary,
                                stager=self.stager, harness='hermes', enabled=False)
        self.f.memory.update({key: old.memory[key] for key in
                              ('cairn', 'socket', 'token_file', 'claude', 'model', 'capture_observations')})
        self.f.memory['recall_mode'] = 'agent_tools'
        self.f.publish_configuration(enabled=False)
        self.home = self.f.account
        self.session = 'Hermes R03/session:日本語'
        self.f.event = dict(hook_event_name='UserPromptSubmit', session_id=self.session,
                            cwd=str(self.f.root), prompt='Continue retention policy.')
        self.skill = self.root / 'fixture-skill.md'
        self.skill.write_text('Synthetic R03 fixture skill.\n')
        self.native = dict(executable=str(self.cli), socket=self.f.memory['socket'],
                           token_file=self.f.memory['token_file'], repo=self.f.memory['repo'])
        self.home.joinpath('config.yaml').write_text(json.dumps(dict(unrelated={'keep': ['日本語', 7]},
            memory={'memory_enabled': True}, plugins={'enabled': []})))
        self.original_config = self.home.joinpath('config.yaml').read_bytes()
        self.root.joinpath('selector').chmod(0o500)
        self.selection = dict(checkpoint=None, workstream=None, memories=[self.note()])
        self.publish_selector()
        self.results = []

    def publish_selector(self):
        self.root.joinpath('provider.json').write_text(json.dumps(dict(result=dict(structured_output=self.selection))))

    def retain(self):
        case = OUTPUT / self._testMethodName
        case.mkdir(mode=0o700)
        if hasattr(self, 'results'):
            case.joinpath('processes.json').write_text(json.dumps(self.results, indent=2) + '\n')
        case.joinpath('api.json').write_text(json.dumps(self.calls, indent=2) + '\n')
        sockets = {str(p.relative_to(self.root)): dict(mode=stat.S_IMODE(p.stat().st_mode),
                                                     kind='unix-socket; requests retained in api.json')
                   for p in self.root.rglob('*') if p.is_socket()}
        case.joinpath('socket-inventory.json').write_text(json.dumps(sockets, indent=2) + '\n')
        if EMIT_CASES:
            roots = [Path(self.f.memory['state_dir']), self.home / 'cairn'] if hasattr(self, 'home') else []
            files = {str(p.relative_to(self.root)): dict(mode=stat.S_IMODE(p.stat().st_mode),
                     base64=base64.b64encode(p.read_bytes()).decode())
                     for root in roots if root.exists() for p in sorted(root.rglob('*')) if p.is_file()}
            extra = [self.f.memory_path, self.f.coord_path, self.f.binding, self.f.receipt_path]
            if hasattr(self, 'selected'):
                extra += [Path(argv[0]) for argv in self.selected['commands'].values()]
            for pattern in ('calls.json', 'result.json', 'provider.json', 'r03-fixture.json'):
                extra += list(self.root.rglob(pattern))
            for path in extra:
                if path.is_file():
                    files[str(path.relative_to(self.root))] = dict(mode=stat.S_IMODE(path.stat().st_mode),
                        base64=base64.b64encode(path.read_bytes()).decode())
            print('R03_CASE=' + json.dumps(dict(name=self._testMethodName, processes=getattr(self, 'results', []),
                  api=self.calls, states=files, sockets=sockets, records=getattr(self, 'records', []),
                  selection=getattr(self, 'selected', None),
                  receipt=getattr(self.f, 'receipt', None)), ensure_ascii=True), flush=True)
        shutil.copytree(self.root, case / 'fixture',
                        ignore=lambda directory, names: [name for name in names if (Path(directory) / name).is_socket()])

    def install(self):
        installer.install(self.home, self.native, str(self.root / 'selector'), self.skill,
                          lifecycle_selection=self.f.selected, stager=str(self.stager))
        self.selected = json.loads(self.home.joinpath('cairn-lifecycle.json').read_bytes())
        return self.selected

    def run_event(self, name, **fields):
        event = dict(self.f.event, hook_event_name=name, **fields)
        raw = json.dumps(event, ensure_ascii=True, separators=(',', ':')).encode()
        argv = self.selected['commands'][name]
        result = subprocess.run(argv, input=raw, cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=155)
        self.results.append(dict(argv=argv, input=raw.decode(), exit=result.returncode,
                                 stdout=result.stdout.decode(), stderr=result.stderr.decode()))
        return result

    def assert_go_attribution(self):
        for _, _, headers in self.calls:
            declaration = headers['Cairn-Client-Diagnostics']
            origin = json.loads(base64.urlsafe_b64decode(declaration + '=' * (-len(declaration) % 4)))['origin']
            self.assertEqual(origin['implementation_id'], self.f.receipt['binary']['sha256'])
            self.assertEqual(headers['Authorization'], 'Bearer f03-synthetic-token')

    def test_installer_preserves_profile_and_emits_exact_checked_rows(self):
        selected = self.install()
        first = self.home.joinpath('config.yaml').read_bytes()
        self.install()
        self.assertEqual(self.home.joinpath('config.yaml').read_bytes(), first)
        self.assertEqual(self.home.joinpath('config.yaml.before-cairn').read_bytes(), self.original_config)
        self.assertEqual(json.loads(first)['unrelated'], {'keep': ['日本語', 7]})
        self.assertEqual(json.loads(first)['memory']['provider'], 'cairn')
        self.assertEqual(selected['engine_config'], str(self.f.memory_path))
        self.assertFalse(self.home.joinpath('cairn/engine.json').exists())
        self.assertEqual(set(selected['commands']), {'SessionStart', 'UserPromptSubmit', 'PreCompact',
                                                   'SessionEnd', 'PostToolUse', 'PostToolUseFailure'})
        for event, argv in selected['commands'].items():
            path = Path(argv[0])
            self.assertEqual(path.stat().st_mode & 0o777, 0o500)
            raw = path.read_bytes()
            self.assertEqual(path.parent.name, hashlib.sha256(raw).hexdigest())
            vector = shlex.split(raw.decode().splitlines()[1])
            self.assertEqual(vector[:3], ['exec', str(self.f.binary),
                                         'memory-capture' if event in ('PreCompact', 'SessionEnd') else 'memory'])
            self.assertEqual(vector[vector.index('--hook-timeout') + 1],
                             '150' if event in ('PreCompact', 'SessionEnd') else '13')
            self.assertEqual(vector[vector.index('--receipt-sha256') + 1], self.f.receipt_sha)
        self.assertEqual(self.calls, [])

    def test_effectful_recall_capture_and_dedup_after_installer_inputs_removed(self):
        self.install()
        shutil.rmtree(self.f.package)
        for event in ('SessionStart', 'UserPromptSubmit'):
            result = self.run_event(event)
            self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
            self.assertTrue(any(op == 'index' for op, _, _ in self.calls))
            request = next(body for op, body, _ in self.calls if op == 'index')
            self.assertEqual(request['scope']['task_id'], 'hermes/' + self.session)
            self.assertEqual(request['scope']['run_id'], self.session)
        for event in ('PreCompact', 'SessionEnd'):
            self.selection['memories'] = [self.note(title=event + ' policy', body=event + ': retain thirty days.')]
            self.publish_selector()
            dialogue = [dict(role='user', text=event + ': retain thirty days.'), dict(role='assistant', text='Agreed.')]
            before = self.write_count
            result = self.run_event(event, messages=dialogue)
            self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
            self.assertEqual(self.write_count, before + 1)
            self.assertEqual(json.loads(result.stdout)['cairn_status']['last_capture']['outcome'], 'saved')
            duplicate = self.run_event(event, messages=dialogue)
            self.assertEqual((duplicate.returncode, duplicate.stderr), (0, b''))
            self.assertEqual(self.write_count, before + 1)
            self.assertEqual(json.loads(duplicate.stdout)['cairn_status']['last_capture']['outcome'], 'unchanged')
        self.assert_go_attribution()
        state = Path(self.f.memory['state_dir']) / (self.session_key() + '.json')
        self.assertTrue(state.exists())
        self.assertEqual([p.name for p in state.parent.glob('*.json')], [state.name])

    def test_missing_changed_selection_refuses_before_api_and_state(self):
        self.install()
        for name, path in (('receipt', self.f.receipt_path), ('profile', self.f.memory_path),
                           ('binary', self.f.binary), ('helper', Path(self.f.receipt['snapshot_root']) / 'recall_dispatch.py')):
            raw, mode = path.read_bytes(), path.stat().st_mode & 0o777
            for missing in (False, True):
                with self.subTest(name=name, missing=missing):
                    self.calls.clear()
                    parked = path.with_name(path.name + '.r03-parked')
                    try:
                        if missing:
                            path.rename(parked)
                        else:
                            path.chmod(0o600)
                            path.write_bytes(raw + b'\n')
                            path.chmod(mode)
                        result = self.run_event('UserPromptSubmit')
                        self.assertEqual(result.returncode, 127 if name == 'binary' and missing else 1)
                        self.assertEqual(result.stdout, b'')
                        self.assertEqual(self.calls, [])
                        self.assertFalse(Path(self.f.memory['state_dir']).exists())
                    finally:
                        if missing:
                            parked.rename(path)
                        else:
                            path.chmod(0o600)
                            path.write_bytes(raw)
                            path.chmod(mode)

    def test_checked_tool_gap_is_an_explicit_refusal_without_fallback(self):
        self.install()
        for event in ('PostToolUse', 'PostToolUseFailure'):
            result = self.run_event(event, tool_name='Read', tool_input={'file_path': str(self.f.root / 'policy.txt')})
            self.assertEqual((result.returncode, result.stdout), (1, b''))
            self.assertIn(b'CompatibilityPreflightError', result.stderr)
        self.assertEqual(self.calls, [])
        self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_explicit_reverse_to_retained_python_preserves_go_state_and_dedup(self):
        installer.install(self.home, self.native, str(self.root / 'selector'), self.skill,
                          model='fixture-model', recall_mode='agent_tools')
        legacy = json.loads(self.home.joinpath('cairn-lifecycle.json').read_bytes())
        engine_path = Path(legacy['engine_config'])
        engine = json.loads(engine_path.read_bytes())
        engine.update(state_dir=self.f.memory['state_dir'], semantic_fallback=False, capture_observations=True)
        installer.shared.write_json(engine_path, engine)
        retained = {path: path.read_bytes() for path in
                    (self.home / 'cairn-lifecycle.json', self.home / 'cairn/installed.json', engine_path)}
        script_hash = sha(legacy['script'])
        self.install()
        result = self.run_event('SessionEnd', messages=[dict(role='user', text='Retain thirty days.'),
                                                      dict(role='assistant', text='Agreed.')])
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        self.assertEqual(self.write_count, 1)
        state_path = Path(self.f.memory['state_dir']) / (self.session_key() + '.json')
        state = json.loads(state_path.read_bytes())
        state['opaque_reverse_canary'] = ['日本語', 900719925474099312345]
        state_path.write_text(json.dumps(state))
        self.assertEqual(engine_path.read_bytes(), retained[engine_path])
        self.assertEqual(sha(legacy['script']), script_hash)
        # This is an explicit fixture reversal, never an error fallback. The
        # old selected profile/manifest and its existing state are retained.
        for path, raw in retained.items():
            pending = path.with_suffix(path.suffix + '.restore')
            pending.write_bytes(raw)
            pending.chmod(0o600)
            pending.replace(path)
        argv = [sys.executable, legacy['script'], '--config', legacy['engine_config']]
        for event, fields in (('UserPromptSubmit', {}), ('SessionEnd', dict(messages=[
                dict(role='user', text='Retain thirty days.'), dict(role='assistant', text='Agreed.')]))):
            raw = json.dumps(dict(self.f.event, hook_event_name=event, **fields)).encode()
            observed = subprocess.run(argv, input=raw, cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=155)
            self.results.append(dict(argv=argv, input=raw.decode(), exit=observed.returncode,
                                     stdout=observed.stdout.decode(), stderr=observed.stderr.decode(), explicit_reverse=True))
            self.assertEqual((observed.returncode, observed.stderr), (0, b''), observed.stdout)
        self.assertEqual(self.write_count, 1, 'reversal duplicated the completed capture')
        after = json.loads(state_path.read_bytes())
        self.assertEqual(after['opaque_reverse_canary'], state['opaque_reverse_canary'])
        self.assertEqual(after['captured_digest'], state['captured_digest'])
        self.assertEqual(after['last_capture']['outcome'], 'unchanged')
        self.assertTrue(any(op == 'index' for op, _, _ in self.calls))

    def test_actual_cli_fresh_initialization_reloads_selection_and_reverses(self):
        installer.install(self.home, self.native, str(self.root / 'selector'), self.skill,
                          model='fixture-model', recall_mode='agent_tools')
        legacy = self.home.joinpath('cairn-lifecycle.json').read_bytes()
        engine_path = self.home / 'cairn/engine.json'
        engine = json.loads(engine_path.read_bytes())
        engine.update(state_dir=self.f.memory['state_dir'], semantic_fallback=False)
        installer.shared.write_json(engine_path, engine)
        config = json.loads(self.home.joinpath('config.yaml').read_bytes())
        config.update(fallback_model=[], fallback_providers=[], security=dict(tirith_enabled=False))
        config['display'] = dict(tool_progress='off', persistent_output=False)
        installer.shared.write_json(self.home / 'config.yaml', config)
        legacy_manifest = self.home.joinpath('cairn/installed.json').read_bytes()
        legacy_settings = self.home.joinpath('config.yaml').read_bytes()
        first = self.install()
        first_raw = self.home.joinpath('cairn-lifecycle.json').read_bytes()
        self.f.memory['r03_revision_canary'] = 'second selection'
        self.f.publish_configuration(enabled=False)
        second = self.install()
        self.assertNotEqual(first['commands'], second['commands'])
        replacement = self.root / 'second-active.json'
        replacement.write_bytes(self.home.joinpath('cairn-lifecycle.json').read_bytes())
        self.home.joinpath('cairn-lifecycle.json').write_bytes(first_raw)
        self.home.joinpath('r03-fixture.json').write_text(json.dumps(dict(
            schema='cairn.r03-hermes-fixture/1', root=str(self.root), project=str(self.f.root), session=self.session)))

        def cli_owner(name, expected, replacement=None):
            command = [str(HERMES_PYTHON), '-I', '-B', str(ROOT / 'scripts/check_r03_hermes_cli.py'),
                       '--hermes-root', str(HERMES_ROOT), '--home', str(self.home), '--output', str(self.root / name),
                       '--expected-sha256', hashlib.sha256(expected).hexdigest()]
            if replacement:
                command += ['--replacement-config', str(replacement)]
            result = subprocess.run(command, cwd=self.f.root, env=dict(self.f.env(), HERMES_HOME=str(self.home),
                TERMINAL_CWD=str(self.f.root), TIRITH_ENABLED='false'), capture_output=True, timeout=90)
            self.results.append(dict(argv=command, exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode()))
            self.assertEqual(result.returncode, 0, result.stderr.decode() + result.stdout.decode())
            return json.loads((self.root / name / 'result.json').read_bytes())

        old = cli_owner('cli-cached', first_raw, replacement)
        fresh = cli_owner('cli-refreshed', replacement.read_bytes())
        self.assertEqual(old['calls'][0]['argv'], first['commands']['UserPromptSubmit'])
        self.assertEqual(fresh['calls'][0]['argv'], second['commands']['UserPromptSubmit'])
        self.home.joinpath('cairn-lifecycle.json').write_bytes(legacy)
        self.home.joinpath('cairn/installed.json').write_bytes(legacy_manifest)
        self.home.joinpath('config.yaml').write_bytes(legacy_settings)
        reverse = cli_owner('cli-reverse', legacy)
        self.assertEqual(reverse['calls'][0]['argv'][1], json.loads(legacy)['script'])
        self.assertNotEqual(old['session'], fresh['session'])
        self.assertNotEqual(fresh['session'], reverse['session'])
        self.assertEqual(self.write_count, 0)

    def native_objects(self, name, refusal=False):
        self.home.joinpath('r03-fixture.json').write_text(json.dumps(dict(
            schema='cairn.r03-hermes-fixture/1', root=str(self.root), project=str(self.f.root), session=self.session)))
        command = [str(HERMES_PYTHON), '-I', '-B', str(ROOT / 'scripts/check_r03_hermes_objects.py'),
                   '--hermes-root', str(HERMES_ROOT), '--home', str(self.home), '--output', str(self.root / name)]
        if refusal:
            command.append('--refusal')
        result = subprocess.run(command, cwd=self.f.root, env=dict(self.f.env(), HERMES_HOME=str(self.home),
                                TERMINAL_CWD=str(self.f.root)), capture_output=True, timeout=180)
        self.results.append(dict(argv=command, exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode()))
        self.assertEqual(result.returncode, 0, result.stderr.decode() + result.stdout.decode())
        self.assertNotIn(b'PRIVATE_FIXTURE', result.stdout + result.stderr)

    def test_actual_native_provider_hooks_middleware_and_disposal(self):
        self.install()
        self.records.append(dict(record_id='66666666-6666-4666-8666-666666666666', version=1,
                                 kind='decision', **{'class': 'A'}, body='Retain policy for thirty days.'))
        shutil.rmtree(self.f.package)
        self.native_objects('native-cycle')
        self.assertGreaterEqual(self.write_count, 1)
        self.assert_go_attribution()

    def test_native_missing_and_tampered_image_keep_pending_retry_private(self):
        self.install()
        image = self.f.binary
        raw, mode = image.read_bytes(), image.stat().st_mode & 0o777
        for kind in ('missing', 'changed'):
            parked = image.with_name('r03-parked-image')
            try:
                if kind == 'missing':
                    image.rename(parked)
                else:
                    image.chmod(0o600)
                    image.write_bytes(raw + b'changed')
                    image.chmod(mode)
                self.native_objects('native-' + kind, refusal=True)
                self.assertEqual(self.calls, [])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())
            finally:
                if kind == 'missing':
                    parked.rename(image)
                else:
                    image.chmod(0o600)
                    image.write_bytes(raw)
                    image.chmod(mode)

    def test_actual_go_capture_interruption_joins_owned_selector_tree(self):
        selector = self.root / 'blocking-selector'
        selector.write_text('#!' + str(HERMES_PYTHON) + '\n' + '''import json,os,pathlib,subprocess,sys,time
sys.stdin.read()
child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'])
path=pathlib.Path(__file__).parent/'blocking-owned.json';pending=path.with_suffix('.pending')
pending.write_text(json.dumps(dict(engine=os.getppid(),selector=os.getpid(),child=child.pid,group=os.getpgrp())))
pending.replace(path)
time.sleep(60)
''')
        selector.chmod(0o500)
        self.f.memory['claude'] = str(selector)
        self.f.publish_configuration(enabled=False)
        self.install()
        self.home.joinpath('r03-fixture.json').write_text(json.dumps(dict(
            schema='cairn.r03-hermes-fixture/1', root=str(self.root), project=str(self.f.root), session=self.session)))
        command = [str(HERMES_PYTHON), '-I', '-B', str(ROOT / 'scripts/check_r03_hermes_objects.py'),
                   '--hermes-root', str(HERMES_ROOT), '--home', str(self.home),
                   '--output', str(self.root / 'native-interrupted'), '--interrupt-capture']
        unrelated = subprocess.Popen([str(HERMES_PYTHON), '-I', '-c', 'import time; time.sleep(60)'])
        owner = subprocess.Popen(command, cwd=self.f.root, env=dict(self.f.env(), HERMES_HOME=str(self.home),
            TERMINAL_CWD=str(self.f.root)), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertGreater(owner.pid, 1)
            self.assertGreater(unrelated.pid, 1)
            path = self.root / 'blocking-owned.json'
            deadline = time.monotonic() + 8
            while not path.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(path.exists(), 'actual Go selector did not reach controlled boundary')
            owned = json.loads(path.read_bytes())
            self.assertTrue(all(type(pid) is int and pid > 1 for pid in owned.values()))
            self.assertEqual(owned['group'], owned['engine'])
            self.assertEqual(Path('/proc', str(owned['engine']), 'exe').resolve(), self.f.binary.resolve())
            for pid in (owner.pid, unrelated.pid, owned['selector'], owned['child']):
                self.assertEqual(Path('/proc', str(pid), 'exe').resolve(), HERMES_PYTHON.resolve())
            owner.send_signal(signal.SIGINT)
            stdout, stderr = owner.communicate(timeout=8)
            self.results.append(dict(argv=command, exit=owner.returncode, stdout=stdout.decode(),
                stderr=stderr.decode(), owned=owned, unrelated_pid=unrelated.pid))
            self.assertEqual(owner.returncode, 130, stderr.decode())
            self.assertIsNone(unrelated.poll(), 'another invocation was interrupted')
            deadline = time.monotonic() + 3
            for pid in (owned['engine'], owned['selector'], owned['child']):
                while True:
                    try:
                        state = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[0]
                    except (FileNotFoundError, ProcessLookupError):
                        break
                    if state == 'Z':
                        break
                    self.assertLess(time.monotonic(), deadline, 'owned selector process remained running')
                    time.sleep(0.02)
            self.assertEqual(self.write_count, 0)
            self.assertNotIn('PRIVATE_FIXTURE', stdout.decode() + stderr.decode())
        finally:
            if owner.poll() is None:
                owner.send_signal(signal.SIGINT)
                owner.communicate(timeout=8)
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=5)


def main():
    global OUTPUT, HERMES_ROOT, HERMES_PYTHON, PREBUILT_IMAGES, SOURCE_REVISION, EMIT_CASES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    parser.add_argument('--images', type=Path, help='explicit frozen image directory with pins.json; no build fallback')
    parser.add_argument('--source-revision', help='source provenance when the selected checkout is not Git-readable')
    parser.add_argument('--emit-cases', action='store_true', help='emit raw process/API/state evidence for an isolated run')
    parser.add_argument('tests', nargs='*', help='optional unittest names within this module')
    args = parser.parse_args()
    OUTPUT, HERMES_ROOT, HERMES_PYTHON = args.output.resolve(), args.hermes_root.resolve(), args.hermes_python.absolute()
    PREBUILT_IMAGES = args.images.resolve() if args.images else None
    SOURCE_REVISION = args.source_revision or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    EMIT_CASES = args.emit_cases
    OUTPUT.mkdir(mode=0o700)
    if not (HERMES_ROOT / 'agent/memory_provider.py').is_file() or not HERMES_PYTHON.is_file():
        raise RuntimeError('R03_HERMES_PREREQUISITE: explicit existing Hermes source/interpreter required')
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests or ['InstalledMemory'], sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    OUTPUT.joinpath('summary.json').write_text(json.dumps(dict(tests=result.testsRun, failures=len(result.failures),
        errors=len(result.errors), skips=len(result.skipped), passed=result.wasSuccessful()), indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
