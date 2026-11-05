"""The coordinated Codex launcher serves an app-server and a --remote TUI."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from unittest import mock
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def companion(root):
    path = root / 'codex-code-mode-host'
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o700)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class OwnerOriginManifest(unittest.TestCase):
    def test_selected_runtime_requires_matching_bytes_and_capability(self):
        spec = importlib.util.spec_from_file_location('owner_launcher', ROOT / 'scripts/launch-codex-coordination.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'codex'
            capability = dict(schema='codex.owner-input-capabilities/1',
                              contract='codex.owner-input/1', version=1, attestation_contract='cairn.owner-input-attestation/1',
                              upstream_commit='6ff1e1ca3b30fc8b914720681fcf5fe62e95f856',
                              patch_sha256='a' * 64)
            binary.write_text('#!/usr/bin/python3\nimport json\nprint(' + repr(json.dumps(capability)) + ')\n')
            binary.chmod(0o700)
            manifest = root / 'manifest.json'
            selected = dict(schema='cairn.codex-owner-origin/1', code_mode_host=companion(root),
                            binary_path=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                            **{key: capability[key] for key in ('contract', 'version', 'upstream_commit', 'patch_sha256', 'attestation_contract')})
            manifest.write_text(json.dumps(selected))
            manifest.chmod(0o600)
            with launcher.owner_origin_runtime(manifest, binary) as (actual, executable):
                self.assertEqual(actual, selected)
                self.assertTrue(Path(executable).samefile(binary))
            helper = root / 'codex-code-mode-host'
            original = helper.read_bytes()
            for defect in ('missing', 'moved', 'hash', 'writable'):
                with self.subTest(companion=defect):
                    helper.write_bytes(original)
                    helper.chmod(0o700)
                    changed = dict(selected, code_mode_host=dict(selected['code_mode_host']))
                    if defect == 'missing': helper.unlink()
                    elif defect == 'moved': changed['code_mode_host']['path'] = str(root/'elsewhere')
                    elif defect == 'hash': helper.write_bytes(original+b'# changed\n')
                    else: helper.chmod(0o722)
                    manifest.write_text(json.dumps(changed))
                    with self.assertRaises((ValueError, OSError)):
                        with launcher.owner_origin_runtime(manifest, binary):
                            self.fail('invalid native companion was accepted')
            helper.write_bytes(original)
            helper.chmod(0o700)
            manifest.write_text(json.dumps(selected))
            binary.write_text(binary.read_text() + '# changed\n')
            with self.assertRaisesRegex(ValueError, 'binary.*changed'):
                with launcher.owner_origin_runtime(manifest, binary):
                    self.fail('changed binary was accepted')

    def test_capability_probe_bounds_output_time_and_reaps_failures(self):
        spec = importlib.util.spec_from_file_location('bounded_owner_launcher', ROOT / 'scripts/launch-codex-coordination.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'probe'
            pid_file = root / 'pid'
            for body, expected in [("print('x'*100000, flush=True)", 'output bound'),
                                   ("print('diagnostic', file=sys.stderr, flush=True)", 'diagnostics'),
                                   ("pass", 'timed out')]:
                with self.subTest(expected=expected):
                    binary.write_text('#!/usr/bin/python3\nimport os,sys,time\n'
                                      + f'open({str(pid_file)!r},"w").write(str(os.getpid()))\n'
                                      + body + '\ntime.sleep(60)\n')
                    binary.chmod(0o700)
                    with self.assertRaisesRegex(ValueError, expected):
                        launcher.owner_origin_probe(binary, timeout=0.3)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid_file.read_text()), 0)


CODEX = r'''#!/usr/bin/env python3
import hashlib, json, os, pathlib, socket, sys, time
args = sys.argv[1:]
if args == ['--cairn-owner-origin-capabilities']:
    print(json.dumps(dict(schema='codex.owner-input-capabilities/1', contract='codex.owner-input/1',
                         version=1, attestation_contract='cairn.owner-input-attestation/1', upstream_commit='6ff1e1ca3b30fc8b914720681fcf5fe62e95f856', patch_sha256='a'*64)))
    raise SystemExit(0)
if '--cairn-owner-origin-fd' in args:
    index = args.index('--cairn-owner-origin-fd')
    fd = int(args[index+1])
    with socket.socket(fileno=fd) as channel:
        channel.settimeout(1)
        frame = b''
        while True:
            chunk = channel.recv(1024)
            if not chunk: break
            frame += chunk
    try: os.fstat(fd)
    except OSError: pass
    else: raise AssertionError('proof descriptor survived startup')
    magic = b'CAIRN-OWNER-INPUT/1\n'
    assert frame.startswith(magic)
    secret = frame[len(magic):len(magic)+32]
    endpoint = frame[len(magic)+33:]
    assert frame[len(magic)+32] == len(endpoint)
    assert ('unix://' + os.fsdecode(endpoint)) in args
    with pathlib.Path(os.environ['LAUNCHER_PROOF_LOG']).open('a') as receipt:
        receipt.write(json.dumps(dict(proof_digest=hashlib.sha256(secret).hexdigest(), endpoint=os.fsdecode(endpoint),
                                     metadata={key: value for key, value in os.environ.items() if key.startswith('CAIRN_CODEX_OWNER_ORIGIN_')}))+'\n')
    del frame, secret
    del args[index:index+2]
if '--cairn-owner-attestation-fd' in args:
    index = args.index('--cairn-owner-attestation-fd')
    fd = int(args[index+1])
    with socket.socket(fileno=fd) as channel:
        channel.settimeout(2)
        size = len(b'CAIRN-OWNER-ATTESTATION/1\n') + 16 + 32
        frame = b''
        while len(frame) < size:
            frame += channel.recv(size - len(frame))
        assert frame.startswith(b'CAIRN-OWNER-ATTESTATION/1\n')
        channel.sendall(b'k' * 32)
        generation = b''
        while len(generation) < 8:
            part = channel.recv(8-len(generation))
            if not part: raise SystemExit(125)
            generation += part
        assert int.from_bytes(generation, 'big') == 7
        assert channel.recv(1) == b''
    del frame
    del args[index:index+2]
if os.environ.get('LAUNCHER_EXPECT_OPERATOR_SCRUB'):
    assert 'CAIRN_DATABASE_URL' not in os.environ
    assert 'PGPASSWORD' not in os.environ
    assert 'CAIRN_HOME' not in os.environ
    assert not any(key.startswith('CAIRN_OPERATOR_') for key in os.environ)
    if os.environ.get('LAUNCHER_EXPECT_HOME'):
        assert os.environ['HOME'] == os.environ['LAUNCHER_EXPECT_HOME']
if os.environ.get('LAUNCHER_EXPECT_UNSUPPORTED'):
    assert not any(key.startswith('CAIRN_CODEX_OWNER_ORIGIN_') for key in os.environ)
args = list(args)
log = pathlib.Path(os.environ['LAUNCHER_LOG']).open('a')
log.write(json.dumps(args) + '\n'); log.flush()
if args[:2] == ['app-server', '--listen']:
    path = args[2][7:]
    if os.environ.get('LAUNCHER_SERVER_PID'):
        pathlib.Path(os.environ['LAUNCHER_SERVER_PID']).write_text(str(os.getpid()))
    if os.environ.get('LAUNCHER_SERVER_DELAY'):
        time.sleep(float(os.environ['LAUNCHER_SERVER_DELAY']))  # startup window, before binding
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)
    if os.environ.get('LAUNCHER_SERVER_EXIT'):
        raise SystemExit(int(os.environ['LAUNCHER_SERVER_EXIT']))
    time.sleep(60)
if args[0] == '--remote':
    assert args[1].startswith('unix://'), args
    head = args[:args.index('--')] if '--' in args else args
    if '--no-daemon' in head:  # Codex 0.156 refuses the combination.
        sys.stderr.write('ERROR: --no-daemon cannot be used with --remote.\n')
        raise SystemExit(2)
    if os.environ.get('LAUNCHER_TUI_PID'):
        pathlib.Path(os.environ['LAUNCHER_TUI_PID']).write_text(str(os.getpid()))
    time.sleep(float(os.environ.get('LAUNCHER_TUI_SECONDS', '0.2')))
    raise SystemExit(int(os.environ.get('LAUNCHER_TUI_EXIT', '0')))
raise SystemExit(int(os.environ.get('LAUNCHER_DIRECT_EXIT', '0')))  # non-interactive passthrough
'''


class CodexLauncher(unittest.TestCase):
    def host_config(self, work, reject=False):
        helper = work / 'cairn-operator'
        helper.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                          + 'assert sys.argv[1:] == ["native-owner-launch-register"]\n'
                          + 'request=json.load(sys.stdin)\n'
                          + 'assert len(request["public_key"]) == 43\n'
                          + 'assert request["binding"] == "fixture"\n'
                          + 'if os.environ.get("LAUNCHER_EXPECT_OPERATOR_SCRUB"):\n'
                          + '    assert os.environ.get("CAIRN_DATABASE_URL") == "operator-test-only"\n'
                          + '    assert os.environ.get("PGPASSWORD") == "operator-test-only"\n'
                          + 'print(json.dumps(dict(schema="cairn.response/1",ok=True,status="OK",data=dict(launch_id=request["launch_id"],restore_generation=7,status='
                          + repr('refused' if reject else 'active') + '))))\n')
        helper.chmod(0o700)
        config = work / 'host.json'
        config.write_text(json.dumps(dict(schema='cairn.codex-owner-origin-host/1',
            cairn_path=str(helper), repo=str(work), profile_principal='fixture-profile', binding='fixture')))
        config.chmod(0o600)
        return config

    def launch(self, forwarded, tui_exit='0'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)
        shim = work / 'codex'
        shim.write_text(CODEX)
        shim.chmod(0o700)
        runtime = work / 'runtime'
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                   LAUNCHER_LOG=str(work / 'log.jsonl'),
                   LAUNCHER_SERVER_PID=str(work / 'server.pid'),
                   LAUNCHER_TUI_EXIT=tui_exit)
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
            '--codex', str(shim), '--', *forwarded], env=env, capture_output=True, text=True, timeout=30)
        entries = [json.loads(line) for line in (work / 'log.jsonl').read_text().splitlines()]
        sockets = list(runtime.glob('*.sock')) if runtime.exists() else []
        return result, entries, sockets, work

    def test_verified_owner_runtime_uses_private_fds_and_preserves_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = work / 'codex'
            binary.write_text(CODEX)
            binary.chmod(0o700)
            manifest = work / 'manifest.json'
            selected = dict(schema='cairn.codex-owner-origin/1', code_mode_host=companion(work), binary_path=str(binary),
                            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                            contract='codex.owner-input/1', version=1, attestation_contract='cairn.owner-input-attestation/1',
                            upstream_commit='6ff1e1ca3b30fc8b914720681fcf5fe62e95f856', patch_sha256='a'*64)
            manifest.write_text(json.dumps(selected))
            manifest.chmod(0o600)
            env = dict(os.environ, XDG_RUNTIME_DIR=str(work / 'run'), LAUNCHER_LOG=str(work / 'argv'),
                       LAUNCHER_PROOF_LOG=str(work / 'proof'), LAUNCHER_SERVER_PID=str(work / 'server.pid'),
                       CAIRN_DATABASE_URL='operator-test-only', PGPASSWORD='operator-test-only', LAUNCHER_EXPECT_OPERATOR_SCRUB='1')
            command = [sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
                       '--codex', str(binary), '--owner-origin-manifest', str(manifest),
                       '--owner-origin-host-config', str(self.host_config(work)), '--', 'resume', '--last']
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipts = [json.loads(line) for line in (work / 'proof').read_text().splitlines()]
            self.assertEqual(len(receipts), 2)
            self.assertEqual(receipts[0], receipts[1])
            self.assertEqual(receipts[0]['metadata']['CAIRN_CODEX_OWNER_ORIGIN_RUNTIME_SHA256'], selected['binary_sha256'])
            self.assertEqual(list((work / 'run').glob('*.sock')), [])
            with self.assertRaises(ProcessLookupError):
                os.kill(int((work / 'server.pid').read_text()), 0)
            # An explicit bad manifest refuses before another server or TUI launches.
            (work / 'argv').unlink()
            selected['patch_sha256'] = 'b'*64
            manifest.write_text(json.dumps(selected))
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('capability does not match', result.stderr)
            self.assertFalse((work / 'argv').exists())
            selected['patch_sha256'] = 'a'*64
            manifest.write_text(json.dumps(selected))
            self.host_config(work, reject=True)
            before = len((work / 'proof').read_text().splitlines())
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 125)
            self.assertIn('operator refused', result.stderr)
            self.assertEqual(len((work / 'proof').read_text().splitlines()), before + 1)
            self.assertFalse((work / 'argv').exists())

    def test_verified_inode_survives_path_swap_during_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = work / 'codex'
            replacement = work / 'replacement'
            attacked = work / 'unverified-ran'
            replacement.write_text('#!/usr/bin/python3\nfrom pathlib import Path\n'
                                   + f'Path({str(attacked)!r}).touch()\nraise SystemExit(91)\n')
            replacement.chmod(0o700)
            # The checked probe atomically replaces its pathname before launch.
            source = CODEX.replace("if args == ['--cairn-owner-origin-capabilities']:",
                "if args == ['--cairn-owner-origin-capabilities']:\n    os.replace("
                + repr(str(replacement)) + ", " + repr(str(binary)) + ")")
            binary.write_text(source)
            binary.chmod(0o700)
            manifest = work / 'manifest.json'
            manifest.write_text(json.dumps(dict(schema='cairn.codex-owner-origin/1', code_mode_host=companion(work),
                binary_path=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                contract='codex.owner-input/1', version=1, attestation_contract='cairn.owner-input-attestation/1',
                upstream_commit='6ff1e1ca3b30fc8b914720681fcf5fe62e95f856', patch_sha256='a'*64)))
            manifest.chmod(0o600)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
                '--codex', str(binary), '--owner-origin-manifest', str(manifest),
                '--owner-origin-host-config', str(self.host_config(work)), '--'],
                env=dict(os.environ, XDG_RUNTIME_DIR=str(work / 'run'), LAUNCHER_LOG=str(work / 'argv'),
                         LAUNCHER_PROOF_LOG=str(work / 'proof')),
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(attacked.exists())
            self.assertEqual(len((work / 'proof').read_text().splitlines()), 2)

    def test_selected_direct_invocation_scrubs_operator_environment_and_requires_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = work / 'codex'
            binary.write_text(CODEX)
            binary.chmod(0o700)
            command = [sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
                       '--codex', str(binary), '--owner-origin-manifest', str(work / 'manifest.json')]
            env = dict(os.environ, LAUNCHER_LOG=str(work / 'argv'),
                       LAUNCHER_EXPECT_OPERATOR_SCRUB='1', LAUNCHER_EXPECT_HOME=os.environ['HOME'],
                       CAIRN_DATABASE_URL='operator-test-only', CAIRN_HOME='operator-test-only',
                       PGPASSWORD='operator-test-only', CAIRN_OPERATOR_SECRET='operator-test-only')
            missing = subprocess.run([*command, '--', 'exec', 'ordinary task'], env=env,
                                     capture_output=True, text=True, timeout=5)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('selected together', missing.stderr)
            self.assertFalse((work / 'argv').exists())
            for forwarded in [['exec', 'ordinary task'], ['--remote', 'unix:///unowned', 'ordinary task']]:
                result = subprocess.run([*command, '--owner-origin-host-config', str(self.host_config(work)), '--', *forwarded],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_capability_query_is_direct_without_spawning_session_children(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = work / 'codex'
            binary.write_text(CODEX)
            binary.chmod(0o700)
            runtime = work / 'run'
            log = work / 'log'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
                                     '--codex', str(binary), '--', '--cairn-owner-origin-capabilities'],
                                    env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime), LAUNCHER_LOG=str(log)),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['contract'], 'codex.owner-input/1')
            self.assertFalse(runtime.exists())
            self.assertFalse(log.exists())

    def test_no_manifest_clears_inherited_capability_metadata(self):
        with mock.patch.dict(os.environ, CAIRN_CODEX_OWNER_ORIGIN_CONTRACT='forged', LAUNCHER_EXPECT_UNSUPPORTED='1'):
            result, entries, sockets, _ = self.launch([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(entries), 2)
        self.assertEqual(sockets, [])

    def test_launches_remote_tui_on_own_app_server_and_cleans_up(self):
        result, entries, sockets, work = self.launch(['resume', '--last'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(entries), 2)
        server, tui = entries
        self.assertEqual(server[:2], ['app-server', '--listen'])
        self.assertTrue(server[2].startswith('unix://'))
        self.assertEqual(tui[:2], ['--remote', server[2]], 'TUI must share the served socket')
        self.assertEqual(tui[2:], ['resume', '--last'], 'TUI arguments must be forwarded unchanged')
        self.assertEqual(sockets, [], 'session socket survived launcher exit')
        pid = int((work / 'server.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)  # the app-server must not outlive the TUI

    def test_no_daemon_is_satisfied_by_the_private_app_server(self):
        # Codex 0.156 added --no-daemon ("run without the shared background
        # server") and refuses it alongside --remote. The launcher's private
        # app-server is not the shared daemon, so the flag is already honored:
        # it is dropped from the served TUI, while literal prompt text keeps it.
        for forwarded, expected in [
                (['--no-daemon'], []),
                (['--no-daemon', 'resume', '--last'], ['resume', '--last']),
                (['-m', 'gpt', '--no-daemon', '--', 'explain --no-daemon'], ['-m', 'gpt', '--', 'explain --no-daemon'])]:
            with self.subTest(forwarded=forwarded):
                result, entries, sockets, work = self.launch(forwarded)
                self.assertEqual(result.returncode, 0, result.stderr)
                server, tui = entries
                self.assertNotIn('--no-daemon', server)
                self.assertEqual(tui[:2], ['--remote', server[2]])
                self.assertEqual(tui[2:], expected)
                self.assertEqual(sockets, [])

    def test_tui_exit_status_is_propagated_with_cleanup(self):
        result, entries, sockets, work = self.launch([], tui_exit='3')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(entries), 2)
        self.assertEqual(sockets, [])

    def test_remote_tui_argv_selects_the_uid_verified_queue_endpoint(self):
        spec = importlib.util.spec_from_file_location('coordination_launcher', ROOT / 'integrations/lifecycle/coordination.py')
        coordination = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(coordination)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)',
                                  '--remote', 'unix:///absent-queue.sock'])
        self.addCleanup(lambda: (child.terminate(), child.wait(timeout=5)))
        time.sleep(0.2)
        reference = coordination.process_reference(child.pid)
        self.assertEqual(coordination.codex_queue_endpoint(dict(harness='codex'), reference),
                         dict(endpoint='/absent-queue.sock', owner='uid'))
        plain = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        self.addCleanup(lambda: (plain.terminate(), plain.wait(timeout=5)))
        time.sleep(0.2)
        self.assertIsNone(coordination.codex_queue_endpoint(
            dict(harness='codex'), coordination.process_reference(plain.pid)))


class CodexShim(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.real_dir = self.work / 'realbin'
        self.real_dir.mkdir()
        self.real = self.real_dir / 'codex'
        self.real.write_text(CODEX)
        self.real.chmod(0o700)
        spec = importlib.util.spec_from_file_location('codex_installer', ROOT / 'scripts/install-agent-coordination.py')
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

    def install(self, home, path_env):
        root = self.work / 'engine-root'
        root.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'scripts/launch-codex-coordination.py', root / 'launch-codex-coordination.py')
        with mock.patch.dict(os.environ, HOME=str(home), PATH=path_env):
            self.installer.install_codex_shim(root, str(self.real))
            return self.installer

    def test_shim_installs_idempotently_and_backs_up_foreign_wrappers(self):
        home = self.work / 'home'
        shim = home / '.local' / 'bin' / 'codex'
        shim.parent.mkdir(parents=True)
        shim.write_text('#!/bin/sh\nexec other-codex "$@"\n')
        self.install(home, str(self.real_dir))
        body = shim.read_text()
        self.assertIn(self.installer.SHIM_MARKER, body)
        self.assertIn(f'REAL="{self.real}"', body)
        self.assertIn('exec "$PYTHON" "$LAUNCHER" --codex "$REAL" -- "$@"', body)
        self.assertNotIn('case "$1"', body)
        self.assertEqual(shim.stat().st_mode & 0o777, 0o700)
        self.assertIn('other-codex', (shim.parent / 'codex.before-cairn-coordination').read_text())
        first = shim.read_text()
        self.install(home, str(self.real_dir))
        self.assertEqual(shim.read_text(), first)

    def test_existing_symlink_install_preserves_target_and_routes_interactively(self):
        # The real account layout: ~/.local/bin/codex symlinks to the npm
        # installation. Installing the shim must replace only the directory
        # entry and leave the native bytes untouched.
        home = self.work / 'home3'
        shim_dir = home / '.local' / 'bin'
        shim_dir.mkdir(parents=True)
        shim = shim_dir / 'codex'
        shim.symlink_to(self.real)
        native = self.real.read_bytes()
        log = self.work / 'symlink-log.jsonl'
        self.install(home, f"{shim_dir}:{self.real_dir}:{os.environ['PATH']}")
        self.assertEqual(self.real.read_bytes(), native, 'native codex bytes were modified')
        self.assertFalse(shim.is_symlink())
        body = shim.read_text()
        self.assertIn(self.installer.SHIM_MARKER, body)
        self.assertIn(f'REAL="{self.real}"', body)
        preserved = shim_dir / 'codex.before-cairn-coordination'
        self.assertTrue(preserved.is_symlink() and str(preserved.resolve()) == str(self.real),
                        'the foreign symlink was not preserved whole')
        env = dict(os.environ, HOME=str(home), PATH=f"{shim_dir}:{self.real_dir}:{os.environ['PATH']}",
                   LAUNCHER_LOG=str(log), LAUNCHER_SERVER_PID=str(self.work / 'server3.pid'),
                   LAUNCHER_TUI_EXIT='0', LAUNCHER_DIRECT_EXIT='7')
        result = subprocess.run([str(shim), 'resume', '--last'], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(entries[0][:2], ['app-server', '--listen'])
        self.assertEqual(entries[1][:2], ['--remote', entries[0][2]])
        self.assertEqual(entries[1][2:], ['resume', '--last'])
        self.assertEqual(list(shim_dir.glob('queue-*.sock')), [])

    def test_explicit_codex_argument_overrides_path_resolution(self):
        other_dir = self.work / 'otherbin'
        other_dir.mkdir()
        other = other_dir / 'codex'
        other.write_text(CODEX)
        other.chmod(0o700)
        home = self.work / 'home4'
        root = self.work / 'engine4'
        root.mkdir()
        shutil.copyfile(ROOT / 'scripts/launch-codex-coordination.py', root / 'launch-codex-coordination.py')
        with mock.patch.dict(os.environ, HOME=str(home), PATH=str(self.real_dir)):
            self.installer.install_codex_shim(root, str(other))
        body = (home / '.local' / 'bin' / 'codex').read_text()
        self.assertIn(f'REAL="{other}"', body)

    def test_shim_is_the_ordinary_route_for_interactive_launches_only(self):
        home = self.work / 'home2'
        shim_dir = home / '.local' / 'bin'
        log = self.work / 'shim-log.jsonl'
        self.install(home, f"{shim_dir}:{self.real_dir}")
        shim = shim_dir / 'codex'
        self.assertEqual((shim.stat().st_mode & 0o777), 0o700)
        env = dict(os.environ, HOME=str(home), PATH=f"{shim_dir}:{self.real_dir}:{os.environ['PATH']}",
                   LAUNCHER_LOG=str(log), LAUNCHER_SERVER_PID=str(self.work / 'server.pid'),
                   LAUNCHER_TUI_PID=str(self.work / 'tui.pid'), LAUNCHER_TUI_SECONDS='0.2',
                   LAUNCHER_TUI_EXIT='0', LAUNCHER_DIRECT_EXIT='7')
        direct_cases = [['exec', 'run', 'task'], ['--help'], ['-c', 'model=gpt-6', 'exec', 'x'],
                        ['--dangerously-bypass-approvals-and-sandbox', 'exec', 'y'],
                        ['--config=k=v', 'exec'], ['--enable', 'beta', '--disable', 'alpha', 'exec'],
                        ['-m', 'gpt-6', 'exec'], ['-i', 'a.png', '-m', 'gpt-6', 'exec'],
                        ['agents'], ['resume', '--remote', 'unix:///explicit.sock'],
                        # The equals form of an explicit endpoint; a bare
                        # --remote argv is indistinguishable from a served
                        # TUI to the fake, so the command-qualified cases
                        # above carry the explicit-endpoint proof.
                        ['--remote=unix:///explicit.sock', 'resume'], ['-V']]
        serve_cases = [[], ['resume', '--last'], ['fork', '--last'], ['do the thing'],
                       ['--', 'exec', 'the', 'plan'], ['--search', '--no-alt-screen']]
        for forwarded in direct_cases:
            with self.subTest(direct=forwarded):
                result = subprocess.run([str(shim), *forwarded], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 7, result.stderr)
        for forwarded in serve_cases:
            with self.subTest(serve=forwarded):
                result = subprocess.run([str(shim), *forwarded], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        # Every direct case execs the real codex with the user argv unchanged.
        self.assertEqual([e for e in entries if e and e[0] != 'app-server' and e[0] != '--remote'],
                         direct_cases)
        served = [e for e in entries if e and e[0] in ('app-server', '--remote')]
        self.assertEqual(len(served), 2 * len(serve_cases))
        self.assertEqual(list((home / '.local' / 'bin').glob('queue-*.sock')), [])


class LauncherLifetime(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.real = self.work / 'codex'
        self.real.write_text(CODEX)
        self.real.chmod(0o700)
        self.runtime = self.work / 'runtime'
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime), PATH=os.environ['PATH'],
                        LAUNCHER_LOG=str(self.work / 'log.jsonl'),
                        LAUNCHER_SERVER_PID=str(self.work / 'server.pid'),
                        LAUNCHER_TUI_PID=str(self.work / 'tui.pid'))

    def launch(self, extra):
        return subprocess.Popen([sys.executable, str(ROOT / 'scripts/launch-codex-coordination.py'),
            '--codex', str(self.real), *extra], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def pid_of(self, name):
        return int((self.work / name).read_text())

    def assert_dead(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_sigterm_cleans_up_owned_children_and_socket(self):
        self.env.update(LAUNCHER_TUI_SECONDS='25')
        launcher = self.launch(['resume', '--last'])
        deadline = time.time() + 10
        while not (self.work / 'tui.pid').exists() and time.time() < deadline:
            time.sleep(0.05)
        launcher.send_signal(signal.SIGTERM)
        self.assertEqual(launcher.wait(timeout=10), 128 + signal.SIGTERM)
        self.assert_dead(self.pid_of('server.pid'))
        self.assert_dead(self.pid_of('tui.pid'))
        self.assertEqual(list(self.runtime.glob('*.sock')), [])

    def test_sigterm_during_startup_still_cleans_up_the_app_server(self):
        # Termination inside the up-to-5-second startup wait must not orphan
        # the app-server: the handlers are armed before any child exists.
        self.env.update(LAUNCHER_SERVER_DELAY='8')
        launcher = self.launch([])
        deadline = time.time() + 10
        while not (self.work / 'server.pid').exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(list(self.runtime.glob('*.sock')), 'fixture bound early; delay not honored')
        launcher.send_signal(signal.SIGTERM)
        self.assertEqual(launcher.wait(timeout=10), 128 + signal.SIGTERM)
        self.assert_dead(self.pid_of('server.pid'))
        self.assertFalse((self.work / 'tui.pid').exists(), 'TUI was spawned during startup')
        self.assertEqual(list(self.runtime.glob('*.sock')), [])

    def test_server_death_under_tui_fails_loudly_and_recovers(self):
        self.env.update(LAUNCHER_TUI_SECONDS='25', LAUNCHER_SERVER_EXIT='9')
        launcher = self.launch([])
        _, stderr = launcher.communicate(timeout=15)
        self.assertNotEqual(launcher.returncode, 0)
        self.assertIn('app-server exited while the TUI was running', stderr)
        self.assert_dead(self.pid_of('server.pid'))
        self.assert_dead(self.pid_of('tui.pid'))
        self.assertEqual(list(self.runtime.glob('*.sock')), [])


class InstalledHelpBoundary(unittest.TestCase):
    def test_every_documented_global_option_is_classified(self):
        codex = shutil.which('codex')
        if codex is None:
            self.skipTest('installed codex unavailable')
        spec = importlib.util.spec_from_file_location('launcher_help', ROOT / 'scripts/launch-codex-coordination.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        help_text = subprocess.run([codex, '--help'], capture_output=True, text=True, timeout=15).stdout
        section = help_text.split('Options:', 1)[1].split('Arguments:')[0] if 'Options:' in help_text else ''
        documented = {token.split(',')[0].split('=')[0] for line in section.splitlines()
                      if line.startswith('  ') and line.strip().startswith('-')
                      for token in [line.strip().split()[0]]}
        documented = {name for name in documented if name.startswith('-') and len(name) > 1}
        unknown = {name for name in documented
                   if name not in launcher.VALUE_OPTIONS and name not in launcher.FLAG_OPTIONS}
        self.assertEqual(unknown, set(), f'unclassified global options: {unknown}')


class PathDiscoveredSymlink(unittest.TestCase):
    def test_path_discovery_without_explicit_codex_preserves_target(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)
        real_dir = work / 'realbin'
        real_dir.mkdir()
        real = real_dir / 'codex'
        real.write_text(CODEX)
        real.chmod(0o700)
        native = real.read_bytes()
        home = work / 'home'
        shim_dir = home / '.local' / 'bin'
        shim_dir.mkdir(parents=True)
        shim_dir.joinpath('codex').symlink_to(real)
        root = work / 'engine'
        root.mkdir()
        shutil.copyfile(ROOT / 'scripts/launch-codex-coordination.py', root / 'launch-codex-coordination.py')
        spec = importlib.util.spec_from_file_location('installer_path', ROOT / 'scripts/install-agent-coordination.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with mock.patch.dict(os.environ, HOME=str(home), PATH=f"{shim_dir}:{real_dir}:{os.environ['PATH']}"):
            installer.install_codex_shim(root)          # No explicit codex: PATH discovery.
            installer.install_codex_shim(root)          # Reinstall over the managed shim.
        shim = shim_dir / 'codex'
        self.assertEqual(real.read_bytes(), native, 'native codex bytes were modified')
        self.assertFalse(shim.is_symlink())
        self.assertIn(f'REAL="{real}"', shim.read_text())
        backups = list(shim_dir.glob('codex.before-cairn-coordination'))
        self.assertEqual(len(backups), 1, 'reinstall churned the preserved foreign entry')
        self.assertTrue(backups[0].is_symlink() and str(backups[0].resolve()) == str(real))


if __name__ == '__main__':
    unittest.main()
