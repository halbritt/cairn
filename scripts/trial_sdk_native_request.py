"""Native search/pull JSON mapped to fixed CLI scope without losing arguments.

The service applies this policy again before its original command owner starts.
No API client, MCP process, dispatch or receipt authority is created here.
"""
import json
import re

from trial_sdk_command_policy import (FixedCommandPolicy, CommandPlan,
    CommandPolicyError, _CONTEXT, _KINDS, _string, _integer, _id)
from trial_sdk_memory_gate import HookRequest
from trial_sdk_incoming import canonical_size_sha256, parse_document, MAX_DOCUMENT_BYTES

SEARCH_FIELDS = frozenset(('query', 'available_tokens', 'semantic', 'offset', 'entities',
    'kinds', 'request_id', 'browse', 'memory_budget_bytes', 'inspection_policy',
    'min_pull_bytes', 'advisory_conflicts', 'context', 'error_signature_sha256', 'semantic_query'))
PULL_FIELDS = frozenset(('request_id', 'receipt_id', 'handle', 'span', 'opening_bytes'))
TOOLS = {'mcp__cairn__cairn_search': 'native-search', 'mcp__cairn__cairn_pull': 'native-pull'}


def require(value, code='native_request_shape'):
    if not value:
        raise CommandPolicyError(code)


def command_request(tool, arguments, policy):
    require(type(tool) is str and tool in TOOLS, 'native_request_tool')
    require(type(arguments) is dict, 'native_request_shape')
    size, _ = canonical_size_sha256(arguments)
    require(size <= MAX_DOCUMENT_BYTES, 'native_request_bound')
    body = json.dumps(arguments, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    operation = TOOLS[tool]
    native_plan(policy, operation, body)
    return HookRequest(operation, (), body)


def native_plan(policy, operation, body):
    require(type(policy) is FixedCommandPolicy and type(body) is str, 'native_request_binding')
    args = parse_document(body, _string(body))
    require(type(args) is dict, 'native_request_shape')
    prefix = (policy.executable, 'agent', '--socket', policy.socket, '--token-file', policy.token_file)
    if operation == 'native-pull':
        require(set(args) <= PULL_FIELDS, 'native_request_field')
        normalized = policy._json('pull', args)
        return CommandPlan(operation, prefix + ('pull',),
            json.dumps(normalized, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
    require(operation == 'native-search' and not set(args) - SEARCH_FIELDS, 'native_request_field')
    query, semantic_query = args.get('query', ''), args.get('semantic_query', '')
    require(_string(query) + _string(semantic_query) <= 4096, 'native_request_bound')
    tokens = args.get('available_tokens')
    tokens = 32000 if tokens is None else _integer(tokens, 256, 32000)
    argv = list(prefix) + ['search', '--repo', policy.repo, '--task', policy.task,
        '--run', policy.run, '--tokens', str(tokens)]
    for key in ('semantic', 'browse', 'advisory_conflicts'):
        if key in args:
            require(type(args[key]) is bool)
        if args.get(key):
            argv.append('--' + key.replace('_', '-'))
    semantic, browse = args.get('semantic', False), args.get('browse', False)
    require(not (semantic and browse))
    signature = args.get('error_signature_sha256', '')
    require(type(signature) is str and (not signature or re.fullmatch('[0-9a-fA-F]{64}', signature)))
    if signature:
        argv.extend(('--error-signature-sha256', signature))
    entities, kinds = args.get('entities'), args.get('kinds')
    entities = [] if entities is None else entities
    kinds = [] if kinds is None else kinds
    require(type(entities) is list and len(entities) <= 16 and type(kinds) is list and len(kinds) <= 8)
    for entity in entities:
        require(type(entity) is dict and set(entity) == {'kind', 'name'}
                and entity['kind'] in ('file', 'symbol') and type(entity['name']) is str)
        # The CLI's entity parser performs the same canonical path/symbol checks
        # as the API. No shell expansion or concatenated flag value occurs.
        require(0 < _string(entity['name']) <= 4096)
        argv.extend(('--entity-' + entity['kind'], entity['name']))
    for kind in kinds:
        require(type(kind) is str and kind in _KINDS)
        argv.extend(('--kind', kind))
    require((browse and not query and not entities and not signature)
            or (not browse and (query.strip() or entities or signature)))
    require(not semantic or bool(query.strip()))
    if semantic_query:
        require(semantic and not browse and bool(query.strip()))
        argv.append('--semantic-query-stdin')
    memory = args.get('memory_budget_bytes')
    if memory is not None:
        memory = _integer(memory, 256, tokens)
        argv.extend(('--memory-budget-bytes', str(memory)))
    minimum = args.get('min_pull_bytes')
    if minimum is not None:
        require(memory is not None)
        minimum = _integer(minimum, 1, min(24000, memory))
        argv.extend(('--min-pull-bytes', str(minimum)))
    inspection = args.get('inspection_policy', '')
    require(type(inspection) is str)
    if inspection:
        require(inspection in ('first-fitting-whole/1', 'first-fitting-whole/2')
                and memory is not None and minimum is None)
        argv.extend(('--inspection-policy', inspection))
    if args.get('offset') is not None:
        argv.extend(('--offset', str(_integer(args['offset'], 0, 10000))))
    if 'request_id' in args:
        require(type(args['request_id']) is str)
        if args['request_id']:
            argv.extend(('--request-id', _id(args['request_id'])))
    context = args.get('context')
    context = {} if context is None else context
    aliases = {'binding_id': 'binding', 'capability_id': 'capability'}
    require(type(context) is dict and set(context) <=
            {'revision', 'workspace_sha256', 'task_class', 'task_phase', 'binding_id', 'capability_id'})
    fixed = dict(policy.context)
    for key, value in context.items():
        key = aliases.get(key, key)
        require(type(value) is str)
        require(not fixed.get(key) or not value or fixed[key] == value, 'command_policy_scope')
        if value:
            fixed[key] = value
    # Apply existing fixed-context grammar after merging; no invented authority.
    selected = FixedCommandPolicy(policy.executable, policy.socket, policy.token_file,
        policy.repo, policy.task, policy.run, tuple(fixed.items()))
    for key, flag in _CONTEXT:
        value = dict(selected.context).get(key)
        if value:
            argv.extend((flag, value))
    argv.extend(('--', query))
    return CommandPlan(operation, tuple(argv), semantic_query or None)
