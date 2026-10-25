"""Private, sanitized persistence for native notice-batch advance responses."""

import json
import os
from pathlib import Path
import stat

_RESPONSE_SCHEMA = "cairn.compact-inbox-batch/1"
_SAFE_KEYS = {
    "schema", "ok", "next", "remainder", "batch_index", "batch_limit", "status",
}
_SAFE_STATUS = {
    "API_CONNECTION_FAILED", "API_UNAVAILABLE", "CONFIRMATION_MISMATCH",
    "HANDLE_EXPIRED", "INVALID_HOST", "INVALID_REQUEST", "NOT_FOUND",
    "NOT_THIS_SESSION", "PAYLOAD_UNAVAILABLE", "SCHEMA_MISMATCH",
    "SESSION_BUSY", "SOURCE_UNAVAILABLE", "STALE_LEASE", "STALE_SESSION",
    "UNSUPPORTED_CONTROL",
}
_SAFE_REMAINDERS = {"batch_limit", "priority_waiting"}
_ACTOR_RESPONSE_CLASSES = {
    "registration_message", "read_notice", "settle_handled", "settle_failed",
    "settle_ignored", "advance_successor", "advance_limit", "advance_priority",
    "final_message", "unexpected",
}
_ACTOR_TOOL_RESULT_CLASSES = {
    "none", "read_success", "read_unavailable", "settle_success", "settle_failure",
    "advance_successor", "advance_limit", "advance_priority", "tool_error", "other",
}
_ACTOR_TRACE_STAGES = {
    "registration", "context_lookup", "session_claim", "read_tool_call",
    "read_tool_result", "settle_tool_call", "settle_tool_result",
    "advance_tool_call", "advance_tool_result", "turn_terminal",
    "handler_error", "unknown",
}


def classify_advance_tool_result(tool_result):
    """Classify a nested Responses tool result without retaining its payload."""
    if not isinstance(tool_result, dict) or tool_result.get("is_error") is True:
        return "tool_error"
    output = tool_result.get("output")
    if isinstance(output, list):
        output = "".join(item.get("text", "") for item in output
                         if isinstance(item, dict) and isinstance(item.get("text"), str))
    if not isinstance(output, str):
        return "other"
    try:
        envelope = json.loads(output)
    except (TypeError, ValueError):
        return "other"
    exit_code = envelope.get("exit_code") if isinstance(envelope, dict) else None
    if type(exit_code) is int and exit_code != 0:
        return "tool_error"
    stdout = envelope.get("output") if isinstance(envelope, dict) else None
    if not isinstance(stdout, str):
        stdout = output
    try:
        response = json.loads(stdout)
    except (TypeError, ValueError):
        return "other"
    if (not isinstance(response, dict) or response.get("schema") != _RESPONSE_SCHEMA or
            response.get("ok") is not True):
        return "tool_error"
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


def _bounded_code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "other"


def _bounded_integer(value, minimum, maximum):
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


class AdvanceDiagnosticWriter:
    def __init__(self, path, disposable_root):
        self.path = Path(path)
        root = Path(disposable_root).resolve(strict=True)
        if not self.path.is_absolute():
            raise ValueError("diagnostic output must be an absolute path")
        parent = self.path.parent
        parent_info = parent.lstat()
        if (stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) or
                parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077):
            raise ValueError("diagnostic output parent must be an owner-only directory")
        resolved = parent.resolve(strict=True) / self.path.name
        try:
            resolved.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("diagnostic output must be outside the disposable root")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        os.fchmod(fd, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")

    def record_advance(self, response, returncode):
        response = response if isinstance(response, dict) else {}
        has_next = "next" in response
        next_value = response.get("next")
        row = {
            "operation": "advance",
            "keys": sorted(_SAFE_KEYS.intersection(response)),
            "schema": response.get("schema") if response.get("schema") == _RESPONSE_SCHEMA else "other",
            "ok": response.get("ok") if type(response.get("ok")) is bool else None,
            "status": _bounded_code(response.get("status"), _SAFE_STATUS),
            "next_type": ("null" if next_value is None else
                          "string" if isinstance(next_value, str) else
                          type(next_value).__name__) if has_next else "absent",
            "remainder": _bounded_code(response.get("remainder"), _SAFE_REMAINDERS),
            "batch_index": _bounded_integer(response.get("batch_index"), 1, 3),
            "batch_limit": _bounded_integer(response.get("batch_limit"), 1, 3),
            "returncode": _bounded_integer(returncode, -128, 255),
        }
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def record_actor_exchange(self, response_class, tool_result_classes, stage="unknown"):
        """Persist bounded actor protocol classes without retaining payloads."""
        response_class = (response_class if isinstance(response_class, str)
                          and response_class in _ACTOR_RESPONSE_CLASSES else "unexpected")
        if not isinstance(tool_result_classes, (list, tuple)):
            tool_result_classes = ["other"]
        classes = sorted({value if isinstance(value, str) and value in _ACTOR_TOOL_RESULT_CLASSES
                          else "other" for value in tool_result_classes})
        stage = stage if isinstance(stage, str) and stage in _ACTOR_TRACE_STAGES else "unknown"
        row = {"operation": "actor_exchange", "response_class": response_class,
               "tool_result_classes": classes, "stage": stage}
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()
