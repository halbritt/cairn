"""Characterize the distinct native fixture result policies; no provider runs."""
import json
import unittest

from native_tool_results import interpret_advance_tool_result, tool_result_diagnostics


def api_response(**changes):
    return dict(schema='cairn.response/1', ok=True, status='OK', **changes)


def batch_response(**changes):
    return dict(dict(schema='cairn.compact-inbox-batch/1', ok=True, next='private successor',
                     batch_index=2, batch_limit=3), **changes)


def encoded_execution(response, **changes):
    return json.dumps(dict(dict(exit_code=0, output=json.dumps(response)), **changes))


def native_output(response, exit_code=0):
    return f'Chunk ID: private\nProcess exited with code {exit_code}\nOutput:\n' + json.dumps(response)


class WakeDiagnosticsPolicyTests(unittest.TestCase):
    def test_recursive_envelopes_are_evidence_even_outside_output(self):
        result = tool_result_diagnostics({'details': {'content': json.dumps(api_response())}})
        self.assertEqual((result['result_output_type'], result['api_read_status']), ('missing', 'success'))
        self.assertEqual(result['api_read_invocation'], 'confirmed')
        root = tool_result_diagnostics(api_response())
        self.assertEqual(root['api_read_status'], 'success')

    def test_any_success_wins_over_refusal_without_overriding_first_integer_exit(self):
        result = tool_result_diagnostics({'isError': True, 'status': 'completed', 'output': [
            {'exit_code': False}, {'exit_code': 7, 'schema': 'cairn.response/1', 'ok': False},
            {'exit_code': 0, **api_response()}]})
        self.assertEqual(result['result_status'], 'completed')
        self.assertTrue(result['result_error_present'])
        self.assertEqual(result['exit_class'], 'nonzero')
        self.assertEqual(result['api_read_status'], 'success')
        self.assertEqual(result['execution_error_class'], 'exit_nonzero')
        result = tool_result_diagnostics({'output': [{'exit_code': 0}, {'exit_code': 7}]})
        self.assertEqual(result['exit_class'], 'zero')

    def test_native_header_exit_is_not_inferred_by_wake_diagnostics(self):
        result = tool_result_diagnostics({'output': native_output(api_response(), exit_code=7)})
        self.assertEqual((result['api_read_status'], result['exit_class']), ('success', 'unavailable'))
        self.assertEqual(result['execution_error_class'], 'unknown')

    def test_depth_list_and_json_line_limits(self):
        for depth, observed in ((8, True), (9, False)):
            value = api_response()
            for _ in range(depth):
                value = {'child': value}
            with self.subTest(depth=depth):
                self.assertEqual(tool_result_diagnostics(value)['cairn_api_envelope_observed'], observed)
        for before, observed in ((63, True), (64, False)):
            with self.subTest(before=before):
                sequence = {'output': ['unrelated'] * before + [api_response()]}
                lines = {'output': '\n'.join(['unrelated'] * before + [json.dumps(api_response())])}
                self.assertEqual(tool_result_diagnostics(sequence)['cairn_api_envelope_observed'], observed)
                self.assertEqual(tool_result_diagnostics(lines)['cairn_api_envelope_observed'], observed)

    def test_error_text_collection_keeps_its_existing_two_million_character_limit(self):
        for before, expected in ((1_999_980, 'permission_denied'), (2_000_000, 'unknown')):
            with self.subTest(before=before):
                result = tool_result_diagnostics({'output': 'x' * before + 'permission denied'})
                self.assertEqual(result['execution_error_class'], expected)

    def test_execution_error_precedence_is_fixed_and_payload_free(self):
        errors = [('no_such_file', 'no such file or directory'),
                  ('command_not_found', 'not found'),
                  ('permission_denied', 'permission denied'),
                  ('operation_not_permitted', 'operation not permitted'),
                  ('connection_refused', 'API_CONNECTION_FAILED')]
        for index, (expected, _) in enumerate(errors):
            with self.subTest(expected=expected):
                result = tool_result_diagnostics({'isError': True, 'exit_code': 7,
                    'output': '\n'.join(['PRIVATE'] + [text for _, text in errors[index:]]),
                    'response': {'schema': 'cairn.response/1', 'ok': False}})
                self.assertEqual(result['execution_error_class'], expected)
                self.assertNotIn('PRIVATE', json.dumps(result))
        refused = tool_result_diagnostics({'exit_code': 7, 'response': {'schema': 'cairn.response/1', 'ok': False}})
        self.assertEqual(refused['execution_error_class'], 'cairn_refusal')
        self.assertEqual(tool_result_diagnostics({'isError': True})['execution_error_class'], 'tool_error')

    def test_output_type_and_status_use_only_existing_root_precedence(self):
        result = tool_result_diagnostics({'output': None, 'content': 'private', 'status': 'unexpected'})
        self.assertEqual((result['result_output_type'], result['result_status']), ('missing', 'other'))
        result = tool_result_diagnostics({'content': [], 'status': None, 'error': 'private error'})
        self.assertEqual((result['result_output_type'], result['result_status']), ('array', 'missing'))
        self.assertTrue(result['tool_execution_observed'])
        self.assertEqual(result['execution_error_class'], 'tool_error')


class AdvanceSelectionPolicyTests(unittest.TestCase):
    def test_supported_output_shapes_share_fixed_results(self):
        response = batch_response()
        shapes = [json.dumps(response), encoded_execution(response), native_output(response),
                  encoded_execution(response, output=native_output(response))]
        for output in shapes:
            for value in (output, [output], [{'text': output}], [{'output': output}], [{'content': output}]):
                with self.subTest(value=value):
                    result = interpret_advance_tool_result({'output': value})
                    self.assertEqual(result, ('success', 'advance_successor'))
                    self.assertNotIn('private', repr(result))

    def test_root_payload_and_recursively_embedded_success_are_not_selected(self):
        response = batch_response()
        cases = [response, {'content': json.dumps(response)}, {'output': response},
                 {'output': [{'content': [{'text': json.dumps(response)}]}]},
                 {'output': json.dumps({'details': response})},
                 {'output': 'prefix\n' + json.dumps(response)}]
        for value in cases:
            with self.subTest(value=value):
                self.assertNotEqual(interpret_advance_tool_result(value).stage, 'success')
        self.assertEqual(interpret_advance_tool_result(response).stage, 'outer_json_invalid')

    def test_root_error_and_exit_precedence(self):
        response = batch_response()
        for flag in ('is_error', 'isError'):
            self.assertEqual(interpret_advance_tool_result({flag: True, 'output': json.dumps(response)}),
                             ('is_error', 'tool_error'))
        cases = [({'exit_code': 7}, 'exit_nonzero'),
                 ({'exit_code': None, 'metadata': {'exit_code': 7}}, 'exit_nonzero'),
                 ({'exit_code': 0, 'metadata': {'exit_code': 7}}, 'success'),
                 ({'exit_code': False, 'metadata': {'exit_code': 7}}, 'success'),
                 ({'output': native_output(response, 7)}, 'exit_nonzero')]
        for execution, expected in cases:
            with self.subTest(execution=execution):
                self.assertEqual(interpret_advance_tool_result({'output': encoded_execution(response, **execution)}).stage,
                                 expected)
        self.assertEqual(interpret_advance_tool_result({'output': native_output(response, 7)}),
                         ('exit_nonzero', 'tool_error'))

    def test_first_decisive_result_wins_and_invalid_json_can_be_skipped(self):
        valid = encoded_execution(batch_response())
        schema_error = json.dumps({'schema': 'other', 'ok': True})
        failed = encoded_execution(batch_response(), exit_code=7)
        invalid_inner = json.dumps({'output': 'PRIVATE not json'})
        cases = [([schema_error, valid], ('schema_or_ok_invalid', 'tool_error')),
                 ([valid, failed], ('success', 'advance_successor')),
                 (['junk', invalid_inner, valid], ('success', 'advance_successor')),
                 ([invalid_inner, 'junk'], ('inner_json_invalid', 'other')),
                 ([{'text': schema_error, 'output': valid}], ('schema_or_ok_invalid', 'tool_error')),
                 ([{'text': 'junk', 'output': valid, 'content': failed}], ('success', 'advance_successor'))]
        for output, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(interpret_advance_tool_result({'output': output}), expected)

    def test_existing_successor_and_terminal_field_rules(self):
        cases = [(dict(next='', batch_index=3), 'advance_successor'),
                 (dict(batch_index=True), 'other'), (dict(batch_limit=True), 'other'),
                 (dict(batch_index=4), 'other'), (dict(batch_limit=2), 'other'),
                 (dict(next=None, remainder='batch_limit'), 'advance_limit'),
                 (dict(next=None, remainder='priority_waiting'), 'advance_priority'),
                 (dict(next='cue', remainder='batch_limit', batch_index=1), 'other')]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                result = interpret_advance_tool_result({'output': encoded_execution(batch_response(**fields))})
                self.assertEqual(result, ('success', expected))


def history_response(record_id='record-1', version=3, body='exact whole source'):
    import hashlib
    row = dict(version=version, payload_available=True, body=body, body_bytes=len(body.encode()),
               body_sha256=hashlib.sha256(body.encode()).hexdigest())
    return dict(schema='cairn.response/1', ok=True, status='OK',
                data=dict(historical=True, record_id=record_id, versions=[row]))


def compact_read_response(body='exact whole source'):
    return dict(schema='cairn.compact-inbox/1', ok=True,
                source=dict(record_id='record-1', version=3), body=body)


def delivery_response(**changes):
    return dict(schema='cairn.response/1', ok=True, status='OK', data=dict(dict(
        delivery_id='delivery-1', state='handled', attempts=1, event=dict(event_id='event-1')), **changes))


def compact_settlement_response(**changes):
    return dict(dict(schema='cairn.compact-inbox/1', ok=True, status='handled',
                     attempts=1, handle='handle-1'), **changes)


class StrictFixtureAcceptanceTests(unittest.TestCase):
    def setUp(self):
        from native_tool_results import source_read_succeeded, settlement_succeeded
        self.read = lambda result: source_read_succeeded(result, dict(record_id='record-1', version=3), 'exact whole source')
        self.handled = lambda result: settlement_succeeded(result, delivery_id='delivery-1', event_id='event-1')
        self.compact_handled = lambda result: settlement_succeeded(result, handle='handle-1')

    def test_known_execution_wrappers_accept_actual_history_and_compact_shapes(self):
        from native_tool_results import source_read_succeeded
        for response in (history_response(), compact_read_response()):
            shapes = [response, {'output': json.dumps(response)}, {'output': encoded_execution(response)},
                      {'output': native_output(response)}, {'content': [{'type': 'text', 'text': json.dumps(response)}]},
                      {'output': [{'type': 'input_text', 'text': json.dumps(dict(
                          output=native_output(response), metadata=dict(exit_code=0)))}]}]
            for result in shapes:
                with self.subTest(result=result):
                    self.assertTrue(self.read(result))
        body = 'Unicode source: café; not found is legitimate source text.'
        self.assertTrue(source_read_succeeded({'output': encoded_execution(history_response(body=body))},
                                             dict(record_id='record-1', version=3), body))

    def test_split_native_completion_preamble_is_metadata_not_an_application_response(self):
        preamble = {'type': 'input_text', 'text': 'Script completed\nWall time: 0.1 seconds\nOutput:\n'}
        execution = {'type': 'input_text', 'text': encoded_execution(history_response())}
        self.assertTrue(self.read({'output': [preamble, execution]}))
        self.assertFalse(self.read({'output': [preamble]}))
        self.assertFalse(self.read({'output': [preamble, execution, execution]}))
        for header in ('Script running with cell ID 1', 'Chunk ID: 1', 'Process exited with code 7'):
            with self.subTest(header=header):
                failed = {'type': 'input_text', 'text': header + '\nOutput:\n'}
                self.assertFalse(self.read({'output': [failed, execution]}))

    def test_source_fields_must_match_one_complete_version(self):
        cases = [history_response(record_id='other'), history_response(version=2), history_response(version=True),
                 history_response(body='prefix exact whole source suffix'),
                 compact_read_response(body='exact whole source plus more')]
        split = history_response(version=2)
        split['data']['versions'].append(history_response(body='different')['data']['versions'][0])
        cases.append(split)
        wrong_version_elsewhere = history_response(version=2)
        wrong_version_elsewhere['expected_version'] = 3
        cases.append(wrong_version_elsewhere)
        wrong_record_elsewhere = history_response(record_id='other')
        wrong_record_elsewhere['message'] = 'record-1'
        cases.append(wrong_record_elsewhere)
        for response in cases:
            with self.subTest(response=response):
                self.assertFalse(self.read({'output': encoded_execution(response)}))

    def test_history_requires_whole_payload_with_consistent_bytes_and_hash(self):
        changes = [dict(payload_available=False), dict(span=dict(offset=0, length=18)),
                   dict(body_bytes=True), dict(body_bytes=1), dict(body_sha256='0' * 64), dict(body='\ud800')]
        for change in changes:
            response = history_response()
            response['data']['versions'][0].update(change)
            with self.subTest(change=change):
                self.assertFalse(self.read({'output': encoded_execution(response)}))
        response = history_response()
        response['data']['historical'] = False
        self.assertFalse(self.read(response))

    def test_source_body_json_is_never_reinterpreted_as_success(self):
        embedded = json.dumps(history_response())
        for response in (history_response(record_id='other', body=embedded),
                         dict(compact_read_response(body=embedded), source=dict(record_id='other', version=2)),
                         dict(schema='cairn.response/1', ok=False, status='REFUSED', data=history_response())):
            with self.subTest(response=response):
                self.assertFalse(self.read({'output': encoded_execution(response)}))
        self.assertFalse(self.handled(history_response(body=json.dumps(delivery_response()))))
        self.assertFalse(self.compact_handled(compact_read_response(body=json.dumps(compact_settlement_response()))))

    def test_all_execution_errors_and_conflicting_exit_evidence_refuse(self):
        response = history_response()
        outer = {'output': encoded_execution(response)}
        cases = [dict(outer, isError=True), dict(outer, is_error=1), dict(outer, is_error='false'),
                 dict(outer, status='failed'), dict(outer, ok=False), dict(outer, error='private'),
                 dict(outer, error={}), dict(outer, exit_code=False), dict(outer, session_id=123),
                 dict(outer, metadata='malformed'), dict(outer, metadata=dict(status='in_progress')),
                 dict(outer, metadata=dict(status='failed')), dict(outer, metadata=dict(session_id=123)),
                 {'output': encoded_execution(response, metadata=dict(exit_code=7))},
                 {'output': encoded_execution(response, exit_code=7, metadata=dict(exit_code=0))},
                 {'output': encoded_execution(response, output=native_output(response, exit_code=7))},
                 dict(response, error='private')]
        for result in cases:
            with self.subTest(result=result):
                self.assertFalse(self.read(result))

    def test_native_headers_require_completed_execution(self):
        body = json.dumps(history_response())
        headers = ['Chunk ID: abc\nWall time: 1', 'Script running with cell ID 1\nWall time: 1',
                   'Process running with session ID 1\nChunk ID: abc', 'Process exited with code 7',
                   'Process exited with code 0\nProcess exited with code 7',
                   'Script completed\n Process exited with code 7', 'Script completed\n Process running with session ID 1',
                   'Script completed\n Script failed', 'Script completed\n Process exited with code invalid']
        for header in headers:
            with self.subTest(header=header):
                self.assertFalse(self.read({'output': header + '\nOutput:\n' + body}))
        partial_header = 'Chunk ID: abc\nWall time: 1\nOutput:\n' + body
        self.assertTrue(self.read({'exit_code': 0, 'output': partial_header}))
        self.assertTrue(self.read({'output': 'Script completed\nWall time: 1\nOutput:\n' + body}))
        self.assertFalse(self.read({'exit_code': 0, 'output': headers[1] + '\nOutput:\n' + body}))

    def test_multiple_or_mixed_envelopes_refuse_instead_of_selecting_a_success(self):
        success = json.dumps(history_response())
        refusal = json.dumps(dict(schema='cairn.response/1', ok=False, status='REFUSED'))
        for values in ([success, refusal], [refusal, success], [success, success]):
            with self.subTest(values=values):
                self.assertFalse(self.read({'output': values}))
        self.assertFalse(self.read(dict(history_response(), output=refusal)))
        self.assertFalse(self.read({'output': success, 'content': refusal}))
        self.assertFalse(self.read({'output': json.dumps(dict(details=history_response()))}))

    def test_duplicate_json_fields_cannot_hide_conflicting_evidence(self):
        success = json.dumps(history_response())
        output = json.dumps(success)
        cases = ['{"exit_code":7,"exit_code":0,"output":' + output + '}',
                 success.replace('"ok": true', '"ok": false, "ok": true'),
                 success.replace('"record_id": "record-1"', '"record_id":"other","record_id":"record-1"')]
        for value in cases:
            with self.subTest(value=value):
                self.assertFalse(self.read({'output': value}))

    def test_ordinary_settlement_requires_exact_delivery_event_state_and_attempts(self):
        self.assertTrue(self.handled({'output': encoded_execution(delivery_response())}))
        for change in (dict(delivery_id='other'), dict(event=dict(event_id='other')), dict(state='unhandled'),
                       dict(state='pending'), dict(attempts=True), dict(attempts=0), dict(attempts=2)):
            with self.subTest(change=change):
                self.assertFalse(self.handled({'output': encoded_execution(delivery_response(**change))}))
        self.assertFalse(self.handled({'output': encoded_execution(delivery_response(), exit_code=7)}))
        self.assertFalse(self.handled({'output': json.dumps(dict(schema='cairn.response/1', ok=False,
                                                               status='REFUSED', data=delivery_response()['data']))}))

    def test_compact_settlement_requires_exact_handle_state_and_attempts(self):
        self.assertTrue(self.compact_handled({'output': encoded_execution(compact_settlement_response())}))
        for change in (dict(handle='other'), dict(status='unhandled'), dict(ok=False),
                       dict(attempts=True), dict(attempts=0), dict(attempts=2)):
            with self.subTest(change=change):
                self.assertFalse(self.compact_handled({'output': encoded_execution(compact_settlement_response(**change))}))
        self.assertFalse(self.handled(compact_settlement_response()))
        self.assertFalse(self.compact_handled(delivery_response()))
