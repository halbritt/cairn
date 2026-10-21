"""Original control writer, bounded partial writes and SDK MCP correlation."""
import json
import unittest

from test_trial_sdk_control import controller, user_ready, callback
from trial_sdk_control import SDKControlError


class NativeControlTests(unittest.TestCase):
    def test_memory_reply_uses_same_partial_writer_and_distinct_hook_budget(self):
        control = controller()
        control.enable_native_memory()
        user_ready(control)
        before = control.ledger()
        event = control.receive(dict(type='control_request', request_id='memory-1',
            request=dict(subtype='mcp_message', server_name='cairn', message={
                'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {'name': 'cairn_search', 'arguments': {'query': 'q'}}})))
        self.assertEqual(event.request_id, 'memory-1')
        result = {'content': [{'type': 'text', 'text': 'é' * 9000}]}
        control.memory_reply(event.request_id, result)
        output = bytearray()
        while control.write_pending:
            offered = control.pending_write(127)
            self.assertLessEqual(len(offered), 127)
            count = min(11, len(offered))
            output.extend(offered[:count])
            control.written(count)
        response = json.loads(output)['response']
        self.assertEqual(response['request_id'], 'memory-1')
        self.assertEqual(response['response']['mcp_response'],
                         {'jsonrpc': '2.0', 'id': 7, 'result': result})
        self.assertEqual(control.ledger()['reserved_input_bytes'], before['reserved_input_bytes'])
        self.assertEqual(control.ledger()['written_input_bytes'], before['written_input_bytes'] + len(output))
        with self.assertRaises(SDKControlError):
            control.memory_reply(event.request_id, result)

    def memory(self, ident):
        return dict(type='control_request', request_id=ident, request=dict(
            subtype='mcp_message', server_name='cairn', message={
                'jsonrpc':'2.0', 'id':1, 'method':'ping'}))

    def test_ids_cannot_cross_hook_native_or_initialize_namespaces(self):
        for first_native in (True, False):
            control = controller(); control.enable_native_memory(); user_ready(control)
            first, second = (self.memory('same'), callback(control, ident='same'))
            if not first_native: first, second = second, first
            control.receive(first)
            with self.assertRaises(SDKControlError): control.receive(second)
            self.assertIsNotNone(control.ledger()['failure'])
        for native in (True, False):
            control = controller(); control.enable_native_memory(); user_ready(control)
            ident = control.bindings['initialize_request_id']
            with self.assertRaises(SDKControlError):
                control.receive(self.memory(ident) if native else callback(control, ident=ident))

    def test_cancel_wrong_server_duplicate_and_full_input_custody(self):
        from trial_sdk_incoming import canonical_size_sha256
        control = controller(); control.enable_native_memory(); user_ready(control)
        frame = self.memory('one')
        control.receive(frame)
        row = control._memory.rows['one']
        self.assertEqual((row['input_bytes'], row['input_sha256']),
                         canonical_size_sha256(frame['request']['message']))
        with self.assertRaisesRegex(SDKControlError, 'sdk_memory_cancelled'):
            control.receive({'type':'control_cancel_request', 'request_id':'one'})
        self.assertTrue(row['cancelled'])
        for mutation in ('server', 'id', 'duplicate'):
            control = controller(); control.enable_native_memory(); user_ready(control)
            frame = self.memory('one')
            if mutation == 'server': frame['request']['server_name'] = 'foreign'
            if mutation == 'id': frame['request_id'] = {}
            if mutation == 'duplicate': control.receive(frame)
            with self.assertRaises(SDKControlError): control.receive(frame)

    def test_reply_failure_keeps_input_and_output_charges_not_a_9500_grade(self):
        control = controller(); control.enable_native_memory(); user_ready(control)
        control.receive(self.memory('one'))
        control.memory_reply('one', {'content':[{'type':'text','text':'x'*18000}]})
        offered = control.pending_write(127); control.written(min(3, len(offered)))
        before = control.ledger()['written_input_bytes']
        with self.assertRaises(SDKControlError): control.write_failed()
        self.assertEqual(control.ledger()['written_input_bytes'], before)
        self.assertFalse(control._memory.rows['one']['reply_written'])
        self.assertGreater(control._memory.rows['one']['reply_bytes'], 18000)


if __name__ == '__main__':
    unittest.main()
