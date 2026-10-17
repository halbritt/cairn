"""Pure input-end transitions; reported writes are model inputs, not syscalls."""
import json
import unittest

from trial_sdk_control import SDKControl, SDKControlError
from trial_sdk_incoming import IncomingError, LineAssembler, validate_line


SESSION = '90000000-0000-4000-8000-000000000001'


def token(frame):
    reader = LineAssembler(128 * 1024)
    lines = list(reader.feed(json.dumps(frame).encode() + b'\n', 1))
    reader.finish()
    return validate_line(lines[0])


def callback(control, event, ident):
    return dict(type='control_request', request_id=ident, request=dict(
        subtype='hook_callback', callback_id=control.bindings['callback_ids'][event],
        input=dict(session_id=SESSION, hook_event_name=event)))


def write_frame(control):
    wire = control.pending_write()
    control.written(len(wire))
    return wire


class InputSealTests(unittest.TestCase):
    def control(self, stage='complete'):
        control = SDKControl(SESSION,
            b'{"type":"user","message":{"role":"user","content":"Frozen task."}}\n', 31)
        self.addCleanup(control.close)
        if stage == 'pristine':
            return control
        write_frame(control)
        if stage == 'initialize_written':
            return control
        control.receive_owned(token(dict(type='control_response', response=dict(
            subtype='success', request_id=control.bindings['initialize_request_id'], response={}))))
        if stage == 'user_queued':
            return control
        write_frame(control)
        if stage == 'user_written':
            return control
        for event in ('SessionStart', 'UserPromptSubmit'):
            descriptor = control.receive_owned(token(callback(control, event, event)))
            control.reply(descriptor.request_id, '')
            write_frame(control)
        return control

    def test_completed_required_replies_allow_seal_without_new_write_or_settlement(self):
        control = self.control()
        before = control.ledger()
        self.assertIsNone(control.seal_input())
        after = control.ledger()
        self.assertFalse(before['input_ended'])
        self.assertTrue(after['input_ended'])
        self.assertEqual(after, before | {'input_ended': True})
        self.assertIsNone(control.pending_write())
        self.assertFalse(control.write_pending)
        self.assertTrue(all(row['reply_written'] for row in after['rows']))
        self.assertTrue(all(not row['replayed'] for row in after['rows']))
        self.assertFalse(after['runtime_attested'])
        self.assertIsNone(after['native_consumer_settled'])

    def test_seal_refuses_before_initial_user_and_observed_reply_obligations_complete(self):
        cases = ('pristine', 'initialize_written', 'user_queued', 'partial_user',
                 'user_written', 'missing_session_start', 'missing_user_prompt',
                 'unanswered_callback', 'queued_reply', 'partial_reply')
        for stage in cases:
            with self.subTest(stage=stage):
                if stage == 'partial_user':
                    control = self.control('user_queued')
                    control.written(1)
                elif stage.startswith('missing_'):
                    control = self.control('user_written')
                    event = 'UserPromptSubmit' if stage == 'missing_session_start' else 'SessionStart'
                    control.receive_owned(token(callback(control, event, 'only-required')))
                    control.reply('only-required', '')
                    write_frame(control)
                elif stage in ('unanswered_callback', 'queued_reply', 'partial_reply'):
                    control = self.control()
                    control.receive_owned(token(callback(control, 'PostToolUse', 'tool')))
                    if stage != 'unanswered_callback':
                        control.reply('tool', '')
                    if stage == 'partial_reply':
                        control.written(1)
                else:
                    control = self.control(stage)
                before = control.ledger()
                code = ('sdk_required_callbacks_unobserved'
                        if stage == 'user_written' or stage.startswith('missing_')
                        else 'sdk_input_not_drained')
                with self.assertRaisesRegex(SDKControlError, '^' + code + '$'):
                    control.seal_input()
                after = control.ledger()
                self.assertFalse(after['input_ended'])
                self.assertEqual(after['failure'], code)
                self.assertEqual(after['rows'], before['rows'])
                self.assertEqual(after['written_input_bytes'], before['written_input_bytes'])
                with self.assertRaisesRegex(SDKControlError, '^sdk_unusable$'):
                    control.pending_write()

    def test_sealed_input_refuses_further_mutations_without_poisoning_drain_queries(self):
        operations = ('seal', 'receive', 'receive_owned', 'reply', 'written',
                      'callback_timeout', 'context_capacity')
        for operation in operations:
            with self.subTest(operation=operation):
                control = self.control()
                control.seal_input()
                before = control.ledger()
                owned = token(callback(control, 'PostToolUse', 'late'))
                calls = {
                    'seal': control.seal_input,
                    'receive': lambda: control.receive(callback(control, 'PostToolUse', 'late')),
                    'receive_owned': lambda: control.receive_owned(owned),
                    'reply': lambda: control.reply('SessionStart', 'late'),
                    'written': lambda: control.written(1),
                    'callback_timeout': lambda: control.callback_timeout('SessionStart'),
                    'context_capacity': lambda: control.context_capacity('SessionStart'),
                }
                for _ in range(2):
                    with self.assertRaisesRegex(SDKControlError, '^sdk_input_ended$'):
                        calls[operation]()
                    self.assertEqual(control.ledger(), before)
                    self.assertIsNone(control.pending_write())
                    self.assertFalse(control.write_pending)
                if operation == 'receive_owned':
                    with self.assertRaises(IncomingError):
                        owned.take()
                with self.assertRaisesRegex(SDKControlError, '^sdk_control_not_pristine$'):
                    control.pristine_binding()

    def test_closed_or_failed_controller_cannot_be_sealed_or_reopened(self):
        for was_sealed in (False, True):
            for stop in ('close', 'write_failed'):
                with self.subTest(was_sealed=was_sealed, stop=stop):
                    control = self.control()
                    if was_sealed:
                        control.seal_input()
                    if stop == 'write_failed':
                        with self.assertRaisesRegex(SDKControlError, '^sdk_write_failed$'):
                            control.write_failed()
                    else:
                        control.close()
                    before = control.ledger()
                    for _ in range(2):
                        with self.assertRaisesRegex(SDKControlError, '^sdk_unusable$'):
                            control.seal_input()
                    self.assertEqual(control.ledger(), before)
                    self.assertEqual(before['input_ended'], was_sealed)
