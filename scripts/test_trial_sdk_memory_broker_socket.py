"""Pure I/O-boundary faults; fake endpoints do not attest kernel identity."""
import socket
import sys
import unittest
from unittest.mock import patch

import trial_sdk_memory_broker_socket as broker
import trial_sdk_peer as peer


class CleanupInterrupted(BaseException):
    pass


class Endpoint:
    def __init__(self, ancillary=(), flags=0):
        self.ancillary, self.flags = ancillary, flags
        self.receives = self.sends = self.detaches = 0

    def recvmsg_into(self, views, control, flags):
        self.receives += 1
        views[0][:34] = b'x' * 34
        return 34, self.ancillary, self.flags, None

    def detach(self):
        self.detaches += 1
        return 731

    def send(self, view, flags):
        self.sends += 1
        return len(view)


class SocketBoundaryTests(unittest.TestCase):
    def bound(self, endpoint, release=lambda n: None):
        value = broker.RecordSocket()
        value._socket = endpoint
        value._credentials = (123, 1000, 1000)
        value._reserve = lambda n: True
        value._release = release
        # Bypass only host prerequisites: these tests establish no authentication.
        value._live = lambda: None
        value._options = lambda: None
        return value

    def test_consumed_record_parser_interruption_poisoned(self):
        endpoint = Endpoint()
        value = self.bound(endpoint)
        data = bytearray(b'y' * 544)
        with memoryview(data) as view:
            with patch.object(broker, 'check_ancillary', side_effect=CleanupInterrupted):
                with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_receive_unknown$'):
                    value.receive_into(view)
        self.assertEqual(data, bytes(544))
        self.assertEqual(endpoint.receives, 1)
        self.assertTrue(value.snapshot()['outcome_unknown'])
        self.assertIsNotNone(value.snapshot()['primary'])
        self.assertFalse(value._busy)
        with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_receive_unknown$'):
            broker.RecordSocket._live(value)

    def test_every_received_right_closed_despite_one_interrupt(self):
        attempted = []
        def close(fd):
            attempted.append(fd)
            if fd == 501:
                raise CleanupInterrupted
        rights = peer.RIGHT.pack(501) + peer.RIGHT.pack(502)
        credentials = peer.CREDENTIAL.pack(123, 1000, 1000)
        with patch.object(peer.os, 'close', side_effect=close):
            result = peer.check_ancillary([
                (socket.SOL_SOCKET, socket.SCM_RIGHTS, rights),
                (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
                (123, 1000, 1000))
        self.assertEqual(attempted, [501, 502])
        self.assertEqual(result, (True, True))

    def test_release_fault_retains_primary_and_unknown(self):
        def release(n):
            raise CleanupInterrupted
        value = self.bound(Endpoint(), release)
        with memoryview(bytearray(544)) as view:
            with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_record_refused$'):
                value.receive_into(view)
        self.assertEqual(value.snapshot()['primary'], 'broker_record_refused')
        self.assertTrue(value.snapshot()['outcome_unknown'])
        self.assertIn('auxiliary_release_unknown', value.snapshot()['cleanup'])

    def test_close_uncertainty_never_retries_a_raw_number(self):
        endpoint = Endpoint()
        value = self.bound(endpoint)
        value._parent_pidfd = 732
        attempted = []
        def close(fd):
            attempted.append(fd)
            if fd == 731:
                raise InterruptedError
        with patch.object(broker.os, 'close', side_effect=close):
            value.close()
            value.close()
        self.assertEqual(attempted, [731, 732])
        self.assertEqual(endpoint.detaches, 1)
        self.assertFalse(value.snapshot()['closed_known'])

    def test_accept_unexpected_error_is_terminal(self):
        listener = broker.BrokerListener()
        class Accept:
            def accept(self):
                raise CleanupInterrupted
        listener._socket = Accept()
        listener.fileno = lambda: 700
        owner = broker.NativeProcessOwner()
        with patch.object(owner, 'snapshot', return_value={'launched': True, 'primary': None, 'pid': 123}), \
                patch.object(owner, 'observe_wait', return_value=None):
            with self.assertRaises(CleanupInterrupted):
                listener.accept(owner)
            self.assertTrue(listener.snapshot()['accept_consumed'])
            self.assertIsNotNone(listener.snapshot()['primary'])
            with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_accept_consumed$'):
                listener.accept(owner)

    def test_close_interruption_cannot_report_known_or_lose_untouched_pidfd(self):
        value = self.bound(Endpoint())
        value._parent_pidfd = 732
        attempted = []
        interrupted = False
        def trace(frame, event, arg):
            nonlocal interrupted
            if (event == 'line' and frame.f_code is broker._close_handle.__code__
                    and frame.f_locals.get('attribute') == '_socket'
                    and value._socket is None and not attempted and not interrupted):
                interrupted = True
                raise CleanupInterrupted
            return trace
        try:
            with patch.object(broker.os, 'close', side_effect=attempted.append):
                sys.settrace(trace)
                try:
                    value.close()
                except CleanupInterrupted:
                    pass
                finally:
                    sys.settrace(None)
                state = value.snapshot()
                self.assertTrue(interrupted)
                self.assertFalse(state['closed_known'])
                self.assertIn('socket_close_unknown', state['cleanup'])
                value.close()
        finally:
            sys.settrace(None)
        self.assertEqual(attempted.count(732), 1)
        self.assertNotIn(731, attempted)

    def test_listener_closed_before_start_is_consumed_without_effects(self):
        listener = broker.BrokerListener()
        listener.close()
        with patch.object(broker.os, 'open', side_effect=AssertionError('unexpected_open')):
            with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_listener_consumed$'):
                listener.start('/unused', 1, lambda n: True, lambda n: None)

    def test_interruption_after_accepted_send_is_terminal(self):
        endpoint = Endpoint()
        value = self.bound(endpoint)
        interrupted = False
        def trace(frame, event, arg):
            nonlocal interrupted
            if (event == 'line' and frame.f_code is broker.RecordSocket.send.__code__
                    and endpoint.sends and not interrupted):
                interrupted = True
                raise CleanupInterrupted
            return trace
        try:
            with memoryview(bytes(34)) as view:
                sys.settrace(trace)
                try:
                    with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_send_unknown$'):
                        value.send(view)
                finally:
                    sys.settrace(None)
        finally:
            sys.settrace(None)
        self.assertTrue(interrupted)
        self.assertEqual(endpoint.sends, 1)
        self.assertTrue(value.snapshot()['outcome_unknown'])
        with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_send_unknown$'):
            broker.RecordSocket._live(value)

    def test_release_failure_after_good_record_erases_and_is_terminal(self):
        def release(n):
            raise CleanupInterrupted
        credentials = peer.CREDENTIAL.pack(123, 1000, 1000)
        endpoint = Endpoint([(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)])
        value = self.bound(endpoint, release)
        data = bytearray(b'y' * 544)
        with memoryview(data) as view:
            with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_auxiliary_unknown$'):
                value.receive_into(view)
        self.assertEqual(data, bytes(544))
        self.assertTrue(value.snapshot()['outcome_unknown'])

    def test_receive_final_cleanup_interruption_is_terminal(self):
        credentials = peer.CREDENTIAL.pack(123, 1000, 1000)
        endpoint = Endpoint([(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)])
        value = self.bound(endpoint)
        interrupted = False
        def trace(frame, event, arg):
            nonlocal interrupted
            if (event == 'line' and frame.f_code is broker.RecordSocket.receive_into.__code__
                    and endpoint.receives and not value._busy and not interrupted):
                interrupted = True
                raise CleanupInterrupted
            return trace
        try:
            with memoryview(bytearray(544)) as view:
                sys.settrace(trace)
                try:
                    with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_receive_unknown$'):
                        value.receive_into(view)
                finally:sys.settrace(None)
        finally:sys.settrace(None)
        self.assertTrue(interrupted)
        self.assertTrue(value.snapshot()['outcome_unknown'])

    def test_reserve_return_interruption_retains_credit_unknown(self):
        endpoint = Endpoint()
        value = self.bound(endpoint)
        interrupted = False
        def trace(frame, event, arg):
            nonlocal interrupted
            if (event == 'line' and frame.f_code is broker.RecordSocket.receive_into.__code__
                    and frame.f_locals.get('grant') is True
                    and not frame.f_locals.get('held') and not interrupted):
                interrupted = True
                raise CleanupInterrupted
            return trace
        try:
            with memoryview(bytearray(544)) as view:
                sys.settrace(trace)
                try:
                    with self.assertRaisesRegex(broker.BrokerSocketError, '^broker_auxiliary_unknown$'):
                        value.receive_into(view)
                finally:sys.settrace(None)
        finally:sys.settrace(None)
        self.assertTrue(interrupted)
        self.assertEqual(endpoint.receives, 0)
        self.assertTrue(value.snapshot()['outcome_unknown'])
        self.assertIn('auxiliary_reserve_unknown', value.snapshot()['cleanup'])

    def test_known_credit_refusal_does_not_consume_record_or_poison(self):
        endpoint = Endpoint()
        value = self.bound(endpoint)
        value._reserve = lambda n: False
        with memoryview(bytearray(544)) as view:
            with self.assertRaisesRegex(broker.BrokerCreditDenied, '^broker_auxiliary_credit$'):
                value.receive_into(view)
        self.assertEqual(endpoint.receives, 0)
        self.assertIsNone(value.snapshot()['primary'])
        self.assertFalse(value.snapshot()['outcome_unknown'])
        self.assertEqual(value.snapshot()['cleanup'], ())

    def test_factory_return_interruption_cannot_claim_known_close(self):
        value = broker.RecordSocket()
        interrupted = False
        def factory(parent):
            return 733  # Simulated newly acquired raw handle; no real descriptor.
        def trace(frame, event, arg):
            nonlocal interrupted
            if event == 'return' and frame.f_code is factory.__code__:
                interrupted = True
                raise CleanupInterrupted
            return trace
        try:
            with patch.object(broker.os,'getppid',return_value=123), \
                    patch.object(broker,'_prctl',return_value=0), \
                    patch.object(broker.os,'pidfd_open',side_effect=factory), \
                    patch.object(broker.socket,'socket',side_effect=AssertionError('unexpected_socket')):
                sys.settrace(trace)
                try:
                    with self.assertRaises(CleanupInterrupted):
                        value.connect_parent('/unused',1,lambda n:True,lambda n:None)
                finally:sys.settrace(None)
        finally:sys.settrace(None)
        self.assertTrue(interrupted)
        value.close()
        self.assertFalse(value.snapshot()['closed_known'])
        self.assertTrue(value.snapshot()['outcome_unknown'])
        self.assertIn('parent_pidfd_acquire_unknown',value.snapshot()['cleanup'])
        with self.assertRaisesRegex(broker.BrokerSocketError,'^broker_connect_consumed$'):
            value.connect_parent('/unused',1,lambda n:True,lambda n:None)


if __name__ == '__main__':
    unittest.main()
