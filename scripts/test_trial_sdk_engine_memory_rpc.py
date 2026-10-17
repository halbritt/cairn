"""Actual codecs, loans and transcript; only transport/readiness edges are inert."""
import hashlib
import sys
import weakref
import socket
import select
import unittest
from unittest.mock import patch

from trial_sdk_ipc import Codec, TABLE_PROFILE_3
from trial_sdk_peer import CREDENTIAL
from trial_sdk_incoming import canonical_chunks
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_engine_memory_rpc_client import ClientQuote, ProducerClient, RPCRefused
import trial_sdk_engine_memory_rpc_wire as w


def observed(codec, transcript, kind, operation, *fields, data=None):
    raw = w.STRUCTS[kind].pack(*fields) if data is None else data
    with memoryview(bytearray(32 + len(raw))) as backing, memoryview(raw) as body:
        frame = codec.encode_into(backing, kind, operation, body)
        try:
            transcript.observe(frame)
        finally:
            frame.release()


class WireTests(unittest.TestCase):
    def test_profile3_literal_and_signed_status_remain_unchanged(self):
        self.assertEqual(dict(TABLE_PROFILE_3), {
            33: ('C2B', 1, 512, True), 34: ('C2B', 0, 0, True),
            65: ('B2C', 1, 512, True), 66: ('B2C', 1, 512, True),
            67: ('B2C', 4, 4, True), 68: ('B2C', 1, 1, True)})
        self.assertEqual(w.STRUCTS[w.STATUS].pack(-17), b'\xff\xff\xff\xef')
        with memoryview(bytearray(36)) as record, memoryview(b'\0' * 4) as body:
            frame = Codec('B2C', lambda n: True, lambda n: None, profile=5).encode_into(
                record, w.STATUS, 1, body)
            try:
                with frame.record as encoded:
                    with self.assertRaisesRegex(ValueError, '^frame_version$'):
                        Codec('B2C', lambda n: True, lambda n: None, profile=3).decode(encoded)
            finally:
                frame.release()

    def test_uncredited_request_and_hash_mismatch_refuse(self):
        for credited in (False, True):
            with self.subTest(credited=credited):
                t = w.Transcript(100)
                send = Codec('C2B', lambda n: True, lambda n: None, profile=5)
                reply = Codec('B2C', lambda n: True, lambda n: None, profile=5)
                observed(send, t, w.BEGIN, 1, 99, 2, hashlib.sha256(b'{}').digest())
                if credited:
                    observed(reply, t, w.REQUEST_CREDIT, 1, 2)
                    observed(send, t, w.REQUEST, 1, data=b'[]')
                    observed(reply, t, w.REQUEST_CREDIT, 1, 0)
                    with self.assertRaisesRegex(w.RPCError, '^rpc_request_digest$'):
                        observed(send, t, w.REQUEST_END, 1)
                else:
                    with self.assertRaisesRegex(w.RPCError, '^rpc_request_credit$'):
                        observed(send, t, w.REQUEST, 1, data=b'{}')
                self.assertTrue(t.failed)


class Peer:
    """Inert external peer with real framing; no process/socket is established."""
    def __init__(self, stdout='', stderr='', raw_stderr=None, code=0, refuse=False):
        self.transcript = w.Transcript(5_000_000_001)
        self.decoder = Codec('C2B', lambda n: True, lambda n: None, profile=5)
        self.encoder = Codec('B2C', lambda n: True, lambda n: None, profile=5)
        self.stdout, self.stderr, self.code = stdout, stderr, code
        self.raw_stderr = len(stderr.encode()) if raw_stderr is None else raw_stderr
        self.refuse = refuse
        self.replies = iter(())
        self.sent = []
        self.again_send = self.again_receive = False
        self.shutdown = False
        self.terminal_reads = 0

    def send(self, record, flags):
        self.sent.append(bytes(record))
        if self.again_send:
            self.again_send = False
            raise BlockingIOError()
        frame = self.decoder.decode(record)
        try:
            self.transcript.observe(frame)
            kind, operation = frame.kind, frame.operation
        finally:
            frame.release()
        if kind in (w.BEGIN, w.REQUEST):
            remaining = self.transcript.request_size - self.transcript.request_count
            self.replies = iter(((w.REQUEST_CREDIT, operation, (min(512, remaining),), None),))
        elif kind == w.REQUEST_END:
            self.replies = self.result(operation)
        elif kind == w.SESSION_END:
            self.replies = iter(((w.SESSION_CLOSED, 0, (), None),))
        return len(record)

    def result(self, operation):
        if self.refuse:
            yield w.REFUSE, operation, (2,), None
            return
        yield w.RESULT_META, operation, (len(self.stdout.encode()), self.raw_stderr,
                                        len(self.stdout.encode()), len(self.stderr.encode())), None
        for kind, text in ((w.STDOUT, self.stdout), (w.STDERR, self.stderr)):
            for start in range(0, len(text), 128):
                yield kind, operation, (), text[start:start + 128].encode()
        yield w.STATUS, operation, (self.code,), None

    def recvmsg_into(self, views, control, flags):
        if self.again_receive:
            self.again_receive = False
            raise BlockingIOError()
        if self.shutdown:
            self.terminal_reads += 1
            return 0, [], 0, None
        kind, operation, fields, data = next(self.replies)
        raw = w.STRUCTS[kind].pack(*fields) if data is None else data
        with memoryview(bytearray(32 + len(raw))) as backing, memoryview(raw) as body:
            frame = self.encoder.encode_into(backing, kind, operation, body)
            try:
                self.transcript.observe(frame)
                with frame.record as record:
                    views[0][:len(record)] = record
                    count = len(record)
            finally:
                frame.release()
        if kind == w.SESSION_CLOSED:
            self.shutdown = True
        return count, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                        CREDENTIAL.pack(123, 1000, 1000))], 0, None

    def fileno(self): return 123
    def detach(self): return 123


class Ready:
    def register(self, fd, events): pass
    def poll(self, timeout): return [(123, select.POLLIN | select.POLLHUP)]


class ClientTests(unittest.TestCase):
    def test_request_end_follows_actual_canonical_generator_and_chunk_release(self):
        # The parent starts its own canonical rehash upon END. Both endpoints
        # must not retain their quoted canonical scratch at that same instant.
        observed = dict(generator=None, end=False)
        test = self
        class ReleasePeer(Peer):
            def send(self, record, flags):
                if record[5] == w.REQUEST_END:
                    observed['end'] = True
                    test.assertIsNotNone(observed['generator'])
                    generator = observed['generator']()
                    test.assertTrue(generator is None or generator.gi_frame is None)
                    caller = sys._getframe()
                    while caller.f_code is not ProducerClient.call.__code__:
                        caller = caller.f_back
                    test.assertIsNone(caller.f_locals['chunks'])
                    test.assertIsNone(caller.f_locals['chunk'])
                return super().send(record, flags)
        def trace(frame, event, arg):
            if frame.f_code is canonical_chunks.__code__:
                caller = frame.f_back
                if (event == 'call' and caller.f_code is ProducerClient.call.__code__
                        and observed['generator'] is None):
                    observed['generator'] = weakref.ref(caller.f_locals['chunks'])
            return trace
        client = self.client(ReleasePeer('ok'))
        sys.settrace(trace)
        try:
            self.assertEqual(client.call('search', ('\x00' * 19065,)).stdout, 'ok')
        finally:
            sys.settrace(None)
        self.assertTrue(observed['end'])

    def client(self, peer, clock=lambda: 1):
        client = ProducerClient(5_000_000_001, ClientQuote(34848, 64 * 1024 * 1024), clock=clock)
        transport = RecordSocket()
        transport._socket, transport._credentials = peer, (123, 1000, 1000)
        transport._reserve, transport._release = client._reserve_auxiliary, client._release_auxiliary
        transport._live = transport._options = lambda: None
        client._connection = transport
        return client

    def test_replacement_raw_counts_nonzero_status_and_two_calls(self):
        peer = Peer('answer', '\ufffd', raw_stderr=1, code=17)
        client = self.client(peer)
        for _ in range(2):
            outcome = client.call('search', ('query',), timeout=2)
            self.assertEqual((outcome.returncode, outcome.stdout, outcome.stderr,
                              outcome.stdout_bytes, outcome.stderr_bytes), (17, 'answer', '\ufffd', 6, 1))
        self.assertEqual(client.transcript.operation, 2)
        self.assertEqual(client.transcript.wire_sizes, (6, 3))

    def test_large_paired_streams_are_decoded_without_document_cap(self):
        client = self.client(Peer('x' * 140000, '\ufffd' * 60000, raw_stderr=60000))
        outcome = client.call('search')
        self.assertEqual((outcome.stdout_bytes, outcome.stderr_bytes), (140000, 60000))
        self.assertEqual((len(outcome.stdout), len(outcome.stderr)), (140000, 60000))

    def test_eagain_keeps_identical_frame_then_waits_for_actual_terminal_eof(self):
        peer = Peer('ok')
        peer.again_send = peer.again_receive = True
        client = self.client(peer)
        with patch('select.poll', Ready), patch('os.close'):
            self.assertEqual(client.call('search').stdout, 'ok')
            client.close()
        self.assertEqual(peer.sent[0], peer.sent[1])
        self.assertEqual(peer.terminal_reads, 1)
        self.assertTrue(client.snapshot()['closed'])
        self.assertTrue(client.snapshot()['connection']['terminal_eof'])

    def test_zero_call_and_known_refusal_both_use_end_ack_eof(self):
        for refused in (False, True):
            with self.subTest(refused=refused):
                peer = Peer(refuse=refused)
                client = self.client(peer)
                with patch('select.poll', Ready), patch('os.close'):
                    if refused:
                        with self.assertRaisesRegex(RPCRefused, '^rpc_refused_2$'):
                            client.call('search')
                    client.close()
                self.assertTrue(client.snapshot()['closed'])
                self.assertEqual(peer.terminal_reads, 1)

    def test_expired_call_does_not_emit_and_client_failure_is_sticky(self):
        times = iter((1, 2_000_000_001))
        peer = Peer()
        client = self.client(peer, lambda: next(times))
        with self.assertRaisesRegex(w.RPCError, '^rpc_deadline$'):
            client.call('search', timeout=1)
        self.assertEqual(peer.sent, [])
        with self.assertRaisesRegex(w.RPCError, '^rpc_client_unavailable$'):
            client.call('search')


if __name__ == '__main__':
    unittest.main()
