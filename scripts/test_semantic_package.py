"""Package boundary tests. The small C process is a process fixture, not a model."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('semantic_package', Path(__file__).with_name('semantic_package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        for crate in package.CRATES:
            directory = self.source / 'rust' / crate
            directory.mkdir(parents=True)
            (directory / 'Cargo.toml').write_text('fixture ' + crate)
            (directory / 'Cargo.lock').write_text('locked fixture')
            (directory / 'src.rs').write_text('fixture source')
        self.build = self.root / 'build'
        self.build.mkdir()
        code = self.root / 'fixture.c'
        code.write_text('#include <stdio.h>\n#include <stdlib.h>\nint main(int n,char **a) {'
                        'for(int i=0;i<n;i++) puts(a[i]);'
                        'puts(getenv("HOME")); puts(getenv("ORT_DISABLE_TELEMETRY"));'
                        'puts(getenv("OPENBLAS_NUM_THREADS"));'
                        'return getenv("UNEXPECTED_INHERITED") ? 9 : 0;}\n')
        subprocess.run(['/usr/bin/gcc', str(code), '-o', str(self.build / 'cairn-semantic-rank')], check=True)
        shutil.copyfile(self.build / 'cairn-semantic-rank', self.build / 'cairn-semantic-embed')
        binaries = {mode: package.file_record(package.regular_bytes(self.build / ('cairn-semantic-' + mode)), True)
                    for mode in ('rank', 'embed')}
        self.receipt = {'schema': 'cairn.semantic-build/1', 'target': package.TARGET,
                        'source_inputs': package.records(package.source_files(self.source)),
                        'binaries': binaries, 'logs': {}, 'fixture': True}
        (self.build / 'build.json').write_bytes(package.canonical(self.receipt))
        self.model = self.root / 'model'
        self.model.mkdir()
        self.provenance = {}
        for name in package.MODELS:
            data = ('process fixture ' + name).encode()
            (self.model / name).write_bytes(data)
            self.provenance[name] = {'source': 'test process fixture', 'sha256': package.digest(data)}
        self.libraries = {}
        for role in package.LIBRARIES:
            path = self.root / role
            shutil.copyfile(self.build / 'cairn-semantic-rank', path)
            self.libraries[role] = str(path)
            self.provenance[role] = {'source': 'test ELF fixture', 'sha256': package.digest(path.read_bytes())}
        self.destination = self.root / 'stage'
        self.destination.mkdir(mode=0o700)

    def cleanup(self):
        for base, _, files in os.walk(self.temp.name):
            Path(base).chmod(0o700)
            for name in files:
                path = Path(base) / name
                if not path.is_symlink():
                    path.chmod(0o600)
        self.temp.cleanup()

    def stage(self, destination=None, abi=()):
        return package.stage(destination or self.destination, self.source, self.build,
                             self.model, self.libraries, self.provenance, abi)

    def test_separate_content_addresses_reuse_without_touching_selected_bytes(self):
        first = self.stage()
        selected = Path(first['launchers']['stream']['path'])
        before = (selected.stat().st_ino, selected.stat().st_mtime_ns, selected.read_bytes())
        self.assertEqual(first, self.stage())
        self.assertEqual(before, (selected.stat().st_ino, selected.stat().st_mtime_ns, selected.read_bytes()))
        manifest = package.read_json(first['manifest'])
        self.assertNotEqual(manifest['payload']['address'], manifest['launcher_set_address'])
        self.assertEqual(manifest['schema'], package.SCHEMA)
        self.assertEqual(manifest['derivations']['rank'], {'state': 'unobserved'})
        self.assertEqual(manifest['qualification']['state'], 'incomplete')

    def test_render_at_another_root_keeps_payload_and_changes_launcher_address(self):
        first = self.stage()
        other = self.root / 'another stage'
        other.mkdir(mode=0o700)
        second = self.stage(other)
        self.assertEqual(Path(first['payload']).name, Path(second['payload']).name)
        self.assertNotEqual(Path(first['launchers']['rank']['path']).parent.name,
                            Path(second['launchers']['rank']['path']).parent.name)

    def test_changed_immutable_file_refuses_without_repair(self):
        first = self.stage()
        selected = Path(first['payload']) / 'model/config.json'
        selected.chmod(0o644)
        selected.write_bytes(b'changed')
        selected.chmod(0o444)
        with self.assertRaisesRegex(package.PackageError, 'refusing repair'):
            self.stage()
        self.assertEqual(selected.read_bytes(), b'changed')

    def test_changed_launcher_refuses_without_repair(self):
        first = self.stage()
        selected = Path(first['launchers']['rank']['path'])
        selected.chmod(0o755)
        selected.write_bytes(b'changed launcher')
        selected.chmod(0o555)
        with self.assertRaisesRegex(package.PackageError, 'refusing repair'):
            self.stage()
        self.assertEqual(selected.read_bytes(), b'changed launcher')

    def test_missing_or_wrong_asset_provenance_leaves_destination_empty(self):
        self.provenance['config.json']['sha256'] = '0' * 64
        with self.assertRaisesRegex(package.PackageError, 'mismatched provenance'):
            self.stage()
        self.assertEqual(list(self.destination.iterdir()), [])
        (self.model / 'config.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.stage()

    def test_build_source_or_binary_changes_are_not_relabelled(self):
        (self.source / 'rust/semantic-rank/src.rs').write_text('new source')
        with self.assertRaisesRegex(package.PackageError, 'source differs'):
            self.stage()
        (self.source / 'rust/semantic-rank/src.rs').write_text('fixture source')
        (self.build / 'cairn-semantic-rank').write_bytes(b'different')
        with self.assertRaisesRegex(package.PackageError, 'binary differs'):
            self.stage()

    def test_existing_untagged_destination_and_symlink_are_refused(self):
        (self.destination / 'live-worker').write_bytes(b'do not touch')
        with self.assertRaisesRegex(package.PackageError, 'empty disposable'):
            self.stage()
        self.assertEqual((self.destination / 'live-worker').read_bytes(), b'do not touch')
        link = self.root / 'link'
        link.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaisesRegex(package.PackageError, 'symlink'):
            self.stage(link)

    def test_launchers_use_fixed_environment_and_reject_extra_arguments_and_shadows(self):
        result = self.stage()
        for mode, item in result['launchers'].items():
            env = {key: value for key, value in os.environ.items()
                   if key not in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT')}
            env.update(HOME='/wrong-home', OPENBLAS_NUM_THREADS='999', UNEXPECTED_INHERITED='yes')
            p = subprocess.run([item['path']], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(p.stdout.endswith('/nonexistent\n1\n2\n'))
            self.assertEqual('--stream\n' in p.stdout, mode == 'stream')
            p = subprocess.run([item['path'], '--model-dir', '/wrong'], capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            env['LD_LIBRARY_PATH'] = '/shadow'
            p = subprocess.run([item['path']], env=env, capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(b'loader override refused', p.stderr)

    def test_launcher_refuses_missing_and_mismatched_selected_files(self):
        result = self.stage()
        selected = Path(result['payload']) / 'native/ort'
        selected.chmod(0o644)
        selected.write_bytes(b'wrong library')
        selected.chmod(0o444)
        for item in result['launchers'].values():
            p = subprocess.run([item['path']], capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(p.stdout, b'')
        selected.parent.chmod(0o755)
        selected.unlink()
        for item in result['launchers'].values():
            self.assertNotEqual(subprocess.run([item['path']], capture_output=True).returncode, 0)

    def test_host_abi_prerequisite_mismatch_refuses(self):
        prerequisite = self.root / 'abi.so'
        prerequisite.write_bytes(b'ABI fixture')
        with self.assertRaisesRegex(package.PackageError, 'ABI prerequisite digest differs'):
            self.stage(abi=[{'path': str(prerequisite), 'sha256': '0' * 64}])

    def test_host_abi_mode_is_observed_instead_of_using_payload_permissions(self):
        prerequisite = self.root / 'abi.so'
        prerequisite.write_bytes(b'ABI fixture')
        prerequisite.chmod(0o640)
        staged = self.stage(abi=[{'path': str(prerequisite), 'sha256': package.digest(prerequisite.read_bytes())}])
        observation = package.read_json(staged['manifest'])['requirements']['host_abi_prerequisites'][0]
        self.assertEqual(observation['observed_mode'], 0o640)
        self.assertNotIn('mode', observation)

    def test_fifo_asset_refuses_without_waiting_for_a_writer(self):
        selected = self.model / 'config.json'
        selected.unlink()
        os.mkfifo(selected)
        with self.assertRaisesRegex(package.PackageError, 'not a regular file'):
            self.stage()

    def test_added_source_member_refuses_before_worker_start(self):
        staged = self.stage()
        root = Path(staged['payload']) / 'source/rust/semantic-backend'
        root.chmod(0o755)
        extra = root / 'unexpected.rs'
        extra.write_bytes(b'new unmanifested identity input')
        extra.chmod(0o444)
        root.chmod(0o555)
        result = subprocess.run([staged['launchers']['rank']['path']], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        with self.assertRaisesRegex(package.PackageError, 'refusing repair'):
            self.stage()

    def test_rust_installer_branch_does_not_touch_legacy_home(self):
        env = dict(os.environ, HOME=str(self.root / 'legacy-home'), CAIRN_HOME=str(self.root / 'legacy-cairn'))
        script = Path(__file__).with_name('install-semantic.sh')
        p = subprocess.run(['/bin/bash', str(script), '--stage-rust', '--help'], env=env, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.root / 'legacy-home').exists())
        self.assertFalse((self.root / 'legacy-cairn').exists())

    def test_verbose_cargo_build_script_lines_do_not_hide_binary_artifacts(self):
        # Actual first package build (2026-11-11) failed after compiling because
        # Cargo mixes -vv build-script text into its JSON output stream.
        text = ('[quote 1.0.47] cargo:rerun-if-changed=build.rs\n'
                '{"reason":"compiler-artifact","executable":"/tmp/worker"}\n'
                '[cc 1.2] exit status: 0\n{"reason":"build-finished","success":true}\n')
        self.assertEqual([r.get('executable') for r in package.cargo_messages(text)
                          if r['reason'] == 'compiler-artifact'], ['/tmp/worker'])
        with self.assertRaises(json.JSONDecodeError):
            package.cargo_messages('{malformed claimed JSON}\n')


class TraceEffectsTests(unittest.TestCase):
    def test_store_names_in_output_are_not_store_writes(self):
        spec = importlib.util.spec_from_file_location('real_package_fixtures', Path(__file__).with_name('test_semantic_package_real.py'))
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        trace = ('123 write(1<pipe:[12]>, "Microsoft onnxruntime.db connect(", 33) = 33\n'
                 '123 openat(AT_FDCWD, "/home/.cache/Microsoft/onnxruntime.db", O_RDONLY) = 5\n'
                 '123 connect(3<TCP:[42]>, {sa_family=AF_INET}, 16) = -1 ENETUNREACH\n'
                 '123 openat(AT_FDCWD, "/nonexistent/Microsoft/onnxruntime.db", O_CREAT|O_RDWR, 0600) = 4\n'
                 '123 write(4</nonexistent/Microsoft/onnxruntime.db>, "control", 7) = 7\n')
        effects = fixture.trace_effects(trace)
        self.assertEqual(len(effects['connects']), 1)
        self.assertEqual(len(effects['store_mutations']), 2)


if __name__ == '__main__':
    unittest.main()
