"""Pure queue readiness; reported writes are model inputs, not native effects."""
import unittest

from trial_coding_launch import compile_sdk_launch
from trial_sdk_control import SDKControlError


class WritePendingTests(unittest.TestCase):
    def test_deferred_user_counter_is_not_current_write_readiness(self):
        control = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000004',
            model='fixture-model', effort='high', prompt='Frozen prompt.', memory_instruction='Frozen instruction.').control
        self.addCleanup(control.close)
        self.assertTrue(control.write_pending)
        control.written(len(control.pending_write()))
        self.assertFalse(control.write_pending)
        control.receive(dict(type='control_response', response=dict(subtype='success',
            request_id=control.bindings['initialize_request_id'], response={})))
        self.assertTrue(control.write_pending)
        callback = control.receive(dict(type='control_request', request_id='start', request=dict(
            subtype='hook_callback', callback_id=control.bindings['callback_ids']['SessionStart'],
            input=dict(hook_event_name='SessionStart', session_id=control.bindings['session_id']))))
        self.assertGreater(control.ledger()['queued_input_bytes'], control.ledger()['written_input_bytes'])
        self.assertFalse(control.write_pending)
        control.reply(callback.request_id, '')
        self.assertTrue(control.write_pending)
        control.written(len(control.pending_write()))
        self.assertTrue(control.write_pending)  # The deferred user is now current.
        control.written(len(control.pending_write()))
        self.assertFalse(control.write_pending)
        self.assertTrue(control.ledger()['user_written'])

    def test_readiness_refuses_after_terminal_controller_failure_or_close(self):
        for stop in ('write_failed', 'close'):
            with self.subTest(stop=stop):
                control = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000004',
                    model='fixture-model', effort='high', prompt='Frozen prompt.', memory_instruction='Frozen instruction.').control
                self.addCleanup(control.close)
                try:
                    getattr(control, stop)()
                except SDKControlError as error:
                    self.assertEqual(str(error), 'sdk_write_failed')
                with self.assertRaisesRegex(SDKControlError, '^sdk_unusable$'):
                    _ = control.write_pending
