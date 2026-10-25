"""Strict, body-free parsing of one Codex exec source-fit turn."""

import hashlib
import json

from check_source_fit_panel import source_body
from trial_source_delivery import native_delivery

CODE_MODE_GUARD_ERROR = ('Code Mode is unavailable because code-mode host is disabled. '
                         'Code mode will fail closed; enable `features.code_mode_host` '
                         'and install `codex-code-mode-host`.')

def sha256(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def usage_total(usage):
    """Count input plus output; cached input is already included in input."""
    return usage['input_tokens'] + usage['output_tokens']


def _content_text(result):
    content = result.get('content') if isinstance(result, dict) else None
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
        if content[0].get('type') == 'text' and isinstance(content[0].get('text'), str):
            return content[0]['text']
    if isinstance(content, str):
        return content
    raise RuntimeError('codex_tool_result_shape_unknown')


def _source_delivery(tool, result):
    native_result = dict(content=result.get('content'))
    error_flag = result.get('isError', result.get('is_error'))
    if error_flag is not None:
        native_result['is_error'] = error_flag
    return native_delivery('mcp__cairn__' + tool, native_result)


def _expected_sources(panel, case_id):
    bodies = {name: source_body(record) for name, record in panel['records'].items()}
    hashes = {name: sha256(body.encode()) for name, body in bodies.items()}
    case = next((row for row in panel['cases'] if row['id'] == case_id), None)
    if case is None:
        raise RuntimeError('codex_case_unknown')
    return case, bodies, hashes


def parse_codex_stream(text, panel, case_id, arm, record_names):
    """Validate one Codex user turn and reduce tool results to body-free proof."""
    case, bodies, source_hashes = _expected_sources(panel, case_id)
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError('codex_jsonl_malformed') from error
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise RuntimeError('codex_event_shape_unknown')
        events.append(event)
    if not events:
        raise RuntimeError('codex_stream_empty')

    turn_starts = 0
    terminals = []
    calls = {}
    search_pull_args = {}
    tool_rows = []
    answer = None
    observed_model_ids = set()
    source_mismatch = False
    guard_events = []

    for position, event in enumerate(events):
        kind = event['type']
        if kind == 'thread.started':
            continue
        if kind == 'turn.started':
            turn_starts += 1
            continue
        if kind in ('turn.failed', 'error'):
            raise RuntimeError('codex_turn_failed')
        if kind == 'turn.completed':
            if position != len(events) - 1:
                raise RuntimeError('codex_events_after_terminal')
            terminals.append(event)
            for key in ('resolved_model_id', 'provider_model_id'):
                model_id = event.get(key)
                if isinstance(model_id, str) and model_id:
                    observed_model_ids.add(model_id)
            continue
        if kind in ('item.started', 'item.updated', 'item.completed'):
            item = event.get('item')
            if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                raise RuntimeError('codex_item_shape_unknown')
            item_type = item['type']
            if item_type == 'error':
                if (turn_starts == 0 and item.get('id') == 'item_0'
                        and item.get('message') == CODE_MODE_GUARD_ERROR):
                    guard_events.append('code_mode_host_disabled_before_turn')
                    continue
                raise RuntimeError('codex_unexpected_error_item')
            if item_type == 'agent_message':
                if kind == 'item.completed' and isinstance(item.get('text'), str):
                    answer = item['text']
                continue
            if item_type in ('reasoning', 'plan'):
                continue
            if item_type != 'mcp_tool_call':
                raise RuntimeError('codex_unexpected_tool_call')
            if item.get('server') != 'cairn':
                raise RuntimeError('codex_unexpected_tool_call')
            tool = item.get('tool')
            if tool not in ('cairn_search', 'cairn_pull'):
                raise RuntimeError('codex_unexpected_tool_call')
            if arm == 'direct':
                raise RuntimeError('codex_direct_tool_call')
            ident = item.get('id')
            arguments = item.get('arguments')
            if not isinstance(ident, str) or not ident or not isinstance(arguments, dict):
                raise RuntimeError('codex_tool_identity_unknown')
            if kind == 'item.updated':
                continue
            if kind == 'item.started':
                if ident in calls:
                    raise RuntimeError('codex_tool_identity_duplicate')
                calls[ident] = dict(tool=tool, arguments=arguments, status='started')
                continue
            call = calls.get(ident)
            if call is None or call['status'] != 'started':
                raise RuntimeError('codex_tool_start_missing')
            if call['tool'] != tool or call['arguments'] != arguments:
                raise RuntimeError('codex_tool_identity_changed')
            if item.get('status') != 'completed' or item.get('error'):
                raise RuntimeError('codex_tool_result_error')
            result = item.get('result')
            if (not isinstance(result, dict) or result.get('isError') is True
                    or result.get('is_error') is True):
                raise RuntimeError('codex_tool_result_missing')
            raw_content = result.get('content')
            content_bytes = canonical(raw_content).encode('utf-8')
            try:
                delivery = _source_delivery(tool, result)
            except (TypeError, ValueError, KeyError):
                raise RuntimeError('codex_source_delivery_unknown') from None
            if delivery.get('status') != 'observed':
                raise RuntimeError('codex_source_delivery_unknown')
            if tool == 'cairn_search':
                try:
                    payload = json.loads(_content_text(result))
                except (json.JSONDecodeError, RuntimeError, TypeError):
                    raise RuntimeError('codex_search_payload_unknown') from None
                if payload.get('schema') != 'cairn.mcp-search/1':
                    raise RuntimeError('codex_search_payload_unknown')
                for entry in payload.get('index', []):
                    pull_args = entry.get('pull_arguments') if isinstance(entry, dict) else None
                    if isinstance(pull_args, dict):
                        search_pull_args.setdefault(canonical(pull_args), []).append(ident)
            else:
                matching_searches = search_pull_args.get(canonical(arguments), [])
                if len(matching_searches) != 1:
                    raise RuntimeError('codex_pull_arguments_unmatched')

            for source in delivery.get('items', []):
                record_id = source.get('record_id')
                fixture_id = record_names.get(record_id)
                if fixture_id is None:
                    source_mismatch = True
                    continue
                if source.get('version') != 1:
                    source_mismatch = True
                    continue
                if panel['records'][fixture_id]['eligibility'] == 'local_only_inaccessible_to_hosted':
                    source_mismatch = True
                    continue
                if source.get('source_sha256') != source_hashes[fixture_id]:
                    source_mismatch = True
                    continue
                source['fixture_id'] = fixture_id
                if source.get('extent') == 'full_body':
                    expected_body = bodies[fixture_id].encode()
                    if (source.get('delivered_sha256') != source_hashes[fixture_id]
                            or source.get('delivered_bytes') != len(expected_body)):
                        source_mismatch = True
                if tool == 'cairn_pull' and source.get('extent') == 'partial_span':
                    if source.get('span', {}).get('total_bytes') != len(bodies[fixture_id].encode()):
                        source_mismatch = True
            call['status'] = 'completed'
            row = dict(item_id=ident, tool=tool, status='completed',
                       arguments_sha256=sha256(canonical(arguments).encode()),
                       result_bytes=len(content_bytes), result_sha256=sha256(content_bytes),
                       source_delivery=delivery,
                       pull_arguments_match_search=(tool != 'cairn_pull' or len(matching_searches) == 1),
                       matched_search_item_id=(matching_searches[0] if tool == 'cairn_pull' else None))
            tool_rows.append(row)
            continue
        raise RuntimeError('codex_event_type_unknown')

    if turn_starts != 1 or len(terminals) != 1:
        raise RuntimeError('codex_turn_count_mismatch')
    if any(call['status'] != 'completed' for call in calls.values()):
        raise RuntimeError('codex_tool_result_missing')
    if arm == 'direct' and calls:
        raise RuntimeError('codex_direct_tool_call')
    if answer is None or not answer.strip():
        raise RuntimeError('codex_answer_missing')
    terminal = terminals[0]
    usage = terminal.get('usage')
    required_usage = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                          for key in required_usage):
        raise RuntimeError('codex_usage_missing')
    if usage['cached_input_tokens'] > usage['input_tokens']:
        raise RuntimeError('codex_usage_invalid')
    if source_mismatch:
        raise RuntimeError('codex_source_mismatch')
    required = panel.get('task_checks', {}).get(case_id, {}).get('required')
    required_phrases = case.get('required_answer', [])
    return dict(
        answer=answer,
        turn_count=turn_starts,
        terminal_status='completed',
        usage_semantics='codex_exec_turn_reported',
        usage=dict(input_tokens=usage['input_tokens'],
                   cached_input_tokens=usage['cached_input_tokens'],
                   output_tokens=usage['output_tokens'],
                   cache_write_input_tokens=usage.get('cache_write_input_tokens')),
        reported_tokens=usage_total(usage),
        per_response_usage='unknown_not_exposed_by_exec_jsonl',
        requested_model_id=None,
        resolved_model_id=next(iter(observed_model_ids)) if len(observed_model_ids) == 1 else None,
        resolved_model_observation='native_turn_event' if len(observed_model_ids) == 1 else 'unknown_not_emitted',
        source_validation=('match' if any(
            row['source_delivery'].get('items') for row in tool_rows) else 'no_source_delivery'),
        observed_tools=[row['tool'] for row in tool_rows],
        whole_source_delivered=any(
            source.get('extent') == 'full_body'
            for row in tool_rows for source in row['source_delivery'].get('items', [])),
        tool_results=tool_rows,
        guard_events=guard_events,
        literal_screen={phrase: phrase.casefold() in answer.casefold() for phrase in required_phrases},
        task_review='pending_independent_review',
        task_check_text_hash=sha256((required or '').encode()),
    )
