"""Private, sanitized persistence for native notice-batch advance responses."""

import json
import os
from pathlib import Path
import stat

from native_tool_results import (
    advance_tool_result_stage,
    classify_advance_tool_result,
)

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
_ADVANCE_RESULT_STAGES = {
    "is_error", "outer_json_invalid", "exit_nonzero", "inner_json_invalid",
    "schema_or_ok_invalid", "success",
}
_HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "Notification"}
_HOOK_ERROR_CODES = {
    "API_UNAVAILABLE", "HANDLE_EXPIRED", "HOST_TARGET_GONE", "IDEMPOTENCY_CONFLICT",
    "INBOX_ATTEMPT_MISMATCH", "INTENT_PENDING", "INVALID_CONFIG", "INVALID_HOST",
    "INVALID_REQUEST", "NATIVE_ACTIVE", "NATIVE_PROMPT_REFUSED", "NATIVE_TURN_MISMATCH",
    "NOT_FOUND", "NOT_THIS_SESSION", "REQUEST_CANCELLED", "SESSION_BUSY",
    "SOURCE_UNAVAILABLE", "STALE_LEASE", "STALE_SESSION", "UNSUPPORTED_CONTROL",
    "WAKE_UNAVAILABLE", "WAKE_UNCERTAIN",
}
_HOOK_ERROR_CLASSES = {
    "CoordinationError", "ConnectionError", "FileNotFoundError", "KeyError", "OSError",
    "PermissionError", "RuntimeError", "SubprocessError", "TimeoutError", "TimeoutExpired",
    "TypeError", "ValueError",
}
_HOOK_PROBE_STAGES = {
    "wrapper_entry", "payload_invalid", "payload_valid",
    "command_start", "command_complete", "command_timeout", "command_error",
}
_PREFLIGHT_FIELDS = {
    "token_identity": {"matched", "mismatched", "unavailable"},
    "api_socket": {"available", "unavailable"},
    "api_version": {"authenticated", "unavailable"},
}
_FIXTURE_FAILURE_CLASSES = {
    "AssertionError", "FileNotFoundError", "KeyError", "OSError", "RuntimeError",
    "StopIteration", "TimeoutError", "TypeError", "ValueError",
}
_SETUP_STAGES = {
    "check_entry", "validate_codex_paths", "validate_candidate_paths",
    "verify_test_database", "verify_token_identity", "load_actor_task",
    "verify_api", "create_trace_directory",
    "create_actor_workspace", "copy_coordination", "configure_profile",
    "install_hooks", "build_sandbox_launcher", "configure_responses",
    "start_actor_server", "register_hooks", "registration_turn",
    "registration_hook_wait", "publish_notices", "actor_turn_start",
}
_SETUP_FAILURE_CLASSES = _FIXTURE_FAILURE_CLASSES | {"ImportError", "ModuleNotFoundError"}
_FILENAME_ROLES = {
    "selected_codex", "codex_entry", "codex_package", "candidate_root",
    "cairn_binary", "api_socket", "token_file", "identity_file",
    "fixture_source", "coordination_source", "hook_metadata_wrapper",
    "actor_workspace", "profile_settings", "launcher", "shared_parent",
    "ambiguous", "unknown",
}


def classify_setup_filename(filename, role_paths):
    """Match an exception filename to caller-supplied roles without returning it."""
    if not isinstance(filename, (str, bytes, os.PathLike)) or not isinstance(role_paths, dict):
        return "unknown"
    try:
        observed = os.path.normcase(os.path.abspath(os.fsdecode(filename)))
    except (OSError, TypeError, ValueError):
        return "unknown"
    exact, descendants = [], []
    for role, candidate in role_paths.items():
        if not isinstance(role, str) or role not in _FILENAME_ROLES - {"unknown"}:
            continue
        if not isinstance(candidate, (str, bytes, os.PathLike)):
            continue
        try:
            expected = os.path.normcase(os.path.abspath(os.fsdecode(candidate)))
        except (OSError, TypeError, ValueError):
            continue
        if observed == expected:
            exact.append(role)
        elif Path(expected).is_relative_to(Path(observed)):
            descendants.append(role)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return "ambiguous"
    if len(descendants) == 1:
        return descendants[0]
    if len(descendants) > 1:
        return "shared_parent"
    return "unknown"



def _bounded_code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "other"


def _bounded_integer(value, minimum, maximum):
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


def fixture_api_argv(binary, socket, token_file, command, *args, raw=False):
    """Build a fixture CLI command using the integration harness argument order."""
    connection = ["--socket", str(socket), "--token-file", str(token_file)]
    if raw:
        return [str(binary), "agent", *connection, command, *args]
    if command == "agents":
        if not args:
            raise ValueError("agents fixture command requires its subcommand")
        return [str(binary), command, args[0], *connection, *args[1:]]
    return [str(binary), command, *connection, *args]


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
        self.setup_stage = "check_entry"
        self.setup_finished = False
        self.setup_failure_recorded = False

    def record_setup_stage(self, stage, phase):
        """Persist one allowlisted pre-actor setup boundary."""
        safe_stage = stage if isinstance(stage, str) and stage in _SETUP_STAGES else "check_entry"
        safe_phase = phase if phase in {"start", "complete"} else "start"
        self.setup_stage = safe_stage
        self._write({"operation": "setup_stage", "stage": safe_stage, "phase": safe_phase})

    def record_setup_failure(self, exception_class, filename_role="unknown"):
        """Persist a fixed setup failure class and sanitized filename role."""
        safe_class = (exception_class if isinstance(exception_class, str)
                      and exception_class in _SETUP_FAILURE_CLASSES else "other")
        safe_role = (filename_role if isinstance(filename_role, str)
                     and filename_role in _FILENAME_ROLES else "unknown")
        self._write({"operation": "setup_failure", "stage": self.setup_stage,
                     "exception_class": safe_class, "filename_role": safe_role})
        self.setup_failure_recorded = True

    def record_setup_complete(self):
        self.setup_finished = True
        self._write({"operation": "setup_complete"})

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

    def record_advance_result_stage(self, result_stage):
        """Persist the fixed parser boundary before the caller asserts on it."""
        stage = (result_stage if isinstance(result_stage, str)
                 and result_stage in _ADVANCE_RESULT_STAGES else "other")
        self.stream.write(json.dumps({"operation": "advance_result_stage",
                                      "stage_code": stage}, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def record_hook_metadata(self, rows):
        """Persist sanitized hook entry/exit classifications, never hook data or IDs."""
        if not isinstance(rows, (list, tuple)):
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event = row.get("event")
            event_class = event if isinstance(event, str) and event in _HOOK_EVENTS else "other"
            exiting = type(row.get("exit_code")) is int
            code = row.get("exit_code")
            exit_class = "not_applicable" if not exiting else "zero" if code == 0 else "nonzero"
            stderr_code = row.get("stderr_code")
            stderr_class = row.get("stderr_class")
            safe_code = stderr_code if isinstance(stderr_code, str) and stderr_code in _HOOK_ERROR_CODES else "none"
            safe_class = stderr_class if isinstance(stderr_class, str) and stderr_class in _HOOK_ERROR_CLASSES else "none"
            probe_stage = row.get("probe_stage")
            if probe_stage in _HOOK_PROBE_STAGES:
                error_class = row.get("error_class")
                safe_error_class = (error_class if isinstance(error_class, str)
                                    and error_class in _HOOK_ERROR_CLASSES else "none")
                probe_exit = row.get("exit_class")
                if probe_exit not in {"not_applicable", "zero", "nonzero"}:
                    probe_exit = "not_applicable"
                self._write({"operation": "hook_probe", "event_class": event_class,
                             "stage": probe_stage, "error_class": safe_error_class,
                             "exit_class": probe_exit})
                continue
            self._write({"operation": "hook", "event_class": event_class,
                         "phase": "exit" if exiting else "entry",
                         "exit_class": exit_class, "stderr_code": safe_code,
                         "stderr_class": safe_class})

    def record_store_slot(self, slot, *, status_class, observed, pending=None, leased=None,
                          handled=None, attempts_zero=None, attempted=None):
        """Persist one ordered notice slot as booleans, without Store identifiers."""
        slot = _bounded_integer(slot, 1, 4)
        if slot is None:
            slot = 0
        status_class = (status_class if isinstance(status_class, str) and
                        status_class in {"available", "not_created", "unavailable", "unexpected"}
                        else "unexpected")
        values = (observed, pending, leased, handled, attempts_zero, attempted)
        values = [value if type(value) is bool else None for value in values]
        self._write({"operation": "store_slot", "slot": slot, "status_class": status_class,
                     "observed": values[0], "pending": values[1],
                     "leased": values[2], "handled": values[3],
                     "attempts_zero": values[4], "attempted": values[5]})

    def record_actor_terminal(self, terminal_class):
        allowed = {"completed", "failed", "cancelled", "not_observed", "not_started", "other"}
        value = terminal_class if isinstance(terminal_class, str) and terminal_class in allowed else "other"
        self._write({"operation": "actor_terminal", "terminal_class": value})

    def record_fixture_failure(self, exception_class):
        if exception_class is None:
            value = "none"
        elif isinstance(exception_class, str) and exception_class in _FIXTURE_FAILURE_CLASSES:
            value = exception_class
        else:
            value = "other"
        self._write({"operation": "fixture_failure", "exception_class": value})

    def record_preflight(self, check, result):
        allowed = _PREFLIGHT_FIELDS.get(check)
        if allowed is None:
            return
        value = result if isinstance(result, str) and result in allowed else "unavailable"
        self._write({"operation": "preflight", "check": check, "result": value})

    def _write(self, row):
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()
