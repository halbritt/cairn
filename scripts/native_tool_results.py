"""Interpret native fixture tool results without retaining their payloads.

Wake diagnostics search recursively for evidence; advance selection accepts only
its documented outer output forms and stops at the first decisive result. These
are deliberately distinct policies, including their existing error precedence.
"""

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
