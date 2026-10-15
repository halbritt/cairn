"""Provider-free control protocol tests; no native invocation or settlement proof."""
import fcntl
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from trial_sdk_control import SDKControl, SDKControlError, reply_wire_bytes
from trial_sdk_incoming import DecodedLine, IncomingError, LineAssembler, validate_line


SESSION = '90000000-0000-4000-8000-000000000001'


def user_message(prompt='Implement the frozen task.'):
    return (json.dumps(dict(type='user', message=dict(role='user', content=prompt)),
                       ensure_ascii=False) + '\n').encode()


def controller(prompt='Implement the frozen task.', instruction_bytes=31):
    return SDKControl(SESSION, user_message(prompt), instruction_bytes)


def initialized(control, response=None):
    return dict(type='control_response', response=dict(subtype='success',
                request_id=control.bindings['initialize_request_id'],
                response={} if response is None else response))


def callback(control, event='UserPromptSubmit', ident='call-1', **payload):
    return dict(type='control_request', request_id=ident,
                request=dict(subtype='hook_callback',
                             callback_id=control.bindings['callback_ids'][event],
                             input=dict(session_id=SESSION, hook_event_name=event, **payload)))


def write_one(control):
    """Report partial local writes of exactly one offered frame, not the next."""
    wire = control.pending_write()
    received = bytearray()
    for width in (1, 3, 11, len(wire)):
        offered = control.pending_write()
        count = min(width, len(wire) - len(received))
        assert offered == wire[len(received):]
        received.extend(offered[:count])
        control.written(count)
        if len(received) == len(wire):
            return bytes(received)
    raise AssertionError('one frame not completed')


def user_ready(control):
    write_one(control)
    control.receive(initialized(control))
    return write_one(control)


def input_bytes(descriptor):
    """Materialize only in offline tests; production streams bounded chunks."""
    return b''.join(descriptor.iter_input_json())


class SDKControlTests(unittest.TestCase):
    def refuses(self, control, code, operation):
        with self.assertRaisesRegex(SDKControlError, '^' + code + '$'):
            operation()
        self.assertEqual(control.ledger()['failure'], code)
        with self.assertRaisesRegex(SDKControlError, '^sdk_unusable$'):
            control.pending_write()

    def test_exact_reply_cost_includes_actual_empty_envelope_and_escaped_context(self):
        for event in ('PostToolUse', 'PostToolUseFailure'):
            for ident in ('x', 'x' * 128):
                for context in ('', '\x00\n"\\é'):
                    control = controller()
                    user_ready(control)
                    control.receive(callback(control, event, ident))
                    cost = reply_wire_bytes(ident, event, context)
                    control.reply(ident, context)
                    actual = write_one(control)
                    with self.subTest(event=event, ident_bytes=len(ident), context=context):
                        self.assertEqual(cost, len(actual))
                        self.assertTrue(actual.endswith(b'\n'))
                        self.assertEqual(json.loads(actual)['response']['response']['hookSpecificOutput']
                                         ['additionalContext'], context)
        self.assertEqual(reply_wire_bytes('x' * 128, 'PostToolUseFailure') -
                         reply_wire_bytes('x', 'PostToolUse'), 127 + len('Failure'))

    def test_reply_budget_distinguishes_pending_required_and_spendable_bytes(self):
        control = controller()
        user_ready(control)
        first = control.reply_budget()
        mandatory = sum(reply_wire_bytes('x' * 128, event)
                        for event in ('SessionStart', 'UserPromptSubmit'))
        self.assertEqual(first['pending_callbacks'], 0)
        self.assertEqual(first['mandatory_empty_reply_bytes'], mandatory)
        self.assertEqual(first['reserved_empty_reply_bytes'], mandatory)
        control.receive(callback(control, 'SessionStart', 's'))
        control.receive(callback(control, 'PostToolUseFailure', 't' * 128))
        budget = control.reply_budget()
        pending = reply_wire_bytes('s', 'SessionStart') + reply_wire_bytes('t' * 128, 'PostToolUseFailure')
        required = reply_wire_bytes('x' * 128, 'UserPromptSubmit')
        self.assertEqual(budget['pending_callbacks'], 2)
        self.assertEqual(budget['pending_empty_reply_bytes'], pending)
        self.assertEqual(budget['mandatory_empty_reply_bytes'], required)
        self.assertEqual(budget['mandatory_callbacks_unobserved'], ['UserPromptSubmit'])
        self.assertEqual(budget['context_json_bytes'],
                         min(control.remaining_wire_bytes, control.remaining_selected_bytes) - pending - required)
        self.assertIsNone(budget['additional_tool_callback_count'])
        self.assertFalse(budget['external_selected_charges_included'])
        before = control.ledger()
        budget['mandatory_callbacks_unobserved'].clear()
        self.assertEqual(control.ledger(), before)
        control.reply('s', '\x00é')
        after = control.reply_budget()
        extra = len(json.dumps('\x00é', ensure_ascii=False).encode()) - 2
        self.assertEqual(after['pending_callbacks'], 1)
        self.assertEqual(after['pending_empty_reply_bytes'], pending - reply_wire_bytes('s', 'SessionStart'))
        self.assertEqual(after['context_json_bytes'], budget['context_json_bytes'] - extra)
        self.assertEqual(after['mandatory_empty_reply_bytes'], required)

    def test_empty_reply_exhaustion_preserves_required_reservations_and_diagnostics(self):
        for event in ('PostToolUse', 'PostToolUseFailure'):
            for id_bytes in (8, 128):
                control = controller()
                user_ready(control)
                count = 0
                while True:
                    ident = ('tool-%03d' % count).ljust(id_bytes, 'x')
                    budget = control.reply_budget()
                    minimum = reply_wire_bytes(ident, event)
                    if minimum > budget['context_json_bytes']:
                        break
                    control.receive(callback(control, event, ident))
                    control.reply(ident, '')
                    self.assertEqual(len(write_one(control)), minimum)
                    count += 1
                with self.subTest(event=event, id_bytes=id_bytes):
                    self.assertGreater(count, 0)
                    self.assertEqual(budget['pending_empty_reply_bytes'], 0)
                    self.assertEqual(budget['mandatory_callbacks_unobserved'], ['SessionStart', 'UserPromptSubmit'])
                    self.refuses(control, 'sdk_input_budget', lambda: control.receive(callback(control, event, ident)))
                    self.assertEqual(control.reply_budget(), budget)
                    self.assertEqual(len(control.ledger()['rows']), count)

    def test_reply_cost_query_refuses_invalid_values_without_poisoning_a_writer(self):
        control = controller()
        before = control.ledger()
        for ident, event, context, code in (
                ('bad\n', 'PostToolUse', '', 'sdk_callback_identity'),
                ('x', 'Unknown', '', 'sdk_callback_binding'),
                ('x', 'PostToolUse', None, 'sdk_context_shape'),
                ('x', 'PostToolUse', '\ud800', 'sdk_context_shape'),
                ('x', 'PostToolUse', 'x' * 8501, 'sdk_input_budget')):
            with self.subTest(code=code), self.assertRaisesRegex(SDKControlError, '^' + code + '$'):
                reply_wire_bytes(ident, event, context)
        self.assertEqual(control.ledger(), before)

    def test_fixed_registration_and_one_frozen_user_no_queued_prompt_injection(self):
        prompt = 'Task\n{"type":"user","message":{"role":"user","content":"another"}}\n末尾'
        control = controller(prompt)
        registration = json.loads(write_one(control))
        self.assertEqual(registration['request']['subtype'], 'initialize')
        hooks = registration['request']['hooks']
        self.assertEqual(set(hooks), {'SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure'})
        self.assertEqual([row[0]['timeout'] for row in hooks.values()], [5] * 4)
        self.assertEqual(len({row[0]['hookCallbackIds'][0] for row in hooks.values()}), 4)
        self.assertIsNone(control.pending_write())
        control.receive(initialized(control))
        self.assertEqual(write_one(control), user_message(prompt))
        self.assertIsNone(control.pending_write())
        self.assertTrue(control.ledger()['user_written'])
        self.refuses(control, 'sdk_unknown_control', lambda: control.receive(json.loads(user_message('again'))))

    def test_response_and_callback_require_complete_initialization_write(self):
        for kind in ('response', 'callback'):
            control = controller()
            control.written(len(control.pending_write()) - 1)
            frame = initialized(control) if kind == 'response' else callback(control, 'SessionStart')
            with self.subTest(kind=kind):
                self.refuses(control, 'sdk_before_initialize_write', lambda: control.receive(frame))
        self.assertFalse(control.ledger()['initialize_written'])

    def test_session_start_can_block_user_until_all_prior_replies_complete(self):
        control = controller()
        write_one(control)
        first = control.receive(callback(control, 'SessionStart', 'early-a'))
        second = control.receive(callback(control, 'SessionStart', 'early-b'))
        control.receive(initialized(control))
        self.assertIsNone(control.pending_write())
        control.reply(second.request_id, 'second')
        write_one(control)
        self.assertFalse(control.ledger()['user_queued'])
        control.reply(first.request_id, 'first')
        offered = control.pending_write()
        control.written(len(offered) - 1)
        self.assertFalse(control.ledger()['user_queued'])
        control.written(1)
        self.assertEqual(write_one(control), user_message())
        self.assertEqual([r['reply_written'] for r in control.ledger()['rows']], [True, True])

    def test_non_session_start_requires_complete_user_even_during_partial_write(self):
        for event in ('UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure'):
            control = controller()
            write_one(control)
            control.receive(initialized(control))
            control.written(1)
            with self.subTest(event=event):
                self.refuses(control, 'sdk_callback_before_user',
                             lambda: control.receive(callback(control, event)))

    def test_session_start_defers_queued_unstarted_user_without_double_charge(self):
        control = controller()
        write_one(control)
        control.receive(initialized(control))
        queued_user = control.pending_write()
        queued_bytes = control.ledger()['queued_input_bytes']
        descriptor = control.receive(callback(control, 'SessionStart', 'before-user'))
        self.assertIsNone(control.pending_write())
        control.reply(descriptor.request_id, 'startup context')
        reply = write_one(control)
        self.assertEqual(json.loads(reply)['type'], 'control_response')
        self.assertEqual(control.pending_write(), queued_user)
        self.assertEqual(control.ledger()['queued_input_bytes'], queued_bytes + len(reply))
        self.assertEqual(write_one(control), user_message())
        self.assertEqual(control.ledger()['user_written_bytes'], len(queued_user))
        self.assertIsNone(control.pending_write())

    def test_session_start_during_partial_user_must_wait_for_frame_boundary(self):
        control = controller()
        write_one(control)
        control.receive(initialized(control))
        queued_user = control.pending_write()
        control.written(1)
        descriptor = control.receive(callback(control, 'SessionStart', 'partial-user'))
        control.reply(descriptor.request_id, 'startup context')
        self.assertEqual(control.pending_write(), queued_user[1:])
        control.written(len(queued_user) - 1)
        self.assertTrue(control.ledger()['user_written'])
        self.assertFalse(control.ledger()['rows'][0]['reply_written'])
        self.assertEqual(json.loads(write_one(control))['response']['request_id'], 'partial-user')
        self.assertTrue(control.ledger()['rows'][0]['reply_written'])

    def test_real_nonblocking_pipe_short_write_preserves_user_and_later_reply(self):
        control = controller('x' * 6000)
        read_fd, write_fd = os.pipe()
        try:
            fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
            os.set_blocking(read_fd, False)
            os.set_blocking(write_fd, False)
            init = control.pending_write()
            count = os.write(write_fd, init)
            control.written(count)
            self.assertEqual(os.read(read_fd, 8192), init)
            control.receive(initialized(control))
            frozen_user = control.pending_write()
            count = os.write(write_fd, frozen_user)
            self.assertGreater(count, 0)
            self.assertLess(count, len(frozen_user))
            control.written(count)
            early = control.receive(callback(control, 'SessionStart', 'during-user'))
            control.reply(early.request_id, 'bounded context')
            self.assertEqual(control.pending_write(), frozen_user[count:])
            actual = os.read(read_fd, 8192)
            offered = control.pending_write()
            tail_count = os.write(write_fd, offered)
            control.written(tail_count)
            actual += os.read(read_fd, 8192)
            self.assertEqual(actual, user_message('x' * 6000))
            self.assertEqual(control.ledger()['user_written_bytes'], len(actual))
            self.assertEqual(json.loads(write_one(control))['response']['request_id'], early.request_id)
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_canonical_replay_is_only_a_local_delivery_observation(self):
        control = controller()
        user_ready(control)
        descriptor = control.receive(callback(control))
        control.reply(descriptor.request_id, 'é\ncontext')
        reply = json.loads(write_one(control))
        self.assertEqual(reply['response']['response'], {'continue': True, 'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit', 'additionalContext': 'é\ncontext'}})
        # Encoding/key order are not content differences.
        reordered = dict(reversed(list(reply.items())))
        control.receive(json.dumps(reordered, ensure_ascii=True).encode())
        row = control.ledger()['rows'][0]
        self.assertTrue(row['reply_written'])
        self.assertTrue(row['replayed'])
        self.assertIsNone(control.ledger()['native_consumer_settled'])
        self.assertFalse(control.ledger()['native_settlement_attested'])
        self.assertFalse(control.ledger()['runtime_attested'])
        self.assertFalse({'exit_code', 'stdout', 'stderr', 'stdout_sha256'} & set(row))
        self.refuses(control, 'sdk_duplicate_replay', lambda: control.receive(reply))

    def test_early_replay_refuses_but_retains_partial_write_fact(self):
        control = controller()
        user_ready(control)
        control.receive(callback(control))
        control.reply('call-1', 'context')
        reply = json.loads(control.pending_write())
        control.written(7)
        self.refuses(control, 'sdk_early_replay', lambda: control.receive(reply))
        row = control.ledger()['rows'][0]
        self.assertEqual(row['reply_written_bytes'], 7)
        self.assertFalse(row['reply_written'])
        self.assertFalse(row['replayed'])

    def test_reply_content_drift_never_becomes_replayed_or_settled(self):
        for drift in ('context', 'extra', 'error'):
            control = controller()
            user_ready(control)
            control.receive(callback(control))
            control.reply('call-1', 'original')
            reply = json.loads(write_one(control))
            if drift == 'context':
                reply['response']['response']['hookSpecificOutput']['additionalContext'] = 'changed'
            elif drift == 'extra':
                reply['response']['response']['extra'] = 'changed'
            else:
                reply['response'] = dict(subtype='error', request_id='call-1', error='private details')
            code = 'sdk_callback_response_error' if drift == 'error' else 'sdk_reply_content_drift'
            with self.subTest(drift=drift):
                self.refuses(control, code, lambda: control.receive(reply))
                self.assertTrue(control.ledger()['rows'][0]['reply_written'])
                self.assertFalse(control.ledger()['rows'][0]['replayed'])

    def test_duplicate_reply_and_duplicate_invocation_refuse_after_local_delivery(self):
        for kind in ('reply', 'invocation'):
            control = controller()
            user_ready(control)
            request = callback(control)
            control.receive(request)
            control.reply('call-1', '')
            control.receive(json.loads(write_one(control)))
            code = 'sdk_reply_duplicate' if kind == 'reply' else 'sdk_callback_identity'
            operation = (lambda: control.reply('call-1', '')) if kind == 'reply' else (lambda: control.receive(request))
            with self.subTest(kind=kind):
                self.refuses(control, code, operation)

    def test_wrong_identity_session_event_or_extra_control_refuses(self):
        for kind in ('session', 'callback', 'event', 'initialize-id', 'subtype', 'extra'):
            control = controller()
            user_ready(control)
            request = callback(control)
            if kind == 'session':
                request['request']['input']['session_id'] = 'another session'
            elif kind == 'callback':
                request['request']['callback_id'] = control.bindings['callback_ids']['PostToolUse']
            elif kind == 'event':
                request['request']['input']['hook_event_name'] = 'PermissionRequest'
            elif kind == 'initialize-id':
                request['request_id'] = control.bindings['initialize_request_id']
            elif kind == 'subtype':
                request['request']['subtype'] = 'can_use_tool'
            else:
                request['request']['command'] = 'arbitrary command'
            code = ('sdk_callback_identity' if kind == 'initialize-id' else
                    'sdk_callback_shape' if kind in ('subtype', 'extra') else 'sdk_callback_binding')
            with self.subTest(kind=kind):
                self.refuses(control, code, lambda: control.receive(request))

    def test_cancellation_timeout_and_late_reply_keep_selected_failure(self):
        for kind in ('cancel', 'timeout'):
            control = controller()
            user_ready(control)
            control.receive(callback(control))
            control.reply('call-1', 'pending')
            control.written(1)
            operation = ((lambda: control.receive(dict(type='control_cancel_request', request_id='call-1')))
                         if kind == 'cancel' else (lambda: control.callback_timeout('call-1')))
            code = 'sdk_callback_cancelled' if kind == 'cancel' else 'sdk_callback_timeout'
            with self.subTest(kind=kind):
                self.refuses(control, code, operation)
                row = control.ledger()['rows'][0]
                self.assertTrue(row['cancelled' if kind == 'cancel' else 'timed_out'])
                self.assertEqual(row['reply_written_bytes'], 1)
                with self.assertRaisesRegex(SDKControlError, 'sdk_unusable'):
                    control.reply('call-1', 'late retry')
                self.assertEqual(control.ledger()['failure'], code)

    def test_unknown_initialize_error_duplicate_and_unbound_responses_refuse(self):
        for kind in ('error', 'duplicate', 'unknown'):
            control = controller()
            write_one(control)
            response = initialized(control)
            if kind == 'error':
                response['response'] = dict(subtype='error', request_id=control.bindings['initialize_request_id'],
                                            error='private account details')
                code = 'sdk_initialize_error'
            elif kind == 'duplicate':
                control.receive(response)
                code = 'sdk_duplicate_initialize'
            else:
                response['response']['request_id'] = 'unbound'
                code = 'sdk_unknown_response'
            with self.subTest(kind=kind):
                self.refuses(control, code, lambda: control.receive(response))

    def test_zero_invalid_or_failed_write_poison_without_fabricated_completion(self):
        for count in (0, -1, True, 1000000, None):
            control = controller()
            control.written(3)
            with self.subTest(count=count):
                self.refuses(control, 'sdk_write_progress', lambda: control.written(count))
                self.assertEqual(control.ledger()['written_input_bytes'], 3)
                self.assertFalse(control.ledger()['initialize_written'])
        control = controller()
        self.refuses(control, 'sdk_write_failed', control.write_failed)

    def test_constructor_budgets_reserve_initialize_user_instruction_and_hook_room(self):
        ordinary = controller(instruction_bytes=0)
        reserved = ordinary.ledger()['reserved_input_bytes']
        instruction = 9500 - 512 - reserved - 1000
        exact = controller(instruction_bytes=instruction)
        self.assertEqual(exact.remaining_selected_bytes, 1000)
        self.assertEqual(exact.ledger()['queued_input_bytes'], reserved - len(user_message()))
        with self.assertRaisesRegex(SDKControlError, 'sdk_input_budget'):
            controller(instruction_bytes=instruction + 1)
        with self.assertRaisesRegex(SDKControlError, 'sdk_instruction_bytes'):
            controller(instruction_bytes=True)

    def test_minimum_hook_room_must_also_fit_capture_write_ceiling(self):
        base_prompt = 'x'
        base = controller(base_prompt, instruction_bytes=0)
        extra = 7500 - base.ledger()['reserved_input_bytes']
        exact = controller(base_prompt + 'x' * extra, instruction_bytes=0)
        self.assertEqual(exact.ledger()['reserved_input_bytes'], 7500)
        self.assertGreater(exact.remaining_selected_bytes, 1000)
        with self.assertRaisesRegex(SDKControlError, 'sdk_input_budget'):
            controller(base_prompt + 'x' * (extra + 1), instruction_bytes=0)

    def test_reply_envelopes_and_escaped_context_share_both_original_limits(self):
        for instruction in (0, 1000):
            control = controller(instruction_bytes=instruction)
            user_ready(control)
            control.receive(callback(control, ident='first'))
            response = dict(type='control_response', response={'subtype': 'success', 'request_id': 'first',
                        'response': {'continue': True, 'hookSpecificOutput': {
                            'hookEventName': 'UserPromptSubmit', 'additionalContext': ''}}})
            overhead = len(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()) + 1
            capacity = control.context_capacity('first')
            context = '\x00' * (capacity.json_content_bytes // 6) + 'x' * (capacity.json_content_bytes % 6)
            control.reply('first', context)
            actual = write_one(control)
            self.assertEqual(len(actual), overhead + capacity.json_content_bytes)
            self.assertEqual(control.ledger()['reserved_reply_bytes'],
                             min(control.remaining_wire_bytes, control.remaining_selected_bytes))
            # The still-mandatory SessionStart survives even a maximum-sized ID.
            control.receive(callback(control, 'SessionStart', ident='s' * 128))
            control.reply('s' * 128, '')
            write_one(control)
            charge = control.ledger()['reserved_input_bytes']
            self.assertLessEqual(charge, 8500)
            self.assertLessEqual(charge + instruction + 512, 9500)
            self.assertEqual(charge, 8500 if instruction == 0 else 9500 - instruction - 512)
            with self.subTest(instruction=instruction):
                self.refuses(control, 'sdk_input_budget',
                             lambda: control.receive(callback(control, ident='second')))
                self.assertEqual(control.ledger()['reserved_input_bytes'], charge)

    def test_distinct_wire_selected_and_conservative_context_capacities(self):
        control = controller(instruction_bytes=0)
        self.assertGreater(control.remaining_selected_bytes, control.remaining_wire_bytes)
        self.assertEqual(control.ledger()['mandatory_callbacks_unobserved'],
                         ['SessionStart', 'UserPromptSubmit'])
        self.assertEqual(control.guaranteed_context_bytes,
                         (control.remaining_wire_bytes - control.ledger()['reserved_reply_bytes']) // 6)
        user_ready(control)
        control.receive(callback(control, 'SessionStart', ident='startup'))
        control.receive(callback(control, ident='prompt'))
        capacity = control.context_capacity('prompt')
        self.assertEqual(capacity.guaranteed_utf8_bytes, capacity.json_content_bytes // 6)
        self.assertLess(capacity.json_content_bytes, control.remaining_selected_bytes)
        self.assertLess(capacity.json_content_bytes, control.remaining_wire_bytes)
        self.assertEqual(control.ledger()['mandatory_callbacks_unobserved'], [])

    def test_known_pending_empty_reply_survives_context_spending(self):
        control = controller()
        user_ready(control)
        control.receive(callback(control, 'SessionStart', ident='startup'))
        control.receive(callback(control, ident='prompt'))
        capacity = control.context_capacity('startup')
        control.reply('startup', 'x' * capacity.json_content_bytes)
        remaining = control.context_capacity('prompt')
        self.assertEqual(remaining.json_content_bytes, 0)
        self.assertEqual(control.ledger()['reserved_reply_bytes'], remaining.empty_reply_wire_bytes)
        control.reply('prompt', '')
        self.assertEqual(control.ledger()['reserved_reply_bytes'], 0)
        self.assertEqual(json.loads(write_one(control))['response']['request_id'], 'startup')
        self.assertEqual(json.loads(write_one(control))['response']['request_id'], 'prompt')
        self.assertTrue(all(row['reply_written'] for row in control.ledger()['rows']))

    def test_new_pending_reply_invalidates_stale_context_capacity_without_overspend(self):
        control = controller()
        user_ready(control)
        control.receive(callback(control, ident='first'))
        earlier = control.context_capacity('first')
        control.receive(callback(control, 'PostToolUse', ident='second'))
        self.assertLess(control.context_capacity('first').json_content_bytes, earlier.json_content_bytes)
        charged = control.ledger()['reserved_input_bytes']
        self.refuses(control, 'sdk_input_budget',
                     lambda: control.reply('first', 'x' * earlier.json_content_bytes))
        self.assertEqual(control.ledger()['reserved_input_bytes'], charged)
        self.assertTrue(all(not row['reply_written'] for row in control.ledger()['rows']))

    def test_context_capacity_accounts_unicode_quotes_and_escape_expansion(self):
        control = controller()
        user_ready(control)
        control.receive(callback(control, ident='quoted'))
        capacity = control.context_capacity('quoted')
        unit = 'é\n"\\'
        encoded_unit = len(json.dumps(unit, ensure_ascii=False).encode()) - 2
        count, tail = divmod(capacity.json_content_bytes, encoded_unit)
        context = unit * count + 'x' * tail
        self.assertGreater(len(context.encode()), capacity.guaranteed_utf8_bytes)
        control.reply('quoted', context)
        wire = write_one(control)
        self.assertEqual(len(wire) - capacity.empty_reply_wire_bytes, capacity.json_content_bytes)
        self.assertEqual(json.loads(wire)['response']['response']['hookSpecificOutput']['additionalContext'], context)
        self.assertFalse(control.ledger()['native_hook_schema_attested'])

    def test_reply_context_utf8_and_invalid_surrogates_cannot_bypass_wire_charge(self):
        for context in ('é' * 8000, '\ud800'):
            control = controller()
            user_ready(control)
            control.receive(callback(control))
            code = 'sdk_context_shape' if context == '\ud800' else 'sdk_input_budget'
            with self.subTest(kind=code):
                self.refuses(control, code, lambda: control.reply('call-1', context))

    def test_escaped_overbudget_reply_refuses_before_materializing_its_wire(self):
        control = controller()
        user_ready(control)
        control.receive(callback(control))
        capacity = control.context_capacity('call-1')
        with patch('trial_sdk_control._reply_wire') as materialize:
            self.refuses(control, 'sdk_input_budget',
                         lambda: control.reply('call-1', '\x00' * capacity.json_content_bytes))
        materialize.assert_not_called()
        self.assertIsNone(control.ledger()['rows'][0]['reply_bytes'])

    def test_ledger_and_descriptor_freeze_inputs_without_retaining_private_content(self):
        secret = 'SELECTED_TEST_SECRET_DO_NOT_RETAIN'
        control = controller(prompt=secret)
        write_one(control)
        control.receive(initialized(control, dict(account=secret, models=[secret])))
        write_one(control)
        request = callback(control, transcript_path='/private/' + secret, prompt=secret)
        descriptor = control.receive(request)
        frozen = input_bytes(descriptor)
        request['request']['input']['prompt'] = 'mutated'
        self.assertEqual(input_bytes(descriptor), frozen)
        self.assertIn(secret.encode(), frozen)
        self.assertNotIn(secret, repr(descriptor))
        control.reply('call-1', secret)
        write_one(control)
        first = control.ledger()
        self.assertNotIn(secret, json.dumps(first))
        first['rows'][0]['reply_written'] = False
        self.assertTrue(control.ledger()['rows'][0]['reply_written'])
        binding = control.bindings
        binding['callback_ids']['SessionStart'] = 'changed'
        self.assertNotEqual(control.bindings, binding)
        control.close()
        self.assertFalse(control._queue)
        self.assertFalse(control._expected_replays)
        self.assertEqual(control._user, b'')
        self.assertNotIn(secret, json.dumps(control.ledger()))
        with self.assertRaisesRegex(SDKControlError, 'sdk_unusable'):
            control.receive(initialized(control))

    def test_strict_json_and_bounded_plain_graph_refuse_before_retaining(self):
        cycle = {}; cycle['cycle'] = cycle
        deeply_nested = []
        for _ in range(100):
            deeply_nested = [deeply_nested]
        for frame in (b'{"type":"control_response","type":"control_request"}',
                      b'{"type":"control_response","response":NaN}',
                      b'{"type":"control_response","response":"\xff"}',
                      dict(type='control_response', response=deeply_nested), cycle,
                      dict(type='control_response', response='x' * 131073)):
            control = controller()
            write_one(control)
            with self.subTest(kind=type(frame)), self.assertRaises(SDKControlError):
                control.receive(frame)
            self.assertFalse(control.ledger()['initialized'])
            with self.assertRaisesRegex(SDKControlError, 'sdk_unusable'):
                control.pending_write()

    def test_bytes_and_dict_frames_share_depth_list_integer_and_node_bounds(self):
        deep = 0
        for _ in range(33):
            deep = [deep]
        cases = [('depth', deep, 'sdk_frame_bounds'),
                 ('list', [0] * 131072, 'sdk_frame_bounds'),
                 ('integer', 1 << 1024, 'sdk_frame_shape'),
                 ('nodes', [[] for _ in range(131072)], 'sdk_frame_bounds')]
        for label, sample, code in cases:
            for as_bytes in (False, True):
                control = controller()
                user_ready(control)
                frame = callback(control, sample=sample)
                if as_bytes:
                    frame = json.dumps(frame).encode()
                with self.subTest(bound=label, as_bytes=as_bytes):
                    self.refuses(control, code, lambda: control.receive(frame))
                    self.assertEqual(control.ledger()['rows'], [])

    def test_equivalent_near_boundary_values_are_accepted_on_both_transports(self):
        for sample in (list(range(256)), list(range(1024)), (1 << 64), (1 << 1024) - 1,
                       [list(range(256)) for _ in range(7)]):
            for as_bytes in (False, True):
                control = controller()
                user_ready(control)
                frame = callback(control, sample=sample)
                if as_bytes:
                    frame = json.dumps(frame).encode()
                with self.subTest(kind=type(sample), as_bytes=as_bytes):
                    descriptor = control.receive(frame)
                    self.assertEqual(json.loads(input_bytes(descriptor))['sample'], sample)
                    self.assertIsNone(control.ledger()['failure'])

    def test_bytes_and_dict_accept_large_dictionaries_within_document_bounds(self):
        for width in (64, 65, 512):
            for as_bytes in (False, True):
                control = controller()
                user_ready(control)
                frame = callback(control)
                payload = frame['request']['input']
                payload.update({'extra-' + str(i): i for i in range(width - len(payload))})
                if as_bytes:
                    frame = json.dumps(frame).encode()
                with self.subTest(width=width, as_bytes=as_bytes):
                    self.assertEqual(len(json.loads(input_bytes(control.receive(frame)))), width)

    def test_compact_numeric_wire_cannot_expand_past_canonical_frame_limit(self):
        for canonical_size in (131072, 131073):
            for as_bytes in (False, True):
                control = controller()
                user_ready(control)
                frame = callback(control, samples=[1e15] * 256, padding='')
                empty = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                frame['request']['input']['padding'] = 'x' * (canonical_size - len(empty))
                encoded = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                self.assertEqual(len(encoded), canonical_size)
                compact = encoded.replace(b'1000000000000000.0', b'1e15')
                self.assertLess(len(compact), 131072)
                self.assertEqual(json.loads(compact), frame)
                incoming = compact if as_bytes else frame
                with self.subTest(canonical_size=canonical_size, as_bytes=as_bytes):
                    if canonical_size == 131072:
                        self.assertEqual(len(json.loads(input_bytes(control.receive(incoming)))['samples']), 256)
                    else:
                        self.refuses(control, 'sdk_frame_bounds', lambda: control.receive(incoming))

    def test_70kib_full_input_and_explicit_projection_across_three_entry_points(self):
        sample = 'é\n"\\' * 10000 + 'TAIL_SENTINEL'
        for transport in ('bytes', 'dict', 'owned'):
            control = controller()
            user_ready(control)
            frame = callback(control, 'PostToolUse', 'large-tool', tool_name='Read',
                             tool_use_id='tool-1', tool_response=sample)
            wire = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            self.assertGreater(len(wire), 70 * 1024)
            expected = json.dumps(frame['request']['input'], ensure_ascii=False,
                                  sort_keys=True, separators=(',', ':')).encode()
            if transport == 'dict':
                descriptor = control.receive(frame)
            elif transport == 'bytes':
                descriptor = control.receive(wire)
            else:
                reader = LineAssembler(128 * 1024)
                documents = []
                for offset in range(0, len(wire) + 1, 997):
                    documents.extend(reader.feed((wire + b'\n')[offset:offset + 997], 123))
                reader.finish()
                self.assertEqual(len(documents), 1)
                token = validate_line(documents[0])
                self.assertEqual(token.kind, 'control_request')
                descriptor = control.receive_owned(token)
                with self.assertRaises(IncomingError):
                    token.take()
            with self.subTest(transport=transport):
                chunks = list(descriptor.iter_input_json())
                self.assertTrue(all(type(chunk) is bytes and 0 < len(chunk) <= 8192 for chunk in chunks))
                self.assertEqual(b''.join(chunks), expected)
                self.assertEqual(descriptor.input_bytes, len(expected))
                self.assertEqual(descriptor.input_sha256, hashlib.sha256(expected).hexdigest())
                projection = descriptor.diagnostic_projection()
                self.assertEqual(projection['tool_name'], 'Read')
                self.assertEqual(projection['tool_use_id'], 'tool-1')
                self.assertNotIn('tool_response', projection)
                self.assertEqual(projection['input_sha256'], descriptor.input_sha256)
                row = control.ledger()['rows'][0]
                self.assertEqual(row['frame_raw_bytes'], len(wire))
                self.assertEqual(row['frame_raw_sha256'], hashlib.sha256(wire).hexdigest())
                self.assertNotIn('TAIL_SENTINEL', json.dumps(control.ledger()))
                self.assertFalse(hasattr(descriptor, 'input_json'))
                self.assertIsNone(control.ledger()['failure'])

    def test_owned_refusal_consumes_token_without_committing_a_callback(self):
        control = controller()
        user_ready(control)
        frame = callback(control, session_marker='large-prefix' * 7000)
        frame['request']['input']['session_id'] = 'wrong-session'
        wire = json.dumps(frame).encode() + b'\n'
        reader = LineAssembler(128 * 1024)
        documents = []
        for offset in range(0, len(wire), 8192):
            documents.extend(reader.feed(wire[offset:offset + 8192], 7))
        token = validate_line(documents[0])
        self.refuses(control, 'sdk_callback_binding', lambda: control.receive_owned(token))
        self.assertEqual(control.ledger()['rows'], [])
        with self.assertRaises(IncomingError):
            token.take()

    def test_owned_metadata_has_bounded_shape_without_becoming_authentication(self):
        for digest in ('x', b'0' * 64, {'x': 1}, '1' * 65):
            control = controller()
            user_ready(control)
            wire = json.dumps(callback(control)).encode()
            token = validate_line(DecodedLine(wire.decode(), len(wire), digest, 3))
            with self.subTest(kind=type(digest)):
                self.refuses(control, 'sdk_frame_shape', lambda: control.receive_owned(token))
                self.assertEqual(control.ledger()['rows'], [])
                with self.assertRaises(IncomingError):
                    token.take()

    def test_owned_mixed_stream_demux_and_malformed_tail_do_not_create_callbacks(self):
        control = controller()
        user_ready(control)
        reader = LineAssembler(128 * 1024)
        ordinary = list(reader.feed(b'{"type":"assistant","message":{"content":[]}}\n', 9))[0]
        token = validate_line(ordinary)
        self.assertEqual(token.kind, 'assistant')
        self.assertEqual(token.take()['message'], {'content': []})
        self.assertEqual(control.ledger()['rows'], [])
        prefix = json.dumps(callback(control, padding='x' * (70 * 1024))).encode()
        malformed = prefix[:-1] + b',"bad":{"key":1,"key":2}}\n'
        documents = []
        for offset in range(0, len(malformed), 8192):
            documents.extend(reader.feed(malformed[offset:offset + 8192], 10))
        with self.assertRaises(IncomingError):
            validate_line(documents[0])
        self.assertEqual(control.ledger()['rows'], [])
        self.assertIsNone(control.ledger()['failure'])

    def test_only_one_strict_user_object_can_enter_constructor(self):
        for wire in (user_message() + user_message('another'),
                     b'{"type":"user","message":{"role":"user","content":"x","content":"y"}}\n',
                     b'{"type":"user","message":{"role":"assistant","content":"x"}}\n',
                     b'{"type":"user","message":{"role":"user","content":"x"},"extra":1}\n'):
            with self.subTest(wire=wire), self.assertRaisesRegex(SDKControlError, 'sdk_user_frame'):
                SDKControl(SESSION, wire, 0)

    def test_pending_empty_replies_cannot_overcommit_input_or_ledger_bounds(self):
        control = controller()
        user_ready(control)
        refused = False
        for number in range(128):
            try:
                control.receive(callback(control, 'PostToolUse', 'call-' + str(number)))
            except SDKControlError as error:
                self.assertEqual(str(error), 'sdk_input_budget')
                refused = True
                break
        self.assertTrue(refused)
        self.assertGreater(len(control.ledger()['rows']), 0)
        self.assertLess(len(control.ledger()['rows']), 128)
        self.assertLessEqual(control.ledger()['reserved_reply_bytes'], control.remaining_wire_bytes)
        self.assertLessEqual(control.ledger()['reserved_reply_bytes'], control.remaining_selected_bytes)
        self.assertLessEqual(len(json.dumps(control.ledger()).encode()), 65536)


if __name__ == '__main__':
    unittest.main()
