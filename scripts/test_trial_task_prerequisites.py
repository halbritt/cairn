"""Actual namespace prerequisite execution; no native/provider/store invocation."""
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
import trial_task_eval as te


class PrerequisiteTests(unittest.TestCase):
    def execute(self, root, argv):
        env = dict(HOME=str(Path.home()), PATH='/usr/bin:/bin', LANG='C.UTF-8',
                   FIXTURE_LITERAL='inherited environment')
        coordinator = te.ROOT / 'integrations/lifecycle/coordination.py'
        command = te.sandbox_command(root, 'cairn', [(coordinator, '/tmp/coordination.py', 'ro')],
                                     env, te.prerequisite_command(argv), harness='codex')
        command.insert(1, '--unshare-net')
        return subprocess.run(command, input="fixture stdin", capture_output=True, text=True, timeout=15)

    def test_real_parent_identity_literal_argv_and_inherited_io_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cairn').mkdir()
            literal = ['space separated', '$(touch unexpected)', '; exit 0', '日本語']
            program = '''import json,os,runpy,sys
parent=os.getppid()
assert parent>1, ('invalid parent', parent)
c=runpy.run_path('/tmp/coordination.py')
ref=c['owner_process']({}, {'host_pid':parent})
assert ref['pid']==parent and c['process_alive'](ref)
print(json.dumps(dict(parent=parent,start=ref['start'],args=sys.argv[1:],cwd=os.getcwd(),env=os.environ['FIXTURE_LITERAL'],stdin=sys.stdin.read())))
print('fixture stderr',file=sys.stderr)
sys.exit(23)
'''
            argv = ['python3', '-c', program, *literal]
            before = list(argv)
            done = self.execute(root, argv)
            self.assertEqual(done.returncode, 23, done.stderr)
            observed = json.loads(done.stdout)
            self.assertGreater(observed['parent'], 1)
            self.assertGreater(observed['start'], 0)
            self.assertEqual(observed['args'], literal)
            self.assertEqual(observed['cwd'], str(root / 'cairn'))
            self.assertEqual(observed['env'], 'inherited environment')
            self.assertEqual(observed['stdin'], 'fixture stdin')
            self.assertEqual(done.stderr, 'fixture stderr\n')
            self.assertEqual(argv, before)
            self.assertFalse((root / 'cairn' / 'unexpected').exists())

    def test_normal_success_and_child_signal_are_distinct_terminal_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cairn').mkdir()
            success = self.execute(root, ['python3', '-c', "print('complete')"])
            self.assertEqual((success.returncode, success.stdout, success.stderr), (0, 'complete\n', ''))
            killed = self.execute(root, ['python3', '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGTERM)'])
            self.assertEqual(killed.returncode, 128 + signal.SIGTERM)
            self.assertEqual(killed.stdout, '')


if __name__ == '__main__':
    unittest.main()
