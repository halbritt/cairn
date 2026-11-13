"""Owned SIGKILL boundaries followed by the actual pinned Go recovery CLI."""
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'scripts/stage_lifecycle_hooks.py'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AgyLayerRecoveryProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory(prefix='cairn-agy-recovery-tools-')
        cls.addClassCleanup(cls.tools.cleanup)
        cls.evidence = Path(os.environ['CAIRN_AGY_RECOVERY_EVIDENCE']) if os.environ.get('CAIRN_AGY_RECOVERY_EVIDENCE') else None
        if cls.evidence:
            cls.evidence.mkdir(mode=0o700)
        cls.images = cls.evidence / 'images' if cls.evidence else Path(cls.tools.name) / 'images'
        cls.images.mkdir(mode=0o700)
        cls.pins = {}
        for name, arguments in [
            ('stager', ['go', 'build', '-buildvcs=false', './cmd/cairn-stage-lifecycle']),
            ('observer', ['go', 'test', '-c', './internal/lifecycle/install']),
        ]:
            build = Path(cls.tools.name) / name
            argv = arguments[:2] + ['-o', str(build)] + arguments[2:]
            done = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=120)
            if done.returncode:
                raise RuntimeError(done.stderr.decode())
            image = cls.images / name
            image.write_bytes(build.read_bytes())
            image.chmod(0o500)
            info = subprocess.check_output(['go', 'version', '-m', str(image)], cwd=ROOT)
            cls.pins[name] = {'path': str(image), 'sha256': digest(image), 'mode': '0o500',
                              'argv': argv, 'build_info': info.decode()}
            setattr(cls, name, image)
        cls.pins['head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
        cls.pins['source_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
        (cls.images / 'pins.json').write_bytes(encoded(cls.pins))
        print('actual recovery images pinned BEFORE use', encoded(cls.pins).decode(), flush=True)

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='cairn-agy-recovery-files-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.children = []
        self.addCleanup(self.join_children)

    def join_children(self):
        for child in self.children:
            if child.poll() is None:
                if child.pid <= 1:
                    raise AssertionError('refuse non-owned PID boundary')
                child.kill()
            child.communicate(timeout=10)
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def fixture(self, layer, present):
        root = self.root / str(uuid.uuid4())
        root.mkdir(mode=0o700)
        account, workspace = root / 'account', root / 'workspace'
        account.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        paths = {'account': account / '.gemini/config/hooks.json',
                 'project': workspace / '.agents/hooks.json'}
        old = ('{\n "credential_selector" : "/fixture/never-open.token",'
               '\n "opaque" : ["日本語 🛡️ 😀",900719925474099312345],'
               '\n "third-party" : {"Stop":[{"type":"command","command":"audit"}]},'
               '\n "cairn-coordination" : {}\n}\n').encode()
        other = 'project' if layer == 'account' else 'account'
        (account / '.gemini').mkdir(mode=0o750)
        for label in ['account', 'project']:
            paths[label].parent.mkdir(mode=0o750)
            if label == other or present:
                paths[label].write_bytes(old)
                paths[label].chmod(0o640)
        prepared = root / 'prepared.json'
        prepared.write_bytes(encoded({'credential_selector': 'must-not-copy', 'cairn-coordination': {
            'PreInvocation': [{'type': 'command', 'command': '/fixture/cairn hook'}]}}))
        prepared.chmod(0o400)
        record = paths[layer].parent / ('.cairn-recovery-agy-' + uuid.uuid4().hex + '.json')
        return dict(root=root, account=account, workspace=workspace, selected=paths[layer],
                    other=paths[other], old=old, layer=layer, present=present, record=record,
                    prepared=prepared, plan=dict(AccountHome=str(account), Workspace=str(workspace),
                    Layer=layer, Settings=dict(path=str(prepared), sha256=digest(prepared)),
                    RecoveryPath=str(record)))

    def snapshot(self, root):
        rows = {}
        for path in sorted(root.rglob('*')):
            st = path.lstat()
            row = dict(mode=stat.S_IMODE(st.st_mode), dev=st.st_dev, ino=st.st_ino,
                       uid=st.st_uid, gid=st.st_gid, links=st.st_nlink,
                       size=st.st_size, mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns)
            if stat.S_ISREG(st.st_mode):
                row['raw'] = base64.b64encode(path.read_bytes()).decode()
            elif stat.S_ISLNK(st.st_mode):
                row['symlink'] = os.readlink(path)
            rows[str(path.relative_to(root))] = row
        return rows

    def retain(self, name, value):
        if self.evidence:
            path = self.evidence / self._testMethodName
            path.mkdir(mode=0o700, exist_ok=True)
            (path / (name + '.json')).write_bytes(encoded(value))

    def recover(self, f, pin, **overrides):
        arguments = dict(account_home=str(f['account']), workspace=str(f['workspace']),
                         layer=f['layer'], record=str(f['record']), record_sha256=pin)
        arguments.update(overrides)
        argv = [str(self.stager), 'recover-agy-layer']
        for key, value in arguments.items():
            argv.extend(['--' + key.replace('_', '-'), value])
        self.assertEqual(digest(self.stager), self.pins['stager']['sha256'])
        return subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=20)

    def publish(self, f):
        argv = [sys.executable, '-I', '-S', '-B', str(BRIDGE), 'publish-agy',
                '--stager', str(self.stager), '--stager-sha256', self.pins['stager']['sha256'],
                '--account-home', str(f['account']), '--workspace', str(f['workspace']),
                '--layer', f['layer'], '--recovery-record', str(f['record'])]
        plan = dict(schema='cairn.agy-layer-install/1', settings=f['plan']['Settings'])
        done = subprocess.run(argv, input=encoded(plan), cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        selected = json.loads(done.stdout)
        self.assertEqual(selected['recovery'], dict(path=str(f['record']), sha256=digest(f['record'])))
        return selected['recovery']['sha256']

    def assert_old(self, f):
        if f['present']:
            self.assertEqual(f['selected'].read_bytes(), f['old'])
            self.assertEqual(stat.S_IMODE(f['selected'].stat().st_mode), 0o640)
        else:
            self.assertFalse(f['selected'].exists())
        self.assertEqual(f['other'].read_bytes(), f['old'])

    def test_owned_sigkill_every_publication_boundary_then_checked_cli(self):
        points = [side + point for point in ['settings-sync', 'recovery-sync', 'publication',
                  'readback', 'directory-sync'] for side in ['before:', 'after:']]
        for layer in ['account', 'project']:
            for present in [False, True]:
                for point in points:
                    with self.subTest(layer=layer, present=present, boundary=point):
                        f = self.fixture(layer, present)
                        request = f['root'] / 'owned-child.json'
                        request.write_bytes(encoded(dict(Plan=f['plan'], Boundary=point)))
                        request.chmod(0o600)
                        env = dict(os.environ, CAIRN_AGY_RECOVERY_CHILD=str(request))
                        child = subprocess.Popen([str(self.observer), '-test.run=^TestAgyRecoveryOwnedChild$'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env)
                        self.children.append(child)
                        self.assertGreater(child.pid, 1)
                        ready, _, _ = select.select([child.stdout], [], [], 20)
                        self.assertTrue(ready, 'owned child did not reach finite boundary')
                        self.assertEqual(child.stdout.readline(), ('AGY-BOUNDARY:' + point + '\n').encode())
                        self.assertIsNone(child.poll())
                        child.kill()
                        stdout, stderr = child.communicate(timeout=10)
                        self.assertEqual(child.returncode, -signal.SIGKILL)
                        killed = self.snapshot(f['root'])
                        before_new = f['selected'].exists() and f['selected'].read_bytes() != f['old']
                        should_new = point in ['after:publication', 'before:readback', 'after:readback',
                                               'before:directory-sync', 'after:directory-sync']
                        self.assertEqual(before_new, should_new)
                        record_present = f['record'].exists()
                        pin = digest(f['record']) if record_present else '0' * 64
                        recovered = self.recover(f, pin)
                        after = self.snapshot(f['root'])
                        row = dict(layer=layer, present=present, boundary=point, owned_pid=child.pid,
                            kill_returncode=child.returncode, child_stdout=stdout.decode(), child_stderr=stderr.decode(),
                            new_active_immediately_after_kill=before_new, record_present=record_present,
                            after_kill=killed, recovery_exit=recovered.returncode,
                            recovery_stdout=recovered.stdout.decode(), recovery_stderr=recovered.stderr.decode(), after_recovery=after)
                        self.retain(layer + '-' + str(present) + '-' + point.replace(':', '-'), row)
                        if not record_present:
                            self.assertNotEqual(recovered.returncode, 0)
                            self.assertEqual(killed, after)
                        else:
                            self.assertEqual(recovered.returncode, 0, recovered.stderr.decode())
                            self.assertEqual(json.loads(recovered.stdout)['changed'], before_new)
                            self.assertEqual(digest(f['record']), pin)
                            stable = self.snapshot(f['root'])
                            repeated = self.recover(f, pin)
                            self.assertEqual(repeated.returncode, 0, repeated.stderr.decode())
                            self.assertFalse(json.loads(repeated.stdout)['changed'])
                            self.assertEqual(self.snapshot(f['root']), stable)
                        self.assert_old(f)
                        self.assertEqual(digest(self.observer), self.pins['observer']['sha256'])
        self.retain('summary', dict(owned_children=40, joined=40, new_active_intervals=20,
                                   record_missing_before_durable_journal=12, strict_old_or_disabled=False))

    def test_actual_publisher_bridge_pin_and_cli_restore_repeat(self):
        for layer in ['account', 'project']:
            with self.subTest(layer=layer):
                f = self.fixture(layer, True)
                pin = self.publish(f)
                before = self.snapshot(f['root'])
                result = self.recover(f, pin)
                after = self.snapshot(f['root'])
                self.retain(layer, dict(before_recovery=before, after_recovery=after,
                    recovery_exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode()))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertTrue(json.loads(result.stdout)['changed'])
                self.assert_old(f)
                repeat = self.recover(f, pin)
                self.assertEqual(repeat.returncode, 0, repeat.stderr.decode())
                self.assertFalse(json.loads(repeat.stdout)['changed'])
                self.assertEqual(self.snapshot(f['root']), after)

    def test_actual_cli_refusals_preserve_both_layers_and_evidence(self):
        for problem in ['pin', 'record-bytes', 'backup-bytes', 'same-inode', 'foreign-inode',
                        'other-layer', 'missing-record', 'record-hardlink']:
            with self.subTest(problem=problem):
                f = self.fixture('project', True)
                pin = self.publish(f)
                record = json.loads(f['record'].read_bytes())
                backup = f['selected'].parent / record['Temporary']
                if problem == 'pin':
                    pin = '0' * 64
                elif problem == 'record-bytes':
                    f['record'].chmod(0o600)
                    f['record'].write_bytes(b'{"forged":true}')
                    f['record'].chmod(0o400)
                elif problem == 'backup-bytes':
                    backup.write_bytes(b'{"modified":true}')
                elif problem == 'same-inode':
                    f['selected'].write_bytes(b'{"foreign":true}')
                elif problem == 'foreign-inode':
                    foreign = f['root'] / 'foreign.json'
                    foreign.write_bytes(b'{"foreign":true}')
                    foreign.chmod(0o640)
                    foreign.replace(f['selected'])
                elif problem == 'other-layer':
                    f['other'].write_bytes(b'{"foreign":true}')
                elif problem == 'missing-record':
                    f['record'].rename(f['record'].with_suffix('.retained'))
                elif problem == 'record-hardlink':
                    os.link(f['record'], f['record'].with_suffix('.alias'))
                before = self.snapshot(f['root'])
                result = self.recover(f, pin)
                after = self.snapshot(f['root'])
                self.retain(problem, dict(before=before, after=after, exit=result.returncode,
                    stdout=result.stdout.decode(), stderr=result.stderr.decode()))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
