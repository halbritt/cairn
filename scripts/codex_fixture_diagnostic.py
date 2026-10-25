"""Write a tiny owner-only summary for the disposable Codex fixture."""
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path


SCHEMA = "cairn.codex-fixture-boundary/1"
OWNER_CLASSES = {"not_observed", "configured_name_match", "none", "not_applicable"}
HOOK_FIELDS = {"entry_seen", "exit_seen", "observation_count", "owner_process_class", "exit_code",
               "stderr_code", "stderr_class", "output_status", "output_code"}
CONTEXT_FIELDS = {"stage", "context_files", "matching_contexts", "result"}
STAGES = {"not_reached", "batch_context", "auto_context", "notice_context"}
RESULTS = {"not_observed", "matched", "missing", "ambiguous"}


def _initial():
    return dict(schema=SCHEMA,
        user_prompt_submit=dict(entry_seen=False, exit_seen=False,
            observation_count=0, owner_process_class="not_observed", exit_code=None, stderr_code=None,
            stderr_class=None, output_status=None, output_code=None),
        context_lookup=dict(stage="not_reached", context_files=None,
            matching_contexts=None, result="not_observed"))


def _validate_values(section, values):
    allowed = HOOK_FIELDS if section == "user_prompt_submit" else CONTEXT_FIELDS
    if not isinstance(values, dict) or not set(values) <= allowed:
        raise ValueError("invalid fixture diagnostic fields")
    if section == "user_prompt_submit":
        if values.get("owner_process_class") not in (None, *OWNER_CLASSES):
            raise ValueError("invalid owner process classification")
        for key in ("entry_seen", "exit_seen"):
            if key in values and type(values[key]) is not bool:
                raise ValueError("invalid hook observation flag")
        if "observation_count" in values and (type(values["observation_count"]) is not int or
                not 0 <= values["observation_count"] <= 1000):
            raise ValueError("invalid hook observation count")
        if "exit_code" in values and values["exit_code"] is not None and (
                type(values["exit_code"]) is not int or not 0 <= values["exit_code"] <= 255):
            raise ValueError("invalid hook exit code")
        for key in ("stderr_code", "output_code"):
            if key in values and values[key] is not None and not re.fullmatch(
                    r"[A-Z][A-Z0-9_]{0,63}", values[key]):
                raise ValueError("invalid hook code")
        for key in ("stderr_class", "output_status"):
            if key in values and values[key] is not None and not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{0,63}", values[key]):
                raise ValueError("invalid hook status")
    else:
        if values.get("stage") not in STAGES or values.get("result") not in RESULTS:
            raise ValueError("invalid context diagnostic classification")
        for key in ("context_files", "matching_contexts"):
            value = values.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= 1000):
                raise ValueError("invalid context diagnostic count")


def update(path, section, values, *, increment=()):
    """Merge one fixed metadata section and atomically replace the bounded file."""
    if section not in ("user_prompt_submit", "context_lookup"):
        raise ValueError("invalid fixture diagnostic section")
    _validate_values(section, values)
    if increment and (any(key != "observation_count" for key in increment) or
                      section != "user_prompt_submit"):
        raise ValueError("invalid fixture diagnostic increment")
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = target.parent.stat()
    if target.parent.is_symlink() or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise PermissionError("fixture diagnostic directory must be owner-only")
    lock_path = target.with_name(target.name + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if target.exists():
            if target.is_symlink() or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
                raise PermissionError("fixture diagnostic file must be owner-only")
            current = json.loads(target.read_text(encoding="utf-8"))
            if current.get("schema") != SCHEMA:
                raise ValueError("unsupported fixture diagnostic schema")
        else:
            current = _initial()
        current[section].update(values)
        for key in increment:
            current[section][key] = min(1000, current[section][key] + 1)
        payload = json.dumps(current, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(payload) > 2048:
            raise ValueError("fixture diagnostic exceeds size limit")
        fd, temp_name = tempfile.mkstemp(prefix=".fixture-diagnostic-", dir=target.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
            dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    finally:
        os.close(lock_fd)

