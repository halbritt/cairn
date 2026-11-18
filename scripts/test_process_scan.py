"""Marker scans find escaped tool processes and never report unknown coverage as clear."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import opencode_cancel_trial as trial  # noqa: E402
from integrations.lifecycle import process_scan  # noqa: E402

MARKER = 'CAIRN_REQUEST_ID'
EXPECTED = dict(tree=4, escaped=5)


class MarkerScan(unittest.TestCase):
    def setUp(self):
        self.since = process_scan.start_ticks()

    def start(self, mode, value):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ledger = Path(directory.name) / 'ledger.jsonl'
        tool = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/opencode_cancel_trial.py'),
                                 'workload', mode, str(ledger)], stdout=subprocess.DEVNULL,
                                start_new_session=True, env=dict(os.environ, **{MARKER: value}))
        self.addCleanup(self.reap, tool)
        self.addCleanup(lambda: trial.cleanup(ledger) if ledger.exists() else None)
        deadline = time.monotonic() + 10
        while not (ledger.exists() and len(trial.entries(ledger)) == EXPECTED[mode]):
            self.assertLess(time.monotonic(), deadline, 'workload did not start')
            time.sleep(0.05)
        return tool, ledger

    @staticmethod
    def reap(tool):
        try:
            tool.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(tool.pid, signal.SIGKILL)
            tool.wait(timeout=5)

    def cleared(self, value):
        # Unrelated non-dumpable host processes can start mid-test and make one
        # scan's coverage unknown; they are transient, so a fresh scan settles.
        deadline = time.monotonic() + 5
        while True:
            result = process_scan.markers(MARKER, value, since=self.since)
            if result['clear'] or time.monotonic() > deadline:
                return result
            time.sleep(0.1)

    def found(self, value, count):
        deadline = time.monotonic() + 5
        while True:
            result = process_scan.markers(MARKER, value, since=self.since)
            if len(result['processes']) == count or time.monotonic() > deadline:
                return result
            time.sleep(0.05)

    def test_finds_every_marked_process_and_clears_after_group_stop(self):
        value = str(uuid.uuid4())
        tool, ledger = self.start('tree', value)
        result = self.found(value, 4)
        self.assertEqual(sorted(p['pid'] for p in result['processes']),
                         sorted(e['pid'] for e in trial.entries(ledger)))
        self.assertFalse(result['clear'])
        os.killpg(tool.pid, signal.SIGKILL)
        self.assertEqual(self.found(value, 0)['processes'], [])
        result = self.cleared(value)
        self.assertTrue(result['clear'], result)

    def test_escapee_outside_the_group_is_still_found(self):
        value = str(uuid.uuid4())
        tool, ledger = self.start('escaped', value)
        os.killpg(tool.pid, signal.SIGKILL)
        result = self.found(value, 1)
        escapee = next(e for e in trial.entries(ledger) if e['role'] == 'escapee')
        self.assertEqual([p['pid'] for p in result['processes']], [escapee['pid']])
        self.assertNotEqual(result['processes'][0]['sid'], tool.pid)
        self.assertFalse(result['clear'])
        trial.cleanup(ledger)
        self.assertTrue(self.cleared(value)['clear'])

    def test_other_marker_values_do_not_match(self):
        value = str(uuid.uuid4())
        self.start('tree', value)
        self.assertEqual(self.found(value, 4)['processes'].__len__(), 4)
        other = process_scan.markers(MARKER, str(uuid.uuid4()), since=self.since)
        self.assertEqual(other['processes'], [])

    def test_zombie_is_not_counted(self):
        value = str(uuid.uuid4())
        child = subprocess.Popen([sys.executable, '-c', 'pass'], env=dict(os.environ, **{MARKER: value}))
        deadline = time.monotonic() + 5
        while process_scan._stat(child.pid)['state'] != 'Z':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.assertTrue(self.cleared(value)['clear'])
        child.wait()

    def test_unreadable_process_is_unknown_unless_older_than_the_work(self):
        with tempfile.TemporaryDirectory() as proc:
            for pid, start in ((4242, 500), (4243, 100)):
                (Path(proc) / str(pid)).mkdir()
                (Path(proc) / str(pid) / 'stat').write_text(
                    f'{pid} (tool) S 1 {pid} {pid} 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {start} 0 0\n')
                environ = Path(proc) / str(pid) / 'environ'
                environ.write_bytes(b'')
                environ.chmod(0)
            try:
                if os.access(Path(proc) / '4242' / 'environ', os.R_OK):
                    self.skipTest('running with privileges that ignore file modes')
                everything = process_scan.markers(MARKER, 'anything', proc=proc)
                recent = process_scan.markers(MARKER, 'anything', since=300, proc=proc)
                older = process_scan.markers(MARKER, 'anything', since=600, proc=proc)
            finally:
                for pid in (4242, 4243):
                    (Path(proc) / str(pid) / 'environ').chmod(0o600)
        self.assertEqual(sorted(everything['unknown']), [4242, 4243])
        self.assertEqual(recent['unknown'], [4242])
        self.assertEqual((recent['coverage'], recent['clear']), ('unknown', False))
        self.assertEqual((older['coverage'], older['clear'], older['unknown']), ('complete', True, []))

    def test_invalid_marker_is_refused(self):
        for name, value in (('', 'x'), ('A=B', 'x'), ('A', '')):
            with self.assertRaises(ValueError):
                process_scan.markers(name, value)

    def test_mid_execve_kernel_empty_env_and_fork_behind_cursor(self):
        import shutil
        import threading
        from unittest.mock import patch

        def full_stat(pid, state='S', flags=0x400000, start=500, env_start=0, env_end=0):
            fields = ['0'] * 50
            fields[0], fields[1], fields[2], fields[3] = state, '1', str(pid), str(pid)
            fields[6], fields[19] = str(flags), str(start)
            fields[47], fields[48] = str(env_start), str(env_end)
            return f'{pid} (proc) ' + ' '.join(fields) + '\n'

        with tempfile.TemporaryDirectory() as proc:
            root = Path(proc)
            # 1. Kernel thread + empty-env user process -> clear & complete (never unknown).
            for pid, flags, es, ee in ((501, process_scan.PF_KTHREAD, 0, 0),
                                       (502, 0x400000, 0x7fff1000, 0x7fff1000)):
                d = root / str(pid)
                d.mkdir()
                (d / 'stat').write_text(full_stat(pid, flags=flags, env_start=es, env_end=ee))
                (d / 'environ').write_bytes(b'')
                (d / 'cmdline').write_bytes(b'worker\0')
            res = process_scan.markers(MARKER, 'm1', since=100, proc=proc)
            self.assertEqual((res['clear'], res['coverage'], res['unknown']), (True, 'complete', []))

            # 2. Transient 0-byte mid-execve environ resolves within retry window.
            d503 = root / '503'
            d503.mkdir()
            (d503 / 'stat').write_text(full_stat(503, state='R', env_start=0, env_end=0))
            (d503 / 'environ').write_bytes(b'')
            (d503 / 'cmdline').write_bytes(b'child\0')

            def finish_exec():
                time.sleep(0.003)
                (d503 / 'environ').write_bytes(f'{MARKER}=m2\0'.encode())
                (d503 / 'stat').write_text(full_stat(503, env_start=0x7fff1000, env_end=0x7fff1020))

            t = threading.Thread(target=finish_exec)
            t.start()
            try:
                res2 = process_scan.markers(MARKER, 'm2', since=100, proc=proc)
            finally:
                t.join(timeout=1)
            self.assertEqual([p['pid'] for p in res2['processes']], [503])
            self.assertEqual((res2['clear'], res2['coverage']), (False, 'complete'))
            shutil.rmtree(d503)

            # 3. Stalled mid-execve stays unknown rather than false clear.
            d504 = root / '504'
            d504.mkdir()
            (d504 / 'stat').write_text(full_stat(504, state='D', env_start=0, env_end=0))
            (d504 / 'environ').write_bytes(b'')
            (d504 / 'cmdline').write_bytes(b'')
            res3 = process_scan.markers(MARKER, 'm3', since=100, proc=proc)
            self.assertEqual((res3['clear'], res3['coverage'], res3['unknown']), (False, 'unknown', [504]))
            shutil.rmtree(d504)

            # 4. Parent forks child 500 behind Pass-0 cursor and vanishes before inspection.
            d505 = root / '505'
            d505.mkdir()
            (d505 / 'stat').write_text(full_stat(505, env_start=0x7fff1000, env_end=0x7fff1020))
            (d505 / 'environ').write_bytes(f'{MARKER}=m4\0'.encode())
            (d505 / 'cmdline').write_bytes(b'parent\0')
            orig_read = process_scan._read_environ

            def fork_and_exit(p_root, pid, since):
                if pid == 505:
                    d500 = root / '500'
                    d500.mkdir()
                    (d500 / 'stat').write_text(full_stat(500, env_start=0x7fff1000, env_end=0x7fff1020))
                    (d500 / 'environ').write_bytes(f'{MARKER}=m4\0'.encode())
                    (d500 / 'cmdline').write_bytes(b'forked-child\0')
                    shutil.rmtree(d505)
                    return None, 'gone'
                return orig_read(p_root, pid, since)

            with patch.object(process_scan, '_read_environ', side_effect=fork_and_exit):
                res4 = process_scan.markers(MARKER, 'm4', since=100, proc=proc)
            self.assertEqual([p['pid'] for p in res4['processes']], [500])
            self.assertFalse(res4['clear'])


if __name__ == '__main__':
    unittest.main()
