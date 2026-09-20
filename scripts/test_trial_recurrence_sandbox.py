"""Sandbox/candidate-integrity regressions for trial-opencode-recurrence.

The controller never trusts model-writable repository metadata: inventory and
reconstruction use only the controller-owned trusted baseline established
before authoring, and the held-out gate runs under a PID-isolated, cleared
environment with no host runtime sockets. Evidence uses harmless sentinels
and a fake credential marker; no real credentials, providers or hostile code
run on the host."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from unittest.mock import patch as mock_patch
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_controller():
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('trial_controller', ROOT / 'scripts/trial-opencode-recurrence.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE_TEST = '''package probe

import (
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestIsolationEvidence(t *testing.T) {
	body, err := os.ReadFile("/proc/self/cgroup")
	if err != nil {
		t.Fatal(err)
	}
	entry := strings.TrimSpace(string(body))
	if !strings.HasPrefix(entry, "0::/") {
		t.Fatalf("unexpected cgroup membership: %q", entry)
	}
	scope := filepath.Join("/sys/fs/cgroup", strings.TrimPrefix(entry, "0::"))
	probe := filepath.Join(scope, "cairn-delegation-probe")
	if err := os.Mkdir(probe, 0700); err != nil {
		t.Fatalf("own delegated subtree is not writable: %v", err)
	}
	if err := os.Remove(probe); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/sys/fs/cgroup", filepath.Dir(scope)} {
		var stat syscall.Statfs_t
		if err := syscall.Statfs(path, &stat); err != nil {
			t.Fatal(err)
		}
		if stat.Flags&1 == 0 {
			t.Fatalf("outside cgroup mount is writable: %s", path)
		}
	}
	t.Log("OWN_CGROUP_WRITABLE=true OUTSIDE_CGROUP_READONLY=true")
	port, err := os.ReadFile("/work/host-port")
	if err != nil {
		t.Fatal(err)
	}
	connection, err := net.DialTimeout("tcp", "127.0.0.1:" + string(port), time.Second)
	if err == nil {
		connection.Close()
		t.Fatal("host loopback listener is reachable")
	}
	t.Log("HOST_NETWORK_UNREACHABLE=true")
	marker := false
	for _, entry := range os.Environ() {
		if strings.Contains(entry, "HOSTILE_PROBE_SECRET") {
			marker = true
		}
	}
	_, runErr := os.Stat("/run/user")
	procEntries := 0
	if entries, err := os.ReadDir("/proc"); err == nil {
		procEntries = len(entries)
	}
	_, homeErr := os.Stat("/home")
	t.Logf("MARKER_PRESENT=%v RUN_USER_ERR=%v HOME_ERR=%v PROC_ENTRIES=%d",
		marker, runErr != nil, homeErr != nil, procEntries)
}
'''


class SandboxIntegrity(unittest.TestCase):
    def setUp(self):
        self.controller = load_controller()
        self.temp = tempfile.TemporaryDirectory(prefix='recurrence-audit-', dir='/tmp/opencode')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'trial'
        self.root.mkdir()
        self.work = Path(self.temp.name) / 'work'
        self.work.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.work)], check=True)
        (self.work / 'internal' / 'backend' / 'llm').mkdir(parents=True)
        tracked = self.work / 'internal' / 'backend' / 'llm' / 'supervisor.go'
        tracked.write_text('package llm\n')
        subprocess.run(['git', '-C', str(self.work), 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.work), '-c', 'user.name=t', '-c', 'user.email=t@localhost',
                        'commit', '-qm', 'snapshot'], check=True, capture_output=True)
        # The controller snapshots its trusted baseline while pristine, exactly
        # as run_trial does before the model authors anything.
        self.trusted = self.controller.establish_trusted(self.work, self.root, 'arm')

    def test_repository_metadata_is_never_trusted(self):
        sentinel = Path(self.temp.name) / 'host-executed-sentinel'
        # Model-written .git: config symlinked at a host file, execution
        # vectors in a replacement config, a smuggled hook, staged changes,
        # and a rewritten baseline commit.
        (self.work / 'internal' / 'backend' / 'llm' / 'supervisor.go').write_text('package llm\n// touched\n')
        (self.work / '.git' / 'config').unlink()
        (self.work / '.git' / 'config').symlink_to('/etc/passwd')
        hooks = self.work / '.git' / 'hooks'
        hooks.mkdir(exist_ok=True)
        (hooks / 'pre-commit').write_text('#!/bin/sh\ntouch {}\n'.format(sentinel))
        (hooks / 'pre-commit').chmod(0o755)
        (self.work / '.git' / 'HEAD').write_text('ref: refs/heads/hostile\n')
        (self.work / '.git' / 'index').write_bytes(b'')
        patch, names, outside = self.controller.candidate_diff(self.work, self.trusted)
        self.assertFalse(sentinel.exists(), 'repository metadata executed on the host')
        self.assertIn('internal/backend/llm/supervisor.go', names)
        self.assertEqual(outside, [])
        self.assertIn(b'// touched', patch)
        self.assertNotIn(b'root:', patch, 'symlinked host file leaked into the patch')

    def test_gate_path_symlink_cannot_overwrite_host_marker(self):
        marker = Path(self.temp.name) / 'host-marker'
        marker.write_text('synthetic-marker')
        name = self.controller.GATE_PATH
        patch = (f'diff --git a/{name} b/{name}\nnew file mode 120000\n'
                 f'--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+{marker}\n'
                 '\\ No newline at end of file\n').encode()
        with self.assertRaises((ValueError, OSError)):
            self.controller.gate(self.work, self.root, patch, trusted=self.trusted)
        self.assertEqual(marker.read_text(), 'synthetic-marker')

    def test_links_and_special_files_are_rejected_before_ingestion(self):
        marker = Path(self.temp.name) / 'outside-marker'
        marker.write_text('synthetic-private-content')
        package = self.work / 'internal/backend/llm'
        for kind in ('symlink', 'hardlink', 'fifo', 'directory-symlink'):
            with self.subTest(kind=kind):
                path = package / 'extra_test.go'
                if kind == 'symlink':
                    path.symlink_to(marker)
                elif kind == 'hardlink':
                    os.link(marker, path)
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.symlink_to(marker.parent, target_is_directory=True)
                try:
                    with self.assertRaises((ValueError, OSError)):
                        self.controller.candidate_diff(self.work, self.trusted)
                    self.assertEqual(marker.read_text(), 'synthetic-private-content')
                finally:
                    path.unlink()

    def test_filename_inventory_is_lossless_and_roundtrips(self):
        names = ['internal/backend/llm/new\nquoted"\\_test.go',
                 'internal/backend/llm/é_test.go', 'internal/backend/llm/:*_test.go']
        for name in names:
            (self.work / name).write_bytes(b'package llm\n')
        patch, paths, outside = self.controller.candidate_diff(self.work, self.trusted)
        self.assertEqual(paths, sorted(names))
        self.assertEqual(outside, [])
        evaluation = self.root / 'roundtrip'
        evaluation.mkdir()
        self.controller.safe_git(self.trusted, evaluation, 'read-tree', 'HEAD')
        self.controller.safe_git(self.trusted, evaluation, 'checkout-index', '--all')
        self.controller.safe_git(self.trusted, evaluation, 'apply', '-', data=patch)
        for name in names:
            self.assertEqual((evaluation / name).read_bytes(), b'package llm\n')
        (self.work / 'outside\n_test.go').write_text('outside')
        (self.work / self.controller.GATE_PATH).write_text('held-out')
        _, _, outside = self.controller.candidate_diff(self.work, self.trusted)
        self.assertEqual(outside, ['internal/backend/llm/cairn_recurrence_gate_test.go',
                                   'outside\n_test.go'])

    def test_git_attributes_and_inherited_configuration_cannot_filter_candidate(self):
        marker = Path(self.temp.name) / 'filter-executed'
        global_config = Path(self.temp.name) / 'git-global'
        global_config.write_text('[filter "probe"]\n clean = touch ' + str(marker) + '\n')
        attributes = self.work / '.gitattributes'
        attributes.write_text('*.go filter=probe text eol=lf working-tree-encoding=UTF-16\n')
        self.trusted = self.controller.establish_trusted(self.work, self.root, 'attributes')
        content = b'package llm\r\nvar marker = "raw bytes"\r\n'
        (self.work / 'internal/backend/llm/supervisor.go').write_bytes(content)
        environment = dict(GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_COUNT='1',
                           GIT_CONFIG_KEY_0='filter.probe.clean', GIT_CONFIG_VALUE_0='touch ' + str(marker),
                           GIT_CONFIG_PARAMETERS="'core.fsmonitor=touch " + str(marker) + "'",
                           GIT_EXTERNAL_DIFF='touch ' + str(marker),
                           GIT_INDEX_FILE=str(Path(self.temp.name) / 'injected-index'))
        with mock_patch.dict(os.environ, environment):
            patch, names, outside = self.controller.candidate_diff(self.work, self.trusted)
        self.assertFalse(marker.exists())
        self.assertFalse(Path(environment['GIT_INDEX_FILE']).exists())
        self.assertEqual(names, ['internal/backend/llm/supervisor.go'])
        self.assertEqual(outside, [])
        evaluation = self.root / 'attribute-roundtrip'
        evaluation.mkdir()
        self.controller.safe_git(self.trusted, evaluation, 'read-tree', 'HEAD')
        self.controller.safe_git(self.trusted, evaluation, 'checkout-index', '--all')
        self.controller.safe_git(self.trusted, evaluation, 'apply', '-', data=patch)
        self.assertEqual((evaluation / names[0]).read_bytes(), content)

    def test_baseline_link_is_preserved_but_retargeting_is_rejected(self):
        marker = Path(self.temp.name) / 'baseline-target'
        marker.write_text('synthetic-baseline-marker')
        link = self.work / 'CLAUDE.md'
        link.symlink_to(marker)
        trusted = self.controller.establish_trusted(self.work, self.root, 'baseline-link')
        self.assertEqual(self.controller.candidate_diff(self.work, trusted), (b'', [], []))
        link.unlink()
        link.symlink_to('internal/backend/llm/supervisor.go')
        with self.assertRaisesRegex(ValueError, 'candidate changes a symlink'):
            self.controller.candidate_diff(self.work, trusted)
        self.assertEqual(marker.read_text(), 'synthetic-baseline-marker')

    def test_baseline_ignores_all_existing_git_metadata(self):
        marker = Path(self.temp.name) / 'metadata-marker'
        marker.write_text('synthetic-marker')
        shutil.rmtree(self.work / '.git')
        (self.work / '.git').symlink_to(marker)
        trusted = self.controller.establish_trusted(self.work, self.root, 'fresh')
        self.assertEqual(self.controller.candidate_diff(self.work, trusted), (b'', [], []))
        self.assertEqual(marker.read_text(), 'synthetic-marker')
        with self.assertRaises(FileExistsError):
            self.controller.establish_trusted(self.work, self.root, 'fresh')

    def test_ignored_files_stay_in_the_candidate_inventory(self):
        (self.work / '.gitignore').write_text('ignored_input.go\n')
        subprocess.run(['git', '-C', str(self.work), 'add', '.gitignore'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.work), '-c', 'user.name=t', '-c', 'user.email=t@l',
                        'commit', '-qm', 'ignore rule'], check=True, capture_output=True)
        self.trusted = self.controller.establish_trusted(self.work, self.root, 'arm2')
        (self.work / 'ignored_input.go').write_text('package llm\n')
        _, names, outside = self.controller.candidate_diff(self.work, self.trusted)
        self.assertIn('ignored_input.go', names)
        self.assertIn('ignored_input.go', outside)

    def test_gate_isolates_environment_and_reconstructs_candidate(self):
        captured = {}

        def fake_run(argv, **kwargs):
            if argv[0] == 'git':
                return run_real(argv, **kwargs)
            captured['argv'] = argv
            captured['kwargs'] = kwargs
            return subprocess.CompletedProcess(argv, 0, json.dumps(
                {'Action': 'pass', 'Test': 'TestCairnRecurrenceRuntimeCacheLifetime'}).encode() + b'\n', b'')

        run_real = subprocess.run
        subprocess.run = fake_run
        try:
            (self.work / 'internal' / 'backend' / 'llm' / 'supervisor.go').write_text('package llm\n// hostile\n')
            patch, _, outside = self.controller.candidate_diff(self.work, self.trusted)
            self.assertEqual(outside, [])
            (self.work / '.gitignore').write_text('ignored_input.go\n')
            (self.work / 'ignored_input.go').write_text('package llm\n// hidden\n')
            result = self.controller.gate(self.work, self.root, patch, trusted=self.trusted)
        finally:
            subprocess.run = run_real
        evaluation, = self.root.glob('gate-evaluation-*')
        hostile = evaluation / 'internal' / 'backend' / 'llm' / 'supervisor.go'
        self.assertIn('// hostile', hostile.read_text())
        self.assertFalse((evaluation / 'ignored_input.go').exists())
        argv = captured['argv']
        self.assertEqual(argv[0], 'systemd-run')
        self.assertIn('exec bwrap ', argv[argv.index('-c') + 1])
        self.assertIn('--clearenv', argv)
        self.assertIn('--unshare-pid', argv)
        binds = [str(bind) for bind in argv]
        self.assertNotIn('/run', binds)
        self.assertNotIn('/run/user', binds)
        self.assertNotIn('cwd', captured['kwargs'])
        self.assertNotIn('env', captured['kwargs'])
        setenv = {argv[i + 1]: argv[i + 2] for i in range(len(argv) - 2) if argv[i] == '--setenv'}
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', setenv)
        self.assertNotIn('XDG_RUNTIME_DIR', setenv)
        self.assertEqual(setenv.get('HOME'), '/tmp/gate-home')
        self.assertEqual(setenv.get('GOROOT'), '/opt/go')
        self.assertTrue(result['passed'])

    def test_real_gate_command_isolates_an_untrusted_go_test(self):
        """Executes genuinely untrusted (harmless) Go code through the exact
        gate command with a fake credential marker in the controller env."""
        evaluation = self.root / 'probe-evaluation'
        (evaluation / 'probe').mkdir(parents=True)
        (evaluation / 'go.mod').write_text('module isolation.probe\n\ngo 1.25\n')
        (evaluation / 'probe' / 'isolation_test.go').write_text(PROBE_TEST)
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        (evaluation / 'host-port').write_text(str(listener.getsockname()[1]))
        argv = self.controller.gate_command(evaluation, self.root,
                                            tail=['--', 'go', 'test', '-json', '-count=1',
                                                  '-timeout=60s', './probe'])
        environment = dict(os.environ, HOSTILE_PROBE_SECRET='fake-controller-secret')
        result = subprocess.run(argv, capture_output=True, timeout=180, env=environment)
        output = ''.join(json.loads(line).get('Output', '') for line in result.stdout.splitlines()
                         if line.startswith(b'{'))
        self.assertEqual(result.returncode, 0, output + result.stderr.decode(errors='replace'))
        self.assertIn('OWN_CGROUP_WRITABLE=true OUTSIDE_CGROUP_READONLY=true', output)
        self.assertIn('MARKER_PRESENT=false', output, 'controller credential marker reached the sandbox')
        self.assertIn('RUN_USER_ERR=true', output, 'host runtime sockets remained reachable')
        self.assertIn('HOME_ERR=true', output, 'host home remained reachable')
        import re
        entries = int(re.search(r'PROC_ENTRIES=(\d+)', output).group(1))
        # The namespace contains only the sandbox's own toolchain processes
        # (go, compiler, test binary); the host exposes several hundred.
        host = sum(1 for name in os.listdir('/proc') if name.isdigit())
        self.assertLess(entries, 300, 'host processes visible inside the sandbox')
        self.assertLess(entries, host // 2, 'sandbox pid namespace mirrors the host')


if __name__ == '__main__':
    unittest.main()
