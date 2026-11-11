"""Real filesystem publication tests; no selected Python, ORT or model execution."""
import copy
import importlib.util
import json
import mmap
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tarfile
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

    def python_fixture(self):
        # Structural fixtures only: neither binary is a runnable interpreter.
        (self.inputs / 'bin/python3.11').write_bytes(b'not an interpreter')
        (self.inputs / 'bin/python3.11').chmod(0o755)
        roots = {'python': self.inputs}
        for role in ('site', 'workers', 'model', 'native', 'locale', 'gconv'):
            path = self.root / role
            path.mkdir()
            roots[role] = path
        for name in ('semantic_rank.py', 'embedding_worker.py'):
            (roots['workers'] / name).write_bytes(b'nonmodel worker fixture')
        for name in closure.MODEL_FILES:
            (roots['model'] / name).write_bytes(b'nonmodel asset fixture')
        (roots['native'] / 'ld-linux-x86-64.so.2').write_bytes(b'not a loader')
        (roots['native'] / 'ld-linux-x86-64.so.2').chmod(0o755)
        config = self.root / 'original-pyvenv.cfg'
        config.write_text('home = /original/python/bin\nimplementation = CPython\n'
                          'include-system-site-packages = false\n')
        home = self.root / 'worker-home'
        home.mkdir(mode=0o700)
        payload = closure.publish_payload(self.stage, closure.seal_inputs(roots, self.provenance))
        return payload, config, home

    def test_fixed_venv_controls_bind_exact_payload_and_preserve_original_config(self):
        payload, config, home = self.python_fixture()
        controls = closure.render_python_controls(self.stage, payload, config, home)
        root = Path(controls['path'])
        self.assertEqual((root / 'original-pyvenv.cfg').read_bytes(), config.read_bytes())
        expected = config.read_text().replace('/original/python/bin', payload['path'] + '/python/bin')
        self.assertEqual((root / 'pyvenv.cfg').read_text(), expected)
        self.assertEqual((root / 'bin/python').resolve(), Path(payload['path']) / 'python/bin/python3.11')
        self.assertEqual((root / 'lib/python3.11/site-packages').resolve(), Path(payload['path']) / 'site')
        self.assertFalse(Path(os.readlink(root / 'bin/python')).is_absolute())
        before = self.snapshot(root)
        self.assertEqual(controls, closure.render_python_controls(self.stage, payload, config, home))
        self.assertEqual(before, self.snapshot(root))
        binding = closure.read_json(root / 'binding.json')
        for mode, entry in controls['launchers'].items():
            env = binding['environment'][mode]
            self.assertEqual(env['ORT_DISABLE_TELEMETRY'], '1')
            self.assertFalse({'PYTHONHOME', 'PYTHONPATH', 'OPENBLAS_NUM_THREADS',
                              'OMP_NUM_THREADS', 'MKL_NUM_THREADS'} & set(env))
            self.assertEqual(env.get('CAIRN_SEMANTIC_STREAM_CACHE'), '1' if mode == 'stream' else None)
            # This exits before the fixture loader/interpreter can run.
            result = subprocess.run([entry, 'extra'], capture_output=True)
            self.assertEqual(result.returncode, 64)
            self.assertIn(b'accepts no arguments', result.stderr)
        self.assertEqual(list(home.iterdir()), [])

    def test_control_references_cannot_escape_selected_payload(self):
        payload = self.publish()
        with self.assertRaisesRegex(closure.ClosureError, 'not a selected payload member'):
            closure.publish_controls(self.stage, payload, {'config': b'fixed'},
                                     {'bin/python': str(self.inputs / 'bin/python')})
        with self.assertRaisesRegex(closure.ClosureError, 'parent conflict'):
            closure.publish_controls(self.stage, payload, {'bin': b'fixed', 'bin/worker': b'fixed'})

    def test_observation_manifest_is_independently_addressed_and_refuses_tampering(self):
        payload, config, home = self.python_fixture()
        controls = closure.render_python_controls(self.stage, payload, config, home)
        identities = {'rank': 'expected fixture only', 'embed': 'expected fixture only'}
        first = closure.publish_observations(self.stage, payload, controls, identities, self.provenance)
        selected = Path(first['path'])
        before = self.snapshot(selected.parent)
        self.assertEqual(first, closure.publish_observations(self.stage, payload, controls,
                                                            identities, self.provenance))
        self.assertEqual(before, self.snapshot(selected.parent))
        manifest = closure.read_json(selected)
        self.assertEqual(manifest['qualification'], 'runtime/effects/numerics unobserved')
        self.assertNotEqual(first['address'], payload['address'])
        self.assertNotEqual(first['address'], controls['address'])
        selected.chmod(0o644)
        selected.write_bytes(b'tampered evidence')
        selected.chmod(0o444)
        with self.assertRaisesRegex(closure.ClosureError, 'refusing repair'):
            closure.publish_observations(self.stage, payload, controls, identities, self.provenance)
        self.assertEqual(selected.read_bytes(), b'tampered evidence')

    def test_safe_archive_export_restores_sealed_modes_only_after_integrity_checks(self):
        spec = importlib.util.spec_from_file_location('qualification', MODULE.with_name(
            'check_semantic_python_closure.py'))
        qualification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualification)
        payload = self.publish()
        archive = self.root / 'export.tar.gz'
        with tarfile.open(archive, 'w:gz') as out:
            out.add(payload['path'], arcname=payload['address'])
        exported = self.root / 'exported'
        exported.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(exported, filter='data')
        selected = exported/payload['address']
        self.assertNotEqual(closure.tree(selected), payload['descriptor']['entries'])
        qualification.restore_exported_modes(selected, payload['descriptor'])
        self.assertEqual(closure.verify_generation(selected, payload['descriptor']), selected)
        selected.chmod(0o755)
        changed = selected/'python/lib/naïve.txt'
        changed.chmod(0o644)
        changed.write_bytes(b'corrupt exported byte')
        with self.assertRaisesRegex(RuntimeError, 'exported file differs'):
            qualification.restore_exported_modes(selected, payload['descriptor'])
        self.assertEqual(selected.stat().st_mode & 0o777, 0o755)
        self.assertEqual(changed.read_bytes(), b'corrupt exported byte')

    def test_mapping_identity_uses_kernel_file_and_device_for_readonly_source_data(self):
        spec = importlib.util.spec_from_file_location('qualification', MODULE.with_name(
            'check_semantic_python_closure.py'))
        qualification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualification)
        # /work is the real readonly repository bind, including Btrfs subvolume
        # device behavior; this maps source bytes as data without executing them.
        with MODULE.open('rb') as stream, mmap.mmap(stream.fileno(), 0, prot=mmap.PROT_READ):
            fields = next(line.split(maxsplit=5) for line in Path('/proc/self/maps').read_text().splitlines()
                          if line.split(maxsplit=5)[-1] == str(MODULE))
            row, elf = qualification.mapped_file(Path('/proc/self'), fields)
            self.assertFalse(elf)
            self.assertEqual(row['sha256'], closure.digest(MODULE.read_bytes()))
            self.assertEqual(row['inode'], MODULE.stat().st_ino)
            self.assertEqual(row['mapped_device'], fields[3])
            wrong = list(fields)
            wrong[4] = str(int(wrong[4])+1)
            with self.assertRaisesRegex(RuntimeError, 'inode differs'):
                qualification.mapped_file(Path('/proc/self'), wrong)
            wrong = list(fields)
            wrong[3] = 'ff:ff'
            with self.assertRaisesRegex(RuntimeError, 'mapping device differs'):
                qualification.mapped_file(Path('/proc/self'), wrong)

    def test_observer_lifetime_distinguishes_live_zombie_and_vanished_child(self):
        spec = importlib.util.spec_from_file_location('qualification', MODULE.with_name(
            'check_semantic_python_closure.py'))
        qualification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualification)
        proc = subprocess.Popen([sys.executable, '-I', '-B', '-c',
                                 'import sys;sys.stdin.buffer.read(1)'], stdin=subprocess.PIPE)
        path = Path('/proc')/str(proc.pid)
        try:
            self.assertFalse(qualification.process_terminal(path))
            proc.stdin.close()
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
            self.assertTrue(qualification.process_terminal(path))
            proc.wait(timeout=5)
            self.assertTrue(qualification.process_terminal(path))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_embedding_identity_compares_complete_sealed_object(self):
        spec = importlib.util.spec_from_file_location('qualification', MODULE.with_name(
            'check_semantic_python_closure.py'))
        qualification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualification)
        identity = {'model_sha256': '1'*64, 'algorithm': 'bge-original-passages/1', 'dimensions': 384}
        reply = {'id': 'identity-fixture', 'identity': identity}
        candidate = {'expected_identities': {'embed': identity}}
        qualification.compare_embedding_reply(reply, copy.deepcopy(reply), candidate, [])
        wrong = copy.deepcopy(reply)
        wrong['identity']['dimensions'] = 383
        with self.assertRaisesRegex(RuntimeError, 'discrete field differs'):
            qualification.compare_embedding_reply(reply, wrong, candidate, [])
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            qualification.compare_embedding_reply(wrong, wrong, candidate, [])

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
