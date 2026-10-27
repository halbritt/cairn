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
