"""Synthetic preparation; never import or run an embedding model."""
import hashlib
import json
from pathlib import Path
import shutil
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import trial_embedding_launch as launch


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='embedding-launch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.modules = self.root/'modules'
        self.modules.mkdir()
        for name in ('embedding_worker.py', 'semantic_rank.py'):
            shutil.copyfile(Path(__file__).with_name(name), self.modules/name)
        self.python = self.root/'python'
        self.python.write_text('#!/bin/sh\nexit 99\n')
        self.python.chmod(0o700)
        self.model = self.root/'prepared-model'
        self.model.mkdir()
        self.config = dict(schema='cairn.weekly-embedding-launch/1', python=str(self.python),
                           worker=pin(self.modules/'embedding_worker.py'),
                           scorer=pin(self.modules/'semantic_rank.py'), model_dir=str(self.model))
        self.config_path = self.root/'selection.json'
        self.command = self.root/'worker-embeddings'
        # Literal installer output for these shell-safe fixture paths.
        self.expected = (f'#!/bin/sh\nexec {self.python} {self.modules}/embedding_worker.py '
                         f'--model-dir {self.model}\n').encode()
        self.command.write_bytes(self.expected)
        self.command.chmod(0o700)
        self.output = self.root/'retained'

    def prepare(self):
        self.config_path.write_text(json.dumps(self.config))
        return launch.prepare(pin(self.config_path),
                              {'CURRENT': pin(self.command), 'CAND': pin(self.command)},
                              self.output)

    def test_retains_exact_pinned_config_and_common_installer_launcher(self):
        receipt = self.prepare()
        self.assertEqual((self.output/'embedding-config.json').read_bytes(), self.config_path.read_bytes())
        self.assertEqual((self.output/'worker-embeddings').read_bytes(), self.expected)
        self.assertEqual(receipt['launcher'], pin(self.output/'worker-embeddings'))
        self.assertEqual(receipt['declared_worker'], pin(self.command))
        self.assertEqual(receipt['python']['invocation'], str(self.python))
        self.assertEqual(set(p.name for p in self.output.iterdir()),
                         {'embedding-config.json', 'worker-embeddings'})
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output/'worker-embeddings').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output/'embedding-config.json').stat().st_mode & 0o777, 0o600)

    def test_installed_template_known_correspondence(self):
        root = '/home/halbritt/.local/share/cairn/semantic'
        config = dict(python=root+'/venv/bin/python', worker={'path': root+'/embedding_worker.py'},
                      model_dir=root+'/model-cache/models--qdrant--bge-small-en-v1.5-onnx-q/'
                                     'snapshots/52398278842ec682c6f32300af41344b1c0b0bb2')
        # Pure rendering: no installed files, interpreter or model are accessed.
        rendered = launch._render(config)
        self.assertEqual(len(rendered), 296)
        self.assertEqual(hashlib.sha256(rendered).hexdigest(),
                         'f63e0a943e1607e0b24e960206e67d9161290115c9999447bd133305b56b5aa8')

    def test_literal_quoted_argv_and_lexical_interpreter_symlink(self):
        recorder = self.root/'recorder'
        recorder.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv))\n')
        recorder.chmod(0o700)
        python = self.root/"python 'quoted'"
        python.symlink_to(recorder)
        model = self.root/"model $(touch SHOULD_NOT_EXIST)"
        model.mkdir()
        self.config.update(python=str(python), model_dir=str(model))
        # Expected shell quoting from the documented POSIX template, independent
        # of the production renderer. The child only records arguments.
        self.command.write_text("#!/bin/sh\nexec '" + str(self.root) + "/python " +
                                "'\"'\"'" + "quoted" + "'\"'\"'" + "' " +
                                str(self.modules/'embedding_worker.py') +
                                " --model-dir '" + str(model) + "'\n")
        receipt = self.prepare()
        observed = subprocess.run([receipt['launcher']['path']], cwd=self.root,
                                  capture_output=True, check=True, timeout=3)
        self.assertEqual(json.loads(observed.stdout),
                         [str(python), str(self.modules/'embedding_worker.py'), '--model-dir', str(model)])
        self.assertEqual(receipt['python']['invocation'], str(python))
        self.assertEqual(receipt['python']['resolved'], str(recorder))
        self.assertFalse((self.root/'SHOULD_NOT_EXIST').exists())

    def test_changed_config_cannot_match_unchanged_worker(self):
        other = self.root/'other-model'
        other.mkdir()
        self.config['model_dir'] = str(other)
        with self.assertRaisesRegex(launch.Refusal, 'embedding_launcher_mismatch'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_both_arms_must_declare_the_same_actual_worker_pin(self):
        self.config_path.write_text(json.dumps(self.config))
        other = self.root/'other-worker'
        other.write_bytes(self.expected)
        other.chmod(0o700)
        for content, error in ((self.expected, 'embedding_common_worker'),
                               (b'#!/bin/sh\nexit 0\n', 'embedding_launcher_mismatch')):
            with self.subTest(error=error):
                other.write_bytes(content)
                with self.assertRaisesRegex(launch.Refusal, error):
                    launch.prepare(pin(self.config_path),
                                   {'CURRENT': pin(self.command), 'CAND': pin(other)}, self.output)
                self.assertFalse(self.output.exists())

    def test_closed_schema_duplicate_keys_and_wrong_types_refuse_without_output(self):
        original = json.loads(json.dumps(self.config))
        for key, value in [('schema', 'cairn.weekly-embedding-launch/2'), ('env', {}),
                           ('python', False), ('model_dir', []), ('worker', True),
                           ('python', '/a/../python'), ('python', '/python\u0000'),
                           ('python', '/\ud800')]:
            with self.subTest(key=key, value=value):
                self.config = {**original, key: value}
                with self.assertRaises(launch.Refusal):
                    self.prepare()
                self.assertFalse(self.output.exists())
        for raw in [b'{"schema":1,"schema":2}', b'[]', b'{"x":NaN}',
                    b'['*1100+b']'*1100, b'\xff', b' '*16385,
                    json.dumps(original).encode('utf16')]:
            with self.subTest(raw=raw[:30]):
                self.config_path.write_bytes(raw)
                with self.assertRaises(launch.Refusal):
                    launch.prepare(pin(self.config_path), {}, self.output)
                self.assertFalse(self.output.exists())

    def test_source_pair_is_fixed_and_actual_bytes_are_verified(self):
        worker = self.modules/'embedding_worker.py'
        worker.write_bytes(worker.read_bytes()+b'\n# different source\n')
        for refresh in (False, True):
            with self.subTest(refresh=refresh):
                if refresh:
                    self.config['worker'] = pin(worker)
                with self.assertRaises(launch.Refusal):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_scorer_must_be_the_adjacent_module(self):
        other = self.root/'semantic_rank.py'
        shutil.copyfile(self.modules/'semantic_rank.py', other)
        self.config['scorer'] = pin(other)
        with self.assertRaisesRegex(launch.Refusal, 'embedding_scorer_location'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_module_symlink_refuses_even_when_bytes_match(self):
        worker = self.modules/'embedding_worker.py'
        target = self.root/'worker-source'
        worker.rename(target)
        worker.symlink_to(target)
        with self.assertRaisesRegex(launch.Refusal, 'path_indirection'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_fifo_source_and_interpreter_refuse_without_blocking(self):
        fifo = self.root/'fifo'
        os.mkfifo(fifo)
        self.config['python'] = str(fifo)
        self.config_path.write_text(json.dumps(self.config))
        # An isolated timeout guards against open-before-fstat blocking; the
        # parent always waits/reaps via subprocess.run, including timeout.
        code = ('import sys; from pathlib import Path; import trial_embedding_launch as m\n'
                'try:\n'
                ' if sys.argv[1] == "pin": m.prepare({"path":sys.argv[2],"sha256":"0"*64}, {}, Path(sys.argv[3]))\n'
                ' else: m.prepare(json.loads(sys.argv[4]), {}, Path(sys.argv[3]))\n'
                'except m.Refusal: sys.exit(0)\n'
                'sys.exit(9)\n')
        for kind in ('pin', 'interpreter'):
            with self.subTest(kind=kind):
                subprocess.run([sys.executable, '-B', '-c', 'import json\n'+code, kind, str(fifo),
                                str(self.output), json.dumps(pin(self.config_path))],
                               cwd=Path(__file__).parent, check=True, timeout=3, capture_output=True)
                self.assertFalse(self.output.exists())

    def test_pin_mismatch_and_nonexecutable_launcher_refuse(self):
        self.config_path.write_text(json.dumps(self.config))
        selected = pin(self.config_path)
        self.config_path.write_bytes(self.config_path.read_bytes()+b' ')
        with self.assertRaisesRegex(launch.Refusal, 'embedding_pin_mismatch'):
            launch.prepare(selected, {}, self.output)
        self.command.chmod(0o600)
        with self.assertRaisesRegex(launch.Refusal, 'embedding_executable'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_reused_or_removed(self):
        self.output.mkdir()
        sentinel = self.output/'owned-by-another-call'
        sentinel.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_output_symlink_does_not_touch_target(self):
        target = self.root/'unrelated'
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(launch.Refusal, 'path_indirection'):
            self.prepare()
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue(self.output.is_symlink())

    def test_launcher_and_path_bounds(self):
        self.command.write_bytes(b'x'*16385)
        with self.assertRaisesRegex(launch.Refusal, 'embedding_file_bound'):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.config['python'] = '/'+'x'*4096
        with self.assertRaisesRegex(launch.Refusal, 'embedding_path_bound'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_model_directory_and_interpreter_must_exist_and_have_correct_kind(self):
        self.model.rmdir()
        with self.assertRaisesRegex(launch.Refusal, 'embedding_model_directory'):
            self.prepare()
        self.model.mkdir()
        self.python.chmod(0o600)
        with self.assertRaisesRegex(launch.Refusal, 'embedding_interpreter'):
            self.prepare()
        self.python.unlink()
        self.python.symlink_to(self.root/'missing-interpreter')
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_failed_second_write_removes_only_owned_outputs(self):
        original = os.fsync
        calls = []
        def fail_second(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError('synthetic retention failure')
            original(fd)
        with mock.patch.object(launch.os, 'fsync', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'synthetic retention failure'):
                self.prepare()
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
