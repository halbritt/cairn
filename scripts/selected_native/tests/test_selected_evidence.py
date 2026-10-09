import base64
import copy
import hashlib
import json
import unittest

from ..evidence import EvidenceError, Ledger, PULL, SEARCH


RECEIPT = '10000000-0000-4000-8000-000000000001'
RECORD = '20000000-0000-4000-8000-000000000001'
HANDLE = '30000000-0000-4000-8000-000000000001'
REQUEST = '40000000-0000-4000-8000-000000000001'
PULL_REQUEST = '40000000-0000-4000-8000-000000000002'
BODY = 'new decision: preserve the failed attempt; next source review only'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def selection(body=BODY):
    return {'record': {'record_id': RECORD, 'version': 7, 'class': 'A', 'body': body}}


def search(body=BODY):
    return {'schema': 'cairn.mcp-search/1', 'source_schema': 'cairn.semantic/17',
            'request_id': REQUEST, 'receipt_id': RECEIPT, 'selected': [], 'bytes_remaining': 5000,
            'credits_remaining': 4,
            'optional_limit': 3200, 'available_tokens': 32000,
            'memory_budget': {'schema': 'cairn.memory-budget/2', 'bytes': 9500, 'min_pull_bytes': 5000},
            'policy_revision': {'revision_id': '50000000-0000-4000-8000-000000000001'},
            'index': [{'record_id': RECORD, 'version': 7, 'class': 'A', 'kind': 'note',
                       'summary': 'new decision', 'body_sha256': sha(body.encode()),
                       'pull_arguments': {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE}}]}


def offered(body=BODY):
    ledger = Ledger()
    ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current decision',
                                    'semantic_query': 'unfinished work next safe action'})
    ledger.response('search', json.dumps(search(body)))
    return ledger


def partial(body=BODY, offset=14, length=12, opening=0):
    data = body.encode()
    end = min(offset+length, len(data))
    fragment = data[offset:end]
    span = {'offset': offset, 'end': end, 'total_bytes': len(data),
            'sha256': sha(fragment), 'source_sha256': sha(data)}
    try:
        span['body'] = fragment.decode()
    except UnicodeDecodeError:
        span['body_base64'] = base64.b64encode(fragment).decode()
    result = {'selection': selection(''), 'span': span, 'bytes_remaining': 3900, 'credits_remaining': 3}
    if opening:
        result['source_opening'] = {'offset': 0, 'end': opening, 'total_bytes': len(data),
                                    'sha256': sha(data[:opening]), 'body': data[:opening].decode()}
    return result


def pull(ledger, result, offset=14, length=12):
    args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE,
            'span': {'offset': offset, 'length': length}}
    ledger.request('pull', PULL, args)
    ledger.response('pull', [{'type': 'text', 'text': json.dumps(result)}])
    return ledger.selected()['responses']['pull']['source']


def grouped():
    companion = selection('competing advisory position')
    companion['record']['record_id'] = '20000000-0000-4000-8000-000000000002'
    group = [{'conflict_id': '60000000-0000-4000-8000-000000000001', 'version': 1,
              'members': [{'record_id': RECORD, 'version': 7},
                          {'record_id': companion['record']['record_id'], 'version': 7}]}]
    index = search()
    index['index'][0]['conflicts'] = copy.deepcopy(group)
    entry = copy.deepcopy(index['index'][0])
    entry.update(record_id=companion['record']['record_id'], body_sha256=sha(companion['record']['body'].encode()))
    entry['pull_arguments']['handle'] = '30000000-0000-4000-8000-000000000002'
    entry['pull_arguments']['request_id'] = '40000000-0000-4000-8000-000000000003'
    index['index'].append(entry)
    primary = selection()
    primary['conflicts'] = copy.deepcopy(group)
    companion['conflicts'] = copy.deepcopy(group)
    result = {'selection': primary, 'competing': [companion], 'bytes_remaining': 300, 'credits_remaining': 3}
    return index, result


class SelectedEvidenceTest(unittest.TestCase):
    def test_search_queries_budget_and_offered_arguments_survive_without_unknown_fields(self):
        ledger = Ledger()
        arguments = {'request_id': REQUEST, 'query': 'actual lexical phrase',
                     'semantic_query': 'different semantic intent', 'memory_budget_bytes': 9500}
        ledger.request('search', SEARCH, arguments)
        result = search()
        result['private_unregistered_field'] = 'RAW_EVENT_SENTINEL'
        result['index'][0]['unregistered'] = 'RAW_EVENT_SENTINEL'
        ledger.response('search', json.dumps(result))
        got = ledger.selected()
        self.assertEqual(got['calls']['search']['arguments'], arguments)
        self.assertEqual(got['responses']['search']['optional_limit'], 3200)
        self.assertEqual(got['responses']['search']['policy_revision'], result['policy_revision'])
        self.assertEqual(got['responses']['search']['index'][0]['pull_arguments'], result['index'][0]['pull_arguments'])
        self.assertNotIn('RAW_EVENT_SENTINEL', json.dumps(got))
        arguments['query'] = 'changed after observation'
        got['responses']['search']['index'].clear()
        self.assertEqual(ledger.selected()['calls']['search']['arguments']['query'], 'actual lexical phrase')
        self.assertEqual(len(ledger.selected()['responses']['search']['index']), 1)

    def test_partial_bytes_survive_empty_record_body_and_never_become_whole_source(self):
        source = pull(offered(), partial())
        returned = base64.b64decode(source['span']['body_base64'])
        self.assertEqual(returned, BODY.encode()[14:26])
        self.assertEqual(source['source_sha256'], sha(BODY.encode()))
        self.assertEqual(source['coverage'], 'returned_byte_range')
        self.assertFalse(source['span']['range_covers_source'])
        self.assertNotIn('body_sha256', source)
        self.assertNotIn('body', source)

    def test_utf8_fragment_eof_and_full_span_retain_exact_byte_ranges(self):
        body = 'x😀z'
        cases = [(2, 1, b'\x9f', False), (4, 8, b'\x80z', False), (0, 6, body.encode(), True)]
        for offset, length, expected, whole_range in cases:
            with self.subTest(offset=offset, length=length):
                source = pull(offered(body), partial(body, offset, length), offset, length)
                self.assertEqual(base64.b64decode(source['span']['body_base64']), expected)
                self.assertEqual(source['span']['range_covers_source'], whole_range)
                self.assertEqual(source['coverage'], 'returned_byte_range')

    def test_source_opening_keeps_distinct_nonoverlapping_bytes(self):
        source = pull(offered(), partial(opening=10))
        self.assertEqual(base64.b64decode(source['source_opening']['body_base64']), BODY.encode()[:10])
        self.assertEqual(source['source_opening']['end'], 10)
        self.assertEqual(source['span']['offset'], 14)

    def test_whole_pull_keeps_body_and_link_to_delivered_handle(self):
        ledger = offered()
        args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE}
        ledger.request('pull', PULL, args)
        ledger.response('pull', json.dumps({'selection': selection(), 'bytes_remaining': 900, 'credits_remaining': 3}))
        got = ledger.selected()['responses']['pull']
        self.assertEqual(got['source']['body'], BODY)
        self.assertEqual(got['source']['body_sha256'], sha(BODY.encode()))
        self.assertEqual(got['offered_arguments'], args)
        self.assertEqual(got['source']['coverage'], 'whole_returned_body')

    def test_unoffered_handle_and_wrong_record_version_are_rejected(self):
        ledger = offered()
        args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT,
                'handle': '30000000-0000-4000-8000-000000000099'}
        with self.assertRaisesRegex(EvidenceError, 'pull_handle_not_delivered'):
            ledger.request('pull', PULL, args)
        self.assertNotIn('pull', ledger.selected()['calls'])
        result = partial()
        result['selection']['record']['version'] = 8
        with self.assertRaisesRegex(EvidenceError, 'source_not_offered'):
            pull(ledger, result)
        self.assertNotIn('pull', ledger.selected()['responses'])

    def test_malformed_span_is_rejected_without_committing_response(self):
        mutations = [('span_not_requested', lambda r: r.update(span=partial(offset=15)['span'])),
                     ('span_bytes_hash_mismatch', lambda r: r['span'].update(sha256='0'*64)),
                     ('span_source_not_offered', lambda r: r['span'].update(source_sha256='0'*64)),
                     ('span_shape', lambda r: r['span'].update(body_base64='YQ==')),
                     ('opening_overlap_or_source_size', lambda r: r.update(source_opening={'offset': 0, 'end': 15,
                         'total_bytes': len(BODY), 'sha256': sha(BODY.encode()[:15]), 'body': BODY[:15]})),
                     ('base64_invalid', lambda r: r.update(span={**{k: v for k, v in r['span'].items() if k != 'body'},
                                                                'body_base64': '***'})),
                     ('base64_noncanonical', lambda r: r.update(span={**{k: v for k, v in r['span'].items() if k != 'body'},
                                                                     'body_base64': 'YR=='}))]
        for label, mutate in mutations:
            with self.subTest(label=label):
                result, ledger = partial(), offered()
                mutate(result)
                with self.assertRaisesRegex(EvidenceError, label):
                    pull(ledger, result)
                self.assertNotIn('pull', ledger.selected()['responses'])

    def test_whole_response_cannot_fill_partial_request_or_forge_empty_body_source(self):
        with self.assertRaisesRegex(EvidenceError, 'whole_for_partial_request'):
            pull(offered(), {'selection': selection(), 'bytes_remaining': 300, 'credits_remaining': 3})
        ledger = offered()
        ledger.request('pull', PULL, {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE})
        with self.assertRaisesRegex(EvidenceError, 'whole_hash_not_offered'):
            ledger.response('pull', json.dumps({'selection': selection(''), 'bytes_remaining': 300, 'credits_remaining': 3}))

    def test_duplicate_payload_keys_and_duplicate_responses_are_rejected(self):
        ledger = offered()
        with self.assertRaisesRegex(EvidenceError, 'response_identity'):
            ledger.response('search', json.dumps(search()))
        ledger = Ledger()
        ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
        duplicate = json.dumps(search())[:-1] + ',"receipt_id":"'+RECEIPT+'"}'
        with self.assertRaisesRegex(EvidenceError, 'duplicate_json_key'):
            ledger.response('search', duplicate)
        self.assertEqual(ledger.selected()['responses'], {})
        with self.assertRaisesRegex(EvidenceError, 'pull_handle_not_delivered'):
            ledger.request('pull', PULL, search()['index'][0]['pull_arguments'])

    def test_tool_error_preserves_call_linkage_without_private_error_text(self):
        ledger = offered()
        ledger.request('pull', PULL, {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE})
        ledger.response('pull', 'BUDGET_REFUSED: SECRET_EXCEPTION_SENTINEL', is_error=True)
        got = ledger.selected()
        self.assertTrue(got['responses']['pull']['is_error'])
        self.assertEqual(got['calls']['pull']['arguments']['handle'], HANDLE)
        self.assertNotIn('SECRET_EXCEPTION_SENTINEL', json.dumps(got))

    def test_unknown_nested_request_fields_are_refused_before_capture(self):
        for key, value in [('context', {'secret': 'PRIVATE_SENTINEL'}),
                           ('entities', [{'kind': 'file', 'name': 'core/index.go', 'secret': 'PRIVATE_SENTINEL'}])]:
            with self.subTest(key=key):
                ledger = Ledger()
                with self.assertRaises(EvidenceError):
                    ledger.request('search', SEARCH, {'query': 'current', key: value})
                self.assertEqual(ledger.selected()['calls'], {})
                self.assertNotIn('PRIVATE_SENTINEL', json.dumps(ledger.selected()))

    def test_competing_whole_sources_remain_distinct_and_cannot_be_partial(self):
        ledger = Ledger()
        ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
        companion = selection('competing advisory position')
        companion['record']['record_id'] = '20000000-0000-4000-8000-000000000002'
        conflicts = [{'conflict_id': '60000000-0000-4000-8000-000000000001', 'version': 1,
                      'members': [{'record_id': RECORD, 'version': 7},
                                  {'record_id': companion['record']['record_id'], 'version': 7}]}]
        offered_result = search()
        offered_result['index'][0]['conflicts'] = conflicts
        entry = copy.deepcopy(offered_result['index'][0])
        entry.update(record_id=companion['record']['record_id'], body_sha256=sha(companion['record']['body'].encode()))
        entry['pull_arguments']['handle'] = '30000000-0000-4000-8000-000000000002'
        entry['pull_arguments']['request_id'] = '40000000-0000-4000-8000-000000000003'
        offered_result['index'].append(entry)
        ledger.response('search', json.dumps(offered_result))
        primary = selection()
        primary['conflicts'] = conflicts
        companion['conflicts'] = conflicts
        ledger.request('pull', PULL, search()['index'][0]['pull_arguments'])
        ledger.response('pull', json.dumps({'selection': primary, 'competing': [companion],
                                         'bytes_remaining': 300, 'credits_remaining': 3}))
        got = ledger.selected()['responses']['pull']
        self.assertEqual(got['source']['body'], BODY)
        self.assertEqual(got['competing'][0]['body'], 'competing advisory position')
        self.assertNotEqual(got['source']['record_id'], got['competing'][0]['record_id'])
        result = partial()
        result['competing'] = [companion]
        with self.assertRaisesRegex(EvidenceError, 'partial_delivery_shape'):
            pull(offered(), result)

    def test_companion_without_offered_hash_and_truncated_group_are_rejected(self):
        companion = selection('unknown advisory position')
        companion['record']['record_id'] = '20000000-0000-4000-8000-000000000002'
        result = {'selection': selection(), 'competing': [companion], 'bytes_remaining': 100, 'credits_remaining': 3}
        ledger = offered()
        ledger.request('pull', PULL, search()['index'][0]['pull_arguments'])
        with self.assertRaisesRegex(EvidenceError, 'competing_not_offered'):
            ledger.response('pull', json.dumps(result))
        self.assertNotIn('pull', ledger.selected()['responses'])
        # A complete descriptor cannot be silently reduced to one delivered position.
        group = [{'conflict_id': '60000000-0000-4000-8000-000000000001', 'version': 1,
                  'members': [{'record_id': RECORD, 'version': 7},
                              {'record_id': companion['record']['record_id'], 'version': 7}]}]
        ledger = Ledger()
        ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
        index = search()
        index['index'][0]['conflicts'] = group
        ledger.response('search', json.dumps(index))
        result['selection']['conflicts'] = group
        result['competing'] = []
        ledger.request('pull', PULL, search()['index'][0]['pull_arguments'])
        with self.assertRaisesRegex(EvidenceError, 'competing_group_incomplete'):
            ledger.response('pull', json.dumps(result))

    def test_stable_search_retry_keeps_first_offer_but_records_new_pull_request(self):
        ledger = offered()
        args = copy.deepcopy(ledger.selected()['calls']['search']['arguments'])
        ledger.request('retry', SEARCH, args)
        result = search()
        fresh = '40000000-0000-4000-8000-000000000009'
        result['index'][0]['pull_arguments']['request_id'] = fresh
        ledger.response('retry', json.dumps(result))
        pull_args = result['index'][0]['pull_arguments']
        ledger.request('pull', PULL, pull_args)
        ledger.response('pull', json.dumps({'selection': selection(), 'bytes_remaining': 100, 'credits_remaining': 3}))
        selected = ledger.selected()
        self.assertEqual(selected['responses']['retry']['index'][0]['pull_arguments']['request_id'], fresh)
        self.assertEqual(selected['calls']['pull']['arguments']['request_id'], fresh)
        self.assertEqual(selected['responses']['pull']['offered_arguments']['request_id'], PULL_REQUEST)
        ledger.request('changed', SEARCH, args)
        result['index'][0]['body_sha256'] = '0'*64
        with self.assertRaisesRegex(EvidenceError, 'changed_replayed_offer'):
            ledger.response('changed', json.dumps(result))
        self.assertNotIn('changed', ledger.selected()['responses'])

    def test_partial_conflict_or_instruction_refuses_even_without_companions(self):
        for case in ['conflict', 'instruction']:
            with self.subTest(case=case):
                ledger = Ledger()
                ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
                index, response = search(), partial()
                if case == 'instruction':
                    index['index'][0]['class'] = 'C'
                    response['selection']['record']['class'] = 'C'
                else:
                    response['selection']['conflicts'] = [{'members': []}]
                ledger.response('search', json.dumps(index))
                with self.assertRaisesRegex(EvidenceError, 'partial_delivery_shape'):
                    pull(ledger, response)

    def test_search_request_mismatch_wrong_pull_receipt_and_content_cap_refuse(self):
        ledger = Ledger()
        ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
        result = search()
        result['request_id'] = PULL_REQUEST
        with self.assertRaisesRegex(EvidenceError, 'search_request_mismatch'):
            ledger.response('search', json.dumps(result))
        ledger = offered()
        args = search()['index'][0]['pull_arguments']
        args['receipt_id'] = '10000000-0000-4000-8000-000000000099'
        with self.assertRaisesRegex(EvidenceError, 'pull_handle_not_delivered'):
            ledger.request('pull', PULL, args)
        ledger.request('bounded', SEARCH, {'query': 'current'})
        with self.assertRaisesRegex(EvidenceError, 'content_bounds'):
            ledger.response('bounded', json.dumps({'body': 'x'*32000}))

    def test_nonfinite_unicode_and_deep_response_never_commit_unreadable_state(self):
        nested = {}
        for _ in range(34):
            nested = {'inner': nested}
        cases = [('NaN', 'json_nonfinite'), ('Infinity', 'json_nonfinite'), ('9'*5000, 'content_not_json'),
                 ('"\\ud800"', 'arguments_not_bounded_json'), (json.dumps(nested), 'json_tree_bounds')]
        for raw, error in cases:
            with self.subTest(raw=raw[:30]):
                ledger = Ledger()
                ledger.request('search', SEARCH, {'query': 'current'})
                with self.assertRaisesRegex(EvidenceError, error):
                    ledger.response('search', '{"discovery":'+raw+'}')
                self.assertEqual(ledger.selected()['responses'], {})
        ledger = Ledger()
        ledger.request('search', SEARCH, {'query': 'current'})
        with self.assertRaisesRegex(EvidenceError, 'invalid_unicode'):
            ledger.response('search', '{"x":"'+'\ud800'+'"}')

    def test_reported_numeric_counters_and_request_options_have_runtime_types(self):
        for key, value in [('bytes_remaining', '100'), ('credits_remaining', True), ('optional_limit', -1)]:
            with self.subTest(key=key):
                ledger = Ledger()
                ledger.request('search', SEARCH, {'query': 'current'})
                result = search()
                result[key] = value
                with self.assertRaisesRegex(EvidenceError, 'integer_out_of_range'):
                    ledger.response('search', json.dumps(result))
                self.assertEqual(ledger.selected()['responses'], {})
        for arguments, error in [({'kinds': {'secret': 'unselected'}}, 'kinds_shape'),
                                 ({'browse': {'secret': 'unselected'}}, 'boolean_argument'),
                                 ({'offset': True}, 'integer_out_of_range'),
                                 ({'opening_bytes': {'secret': 'unselected'}}, 'integer_out_of_range')]:
            with self.subTest(arguments=arguments):
                ledger = Ledger()
                with self.assertRaisesRegex(EvidenceError, error):
                    ledger.request('call', SEARCH if 'opening_bytes' not in arguments else PULL, arguments)
                self.assertEqual(ledger.selected()['calls'], {})

    def test_invalid_group_descriptors_or_companion_content_never_commit_evidence(self):
        cases = ['offered conflicts mismatch', 'duplicate companion', 'wrong companion body',
                 'returned primary conflicts mismatch', 'primary missing', 'version mismatch',
                 'group shape', 'members shape', 'reference shape', 'unlisted companion']
        labels = ['competing_not_in_conflict', 'duplicate_competing_source', 'whole_hash_not_offered',
                  'conflicts_not_offered', 'primary_not_in_conflict', 'conflict_version_mismatch',
                  'conflict_shape', 'conflict_members_shape', 'conflict_reference_shape', 'competing_not_in_conflict']
        for case, label in zip(cases, labels):
            with self.subTest(case=case):
                index, result = grouped()
                primary = result['selection']
                if case == 'offered conflicts mismatch':
                    index['index'][1]['conflicts'][0]['version'] = 2
                elif case == 'duplicate companion':
                    result['competing'].append(copy.deepcopy(result['competing'][0]))
                elif case == 'wrong companion body':
                    result['competing'][0]['record']['body'] = 'altered bytes'
                elif case == 'returned primary conflicts mismatch':
                    primary['conflicts'] = []
                else:
                    groups = copy.deepcopy(primary['conflicts'])
                    if case == 'primary missing':
                        groups[0]['members'][0]['record_id'] = '20000000-0000-4000-8000-000000000099'
                    elif case == 'version mismatch':
                        groups[0]['members'].append({'record_id': RECORD, 'version': 8})
                    elif case == 'group shape':
                        groups[0]['unexpected'] = True
                    elif case == 'members shape':
                        groups[0]['members'] = []
                    elif case == 'reference shape':
                        groups[0]['members'][0]['unexpected'] = True
                    elif case == 'unlisted companion':
                        groups[0]['members'][1]['record_id'] = '20000000-0000-4000-8000-000000000099'
                    primary['conflicts'] = copy.deepcopy(groups)
                    result['competing'][0]['conflicts'] = copy.deepcopy(groups)
                    for entry in index['index']:
                        entry['conflicts'] = copy.deepcopy(groups)
                ledger = Ledger()
                ledger.request('search', SEARCH, {'request_id': REQUEST, 'query': 'current'})
                ledger.response('search', json.dumps(index))
                ledger.request('pull', PULL, index['index'][0]['pull_arguments'])
                with self.assertRaisesRegex(EvidenceError, '^'+label+'$'):
                    ledger.response('pull', json.dumps(result))
                self.assertNotIn('pull', ledger.selected()['responses'])


if __name__ == '__main__':
    unittest.main()
