"""CAIRN-224: native owners require exact observed process identity."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owner_coordination', ROOT / 'integrations/lifecycle/coordination.py')
coord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coord)


def process(pid, parent, comm, exe):
    return dict(ref=dict(pid=pid, start=pid * 10, state='S', parent=parent, boot='fixture'),
                comm=comm, exe=exe, argv=[])


class OwnerProcessAttribution(unittest.TestCase):
    def resolve(self, tree, configured):
        def view(pid):
            value = tree.get(pid)
            if value is None:
                raise ProcessLookupError(pid)
            return value

        with patch.object(coord, 'proc_view', side_effect=view), patch.object(coord.os, 'getppid', return_value=40):
            return coord.owner_process(dict(process_names=configured), {})

    def test_nearest_configured_child_wins_over_outer_codex(self):
        tree = {
            40: process(40, 30, 'sh', '/usr/bin/dash'),
            30: process(30, 20, 'python3', '/usr/bin/python3'),
            20: process(20, 10, 'codex-app-serve', '/opt/codex/codex-app-server'),
            10: process(10, 1, 'codex', '/opt/codex/codex'),
        }
        self.assertEqual(self.resolve(tree, ['codex-app-server', 'codex'])['pid'], 20)

    def test_truncated_comm_matches_exact_executable_basename(self):
        tree = {40: process(40, 20, 'sh', '/usr/bin/dash'),
                20: process(20, 1, 'codex-app-serve', '/opt/codex/codex-app-server')}
        self.assertEqual(self.resolve(tree, ['codex-app-server'])['pid'], 20)

    def test_prefix_collision_with_wrong_executable_is_skipped(self):
        tree = {
            40: process(40, 30, 'sh', '/usr/bin/dash'),
            30: process(30, 20, 'codex-app-serve', '/opt/tools/codex-app-server-helper'),
            20: process(20, 10, 'python3', '/usr/bin/python3'),
            10: process(10, 1, 'codex', '/opt/codex/codex'),
        }
        self.assertEqual(self.resolve(tree, ['codex-app-server', 'codex'])['pid'], 10)

    def test_vanished_intermediate_process_refuses_owner_attribution(self):
        tree = {40: process(40, 30, 'sh', '/usr/bin/dash'),
                10: process(10, 1, 'codex', '/opt/codex/codex')}
        with self.assertRaisesRegex(coord.CoordinationError, 'exited during owner lookup'):
            self.resolve(tree, ['codex'])


if __name__ == '__main__':
    unittest.main()
