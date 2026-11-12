import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('real', Path(__file__).resolve().parents[1] / 'semantic_transition_real.py')
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)


class WorkerTraceTests(unittest.TestCase):
    def test_fault_receipt_waits_for_verified_runtime_images(self):
        python = {20: {'images': [{'path': '/selected/python'}]},
                  21: {'images': [{'path': '/selected/numpy/core.so'},
                                  {'path': '/selected/onnxruntime_pybind11_state.so'}]}}
        rust = {30: {'native_images': [{}, {}, {}]},
                31: {'native_images': [{}, {}, {}, {}]}}
        self.assertEqual(real.ready_sample_pids(python, rust), {'python': [21], 'rust': [31]})

    def test_every_worker_root_requires_its_own_sample(self):
        roots = {'python': {20, 30}, 'rust': {40}}
        for samples in ({'python': {20}, 'rust': {40}},
                        {'python': {20, 30}, 'rust': {40, 50}}):
            with self.subTest(samples=samples), self.assertRaisesRegex(
                    RuntimeError, r'worker root/sample mismatch: traced=\[.*\] sampled=\[.*\]'):
                real.require_worker_samples(roots, samples)

    def test_exact_worker_samples_accept_integer_pid_sets(self):
        real.require_worker_samples({'python': {20, 30}, 'rust': {40}},
                                    {'python': {'20': {}, 30: {}}, 'rust': {'40': {}}})

    def test_live_mapping_denial_fails_and_terminal_races_remain_labelled(self):
        proc = Path('/proc/42')
        pending, exits = {}, set()
        real.mapping_permission(proc, pending, exits, lambda path: False)
        real.mapping_permission(proc, pending, exits, lambda path: False)
        self.assertEqual(pending, {42: 2})
        with self.assertRaisesRegex(RuntimeError, 'live worker mapping access denied'):
            real.mapping_permission(proc, pending, exits, lambda path: False)
        real.mapping_permission(proc, pending, exits, lambda path: True)
        self.assertEqual(pending, {})
        self.assertEqual(exits, {42})

    def test_standalone_refusal_is_preserved_for_common_wrapper_observation(self):
        observer = real.load('observer', Path(__file__).resolve().parents[1] / 'test_semantic_package_real.py')
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / 'root/work').mkdir(parents=True)
            (proc / 'root/work/go.mod').write_text('fixture checkout')
            (proc / 'root/usr/bin').mkdir(parents=True)
            (proc / 'root/usr/bin/python3').write_bytes(b'fixture interpreter marker')
            with self.assertRaisesRegex(RuntimeError, 'development checkout exposed'):
                observer.worker_root_visibility(proc)
            self.assertEqual(observer.worker_root_visibility(proc, standalone=False), (True, True))

    def test_private_export_modes_restore_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            for category in ('payloads', 'launchers', 'manifests'):
                (stage / category / 'generation').mkdir(parents=True)
            binary = stage / 'payloads/generation/bin/worker'
            binary.parent.mkdir()
            binary.write_bytes(b'unchanged native fixture')
            data = stage / 'payloads/generation/model.json'
            data.write_bytes(b'unchanged data')
            entry = stage / 'launchers/generation/embed'
            entry.write_bytes(b'unchanged launcher')
            real.restore_private_rust_modes(stage)
            self.assertEqual(binary.read_bytes(), b'unchanged native fixture')
            self.assertEqual(data.read_bytes(), b'unchanged data')
            self.assertEqual(binary.stat().st_mode & 0o777, 0o555)
            self.assertEqual(data.stat().st_mode & 0o777, 0o444)
            self.assertEqual(entry.stat().st_mode & 0o777, 0o555)
            self.assertEqual(binary.parent.stat().st_mode & 0o777, 0o555)
            # TemporaryDirectory cleanup needs writable copied directories.
            binary.parent.chmod(0o700)
            for category in ('payloads', 'launchers', 'manifests'):
                (stage / category / 'generation').chmod(0o700)

    def test_scope_tracks_short_lived_children_and_resumed_clones(self):
        traces = {10: 'connect(1, "private DB", 1) = 0\nclone3({flags=CLONE_VM}, 88) = 20\n',
                  20: 'execve("/selected/worker", ["/selected/worker"], []) = 0\nclone(child_stack=0) = 21\n',
                  21: '<... clone3 resumed> ) = 22\n',
                  22: 'connect(1, "control", 1) = -1 ECONNREFUSED\n',
                  30: 'execve("/other/worker", [], []) = 0\n'}
        roots, selected = real.worker_trace_scope(traces, ['/selected/worker'])
        self.assertEqual(roots, {20})
        self.assertEqual(selected, {20, 21, 22})
        self.assertNotIn(10, selected)
        self.assertNotIn(30, selected)


if __name__ == '__main__':
    unittest.main()
