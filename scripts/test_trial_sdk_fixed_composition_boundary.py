"""Executable evidence for the missing authority/allocation composition seams.

Real RPC/client/service codecs and real authored decoder; inert OS edges only.
These are callback-only checks, not a fitted session or an authorized live read.
"""
import json
import unittest
from unittest.mock import patch

from test_trial_sdk_engine_memory_rpc_parent import Harness, RPC_QUOTE
from test_trial_sdk_engine_adapter import engine_module
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_engine_adapter import EngineMemoryAdapter
from trial_sdk_engine_memory_rpc_client import ProducerClient


class CompositionBoundaryTests(unittest.TestCase):
    def test_policy_accepts_shaped_unknown_receipt_not_current_read_authority(self):
        policy = FixedCommandPolicy('/fixed/cairn', '/fixed/socket', '/fixed/token',
                                    'repo', 'task', 'run')
        body = json.dumps(dict(request_id='00000000-0000-4000-8000-000000000001',
                               receipt_id='00000000-0000-4000-8000-000000000002',
                               handle='00000000-0000-4000-8000-000000000003'))
        plan = policy.plan('pull', (), body)
        self.assertEqual(json.loads(plan.body), json.loads(body))
        # No source receipt was issued or held anywhere in this test.
        # Shape validity is deliberately NOT asserted as execution authority.

    def test_numeric_client_quote_constructs_without_a_held_parent_bank(self):
        client = ProducerClient(5_000_000_001, RPC_QUOTE.client, clock=lambda: 1)
        self.assertIsNone(client._connection)
        self.assertFalse(client._closed)
        # Successful construction cannot be used to authorize producer copies.

    def composed(self, stdout):
        h = Harness(self, stdout=stdout, stderr=b'', code=0)
        engine = engine_module()
        config = dict(cairn='/fixed/cairn', socket='/fixed/socket', token_file='/fixed/token',
                      repo='repo', task_id='task', run_id='run', harness='claude',
                      recall_mode='agent_tools', recall_observations=False)
        adapter = EngineMemoryAdapter(engine, config, h.client)
        return h, engine, config, adapter

    def test_real_decoder_retains_first_graph_across_second_client_command(self):
        h, engine, config, adapter = self.composed(
            b'{"ok":true,"data":{"status":"READY","destination":{"name":"hosted"},"marker":"first"}}')
        with h.readiness(), patch('os.close'), adapter:
            memory = engine.Memory(config, 'session')
            first = memory.search('first')
            h.stdout = b'{"ok":true,"data":{"status":"READY","destination":{"name":"hosted"},"marker":"second"}}'
            second = memory.search('second')
            self.assertEqual(first['marker'], 'first')
            self.assertEqual(second['marker'], 'second')
            self.assertIsNot(first, second)
            self.assertIsNone(h.rpc._result)
            self.assertIsNone(h.rpc._operation)
            self.assertEqual(h.gate.snapshot()['hook_searches'], 2)
            h.client.close()
        self.assertEqual(first['marker'], 'first')
        self.assertEqual(second['marker'], 'second')

    def test_original_decoder_error_traceback_remains_after_parent_result_retirement(self):
        h, engine, config, adapter = self.composed(b'not JSON')
        error = None
        with h.readiness(), patch('os.close'):
            try:
                with adapter:
                    engine.Memory(config, 'session').search('invalid')
            except engine.HookError as caught:
                error = caught
            self.assertIsNotNone(error)
            self.assertEqual(error.reason, 'INVALID_JSON')
            self.assertIsNotNone(error.__traceback__)
            self.assertIsNotNone(error.__cause__)
            self.assertIsNone(h.rpc._result)
            self.assertIsNone(h.rpc._operation)
            self.assertEqual(adapter.snapshot()['failure'], 'engine_result_failed')
            self.assertEqual(h.gate.snapshot()['hook_searches'], 1)
            h.client.close()

    def test_interrupt_at_client_boundary_is_sticky_without_second_dispatch(self):
        h, engine, config, adapter = self.composed(b'{"ok":true,"data":{}}')
        original = KeyboardInterrupt('synthetic client boundary')
        with patch.object(h.client, '_send', side_effect=original):
            try:
                with adapter:
                    engine.Memory(config, 'session').search('interrupt')
            except KeyboardInterrupt as caught:
                self.assertIs(caught, original)
            else:
                self.fail('interrupt lost')
        self.assertTrue(h.client._failed)
        self.assertIsNotNone(adapter.snapshot()['failure'])
        self.assertEqual(h.gate.snapshot()['dispatches'], 0)
        self.assertIsNotNone(original.__traceback__)


if __name__ == '__main__':
    unittest.main()
