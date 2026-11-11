"""Kernel evidence must bind a completed exec to its own process and argv."""
import unittest

from check_r04_native_opencode import successful_execs


class NativeTraceTests(unittest.TestCase):
    def test_interleaved_execs_keep_process_and_literal_arguments(self):
        # Shape observed in the native fixture: the launcher returns from
        # execve after another child's call has already been printed.
        trace = '''3544  execve("/owned/a launcher", ["/owned/a launcher", "hook", "--config", "/owned/profile"], 0x12 /* 17 vars */ <unfinished ...>
3550  execve("/owned/other", ["/owned/other"], 0x13 /* 17 vars */) = 0
3544  <... execve resumed>)             = 0
3544  execve("/owned/image", ["/owned/image", "coordination", "--", "hook"], 0x12 /* 17 vars */) = 0
3544  +++ exited with 0 +++
'''
        records = list(successful_execs(trace))
        self.assertEqual(records, [dict(pid='3550', path='/owned/other', argv=['/owned/other']),
                                   dict(pid='3544', path='/owned/a launcher',
                                        argv=['/owned/a launcher', 'hook', '--config', '/owned/profile']),
                                   dict(pid='3544', path='/owned/image', argv=['/owned/image', 'coordination', '--', 'hook'])])

    def test_failed_exec_and_later_exit_are_not_execution_evidence(self):
        trace = '''7 execve("/absent", ["/absent"], 0x1) = -1 ENOENT (No such file or directory)
7 +++ exited with 0 +++
8 execve("/denied", ["/denied"], 0x2 <unfinished ...>
8 <... execve resumed>) = -1 EACCES (Permission denied)
'''
        self.assertEqual(list(successful_execs(trace)), [])

    def test_missing_half_or_cross_process_resume_refuses(self):
        for trace in ('7 execve("/owned/a", ["/owned/a"], 0x1 <unfinished ...>\n',
                      '7 <... execve resumed>) = 0\n',
                      '7 execve("/owned/a", ["/owned/a"], 0x1 <unfinished ...>\n8 <... execve resumed>) = 0\n'):
            with self.subTest(trace=trace), self.assertRaises(AssertionError):
                list(successful_execs(trace))


if __name__ == '__main__':
    unittest.main()
