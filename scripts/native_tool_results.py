"""Interpret native fixture tool results without retaining their payloads.

Wake diagnostics search recursively for evidence; advance selection accepts only
its documented outer output forms and stops at the first decisive result. These
are deliberately distinct policies, including their existing error precedence.
"""

import hashlib
import json
from typing import NamedTuple

_RESPONSE_SCHEMA = "cairn.compact-inbox-batch/1"


class AdvanceResult(NamedTuple):
    stage: str
    classification: str


def tool_result_diagnostics(result: dict) -> dict:
    """Reduce a custom tool result to fixed status, exit, and API-read classes."""
    output = result.get("output", result.get("content"))
    output_type = ("missing" if output is None else "string" if isinstance(output, str) else
                   "array" if isinstance(output, list) else "object" if isinstance(output, dict) else "other")
    status_value = result.get("status")
    status = status_value if status_value in ("completed", "failed", "in_progress") else (
        "failed" if result.get("isError") is True or result.get("is_error") is True else
        "other" if status_value is not None else "missing")
    error_present = (result.get("isError") is True or result.get("is_error") is True or
                     bool(result.get("error")))

    parsed = []
    strings = []

    def visit(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, dict):
            parsed.append(value)
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, list):
            for part in value[:64]:
                visit(part, depth + 1)
        elif isinstance(value, str):
            strings.append(value[:2_000_000])
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                if "\nOutput:\n" in value:
                    tail = value.split("\nOutput:\n", 1)[1].strip()
                    try:
                        tail_decoded = json.loads(tail)
                    except (ValueError, TypeError):
                        tail_decoded = None
                    if isinstance(tail_decoded, dict):
                        visit(tail_decoded, depth + 1)
                        return
                for line in value.splitlines()[:64]:
                    stripped = line.strip()
                    if stripped.startswith("{") and stripped.endswith("}"):
                        try:
                            line_decoded = json.loads(stripped)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(line_decoded, dict):
                            visit(line_decoded, depth + 1)
                return
            if not isinstance(decoded, str):
                visit(decoded, depth + 1)

    visit(result)
    exit_code = next((node.get("exit_code") for node in parsed
                      if type(node.get("exit_code")) is int), None)
    exit_class = "unavailable" if exit_code is None else ("zero" if exit_code == 0 else "nonzero")
    envelopes = [node for node in parsed if node.get("schema") == "cairn.response/1"]
    cairn_envelope = bool(envelopes)
    api_read_status = ("success" if any(node.get("ok") is True and node.get("status") == "OK"
                                         for node in envelopes) else
                       "refused" if envelopes else
                       "not_observed" if exit_class == "nonzero" else "unknown")
    api_read_invocation = ("confirmed" if cairn_envelope else
                           "not_observed" if exit_class == "nonzero" else "unknown")
    content = "\n".join(strings).lower()
    error_classes = (
        ("no_such_file", ("no such file or directory",)),
        ("command_not_found", ("command not found", "not found")),
        ("permission_denied", ("permission denied",)),
        ("operation_not_permitted", ("operation not permitted",)),
        ("connection_refused", ("connection refused", "api_connection_failed")),
    )
    execution_error = next((code for code, patterns in error_classes
                            if any(pattern in content for pattern in patterns)), None)
    if execution_error is None and envelopes and api_read_status == "refused":
        execution_error = "cairn_refusal"
    if execution_error is None and exit_class == "nonzero":
        execution_error = "exit_nonzero"
    if execution_error is None and error_present:
        execution_error = "tool_error"
    if execution_error is None:
        execution_error = "none" if exit_class == "zero" else "unknown"
    execution_observed = (exit_code is not None or status in ("completed", "failed") or error_present)
    return {
        "result_status": status,
        "result_output_type": output_type,
        "result_error_present": error_present,
        "tool_execution_observed": execution_observed,
        "exit_class": exit_class,
        "execution_error_class": execution_error,
        "cairn_api_envelope_observed": cairn_envelope,
        "api_read_status": api_read_status,
        "api_read_invocation": api_read_invocation,
    }


def _extract_exec_exit_code(header):
    for line in header.splitlines():
        if line.startswith("Process exited with code "):
            suffix = line.removeprefix("Process exited with code ").strip()
            if suffix.lstrip("-").isdigit():
                return int(suffix)
    return None


def _parse_advance_tool_result(tool_result):
    """Unwrap either JSON-wrapped or native exec_command-formatted advance output."""
    if not isinstance(tool_result, dict):
        return "outer_json_invalid", None
    if tool_result.get("is_error") is True or tool_result.get("isError") is True:
        return "is_error", None
    raw_output = tool_result.get("output")
    texts = []
    if isinstance(raw_output, str):
        texts.append(raw_output)
    elif isinstance(raw_output, list):
        for item in raw_output:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "output", "content"):
                    if isinstance(item.get(key), str):
                        texts.append(item[key])
    if not texts:
        return "outer_json_invalid", None
    last_stage = "outer_json_invalid"
    for output in texts:
        envelope = None
        try:
            decoded = json.loads(output)
            if isinstance(decoded, dict):
                envelope = decoded
        except (TypeError, ValueError):
            envelope = None
        if envelope is not None:
            exit_code = envelope.get("exit_code")
            if exit_code is None and isinstance(envelope.get("metadata"), dict):
                exit_code = envelope["metadata"].get("exit_code")
            if type(exit_code) is int and exit_code != 0:
                return "exit_nonzero", None
            stdout = envelope.get("output")
            if isinstance(stdout, str):
                if "\nOutput:\n" in stdout:
                    header, stdout = stdout.split("\nOutput:\n", 1)
                    header_exit = _extract_exec_exit_code(header)
                    if type(header_exit) is int and header_exit != 0:
                        return "exit_nonzero", None
                try:
                    response = json.loads(stdout.strip())
                except (TypeError, ValueError):
                    last_stage = "inner_json_invalid"
                    continue
            elif "schema" in envelope or "ok" in envelope:
                response = envelope
            else:
                last_stage = "inner_json_invalid"
                continue
        elif "\nOutput:\n" in output:
            header, stdout = output.split("\nOutput:\n", 1)
            exit_code = _extract_exec_exit_code(header)
            if type(exit_code) is int and exit_code != 0:
                return "exit_nonzero", None
            try:
                response = json.loads(stdout.strip())
            except (TypeError, ValueError):
                last_stage = "inner_json_invalid"
                continue
        else:
            continue
        if (not isinstance(response, dict) or response.get("schema") != _RESPONSE_SCHEMA or
                response.get("ok") is not True):
            return "schema_or_ok_invalid", None
        return "success", response
    return last_stage, None


def interpret_advance_tool_result(tool_result):
    """Decode once, returning only fixed stage and classification codes."""
    stage, response = _parse_advance_tool_result(tool_result)
    return AdvanceResult(stage, _classify_advance_response(stage, response))


def advance_tool_result_stage(tool_result):
    """Return the existing fixed parse-stage code for callers needing only it."""
    return interpret_advance_tool_result(tool_result).stage


def classify_advance_tool_result(tool_result):
    """Return the existing fixed advance class for callers needing only it."""
    return interpret_advance_tool_result(tool_result).classification


def _classify_advance_response(stage, response):
    if stage in ("is_error", "exit_nonzero", "schema_or_ok_invalid"):
        return "tool_error"
    if stage != "success" or not isinstance(response, dict):
        return "other"
    next_value = response.get("next")
    if (isinstance(next_value, str) and
            _bounded_integer(response.get("batch_index"), 2, 3) is not None and
            _bounded_integer(response.get("batch_limit"), 1, 3) == 3):
        return "advance_successor"
    if next_value is None and response.get("remainder") == "batch_limit":
        return "advance_limit"
    if next_value is None and response.get("remainder") == "priority_waiting":
        return "advance_priority"
    return "other"


def _bounded_integer(value, minimum, maximum):
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


class _InvalidResult(ValueError):
    pass


def _strict_json(text):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise _InvalidResult('duplicate result field')
            value[key] = item
        return value

    def invalid_constant(_value):
        raise _InvalidResult('invalid JSON constant')

    return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def _successful_response(tool_result):
    """Select one application response through explicit transport wrappers only.

    Application data is terminal: source bodies and other payload fields are
    never searched for another response. All selected wrappers must succeed.
    """
    responses = []

    def check_execution(value):
        if (any(key in value and value[key] is not False for key in ('isError', 'is_error'))
                or value.get('error') is not None or ('ok' in value and value['ok'] is not True)):
            raise _InvalidResult('tool error')
        if value.get('session_id') is not None:
            raise _InvalidResult('tool execution incomplete')
        if 'exit_code' in value and (type(value['exit_code']) is not int or value['exit_code'] != 0):
            raise _InvalidResult('execution did not succeed')

    def visit(value, depth=0, execution_complete=False):
        if depth > 8:
            raise _InvalidResult('result nesting limit')
        if isinstance(value, str):
            if len(value) > 2_000_000:
                raise _InvalidResult('result text limit')
            try:
                decoded = _strict_json(value)
            except _InvalidResult:
                raise
            except ValueError:
                if '\nOutput:\n' not in value:
                    raise _InvalidResult('unrecognized result text') from None
                header, body = value.split('\nOutput:\n', 1)
                header_lines = [line.strip() for line in header.splitlines()]
                if not any(line.startswith(('Chunk ID:', 'Wall time:', 'Process exited with code ',
                                            'Script completed')) for line in header_lines):
                    raise _InvalidResult('unrecognized execution header')
                completed_header = False
                for line in header_lines:
                    if line.startswith(('Script ', 'Process ')) and not (
                            line == 'Script completed' or line.startswith('Process exited with code ')):
                        raise _InvalidResult('tool execution did not complete successfully')
                    if line == 'Script completed':
                        completed_header = True
                    if line.startswith('Process exited with code '):
                        if line.removeprefix('Process exited with code ').strip() != '0':
                            raise _InvalidResult('execution did not succeed')
                        completed_header = True
                if not (completed_header or execution_complete):
                    raise _InvalidResult('execution completion missing')
                execution_complete = True
                # Native functions.exec can emit its completed preamble as a
                # separate text block, followed by the actual execution output.
                if not body.strip():
                    return
                decoded = _strict_json(body.strip())
            visit(decoded, depth + 1, execution_complete)
            return
        if isinstance(value, list):
            if not value or len(value) > 64:
                raise _InvalidResult('result block limit')
            for item in value:
                visit(item, depth + 1, execution_complete)
            return
        if not isinstance(value, dict):
            raise _InvalidResult('unrecognized result value')
        check_execution(value)
        metadata = value.get('metadata')
        if metadata is not None:
            if not isinstance(metadata, dict) or metadata.get('status') not in (None, 'completed'):
                raise _InvalidResult('invalid execution metadata')
            check_execution(metadata)
            execution_complete |= metadata.get('exit_code') == 0 or metadata.get('status') == 'completed'
        execution_complete |= value.get('exit_code') == 0 or value.get('status') == 'completed'
        if 'schema' in value:
            if any(key in value for key in ('output', 'content', 'text')):
                raise _InvalidResult('application and transport fields conflict')
            if (value.get('schema') not in ('cairn.response/1', 'cairn.compact-inbox/1')
                    or value.get('ok') is not True
                    or (value['schema'] == 'cairn.response/1' and value.get('status') != 'OK')):
                raise _InvalidResult('application response refused')
            responses.append(value)
            return
        if value.get('status') not in (None, 'completed') or value.get('session_id') is not None:
            raise _InvalidResult('tool execution incomplete')
        fields = [key for key in ('output', 'content', 'text') if key in value]
        if len(fields) != 1:
            raise _InvalidResult('ambiguous result wrapper')
        visit(value[fields[0]], depth + 1, execution_complete)

    try:
        visit(tool_result)
    except (ValueError, TypeError, RecursionError):
        return None
    return responses[0] if len(responses) == 1 else None


def source_result_matches(tool_result, source, body):
    """Match fields from one successful whole-version history or compact read."""
    matches = dict(source_record_match=False, source_version_match=False, source_body_match=False)
    response = _successful_response(tool_result)
    if response is None:
        return matches
    if response['schema'] == 'cairn.response/1':
        data = response.get('data')
        if not isinstance(data, dict) or data.get('historical') is not True:
            return matches
        versions = data.get('versions')
        if not isinstance(versions, list) or len(versions) != 1 or not isinstance(versions[0], dict):
            return matches
        version = versions[0]
        if version.get('payload_available') is not True or version.get('span') is not None:
            return matches
        record_id, number, observed_body = data.get('record_id'), version.get('version'), version.get('body')
        if not isinstance(observed_body, str):
            return matches
        try:
            encoded_body = observed_body.encode()
        except UnicodeError:
            return matches
        if (type(version.get('body_bytes')) is not int or version['body_bytes'] != len(encoded_body)
                or version.get('body_sha256') != hashlib.sha256(encoded_body).hexdigest()):
            return matches
    else:
        ref = response.get('source')
        if not isinstance(ref, dict) or response.get('status') is not None:
            return matches
        record_id, number, observed_body = ref.get('record_id'), ref.get('version'), response.get('body')
    return dict(source_record_match=isinstance(record_id, str) and record_id == source.get('record_id'),
                source_version_match=type(number) is int and number == source.get('version'),
                source_body_match=isinstance(observed_body, str) and observed_body == body)


def source_read_succeeded(tool_result, source, body):
    return all(source_result_matches(tool_result, source, body).values())


def settlement_succeeded(tool_result, *, delivery_id=None, event_id=None, handle=None):
    """Confirm exactly one handled attempt for the caller's expected identity."""
    if (delivery_id is None) == (handle is None):
        raise ValueError('one expected settlement identity is required')
    response = _successful_response(tool_result)
    if response is None:
        return False
    if delivery_id is not None:
        if response['schema'] != 'cairn.response/1':
            return False
        data = response.get('data')
        return (isinstance(data, dict) and isinstance(delivery_id, str) and bool(delivery_id)
                and data.get('delivery_id') == delivery_id and isinstance(data.get('event'), dict)
                and isinstance(event_id, str) and bool(event_id) and data['event'].get('event_id') == event_id
                and data.get('state') == 'handled' and type(data.get('attempts')) is int and data['attempts'] == 1)
    return (response['schema'] == 'cairn.compact-inbox/1' and isinstance(handle, str) and bool(handle)
            and event_id is None and response.get('handle') == handle
            and response.get('status') == 'handled' and type(response.get('attempts')) is int
            and response['attempts'] == 1)
