"""Secure private trace creation and fixed-code JSONL appends for CAIRN-220."""

import json
import os
import re
import stat


ERROR_CLASSES = {
    "QueueError", "QueueUnavailable", "QueueRefused", "OSError", "ValueError",
    "KeyError", "TypeError", "WebSocketException", "ImportError", "FileNotFoundError",
    "CalledProcessError", "TimeoutExpired",
}
STAGES = {"trace", "module_import", "_open", "_admit", "queue_add", "queue_start",
          "observer_subscription", "observer_loaded_list", "observer_resume",
          "observer_connection", "observer_initialize", "observer_initialized",
          "watcher_start", "native_item_started", "bootstrap"}
OUTCOMES = {"started", "failed", "before", "after", "refused", "observed"}
BOOLEAN_FIELDS = {"receipt_confirmed", "delivery_id_matches", "receipt_matches_start", "turn_started",
                  "exit_zero", "item_thread_matches", "item_is_user_message"}
PG_FAILURE_CLASSES = {"address_in_use", "socket_path_too_long", "permission_denied", "port_conflict",
                      "invalid_configuration", "storage_or_filesystem",
                      "data_directory_invalid", "diagnostic_log_unavailable", "unclassified"}
API_FAILURE_CLASSES = {"invalid_json", "cli_exit", "envelope_not_object", "response_refused",
                       "response_contract_mismatch", "server_process_exit", "socket_timeout"}
API_START_FAILURE_CLASSES = {"identity_config_invalid", "database_unavailable", "socket_setup_refused",
                             "api_bind_failed", "api_socket_path_too_long", "runtime_configuration_invalid", "store_open_failed",
                             "unknown_json_refusal",
                             "unclassified_output", "output_truncated", "process_exit_no_output"}
NATIVE_REFUSAL_CLASSES = {
    "busy", "submission_not_found", "invalid_request_other", "native_refusal_other",
    "thread_closing", "thread_not_found", "history_already_running", "stale_rollout_path",
    "unloaded_v2_child", "resume_listener_not_running", "resume_listener_channel_closed",
    "thread_resume_other",
}
DIGEST_FIELDS = {"turn_id_sha256", "delivery_id_sha256", "receipt_id_sha256", "native_item_id_sha256"}
BOOTSTRAP_STEPS = {"runner", "codex_version", "pg_config", "go_build", "pg_initdb",
                   "pg_start", "db_create", "migrate", "api_start", "api_ready",
                   "mounted_preflight", "watcher_bwrap", "pg_cleanup", "api_preflight"}


def create_trace(path):
    """Create an exclusive 0600 trace beneath an owner-only, non-symlink dir."""
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        raise ValueError("unsafe trace path")
    parts = path.split("/")[1:]
    if len(parts) < 2 or any(not part or part in (".", "..") for part in parts):
        raise ValueError("unsafe trace path")
    directory_parts, filename = parts[:-1], parts[-1]

    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in directory_parts:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        directory_stat = os.fstat(directory_fd)
        if (directory_stat.st_uid != os.geteuid()
                or stat.S_IMODE(directory_stat.st_mode) != 0o700):
            raise ValueError("trace directory must be owner-only")
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        file_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.geteuid()
                or stat.S_IMODE(file_stat.st_mode) != 0o600 or file_stat.st_nlink != 1):
            os.close(descriptor)
            raise ValueError("trace file must be exclusive owner-only regular file")
        return descriptor
    finally:
        os.close(directory_fd)


def append_trace(descriptor, stage, outcome, error=None, code=None, flags=None, digests=None,
                 step=None):
    if stage not in STAGES or outcome not in OUTCOMES:
        raise ValueError("invalid trace event")
    row = {"schema": "cairn.codex-queue-stage/1", "stage": stage, "outcome": outcome}
    if error:
        row["error_class"] = error if error in ERROR_CLASSES else "OtherError"
    if type(code) is int and -32768 <= code <= 32767:
        row["native_code"] = code
    for key, value in (flags or {}).items():
        if key in BOOLEAN_FIELDS and type(value) is bool:
            row[key] = value
        elif key == "pg_failure_class" and value in PG_FAILURE_CLASSES:
            row[key] = value
        elif key == "api_failure_class" and value in API_FAILURE_CLASSES:
            row[key] = value
        elif key == "api_start_failure_class" and value in API_START_FAILURE_CLASSES:
            row[key] = value
        elif key == "native_refusal_class" and value in NATIVE_REFUSAL_CLASSES:
            row[key] = value
    for key, value in (digests or {}).items():
        if key in DIGEST_FIELDS and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            row[key] = value
    if stage == "bootstrap" and step in BOOTSTRAP_STEPS:
        row["step"] = step
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    view = memoryview(payload)
    while view:
        view = view[os.write(descriptor, view):]
    os.fsync(descriptor)
