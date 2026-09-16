"""The coordinated Codex launcher serves an app-server and a --remote TUI."""
import importlib.util
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

CODEX = r'''#!/usr/bin/env python3
import json, os, pathlib, socket, sys, time
args = sys.argv[1:]
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
    if os.environ.get('LAUNCHER_TUI_PID'):
        pathlib.Path(os.environ['LAUNCHER_TUI_PID']).write_text(str(os.getpid()))
    time.sleep(float(os.environ.get('LAUNCHER_TUI_SECONDS', '0.2')))
    raise SystemExit(int(os.environ.get('LAUNCHER_TUI_EXIT', '0')))
raise SystemExit(int(os.environ.get('LAUNCHER_DIRECT_EXIT', '0')))  # non-interactive passthrough
'''


class CodexLauncher(unittest.TestCase):
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
            '--codex', str(shim), *forwarded], env=env, capture_output=True, text=True, timeout=30)
        entries = [json.loads(line) for line in (work / 'log.jsonl').read_text().splitlines()]
        sockets = list(runtime.glob('*.sock')) if runtime.exists() else []
        return result, entries, sockets, work

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
