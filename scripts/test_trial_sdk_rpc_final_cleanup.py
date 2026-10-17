"""Cleanup faults after real protocol progress with inert transport edges."""
import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_trial_sdk_engine_memory_rpc_parent import Harness, ARGS
from trial_sdk_engine_memory_rpc import ProducerRPC
from trial_sdk_engine_memory_rpc_client import ProducerClient
from trial_sdk_incoming import canonical_size_sha256
import trial_sdk_engine_memory_rpc_wire as wire


class CleanupFaults(unittest.TestCase):
    def interrupt_cleanup(self, target, action, assignment='self._busy = False'):
        lines, first = inspect.getsourcelines(target)
        candidates = [first + i for i, line in enumerate(lines)
                      if line.strip() == assignment]
        self.assertEqual(len(candidates), 1)
        hits = []

        def trace(frame, event, arg):
            if (frame.f_code is target.__code__ and event == 'line'
                    and frame.f_lineno == candidates[0]):
                hits.append(True)
                sys.settrace(None)
                raise KeyboardInterrupt('final_cleanup')
            return trace

        sys.settrace(trace)
        try:
            with self.assertRaisesRegex(KeyboardInterrupt, 'final_cleanup'):
                action()
        finally:
            sys.settrace(None)
        self.assertEqual(hits, [True], 'the actual final cleanup must be hit')

    def assert_parent_retained(self, h):
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertIsNotNone(h.rpc._allocation)
        with self.assertRaises(wire.RPCError):
            h.rpc.stream_credit()
        before = h.rpc_wire
        with self.assertRaises(Exception):
            h.rpc.step()
        self.assertEqual(h.rpc_wire, before)

    def test_client_result_cleanup_poison(self):
        h = Harness(self)
        with h.readiness():
            self.interrupt_cleanup(ProducerClient.call,
                                   lambda: h.client.call('search', ARGS))
        self.assertEqual(h.client.transcript.phase, 'idle')
        self.assertGreater(h.rpc_wire, 76)
        self.assertTrue(h.client.snapshot()['failed'])
        self.assertTrue(h.client._root.unknown)
        before = h.rpc_wire
        with self.assertRaises(wire.RPCError):
            h.client.call('search', ARGS)
        self.assertEqual(h.rpc_wire, before)

    def test_client_terminal_cleanup_poison(self):
        h = Harness(self)
        with h.readiness(), patch('os.close'):
            self.interrupt_cleanup(ProducerClient.close, h.client.close)
        self.assertTrue(h.client.snapshot()['connection']['closed_known'])
        self.assertTrue(h.client.snapshot()['failed'])
        before = h.rpc_wire
        with self.assertRaises(wire.RPCError):
            h.client.close()
        self.assertEqual(h.rpc_wire, before)

    def test_client_entry_interrupt_is_sticky_without_wire_effects(self):
        for method in ('call', 'close'):
            with self.subTest(method=method):
                h = Harness(self)
                action = (lambda: h.client.call('search', ARGS)) if method == 'call' else h.client.close
                self.interrupt_cleanup(getattr(ProducerClient, method), action,
                                       'self._busy = True')
                self.assertTrue(h.client.snapshot()['failed'])
                self.assertTrue(h.client._root.unknown)
                self.assertEqual(h.rpc_wire, 0)

    def test_canonical_close_failure_prevents_request_end(self):
        from test_trial_sdk_engine_memory_rpc import ClientTests, Peer
        from trial_sdk_incoming import canonical_chunks
        peer = Peer()
        client = ClientTests().client(peer)

        def interrupted_chunks(document):
            try:
                yield from canonical_chunks(document)
            finally:
                raise KeyboardInterrupt('canonical_close')

        with patch('trial_sdk_engine_memory_rpc_client.canonical_chunks', interrupted_chunks):
            with self.assertRaisesRegex(KeyboardInterrupt, 'canonical_close'):
                client.call('search', ARGS)
        self.assertTrue(client.snapshot()['failed'])
        self.assertTrue(client._root.unknown)
        self.assertGreater(len(peer.sent), 1)
        self.assertNotIn(wire.REQUEST_END, [record[5] for record in peer.sent])

    def test_parent_received_begin_cleanup_poison(self):
        h = Harness(self)
        doc = dict(operation='search', args=list(ARGS), body=None)
        size, digest = canonical_size_sha256(doc)
        h.client._send(wire.BEGIN, 1, 2000000001, size, bytes.fromhex(digest))
        self.interrupt_cleanup(ProducerRPC.step, h.rpc.step)
        self.assertEqual(h.rpc.transcript.request_size, size)
        self.assertEqual(h.rpc._control.snapshot()['known'], 76)
        self.assert_parent_retained(h)

    def test_parent_prestart_cleanup_poison(self):
        h = Harness(self)
        started = []
        listener = SimpleNamespace(start=lambda *args: started.append(True))
        with patch('trial_sdk_engine_memory_rpc.BrokerListener', return_value=listener):
            self.interrupt_cleanup(ProducerRPC.prestart, lambda: h.rpc.prestart('/inert'))
        self.assertEqual(started, [True])
        self.assert_parent_retained(h)

    def test_parent_accept_cleanup_poison(self):
        h = Harness(self)
        h.rpc._connection = None
        accepted = []
        def accept(owner):
            accepted.append(owner)
            return None
        h.rpc._listener = SimpleNamespace(accept=accept)
        self.interrupt_cleanup(ProducerRPC.accept, h.rpc.accept)
        self.assertEqual(accepted, [h.owner])
        self.assert_parent_retained(h)

    def test_parent_operation_handoff_cleanup_poison(self):
        h = Harness(self)
        with h.readiness():
            self.interrupt_cleanup(ProducerRPC.start_operation,
                                   lambda: h.client.call('search', ARGS))
        self.assertIsNotNone(h.rpc._operation)
        self.assert_parent_retained(h)

    def test_parent_keeps_original_primary_when_cleanup_also_fails(self):
        h = Harness(self)
        h.rpc._connection = None
        self.interrupt_cleanup(ProducerRPC.step, h.rpc.step)
        self.assertEqual(h.rpc.snapshot()['primary'], 'RPCError')
        self.assert_parent_retained(h)


if __name__ == '__main__':
    unittest.main()
