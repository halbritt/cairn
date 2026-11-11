"""Real filesystem publication tests; no selected Python, ORT or model execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
MODULE = Path(__file__).with_name('semantic_python_closure.py')
spec = importlib.util.spec_from_file_location('closure', MODULE)
closure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closure)


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        (self.inputs / 'bin').mkdir(parents=True)
        (self.inputs / 'lib').mkdir()
        (self.inputs / 'bin/python').write_bytes(b'nonexecutable fixture, not Python')
        (self.inputs / 'bin/python').chmod(0o755)
        (self.inputs / 'lib/naïve.txt').write_text('frozen Καλημέρα\n', encoding='utf-8')
        (self.inputs / 'bin/python-alias').symlink_to('python')
        (self.inputs / 'lib-alias').symlink_to('lib', target_is_directory=True)
        self.stage = self.root / 'stage'
        self.stage.mkdir(mode=0o700)
        self.provenance = {'fixture': 'selected test bytes, not upstream qualification'}
        self.seal = closure.seal_inputs({'python': self.inputs}, self.provenance)

    def cleanup(self):
        for base, _, _ in os.walk(self.temp.name, followlinks=False):
            Path(base).chmod(0o700)
        self.temp.cleanup()

    def publish(self):
        return closure.publish_payload(self.stage, self.seal)

    def snapshot(self, path):
        return {str(x.relative_to(path)): (x.lstat().st_ino, x.lstat().st_mtime_ns,
                                           x.lstat().st_mode)
                for x in [path, *path.rglob('*')]}

    def test_repeated_publication_preserves_all_inodes_mtimes_modes_and_digests(self):
        first = self.publish()
        before = self.snapshot(Path(first['path']))
        second = self.publish()
        self.assertEqual(first, second)
        self.assertEqual(before, self.snapshot(Path(second['path'])))
        self.assertEqual(closure.tree(first['path']), first['descriptor']['entries'])
        self.assertEqual(os.readlink(Path(first['path']) / 'python/bin/python-alias'), 'python')
        self.assertEqual((Path(first['path']) / 'python/lib-alias/naïve.txt').read_text(),
                         'frozen Καλημέρα\n')

    def test_missing_tampered_or_changed_mode_input_refuses_before_stage_write(self):
        for mutation in ('missing', 'content', 'mode'):
            with self.subTest(mutation=mutation):
                path = self.inputs / 'lib/naïve.txt'
                original = path.read_bytes()
                original_mode = path.stat().st_mode & 0o777
                if mutation == 'missing':
                    path.unlink()
                elif mutation == 'content':
                    path.write_bytes(b'different')
                else:
                    path.chmod(0o777)
                with self.assertRaisesRegex(closure.ClosureError, 'inventory differs'):
                    self.publish()
                self.assertEqual(list(self.stage.iterdir()), [])
                path.write_bytes(original)
                path.chmod(original_mode)

    def test_bad_digest_incomplete_inventory_and_traversal_manifest_refuse(self):
        bad = copy.deepcopy(self.seal)
        bad['selection']['provenance']['extra'] = 'tampered'
        with self.assertRaisesRegex(closure.ClosureError, 'digest or schema differs'):
            closure.publish_payload(self.stage, bad)
        for action in ('omit', 'traversal'):
            bad = copy.deepcopy(self.seal)
            item = bad['selection']['entries'].pop('python/lib/naïve.txt')
            if action == 'traversal':
                bad['selection']['entries']['../outside'] = item
            bad['selection_sha256'] = closure.digest(closure.canonical(bad['selection']))
            with self.assertRaises(closure.ClosureError):
                closure.publish_payload(self.stage, bad)
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_escaping_absolute_dangling_and_cyclic_links_refuse(self):
        (self.root / 'outside').write_text('outside fixture')
        link = self.inputs / 'bad'
        for target in ('../outside', str(self.root / 'outside'), 'missing', 'bad'):
            with self.subTest(target=target):
                link.symlink_to(target)
                with self.assertRaisesRegex(closure.ClosureError, 'symlink'):
                    closure.seal_inputs({'python': self.inputs}, self.provenance)
                link.unlink()

    def test_unsupported_fifo_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.inputs / 'fifo')
        with self.assertRaisesRegex(closure.ClosureError, 'unsupported selected file type'):
            closure.seal_inputs({'python': self.inputs}, self.provenance)

    def test_wrong_existing_generation_is_not_repaired(self):
        first = self.publish()
        selected = Path(first['path']) / 'python/lib/naïve.txt'
        selected.chmod(0o644)
        selected.write_bytes(b'wrong existing generation')
        selected.chmod(0o444)
        before = self.snapshot(Path(first['path']))
        with self.assertRaisesRegex(closure.ClosureError, 'refusing repair'):
            self.publish()
        self.assertEqual(before, self.snapshot(Path(first['path'])))
        self.assertEqual(selected.read_bytes(), b'wrong existing generation')

    def test_missing_extra_and_wrong_mode_generation_members_refuse(self):
        first = self.publish()
        root = Path(first['path'])
        selected = root / 'python/lib/naïve.txt'
        selected.chmod(0o644)
        with self.assertRaisesRegex(closure.ClosureError, 'refusing repair'):
            closure.verify_generation(root, first['descriptor'])
        selected.chmod(0o444)
        selected.parent.chmod(0o755)
        selected.unlink()
        selected.parent.chmod(0o555)
        with self.assertRaisesRegex(closure.ClosureError, 'refusing repair'):
            self.publish()

    def test_unknown_symlink_wrong_mode_and_overlapping_destinations_refuse(self):
        selected = self.stage / 'unrelated'
        selected.write_text('preserve me')
        with self.assertRaisesRegex(closure.ClosureError, 'untagged stage must be empty'):
            self.publish()
        self.assertEqual(list(self.stage.iterdir()), [selected])
        selected.unlink()
        self.stage.chmod(0o755)
        with self.assertRaisesRegex(closure.ClosureError, '0700'):
            self.publish()
        self.stage.chmod(0o700)
        alias = self.root / 'stage-alias'
        alias.symlink_to(self.stage)
        with self.assertRaisesRegex(closure.ClosureError, 'symlink'):
            closure.publish_payload(alias, self.seal)
        nested = self.inputs / 'destination'
        nested.mkdir(mode=0o700)
        seal = closure.seal_inputs({'python': self.inputs}, self.provenance)
        with self.assertRaisesRegex(closure.ClosureError, 'overlaps'):
            closure.publish_payload(nested, seal)

    def test_separate_control_address_names_payload_without_self_hash_cycle(self):
        first = self.publish()
        files = {'pyvenv.cfg': ('home = ' + first['path'] + '/python/bin\n').encode(),
                 'bin/worker': b'fixture control bytes, never executed\n'}
        control = closure.publish_controls(self.stage, first, files)
        before = self.snapshot(Path(control['path']))
        self.assertEqual(control, closure.publish_controls(self.stage, first, files))
        self.assertEqual(before, self.snapshot(Path(control['path'])))
        self.assertEqual(control['payload_address'], first['address'])
        self.assertNotEqual(control['address'], first['address'])
        self.assertNotIn(control['address'].encode(), files['pyvenv.cfg'])
        other = self.root / 'other stage'
        other.mkdir(mode=0o700)
        relocated = closure.publish_payload(other, self.seal)
        self.assertEqual(relocated['address'], first['address'])
        files['pyvenv.cfg'] = ('home = ' + relocated['path'] + '/python/bin\n').encode()
        self.assertNotEqual(closure.publish_controls(other, relocated, files)['address'],
                            control['address'])

    def test_atomic_no_replace_refuses_a_concurrent_wrong_generation(self):
        commit = closure.commit_pending
        wrong = []

        def competing_writer(pending, target):
            target.mkdir(mode=0o555)
            wrong.append(target)
            return commit(pending, target)

        with mock.patch.object(closure, 'commit_pending', competing_writer):
            with self.assertRaisesRegex(closure.ClosureError, 'refusing repair'):
                self.publish()
        self.assertTrue(wrong[0].is_dir())
        self.assertEqual(list(wrong[0].iterdir()), [])
        self.assertFalse(list((self.stage / 'payloads').glob('.pending-*')))

    def test_process_killed_before_atomic_rename_leaves_no_selectable_partial(self):
        selection = self.root / 'selection.json'
        selection.write_bytes(closure.canonical(self.seal))
        read_fd, write_fd = os.pipe()
        code = '''import importlib.util,json,os,pathlib,signal,sys
spec=importlib.util.spec_from_file_location('closure',sys.argv[1]);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
def stop(pending,target):
 os.write(int(sys.argv[4]),b'ready');signal.pause()
c.commit_pending=stop
c.publish_payload(sys.argv[2],json.loads(pathlib.Path(sys.argv[3]).read_text()))
'''
        proc = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(MODULE),
                                 str(self.stage), str(selection), str(write_fd)],
                                pass_fds=(write_fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 10)
            self.assertTrue(ready, 'fixture did not reach pre-rename boundary')
            self.assertEqual(os.read(read_fd, 5), b'ready')
            proc.kill()
            _, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, -signal.SIGKILL, stderr)
        finally:
            os.close(read_fd)
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
        generations = list((self.stage / 'payloads').iterdir())
        self.assertEqual(len(generations), 1)
        self.assertTrue(generations[0].name.startswith('.pending-'))
        complete = self.publish()
        self.assertEqual(closure.verify_generation(complete['path'], complete['descriptor']),
                         Path(complete['path']))


if __name__ == '__main__':
    unittest.main()
