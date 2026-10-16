import os
import struct
import ctypes
import array
import contextlib
from unittest.mock import patch
import trial_sdk_peer as peer_module
import sys
import socket
import select
import time
import unittest
from trial_sdk_peer import Peer, PeerError, CreditDenied, AUXILIARY

class Ledger:
    def __init__(self): self.used = 0
    def reserve(self,n): self.used += n; return True
    def release(self,n): self.used -= n

class PeerTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, uid_delta=0, reserve=None, release=None, deadline=None):
        Peer.protect()
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        peer = None
        ledger = Ledger()
        try:
            peer = Peer(left, pidfd, os.getpid(), os.getuid()+uid_delta, os.getgid(),
                        deadline or time.monotonic_ns()+2_000_000_000, memoryview(bytearray(512)),
                        reserve or ledger.reserve, release or ledger.release)
            yield peer, right, left, ledger
        finally:
            if peer is not None: peer.close()
            os.close(pidfd)
            left.close(); right.close()

    def fds(self):
        result = set()
        for text in os.listdir('/proc/self/fd'):
            try: os.fstat(int(text))
            except FileNotFoundError: continue
            except OSError as fault:
                if fault.errno == 9: continue
                raise
            result.add(int(text))
        return result

    def test_refused_lengths_and_wrong_credentials_are_terminal_and_erased(self):
        for payload, uid_delta in [(b'',0),(b'x'*31,0),(b'x'*545,0),(b'x'*32,1)]:
            with self.subTest(length=len(payload),uid_delta=uid_delta):
                with self.fixture(uid_delta=uid_delta) as (peer,right,left,ledger):
                    self.assertEqual(right.send(payload), len(payload))
                    output = bytearray(b'x'*544)
                    with self.assertRaisesRegex(PeerError, '^peer_record_refused$'):
                        peer.receive_into(memoryview(output))
                    self.assertEqual(output, bytes(544))
                    self.assertTrue(peer.snapshot()['failed'])
                    with self.assertRaises(PeerError): peer.send(b'Q'*32)
                    self.assertEqual(ledger.used,0)

    def test_rights_and_truncated_rights_are_closed_without_fd_growth(self):
        for count in (1,32):
            with self.subTest(count=count):
                with self.fixture() as (peer,right,left,ledger):
                    source = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
                    try:
                        held = self.fds()
                        self.assertEqual(right.sendmsg([b'Q'*32],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[source]*count))]),32)
                        with self.assertRaisesRegex(PeerError,'^peer_record_refused$'):
                            peer.receive_into(memoryview(bytearray(544)))
                        self.assertEqual(self.fds(),held)
                        self.assertEqual(ledger.used,0)
                    finally: os.close(source)
    def test_actual_credentials_and_owned_duplicate_close(self):
        self.assertEqual(Peer.protect(), os.getpid())
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        peer = None
        try:
            ledger = Ledger()
            scratch = bytearray(512)
            peer = Peer(left, pidfd, os.getpid(), os.getuid(), os.getgid(),
                        time.monotonic_ns()+2_000_000_000, memoryview(scratch),
                        ledger.reserve, ledger.release)
            self.assertEqual(scratch, bytes(512))
            destination = bytearray(544)
            self.assertEqual(peer.receive_into(memoryview(destination)), -1)
            self.assertEqual(right.send(b'Q'*32), 32)
            self.assertEqual(peer.receive_into(memoryview(destination)), 32)
            self.assertEqual(destination[:32], b'Q'*32)
            self.assertEqual(peer.send(b'R'*33), 33)
            self.assertEqual(right.recv(544), b'R'*33)
            self.assertEqual(ledger.used, 0)
            peer.close()
            os.fstat(pidfd)
            os.fstat(left.fileno())
            self.assertTrue(peer.snapshot()['closed_known'])
        finally:
            if peer is not None: peer.close()
            os.close(pidfd)
            left.close(); right.close()


    def test_denied_auxiliary_credit_does_not_consume_queued_record(self):
        allowed, ledger, calls = [True], Ledger(), []
        def reserve(n):
            calls.append(n)
            return ledger.reserve(n) if allowed[0] else False
        with self.fixture(reserve=reserve,release=ledger.release) as (peer,right,left,unused):
            allowed[0] = False
            right.send(b'Q'*32)
            output = bytearray(544)
            with self.assertRaises(CreditDenied): peer.receive_into(memoryview(output))
            self.assertFalse(peer.snapshot()['failed'])
            self.assertEqual(ledger.used,0)
            allowed[0] = True
            self.assertEqual(peer.receive_into(memoryview(output)),32)
            self.assertEqual(output[:32],b'Q'*32)
            self.assertEqual(ledger.used,0)
            self.assertEqual(calls,[AUXILIARY,AUXILIARY,AUXILIARY])

    def test_release_failure_is_sticky_and_does_not_mask_primary(self):
        for refused in (False,True):
            with self.subTest(refused=refused):
                ledger, calls, active = Ledger(), [], [False]
                def release(n):
                    if not active[0]:
                        ledger.release(n)
                        return
                    calls.append(n)
                    raise OSError('injected ledger uncertainty')
                with self.fixture(reserve=ledger.reserve,release=release) as (peer,right,left,unused):
                    active[0] = True
                    right.send(b'Q'*(31 if refused else 32))
                    wanted = 'peer_record_refused' if refused else 'peer_release_unknown'
                    with self.assertRaisesRegex(PeerError,'^'+wanted+'$'):
                        peer.receive_into(memoryview(bytearray(544)))
                    self.assertEqual(peer.snapshot()['primary'],wanted)
                    self.assertTrue(peer.snapshot()['cleanup_unknown'])
                    with self.assertRaises(PeerError): peer.receive_into(memoryview(bytearray(544)))
                    self.assertEqual(calls,[AUXILIARY])
                    self.assertEqual(ledger.used,AUXILIARY)  # Never guess a refund.

    def test_shared_ledger_callbacks_cannot_reenter_or_retain_a_nested_body(self):
        instance, ledger, held_errors = [None], Ledger(), []
        def nested():
            if instance[0] is None: return  # No endpoint escapes construction.
            raw = bytes(bytearray(b'Z'*32))
            before = sys.getrefcount(raw)
            try: instance[0].send(raw)
            except PeerError as fault: held_errors.append(fault)
            self.assertEqual(str(held_errors[-1]),'peer_busy')
            self.assertEqual(sys.getrefcount(raw),before)
        def reserve(n): nested(); return ledger.reserve(n)
        def release(n): nested(); ledger.release(n)
        with self.fixture(reserve=reserve,release=release) as (peer,right,left,unused):
            instance[0] = peer
            right.send(b'Q'*32)
            self.assertEqual(peer.receive_into(memoryview(bytearray(544))),32)
            self.assertEqual(len(held_errors),2)
            self.assertEqual(ledger.used,0)
            self.assertEqual(peer.send(b'R'*32),32)  # Reentry did not clear outer guard.
            self.assertEqual(right.recv(544),b'R'*32)

    def test_post_send_deadline_failure_retains_actual_remote_effect_no_replay(self):
        deadline = time.monotonic_ns()+2_000_000_000
        with self.fixture(deadline=deadline) as (peer,right,left,ledger):
            with patch.object(peer_module.time,'monotonic_ns',side_effect=[deadline-1,deadline+1]):
                with self.assertRaisesRegex(PeerError,'^peer_boundary$'): peer.send(b'Q'*32)
            self.assertEqual(right.recv(544),b'Q'*32)  # Actual kernel effect despite error.
            self.assertTrue(peer.snapshot()['outcome_unknown'])
            with self.assertRaises(PeerError): peer.send(b'Q'*32)
            right.setblocking(False)
            with self.assertRaises(BlockingIOError): right.recv(544)

    def test_post_receive_deadline_failure_erases_body_and_drops_raw_aliases(self):
        deadline = time.monotonic_ns()+2_000_000_000
        with self.fixture(deadline=deadline) as (peer,right,left,ledger):
            right.send(b'Q'*32)
            output = bytearray(b'x'*544)
            before = sys.getrefcount(output)
            retained = None
            with patch.object(peer_module.time,'monotonic_ns',side_effect=[deadline-1,deadline+1]):
                try: peer.receive_into(memoryview(output))
                except PeerError as fault: retained = fault
            self.assertEqual(str(retained),'peer_boundary')
            self.assertEqual(sys.getrefcount(output),before)
            self.assertEqual(output,bytes(544))
            self.assertTrue(peer.snapshot()['outcome_unknown'])
            self.assertEqual(ledger.used,0)

    def test_changed_credential_option_is_refused_before_read(self):
        with self.fixture() as (peer,right,left,ledger):
            left.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,0)
            right.send(b'Q'*32)
            with self.assertRaisesRegex(PeerError,'^peer_options$'):
                peer.receive_into(memoryview(bytearray(544)))
            self.assertTrue(peer.snapshot()['failed'])
            self.assertEqual(ledger.used,0)

    def test_constructor_wrong_pid_closes_duplicates_and_clears_scratch(self):
        Peer.protect()
        left,right = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        pidfd = os.pidfd_open(os.getpid())
        scratch = bytearray(b'x'*512)
        before = self.fds()
        try:
            with self.assertRaisesRegex(PeerError,'^peer_pidfd_identity$'):
                Peer(left,pidfd,os.getpid()+1,os.getuid(),os.getgid(),
                     time.monotonic_ns()+2_000_000_000,memoryview(scratch),lambda n:True,lambda n:None)
            self.assertEqual(self.fds(),before)
            self.assertEqual(scratch,bytes(512))
        finally: os.close(pidfd);left.close();right.close()


    def test_constructor_info_close_failure_is_reported_as_cleanup_unknown(self):
        Peer.protect()
        left,right = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        pidfd = os.pidfd_open(os.getpid())
        before = self.fds()
        actual_close, calls = os.close, []
        def uncertain(fd):
            actual_close(fd)
            calls.append(fd)
            if len(calls) == 1:
                raise OSError('injected proc-fd close uncertainty')
        try:
            with patch.object(peer_module.os,'close',uncertain):
                with self.assertRaisesRegex(PeerError,'^peer_setup_cleanup_unknown$'):
                    Peer(left,pidfd,os.getpid(),os.getuid(),os.getgid(),
                         time.monotonic_ns()+2_000_000_000,memoryview(bytearray(512)),
                         lambda n:True,lambda n:None)
            self.assertEqual(len(calls),3)  # Info handle and both owned duplicates.
            self.assertEqual(len(set(calls)),3)
            self.assertEqual(self.fds(),before)
        finally: actual_close(pidfd);left.close();right.close()


    def test_uncertain_owned_close_never_closes_a_reused_number(self):
        Peer.protect()
        left,right = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        pidfd = os.pidfd_open(os.getpid())
        before = self.fds()
        instance = Peer(left,pidfd,os.getpid(),os.getuid(),os.getgid(),
                        time.monotonic_ns()+2_000_000_000,memoryview(bytearray(512)),
                        lambda n:True,lambda n:None)
        added = self.fds()-before
        self.assertEqual(len(added),2)
        target = min(added)
        actual_close, calls, replacement = os.close, [], []
        def uncertain(fd):
            calls.append(fd)
            actual_close(fd)
            if fd == target:
                replacement.extend(os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC))
                raise OSError('injected owned-close uncertainty')
        try:
            with patch.object(peer_module.os,'close',uncertain):
                with self.assertRaisesRegex(PeerError,'^peer_close_unknown$'): instance.close()
                with self.assertRaisesRegex(PeerError,'^peer_close_unknown$'): instance.close()
            self.assertEqual(set(calls),added)
            self.assertEqual(len(calls),2)
            self.assertIn(target,replacement)
            for fd in replacement: os.fstat(fd)
            self.assertFalse(instance.snapshot()['closed_known'])
            self.assertTrue(instance.snapshot()['close_unknown'])
        finally:
            for fd in replacement: actual_close(fd)
            actual_close(pidfd);left.close();right.close()


    def test_setup_precharges_both_marshalling_copies_from_same_ledger(self):
        # Worked LP64 Linux model: 112 control bytes twice + 128 address twice.
        self.assertEqual(AUXILIARY,480)
        ledger, reservations, releases = Ledger(), [], []
        def reserve(n): reservations.append(n); return ledger.reserve(n)
        def release(n): releases.append(n); ledger.release(n)
        with self.fixture(reserve=reserve,release=release):
            self.assertEqual(reservations,[480])
            self.assertEqual(releases,[480])
            self.assertEqual(ledger.used,0)


    def test_setup_credit_denial_and_release_uncertainty_never_leak_duplicates(self):
        Peer.protect()
        left,right = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        for endpoint in (left,right): endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        pidfd = os.pidfd_open(os.getpid())
        before = self.fds()
        try:
            releases = []
            with self.assertRaises(CreditDenied):
                Peer(left,pidfd,os.getpid(),os.getuid(),os.getgid(),
                     time.monotonic_ns()+2_000_000_000,memoryview(bytearray(512)),
                     lambda n:False,lambda n:releases.append(n))
            self.assertEqual(releases,[])
            self.assertEqual(self.fds(),before)
            def release(n):
                releases.append(n)
                raise OSError('injected setup release uncertainty')
            with self.assertRaisesRegex(PeerError,'^peer_setup_cleanup_unknown$'):
                Peer(left,pidfd,os.getpid(),os.getuid(),os.getgid(),
                     time.monotonic_ns()+2_000_000_000,memoryview(bytearray(512)),
                     lambda n:True,release)
            self.assertEqual(releases,[AUXILIARY])
            self.assertEqual(self.fds(),before)
            os.fstat(left.fileno());os.fstat(pidfd)  # Caller originals remain owned.
        finally: os.close(pidfd);left.close();right.close()

    def credential_case(self, uid, gid):
        # Pure Linux ABI fixture; no attempt to run under these identities.
        peer = Peer.__new__(Peer)
        peer._pid, peer._uid, peer._gid = 123, uid, gid
        body = struct.pack('iII', 123, uid, gid)
        item = (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, body)
        self.assertEqual(peer._ancillary([item]), (False, False))
        return peer, item

    def test_unsigned_credential_ordinary(self):
        self.credential_case(1000, 2000)

    def test_unsigned_credential_high_uid(self):
        self.credential_case(0x80000000, 2000)

    def test_unsigned_credential_high_gid(self):
        self.credential_case(1000, 0x80000000)

    def test_unsigned_credential_maximum(self):
        self.credential_case(0xffffffff, 0xffffffff)

    def test_credential_mismatch_count_and_width(self):
        peer, item = self.credential_case(1000, 2000)
        cases = [[], [item, item]]
        for pid, uid, gid in [(124,1000,2000),(123,1001,2000),(123,1000,2001)]:
            cases.append([(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,struct.pack('iII',pid,uid,gid))])
        for body in (b'',item[2][:-1],item[2]+b'x'):
            cases.append([(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,body)])
        for case in cases:
            with self.subTest(case=case):self.assertEqual(peer._ancillary(case),(True,False))

    def test_credential_abi_guard(self):
        peer_module._check_credential_abi()
        original = ctypes.sizeof
        def wrong_size(value):
            return 8 if value is ctypes.c_uint else original(value)
        with patch.object(peer_module.ctypes,'sizeof',wrong_size):
            with self.assertRaisesRegex(PeerError,'^peer_credential_abi$'):
                peer_module._check_credential_abi()

    def test_raw_post_send_fault_is_terminal_after_actual_effect(self):
        with self.fixture() as (peer,right,left,ledger):
            actual, calls = peer._live, [0]
            def check():
                calls[0] += 1
                if calls[0] == 1: return actual()
                raise OSError('injected post-send poll failure')
            body = bytes(bytearray(b'Q'*32))
            before = sys.getrefcount(body)
            retained = None
            with patch.object(peer,'_live',check):
                try: peer.send(body)
                except BaseException as fault: retained = fault
            self.assertEqual(right.recv(544),body)  # Actual socket effect before fault.
            self.assertEqual(sys.getrefcount(body),before)
            self.assertIsInstance(retained,PeerError)
            self.assertEqual(str(retained),'peer_post_send_unknown')
            self.assertTrue(peer.snapshot()['failed'])
            self.assertTrue(peer.snapshot()['outcome_unknown'])
            with self.assertRaises(PeerError): peer.send(body)
            right.setblocking(False)
            with self.assertRaises(BlockingIOError): right.recv(544)
            self.assertEqual(ledger.used,0)

    def test_raw_post_receive_fault_is_terminal_and_erased(self):
        with self.fixture() as (peer,right,left,ledger):
            right.send(b'Q'*32)
            actual, calls = peer._live, [0]
            def check():
                calls[0] += 1
                if calls[0] == 1: return actual()
                raise OSError('injected post-receive register failure')
            output = bytearray(b'x'*544)
            before = sys.getrefcount(output)
            retained = None
            with patch.object(peer,'_live',check):
                try: peer.receive_into(memoryview(output))
                except BaseException as fault: retained = fault
            self.assertIsInstance(retained,PeerError)
            self.assertEqual(str(retained),'peer_post_receive_unknown')
            self.assertEqual(output,bytes(544))
            self.assertEqual(sys.getrefcount(output),before)
            self.assertTrue(peer.snapshot()['failed'])
            self.assertTrue(peer.snapshot()['outcome_unknown'])
            with self.assertRaises(PeerError): peer.receive_into(memoryview(output))
            # The first record was actually consumed despite refusal.
            with self.assertRaises(BlockingIOError): left.recv(544,socket.MSG_DONTWAIT)
            self.assertEqual(ledger.used,0)

    def test_control_record_uses_exact_caller_storage_without_bounce(self):
        with self.fixture() as (peer, right, left, ledger):
            backing = bytearray(b'L' + b'x' * 33 + b'R')
            destination = memoryview(backing)[1:-1]
            try:
                self.assertEqual(peer.receive_into(destination), -1)
                self.assertEqual(right.send(b'C' * 33), 33)
                self.assertEqual(peer.receive_into(destination), 33)
                self.assertEqual(backing, b'L' + b'C' * 33 + b'R')
                self.assertEqual(ledger.used, 0)
            finally:
                destination.release()

    def test_header_and_full_record_fit_the_selected_destination(self):
        for width in (32, 544):
            with self.subTest(width=width), self.fixture() as (peer, right, left, ledger):
                backing = bytearray(b'L' + b'x' * width + b'R')
                destination = memoryview(backing)[1:-1]
                try:
                    right.send(b'B' * width)
                    self.assertEqual(peer.receive_into(destination), width)
                    self.assertEqual(backing, b'L' + b'B' * width + b'R')
                    self.assertEqual(ledger.used, 0)
                finally:
                    destination.release()

    def test_invalid_capacity_leaves_record_queued_and_peer_usable(self):
        with self.fixture() as (peer, right, left, ledger):
            right.send(b'C' * 33)
            for width in (31, 545):
                backing = bytearray(b'x' * width)
                destination = memoryview(backing)
                try:
                    with self.assertRaisesRegex(PeerError, '^peer_destination$'):
                        peer.receive_into(destination)
                finally:
                    destination.release()
                self.assertEqual(backing, b'x' * width)
                self.assertFalse(peer.snapshot()['failed'])
                self.assertEqual(ledger.used, 0)
            destination = memoryview(bytearray(33))
            try:
                self.assertEqual(peer.receive_into(destination), 33)
                self.assertEqual(destination, b'C' * 33)
            finally:
                destination.release()

    def test_truncated_control_is_terminal_erased_and_received_rights_closed(self):
        for width in (32, 33):
            for rights in (0, 1, 32):
                with self.subTest(width=width, rights=rights), self.fixture() as (peer, right, left, ledger):
                    source = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
                    destination = None
                    try:
                        before = self.fds()
                        ancillary = [] if not rights else [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                                            array.array('i', [source] * rights))]
                        right.sendmsg([b'B' * (width + 1)], ancillary)
                        backing = bytearray(b'L' + b'x' * width + b'R')
                        destination = memoryview(backing)[1:-1]
                        with self.assertRaisesRegex(PeerError, '^peer_record_refused$'):
                            peer.receive_into(destination)
                        self.assertEqual(backing, b'L' + bytes(width) + b'R')
                        self.assertEqual(self.fds(), before)
                        self.assertEqual(ledger.used, 0)
                        self.assertTrue(peer.snapshot()['failed'])
                        right.send(b'C' * 33)
                        with self.assertRaises(PeerError):
                            peer.receive_into(destination)
                        self.assertEqual(left.recv(544), b'C' * 33)
                        self.assertEqual(self.fds(), before)
                    finally:
                        if destination is not None:
                            destination.release()
                        os.close(source)

    def test_borrow_poll_does_not_consume_record_or_create_descriptors(self):
        before = self.fds()
        with self.fixture() as (peer, right, left, ledger):
            held = self.fds()
            fd = peer.fileno()
            self.assertNotEqual(fd, left.fileno())
            poll = select.poll()
            poll.register(fd, select.POLLIN | select.POLLOUT)
            self.assertTrue(any(flags & select.POLLOUT for _, flags in poll.poll(0)))
            self.assertFalse(any(flags & select.POLLIN for _, flags in poll.poll(0)))
            self.assertEqual(right.send(b'C' * 33), 33)
            self.assertTrue(any(flags & select.POLLIN for _, flags in poll.poll(0)))
            self.assertEqual(peer.fileno(), fd)
            self.assertEqual(self.fds(), held)
            output = bytearray(33)
            self.assertEqual(peer.receive_into(memoryview(output)), 33)
            self.assertEqual(output, b'C' * 33)
            self.assertFalse(any(flags & select.POLLIN for _, flags in poll.poll(0)))
            poll.unregister(fd)
            self.assertEqual(ledger.used, 0)
        self.assertEqual(self.fds(), before)

    def test_reentrant_borrow_refuses_without_clearing_outer_guard(self):
        instance, calls = [None], []
        ledger = Ledger()
        def nested():
            if instance[0] is not None:
                with self.assertRaisesRegex(PeerError, '^peer_busy$'):
                    instance[0].fileno()
                calls.append('busy')
        def reserve(n):
            nested()
            return ledger.reserve(n)
        def release(n):
            nested()
            ledger.release(n)
        with self.fixture(reserve=reserve, release=release) as (peer, right, left, unused):
            instance[0] = peer
            right.send(b'C' * 33)
            self.assertEqual(peer.receive_into(memoryview(bytearray(33))), 33)
            self.assertEqual(calls, ['busy', 'busy'])
            self.assertEqual(ledger.used, 0)
            self.assertGreaterEqual(peer.fileno(), 0)
            self.assertEqual(peer.send(b'R' * 32), 32)
            self.assertEqual(right.recv(544), b'R' * 32)

    def test_closed_borrow_refuses_without_touching_reused_owned_fd(self):
        with self.fixture() as (peer, right, left, ledger):
            fd = peer.fileno()
            peer.close()
            replacement = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                self.assertIn(fd, replacement)
                with self.assertRaisesRegex(PeerError, '^peer_unusable$'):
                    peer.fileno()
                for held in replacement:
                    os.fstat(held)
                self.assertEqual(os.write(replacement[1], b'X'), 1)
                self.assertEqual(os.read(replacement[0], 1), b'X')
                self.assertEqual(ledger.used, 0)
            finally:
                for held in replacement:
                    os.close(held)

    def test_protection_and_expired_deadline_refuse_without_consuming(self):
        for boundary in ('protection', 'deadline'):
            with self.subTest(boundary=boundary), self.fixture() as (peer, right, left, ledger):
                right.send(b'C' * 33)
                if boundary == 'protection':
                    self.assertEqual(peer_module._prctl(4, 1), 0)
                    try:
                        with self.assertRaisesRegex(PeerError, '^peer_boundary$'):
                            peer.fileno()
                    finally:
                        Peer.protect()
                else:
                    # Disclosed clock injection tests the real gate, without waiting out its bound.
                    with patch.object(peer_module.time, 'monotonic_ns', return_value=time.monotonic_ns()+3_000_000_000):
                        with self.assertRaisesRegex(PeerError, '^peer_boundary$'):
                            peer.fileno()
                self.assertTrue(peer.snapshot()['failed'])
                with self.assertRaisesRegex(PeerError, '^peer_boundary$'):
                    peer.fileno()
                self.assertEqual(left.recv(544), b'C' * 33)
                self.assertEqual(ledger.used, 0)

    def test_changed_socket_options_and_failed_peer_refuse(self):
        with self.fixture() as (peer, right, left, ledger):
            left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
            right.send(b'C' * 33)
            with self.assertRaisesRegex(PeerError, '^peer_options$'):
                peer.fileno()
            left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            with self.assertRaisesRegex(PeerError, '^peer_options$'):
                peer.fileno()
            self.assertTrue(peer.snapshot()['failed'])
            self.assertEqual(left.recv(544), b'C' * 33)
            self.assertEqual(ledger.used, 0)

if __name__ == '__main__': unittest.main()
