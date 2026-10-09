"""Real disposable children; no provider, memory service or existing sessions."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from trial_native_capture import capture, process_identity, OwnedProcesses


class CaptureTests(unittest.TestCase):
    def run_child(self, source, timeout=2, input_bytes=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = time.monotonic()
            result = capture([sys.executable, '-u', '-c', source], timeout, root/'timing.json',
                             input_bytes=input_bytes, custody_path=root/'custody.json')
            metadata = json.loads((root/'custody.json').read_text())
            self.assertNotIn('PRIVATE', (root/'custody.json').read_text())
            self.assertNotIn('PRIVATE', (root/'timing.json').read_text())
            self.assertEqual(metadata['stdout_bytes'], len(result[0]))
            self.assertLess(time.monotonic()-start, timeout+5)
            return result, metadata

    def assert_no_live_child(self, pid):
        try:
            self.assertIn(process_identity(pid)['state'], ('Z', 'X'))
        except FileNotFoundError:
            pass

    def test_never_reading_child_does_not_block_large_stdin(self):
        (out, err, code, rows), metadata = self.run_child(
            "import os,time; print(os.getpid()); print('PRIVATE'); time.sleep(30)",
            timeout=.2, input_bytes=b'PRIVATE'*300000)
        self.assertEqual(code, 124)
        self.assertEqual(metadata['classification'], 'timeout')
        self.assertLess(metadata['input_bytes_written'], metadata['input_bytes'])
        self.assertEqual(metadata['observed_returncode'], -signal.SIGTERM)
        self.assertTrue(metadata['leader_reaped'])
        self.assertEqual(metadata['cleanup']['status'], 'observed_clear')
        self.assert_no_live_child(int(out.splitlines()[0]))

    def test_original_bound_input_exceeds_reduced_pipe_capacity_without_hanging(self):
        original_popen=subprocess.Popen
        def small_pipe(*args,**kwargs):
            child=original_popen(*args,**kwargs)
            fcntl.fcntl(child.stdin.fileno(),fcntl.F_SETPIPE_SZ,4096)
            return child
        with patch('trial_native_capture.subprocess.Popen',side_effect=small_pipe):
            (_, _, code, _), metadata = self.run_child(
                "import time; print('ready'); time.sleep(30)", timeout=.2, input_bytes=b'x'*8500)
        self.assertEqual(code,124)
        self.assertEqual(metadata['input_bytes'],8500)
        self.assertLessEqual(metadata['input_bytes_written'],4096)
        self.assertEqual(metadata['observed_returncode'],-signal.SIGTERM)

    def test_concurrent_input_output_and_full_stdin_delivery(self):
        payload = b'x'*300000
        (out, err, code, rows), metadata = self.run_child(
            "import sys,hashlib; sys.stdout.write('y'*200000+'\\n'); sys.stdout.flush(); "
            "print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())", input_bytes=payload)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[1], hashlib.sha256(payload).hexdigest().encode())
        self.assertEqual(metadata['input_bytes_written'], len(payload))
        self.assertTrue(metadata['stdout_eof'] and metadata['stderr_eof'])
        self.assertEqual(len(rows), 2)

    def test_normal_nonzero_is_actual_exit_not_timeout(self):
        (out, err, code, rows), metadata = self.run_child("import sys; print('PRIVATE'); sys.exit(17)")
        self.assertEqual((code, metadata['observed_returncode']), (17, 17))
        self.assertEqual(metadata['classification'], 'completed')
        self.assertEqual(metadata['cleanup']['status'], 'observed_clear')

    def test_actual_124_and_timeout_have_distinct_classification(self):
        (_, _, code, _), metadata = self.run_child("import sys; sys.exit(124)")
        self.assertEqual(code, 124)  # Legacy API intentionally preserved.
        self.assertEqual(metadata['observed_returncode'], 124)
        self.assertEqual(metadata['classification'], 'completed')

    def test_timeout_reaps_term_ignoring_child_with_actual_kill_signal(self):
        (_, _, code, _), metadata = self.run_child(
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready'); time.sleep(30)",
            timeout=.2)
        self.assertEqual(code,124)
        self.assertEqual(metadata['observed_returncode'],-signal.SIGKILL)
        self.assertEqual(metadata['cleanup']['status'],'observed_clear')

    def test_child_closing_stdin_keeps_partial_delivery_explicit(self):
        (_, _, code, _), metadata = self.run_child(
            "import os; os.close(0); print('PRIVATE')", input_bytes=b'x'*2000000)
        self.assertEqual(code,0)
        self.assertLess(metadata['input_bytes_written'],metadata['input_bytes'])
        self.assertEqual(metadata['classification'],'completed')

    def test_many_tiny_lines_hit_metadata_bound_without_unbounded_rows(self):
        (_, _, code, rows), metadata = self.run_child(
            "import sys; sys.stdout.write('\\n'*70000)")
        self.assertEqual(code,125)
        self.assertEqual(metadata['classification'],'timing_limit')
        self.assertEqual(len(rows),65536)

    def test_leader_exit_with_descendant_holding_pipes(self):
        source = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-u','-c',"
                  "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); "
                  "print(p.pid); sys.exit(9)")
        (out, _, code, _), metadata = self.run_child(source, timeout=.2)
        descendant = int(out)
        try:
            self.assertEqual(code, 124)
            self.assertEqual(metadata['observed_returncode'], 9)
            self.assertEqual(metadata['cleanup']['status'], 'observed_clear')
            self.assert_no_live_child(descendant)
        finally:
            self.kill_test_child(descendant)

    def test_leader_exit_with_descendant_that_closes_pipes(self):
        source = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-u','-c','import time; time.sleep(30)'],"
                  "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)")
        (out, _, code, _), metadata = self.run_child(source)
        descendant = int(out)
        try:
            self.assertEqual(code, 0)
            self.assertTrue(any(p['pid'] == descendant for p in metadata['cleanup']['members_before_cleanup']))
            self.assert_no_live_child(descendant)
        finally:
            self.kill_test_child(descendant)

    def test_sampled_escape_is_cleaned_without_claiming_all_descendants(self):
        source = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-u','-c',"
                  "'import os,time; os.setsid(); time.sleep(30)']); print(p.pid); time.sleep(30)")
        (out, _, code, _), metadata = self.run_child(source, timeout=.3)
        descendant = int(out)
        try:
            self.assertEqual(metadata['cleanup']['observed_escaped_count'], 1)
            self.assertFalse(metadata['cleanup']['complete_descendant_custody'])
            self.assert_no_live_child(descendant)
        finally:
            self.kill_test_child(descendant)

    def kill_test_child(self, pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_signal_failure_keeps_actual_exit_and_unknown_cleanup(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        owner = OwnedProcesses(child)
        try:
            # Real child stays live through both cleanup phases; restore effects
            # in finally so fault injection cannot leave a disposable process.
            with patch('trial_native_capture.signal.pidfd_send_signal', side_effect=PermissionError()):
                cleanup = owner.cleanup()
            self.assertEqual(cleanup['status'], 'unknown')
            self.assertIn('observed_members_remain', cleanup['errors'])
            self.assertIn('leader_wait_unknown', cleanup['errors'])
            self.assertIsNone(child.returncode)
        finally:
            owner.close()
            child.kill()
            child.wait(timeout=2)

    def test_nonreaping_exit_observation_preserves_leader_anchor(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
        owner = OwnedProcesses(child)
        try:
            deadline=time.monotonic()+2
            while not owner.leader_exited() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(owner.leader_exited())
            self.assertIsNone(child.returncode)
            self.assertEqual(process_identity(child.pid)['state'],'Z')
            cleanup=owner.cleanup()
            self.assertEqual(child.returncode,0)
            self.assertEqual(cleanup['status'],'observed_clear')
        finally:
            owner.close()
            child.wait(timeout=2)

    def test_changed_identity_never_receives_a_signal(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        owner = OwnedProcesses(child)
        try:
            member=process_identity(child.pid)
            changed=dict(member,start=member['start']+1)
            with patch('trial_native_capture.process_identity',return_value=changed), \
                 patch('trial_native_capture.signal.pidfd_send_signal') as send:
                owner.signal_members([member],signal.SIGTERM)
                send.assert_not_called()
            self.assertIn('descendant_identity_changed',owner.errors)
            self.assertIsNone(child.poll())
        finally:
            owner.close()
            child.kill()
            child.wait(timeout=2)

    def test_missing_actual_wait_status_cannot_fabricate_zero_exit(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
        owner = OwnedProcesses(child)
        try:
            deadline=time.monotonic()+2
            while not owner.leader_exited() and time.monotonic()<deadline:
                time.sleep(.01)
            with patch('trial_native_capture.os.waitpid',side_effect=ChildProcessError()):
                cleanup=owner.cleanup()
            self.assertIsNone(owner.wait_status)
            self.assertIsNone(child.returncode)
            self.assertEqual(cleanup['status'],'unknown')
            self.assertIn('leader_wait_unknown',cleanup['errors'])
        finally:
            owner.close()
            child.wait(timeout=2)

    def test_missing_pidfd_refuses_cleanup_effects(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        owner=None
        try:
            with patch('trial_native_capture.os.pidfd_open',side_effect=AttributeError()), \
                 patch('trial_native_capture.signal.pidfd_send_signal') as send:
                owner=OwnedProcesses(child)
                cleanup=owner.cleanup()
                send.assert_not_called()
            self.assertEqual(cleanup['status'],'unknown')
            self.assertIn('leader_pidfd_unknown',cleanup['errors'])
            self.assertIn('leader_wait_unknown',cleanup['errors'])
            self.assertIsNone(child.returncode)
        finally:
            if owner is not None:
                owner.close()
            child.kill()
            child.wait(timeout=2)

    def test_unbound_reparented_escape_is_not_owned_from_start_time(self):
        source=("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                "start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)")
        child=subprocess.Popen([sys.executable,'-u','-c',source],start_new_session=True,stdout=subprocess.PIPE)
        owner=OwnedProcesses(child)
        escaped=int(child.stdout.readline())
        try:
            deadline=time.monotonic()+2
            while not owner.leader_exited() and time.monotonic()<deadline:
                time.sleep(.01)
            candidate=process_identity(escaped)
            self.assertNotEqual(candidate['ppid'],child.pid)
            with patch('trial_native_capture.signal.pidfd_send_signal', wraps=signal.pidfd_send_signal) as send:
                self.assertFalse(owner.bind_member(candidate))
                owner.signal_members([candidate],signal.SIGTERM)
                self.assertFalse(any(call.args[1] != 0 for call in send.call_args_list))
            self.assertIn('descendant_ancestry_unbound',owner.errors)
            self.assertNotIn((candidate['pid'],candidate['start']),owner.known)
            self.assertNotIn(process_identity(escaped)['state'],('Z','X'))
        finally:
            owner.cleanup()
            owner.close()
            child.stdout.close()
            self.kill_test_child(escaped)

    def test_procfs_failure_is_unknown_even_when_leader_reaped(self):
        with patch('trial_native_capture.process_identity', side_effect=PermissionError()):
            (_, _, code, _), metadata = self.run_child("print('PRIVATE')")
        self.assertEqual(code, 0)
        self.assertTrue(metadata['leader_reaped'])
        self.assertEqual(metadata['cleanup']['status'], 'unknown')
        self.assertIn('leader_identity_unknown', metadata['cleanup']['errors'])

    def test_stderr_tail_is_bounded_and_total_is_not_tail_length(self):
        (_, err, code, _), metadata = self.run_child("import sys; sys.stderr.write('z'*90000)")
        self.assertEqual(code, 0)
        self.assertEqual(len(err), 20000)
        self.assertEqual(metadata['stderr_bytes'], 90000)


if __name__ == '__main__':
    unittest.main()
