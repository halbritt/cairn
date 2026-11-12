import importlib.util
import copy
import json
import contextlib
import io
import subprocess
import threading
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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


class StartupObservationTests(unittest.TestCase):
    def fixture(self):
        cases, selected, roots, samples, traces = [], {}, {}, {}, {}
        for label, pid in [('python', 20), ('rust', 40)]:
            image, entry = '/selected/' + label, '/selected/' + label + '-embed'
            selected[label] = {'embed': entry, 'startup': {'image': image, 'sha256': label + '-image-sha',
                                                         'environment': {'ORT_DISABLE_TELEMETRY': '1'}}}
            mapped = '1000-2000 r-xp 0000 00:01 123 ' + image
            observation = {'pid': pid, 'parent_pid': 10, 'process_start_ticks': '100',
                           'started_at': '2026-11-12T10:00:00.100Z', 'completed_at': '2026-11-12T10:00:00.200Z',
                           'image': image, 'image_sha256': label + '-image-sha', 'image_inode': 123,
                           'mapped_image': mapped, 'map_files_target': image, 'maps': mapped + '\n',
                           'argv': [image, 'actual startup'], 'environment': {'ORT_DISABLE_TELEMETRY': '1'}}
            cases.append({'implementation': label, 'supervisor_pid': 10, 'phases': [{
                'name': 'cold_query_restart', 'startup_worker_observation': observation,
                'new_worker_pipes': {'worker_pid': pid}, 'actual_retrieval_calls': [{
                    'started_at': '2026-11-12T10:00:00Z', 'completed_at': '2026-11-12T10:00:02Z'}]}]})
            roots[label], samples[label] = {pid, pid + 1}, {pid + 1}
            traces[pid] = ('execve(' + json.dumps(entry) + ', [' + json.dumps(entry) + '], [] /* fixture */) = 0\n' +
                           'execve(' + json.dumps(image) + ', [' + json.dumps(image) + '], [] /* fixture */) = 0\n')
        return cases, selected, roots, samples, traces

    def test_own_live_startup_facts_preserve_exact_root_accounting(self):
        fixture = self.fixture()
        proof = real.case7_startup_samples(*fixture)
        self.assertEqual(set(proof['python']), {20})
        self.assertEqual(set(proof['rust']), {40})
        self.assertIn('uninitialized libraries unobserved', proof['python'][20]['runtime_limit'])
        self.assertEqual(proof['python'][20]['trace_file'], 'worker-trace.20')
        self.assertEqual(proof['python'][20]['observation']['image'], '/selected/python')

    def test_undesignated_missing_root_still_refuses(self):
        cases, selected, roots, samples, traces = self.fixture()
        samples['python'].remove(21)
        with self.assertRaisesRegex(RuntimeError, 'python worker root/sample mismatch'):
            real.case7_startup_samples(cases, selected, roots, samples, traces)

    def test_extra_full_sample_still_refuses(self):
        cases, selected, roots, samples, traces = self.fixture()
        samples['rust'].add(99)
        with self.assertRaisesRegex(RuntimeError, 'rust worker root/sample mismatch'):
            real.case7_startup_samples(cases, selected, roots, samples, traces)

    def test_wrong_pid_parent_start_or_selection_refuses(self):
        for field, value in [('pid', 21), ('pid', 1), ('pid', '20'), ('parent_pid', 11),
                             ('process_start_ticks', '0'), ('image', '/other/image'),
                             ('image_sha256', 'wrong'), ('environment', {'ORT_DISABLE_TELEMETRY': '0'}),
                             ('argv', ['/other/image'])]:
            with self.subTest(field=field, value=value):
                fixture = self.fixture()
                fixture[0][0]['phases'][0]['startup_worker_observation'][field] = value
                with self.assertRaises(RuntimeError):
                    real.case7_startup_samples(*fixture)

    def test_own_kernel_mapping_required(self):
        for field, value in [('image_inode', 456), ('map_files_target', '/other/image'),
                             ('maps', ''), ('mapped_image', '1000-2000 r--p 0000 00:01 123 /selected/python')]:
            with self.subTest(field=field):
                fixture = self.fixture()
                fixture[0][0]['phases'][0]['startup_worker_observation'][field] = value
                with self.assertRaisesRegex(RuntimeError, 'kernel executable mapping'):
                    real.case7_startup_samples(*fixture)

    def test_observation_must_fit_actual_call(self):
        for field, value in [('started_at', '2026-11-12T09:59:59Z'),
                             ('completed_at', '2026-11-12T10:00:03Z'),
                             ('completed_at', '2026-11-12T10:00:00.050Z')]:
            with self.subTest(field=field):
                fixture = self.fixture()
                fixture[0][0]['phases'][0]['startup_worker_observation'][field] = value
                with self.assertRaisesRegex(RuntimeError, 'outside the live cold call'):
                    real.case7_startup_samples(*fixture)

    def test_same_pid_canonical_exec_and_image_trace_required(self):
        for trace in ['', 'execve("/selected/python-embed", ["/selected/python-embed", "alias"], []) = 0\n',
                      'execve("/selected/python-embed", ["/selected/python-embed"], []) = 0\n',
                      'execve("/other/worker", [], []) = 0\n']:
            with self.subTest(trace=trace):
                fixture = self.fixture()
                fixture[-1][20] = trace
                with self.assertRaisesRegex(RuntimeError, 'same-PID'):
                    real.case7_startup_samples(*fixture)

    def test_full_sample_does_not_replace_missing_early_measurement(self):
        fixture = self.fixture()
        fixture[3]['python'].add(20)
        fixture[0][0]['phases'][0].pop('startup_worker_observation')
        with self.assertRaises(KeyError):
            real.case7_startup_samples(*fixture)

    def test_duplicate_or_missing_phase_designation_refuses(self):
        fixture = self.fixture()
        fixture[0][0]['phases'].append(copy.deepcopy(fixture[0][0]['phases'][0]))
        with self.assertRaisesRegex(RuntimeError, 'one designated cold phase'):
            real.case7_startup_samples(*fixture)


class SamplerFailureCustodyTests(unittest.TestCase):
    def invoke(self, artifacts, *, wait_timeout=False, exits=False, pid=42, case='7', pgid=42,
               write_failure=False, stuck_join=False, interrupt=False):
        entered = threading.Event()
        process = SimpleNamespace(pid=pid, returncode=None, waits=[], terminated=0, killed=0,
                                  visible_before_wait=False, interrupted=False)

        def poll():
            if not entered.wait(2):
                raise AssertionError('sampler did not reach the injected observer failure')
            if interrupt and not process.interrupted:
                process.interrupted = True
                raise KeyboardInterrupt('owner cancellation')
            if exits:
                process.returncode = 0
            return process.returncode

        def wait(timeout=None):
            process.waits.append(timeout)
            path = artifacts / 'sampler-before-cleanup.json'
            process.visible_before_wait = path.exists() and 'SAMPLER_PRIMARY_SENTINEL' in path.read_text()
            if wait_timeout and not process.killed:
                raise subprocess.TimeoutExpired(['owned fixture strace'], timeout)
            process.returncode = -9 if process.killed else 0
            return process.returncode

        def terminate():
            process.terminated += 1

        def kill():
            process.killed += 1

        process.poll, process.wait, process.terminate, process.kill = poll, wait, terminate, kill

        def fail_snapshot(*args, **kwargs):
            entered.set()
            raise RuntimeError('SAMPLER_PRIMARY_SENTINEL')

        python = SimpleNamespace(observer_controls=lambda path: None, mapped_worker=lambda *args: None,
                                 process_terminal=lambda path: False)
        rust = SimpleNamespace(descendant_of=lambda *args: False, process_snapshot=fail_snapshot)
        modules = {'transition_python_observer': python, 'transition_python_environment': SimpleNamespace(),
                   'transition_rust_observer': rust}
        stderr = io.StringIO()
        join_timeouts = []

        class StuckThread:
            def __init__(self, target, **kwargs):
                self.target = target
                self.daemon = kwargs.get('daemon', False)

            def start(self):
                self.target()

            def join(self, timeout=None):
                join_timeouts.append(timeout)

            def is_alive(self):
                return True

        original_write = Path.write_text

        def write(path, text, *args, **kwargs):
            if write_failure and path.name.startswith('sampler-error-'):
                raise OSError('DIAGNOSTIC_DISK_FAILURE')
            return original_write(path, text, *args, **kwargs)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(real, 'load', side_effect=lambda name, path: modules[name]))
            stack.enter_context(mock.patch.object(real.subprocess, 'Popen', return_value=process))
            group = stack.enter_context(mock.patch.object(real.os, 'killpg'))
            stack.enter_context(mock.patch.object(real.os, 'getpgid', return_value=pgid))
            stack.enter_context(mock.patch('sys.stderr', stderr))
            stack.enter_context(mock.patch.object(Path, 'write_text', write))
            if stuck_join:
                stack.enter_context(mock.patch.object(real.threading, 'Thread', StuckThread))
            try:
                real.run_observed(['owned fixture command'], {'CAIRN_TRANSITION_REAL_CASE': case}, artifacts,
                                  {'trace_prefix': str(artifacts / 'worker-trace')},
                                  {'payload': {'path': '/selected', 'descriptor': {'entries': {}}}}, Path('/rust'))
            except BaseException as error:
                failure = error
            else:
                self.fail('sampler failure became a successful proof')
            return process, failure, stderr.getvalue(), group.call_args_list, join_timeouts

    def test_sampler_traceback_survives_child_wait_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            child, error, stderr, groups, _ = self.invoke(artifacts, wait_timeout=True)
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
            self.assertIn('Traceback (most recent call last)', str(error))
            self.assertIn('TimeoutExpired', str(error))
            self.assertTrue(child.visible_before_wait, 'primary must be retained before wait can fail')
            observed = json.loads((artifacts / 'real-worker-observations.json').read_text())
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', observed['sampler_failures'][0]['traceback'])
            self.assertIn('TimeoutExpired', str(observed['cleanup_errors']))
            self.assertFalse(observed['proof_complete'])
            self.assertEqual(child.terminated, 1)
            self.assertEqual(child.killed, 1)
            self.assertTrue(all(timeout is not None and 0 < timeout <= 10 for timeout in child.waits))
            self.assertEqual(groups, [])

    def test_case8_sampler_failure_uses_only_owned_child_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            child, error, _, groups, _ = self.invoke(Path(directory), case='8', wait_timeout=True)
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
            self.assertIn('TimeoutExpired', str(error))
            self.assertEqual((child.terminated, child.killed), (1, 1))
            self.assertEqual(groups, [])
            self.assertTrue(all(timeout is not None and 0 < timeout <= 10 for timeout in child.waits))

    def test_error_arriving_as_child_exits_is_retained_and_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            child, error, _, _, _ = self.invoke(artifacts, exits=True)
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
            first = json.loads((artifacts / 'sampler-error-0001.json').read_text())
            self.assertIn('fail_snapshot', first['traceback'])
            self.assertEqual(child.terminated, 0)
            observed = json.loads((artifacts / 'real-worker-observations.json').read_text())
            self.assertEqual(observed['returncode'], 0)
            self.assertFalse(observed['proof_complete'])
            self.assertTrue(observed['sampler_thread_joined'])

    def test_diagnostic_write_failure_is_reported_with_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            _, error, stderr, _, _ = self.invoke(artifacts, write_failure=True)
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
            self.assertIn('DIAGNOSTIC_DISK_FAILURE', str(error))
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', stderr)
            observed = json.loads((artifacts / 'real-worker-observations.json').read_text())
            self.assertIn('DIAGNOSTIC_DISK_FAILURE', str(observed['diagnostic_write_errors']))

    def test_unsafe_process_ids_never_receive_process_control(self):
        for pid in (0, 1):
            with self.subTest(pid=pid), tempfile.TemporaryDirectory() as directory:
                child, error, _, groups, _ = self.invoke(Path(directory), pid=pid)
                self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
                self.assertEqual((child.terminated, child.killed, child.waits, groups), (0, 0, [], []))

    def test_unsafe_group_ids_are_never_signalled(self):
        for pgid in (0, 1, 99):
            with self.subTest(pgid=pgid), tempfile.TemporaryDirectory() as directory:
                _, error, _, groups, _ = self.invoke(Path(directory), case='1', pgid=pgid)
                self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
                self.assertEqual(groups, [])

    def test_sampler_join_is_bounded_and_timeout_is_secondary(self):
        with tempfile.TemporaryDirectory() as directory:
            _, error, _, _, joins = self.invoke(Path(directory), stuck_join=True)
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', str(error))
            self.assertIn('sampler thread did not join', str(error))
            self.assertTrue(joins and all(timeout is not None and 0 < timeout <= 10 for timeout in joins))

    def test_keyboard_interrupt_keeps_its_type_after_diagnostic_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            _, error, _, _, _ = self.invoke(artifacts, interrupt=True)
            self.assertIsInstance(error, KeyboardInterrupt)
            observed = json.loads((artifacts / 'real-worker-observations.json').read_text())
            self.assertIn('SAMPLER_PRIMARY_SENTINEL', observed['sampler_failures'][0]['traceback'])


if __name__ == '__main__':
    unittest.main()
