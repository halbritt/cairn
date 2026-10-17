"""RPC recovery failures retain the primary and try independent accounting ports."""
import unittest
from unittest.mock import patch

from test_trial_sdk_engine_memory_rpc_parent import Harness, ARGS, canonical_size_sha256
import trial_sdk_engine_memory_rpc as rpc


class RPCRecoveryTests(unittest.TestCase):
    def test_positive_edge_and_original_error_survive_prefix_freeze_failure(self):
        for direction, expected in (('receive', 76), ('send', 34)):
            with self.subTest(direction=direction):
                h = Harness(self)
                document = dict(operation='search', args=list(ARGS), body=None)
                size, digest = canonical_size_sha256(document)
                h.client._send(rpc.wire.BEGIN, 1, 2_000_000_001, size, bytes.fromhex(digest))
                if direction == 'send':
                    h.rpc.step()
                    h.rpc.step()
                method = 'receive_into' if direction == 'receive' else 'send'
                original = getattr(h.rpc._connection, method)
                primary = RuntimeError('original transport failure')
                def transfer(view):
                    self.assertEqual(original(view), expected)
                    raise primary
                credit = h.rpc._control if direction == 'receive' else h.rpc._stream
                with patch.object(h.rpc._connection, method, side_effect=transfer), \
                        patch.object(h.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
                    with self.assertRaises(RuntimeError) as caught:
                        h.rpc.step()
                self.assertIs(caught.exception, primary)
                self.assertIn('record_prefix_recovery_unknown', primary.cleanup_faults)
                self.assertEqual(h.rpc.snapshot()['primary'], 'RuntimeError')
                self.assertEqual(credit.snapshot()['known'], expected)
                self.assertEqual(h.rpc._connection.snapshot()['last_transfer'][3], expected)
                self.assertIs(h.rpc._allocation, h.allocation)
                self.assertTrue(h.rpc.snapshot()['unknown'])
                before = h.rpc_wire
                with self.assertRaises(rpc.wire.RPCError):
                    h.rpc.step()
                self.assertEqual(h.rpc_wire, before)

    def test_freeze_failure_does_not_replace_original_refusal(self):
        h = Harness(self)
        h.rpc._connection = None
        with patch.object(h.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaisesRegex(rpc.wire.RPCError, '^rpc_not_connected$'):
                h.rpc.step()
        self.assertIn('resource_freeze_unknown', h.rpc.snapshot()['recovery_faults'])
        self.assertTrue(h.rpc.snapshot()['unknown'])
        self.assertIs(h.rpc._allocation, h.allocation)
        self.assertEqual(h.rpc_wire, 0)

    def test_control_failure_still_attempts_final_freeze(self):
        h = Harness(self)
        h.rpc._connection = None
        with patch.object(h.rpc._control, '_poison', side_effect=KeyboardInterrupt('secondary')), \
                patch.object(h.resources, 'freeze', wraps=h.resources.freeze) as freeze:
            with self.assertRaisesRegex(rpc.wire.RPCError, '^rpc_not_connected$'):
                h.rpc.step()
        self.assertGreaterEqual(freeze.call_count, 1)
        self.assertIn('rpc_control_recovery_unknown', h.rpc.snapshot()['recovery_faults'])
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertIs(h.rpc._allocation, h.allocation)

    def test_receipt_recovery_failure_keeps_other_recovery_and_no_replay(self):
        h = Harness(self)
        h.rpc._connection = None
        with patch.object(rpc.custody, 'reconcile_interrupted_transfer',
                          side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaisesRegex(rpc.wire.RPCError, '^rpc_not_connected$'):
                h.rpc.step()
        self.assertIn('rpc_receipt_recovery_unknown', h.rpc.snapshot()['recovery_faults'])
        self.assertTrue(h.rpc._root.unknown)
        self.assertTrue(h.resources.snapshot()['unknown'])
        with self.assertRaisesRegex(rpc.wire.RPCError, '^rpc_parent_unavailable$'):
            h.rpc.step()
        self.assertEqual(h.rpc.snapshot()['primary'], 'RPCError')
        self.assertEqual(h.rpc_wire, 0)


if __name__ == '__main__':
    unittest.main()
