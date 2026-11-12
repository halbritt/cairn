"""Kernel evidence must bind a completed exec to its own process and argv."""
import unittest
import struct
from unittest import mock

from check_r04_native_opencode import successful_execs
from check_r04_native_agy import TracedProcess
from r04_agy_trace import exec_lines, file_read, hook_writes, io_bytes, joined_pids, loopback_connections, opened_paths, thread_pids, walked_path


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


class AgyTraceTests(unittest.TestCase):
    def test_hex_exec_and_producer_input_are_independently_decoded(self):
        trace = r'''7 execve("\x2f\x78", ["\x2f\x78"], 0x1) = 0
7 write(8, "\x7b\x22\x63\x6f\x6e\x76\x65\x72\x73\x61\x74\x69\x6f\x6e\x49\x64\x22\x3a\x22\x69\x64\x22\x2c\x22\x77\x6f\x72\x6b\x73\x70\x61\x63\x65\x50\x61\x74\x68\x73\x22\x3a\x5b\x5d\x2c\x22\x68\x6f\x6f\x6b\x45\x76\x65\x6e\x74\x4e\x61\x6d\x65\x22\x3a\x22\x53\x74\x6f\x70\x22\x7d", 66) = 66
'''
        self.assertEqual(list(successful_execs(exec_lines(trace))), [dict(pid='7', path='/x', argv=['/x'])])
        records = hook_writes(trace, ['7'])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['payload'], dict(conversationId='id', workspacePaths=[], hookEventName='Stop'))

    def test_receipt_walk_binds_parent_descriptors_and_flags(self):
        trace = '''7 openat(AT_FDCWD, "/", O_RDONLY|O_DIRECTORY|O_CLOEXEC) = 3
7 openat(3, "owned", O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC) = 4
7 close(3) = 0
7 openat(4, "receipt.json", O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC) = -1 ENOENT (No such file or directory)
'''
        self.assertEqual(walked_path(trace, ['7'], 'receipt.json'), '/owned/receipt.json')
        with self.assertRaisesRegex(AssertionError, 'already closed'):
            walked_path(trace.replace('7 openat(4,', '7 close(4) = 0\n7 openat(4,'), ['7'], 'receipt.json')

    def test_kernel_thread_group_includes_shared_threads_and_excludes_fork(self):
        trace = '''7 clone(child_stack=NULL, flags=CLONE_VM|CLONE_FILES|CLONE_THREAD) = 8
8 clone3({flags=CLONE_VM|CLONE_THREAD}, 88) = 9
8 clone(child_stack=NULL, flags=CLONE_VM|CLONE_VFORK) = 10
10 clone(child_stack=NULL, flags=CLONE_THREAD) = 11
9 read(0, "abc", 4096) = 3
10 read(0, "foreign", 4096) = 7
'''
        members = thread_pids(trace, '7')
        self.assertEqual(members, ['7', '8', '9'])
        self.assertEqual(io_bytes(trace, members, 0, 'read'), b'abc')

    def test_selected_file_read_excludes_reused_descriptor_bytes(self):
        trace = '''7 read(4, "old", 3) = 3
7 openat(3, "receipt.json", O_RDONLY|O_NOFOLLOW) = 4
7 read(4, "abc", 4096) = 3
7 read(4, "", 4096) = 0
7 close(4) = 0
7 read(4, "later", 5) = 5
'''
        self.assertEqual(file_read(trace, '7', 'receipt.json'), dict(open_error=None, raw=b'abc'))
        missing = '7 openat(3, "receipt.json", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
        self.assertEqual(file_read(missing, '7', 'receipt.json'),
                         dict(open_error='ENOENT (No such file or directory)', raw=b''))
        with self.assertRaisesRegex(AssertionError, 'not closed'):
            file_read(trace.split('7 close')[0], '7', 'receipt.json')
    def test_cleanup_rejects_unsafe_pids_without_sending_signals(self):
        child = object.__new__(TracedProcess)
        for pid in (None, -1, 0, 1):
            child.native_pid = pid
            with self.subTest(pid=pid), mock.patch('check_r04_native_agy.os.kill') as kill:
                with self.assertRaisesRegex(AssertionError, 'unsafe native PID'):
                    child.signal_native(15)
                kill.assert_not_called()

    def test_cleanup_requires_native_identity_and_signals_only_that_pid(self):
        child = object.__new__(TracedProcess)
        child.native_pid, child.native_exe = 9, '/owned/agy'
        with mock.patch('check_r04_native_agy.os.kill') as kill, \
             mock.patch('check_r04_native_agy.os.readlink', return_value='/other') as readlink:
            with self.assertRaisesRegex(AssertionError, 'identity changed'):
                child.signal_native(15)
            kill.assert_not_called()
            readlink.return_value = child.native_exe
            child.signal_native(15)
            kill.assert_called_once_with(9, 15)

    def test_hook_input_reassembles_only_its_pid_and_fd(self):
        trace = '''7 read(0, "{\\"conversationId\\":", 4096) = 18
8 read(0, "foreign", 4096) = 7
7 read(4, "unrelated", 4096) = 9
7 read(0,  <unfinished ...>
8 read(0, "other", 4096) = 5
7 <... read resumed>"\\"native-id\\"}", 4096) = 12
7 read(0, "", 4096) = 0
7 +++ exited with 0 +++
'''
        self.assertEqual(io_bytes(trace, '7', 0, 'read'), b'{"conversationId":"native-id"}')

    def test_stderr_preserves_c_escapes_and_partial_write_count(self):
        trace = r'''7 write(2, "refused: caf\303\251\n", 15) = 15
7 write(2, "suffix", 6) = 3
7 +++ exited with 1 +++
'''
        self.assertEqual(io_bytes(trace, '7', 2, 'write'), 'refused: café\nsuf'.encode())

    def test_incomplete_or_truncated_input_is_not_evidence(self):
        for trace in ('7 read(0, "abc", 100) = 8\n', '7 read(0, "abc"..., 100) = 8\n',
                      '7 read(0,  <unfinished ...>\n', '7 <... read resumed>"abc", 100) = 3\n'):
            with self.subTest(trace=trace), self.assertRaises(AssertionError):
                io_bytes(trace, '7', 0, 'read')

    def test_join_requires_exit_records_for_every_traced_pid(self):
        trace = '7 read(0, "", 1) = 0\n8 +++ killed by SIGTERM +++\n'
        with self.assertRaisesRegex(AssertionError, '7'):
            joined_pids(trace)
        self.assertEqual(joined_pids(trace + '7 +++ exited with 0 +++\n'), ['7', '8'])
        with self.assertRaisesRegex(AssertionError, '9'):
            joined_pids('7 clone(child_stack=NULL, flags=CLONE_VM) = 9\n7 +++ exited with 0 +++\n')

    def test_native_open_and_even_failed_external_connects_are_checked(self):
        trace = '''7 openat(AT_FDCWD, "/owned/.agents/hooks.json", O_RDONLY|O_CLOEXEC) = 4
8 openat(AT_FDCWD, "/other", O_RDONLY) = 5
7 connect(4, {sa_family=AF_INET, sin_port=htons(1234), sin_addr=inet_addr("127.0.0.1")}, 16) = 0
'''
        self.assertEqual(opened_paths(trace, '7'), ['/owned/.agents/hooks.json'])
        self.assertEqual(len(loopback_connections(trace, provider_port=1234)['network_connects']), 1)
        with self.assertRaisesRegex(AssertionError, 'Non-loopback'):
            loopback_connections(trace + '7 connect(4, {sa_family=AF_INET, sin_addr=inet_addr("192.0.2.1")}, 16) = -1 ENETUNREACH\n', provider_port=1234)
        with self.assertRaisesRegex(AssertionError, 'unselected-port'):
            loopback_connections(trace, provider_port=4321)

    def test_only_absent_exact_nss_socket_is_classified_separately(self):
        trace = '7 connect(4, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT (No such file or directory)\n'
        self.assertEqual(len(loopback_connections(trace, provider_port=1234)['unavailable_local_nss']), 1)
        for changed in (trace.replace('/var/run/nscd/socket', '/other'),
                        trace.replace('ENOENT', 'EACCES'), trace.replace('-1 ENOENT (No such file or directory)', '0')):
            with self.subTest(trace=changed), self.assertRaisesRegex(AssertionError, 'Unexpected local'):
                loopback_connections(changed, provider_port=1234)

    def test_dns_decode_follows_shared_fd_lifetime(self):
        raw = struct.pack('!6H', 123, 0x100, 1, 0, 0, 0) + b'\x07example\x04test\x00' + struct.pack('!HH', 1, 1)
        quoted = '"' + ''.join('\\x%02x' % byte for byte in raw) + '"'
        trace = ('7 clone(child_stack=NULL, flags=CLONE_FILES|CLONE_THREAD) = 8\n'
                 '7 connect(4, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n'
                 f'8 write(4, {quoted}, {len(raw)}) = {len(raw)}\n'
                 '8 close(4) = 0\n8 write(4, "foreign", 7) = 7\n')
        result = loopback_connections(trace, provider_port=1234)
        self.assertEqual(result['dns_counts'], [dict(name='example.test', qtype=1, count=1)])
        self.assertEqual(result['unanswered_dns_queries'][0]['pid'], '8')


if __name__ == '__main__':
    unittest.main()
