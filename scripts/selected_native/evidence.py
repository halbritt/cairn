"""Select delivered Cairn evidence only; no I/O, native launch or server calls."""
import base64
import copy
import hashlib
import json
import re
import uuid


class EvidenceError(ValueError):
    pass


SEARCH = 'mcp__cairn__cairn_search'
PULL = 'mcp__cairn__cairn_pull'
SEARCH_FIELDS = {'request_id', 'query', 'semantic_query', 'semantic', 'available_tokens',
                 'memory_budget_bytes', 'min_pull_bytes', 'inspection_policy', 'kinds',
                 'entities', 'context', 'advisory_conflicts', 'browse', 'offset',
                 'error_signature_sha256'}
PULL_FIELDS = {'request_id', 'receipt_id', 'handle', 'span', 'opening_bytes'}
SHA = re.compile(r'[0-9a-f]{64}\Z')
POLICY_1, POLICY_2 = 'first-fitting-whole/1', 'first-fitting-whole/2'
KNOWN_POLICIES = frozenset({'', POLICY_1, POLICY_2})
ACCEPTED_POLICIES = ('', POLICY_1)                    # the accepted reader: /2 is refused
CURRENT_READ_POLICIES = ('', POLICY_1, POLICY_2)      # b5ed072 and main 554037b (core/memory_budget.go)
POLICY_SCHEMA = {POLICY_1: 'cairn.memory-budget/3', POLICY_2: 'cairn.memory-budget/4'}
TOOL_ROOM = 32000                                     # original numeric tool ceiling; /2 never widens it


def require(condition, label):
    if not condition:
        raise EvidenceError(label)


def ident(value):
    require(isinstance(value, str), 'identity_not_text')
    try:
        require(str(uuid.UUID(value)) == value, 'identity_not_canonical')
    except ValueError as exc:
        raise EvidenceError('identity_invalid') from exc
    return value


def number(value, low=0, high=65536):
    require(type(value) is int and low <= value <= high, 'integer_out_of_range')
    return value


def digest(value):
    require(isinstance(value, str) and SHA.fullmatch(value), 'digest_invalid')
    return value


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise EvidenceError('arguments_not_bounded_json') from exc


def utf8(value):
    require(isinstance(value, str), 'text_type')
    try:
        return value.encode()
    except UnicodeEncodeError as exc:
        raise EvidenceError('invalid_unicode') from exc


def bounded_tree(value):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(depth <= 32 and nodes <= 4096, 'json_tree_bounds')
        if isinstance(item, dict):
            pending.extend((v, depth+1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth+1) for v in item)


def reject_constant(_):
    raise EvidenceError('json_nonfinite')


def tool_json(content):
    """One actual text JSON payload, not recursive matches in arbitrary text."""
    if isinstance(content, list):
        require(len(content) == 1 and isinstance(content[0], dict)
                and content[0].get('type') == 'text', 'content_not_one_text')
        content = content[0].get('text')
    require(len(utf8(content)) <= 32000, 'content_bounds')
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate_json_key')
            out[key] = value
        return out
    try:
        value = json.loads(content, object_pairs_hook=pairs, parse_constant=reject_constant)
    except EvidenceError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvidenceError('content_not_json') from exc
    require(isinstance(value, dict), 'content_not_object')
    bounded_tree(value)
    encoded(value)  # Reject surrogate escapes anywhere in the parsed object.
    return value


def inspection_budget(policy, budget, entries):
    """Response-side mirror of core.validateMemoryBudget for a declared inspection policy.
    Returns the ceiling for bytes_remaining. /2 uses the declared room (never above the 32000 tool
    ceiling); /1 and no policy keep the original 24000. Credits and every other bound are unchanged."""
    if budget is None:
        require(policy == '', 'inspection_policy_response_mismatch')
        return 24000
    require(isinstance(budget, dict) and type(budget.get('bytes')) is int and 256 <= budget['bytes'] <= TOOL_ROOM,
            'memory_budget_shape')
    require(budget.get('inspection_policy', '') == policy, 'inspection_policy_response_mismatch')
    if policy == '':
        require(budget.get('schema') in ('cairn.memory-budget/1', 'cairn.memory-budget/2')
                and 'inspection_status' not in budget and 'skipped_units' not in budget, 'memory_budget_policy_schema')
        return 24000
    require(budget.get('schema') == POLICY_SCHEMA[policy], 'memory_budget_policy_schema')
    room = budget['bytes'] if policy == POLICY_2 else min(24000, budget['bytes'])
    status, reserve, skipped = budget.get('inspection_status'), budget.get('min_pull_bytes', 0), budget.get('skipped_units', 0)
    require(type(reserve) is int and type(skipped) is int and 0 <= skipped <= 10000, 'memory_budget_inspection')
    if status == 'ready':
        require(0 < reserve <= room and len(entries) > 0, 'memory_budget_inspection')
    elif status == 'no_whole_fits':
        require(reserve == 0 and len(entries) == 0, 'memory_budget_inspection')
    elif status == 'no_candidates':
        require(reserve == 0 and skipped == 0 and len(entries) == 0, 'memory_budget_inspection')
    else:
        raise EvidenceError('memory_budget_inspection')
    return min(TOOL_ROOM, room) if policy == POLICY_2 else 24000


def source(record, expected=None):
    require(isinstance(record, dict), 'record_not_object')
    out = {'record_id': ident(record.get('record_id')),
           'version': number(record.get('version'), 1, 2147483647)}
    require(isinstance(record.get('class'), str) and record['class'] in {'A', 'B', 'C', 'D'}, 'record_class')
    out['class'] = record['class']
    if expected:
        require((out['record_id'], out['version'], out['class']) ==
                (expected['record_id'], expected['version'], expected['class']), 'source_not_offered')
    return out


def body_source(selection, expected=None):
    require(isinstance(selection, dict), 'selection_not_object')
    record = selection.get('record')
    out = source(record, expected)
    body = record.get('body')
    data = utf8(body)
    require(len(data) <= 65536, 'body_bounds')
    out.update({'body': body, 'body_sha256': hashlib.sha256(data).hexdigest(),
                'coverage': 'whole_returned_body'})
    if expected:
        require(out['body_sha256'] == expected['body_sha256'], 'whole_hash_not_offered')
    return out


def span_bytes(span):
    require(isinstance(span, dict), 'span_not_object')
    offset = number(span.get('offset'))
    end = number(span.get('end'))
    total = number(span.get('total_bytes'), 1, 2147483647)
    require(offset < end <= total and ('body' in span) != ('body_base64' in span), 'span_shape')
    if 'body' in span:
        data = utf8(span['body'])
    else:
        require(isinstance(span['body_base64'], str) and len(span['body_base64']) <= 87384, 'base64_bounds')
        try:
            data = base64.b64decode(span['body_base64'], validate=True)
        except ValueError as exc:
            raise EvidenceError('base64_invalid') from exc
        require(base64.b64encode(data).decode() == span['body_base64'], 'base64_noncanonical')
    require(len(data) == end-offset and hashlib.sha256(data).hexdigest() == digest(span.get('sha256')),
            'span_bytes_hash_mismatch')
    out = {key: span[key] for key in ['offset', 'end', 'total_bytes', 'sha256']}
    out['body_base64'] = base64.b64encode(data).decode()
    out['range_covers_source'] = offset == 0 and end == total
    return out


class Ledger:
    """Caller owns copies; a rejected response changes no offers or response state."""
    def __init__(self, inspection_policies=ACCEPTED_POLICIES):
        require(isinstance(inspection_policies, (tuple, frozenset, set)) and inspection_policies
                and set(inspection_policies) <= KNOWN_POLICIES, 'ledger_policies')
        self.policies = frozenset(inspection_policies)
        self.calls = {}
        self.responses = {}
        self.offers = {}
        self.receipt_limits = {}

    def request(self, call_id, name, arguments):
        require(isinstance(call_id, str) and 0 < len(call_id) <= 256 and call_id not in self.calls,
                'call_identity')
        require(isinstance(name, str) and name in {SEARCH, PULL} and isinstance(arguments, dict), 'call_shape')
        require(set(arguments) <= (SEARCH_FIELDS if name == SEARCH else PULL_FIELDS), 'unknown_argument')
        bounded_tree(arguments)
        require(len(encoded(arguments)) <= 8192, 'arguments_bounds')
        for key in ['query', 'semantic_query']:
            if key in arguments:
                require(len(utf8(arguments[key])) <= 4096, 'query_bounds')
        for key in ['semantic', 'browse', 'advisory_conflicts']:
            if key in arguments:
                require(type(arguments[key]) is bool, 'boolean_argument')
        for key, low, high in [('available_tokens', 256, 32000), ('memory_budget_bytes', 256, 32000),
                               ('min_pull_bytes', 1, 24000), ('offset', 0, 10000), ('opening_bytes', 0, 768)]:
            if key in arguments and arguments[key] is not None:
                number(arguments[key], low, high)
        if 'kinds' in arguments and arguments['kinds'] is not None:
            require(isinstance(arguments['kinds'], list) and len(arguments['kinds']) <= 8
                    and all(isinstance(v, str) and v in {'note', 'observation', 'claim', 'lesson',
                        'procedure', 'decision', 'preference', 'instruction'} for v in arguments['kinds']), 'kinds_shape')
        if 'inspection_policy' in arguments:
            require(arguments['inspection_policy'] in self.policies, 'inspection_policy_shape')
        if 'error_signature_sha256' in arguments:
            digest(arguments['error_signature_sha256'])
        if 'context' in arguments and arguments['context'] is not None:
            context = arguments['context']
            require(isinstance(context, dict) and set(context) <=
                    {'binding_id', 'capability_id', 'revision', 'task_class', 'task_phase', 'workspace_sha256'}
                    and all(isinstance(v, str) and len(utf8(v)) <= 512 for v in context.values()),
                    'context_shape')
        if 'entities' in arguments and arguments['entities'] is not None:
            entities = arguments['entities']
            require(isinstance(entities, list) and len(entities) <= 16
                    and all(isinstance(v, dict) and set(v) == {'kind', 'name'}
                            and isinstance(v['kind'], str) and v['kind'] in {'file', 'symbol'} and isinstance(v['name'], str)
                            and len(utf8(v['name'])) <= 4096 for v in entities), 'entities_shape')
        if 'request_id' in arguments:
            ident(arguments['request_id'])
        if name == PULL:
            require(all(key in arguments for key in ['request_id', 'receipt_id', 'handle']), 'pull_arguments_incomplete')
            receipt, handle = ident(arguments['receipt_id']), ident(arguments['handle'])
            require((receipt, handle) in self.offers, 'pull_handle_not_delivered')
            if 'span' in arguments and arguments['span'] is not None:
                span = arguments['span']
                require(isinstance(span, dict) and set(span) == {'offset', 'length'}, 'requested_span_shape')
                number(span['offset'], 0, 65535)
                number(span['length'], 1)
            require(not arguments.get('opening_bytes') or arguments.get('span') is not None,
                    'opening_requires_span')
        self.calls[call_id] = {'name': name, 'arguments': copy.deepcopy(arguments),
                               'canonical_arguments_sha256': hashlib.sha256(encoded(arguments)).hexdigest()}

    def response(self, call_id, content, is_error=False):
        require(isinstance(call_id, str) and call_id in self.calls and call_id not in self.responses, 'response_identity')
        call = self.calls[call_id]
        require(type(is_error) is bool, 'error_flag')
        if is_error:
            # Error strings can contain private details; no error text is retained.
            self.responses[call_id] = {'is_error': True, 'payload_retained': False}
            return
        value = tool_json(content)
        offers = {}
        args = call['arguments']
        if call['name'] == SEARCH:
            require(value.get('schema') == 'cairn.mcp-search/1', 'search_schema')
            receipt = ident(value.get('receipt_id'))
            request = ident(value.get('request_id'))
            require('request_id' not in args or request == args['request_id'], 'search_request_mismatch')
            index = value.get('index')
            require(isinstance(index, list) and len(index) <= 100, 'index_bounds')
            record_ids = set()
            for entry in index:
                info = source(entry)
                info['body_sha256'] = digest(entry.get('body_sha256'))
                info['conflicts'] = copy.deepcopy(entry.get('conflicts', []))
                require(info['record_id'] not in record_ids, 'duplicate_index_record')
                record_ids.add(info['record_id'])
                pull = entry.get('pull_arguments')
                require(isinstance(pull, dict) and set(pull) == {'request_id', 'receipt_id', 'handle'}, 'offered_pull_shape')
                ident(pull['request_id'])
                handle = ident(pull['handle'])
                require(pull['receipt_id'] == receipt and (receipt, handle) not in offers, 'offered_handle_mismatch')
                previous = self.offers.get((receipt, handle))
                if previous:
                    require(all(info[k] == previous[k] for k in
                                ['record_id', 'version', 'class', 'body_sha256', 'conflicts']), 'changed_replayed_offer')
                info['offered_arguments'] = copy.deepcopy(pull)
                info['offer_history'] = copy.deepcopy(previous['offer_history']) if previous else []
                info['offer_history'].append({'search_call_id': call_id, 'arguments': copy.deepcopy(pull)})
                offers[(receipt, handle)] = info
            require(isinstance(value.get('selected'), list), 'selected_shape')
            selected = [body_source(item) for item in value['selected']]
            out = {key: copy.deepcopy(value[key]) for key in
                   ['request_id', 'receipt_id', 'source_schema', 'source_seal', 'scope', 'query',
                    'policy', 'policy_revision', 'ranking', 'tokenizer', 'available_tokens',
                    'optional_limit', 'memory_budget', 'discovery', 'omitted', 'bytes_remaining',
                    'credits_remaining'] if key in value}
            policy = args.get('inspection_policy') or ''
            ceiling = 24000   # the accepted reader's bound; only a ledger that declares /2 applies the mirror below
            if POLICY_2 in self.policies:
                ceiling = inspection_budget(policy, value.get('memory_budget'), index)
                require(policy == '' or value['memory_budget']['bytes'] == args.get('memory_budget_bytes'),
                        'memory_budget_response_mismatch')
            for key, low, high in [('available_tokens', 256, 32000), ('optional_limit', 0, 2147483647),
                                   ('bytes_remaining', 0, ceiling), ('credits_remaining', 0, 4)]:
                number(value.get(key), low, high)
            projected = [{key: copy.deepcopy(entry[key]) for key in
                          ['record_id', 'version', 'class', 'kind', 'summary', 'body_sha256',
                           'summary_span', 'match_span', 'pull_arguments', 'conflicts', 'category',
                           'entities', 'entities_omitted'] if key in entry}
                         for entry in index]
            out.update({'selected_sources': selected, 'index': projected,
                        'metadata_basis': 'reported MCP payload; not independently validated server state'})
        else:
            expected = self.offers[(args['receipt_id'], args['handle'])]
            for key in ['request_id', 'receipt_id']:
                require(key not in value or value[key] == args[key], 'pull_response_id_mismatch')
            selection = value.get('selection')
            require(isinstance(selection, dict), 'selection_not_object')
            record = selection.get('record')
            primary = source(record, expected)
            companions = value.get('competing', [])
            require(isinstance(companions, list) and len(companions) <= 100, 'companions_shape')
            span = value.get('span')
            requested = args.get('span')
            if span is not None:
                require(requested is not None and record.get('body') == '' and primary['class'] != 'C'
                        and not companions and not selection.get('conflicts'), 'partial_delivery_shape')
                returned = span_bytes(span)
                require(returned['offset'] == requested['offset']
                        and returned['end'] == min(requested['offset']+requested['length'], returned['total_bytes']),
                        'span_not_requested')
                require(digest(span.get('source_sha256')) == expected['body_sha256'], 'span_source_not_offered')
                primary.update({'coverage': 'returned_byte_range', 'span': returned,
                                'source_sha256': expected['body_sha256']})
                opening = value.get('source_opening')
                if opening is not None:
                    opening = span_bytes(opening)
                    require(opening['offset'] == 0 and opening['end'] <= returned['offset']
                            and opening['total_bytes'] == returned['total_bytes'], 'opening_overlap_or_source_size')
                    primary['source_opening'] = opening
                if args.get('opening_bytes'):
                    requested_opening = min(args['opening_bytes'], returned['offset'])
                    require((requested_opening == 0 and opening is None)
                            or (opening is not None and opening['end'] == requested_opening),
                            'opening_not_requested')
            else:
                require(requested is None and value.get('source_opening') is None, 'whole_for_partial_request')
                primary = body_source(selection, expected)
            conflicting = selection.get('conflicts', [])
            require(isinstance(conflicting, list) and conflicting == expected['conflicts'], 'conflicts_not_offered')
            expected_members = {}
            for group in conflicting:
                require(isinstance(group, dict) and set(group) == {'conflict_id', 'version', 'members'}, 'conflict_shape')
                ident(group['conflict_id'])
                number(group['version'], 1, 2147483647)
                members = group['members']
                require(isinstance(members, list) and 2 <= len(members) <= 16, 'conflict_members_shape')
                for member in members:
                    require(isinstance(member, dict) and set(member) == {'record_id', 'version'}, 'conflict_reference_shape')
                    rid, version = ident(member['record_id']), number(member['version'], 1, 2147483647)
                    require(rid not in expected_members or expected_members[rid] == version, 'conflict_version_mismatch')
                    expected_members[rid] = version
            if conflicting:
                require(expected_members.get(primary['record_id']) == primary['version'], 'primary_not_in_conflict')
                del expected_members[primary['record_id']]
            linked_companions, seen = [], set()
            for item in companions:
                require(isinstance(item, dict) and isinstance(item.get('record'), dict), 'selection_not_object')
                rid = ident(item['record'].get('record_id'))
                require(rid != primary['record_id'] and rid not in seen, 'duplicate_competing_source')
                seen.add(rid)
                matching = [info for (receipt, _), info in self.offers.items()
                            if receipt == args['receipt_id'] and info['record_id'] == rid]
                require(len(matching) == 1, 'competing_not_offered')
                require(expected_members.get(rid) == matching[0]['version']
                        and item.get('conflicts', []) == conflicting
                        and matching[0]['conflicts'] == conflicting, 'competing_not_in_conflict')
                linked_companions.append(body_source(item, matching[0]))
            require(seen == set(expected_members), 'competing_group_incomplete')
            primary['conflicts'] = copy.deepcopy(conflicting)
            out = {'request_id': args['request_id'], 'receipt_id': args['receipt_id'], 'handle': args['handle'],
                   'offered_arguments': copy.deepcopy(expected['offered_arguments']), 'source': primary,
                   'offered_arguments_basis': 'first delivered offer, not necessarily this request nonce',
                   'matching_offered_requests': copy.deepcopy([item for item in expected['offer_history']
                           if item['arguments']['request_id'] == args['request_id']]),
                   'request_id_matches_offered': any(item['arguments']['request_id'] == args['request_id']
                           for item in expected['offer_history']),
                   'response_ids_present': [key for key in ['request_id', 'receipt_id'] if key in value],
                   'opening_expectation': 'explicit request checked' if args.get('opening_bytes')
                           else 'receipt policy not exposed; only returned range consistency checked',
                   'competing': linked_companions,
                   'bytes_remaining': number(value.get('bytes_remaining'), 0, self.receipt_limits.get(args['receipt_id'], 24000)),
                   'credits_remaining': number(value.get('credits_remaining'), 0, 4),
                   'metadata_basis': 'reported MCP payload; not independently validated server state'}
        # No caller-owned input is kept by reference and nothing is installed or delivered.
        if call['name'] == SEARCH:
            self.receipt_limits[receipt] = ceiling
        for key, info in offers.items():
            if key in self.offers:
                # New immutable observation list; staged maps never mutate older values.
                info['offered_arguments'] = self.offers[key]['offered_arguments']
            self.offers[key] = info
        self.responses[call_id] = out

    def selected(self):
        return copy.deepcopy({'calls': self.calls, 'responses': self.responses,
                              'limits': 'native-visible evidence only; not server authentication, ranking trace, SDK charge or delivery attestation'})
