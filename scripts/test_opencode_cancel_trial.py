"""The CAIRN-2 trial workloads and observer report survivors from /proc alone."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import opencode_cancel_trial as trial  # noqa: E402

EXPECTED = dict(tree=4, stubborn=4, escaped=5)


class TrialWorkloads(unittest.TestCase):
    def start(self, mode):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ledger = Path(directory.name) / 'ledger.jsonl'
        tool = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/opencode_cancel_trial.py'),
                                 'workload', mode, str(ledger)],
                                stdout=subprocess.DEVNULL, start_new_session=True)
        # Cleanups run last-in first-out: kill every survivor, then reap the tool.
        self.addCleanup(self.reap, tool)
        self.addCleanup(lambda: trial.cleanup(ledger) if ledger.exists() else None)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if ledger.exists() and len(trial.entries(ledger)) == EXPECTED[mode]:
                break
            time.sleep(0.05)
        self.assertEqual(len(trial.entries(ledger)), EXPECTED[mode])
        self.assertEqual(len(trial.observe(ledger)['survivors']), EXPECTED[mode])
        return tool, ledger

    @staticmethod
    def reap(tool):
        # Bounded even when setup failed before the ledger listed the tool.
        try:
            tool.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(tool.pid, signal.SIGKILL)
            tool.wait(timeout=5)

    def settle(self, ledger, survivors):
        deadline = time.monotonic() + 5
        while sorted(trial.observe(ledger)['survivors']) != sorted(survivors) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(sorted(trial.observe(ledger)['survivors']), sorted(survivors))

    def test_tree_stops_on_group_sigterm(self):
        tool, ledger = self.start('tree')
        os.killpg(tool.pid, signal.SIGTERM)
        self.settle(ledger, [])

    def test_stubborn_child_survives_sigterm_until_killed(self):
        tool, ledger = self.start('stubborn')
        os.killpg(tool.pid, signal.SIGTERM)
        self.settle(ledger, ['child-b'])
        os.killpg(tool.pid, signal.SIGKILL)
        self.settle(ledger, [])

    def test_escapee_survives_group_sigkill_and_is_reported(self):
        tool, ledger = self.start('escaped')
        escapee = next(e for e in trial.entries(ledger) if e['role'] == 'escapee')
        self.assertNotEqual(escapee['pgid'], tool.pid)
        self.assertNotEqual(escapee['sid'], tool.pid)
        os.killpg(tool.pid, signal.SIGKILL)
        self.settle(ledger, ['escapee'])
        self.assertEqual(trial.cleanup(ledger)['survivors'], [])

    def test_reused_pid_is_not_reported_alive(self):
        _, ledger = self.start('tree')
        stale = trial.entries(ledger)[0]
        with open(ledger, 'a') as out:
            out.write('{"pid": %d, "start": %d, "role": "reused", "pgid": 0, "sid": 0}\n'
                      % (os.getpid(), stale['start'] - 1))
        self.assertNotIn('reused', trial.observe(ledger)['survivors'])

    def test_cleanup_never_signals_a_process_with_a_reused_pid(self):
        bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(bystander.wait)
        self.addCleanup(bystander.kill)
        start = trial.identity(bystander.pid)['start']
        trial.kill_verified(dict(pid=bystander.pid, start=start - 1))
        time.sleep(0.2)
        self.assertIsNone(bystander.poll())
        trial.kill_verified(dict(pid=bystander.pid, start=start))
        self.assertEqual(bystander.wait(timeout=5), -signal.SIGKILL)

    def test_unknown_mode_is_refused(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/opencode_cancel_trial.py'),
                                 'workload', 'bogus', os.devnull], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown mode', result.stderr)


if __name__ == '__main__':
    unittest.main()
