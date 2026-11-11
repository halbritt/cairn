import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('real', Path(__file__).resolve().parents[1] / 'semantic_transition_real.py')
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)


class WorkerTraceTests(unittest.TestCase):
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
