"""Owned ELF/memfd/process fixtures only; no semantic model or staged Rust runs."""
import copy
import fcntl
import importlib.util
import inspect
import json
import mmap
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location('snapshot_fixture', Path(__file__).with_name('test_semantic_package_real.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

CHILD = r'''
import os,sys,json,mmap,fcntl
cfg=json.load(open(sys.argv[1]));fds=[];maps=[]
for index,path in enumerate(cfg['native']):
 fd=os.memfd_create('cairn-fixture',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 os.write(fd,open(path,'rb').read())
 if cfg.get('unsealed')!=index:fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
 prot=mmap.PROT_READ|(0 if cfg.get('readonly')==index else mmap.PROT_EXEC)
 maps.append(mmap.mmap(fd,0,flags=mmap.MAP_PRIVATE,prot=prot));fds.append(fd)
if cfg.get('anonymous'):
 maps.append(mmap.mmap(-1,4096,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS,prot=mmap.PROT_READ|mmap.PROT_EXEC))
if cfg.get('extra'):
 fd=os.memfd_create('other-name',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 os.write(fd,open(cfg['native'][0],'rb').read()+b'extra');fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
 maps.append(mmap.mmap(fd,0,flags=mmap.MAP_PRIVATE,prot=mmap.PROT_READ|mmap.PROT_EXEC));fds.append(fd)
if cfg.get('replaced_fd'):
 old=os.fstat(fds[0])
 # mmap duplicates its FD: close every descriptor for this object, leaving its VMA.
 for number in os.listdir('/proc/self/fd'):
  try:info=os.fstat(int(number))
  except OSError:continue
  if (info.st_dev,info.st_ino)==(old.st_dev,old.st_ino):os.close(int(number))
 fd=os.memfd_create('cairn-fixture',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 os.write(fd,b'other object');fds[0]=fd
if cfg.get('deleted'):
 fd=os.open(cfg['deleted'],os.O_RDONLY);os.unlink(cfg['deleted'])
 maps.append(mmap.mmap(fd,0,flags=mmap.MAP_PRIVATE,prot=mmap.PROT_READ|mmap.PROT_EXEC));os.close(fd)
print('ready',flush=True)
sys.stdin.buffer.read(1)
'''


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cairn-process-snapshot-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / 'payload'
        (self.payload / 'bin').mkdir(parents=True)
        (self.payload / 'native').mkdir()
        self.worker = self.payload / 'bin/worker'
        shutil.copyfile(sys.executable, self.worker)
        self.worker.chmod(0o700)
        self.native = []
        elf = Path('/usr/bin/true').read_bytes()
        for index, role in enumerate(helper.package.LIBRARIES):
            path = self.payload / 'native' / role
            path.write_bytes(elf + bytes([index]))
            self.native.append(path)
        self.selection = self.make_selection()

    def checksum(self, path):
        return helper.package.digest(Path(path).read_bytes())

    def make_selection(self):
        files = {str(path): self.checksum(path) for path in [self.worker, *self.native]}
        libraries = {}
        for row in helper.parse_process_maps(Path('/proc/self/maps').read_text()):
            if 'x' in row['permissions'] and row['path'].startswith('/') and row['path'] != sys.executable:
                libraries[row['path']] = self.checksum(row['path'])
        home = self.root / 'private-home'
        home.mkdir(mode=0o700)
        environment = helper.package.worker_environment() | {'HOME': str(home)}
        request = self.root / 'request.jsonl'
        request.write_text('{"id":"r07-embed","operation":"query","text":"hello"}\n')
        driver = {'path': '/usr/bin/true', 'sha256': self.checksum('/usr/bin/true')}
        return {'schema': 'cairn.semantic-staged-case/1', 'mode': 'embed', 'payload': str(self.payload),
                'payload_files': files, 'worker': {'path': str(self.worker), 'sha256': files[str(self.worker)]},
                'native': {role: files[str(path)] for role, path in zip(helper.package.LIBRARIES, self.native)},
                'system_libraries': libraries, 'worker_environment': environment,
                'driver': driver, 'launcher': {'path': str(self.worker), 'sha256': files[str(self.worker)]},
                'input': {'path': str(request), 'sha256': self.checksum(request)}, 'private_home': str(home),
                'expected_identity': {'model_sha256': 'a' * 64, 'algorithm': 'bge-original-passages/1', 'dimensions': 384},
                'expected_projection': {'method': 'test'}, 'timeout_seconds': 2}

    def child(self, **options):
        cfg = self.root / ('child-' + str(len(list(self.root.glob('child-*')))) + '.json')
        cfg.write_text(json.dumps({'native': [str(path) for path in self.native], **options}))
        child = subprocess.Popen([str(self.worker), '-I', '-B', '-c', CHILD, str(cfg)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=self.selection['worker_environment'])
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
        self.addCleanup(cleanup)
        ready = child.stdout.readline()
        self.assertEqual(ready, b'ready\n', child.stderr.read() if not ready else ready)
        return child

    def capture(self, child, selection=None):
        return helper.strict_snapshot(child.pid, selection or self.selection, os.getpid())

    def test_complete_actual_owned_snapshot_covers_every_executable_range(self):
        child = self.child()
        row = self.capture(child)
        self.assertTrue(row['qualified'], (row['errors'], [r for r in row['executable_mappings'] if r['classification'] == 'unknown']))
        self.assertEqual(row['raw_maps'], row['raw_maps_after'])
        self.assertEqual(row['identity_before'], row['identity_after'])
        self.assertEqual(len(row['native_images']), 4)
        executable = [r for r in helper.parse_process_maps(row['raw_maps']) if 'x' in r['permissions']]
        self.assertEqual(len(executable), len(row['executable_mappings']))
        self.assertEqual({r['classification'] for r in row['executable_mappings']},
                         {'pinned_payload_elf', 'pinned_native_memfd', 'named_system_elf', 'kernel_virtual_mapping'})
        for proof in row['native_images']:
            self.assertEqual(proof['seals'], 15)
            self.assertTrue(proof['maps'])

    def test_wrong_worker_hash_refuses_without_a_positive_subset_fallback(self):
        child = self.child()
        selection = copy.deepcopy(self.selection)
        selection['worker']['sha256'] = '0' * 64
        selection['payload_files'][str(self.worker)] = '0' * 64
        row = self.capture(child, selection)
        self.assertFalse(row['qualified'])
        self.assertIn('running ELF differs', row['errors'][0]['message'])

    def test_wrong_native_hash_is_unknown(self):
        child = self.child()
        selection = copy.deepcopy(self.selection)
        selection['native']['ort'] = '0' * 64
        row = self.capture(child, selection)
        self.assertFalse(row['qualified'])
        self.assertTrue(any(r['classification'] == 'unknown' for r in row['executable_mappings']))

    def test_unsealed_mapped_native_is_unknown(self):
        row = self.capture(self.child(unsealed=0))
        self.assertFalse(row['qualified'])
        self.assertTrue(any(r['classification'] == 'unknown' for r in row['executable_mappings']))

    def test_native_image_without_executable_mapping_is_incomplete(self):
        row = self.capture(self.child(readonly=0))
        self.assertFalse(row['qualified'])
        self.assertFalse(row['native_set_complete'])
        self.assertIn('native images incomplete', row['errors'][0]['message'])

    def test_other_named_memfd_is_unknown_even_with_valid_elf_and_seals(self):
        row = self.capture(self.child(extra=True))
        self.assertFalse(row['qualified'])
        self.assertTrue(any(r['classification'] == 'unknown' and 'other-name' in r['path'] for r in row['executable_mappings']))

    def test_anonymous_executable_is_unknown(self):
        row = self.capture(self.child(anonymous=True))
        self.assertFalse(row['qualified'])
        self.assertTrue(any(r['classification'] == 'unknown' and not r['path'] for r in row['executable_mappings']))

    def test_reused_fd_number_cannot_prove_old_mapped_memfd(self):
        row = self.capture(self.child(replaced_fd=True))
        self.assertFalse(row['qualified'])
        self.assertFalse(row['native_set_complete'])
        self.assertTrue(any(r['classification'] == 'unknown' and '/memfd:' in r['path'] for r in row['executable_mappings']))

    def test_deleted_unproved_executable_file_is_unknown(self):
        deleted = self.root / 'unproved-elf'
        shutil.copyfile('/usr/bin/true', deleted)
        row = self.capture(self.child(deleted=str(deleted)))
        self.assertFalse(row['qualified'])
        missing = [r for r in row['executable_mappings'] if str(deleted) in r['path']]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['classification'], 'unknown')
        self.assertIn('deleted', missing[0]['reason'])

    def test_foreign_process_is_refused_before_observation(self):
        child = self.child()
        row = helper.strict_snapshot(os.getpid(), self.selection, child.pid)
        self.assertFalse(row['qualified'])
        self.assertIn('not owned', row['errors'][0]['message'])

    def test_missing_process_preserves_failure(self):
        row = helper.strict_snapshot(2 ** 30, self.selection, os.getpid())
        self.assertFalse(row['qualified'])
        self.assertTrue(row['errors'])

    def test_permission_denial_is_visible(self):
        child = self.child()
        original = helper.proc_text
        def denied(fd, name):
            if name == 'maps':
                raise PermissionError('test denied maps')
            return original(fd, name)
        with mock.patch.object(helper, 'proc_text', side_effect=denied):
            row = self.capture(child)
        self.assertFalse(row['qualified'])
        self.assertEqual(row['errors'][0]['type'], 'PermissionError')

    def test_missing_seal_fact_retains_raw_and_every_executable_range(self):
        child = self.child()
        with mock.patch.object(helper.fcntl, 'fcntl', side_effect=PermissionError('test denied seals')):
            row = self.capture(child)
        self.assertFalse(row['qualified'])
        self.assertIn('denied seals', row['errors'][0]['message'])
        executable = [r for r in helper.parse_process_maps(row['raw_maps']) if 'x' in r['permissions']]
        self.assertEqual(len(executable), len(row['executable_mappings']))
        self.assertTrue(all(r['classification'] == 'unknown' for r in row['executable_mappings']))

    def test_changed_maps_boundary_refuses(self):
        child = self.child()
        original, reads = helper.proc_text, []
        def changed(fd, name):
            raw = original(fd, name)
            if name == 'maps':
                reads.append(raw)
                if len(reads) == 2:
                    return raw + '\n'
            return raw
        with mock.patch.object(helper, 'proc_text', side_effect=changed):
            row = self.capture(child)
        self.assertFalse(row['qualified'])
        self.assertIn('maps changed', row['errors'][0]['message'])

    def test_changed_starttime_and_namespace_boundaries_refuse(self):
        for field in ('starttime', 'namespaces'):
            with self.subTest(field=field):
                child = self.child()
                original, calls = helper.proc_identity, []
                def changed(fd, pid):
                    identity = original(fd, pid)
                    calls.append(identity)
                    if len(calls) == 2:
                        identity[field] = identity[field] + 1 if field == 'starttime' else {'mnt': 'changed'}
                    return identity
                with mock.patch.object(helper, 'proc_identity', side_effect=changed):
                    row = self.capture(child)
                self.assertFalse(row['qualified'])
                self.assertIn('process/starttime/namespace', row['errors'][0]['message'])

    def test_opened_fd_bytes_survive_path_replacement(self):
        path = self.root / 'opened'
        path.write_bytes(b'original-object')
        with path.open('rb') as stream:
            path.rename(self.root / 'old-object')
            path.write_bytes(b'replacement')
            proof = helper.held_file_record(stream.fileno())
        self.assertEqual(proof['sha256'], helper.package.digest(b'original-object'))
        self.assertNotEqual(proof['sha256'], self.checksum(path))

    def test_file_changed_during_held_read_refuses(self):
        path = self.root / 'changing'
        path.write_bytes(b'x' * 4096)
        with path.open('r+b') as stream:
            original, changed = os.pread, []
            def read(fd, length, offset):
                chunk = original(fd, length, offset)
                if not changed:
                    os.pwrite(fd, b'y', 0)
                    changed.append(True)
                return chunk
            with mock.patch.object(helper.os, 'pread', side_effect=read):
                with self.assertRaisesRegex(RuntimeError, 'changed during read'):
                    helper.held_file_record(stream.fileno())

    def test_mapping_classes_reject_wrong_device_inode_seals_and_nonelf(self):
        fd = os.memfd_create('same-name', os.MFD_ALLOW_SEALING)
        self.addCleanup(os.close, fd)
        os.write(fd, Path('/usr/bin/true').read_bytes())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, 15)
        mapping = mmap.mmap(fd, 0, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ | mmap.PROT_EXEC)
        self.addCleanup(mapping.close)
        info = os.fstat(fd)
        row = next(r for r in helper.parse_process_maps(Path('/proc/self/maps').read_text())
                   if r['inode'] == info.st_ino and 'x' in r['permissions'])
        proof = helper.held_file_record(fd)
        proof.update(mapped_device=row['device'], seals=15)
        selection = copy.deepcopy(self.selection)
        selection['native']['ort'] = proof['sha256']
        self.assertEqual(helper.classify_executable(row, proof, selection)['classification'], 'pinned_native_memfd')
        for mutation in ({'device': info.st_dev + 1}, {'inode': info.st_ino + 1}, {'seals': 0}, {'seals': None}, {'elf': False}):
            with self.subTest(mutation=mutation):
                self.assertEqual(helper.classify_executable(row, proof | mutation, selection)['classification'], 'unknown')
        self.assertEqual(helper.classify_executable(row, None, selection)['classification'], 'unknown')

    def test_payload_system_and_kernel_boundaries(self):
        child = self.child()
        observed = self.capture(child)
        self.assertTrue(observed['qualified'], (observed['errors'], [r for r in observed['executable_mappings'] if r['classification'] == 'unknown']))
        for row in observed['executable_mappings']:
            category = row['classification']
            if category in ('pinned_payload_elf', 'named_system_elf'):
                bad = dict(row['evidence'], sha256='0' * 64)
                self.assertEqual(helper.classify_executable(row, bad, self.selection)['classification'], 'unknown')
                self.assertEqual(helper.classify_executable(row, dict(row['evidence'], elf=False), self.selection)['classification'], 'unknown')
            if category == 'kernel_virtual_mapping':
                for mutation in ({'device': '01:00'}, {'inode': 1}, {'path': '[heap]'}, {'path': ''}, {'path': '[vdso-other]'}):
                    self.assertEqual(helper.classify_executable(row | mutation, None, self.selection)['classification'], 'unknown')

    def test_malformed_mapping_rows_refuse(self):
        for raw in ('', 'not maps', 'f-e rw-p 0 00:00 0', '1-2 xx-p 0 00:00 0', '1-2 r-xp nope 00:00 0'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                helper.parse_process_maps(raw)

    def test_legacy_signature_and_empty_selection_compatible(self):
        signature = inspect.signature(helper.process_snapshot)
        self.assertTrue(signature.parameters['standalone'].default)
        self.assertFalse(signature.parameters['minimal_root'].default)
        self.assertEqual(helper.process_snapshot(self.root / 'unselected-payload', {}), [])
        self.assertEqual(helper.trace_effects('connect(3, 0, 0) = -1 EPERM\n')['connects'], ['connect(3, 0, 0) = -1 EPERM'])

    def test_legacy_actual_fixture_return_shape_and_minimal_root_guard(self):
        child = self.child()
        rows = helper.process_snapshot(self.payload, {}, ancestor=os.getpid(), standalone=False)
        self.assertEqual([row['pid'] for row in rows], [child.pid])
        self.assertEqual(len(rows[0]['native_images']), 4)
        self.assertEqual(set(rows[0]), {'pid', 'argv', 'binary_sha256', 'environment', 'native_images',
                                       'system_mappings', 'development_checkout_visible', 'python_visible'})
        self.assertEqual(helper.process_snapshot(self.payload, {child.pid: rows[0]}, ancestor=os.getpid()), [])
        with self.assertRaisesRegex(RuntimeError, 'Python exposed'):
            helper.process_snapshot(self.payload, {}, ancestor=os.getpid(), minimal_root=True, standalone=False)

    def test_known_strict_process_reuse_refuses_instead_of_skipping(self):
        child = self.child()
        row = self.capture(child)
        self.assertTrue(row['qualified'], row['errors'])
        self.assertEqual(helper.process_snapshot(self.payload, {child.pid: row}, ancestor=os.getpid(), strict_selection=self.selection), [])
        row['identity_after']['starttime'] -= 1
        with self.assertRaisesRegex(RuntimeError, 'identity changed or PID reused'):
            helper.process_snapshot(self.payload, {child.pid: row}, ancestor=os.getpid(), strict_selection=self.selection)

    def test_strict_wrapper_requires_ancestor_and_does_not_fallback(self):
        with self.assertRaisesRegex(RuntimeError, 'owned case ancestor'):
            helper.process_snapshot(self.payload, {}, strict_selection=self.selection)

    def test_staged_selection_refusals_never_start_process(self):
        for change in ({'timeout_seconds': 91}, {'mode': 'direct'}, {'schema': 'wrong'}):
            selected = self.root / ('selected-' + str(len(list(self.root.glob('selected-*')))) + '.json')
            selected.write_text(json.dumps(self.selection | change))
            output = self.root / ('case-' + selected.stem)
            with mock.patch.object(helper.subprocess, 'Popen') as launch:
                with self.assertRaises(RuntimeError):
                    helper.run_staged_case(SimpleNamespace(staged_case=str(selected), output=str(output)))
            launch.assert_not_called()
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'FAILED')
            self.assertEqual(receipt['runtime_attempts'], 0)

    def staged_case(self, selection):
        selected = self.root / 'selected-case.json'
        selected.write_text(json.dumps(selection))
        output = self.root / 'staged-output'
        return SimpleNamespace(staged_case=str(selected), output=str(output)), output

    def test_invalid_selection_json_retains_failure_without_launch(self):
        args, output = self.staged_case(self.selection)
        Path(args.staged_case).write_text('{invalid')
        with mock.patch.object(helper.subprocess, 'Popen') as launch:
            with self.assertRaises(json.JSONDecodeError):
                helper.main(['--staged-case=' + args.staged_case, '--output', args.output])
        launch.assert_not_called()
        self.assertEqual(json.loads((output / 'receipt.json').read_text())['runtime_attempts'], 0)

    def test_staged_worker_home_and_offline_guards_refuse(self):
        for field, value in (('HOME', str(Path.home())), ('ORT_DISABLE_TELEMETRY', '0'),
                             ('HF_HUB_OFFLINE', '0'), ('OPENBLAS_NUM_THREADS', '3'), ('LD_PRELOAD', '/bad')):
            with self.subTest(field=field):
                selection = copy.deepcopy(self.selection)
                selection['worker_environment'][field] = value
                with self.assertRaises(RuntimeError):
                    helper.staged_selection(selection)

    def test_actual_absent_selected_home_is_recorded(self):
        home = self.root / 'absent-home'
        self.selection['private_home'] = str(home)
        self.selection['worker_environment']['HOME'] = str(home)
        helper.staged_selection(self.selection)
        before = helper.tree_record(home)
        self.assertEqual(before['state'], 'absent')
        home.mkdir(mode=0o700)
        self.assertNotEqual(before, helper.tree_record(home))

    def test_no_worker_staged_fixture_refuses_and_preserves_trace_and_cleanup(self):
        args, output = self.staged_case(self.selection)
        with self.assertRaisesRegex(RuntimeError, 'no complete owned snapshot'):
            helper.run_staged_case(args)
        receipt = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(receipt['runtime_attempts'], 1)
        self.assertTrue(receipt['cleanup']['all_witnessed_processes_gone'])
        self.assertTrue(receipt['trace']['raw_files'])
        self.assertEqual(receipt['trees_before'], receipt['trees_after'])
        self.assertEqual((output / 'input.jsonl').read_bytes(), Path(self.selection['input']['path']).read_bytes())
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir()))

    def test_timed_out_disposable_driver_reaps_owned_descendant(self):
        driver = self.root / 'fixture-driver'
        driver.write_text('#!/usr/bin/python3 -B\nimport subprocess,sys\nsys.stdin.buffer.read()\n'
                          'child=subprocess.Popen(["/usr/bin/sleep","30"])\nchild.wait()\n')
        driver.chmod(0o700)
        self.selection['driver'] = {'path': str(driver), 'sha256': self.checksum(driver)}
        self.selection['timeout_seconds'] = 0.3
        args, output = self.staged_case(self.selection)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            helper.run_staged_case(args)
        receipt = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertEqual(receipt['runtime_attempts'], 1)
        self.assertGreaterEqual(len(receipt['cleanup']['tracked']), 3)
        self.assertTrue(receipt['cleanup']['all_witnessed_processes_gone'])
        self.assertTrue(receipt['trace']['raw_files'])

    def test_owned_cleanup_reaps_real_fixture(self):
        child = self.child()
        tracked = helper.case_descendants(child.pid)
        receipt = helper.stop_owned_case(child, tracked)
        self.assertTrue(receipt['all_witnessed_processes_gone'])
        self.assertIsNotNone(child.returncode)

    def test_cleanup_refuses_changed_starttime_without_signaling(self):
        child = self.child()
        tracked = helper.case_descendants(child.pid)
        tracked[child.pid]['starttime'] -= 1
        with self.assertRaisesRegex(RuntimeError, 'changed owned process'):
            helper.stop_owned_case(child, tracked)
        self.assertIsNone(child.poll())

    def test_human_trace_audit_keeps_issuing_resumed_and_inherited_writes(self):
        output = self.root / 'audit'
        output.mkdir()
        raw = ('write(9</unrelated>, "x", 1 <unfinished ...>\n'
               '<... write resumed>) = 1\nopenat(AT_FDCWD, "/a", O_WRONLY) = 3\n'
               'write(1<pipe:[1]>, "connect( is data", 16) = 16\n')
        (output / 'case.trace.123').write_text(raw)
        audit = helper.case_trace_inventory(output)
        self.assertEqual(audit['connect_lines'], [])
        self.assertEqual(len((output / 'human-write-audit.txt').read_text().splitlines()), 4)
        self.assertIn('PENDING', audit['persistent_effects'])
        (output / 'human-write-audit.txt').unlink()
        (output / 'case.trace.456').write_text('connect(3, 0, 0 <unfinished ...>\n<... connect resumed>) = -1 EPERM\n')
        self.assertEqual(len(helper.case_trace_inventory(output)['connect_lines']), 2)

    def test_tree_record_detects_bytes_modes_symlinks_and_removals(self):
        directory = self.root / 'tree'
        directory.mkdir()
        file = directory / 'file'
        file.write_bytes(b'first')
        before = helper.tree_record(directory)
        file.write_bytes(b'second')
        self.assertNotEqual(before, helper.tree_record(directory))
        file.chmod(0o400)
        changed = helper.tree_record(directory)
        (directory / 'link').symlink_to('file')
        self.assertNotEqual(changed, helper.tree_record(directory))
        file.unlink()
        self.assertNotEqual(changed, helper.tree_record(directory))

    def test_tree_permission_failure_cannot_be_reported_as_absence(self):
        directory = self.root / 'unreadable'
        directory.mkdir(mode=0o000)
        try:
            with self.assertRaises(PermissionError):
                helper.tree_record(directory)
        finally:
            directory.chmod(0o700)

    def test_embedding_protocol_identity_projection_and_vector_boundaries(self):
        request = {'id': 'selected'}
        good = {'id': 'selected', 'identity': self.selection['expected_identity'],
                'query_projection': self.selection['expected_projection'], 'vector': [1.0] + [0.0] * 383}
        self.assertEqual(helper.staged_reply(self.selection, request, good)['squared_norm'], 1)
        for change in ({'id': 'wrong'}, {'error': 'failed'}, {'identity': {}}, {'query_projection': {}},
                       {'vector': [0.0] * 384}, {'vector': [float('nan')] + [0.0] * 383},
                       {'vector': [2.0] + [0.0] * 383}, {'vector': [1.0] * 383}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                helper.staged_reply(self.selection, request, good | change)

    def test_rank_and_stream_protocol_refuse_wrong_identity_joins_and_scores(self):
        note = {'record_id': 'fixture-record', 'version': 2, 'body_sha256': 'c' * 64}
        request = {'id': 'selected', 'request': {'notes': [note]}}
        identity = {'model_sha256': 'a' * 64, 'algorithm': 'bge-max-chunk/1'}
        for mode in ('rank', 'stream'):
            selection = self.selection | {'mode': mode, 'expected_identity': identity}
            good = {'id': 'selected', 'result': identity | {'scores': [note | {'score': 42}]}}
            self.assertEqual(helper.staged_reply(selection, request, good)['scores'][0]['score'], 42)
            for change in ({'record_id': 'wrong'}, {'version': 1}, {'body_sha256': 'd' * 64},
                           {'score': 0.5}, {'score': float('inf')}, {'score': True}):
                with self.subTest(mode=mode, change=change), self.assertRaises(RuntimeError):
                    helper.staged_reply(selection, request, good | {'result': identity | {'scores': [note | {'score': 42} | change]}})
            for result in (identity | {'scores': []}, identity | {'model_sha256': '0' * 64, 'scores': [note | {'score': 42}]}):
                with self.assertRaises(RuntimeError):
                    helper.staged_reply(selection, request, good | {'result': result})


if __name__ == '__main__':
    unittest.main()
