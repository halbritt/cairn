"""Run in the unchanged campaign wrapper; native binaries have their own tests."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('offline', ROOT / 'scripts/check_semantic_offline_package.py')
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


class NativeRootTests(unittest.TestCase):
    def test_runtime_has_individual_readonly_files_and_private_writable_home(self):
        bindings = {'/bin/sh': Path('/input/dash'), '/usr/bin/env': Path('/input/env'),
                    '/lib64/ld-linux-x86-64.so.2': Path('/input/loader')}
        argv = offline.native_argv(bindings, Path('/input/selection.json'), ['--mode', 'rank', '--route', 'go'])
        self.assertEqual(argv[0], '/usr/bin/bwrap')
        self.assertIn('--unshare-all', argv)
        self.assertIn('--die-with-parent', argv)
        self.assertIn('--new-session', argv)
        pairs = [(argv[i + 1], argv[i + 2]) for i, arg in enumerate(argv) if arg == '--ro-bind']
        self.assertEqual(pairs, [('/input/dash', '/bin/sh'), ('/input/loader', '/lib64/ld-linux-x86-64.so.2'),
                                 ('/input/env', '/usr/bin/env'), ('/input/selection.json', '/fixture/selection.json')])
        for path in ('/tmp', '/nonexistent'):
            self.assertTrue(any(argv[i:i + 2] == ['--tmpfs', path] for i in range(len(argv) - 1)))
        self.assertTrue(any(argv[i:i + 2] == ['--proc', '/proc'] for i in range(len(argv) - 1)))
        self.assertTrue(any(argv[i:i + 2] == ['--dev', '/dev'] for i in range(len(argv) - 1)))
        self.assertTrue(any(argv[i:i + 2] == ['--remount-ro', '/'] for i in range(len(argv) - 1)))
        self.assertEqual(argv[-5:], ['/fixture/native-fixture', '--mode', 'rank', '--route', 'go'])
        self.assertFalse(any('python' in arg for arg in argv))
        self.assertFalse(any(target in ('/', '/usr', '/usr/bin', '/home', '/work') for _, target in pairs))

    def test_outer_effects_observer_counts_failed_connect_and_store_control(self):
        fixture = offline.fixture_module()
        trace = ('123 connect(7<TCP:[1]>, {sa_family=AF_INET}, 16) = -1 ECONNREFUSED\n'
                 '124 write(9</nonexistent/.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db>, "x", 1) = 1\n'
                 '124 write(1</fixture/output>, "onnxruntime.db", 14) = 14\n')
        effects = fixture.trace_effects(trace)
        self.assertEqual(len(effects['connects']), 1)
        self.assertEqual(len(effects['store_mutations']), 1)


if __name__ == '__main__':
    unittest.main()
